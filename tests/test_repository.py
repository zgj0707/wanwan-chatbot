from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.clock import FakeClock
from app.db import (
    MutationBusy,
    REQUIRED_TABLES,
    SCHEMA_VERSION,
    InstanceAlreadyRunning,
    InstanceLock,
    SchemaVersionError,
    connect_database,
    initialize_database,
)
from app.models import CallMetric
from app.persona import EXPECTED_ANCHORS, EXPECTED_HOBBIES, load_persona_snapshot
from app.repository import INITIAL_LIFE_THREADS, Repository, mutation_guard


@pytest.fixture
def initialized(tmp_path: Path):
    now = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    clock = FakeClock(now)
    database_path = tmp_path / "data" / "app.sqlite3"
    assert initialize_database(database_path, clock=clock) is True
    return database_path, clock, Repository(database_path, clock)


def test_initialization_is_atomic_and_does_not_overwrite_existing_preferences(initialized):
    database_path, clock, repository = initialized
    session_id = repository.create_session("第一段对话")
    assert session_id

    changed_preferences = json.dumps(
        {"hobbies": EXPECTED_HOBBIES, "likes": ["乌龙茶"], "dislikes": []},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE persona_versions SET preferences_json = ? WHERE version = 1",
            (changed_preferences,),
        )

    assert initialize_database(database_path, clock=clock) is False

    with repository.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM persona_versions").fetchone()[0] == 1
        assert connection.execute(
            "SELECT preferences_json FROM persona_versions WHERE version = 1"
        ).fetchone()[0] == changed_preferences
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM world_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_schema_contains_all_required_tables_indexes_and_connection_pragmas(initialized):
    _, _, repository = initialized
    with repository.connection() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            )
        }
        assert tables == REQUIRED_TABLES
        assert {
            "idx_messages_session_seq",
            "idx_facts_status_key",
            "idx_proposals_status_created",
            "idx_world_events_status_time_id",
            "idx_prompt_refs_type_id",
            "idx_operations_status",
            "idx_call_metrics_created",
            "uq_facts_active_key",
        } <= indexes
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_initial_persona_states_metadata_and_life_items_are_exactly_seeded(initialized):
    _, clock, repository = initialized
    snapshot = load_persona_snapshot()
    with repository.connection() as connection:
        persona = connection.execute("SELECT * FROM persona_versions").fetchone()
        assert json.loads(persona["anchors_json"]) == EXPECTED_ANCHORS
        assert json.loads(persona["preferences_json"]) == {
            "hobbies": EXPECTED_HOBBIES,
            "likes": [],
            "dislikes": [],
        }
        assert persona["voice_text"] == snapshot.voice_text
        assert json.loads(persona["source_hashes_json"]) == json.loads(snapshot.source_hashes_json)

        character = connection.execute("SELECT * FROM character_state").fetchone()
        relationship = connection.execute("SELECT * FROM relationship_state").fetchone()
        assert (character["valence"], character["energy"], character["version"]) == (0, 60, 1)
        assert (relationship["closeness"], relationship["guard_until"], relationship["version"]) == (10, None, 1)
        assert character["updated_at"] == relationship["updated_at"] == "2026-10-07T00:00:00Z"

        threads = connection.execute(
            "SELECT template_key, label, stage, step, cycle, active, last_event_at FROM life_threads ORDER BY template_key"
        ).fetchall()
        assert [(row["template_key"], row["label"]) for row in threads] == sorted(INITIAL_LIFE_THREADS)
        assert all(
            (row["stage"], row["step"], row["cycle"], row["active"], row["last_event_at"])
            == ("planned", 0, 0, 1, None)
            for row in threads
        )
        assert connection.execute("SELECT COUNT(*) FROM world_events").fetchone()[0] == 0

        metadata = dict(connection.execute("SELECT key, value FROM app_meta").fetchall())
        assert metadata["schema_version"] == "1"
        assert metadata["world_cursor_at"] == clock.now().isoformat().replace("+00:00", "Z")
        assert metadata["initiative_last_at"] == ""
        assert metadata["memory_epoch"] == "0"
        assert len(metadata["installation_id"]) == 36


def test_foreign_keys_and_unique_constraints_reject_invalid_rows(initialized):
    _, _, repository = initialized
    session_id = repository.create_session("约束测试")
    operation_id = str(uuid4())
    with pytest.raises(ValueError, match="session_id"):
        repository.create_pending_operation(
            operation_id=operation_id,
            session_id=str(uuid4()),
            request_hash="a" * 64,
            request_text="你好",
        )

    with pytest.raises(sqlite3.IntegrityError):
        with repository.transaction() as connection:
            connection.execute(
                """
                INSERT INTO operations(
                    id, kind, session_id, request_hash, request_text, status,
                    warnings_json, result_meta_json, call_count, created_at, updated_at
                ) VALUES (?, 'turn', ?, ?, '你好', 'pending', '[]', '{}', 0,
                          '2026-10-07T00:00:00Z', '2026-10-07T00:00:00Z')
                """,
                (str(uuid4()), str(uuid4()), "a" * 64),
            )

    with repository.transaction() as connection:
        now = "2026-10-07T00:00:00Z"
        connection.execute(
            """
            INSERT INTO facts(id, namespace, subject_id, "key", value, canonical_hash,
                              status, origin, supersedes_id, created_at, updated_at)
            VALUES (?, 'user', 'local-user', 'preference.drink', '喜欢乌龙茶', ?, 'active',
                    'manual', NULL, ?, ?)
            """,
            (str(uuid4()), "b" * 64, now, now),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with repository.transaction() as connection:
            now = "2026-10-07T00:00:00Z"
            connection.execute(
                """
                INSERT INTO facts(id, namespace, subject_id, "key", value, canonical_hash,
                                  status, origin, supersedes_id, created_at, updated_at)
                VALUES (?, 'user', 'local-user', 'preference.drink', '喜欢红茶', ?, 'active',
                        'manual', NULL, ?, ?)
                """,
                (str(uuid4()), "c" * 64, now, now),
            )

    second_operation_id = str(uuid4())
    repository.create_pending_operation(
        operation_id=second_operation_id,
        session_id=session_id,
        request_hash=hashlib.sha256(b"request").hexdigest(),
        request_text="你好",
    )
    with repository.transaction() as connection:
        connection.execute(
            """
            INSERT INTO messages(id, seq, session_id, operation_id, role, content, redacted, created_at)
            VALUES (?, 1, ?, ?, 'user', '你好', 0, '2026-10-07T00:00:00Z')
            """,
            (str(uuid4()), session_id, second_operation_id),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with repository.transaction() as connection:
            connection.execute(
                """
                INSERT INTO messages(id, seq, session_id, operation_id, role, content, redacted, created_at)
                VALUES (?, 2, ?, ?, 'user', '重复', 0, '2026-10-07T00:00:00Z')
                """,
                (str(uuid4()), session_id, second_operation_id),
            )


def test_repository_transaction_rolls_back_every_write_on_failure(initialized):
    _, clock, repository = initialized
    session_id = str(uuid4())
    timestamp = clock.now().isoformat().replace("+00:00", "Z")
    with pytest.raises(sqlite3.IntegrityError):
        with repository.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sessions(id, user_id, character_id, title, archived, created_at, updated_at)
                VALUES (?, 'local-user', 'suwan', '临时会话', 0, ?, ?)
                """,
                (session_id, timestamp, timestamp),
            )
            connection.execute(
                """
                INSERT INTO sessions(id, user_id, character_id, title, archived, created_at, updated_at)
                VALUES (?, 'local-user', 'suwan', '重复会话', 0, ?, ?)
                """,
                (session_id, timestamp, timestamp),
            )

    with repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (session_id,)).fetchone()[0] == 0


def test_second_instance_is_rejected_and_lock_file_is_not_the_lock_state(tmp_path: Path):
    path = tmp_path / "data" / "app.lock"
    first = InstanceLock(path)
    second = InstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(InstanceAlreadyRunning):
            second.acquire()
    finally:
        first.release()

    assert path.exists()
    second.acquire()
    second.release()


def test_instance_lock_rejects_a_second_process(tmp_path: Path):
    path = tmp_path / "data" / "app.lock"
    first = InstanceLock(path)
    first.acquire()
    try:
        child_script = "\n".join(
            (
                "import sys",
                "from app.db import InstanceAlreadyRunning, InstanceLock",
                "lock = InstanceLock(sys.argv[1])",
                "try:",
                "    lock.acquire()",
                "except InstanceAlreadyRunning:",
                "    sys.exit(9)",
                "else:",
                "    lock.release()",
            )
        )
        result = subprocess.run(
            [sys.executable, "-c", child_script, str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 9, result.stderr
    finally:
        first.release()


def test_mutation_lock_is_nonblocking_and_transaction_stays_usable(initialized):
    _, _, repository = initialized
    with mutation_guard():
        with repository.transaction() as connection:
            assert connection.execute("SELECT 1").fetchone()[0] == 1
        with pytest.raises(MutationBusy):
            with mutation_guard():
                pass


def test_call_metrics_use_a_short_transaction_while_business_lock_is_held(initialized):
    _, _, repository = initialized
    session_id = repository.create_session("指标事务")
    operation_id = str(uuid4())
    repository.create_pending_operation(
        operation_id=operation_id,
        session_id=session_id,
        request_hash=hashlib.sha256(b"metric operation").hexdigest(),
        request_text="不应进入指标",
    )
    metric = CallMetric(
        operation_id=operation_id,
        profile="chat",
        attempt=1,
        status="succeeded",
        latency_ms=123,
        input_tokens=None,
        output_tokens=7,
        created_at="2026-10-07T00:00:00Z",
    )

    with mutation_guard():
        repository.record_call_metric(metric)

    with repository.connection() as connection:
        row = connection.execute("SELECT * FROM call_metrics WHERE operation_id = ?", (operation_id,)).fetchone()
        assert row is not None
        assert (row["profile"], row["attempt"], row["status"], row["latency_ms"]) == (
            "chat", 1, "succeeded", 123
        )
        assert row["input_tokens"] is None and row["output_tokens"] == 7
        assert "不应进入指标" not in str(dict(row))


def test_unrecognized_schema_version_is_rejected_without_migration(tmp_path: Path):
    database_path = tmp_path / "app.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    with pytest.raises(SchemaVersionError):
        initialize_database(database_path)
    with pytest.raises(SchemaVersionError):
        connect_database(database_path)

    connection = sqlite3.connect(database_path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
    connection.close()


def test_pending_operations_become_interrupted_without_llm_or_message_writes(initialized):
    _, clock, repository = initialized
    session_id = repository.create_session("中断恢复")
    operation_id = str(uuid4())
    repository.create_pending_operation(
        operation_id=operation_id,
        session_id=session_id,
        request_hash=hashlib.sha256(b"original request").hexdigest(),
        request_text="原始消息",
    )
    resumed_at = clock.now() + timedelta(minutes=2)

    assert repository.recover_pending_operations(now=resumed_at) == 1
    operation = repository.get_operation(operation_id)
    assert operation is not None
    assert operation["status"] == "interrupted"
    assert operation["error_code"] == "PROCESS_INTERRUPTED"
    assert operation["request_text"] is None
    assert operation["updated_at"] == resumed_at.isoformat().replace("+00:00", "Z")
    with repository.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM messages WHERE operation_id = ?", (operation_id,)
        ).fetchone()[0] == 0
