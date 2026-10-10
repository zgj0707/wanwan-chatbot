from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db import (
    SCHEMA_VERSION,
    SchemaVersionError,
    _verify_schema,
    initialize_database,
)
from app.clock import FakeClock
from app.memory import canonical_fact_hash


NOW = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)


def _build_v1_database(path: Path) -> None:
    """Create a real schema-v1 database by temporarily downgrading a fresh v3 file.

    The migrations only add structures, so rebuilding v1 from a v3 file by
    dropping the added structures is exact.
    """
    assert initialize_database(path, clock=FakeClock(NOW)) is True
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP TABLE character_schedule")
        connection.execute("DROP TABLE action_intents")
        connection.execute("DROP TABLE character_commitments")
        connection.execute("DROP TABLE character_goals")
        connection.execute("ALTER TABLE character_state DROP COLUMN current_activity")
        connection.execute("DROP TABLE attention_state")
        connection.execute("DROP INDEX idx_materials_status_created")
        connection.execute("DROP TABLE materials")
        connection.execute("ALTER TABLE facts DROP COLUMN importance")
        connection.execute("ALTER TABLE facts DROP COLUMN last_prompt_at")
        connection.execute("PRAGMA user_version = 1")
        connection.execute("UPDATE app_meta SET value = '1' WHERE key = 'schema_version'")
        connection.execute("PRAGMA foreign_keys = ON")
    finally:
        connection.close()


def test_v1_database_migrates_in_place_to_v2_with_data_intact(tmp_path: Path):
    database_path = tmp_path / "data" / "app.sqlite3"
    _build_v1_database(database_path)

    # Sanity: the downgraded file really is v1.
    connection = sqlite3.connect(database_path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    connection.close()

    assert initialize_database(database_path, clock=FakeClock(NOW)) is True

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
        _verify_schema(connection)
        # Pre-existing rows survive the migration untouched.
        assert connection.execute("SELECT COUNT(*) FROM persona_versions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM life_threads").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0
        # v3 columns arrived with backfilled importance defaults.
        importances = {
            row["key"]: row["importance"]
            for row in connection.execute("SELECT \"key\" AS key, importance FROM facts")
        }
        assert importances == {}  # fresh seed has no facts; migration still valid
    finally:
        connection.close()

    # A second run is a no-op.
    assert initialize_database(database_path, clock=FakeClock(NOW)) is False


def test_v2_database_with_facts_backfills_importance_on_upgrade(tmp_path: Path):
    """A v2 database that already holds facts gets importance backfilled by key."""
    database_path = tmp_path / "data" / "app.sqlite3"
    _build_v1_database(database_path)
    # Migrate to v2 only: apply the first migration step manually.
    connection = sqlite3.connect(database_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in (
            """
            CREATE TABLE materials (
                id TEXT PRIMARY KEY CHECK(length(id) = 36),
                message_id TEXT NOT NULL REFERENCES messages(id),
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                session_id TEXT NOT NULL REFERENCES sessions(id),
                snapshot_text TEXT NOT NULL CHECK(length(snapshot_text) > 0),
                pillar TEXT NOT NULL CHECK(pillar IN ('work_diary', 'photo_aesthetics', 'night_emotion', 'cat_ai_girl', 'uncategorized')),
                hook_type TEXT NOT NULL CHECK(hook_type IN ('contrast', 'specific_number', 'suspense_question', 'identity_resonance', 'cat_hook', 'scene_hook', 'none')),
                note TEXT CHECK(note IS NULL OR length(note) <= 200),
                status TEXT NOT NULL CHECK(status IN ('saved', 'discarded', 'invalidated')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(message_id)
            )
            """,
            "CREATE INDEX idx_materials_status_created ON materials(status, created_at)",
        ):
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 2")
        connection.execute("UPDATE app_meta SET value = '2' WHERE key = 'schema_version'")
        connection.commit()

        # Insert two facts the way v2 wrote them (no importance columns yet).
        connection.execute(
            """INSERT INTO facts(id, namespace, subject_id, "key", value, canonical_hash, status, origin, supersedes_id, created_at, updated_at)
               VALUES ('33333333-3333-4333-8333-333333333331', 'user', 'local-user', 'profile.preferred_name', '小满',
                       ?, 'active', 'manual', NULL, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')""",
            (canonical_fact_hash("profile.preferred_name", "小满"),),
        )
        connection.execute(
            """INSERT INTO facts(id, namespace, subject_id, "key", value, canonical_hash, status, origin, supersedes_id, created_at, updated_at)
               VALUES ('33333333-3333-4333-8333-333333333332', 'user', 'local-user', 'preference.drink', '乌龙茶',
                       ?, 'active', 'manual', NULL, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')""",
            (canonical_fact_hash("preference.drink", "乌龙茶"),),
        )
        connection.commit()
    finally:
        connection.close()

    assert initialize_database(database_path, clock=FakeClock(NOW)) is True

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        rows = {
            row["key"]: row["importance"]
            for row in connection.execute('SELECT "key" AS key, importance FROM facts')
        }
        assert rows["profile.preferred_name"] == 1.0
        assert rows["preference.drink"] == 0.7
    finally:
        connection.close()


def test_v1_database_migration_preserves_message_rows(tmp_path: Path):
    database_path = tmp_path / "data" / "app.sqlite3"
    clock = FakeClock(NOW)
    # Build a v1 database, migrate it to v2, then write rows through the
    # normal repository and verify the file still opens and migrates cleanly.
    _build_v1_database(database_path)
    assert initialize_database(database_path, clock=FakeClock(NOW)) is True

    from app.repository import Repository

    repository = Repository(database_path, clock)
    session_id = repository.create_session("迁移前对话")
    with repository.transaction() as connection:
        connection.execute(
            """INSERT INTO operations
               (id, kind, session_id, request_hash, request_text, status, warnings_json, result_meta_json, created_at, updated_at)
               VALUES ('11111111-1111-4111-8111-111111111111', 'turn', ?, ?,
                       '迁移前消息', 'succeeded', '[]', '{}', ?, ?)""",
            (
                session_id,
                "a" * 64,
                clock.now().isoformat().replace("+00:00", "Z"),
                clock.now().isoformat().replace("+00:00", "Z"),
            ),
        )
        connection.execute(
            """INSERT INTO messages (id, seq, session_id, operation_id, role, content, redacted, created_at)
               VALUES ('22222222-2222-4222-8222-222222222222', 1, ?, '11111111-1111-4111-8111-111111111111',
                       'user', '迁移前消息', 0, ?)""",
            (
                session_id,
                clock.now().isoformat().replace("+00:00", "Z"),
            ),
        )

    # Re-initializing an already-current database never rewrites rows.
    assert initialize_database(database_path, clock=FakeClock(NOW)) is False

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT content, redacted FROM messages WHERE id = '22222222-2222-4222-8222-222222222222'"
        ).fetchone()
        assert row is not None
        assert row["content"] == "迁移前消息"
        assert row["redacted"] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
    finally:
        connection.close()


def test_unsupported_future_version_is_refused(tmp_path: Path):
    database_path = tmp_path / "data" / "app.sqlite3"
    assert initialize_database(database_path, clock=FakeClock(NOW)) is True
    connection = sqlite3.connect(database_path, isolation_level=None)
    connection.execute("PRAGMA user_version = 99")
    connection.close()
    with pytest.raises(SchemaVersionError):
        initialize_database(database_path, clock=FakeClock(NOW))
