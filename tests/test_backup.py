from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import warnings
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.backup import BackupError, export_backup, restore_backup
import app.backup as backup_module
from app.clock import FakeClock
from app.config import load_config
from app.db import initialize_database
from app.llm import FakeClient
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config(ROOT / "config.example.yaml")
START = datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc)
WORLD_TIME = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def _make_client(data_dir: Path, clock: FakeClock) -> tuple[TestClient, object, FakeClient]:
    fake = FakeClient(clock=clock)
    app = create_app(CONFIG, project_dir=ROOT, data_dir=data_dir, fake_client=fake)
    app.state.repository.clock = clock
    app.state.llm_client.clock = clock
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    client.__enter__()
    client.headers.update({"X-Suwan-Token": app.state.session_token})
    return client, app, fake


def _make_source_archive(tmp_path: Path) -> tuple[Path, Path]:
    source_dir = tmp_path / "archive-source"
    initialize_database(source_dir / "app.sqlite3", clock=FakeClock(START))
    return export_backup(source_dir, now=START), source_dir


def _zip_contents(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def _write_zip(path: Path, entries: list[tuple[str, bytes, zipfile.ZipInfo | None]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content, info in entries:
            archive.writestr(info or name, content)


def _logical_tables(path: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(path) as connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            name: connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            for name in names
        }


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_backup_round_trip_preserves_messages_memory_state_events_and_persona(tmp_path: Path):
    data_dir = tmp_path / "source-data"
    clock = FakeClock(START)
    client, app, _fake = _make_client(data_dir, clock)
    try:
        created = client.post("/api/v1/sessions", json={"title": "备份往返"})
        assert created.status_code == 201
        session_id = created.json()["id"]
        chat = client.post(
            "/api/v1/chat",
            json={"operation_id": str(uuid4()), "session_id": session_id, "text": "今天先聊一會儿。"},
        )
        assert chat.status_code == 200
        fact, fact_created = app.state.repository.add_manual_fact("preference.drink", "手冲咖啡", now=clock.now())
        assert fact_created
        clock.set(WORLD_TIME)
        event_result = client.post("/api/v1/world/advance", json={})
        assert event_result.status_code == 200 and event_result.json()["advanced"] is True

        export = client.post("/api/v1/backup", json={})
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("application/zip")
        assert export.headers["content-disposition"].isascii()
        archive_path = tmp_path / "round-trip.zip"
        archive_path.write_bytes(export.content)
        with zipfile.ZipFile(archive_path, "r") as archive:
            assert set(archive.namelist()) == {"app.sqlite3", "manifest.json"}
            manifest = json.loads(archive.read("manifest.json"))
        assert manifest["counts"] == {
            "sessions": 1,
            "messages": 2,
            "active_facts": 1,
            "committed_events": 1,
            "persona_versions": 1,
        }
        assert manifest["installation_id"] == app.state.repository.get_meta("installation_id")

        target_project = tmp_path / "restored-project"
        target_project.mkdir()
        config_file = target_project / "config.yaml"
        env_file = target_project / ".env"
        config_file.write_text("mode: fake\ncustom: keep\n", encoding="utf-8")
        env_file.write_text("SUWAN_API_KEY=keep-outside-database\n", encoding="utf-8")
        target_dir = target_project / "data"
        initialize_database(target_dir / "app.sqlite3", clock=FakeClock(START - timedelta(days=1)))
        restored = restore_backup(archive_path, target_dir, confirmed=True)
        assert Path(restored["rollback_database"]).is_file()
        rollback_note = json.loads(Path(restored["rollback_manifest"]).read_text(encoding="utf-8"))
        assert len(rollback_note["sha256"]) == 64
        assert restored["installation_id"] == manifest["installation_id"]
        assert _logical_tables(target_dir / "app.sqlite3") == _logical_tables(data_dir / "app.sqlite3")
        assert config_file.read_text(encoding="utf-8") == "mode: fake\ncustom: keep\n"
        assert env_file.read_text(encoding="utf-8") == "SUWAN_API_KEY=keep-outside-database\n"
        with sqlite3.connect(target_dir / "app.sqlite3") as connection:
            assert connection.execute("SELECT value FROM facts WHERE id = ?", (fact["id"],)).fetchone()[0] == "手冲咖啡"
            assert connection.execute("SELECT status FROM world_events").fetchone()[0] == "committed"
    finally:
        client.__exit__(None, None, None)


def test_bad_hash_and_unsafe_zip_entries_leave_database_unchanged(tmp_path: Path):
    valid_archive, _source_dir = _make_source_archive(tmp_path)
    target_dir = tmp_path / "target-data"
    initialize_database(target_dir / "app.sqlite3", clock=FakeClock(START))
    target = target_dir / "app.sqlite3"
    original_hash = _file_hash(target)
    contents = _zip_contents(valid_archive)
    manifest = json.loads(contents["manifest.json"])

    bad_manifest = dict(manifest)
    bad_manifest["files"] = [dict(manifest["files"][0], sha256="0" * 64)]
    bad_hash_path = tmp_path / "bad-hash.zip"
    _write_zip(
        bad_hash_path,
        [
            ("app.sqlite3", contents["app.sqlite3"], None),
            ("manifest.json", json.dumps(bad_manifest).encode(), None),
        ],
    )

    traversal_path = tmp_path / "traversal.zip"
    _write_zip(
        traversal_path,
        [
            ("app.sqlite3", contents["app.sqlite3"], None),
            ("manifest.json", contents["manifest.json"], None),
            ("../outside.txt", b"not extracted", None),
        ],
    )
    duplicate_path = tmp_path / "duplicate.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        _write_zip(
            duplicate_path,
            [
                ("app.sqlite3", contents["app.sqlite3"], None),
                ("app.sqlite3", contents["app.sqlite3"], None),
                ("manifest.json", contents["manifest.json"], None),
            ],
        )
    symlink_info = zipfile.ZipInfo("manifest.json")
    symlink_info.create_system = 3
    symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    symlink_path = tmp_path / "symlink.zip"
    _write_zip(
        symlink_path,
        [
            ("app.sqlite3", contents["app.sqlite3"], None),
            ("manifest.json", contents["manifest.json"], symlink_info),
        ],
    )
    wrong_version = dict(manifest, format_version=2)
    version_path = tmp_path / "version.zip"
    _write_zip(
        version_path,
        [
            ("app.sqlite3", contents["app.sqlite3"], None),
            ("manifest.json", json.dumps(wrong_version).encode(), None),
        ],
    )

    for invalid_archive in (bad_hash_path, traversal_path, duplicate_path, symlink_path, version_path):
        with pytest.raises(BackupError):
            restore_backup(invalid_archive, target_dir, confirmed=True)
        assert _file_hash(target) == original_hash


def test_restore_refuses_while_application_instance_lock_is_held(tmp_path: Path):
    data_dir = tmp_path / "running-data"
    client, _app, _fake = _make_client(data_dir, FakeClock(START))
    try:
        export = client.post("/api/v1/backup", json={})
        assert export.status_code == 200
        archive_path = tmp_path / "running-copy.zip"
        archive_path.write_bytes(export.content)
        original_hash = _file_hash(data_dir / "app.sqlite3")
        with pytest.raises(BackupError, match="application is running"):
            restore_backup(archive_path, data_dir, confirmed=True)
        assert _file_hash(data_dir / "app.sqlite3") == original_hash
    finally:
        client.__exit__(None, None, None)


def test_restore_size_limits_and_failed_export_leave_no_success_archive(tmp_path: Path, monkeypatch):
    valid_archive, source_dir = _make_source_archive(tmp_path)
    target_dir = tmp_path / "limit-target"
    initialize_database(target_dir / "app.sqlite3", clock=FakeClock(START))
    target_hash = _file_hash(target_dir / "app.sqlite3")

    monkeypatch.setattr(backup_module, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(BackupError, match="50 MB"):
        restore_backup(valid_archive, target_dir, confirmed=True)
    monkeypatch.setattr(backup_module, "MAX_ARCHIVE_BYTES", 50 * 1024 * 1024)
    monkeypatch.setattr(backup_module, "MAX_EXPANDED_BYTES", 10)
    with pytest.raises(BackupError, match="100 MB"):
        restore_backup(valid_archive, target_dir, confirmed=True)
    assert _file_hash(target_dir / "app.sqlite3") == target_hash

    monkeypatch.setattr(backup_module, "MAX_EXPANDED_BYTES", 100 * 1024 * 1024)

    def fail_snapshot_validation(_path: Path):
        raise BackupError("injected validation failure")

    existing_archives = set((source_dir / "exports").glob("*.zip"))
    monkeypatch.setattr(backup_module, "_database_details", fail_snapshot_validation)
    with pytest.raises(BackupError, match="injected validation"):
        export_backup(source_dir, now=START)
    assert set((source_dir / "exports").glob("*.zip")) == existing_archives
    assert list((source_dir / "exports").glob("*.tmp")) == []


def test_backup_api_requires_local_token_and_returns_safe_ascii_filename(tmp_path: Path):
    client, _app, _fake = _make_client(tmp_path / "api-data", FakeClock(START))
    try:
        client.headers.pop("X-Suwan-Token")
        assert client.post("/api/v1/backup", json={}).status_code == 401
        client.headers["X-Suwan-Token"] = _app.state.session_token
        response = client.post("/api/v1/backup", json={})
        assert response.status_code == 200
        filename = response.headers["content-disposition"]
        assert filename.isascii() and "suwan-backup-" in filename and filename.endswith('.zip"')
    finally:
        client.__exit__(None, None, None)
