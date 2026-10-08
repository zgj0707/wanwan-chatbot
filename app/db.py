from __future__ import annotations

import sqlite3
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from app.clock import Clock, SystemClock


SCHEMA_VERSION = 3
MUTATION_LOCK = threading.Lock()
_registry_lock = threading.Lock()
_held_paths: set[str] = set()

REQUIRED_TABLES = frozenset(
    {
        "sessions",
        "operations",
        "messages",
        "character_state",
        "relationship_state",
        "facts",
        "fact_sources",
        "proposals",
        "persona_versions",
        "life_threads",
        "world_events",
        "prompt_refs",
        "app_meta",
        "call_metrics",
        "materials",
    }
)
REQUIRED_INDEXES = frozenset(
    {
        "idx_messages_session_seq",
        "idx_facts_status_key",
        "idx_proposals_status_created",
        "idx_world_events_status_time_id",
        "idx_prompt_refs_type_id",
        "idx_operations_status",
        "idx_call_metrics_created",
        "uq_facts_active_key",
        "idx_materials_status_created",
    }
)

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY CHECK(length(id) = 36),
        user_id TEXT NOT NULL CHECK(user_id = 'local-user'),
        character_id TEXT NOT NULL CHECK(character_id = 'suwan'),
        title TEXT NOT NULL,
        archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE operations (
        id TEXT PRIMARY KEY CHECK(length(id) = 36),
        kind TEXT NOT NULL CHECK(kind IN ('turn', 'initiative')),
        session_id TEXT NOT NULL REFERENCES sessions(id),
        request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
        request_text TEXT,
        status TEXT NOT NULL CHECK(status IN ('pending', 'succeeded', 'failed', 'interrupted')),
        user_message_id TEXT REFERENCES messages(id),
        assistant_message_id TEXT REFERENCES messages(id),
        error_code TEXT,
        warnings_json TEXT NOT NULL CHECK(length(warnings_json) > 0),
        result_meta_json TEXT NOT NULL CHECK(length(result_meta_json) > 0),
        elapsed_ms INTEGER CHECK(elapsed_ms IS NULL OR elapsed_ms >= 0),
        call_count INTEGER NOT NULL DEFAULT 0 CHECK(call_count >= 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE messages (
        id TEXT PRIMARY KEY CHECK(length(id) = 36),
        seq INTEGER NOT NULL UNIQUE CHECK(seq > 0),
        session_id TEXT NOT NULL REFERENCES sessions(id),
        operation_id TEXT NOT NULL REFERENCES operations(id),
        role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
        content TEXT,
        redacted INTEGER NOT NULL DEFAULT 0 CHECK(redacted IN (0, 1)),
        created_at TEXT NOT NULL,
        UNIQUE(operation_id, role)
    )
    """,
    """
    CREATE TABLE character_state (
        character_id TEXT PRIMARY KEY CHECK(character_id = 'suwan'),
        valence INTEGER NOT NULL CHECK(valence BETWEEN -100 AND 100),
        energy INTEGER NOT NULL CHECK(energy BETWEEN 0 AND 100),
        updated_at TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version > 0)
    )
    """,
    """
    CREATE TABLE relationship_state (
        user_id TEXT NOT NULL CHECK(user_id = 'local-user'),
        character_id TEXT NOT NULL CHECK(character_id = 'suwan'),
        closeness INTEGER NOT NULL CHECK(closeness BETWEEN 0 AND 100),
        guard_until TEXT,
        updated_at TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version > 0),
        PRIMARY KEY(user_id, character_id)
    )
    """,
    """
    CREATE TABLE facts (
        id TEXT PRIMARY KEY CHECK(length(id) = 36),
        namespace TEXT NOT NULL CHECK(namespace = 'user'),
        subject_id TEXT NOT NULL CHECK(subject_id = 'local-user'),
        key TEXT NOT NULL CHECK(length(key) > 0),
        value TEXT,
        canonical_hash TEXT NOT NULL CHECK(length(canonical_hash) = 64),
        status TEXT NOT NULL CHECK(status IN ('active', 'superseded', 'forgotten')),
        origin TEXT NOT NULL CHECK(origin IN ('message', 'manual')),
        importance REAL NOT NULL DEFAULT 1.0 CHECK(importance >= 0.5 AND importance <= 1.0),
        last_prompt_at TEXT,
        supersedes_id TEXT REFERENCES facts(id),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE fact_sources (
        fact_id TEXT NOT NULL REFERENCES facts(id),
        message_id TEXT NOT NULL REFERENCES messages(id),
        PRIMARY KEY(fact_id, message_id)
    )
    """,
    """
    CREATE TABLE proposals (
        id TEXT PRIMARY KEY CHECK(length(id) = 36),
        kind TEXT NOT NULL CHECK(kind IN ('user_fact', 'persona')),
        payload_json TEXT NOT NULL CHECK(length(payload_json) > 0),
        source_message_id TEXT NOT NULL REFERENCES messages(id),
        status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'rejected', 'invalidated')),
        result_id TEXT,
        created_at TEXT NOT NULL,
        resolved_at TEXT
    )
    """,
    """
    CREATE TABLE persona_versions (
        id TEXT PRIMARY KEY CHECK(length(id) = 36),
        version INTEGER NOT NULL UNIQUE CHECK(version > 0),
        anchors_json TEXT NOT NULL CHECK(length(anchors_json) > 0),
        preferences_json TEXT NOT NULL CHECK(length(preferences_json) > 0),
        voice_text TEXT NOT NULL,
        source_hashes_json TEXT NOT NULL CHECK(length(source_hashes_json) > 0),
        created_at TEXT NOT NULL,
        proposal_id TEXT REFERENCES proposals(id)
    )
    """,
    """
    CREATE TABLE life_threads (
        id TEXT PRIMARY KEY CHECK(length(id) = 36),
        template_key TEXT NOT NULL UNIQUE CHECK(length(template_key) > 0),
        label TEXT NOT NULL CHECK(length(label) > 0),
        stage TEXT NOT NULL CHECK(stage IN ('planned', 'started', 'progressed', 'done', 'paused')),
        step INTEGER NOT NULL CHECK(step BETWEEN 0 AND 3),
        cycle INTEGER NOT NULL DEFAULT 0 CHECK(cycle >= 0),
        active INTEGER NOT NULL CHECK(active IN (0, 1)),
        last_event_at TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE world_events (
        id TEXT PRIMARY KEY CHECK(length(id) = 36),
        slot_key TEXT NOT NULL UNIQUE CHECK(length(slot_key) > 0),
        thread_id TEXT NOT NULL REFERENCES life_threads(id),
        cycle INTEGER NOT NULL CHECK(cycle >= 0),
        step_before INTEGER NOT NULL CHECK(step_before BETWEEN 0 AND 3),
        step_after INTEGER NOT NULL CHECK(step_after BETWEEN 0 AND 3),
        stage_before TEXT NOT NULL CHECK(stage_before IN ('planned', 'started', 'progressed', 'done', 'paused')),
        stage_after TEXT NOT NULL CHECK(stage_after IN ('planned', 'started', 'progressed', 'done', 'paused')),
        occurred_at TEXT NOT NULL,
        summary TEXT,
        state_before_json TEXT NOT NULL CHECK(length(state_before_json) > 0),
        state_after_json TEXT NOT NULL CHECK(length(state_after_json) > 0),
        status TEXT NOT NULL CHECK(status IN ('committed', 'retracted')),
        created_at TEXT NOT NULL,
        retracted_at TEXT
    )
    """,
    """
    CREATE TABLE prompt_refs (
        assistant_message_id TEXT NOT NULL REFERENCES messages(id),
        ref_type TEXT NOT NULL CHECK(ref_type IN ('fact', 'world_event', 'persona_version')),
        ref_id TEXT NOT NULL,
        PRIMARY KEY(assistant_message_id, ref_type, ref_id)
    )
    """,
    """
    CREATE TABLE app_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE call_metrics (
        id TEXT PRIMARY KEY CHECK(length(id) = 36),
        operation_id TEXT REFERENCES operations(id),
        profile TEXT NOT NULL CHECK(profile IN ('chat', 'analysis', 'world')),
        attempt INTEGER NOT NULL CHECK(attempt >= 1),
        status TEXT NOT NULL CHECK(length(status) > 0),
        latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0),
        input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
        output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_messages_session_seq ON messages(session_id, seq)",
    'CREATE INDEX idx_facts_status_key ON facts(status, "key")',
    "CREATE INDEX idx_proposals_status_created ON proposals(status, created_at)",
    "CREATE INDEX idx_world_events_status_time_id ON world_events(status, occurred_at, id)",
    "CREATE INDEX idx_prompt_refs_type_id ON prompt_refs(ref_type, ref_id)",
    "CREATE INDEX idx_operations_status ON operations(status)",
    "CREATE INDEX idx_call_metrics_created ON call_metrics(created_at)",
    'CREATE UNIQUE INDEX uq_facts_active_key ON facts(namespace, subject_id, "key") WHERE status = \'active\'',
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
)


class SchemaVersionError(RuntimeError):
    """Raised when the database schema version is not supported."""


class SchemaError(RuntimeError):
    """Raised when the database is structurally incomplete or inconsistent."""


# Incremental migrations: {current_version: (target_version, statements)}.
# Each step only adds structures; existing rows and columns are never rewritten.
MIGRATIONS: dict[int, tuple[int, tuple[str, ...]]] = {
    1: (
        2,
        (
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
        ),
    ),
    2: (
        3,
        (
            # Memory decay (gentle, never deletes): importance weights recall,
            # last_prompt_at feeds the recency factor. Existing rows keep
            # sensible defaults: identity facts are heavy, others normal.
            "ALTER TABLE facts ADD COLUMN importance REAL NOT NULL DEFAULT 1.0 CHECK(importance >= 0.5 AND importance <= 1.0)",
            "ALTER TABLE facts ADD COLUMN last_prompt_at TEXT",
            "UPDATE facts SET importance = 1.0 WHERE \"key\" IN ('profile.preferred_name', 'profile.birthday')",
            "UPDATE facts SET importance = 0.7 WHERE \"key\" NOT IN ('profile.preferred_name', 'profile.birthday')",
        ),
    ),
}


def _apply_migrations(connection: sqlite3.Connection, from_version: int) -> None:
    """Upgrade an initialized database in place, one strict step at a time."""
    version = from_version
    while version != SCHEMA_VERSION:
        if version not in MIGRATIONS:
            raise SchemaVersionError(
                f"database schema version {version} has no migration path to {SCHEMA_VERSION}"
            )
        target, statements = MIGRATIONS[version]
        for statement in statements:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {target}")
        # app_meta.schema_version mirrors PRAGMA user_version (seeded that way
        # since v1); keep the mirror exact so _verify_schema passes.
        connection.execute(
            "UPDATE app_meta SET value = ? WHERE key = 'schema_version'",
            (str(target),),
        )
        version = target


class InstanceAlreadyRunning(RuntimeError):
    """Raised when another process or app instance owns the local lock."""


class MutationBusy(RuntimeError):
    """Raised when another local business mutation is in progress."""


class InstanceLock:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle: BinaryIO | None = None
        self._registry_key: str | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("this InstanceLock is already acquired")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = str(self.path.resolve()).casefold() if os.name == "nt" else str(self.path.resolve())

        with _registry_lock:
            if key in _held_paths:
                raise InstanceAlreadyRunning("another application instance already holds the data lock")

            handle = self.path.open("a+b")
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)

                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise InstanceAlreadyRunning("another application process already holds the data lock") from exc

            _held_paths.add(key)
            self._handle = handle
            self._registry_key = key

    def release(self) -> None:
        handle = self._handle
        key = self._registry_key
        if handle is None:
            return

        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None
            self._registry_key = None
            if key is not None:
                with _registry_lock:
                    _held_paths.discard(key)

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


def to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("database timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _verify_schema(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(f"database schema version {version} is unsupported; expected {SCHEMA_VERSION}")
    missing = REQUIRED_TABLES - _table_names(connection)
    if missing:
        raise SchemaError("database is missing required tables: " + ", ".join(sorted(missing)))
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
        )
    }
    missing_indexes = REQUIRED_INDEXES - indexes
    if missing_indexes:
        raise SchemaError("database is missing required indexes: " + ", ".join(sorted(missing_indexes)))
    meta = connection.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
    if meta is None or meta[0] != str(SCHEMA_VERSION):
        raise SchemaError("database schema metadata does not match PRAGMA user_version")


def _set_connection_pragmas(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if str(journal_mode).lower() != "wal":
        raise SchemaError("SQLite WAL mode could not be enabled")
    connection.execute("PRAGMA synchronous = FULL")


def connect_database(path: str | Path, *, require_schema: bool = True) -> sqlite3.Connection:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if require_schema:
            if version != SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"database schema version {version} is unsupported; expected {SCHEMA_VERSION}"
                )
            _set_connection_pragmas(connection)
            _verify_schema(connection)
        return connection
    except Exception:
        connection.close()
        raise


def initialize_database(path: str | Path, clock: Clock | None = None) -> bool:
    """Create the current schema and its seed, or migrate an older database in place; return False if no writes were needed."""
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            _set_connection_pragmas(connection)
            _verify_schema(connection)
            return False
        if version != 0:
            existing_tables = _table_names(connection)
            if not existing_tables:
                raise SchemaVersionError(
                    f"database schema version {version} is unsupported; expected {SCHEMA_VERSION}"
                )
            _set_connection_pragmas(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute("PRAGMA user_version").fetchone()[0] != version:
                    raise SchemaVersionError("database version changed while migration was waiting")
                _apply_migrations(connection, version)
                _verify_schema(connection)
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise SchemaError("migration left foreign-key violations")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return True
        existing_tables = _table_names(connection)
        if existing_tables:
            raise SchemaError("unversioned database already contains tables; refusing to overwrite it")

        _set_connection_pragmas(connection)
        now = to_utc_iso((clock or SystemClock()).now())
        connection.execute("BEGIN IMMEDIATE")
        try:
            if connection.execute("PRAGMA user_version").fetchone()[0] != 0:
                raise SchemaVersionError("database version changed while initialization was waiting")
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

            from app.repository import seed_initial_data

            seed_initial_data(connection, now=now)
            _verify_schema(connection)
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise SchemaError("initial database seed violates a foreign-key constraint")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return True
    finally:
        connection.close()
