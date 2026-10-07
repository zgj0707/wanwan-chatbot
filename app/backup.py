from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from app.db import InstanceAlreadyRunning, InstanceLock, SCHEMA_VERSION, _verify_schema


FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
APP_VERSION = "0.1.0-alpha"
BACKUP_FILES = {"app.sqlite3", "manifest.json"}
COUNT_QUERIES = {
    "sessions": "SELECT COUNT(*) FROM sessions",
    "messages": "SELECT COUNT(*) FROM messages",
    "active_facts": "SELECT COUNT(*) FROM facts WHERE status = 'active'",
    "committed_events": "SELECT COUNT(*) FROM world_events WHERE status = 'committed'",
    "persona_versions": "SELECT COUNT(*) FROM persona_versions",
}


class BackupError(RuntimeError):
    """Raised when a backup cannot be created or safely restored."""


def _utc_now(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("backup timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()).replace(os.sep, '/'), safe='/:')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _database_details(path: Path) -> dict[str, Any]:
    try:
        with closing(_readonly_connection(path)) as connection:
            _verify_schema(connection)
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            if integrity != ["ok"]:
                raise BackupError("database integrity_check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise BackupError("database foreign_key_check failed")
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            installation_id_row = connection.execute(
                "SELECT value FROM app_meta WHERE key = 'installation_id'"
            ).fetchone()
            if installation_id_row is None:
                raise BackupError("database installation ID is missing")
            installation_id = installation_id_row[0]
            if str(UUID(installation_id)) != installation_id:
                raise BackupError("database installation ID is invalid")
            counts = {
                name: int(connection.execute(query).fetchone()[0])
                for name, query in COUNT_QUERIES.items()
            }
            return {
                "schema_version": schema_version,
                "installation_id": installation_id,
                "counts": counts,
            }
    except BackupError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError, RuntimeError) as exc:
        raise BackupError("database validation failed") from exc


def _sqlite_snapshot(source_path: Path, destination_path: Path) -> None:
    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(source_path, timeout=5.0)
        destination = sqlite3.connect(destination_path, timeout=5.0)
        source.backup(destination)
    except (OSError, sqlite3.Error) as exc:
        raise BackupError("SQLite snapshot failed") from exc
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()


def export_backup(data_dir: str | Path, *, now: datetime | None = None) -> Path:
    root = Path(data_dir).resolve()
    source_path = root / "app.sqlite3"
    if not source_path.is_file():
        raise BackupError("application database is missing")
    created_at = _utc_now(now)
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    destination = exports / f"suwan-backup-{stamp}-{uuid4().hex[:8]}.zip"
    temp_archive: Path | None = None

    try:
        with tempfile.TemporaryDirectory(prefix=".suwan-backup-") as temporary:
            snapshot_path = Path(temporary) / "app.sqlite3"
            _sqlite_snapshot(source_path, snapshot_path)
            details = _database_details(snapshot_path)
            manifest = {
                "format_version": FORMAT_VERSION,
                "schema_version": details["schema_version"],
                "created_at": _timestamp(created_at),
                "installation_id": details["installation_id"],
                "app_version": APP_VERSION,
                "files": [
                    {
                        "path": "app.sqlite3",
                        "sha256": _sha256(snapshot_path),
                        "size_bytes": snapshot_path.stat().st_size,
                    }
                ],
                "counts": details["counts"],
            }
            with tempfile.NamedTemporaryFile(
                prefix=".suwan-backup-", suffix=".tmp", dir=exports, delete=False
            ) as temporary_file:
                temp_archive = Path(temporary_file.name)
            with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot_path, arcname="app.sqlite3")
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                )
            with temp_archive.open("rb+") as stream:
                os.fsync(stream.fileno())
            os.replace(temp_archive, destination)
            temp_archive = None
            return destination
    except BackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile, ValueError) as exc:
        raise BackupError("backup export failed") from exc
    finally:
        if temp_archive is not None:
            temp_archive.unlink(missing_ok=True)


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup manifest is invalid") from exc
    required = {
        "format_version", "schema_version", "created_at", "installation_id", "app_version", "files", "counts"
    }
    if not isinstance(value, dict) or set(value) != required:
        raise BackupError("backup manifest fields are invalid")
    if type(value["format_version"]) is not int or value["format_version"] != FORMAT_VERSION:
        raise BackupError("backup format version is unsupported")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise BackupError("backup database schema version is unsupported")
    if not isinstance(value["app_version"], str) or not value["app_version"]:
        raise BackupError("backup application version is invalid")
    try:
        created_at = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError
        installation_id = UUID(value["installation_id"])
        if str(installation_id) != value["installation_id"]:
            raise ValueError
    except (AttributeError, TypeError, ValueError) as exc:
        raise BackupError("backup manifest metadata is invalid") from exc
    files = value["files"]
    if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
        raise BackupError("backup manifest file list is invalid")
    if set(files[0]) != {"path", "sha256", "size_bytes"} or files[0]["path"] != "app.sqlite3":
        raise BackupError("backup manifest file entry is invalid")
    if (
        not isinstance(files[0]["sha256"], str)
        or len(files[0]["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in files[0]["sha256"].lower())
        or type(files[0]["size_bytes"]) is not int
        or files[0]["size_bytes"] < 0
    ):
        raise BackupError("backup manifest file digest is invalid")
    counts = value["counts"]
    if not isinstance(counts, dict) or set(counts) != set(COUNT_QUERIES):
        raise BackupError("backup manifest counts are invalid")
    if any(type(count) is not int or count < 0 for count in counts.values()):
        raise BackupError("backup manifest counts are invalid")
    return value


def _copy_zip_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo, destination: Path) -> str:
    digest = hashlib.sha256()
    written = 0
    try:
        with archive.open(info, "r") as source, destination.open("wb") as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                written += len(chunk)
                if written > MAX_EXPANDED_BYTES:
                    raise BackupError("expanded backup exceeds the size limit")
                digest.update(chunk)
                target.write(chunk)
    except BackupError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
        raise BackupError("backup archive could not be read") from exc
    if written != info.file_size:
        raise BackupError("backup entry size does not match its ZIP header")
    return digest.hexdigest()


def _validated_import(archive_path: Path, temporary_dir: Path) -> tuple[Path, dict[str, Any]]:
    if not archive_path.is_absolute() or not archive_path.is_file():
        raise BackupError("archive path must be an existing absolute file path")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BackupError("backup archive exceeds 50 MB")
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) != 2 or len(set(names)) != 2 or set(names) != BACKUP_FILES:
                raise BackupError("archive must contain app.sqlite3 and manifest.json exactly once")
            if any(
                info.is_dir()
                or PurePosixPath(info.filename).is_absolute()
                or ".." in PurePosixPath(info.filename).parts
                or "\\" in info.filename
                or info.flag_bits & 0x1
                or stat.S_ISLNK(info.external_attr >> 16)
                for info in infos
            ):
                raise BackupError("archive contains an unsafe ZIP entry")
            if sum(info.file_size for info in infos) > MAX_EXPANDED_BYTES:
                raise BackupError("expanded backup exceeds 100 MB")
            by_name = {info.filename: info for info in infos}
            manifest_info = by_name["manifest.json"]
            if manifest_info.file_size > 1024 * 1024:
                raise BackupError("backup manifest exceeds the size limit")
            manifest_path = temporary_dir / "manifest.json"
            _copy_zip_entry(archive, manifest_info, manifest_path)
            manifest = _read_manifest(manifest_path)
            database_info = by_name["app.sqlite3"]
            database_path = temporary_dir / "app.sqlite3"
            actual_digest = _copy_zip_entry(archive, database_info, database_path)
    except BackupError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise BackupError("backup archive is invalid") from exc

    file_info = manifest["files"][0]
    if file_info["size_bytes"] != database_path.stat().st_size or file_info["size_bytes"] != database_info.file_size:
        raise BackupError("backup database size does not match its manifest")
    if file_info["sha256"].lower() != actual_digest:
        raise BackupError("backup database hash does not match its manifest")
    details = _database_details(database_path)
    if details["schema_version"] != manifest["schema_version"]:
        raise BackupError("backup database schema does not match its manifest")
    if details["installation_id"] != manifest["installation_id"]:
        raise BackupError("backup installation ID does not match its manifest")
    if details["counts"] != manifest["counts"]:
        raise BackupError("backup record counts do not match its manifest")
    return database_path, manifest


def _unique_rollback_paths(rollback_dir: Path, now: datetime) -> tuple[Path, Path]:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    stem = f"pre-restore-{stamp}-{uuid4().hex[:8]}"
    return rollback_dir / f"{stem}.sqlite3", rollback_dir / f"{stem}.manifest.json"


def _remove_database_sidecars(database_path: Path, data_dir: Path) -> None:
    parent = database_path.parent.resolve()
    if parent != data_dir.resolve() or database_path.name != "app.sqlite3":
        raise BackupError("database replacement target is outside the data directory")
    for suffix in ("-wal", "-shm"):
        sidecar = data_dir / f"app.sqlite3{suffix}"
        if sidecar.exists():
            if sidecar.parent.resolve() != data_dir.resolve() or sidecar.is_dir():
                raise BackupError("database sidecar path is unsafe")
            sidecar.unlink()


def restore_backup(
    archive_path: str | Path,
    data_dir: str | Path,
    *,
    confirmed: bool = False,
) -> dict[str, str]:
    if not confirmed:
        raise BackupError("restore requires explicit confirmation")
    archive = Path(archive_path)
    if not archive.is_absolute():
        raise BackupError("archive path must be absolute")
    root = Path(data_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(root / "app.lock")
    try:
        lock.acquire()
    except InstanceAlreadyRunning as exc:
        raise BackupError("application is running; stop it before restoring") from exc

    try:
        target_path = root / "app.sqlite3"
        if not target_path.is_file():
            raise BackupError("current application database is missing")
        rollback_dir = root / "rollback"
        rollback_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".restore-import-", dir=root) as import_dir_name:
            import_dir = Path(import_dir_name)
            imported_path, manifest = _validated_import(archive, import_dir)
            _database_details(target_path)

            with tempfile.TemporaryDirectory(prefix=".restore-rollback-", dir=rollback_dir) as rollback_temp_name:
                rollback_temp = Path(rollback_temp_name)
                rollback_temp_db = rollback_temp / "app.sqlite3"
                _sqlite_snapshot(target_path, rollback_temp_db)
                rollback_details = _database_details(rollback_temp_db)
                rollback_db_path, rollback_manifest_path = _unique_rollback_paths(
                    rollback_dir, datetime.now(timezone.utc)
                )
                rollback_manifest_temp = rollback_temp / "manifest.json"
                rollback_hash = _sha256(rollback_temp_db)
                rollback_manifest_temp.write_text(
                    json.dumps(
                        {
                            "created_at": _timestamp(datetime.now(timezone.utc)),
                            "sha256": rollback_hash,
                            "counts": rollback_details["counts"],
                            "installation_id": rollback_details["installation_id"],
                            "source_archive_created_at": manifest["created_at"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                os.replace(rollback_temp_db, rollback_db_path)
                os.replace(rollback_manifest_temp, rollback_manifest_path)

            replacement_started = False
            try:
                replacement_started = True
                _remove_database_sidecars(target_path, root)
                os.replace(imported_path, target_path)
                _database_details(target_path)
            except Exception as exc:
                if replacement_started:
                    restore_copy = root / f".restore-rollback-{uuid4().hex}.sqlite3"
                    try:
                        shutil.copy2(rollback_db_path, restore_copy)
                        _remove_database_sidecars(target_path, root)
                        os.replace(restore_copy, target_path)
                    finally:
                        restore_copy.unlink(missing_ok=True)
                if isinstance(exc, BackupError):
                    raise
                raise BackupError("restored database failed validation; rollback was applied") from exc
            return {
                "database": str(target_path),
                "rollback_database": str(rollback_db_path),
                "rollback_manifest": str(rollback_manifest_path),
                "installation_id": manifest["installation_id"],
            }
    except BackupError:
        raise
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise BackupError("backup restore failed") from exc
    finally:
        lock.release()
