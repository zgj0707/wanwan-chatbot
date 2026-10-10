from __future__ import annotations

import re
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4

from app.clock import Clock, SystemClock
from app.db import MUTATION_LOCK, MutationBusy, SCHEMA_VERSION, connect_database, to_utc_iso
from app.memory import fact_importance
from app.models import CallMetric, FactCandidate, PersonaProposal
from app.memory import (
    MemoryValidationError,
    normalized_payload_signature,
    validate_fact_candidates,
    validate_fact_value,
)
from app.persona import PersonaDataUnavailable, PersonaPolicyError, apply_persona_proposal, load_persona_snapshot
from app.state import parse_db_datetime


INITIAL_LIFE_THREADS = (
    ("work_requirement", "整理这周的一个需求"),
    ("photo_selection", "整理一组随手拍"),
    ("coffee_practice", "试一种手冲方法"),
)


class ProposalInvalid(RuntimeError):
    pass


class ProposalSourceInvalid(RuntimeError):
    pass


@contextmanager
def mutation_guard() -> Iterator[None]:
    """Acquire the process-wide business mutation lock without waiting."""
    if not MUTATION_LOCK.acquire(blocking=False):
        raise MutationBusy("another business mutation is in progress")
    try:
        yield
    finally:
        MUTATION_LOCK.release()


def _validate_uuid4(value: str, field: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID4 string") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field} must be a canonical lowercase UUID4 string")
    return value


def _message_object(row: sqlite3.Row | dict) -> dict:
    redacted = bool(row["redacted"])
    return {
        "id": row["id"],
        "seq": row["seq"],
        "role": row["role"],
        "content": None if redacted else row["content"],
        "redacted": redacted,
        "created_at": row["created_at"],
        "operation_id": row["operation_id"],
    }


def _message_source_object(row: sqlite3.Row | dict) -> dict:
    redacted = bool(row["redacted"])
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": None if redacted else row["content"],
        "redacted": redacted,
        "created_at": row["created_at"],
    }


def seed_initial_data(connection: sqlite3.Connection, *, now: str) -> None:
    """Insert the initial persona, global states, three product seed items, and metadata."""
    snapshot = load_persona_snapshot()
    connection.execute(
        """
        INSERT INTO persona_versions
            (id, version, anchors_json, preferences_json, voice_text, source_hashes_json, created_at, proposal_id)
        VALUES (?, 1, ?, ?, ?, ?, ?, NULL)
        """,
        (
            str(uuid4()),
            snapshot.anchors_json,
            snapshot.preferences_json,
            snapshot.voice_text,
            snapshot.source_hashes_json,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO character_state(character_id, valence, energy, updated_at, version)
        VALUES ('suwan', 0, 60, ?, 1)
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO relationship_state(user_id, character_id, closeness, guard_until, updated_at, version)
        VALUES ('local-user', 'suwan', 10, NULL, ?, 1)
        """,
        (now,),
    )

    for template_key, label in INITIAL_LIFE_THREADS:
        connection.execute(
            """
            INSERT INTO life_threads
                (id, template_key, label, stage, step, cycle, active, last_event_at, created_at)
            VALUES (?, ?, ?, 'planned', 0, 0, 1, NULL, ?)
            """,
            (str(uuid4()), template_key, label, now),
        )

    metadata = {
        "schema_version": str(SCHEMA_VERSION),
        "world_cursor_at": now,
        "initiative_last_at": "",
        "memory_epoch": "0",
        "installation_id": str(uuid4()),
    }
    connection.executemany(
        "INSERT INTO app_meta(key, value) VALUES (?, ?)",
        tuple(metadata.items()),
    )


class Repository:
    def __init__(self, database_path: str | Path, clock: Clock | None = None) -> None:
        self.database_path = database_path
        self.clock = clock or SystemClock()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = connect_database(self.database_path)
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()

    def create_session(self, title: str, now: datetime | None = None) -> str:
        if not isinstance(title, str):
            raise ValueError("session title must be text")
        normalized_title = title.strip()
        if not normalized_title or len(normalized_title) > 40:
            raise ValueError("session title must contain 1 to 40 characters")
        session_id = str(uuid4())
        timestamp = to_utc_iso(now or self.clock.now())
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sessions(id, user_id, character_id, title, archived, created_at, updated_at)
                VALUES (?, 'local-user', 'suwan', ?, 0, ?, ?)
                """,
                (session_id, normalized_title, timestamp, timestamp),
            )
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        _validate_uuid4(session_id, "session_id")
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return dict(row) if row is not None else None

    def list_sessions(self, *, include_archived: bool = False) -> list[dict]:
        with self.connection() as connection:
            query = "SELECT id, title, archived, created_at, updated_at FROM sessions"
            if not include_archived:
                query += " WHERE archived = 0"
            query += " ORDER BY updated_at DESC, id"
            rows = [dict(row) for row in connection.execute(query).fetchall()]
            if not rows:
                return []
            # 微信式最后一句预览：每个会话取最新一条未红action的未空内容。
            marks = ",".join("?" for _ in rows)
            preview_rows = connection.execute(
                f"""SELECT m.session_id, m.content, m.role, m.created_at
                    FROM messages m
                    JOIN (
                        SELECT session_id, MAX(seq) AS max_seq
                        FROM messages
                        WHERE content IS NOT NULL AND redacted = 0
                        GROUP BY session_id
                    ) latest ON latest.session_id = m.session_id AND latest.max_seq = m.seq
                    WHERE m.session_id IN ({marks}) AND m.content IS NOT NULL AND m.redacted = 0""",
                tuple(r["id"] for r in rows),
            ).fetchall()
            previews = {row["session_id"]: row for row in preview_rows}
            for row in rows:
                preview = previews.get(row["id"])
                row["last_message"] = (
                    {
                        "content": preview["content"],
                        "role": preview["role"],
                        "created_at": preview["created_at"],
                    }
                    if preview
                    else None
                )
            return rows

    def set_session_archived(self, session_id: str, archived: bool, now: datetime | None = None) -> dict | None:
        _validate_uuid4(session_id, "session_id")
        timestamp = to_utc_iso(now or self.clock.now())
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET archived = ?, updated_at = ? WHERE id = ?",
                (int(archived), timestamp, session_id),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT id, title, archived, created_at, updated_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            return dict(row)

    def list_session_messages(
        self,
        session_id: str,
        *,
        limit: int = 50,
        before_id: str | None = None,
    ) -> tuple[list[dict], str | None]:
        _validate_uuid4(session_id, "session_id")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
                raise LookupError("session not found")
            before_seq = None
            if before_id is not None:
                _validate_uuid4(before_id, "before_id")
                cursor = connection.execute(
                    "SELECT seq FROM messages WHERE id = ? AND session_id = ?",
                    (before_id, session_id),
                ).fetchone()
                if cursor is None:
                    raise ValueError("before_id does not belong to this session")
                before_seq = cursor["seq"]

            parameters: list[object] = [session_id]
            sql = "SELECT * FROM messages WHERE session_id = ?"
            if before_seq is not None:
                sql += " AND seq < ?"
                parameters.append(before_seq)
            sql += " ORDER BY seq DESC LIMIT ?"
            parameters.append(limit + 1)
            rows = connection.execute(sql, parameters).fetchall()
            has_older = len(rows) > limit
            selected = list(reversed(rows[:limit]))
            items = [_message_object(row) for row in selected]
            next_before_id = items[0]["id"] if has_older and items else None
            return items, next_before_id

    def load_turn_context(self, session_id: str, *, history_limit: int = 32) -> dict:
        """Read prompt inputs in one short snapshot; callers make network calls after it closes."""
        _validate_uuid4(session_id, "session_id")
        with self.connection() as connection:
            session = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if session is None:
                raise LookupError("session not found")
            if session["archived"]:
                raise PermissionError("session is archived")
            persona = connection.execute(
                "SELECT * FROM persona_versions ORDER BY version DESC LIMIT 1"
            ).fetchone()
            character = connection.execute("SELECT * FROM character_state WHERE character_id = 'suwan'").fetchone()
            relationship = connection.execute(
                "SELECT * FROM relationship_state WHERE user_id = 'local-user' AND character_id = 'suwan'"
            ).fetchone()
            if persona is None or character is None or relationship is None:
                raise RuntimeError("required prompt state is missing")
            threads = connection.execute(
                "SELECT * FROM life_threads ORDER BY template_key"
            ).fetchall()
            events = connection.execute(
                "SELECT * FROM world_events WHERE status = 'committed' ORDER BY occurred_at DESC, id DESC LIMIT 20"
            ).fetchall()
            memory_unavailable = False
            try:
                facts = connection.execute(
                    "SELECT id, namespace, subject_id, \"key\" AS key, value, canonical_hash, status, importance, last_prompt_at, updated_at FROM facts WHERE status = 'active' ORDER BY updated_at DESC, id"
                ).fetchall()
            except sqlite3.Error:
                facts = []
                memory_unavailable = True
            message_rows = connection.execute(
                """
                SELECT m.*, o.kind, o.status AS operation_status
                FROM messages AS m JOIN operations AS o ON o.id = m.operation_id
                WHERE m.session_id = ? AND o.status = 'succeeded'
                ORDER BY m.seq DESC LIMIT ?
                """,
                (session_id, history_limit),
            ).fetchall()
            messages = list(reversed(message_rows))
            return {
                "session": dict(session),
                "persona": dict(persona),
                "character": dict(character),
                "relationship": dict(relationship),
                "threads": [dict(row) for row in threads],
                "events": [dict(row) for row in reversed(events)],
                "facts": [dict(row) for row in facts],
                "memory_unavailable": memory_unavailable,
                "messages": [dict(row) for row in messages],
            }

    def requeue_operation(
        self,
        *,
        operation_id: str,
        session_id: str,
        request_hash: str,
        request_text: str,
        now: datetime | None = None,
    ) -> None:
        _validate_uuid4(operation_id, "operation_id")
        _validate_uuid4(session_id, "session_id")
        timestamp = to_utc_iso(now or self.clock.now())
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE operations
                SET status = 'pending', request_text = ?, error_code = NULL,
                    warnings_json = '[]', result_meta_json = '{}', elapsed_ms = NULL,
                    call_count = 0, user_message_id = NULL, assistant_message_id = NULL,
                    updated_at = ?
                WHERE id = ? AND kind = 'turn' AND session_id = ? AND request_hash = ?
                  AND status IN ('failed', 'interrupted')
                """,
                (request_text, timestamp, operation_id, session_id, request_hash),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("operation cannot be retried")

    def fail_operation(
        self,
        *,
        operation_id: str,
        error_code: str,
        warnings: list[str] | tuple[str, ...] = (),
        elapsed_ms: int = 0,
        call_count: int = 0,
        now: datetime | None = None,
    ) -> None:
        _validate_uuid4(operation_id, "operation_id")
        timestamp = to_utc_iso(now or self.clock.now())
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE operations
                SET status = 'failed', request_text = NULL, error_code = ?, warnings_json = ?,
                    elapsed_ms = ?, call_count = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (error_code, json.dumps(list(warnings), ensure_ascii=False), max(0, elapsed_ms), max(0, call_count), timestamp, operation_id),
            )

    def complete_turn(
        self,
        *,
        operation_id: str,
        session_id: str,
        user_text: str,
        assistant_text: str,
        now: datetime,
        state: object,
        persona_version_id: str,
        fact_ids: tuple[str, ...],
        event_ids: tuple[str, ...],
        warnings: list[str] | tuple[str, ...],
        elapsed_ms: int,
        call_count: int,
        world_update_ms: int = 0,
        fact_candidates: tuple[FactCandidate, ...] | list[FactCandidate] = (),
        persona_proposals: tuple[PersonaProposal, ...] | list[PersonaProposal] = (),
    ) -> tuple[dict, dict]:
        _validate_uuid4(operation_id, "operation_id")
        _validate_uuid4(session_id, "session_id")
        timestamp = to_utc_iso(now)
        turn_warnings = warnings if isinstance(warnings, list) else list(warnings)
        user_message_id, assistant_message_id = str(uuid4()), str(uuid4())
        result_meta = {
            "persona_version_id": persona_version_id,
            "fact_ids": list(fact_ids),
            "world_event_ids": list(event_ids),
            "state": {
                "valence": state.valence,
                "energy": state.energy,
                "closeness": state.closeness,
                "guard_until": to_utc_iso(state.guard_until) if state.guard_until else None,
            },
            "world_update_ms": max(0, world_update_ms),
        }
        with self.transaction() as connection:
            op = connection.execute(
                "SELECT status, session_id FROM operations WHERE id = ? AND kind = 'turn'",
                (operation_id,),
            ).fetchone()
            session = connection.execute("SELECT archived FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if op is None or op["status"] != "pending" or op["session_id"] != session_id:
                raise RuntimeError("operation is not pending for this session")
            if session is None or session["archived"]:
                raise PermissionError("session is missing or archived")
            seq = connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM messages").fetchone()[0]
            connection.execute(
                """INSERT INTO messages(id, seq, session_id, operation_id, role, content, redacted, created_at)
                   VALUES (?, ?, ?, ?, 'user', ?, 0, ?)""",
                (user_message_id, seq, session_id, operation_id, user_text, timestamp),
            )
            connection.execute(
                """INSERT INTO messages(id, seq, session_id, operation_id, role, content, redacted, created_at)
                   VALUES (?, ?, ?, ?, 'assistant', ?, 0, ?)""",
                (assistant_message_id, seq + 1, session_id, operation_id, assistant_text, timestamp),
            )
            connection.execute("SAVEPOINT candidate_persistence")
            try:
                self._persist_turn_proposals(
                    connection,
                    user_message_id=user_message_id,
                    fact_candidates=fact_candidates,
                    persona_proposals=persona_proposals,
                    user_text=user_text,
                    now=timestamp,
                )
            except sqlite3.Error:
                connection.execute("ROLLBACK TO SAVEPOINT candidate_persistence")
                connection.execute("RELEASE SAVEPOINT candidate_persistence")
                if "MEMORY_UNAVAILABLE" not in turn_warnings:
                    turn_warnings.append("MEMORY_UNAVAILABLE")
            else:
                connection.execute("RELEASE SAVEPOINT candidate_persistence")
            if state.write_character:
                connection.execute(
                    "UPDATE character_state SET valence = ?, energy = ?, updated_at = ?, version = ? WHERE character_id = 'suwan'",
                    (state.valence, state.energy, to_utc_iso(state.character_updated_at), state.character_version),
                )
            if state.write_relationship:
                connection.execute(
                    """UPDATE relationship_state SET closeness = ?, guard_until = ?, updated_at = ?, version = ?
                       WHERE user_id = 'local-user' AND character_id = 'suwan'""",
                    (state.closeness, to_utc_iso(state.guard_until) if state.guard_until else None,
                     to_utc_iso(state.relationship_updated_at), state.relationship_version),
                )
            references = [(assistant_message_id, "persona_version", persona_version_id)]
            references.extend((assistant_message_id, "fact", ref_id) for ref_id in fact_ids)
            references.extend((assistant_message_id, "world_event", ref_id) for ref_id in event_ids)
            connection.executemany(
                "INSERT INTO prompt_refs(assistant_message_id, ref_type, ref_id) VALUES (?, ?, ?)",
                references,
            )
            if fact_ids:
                # Memory decay bookkeeping: facts injected into this turn's
                # prompt count as "revisited", resetting their recency clock.
                marks = ",".join("?" for _ in fact_ids)
                connection.execute(
                    f"UPDATE facts SET last_prompt_at = ? WHERE id IN ({marks})",
                    (timestamp, *tuple(fact_ids)),
                )
            connection.execute(
                """UPDATE operations SET status = 'succeeded', request_text = NULL,
                   user_message_id = ?, assistant_message_id = ?, error_code = NULL,
                   warnings_json = ?, result_meta_json = ?, elapsed_ms = ?, call_count = ?, updated_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (user_message_id, assistant_message_id, json.dumps(turn_warnings, ensure_ascii=False),
                 json.dumps(result_meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 max(0, elapsed_ms), max(0, call_count), timestamp, operation_id),
            )
            connection.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (timestamp, session_id))
            user_row = connection.execute("SELECT * FROM messages WHERE id = ?", (user_message_id,)).fetchone()
            assistant_row = connection.execute("SELECT * FROM messages WHERE id = ?", (assistant_message_id,)).fetchone()
            return _message_object(user_row), _message_object(assistant_row)

    def complete_skipped_initiative(
        self,
        *,
        operation_id: str,
        session_id: str,
        reason: str,
        warnings: list[str] | tuple[str, ...] = (),
        now: datetime,
        elapsed_ms: int,
        world_update_ms: int = 0,
    ) -> None:
        _validate_uuid4(operation_id, "operation_id")
        _validate_uuid4(session_id, "session_id")
        if reason not in {
            "DISABLED", "QUIET_HOURS", "GUARD_ACTIVE", "LOW_ENERGY",
            "COOLDOWN", "NO_RECENT_EVENT", "WAITING_USER",
        }:
            raise ValueError("invalid initiative skip reason")
        timestamp = to_utc_iso(now)
        result_meta = json.dumps(
            {"sent": False, "reason": reason, "world_update_ms": max(0, world_update_ms)},
            sort_keys=True,
            separators=(",", ":"),
        )
        warning_json = json.dumps(list(warnings), ensure_ascii=False)
        with self.transaction() as connection:
            operation = connection.execute(
                "SELECT status, kind, session_id FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
            session = connection.execute("SELECT archived FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if operation is None or operation["status"] != "pending" or operation["kind"] != "initiative" or operation["session_id"] != session_id:
                raise RuntimeError("initiative operation is not pending")
            if session is None or session["archived"]:
                raise PermissionError("session is missing or archived")
            connection.execute(
                """UPDATE operations SET status = 'succeeded', request_text = NULL, error_code = NULL,
                   warnings_json = ?, result_meta_json = ?, elapsed_ms = ?, call_count = 0, updated_at = ?
                   WHERE id = ?""",
                (warning_json, result_meta, max(0, elapsed_ms), timestamp, operation_id),
            )

    def complete_initiative(
        self,
        *,
        operation_id: str,
        session_id: str,
        assistant_text: str,
        persona_version_id: str,
        event_id: str,
        now: datetime,
        elapsed_ms: int,
        call_count: int,
        world_update_ms: int = 0,
    ) -> dict:
        _validate_uuid4(operation_id, "operation_id")
        _validate_uuid4(session_id, "session_id")
        _validate_uuid4(persona_version_id, "persona_version_id")
        _validate_uuid4(event_id, "event_id")
        timestamp = to_utc_iso(now)
        assistant_message_id = str(uuid4())
        result_meta = json.dumps(
            {"sent": True, "reason": "SENT", "world_update_ms": max(0, world_update_ms)},
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.transaction() as connection:
            operation = connection.execute(
                "SELECT status, kind, session_id FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
            session = connection.execute("SELECT archived FROM sessions WHERE id = ?", (session_id,)).fetchone()
            event = connection.execute(
                "SELECT status FROM world_events WHERE id = ?", (event_id,)
            ).fetchone()
            persona = connection.execute(
                "SELECT 1 FROM persona_versions WHERE id = ?", (persona_version_id,)
            ).fetchone()
            if operation is None or operation["status"] != "pending" or operation["kind"] != "initiative" or operation["session_id"] != session_id:
                raise RuntimeError("initiative operation is not pending")
            if session is None or session["archived"]:
                raise PermissionError("session is missing or archived")
            if event is None or event["status"] != "committed" or persona is None:
                raise RuntimeError("initiative references are no longer available")
            seq = connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM messages").fetchone()[0]
            connection.execute(
                """INSERT INTO messages(id, seq, session_id, operation_id, role, content, redacted, created_at)
                   VALUES (?, ?, ?, ?, 'assistant', ?, 0, ?)""",
                (assistant_message_id, seq, session_id, operation_id, assistant_text, timestamp),
            )
            connection.executemany(
                "INSERT INTO prompt_refs(assistant_message_id, ref_type, ref_id) VALUES (?, ?, ?)",
                (
                    (assistant_message_id, "persona_version", persona_version_id),
                    (assistant_message_id, "world_event", event_id),
                ),
            )
            connection.execute(
                "UPDATE app_meta SET value = ? WHERE key = 'initiative_last_at'",
                (timestamp,),
            )
            connection.execute(
                """UPDATE operations SET status = 'succeeded', request_text = NULL, user_message_id = NULL,
                   assistant_message_id = ?, error_code = NULL, warnings_json = '[]', result_meta_json = ?,
                   elapsed_ms = ?, call_count = ?, updated_at = ? WHERE id = ? AND status = 'pending'""",
                (assistant_message_id, result_meta, max(0, elapsed_ms), max(0, call_count), timestamp, operation_id),
            )
            connection.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (timestamp, session_id))
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (assistant_message_id,)).fetchone()
            assert row is not None
            return _message_object(row)

    def fail_initiative(
        self,
        *,
        operation_id: str,
        error_code: str,
        elapsed_ms: int,
        call_count: int,
        now: datetime,
        warnings: list[str] | tuple[str, ...] = (),
        world_update_ms: int = 0,
    ) -> None:
        _validate_uuid4(operation_id, "operation_id")
        timestamp = to_utc_iso(now)
        result_meta = json.dumps(
            {"sent": False, "reason": "MODEL_FAILED", "world_update_ms": max(0, world_update_ms)},
            sort_keys=True,
            separators=(",", ":"),
        )
        warning_json = json.dumps(list(warnings), ensure_ascii=False)
        with self.transaction() as connection:
            connection.execute(
                """UPDATE operations SET status = 'failed', request_text = NULL, error_code = ?,
                   warnings_json = ?, result_meta_json = ?, elapsed_ms = ?, call_count = ?, updated_at = ?
                   WHERE id = ? AND kind = 'initiative' AND status = 'pending'""",
                (error_code, warning_json, result_meta, max(0, elapsed_ms), max(0, call_count), timestamp, operation_id),
            )

    @staticmethod
    def _persist_turn_proposals(
        connection: sqlite3.Connection,
        *,
        user_message_id: str,
        fact_candidates: tuple[FactCandidate, ...] | list[FactCandidate],
        persona_proposals: tuple[PersonaProposal, ...] | list[PersonaProposal],
        user_text: str,
        now: str,
    ) -> None:
        candidates = validate_fact_candidates(fact_candidates, user_text)
        proposed: list[tuple[str, dict]] = [
            (
                "user_fact",
                {"key": item.key, "value": item.value, "evidence_quote": item.evidence_quote},
            )
            for item in candidates
        ]
        proposed.extend(
            (
                "persona",
                {"field": item.field, "value": item.value, "reason": item.reason},
            )
            for item in persona_proposals
        )
        for kind, payload in proposed:
            signature = normalized_payload_signature(kind, payload)
            serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            duplicate_for_source = False
            duplicate_pending = False
            rows = connection.execute(
                "SELECT id, kind, payload_json, status, source_message_id FROM proposals WHERE kind = ?",
                (kind,),
            ).fetchall()
            for row in rows:
                try:
                    existing_payload = json.loads(row["payload_json"])
                    same_candidate = normalized_payload_signature(kind, existing_payload) == signature
                except (ValueError, TypeError, json.JSONDecodeError, MemoryValidationError):
                    continue
                if same_candidate and row["source_message_id"] == user_message_id:
                    duplicate_for_source = True
                if same_candidate and row["status"] == "pending":
                    duplicate_pending = True
            if duplicate_for_source or duplicate_pending:
                continue
            if kind == "user_fact":
                try:
                    _, fact_hash = validate_fact_value(payload["key"], payload["value"])
                except MemoryValidationError:
                    continue
                active = connection.execute(
                    "SELECT 1 FROM facts WHERE namespace = 'user' AND subject_id = 'local-user' AND \"key\" = ? AND status = 'active' AND canonical_hash = ?",
                    (payload["key"], fact_hash),
                ).fetchone()
                if active is not None:
                    continue
            connection.execute(
                """INSERT INTO proposals(id, kind, payload_json, source_message_id, status, result_id, created_at, resolved_at)
                   VALUES (?, ?, ?, ?, 'pending', NULL, ?, NULL)""",
                (str(uuid4()), kind, serialized, user_message_id, now),
            )

    def list_memory_facts(self, status: str | None = "active") -> list[dict]:
        allowed = {None, "active", "superseded", "forgotten"}
        if status not in allowed:
            raise ValueError("invalid fact status")
        with self.connection() as connection:
            sql = "SELECT * FROM facts WHERE namespace = 'user' AND subject_id = 'local-user'"
            params: tuple[object, ...] = ()
            if status is not None:
                sql += " AND status = ?"
                params = (status,)
            sql += " ORDER BY created_at DESC, id"
            rows = connection.execute(sql, params).fetchall()
            result = []
            for row in rows:
                sources = connection.execute(
                    """SELECT m.id, m.session_id, m.role, m.content, m.redacted, m.created_at
                       FROM fact_sources AS fs JOIN messages AS m ON m.id = fs.message_id
                       WHERE fs.fact_id = ? ORDER BY m.created_at, m.id""",
                    (row["id"],),
                ).fetchall()
                result.append({
                    **dict(row),
                    "source_message_ids": [source["id"] for source in sources],
                    "sources": [_message_source_object(source) for source in sources],
                })
            return result

    def get_current_persona(self) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT id, version, anchors_json, preferences_json, voice_text, created_at FROM persona_versions ORDER BY version DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row is not None else None

    def get_fact(self, fact_id: str) -> dict | None:
        _validate_uuid4(fact_id, "fact_id")
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM facts WHERE id = ? AND namespace = 'user' AND subject_id = 'local-user'",
                (fact_id,),
            ).fetchone()
            if row is None:
                return None
            sources = connection.execute(
                """SELECT m.id, m.session_id, m.role, m.content, m.redacted, m.created_at
                   FROM fact_sources AS fs JOIN messages AS m ON m.id = fs.message_id
                   WHERE fs.fact_id = ? ORDER BY m.created_at, m.id""",
                (fact_id,),
            ).fetchall()
            return {
                **dict(row),
                "source_message_ids": [source["id"] for source in sources],
                "sources": [_message_source_object(source) for source in sources],
            }

    def add_manual_fact(self, key: str, value: str, now: datetime | None = None) -> tuple[dict, bool]:
        value, fact_hash = validate_fact_value(key, value)
        timestamp = to_utc_iso(now or self.clock.now())
        with self.transaction() as connection:
            active = connection.execute(
                "SELECT * FROM facts WHERE namespace = 'user' AND subject_id = 'local-user' AND \"key\" = ? AND status = 'active'",
                (key,),
            ).fetchone()
            if active is not None and active["canonical_hash"] == fact_hash:
                return dict(active), False
            supersedes_id = active["id"] if active is not None else None
            if active is not None:
                connection.execute("UPDATE facts SET status = 'superseded', updated_at = ? WHERE id = ?", (timestamp, active["id"]))
            fact_id = str(uuid4())
            connection.execute(
                """INSERT INTO facts(id, namespace, subject_id, \"key\", value, canonical_hash, status, origin, importance, last_prompt_at, supersedes_id, created_at, updated_at)
                   VALUES (?, 'user', 'local-user', ?, ?, ?, 'active', 'manual', ?, NULL, ?, ?, ?)""",
                (fact_id, key, value, fact_hash, fact_importance(key), supersedes_id, timestamp, timestamp),
            )
            row = connection.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
            return {**dict(row), "source_message_ids": []}, True

    def edit_manual_fact(self, fact_id: str, value: str, now: datetime | None = None) -> tuple[dict, bool]:
        _validate_uuid4(fact_id, "fact_id")
        timestamp = to_utc_iso(now or self.clock.now())
        with self.transaction() as connection:
            old = connection.execute(
                "SELECT * FROM facts WHERE id = ? AND namespace = 'user' AND subject_id = 'local-user'",
                (fact_id,),
            ).fetchone()
            if old is None:
                raise LookupError("fact not found")
            if old["status"] != "active":
                raise PermissionError("fact is not active")
            value, fact_hash = validate_fact_value(old["key"], value)
            connection.execute("UPDATE facts SET status = 'superseded', updated_at = ? WHERE id = ?", (timestamp, fact_id))
            new_id = str(uuid4())
            connection.execute(
                """INSERT INTO facts(id, namespace, subject_id, \"key\", value, canonical_hash, status, origin, importance, last_prompt_at, supersedes_id, created_at, updated_at)
                   VALUES (?, 'user', 'local-user', ?, ?, ?, 'active', 'manual', ?, NULL, ?, ?, ?)""",
                (new_id, old["key"], value, fact_hash, fact_importance(old["key"]), fact_id, timestamp, timestamp),
            )
            row = connection.execute("SELECT * FROM facts WHERE id = ?", (new_id,)).fetchone()
            return {**dict(row), "source_message_ids": []}, True

    def list_proposals(self, *, kind: str | None = None, status: str = "pending") -> list[dict]:
        if kind not in {None, "user_fact", "persona"} or status not in {"pending", "accepted", "rejected", "invalidated"}:
            raise ValueError("invalid proposal filter")
        with self.connection() as connection:
            sql = "SELECT * FROM proposals WHERE status = ?"
            params: list[object] = [status]
            if kind is not None:
                sql += " AND kind = ?"
                params.append(kind)
            sql += " ORDER BY created_at DESC, id"
            rows = connection.execute(sql, params).fetchall()
            result = []
            for row in rows:
                source = connection.execute(
                    "SELECT id, session_id, role, content, redacted, created_at FROM messages WHERE id = ?",
                    (row["source_message_id"],),
                ).fetchone()
                result.append(
                    {
                        **dict(row),
                        "payload": json.loads(row["payload_json"]),
                        "source": _message_source_object(source) if source is not None else None,
                    }
                )
            return result

    @staticmethod
    def _accept_persona_proposal(
        connection: sqlite3.Connection,
        proposal: sqlite3.Row,
        timestamp: str,
    ) -> str:
        try:
            payload = json.loads(proposal["payload_json"])
            candidate = PersonaProposal.model_validate(payload)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ProposalInvalid("persona proposal payload is invalid") from exc
        previous = connection.execute(
            "SELECT * FROM persona_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if previous is None:
            raise PersonaDataUnavailable("current persona version is missing")
        try:
            anchors_json, preferences_json, already_present = apply_persona_proposal(
                anchors_json=previous["anchors_json"],
                preferences_json=previous["preferences_json"],
                field=candidate.field,
                value=candidate.value,
            )
        except PersonaPolicyError as exc:
            if exc.code == "PROPOSAL_INVALID":
                raise ProposalInvalid(str(exc)) from exc
            raise
        if already_present:
            return previous["id"]

        next_version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM persona_versions"
        ).fetchone()[0]
        version_id = str(uuid4())
        connection.execute(
            """INSERT INTO persona_versions(
                   id, version, anchors_json, preferences_json, voice_text, source_hashes_json, created_at, proposal_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                version_id,
                next_version,
                anchors_json,
                preferences_json,
                previous["voice_text"],
                previous["source_hashes_json"],
                timestamp,
                proposal["id"],
            ),
        )
        return version_id

    def resolve_fact_proposal(self, proposal_id: str, decision: str, now: datetime | None = None) -> dict:
        _validate_uuid4(proposal_id, "proposal_id")
        if decision not in {"accept", "reject"}:
            raise ValueError("decision must be accept or reject")
        timestamp = to_utc_iso(now or self.clock.now())
        with self.transaction() as connection:
            proposal = connection.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
            if proposal is None:
                raise LookupError("proposal not found")
            live_source = None
            if decision == "accept":
                live_source = connection.execute(
                    "SELECT id, role, content, redacted FROM messages WHERE id = ?",
                    (proposal["source_message_id"],),
                ).fetchone()
                if live_source is None:
                    raise LookupError("proposal source not found")
                if live_source["redacted"] or live_source["content"] is None:
                    raise PermissionError("proposal source is redacted")
                if live_source["role"] != "user":
                    raise ProposalSourceInvalid("proposal source is not a user message")
            if proposal["status"] in {"accepted", "rejected"}:
                if proposal["status"] != ("accepted" if decision == "accept" else "rejected"):
                    raise PermissionError("proposal already has the opposite decision")
                return {"id": proposal_id, "status": proposal["status"], "result_id": proposal["result_id"]}
            if proposal["status"] != "pending":
                raise PermissionError("proposal is not pending")
            result_id = None
            if decision == "accept":
                if proposal["kind"] == "persona":
                    result_id = self._accept_persona_proposal(connection, proposal, timestamp)
                elif proposal["kind"] == "user_fact":
                    source = live_source
                    try:
                        payload = json.loads(proposal["payload_json"])
                        candidate = FactCandidate.model_validate(payload)
                        valid = validate_fact_candidates([candidate], source["content"])
                        value, fact_hash = validate_fact_value(candidate.key, candidate.value)
                    except (ValueError, TypeError, json.JSONDecodeError, MemoryValidationError) as exc:
                        raise ProposalInvalid("proposal payload is invalid") from exc
                    if not valid:
                        raise ProposalSourceInvalid("proposal source does not support this fact")
                    active = connection.execute(
                        "SELECT * FROM facts WHERE namespace = 'user' AND subject_id = 'local-user' AND \"key\" = ? AND status = 'active'",
                        (candidate.key,),
                    ).fetchone()
                    if active is not None and active["canonical_hash"] == fact_hash:
                        result_id = active["id"]
                    else:
                        supersedes_id = active["id"] if active is not None else None
                        if active is not None:
                            connection.execute("UPDATE facts SET status = 'superseded', updated_at = ? WHERE id = ?", (timestamp, active["id"]))
                        result_id = str(uuid4())
                        connection.execute(
                            """INSERT INTO facts(id, namespace, subject_id, \"key\", value, canonical_hash, status, origin, importance, last_prompt_at, supersedes_id, created_at, updated_at)
                               VALUES (?, 'user', 'local-user', ?, ?, ?, 'active', 'message', ?, NULL, ?, ?, ?)""",
                            (result_id, candidate.key, value, fact_hash, fact_importance(candidate.key), supersedes_id, timestamp, timestamp),
                        )
                    connection.execute(
                        "INSERT OR IGNORE INTO fact_sources(fact_id, message_id) VALUES (?, ?)",
                        (result_id, source["id"]),
                    )
                else:
                    raise ProposalInvalid("proposal kind is invalid")
            new_status = "accepted" if decision == "accept" else "rejected"
            connection.execute(
                "UPDATE proposals SET status = ?, result_id = ?, resolved_at = ? WHERE id = ?",
                (new_status, result_id, timestamp, proposal_id),
            )
            return {"id": proposal_id, "status": new_status, "result_id": result_id}

    def forget_fact(
        self,
        fact_id: str,
        *,
        redact_session_history: bool = True,
        now: datetime | None = None,
    ) -> dict:
        _validate_uuid4(fact_id, "fact_id")
        timestamp = to_utc_iso(now or self.clock.now())
        with self.transaction() as connection:
            target = connection.execute(
                "SELECT \"key\", status FROM facts WHERE id = ? AND namespace = 'user' AND subject_id = 'local-user'",
                (fact_id,),
            ).fetchone()
            if target is None:
                raise LookupError("fact not found")
            epoch_row = connection.execute("SELECT value FROM app_meta WHERE key = 'memory_epoch'").fetchone()
            if epoch_row is None:
                raise RuntimeError("memory epoch metadata is missing")
            try:
                memory_epoch = int(epoch_row["value"])
            except (ValueError, TypeError) as exc:
                raise RuntimeError("memory epoch metadata is invalid") from exc
            if target["status"] == "forgotten":
                return {"forgotten_fact_ids": [], "redacted_message_count": 0, "memory_epoch": memory_epoch}

            keys_to_process = {target["key"]}
            processed_keys: set[str] = set()
            forgotten_ids: set[str] = set()
            redacted_message_ids: set[str] = set()
            source_sessions: set[str] = set()
            changed_message_count = 0

            while keys_to_process - processed_keys:
                key = sorted(keys_to_process - processed_keys)[0]
                processed_keys.add(key)
                facts = connection.execute(
                    "SELECT id FROM facts WHERE namespace = 'user' AND subject_id = 'local-user' AND \"key\" = ?",
                    (key,),
                ).fetchall()
                fact_ids = {row["id"] for row in facts}
                forgotten_ids.update(fact_ids)
                connection.execute(
                    "UPDATE facts SET status = 'forgotten', value = NULL WHERE namespace = 'user' AND subject_id = 'local-user' AND \"key\" = ? AND status <> 'forgotten'",
                    (key,),
                )
                if not fact_ids:
                    continue
                placeholders = ",".join("?" for _ in fact_ids)
                fact_values = tuple(fact_ids)
                source_rows = connection.execute(
                    f"SELECT DISTINCT message_id FROM fact_sources WHERE fact_id IN ({placeholders})",
                    fact_values,
                ).fetchall()
                source_ids = {row["message_id"] for row in source_rows}
                if source_ids:
                    source_marks = ",".join("?" for _ in source_ids)
                    session_rows = connection.execute(
                        f"SELECT DISTINCT session_id FROM messages WHERE id IN ({source_marks})",
                        tuple(source_ids),
                    ).fetchall()
                    source_sessions.update(row["session_id"] for row in session_rows)
                redacted_message_ids.update(source_ids)

                if redact_session_history and source_sessions:
                    session_marks = ",".join("?" for _ in source_sessions)
                    session_message_rows = connection.execute(
                        f"SELECT id FROM messages WHERE session_id IN ({session_marks})",
                        tuple(source_sessions),
                    ).fetchall()
                    redacted_message_ids.update(row["id"] for row in session_message_rows)

                ref_rows = connection.execute(
                    f"SELECT assistant_message_id FROM prompt_refs WHERE ref_type = 'fact' AND ref_id IN ({placeholders})",
                    fact_values,
                ).fetchall()
                redacted_message_ids.update(row["assistant_message_id"] for row in ref_rows)

                if source_ids:
                    source_marks = ",".join("?" for _ in source_ids)
                    pair_rows = connection.execute(
                        f"""SELECT assistant.id FROM messages AS source
                            JOIN messages AS assistant ON assistant.operation_id = source.operation_id
                            WHERE source.id IN ({source_marks}) AND assistant.role = 'assistant'""",
                        tuple(source_ids),
                    ).fetchall()
                    redacted_message_ids.update(row["id"] for row in pair_rows)

                if redacted_message_ids:
                    marks = ",".join("?" for _ in redacted_message_ids)
                    cursor = connection.execute(
                        f"UPDATE messages SET content = NULL, redacted = 1 WHERE id IN ({marks}) AND (redacted = 0 OR content IS NOT NULL)",
                        tuple(redacted_message_ids),
                    )
                    changed_message_count += cursor.rowcount
                    user_rows = connection.execute(
                        f"SELECT id FROM messages WHERE id IN ({marks}) AND role = 'user'",
                        tuple(redacted_message_ids),
                    ).fetchall()
                    redacted_user_ids = {row["id"] for row in user_rows}
                    if redacted_user_ids:
                        user_marks = ",".join("?" for _ in redacted_user_ids)
                        dependent = connection.execute(
                            f"""SELECT DISTINCT f.\"key\" FROM facts AS f
                                JOIN fact_sources AS fs ON fs.fact_id = f.id
                                WHERE fs.message_id IN ({user_marks}) AND f.status = 'active'""",
                            tuple(redacted_user_ids),
                        ).fetchall()
                        keys_to_process.update(row["key"] for row in dependent)
                        op_rows = connection.execute(
                            f"SELECT DISTINCT operation_id FROM messages WHERE id IN ({user_marks})",
                            tuple(redacted_user_ids),
                        ).fetchall()
                        operation_ids = {row["operation_id"] for row in op_rows}
                        if operation_ids:
                            op_marks = ",".join("?" for _ in operation_ids)
                            connection.execute(
                                f"UPDATE operations SET request_text = NULL, updated_at = ? WHERE id IN ({op_marks})",
                                (timestamp, *tuple(operation_ids)),
                            )

            proposal_rows = connection.execute("SELECT * FROM proposals").fetchall()
            for proposal in proposal_rows:
                try:
                    payload = json.loads(proposal["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                payload_key = payload.get("key") if isinstance(payload, dict) else None
                same_key = payload_key in processed_keys
                source_redacted = proposal["source_message_id"] in redacted_message_ids
                if not same_key and not source_redacted:
                    continue
                if proposal["kind"] == "user_fact" and isinstance(payload_key, str):
                    safe_payload = {"key": payload_key}
                elif proposal["kind"] == "persona" and isinstance(payload, dict) and isinstance(payload.get("field"), str):
                    safe_payload = {"field": payload["field"]}
                else:
                    safe_payload = {"redacted": True}
                status = "invalidated" if proposal["status"] == "pending" else proposal["status"]
                connection.execute(
                    "UPDATE proposals SET payload_json = ?, status = ?, resolved_at = CASE WHEN ? = 'invalidated' THEN COALESCE(resolved_at, ?) ELSE resolved_at END WHERE id = ?",
                    (json.dumps(safe_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), status, status, timestamp, proposal["id"]),
                )

            memory_epoch += 1
            connection.execute("UPDATE app_meta SET value = ? WHERE key = 'memory_epoch'", (str(memory_epoch),))
            if redacted_message_ids:
                marks = ",".join("?" for _ in redacted_message_ids)
                # Keep the snapshot text (the CHECK constraint forbids empty
                # text); the invalidated status alone removes the material
                # from every saved-material view and blocks further edits.
                connection.execute(
                    f"""UPDATE materials SET status = 'invalidated', updated_at = ?
                        WHERE message_id IN ({marks}) AND status = 'saved'""",
                    (timestamp, *tuple(redacted_message_ids)),
                )
            return {
                "forgotten_fact_ids": sorted(forgotten_ids),
                "redacted_message_count": changed_message_count,
                "memory_epoch": memory_epoch,
            }

    def create_pending_operation(
        self,
        *,
        operation_id: str,
        session_id: str,
        request_hash: str,
        request_text: str | None,
        kind: str = "turn",
        now: datetime | None = None,
    ) -> None:
        _validate_uuid4(operation_id, "operation_id")
        _validate_uuid4(session_id, "session_id")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", request_hash):
            raise ValueError("request_hash must be a SHA-256 hex digest")
        if request_text is not None and not isinstance(request_text, str):
            raise ValueError("request_text must be text or None")
        timestamp = to_utc_iso(now or self.clock.now())
        with self.transaction() as connection:
            if connection.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
                raise ValueError("session_id does not reference an existing session")
            connection.execute(
                """
                INSERT INTO operations(
                    id, kind, session_id, request_hash, request_text, status,
                    user_message_id, assistant_message_id, error_code,
                    warnings_json, result_meta_json, elapsed_ms, call_count,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, '[]', '{}', NULL, 0, ?, ?)
                """,
                (operation_id, kind, session_id, request_hash.lower(), request_text, timestamp, timestamp),
            )

    def get_operation(self, operation_id: str) -> dict | None:
        _validate_uuid4(operation_id, "operation_id")
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE id = ?",
                (operation_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def get_operation_messages(self, operation_id: str) -> dict[str, dict | None]:
        _validate_uuid4(operation_id, "operation_id")
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE operation_id = ? ORDER BY seq",
                (operation_id,),
            ).fetchall()
            by_role = {row["role"]: _message_object(row) for row in rows}
            return {"user": by_role.get("user"), "assistant": by_role.get("assistant")}

    def get_meta(self, key: str) -> str | None:
        with self.connection() as connection:
            row = connection.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row is not None else None

    def get_world_snapshot(self) -> dict:
        with self.connection() as connection:
            threads = connection.execute("SELECT * FROM life_threads ORDER BY template_key, id").fetchall()
            events = connection.execute(
                "SELECT * FROM world_events ORDER BY occurred_at DESC, id DESC LIMIT 20"
            ).fetchall()
            used_slots = connection.execute("SELECT slot_key FROM world_events").fetchall()
            character = connection.execute("SELECT * FROM character_state WHERE character_id = 'suwan'").fetchone()
            relationship = connection.execute(
                "SELECT * FROM relationship_state WHERE user_id = 'local-user' AND character_id = 'suwan'"
            ).fetchone()
            persona = connection.execute(
                "SELECT * FROM persona_versions ORDER BY version DESC LIMIT 1"
            ).fetchone()
            meta = connection.execute(
                "SELECT key, value FROM app_meta WHERE key IN ('world_cursor_at', 'initiative_last_at')"
            ).fetchall()
            if character is None or relationship is None or persona is None:
                raise RuntimeError("required world or persona state is missing")
            meta_values = {row["key"]: row["value"] for row in meta}
            if "world_cursor_at" not in meta_values or "initiative_last_at" not in meta_values:
                raise RuntimeError("required world metadata is missing")
            return {
                "threads": [dict(row) for row in threads],
                "events": [dict(row) for row in events],
                "used_slot_keys": {row["slot_key"] for row in used_slots},
                "character": dict(character),
                "relationship": dict(relationship),
                "persona": dict(persona),
                "world_cursor_at": meta_values["world_cursor_at"],
                "initiative_last_at": meta_values["initiative_last_at"],
            }

    def get_world_event(self, event_id: str) -> dict | None:
        _validate_uuid4(event_id, "event_id")
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM world_events WHERE id = ?", (event_id,)).fetchone()
            return dict(row) if row is not None else None

    def advance_world_cursor(self, *, expected_cursor: str, now: datetime) -> bool:
        timestamp = to_utc_iso(now)
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE app_meta SET value = ? WHERE key = 'world_cursor_at' AND value = ?",
                (timestamp, expected_cursor),
            )
            return cursor.rowcount == 1

    def commit_world_event(
        self,
        event: dict,
        *,
        expected_cursor: str,
        cursor_now: datetime,
        character_state: dict,
    ) -> dict | None:
        _validate_uuid4(event["id"], "event_id")
        _validate_uuid4(event["thread_id"], "thread_id")
        timestamp = to_utc_iso(cursor_now)
        with self.transaction() as connection:
            cursor = connection.execute(
                "SELECT value FROM app_meta WHERE key = 'world_cursor_at'"
            ).fetchone()
            if cursor is None or cursor["value"] != expected_cursor:
                return None
            thread = connection.execute(
                "SELECT * FROM life_threads WHERE id = ?", (event["thread_id"],)
            ).fetchone()
            if thread is None:
                return None
            if (
                thread["cycle"] != event["cycle"]
                or thread["step"] != event["step_before"]
                or thread["stage"] != event["stage_before"]
                or not thread["active"]
                or thread["step"] >= 3
            ):
                return None
            if connection.execute(
                "SELECT 1 FROM world_events WHERE slot_key = ?", (event["slot_key"],)
            ).fetchone() is not None:
                connection.execute(
                    "UPDATE app_meta SET value = ? WHERE key = 'world_cursor_at'",
                    (timestamp,),
                )
                return None

            before = {
                "cycle": thread["cycle"],
                "step": thread["step"],
                "stage": thread["stage"],
                "active": bool(thread["active"]),
                "last_event_at": thread["last_event_at"],
            }
            after = {
                "cycle": event["cycle"],
                "step": event["step_after"],
                "stage": event["stage_after"],
                "active": bool(thread["active"]),
                "last_event_at": event["occurred_at"],
            }
            if event["state_before_json"] != json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":")):
                return None
            if event["state_after_json"] != json.dumps(after, ensure_ascii=False, sort_keys=True, separators=(",", ":")):
                return None

            connection.execute(
                """INSERT INTO world_events(
                       id, slot_key, thread_id, cycle, step_before, step_after,
                       stage_before, stage_after, occurred_at, summary,
                       state_before_json, state_after_json, status, created_at, retracted_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, NULL)""",
                (
                    event["id"], event["slot_key"], event["thread_id"], event["cycle"],
                    event["step_before"], event["step_after"], event["stage_before"],
                    event["stage_after"], event["occurred_at"], event["summary"],
                    event["state_before_json"], event["state_after_json"], timestamp,
                ),
            )
            connection.execute(
                """UPDATE life_threads
                   SET step = ?, stage = ?, last_event_at = ? WHERE id = ?""",
                (event["step_after"], event["stage_after"], event["occurred_at"], event["thread_id"]),
            )
            connection.execute(
                """UPDATE character_state
                   SET valence = ?, energy = ?, updated_at = ?, version = ?
                   WHERE character_id = 'suwan'""",
                (
                    character_state["valence"], character_state["energy"],
                    to_utc_iso(character_state["updated_at"]), character_state["version"],
                ),
            )
            connection.execute(
                "UPDATE app_meta SET value = ? WHERE key = 'world_cursor_at'",
                (timestamp,),
            )
            row = connection.execute("SELECT * FROM world_events WHERE id = ?", (event["id"],)).fetchone()
            return dict(row)

    def latest_session_activity(self, session_id: str) -> dict | None:
        _validate_uuid4(session_id, "session_id")
        with self.connection() as connection:
            row = connection.execute(
                """SELECT m.id, m.role, m.operation_id, o.kind, o.status
                   FROM messages AS m JOIN operations AS o ON o.id = m.operation_id
                   WHERE m.session_id = ? ORDER BY m.seq DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def set_thread_control(self, thread_id: str, action: str) -> dict:
        _validate_uuid4(thread_id, "thread_id")
        stage_for_step = ("planned", "started", "progressed", "done")
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM life_threads WHERE id = ?", (thread_id,)).fetchone()
            if row is None:
                raise LookupError("world thread not found")
            step = int(row["step"])
            if action == "pause":
                if row["stage"] == "done" or step >= 3:
                    raise PermissionError("completed world thread cannot be paused")
                if row["stage"] != "paused":
                    connection.execute(
                        "UPDATE life_threads SET active = 0, stage = 'paused' WHERE id = ?",
                        (thread_id,),
                    )
            elif action == "resume":
                if step >= 3:
                    raise PermissionError("completed world thread cannot be resumed")
                if row["stage"] == "paused" or not row["active"]:
                    connection.execute(
                        "UPDATE life_threads SET active = 1, stage = ? WHERE id = ?",
                        (stage_for_step[step], thread_id),
                    )
            elif action == "restart":
                if row["stage"] != "done" or step != 3:
                    raise PermissionError("world thread is not complete")
                connection.execute(
                    """UPDATE life_threads
                       SET cycle = cycle + 1, step = 0, stage = 'planned', active = 1, last_event_at = NULL
                       WHERE id = ?""",
                    (thread_id,),
                )
            else:
                raise ValueError("invalid thread action")
            updated = connection.execute("SELECT * FROM life_threads WHERE id = ?", (thread_id,)).fetchone()
            assert updated is not None
            return dict(updated)

    def retract_world_event(self, event_id: str, *, state_before: dict, now: datetime) -> dict:
        _validate_uuid4(event_id, "event_id")
        timestamp = to_utc_iso(now)
        with self.transaction() as connection:
            event = connection.execute("SELECT * FROM world_events WHERE id = ?", (event_id,)).fetchone()
            if event is None:
                raise LookupError("world event not found")
            thread_row = connection.execute(
                "SELECT * FROM life_threads WHERE id = ?", (event["thread_id"],)
            ).fetchone()
            if thread_row is None:
                raise RuntimeError("world event thread is missing")
            if event["status"] == "retracted":
                return {"event": dict(event), "thread": dict(thread_row), "redacted_message_count": 0}
            if thread_row["cycle"] != event["cycle"]:
                raise PermissionError("world event has a successor")
            latest = connection.execute(
                """SELECT id FROM world_events WHERE thread_id = ? AND cycle = ? AND status = 'committed'
                   ORDER BY step_after DESC, occurred_at DESC, id DESC LIMIT 1""",
                (event["thread_id"], event["cycle"]),
            ).fetchone()
            if latest is None or latest["id"] != event_id:
                raise PermissionError("world event has a successor")
            if set(state_before) != {"cycle", "step", "stage", "active", "last_event_at"}:
                raise RuntimeError("world event state snapshot is invalid")
            connection.execute(
                """UPDATE life_threads
                   SET cycle = ?, step = ?, stage = ?, active = ?, last_event_at = ? WHERE id = ?""",
                (
                    state_before["cycle"], state_before["step"], state_before["stage"],
                    int(state_before["active"]), state_before["last_event_at"], event["thread_id"],
                ),
            )
            connection.execute(
                "UPDATE world_events SET status = 'retracted', retracted_at = ? WHERE id = ?",
                (timestamp, event_id),
            )
            changed = connection.execute(
                """UPDATE messages SET content = NULL, redacted = 1
                   WHERE role = 'assistant' AND redacted = 0 AND id IN (
                       SELECT assistant_message_id FROM prompt_refs
                       WHERE ref_type = 'world_event' AND ref_id = ?
                   )""",
                (event_id,),
            ).rowcount
            restored = connection.execute("SELECT * FROM life_threads WHERE id = ?", (event["thread_id"],)).fetchone()
            retracted = connection.execute("SELECT * FROM world_events WHERE id = ?", (event_id,)).fetchone()
            assert restored is not None and retracted is not None
            return {"event": dict(retracted), "thread": dict(restored), "redacted_message_count": changed}

    def upsert_goal(self, *, title: str, horizon: str, detail: str | None, status: str, now: datetime) -> str:
        """创建或更新人物目标（幂等 by title among active/idea）；返回 goal_id。"""
        timestamp = to_utc_iso(now)
        with self.transaction() as connection:
            gid = None
            existing = connection.execute(
                "SELECT id FROM character_goals WHERE title = ? AND status IN ('idea', 'active', 'paused')",
                (title,),
            ).fetchone()
            if existing is not None:
                gid = existing["id"]
            else:
                gid = str(uuid4())
            connection.execute(
                """INSERT INTO character_goals(id, title, horizon, detail, progress_step, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     title = excluded.title, horizon = excluded.horizon, detail = excluded.detail,
                     status = excluded.status, updated_at = excluded.updated_at""",
                (gid, title, horizon, detail, status, timestamp, timestamp),
            )
        return gid

    def list_goals(self, *, status: str | None = "active") -> list[dict]:
        with self.connection() as connection:
            if status is None:
                rows = connection.execute("SELECT * FROM character_goals ORDER BY updated_at DESC").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM character_goals WHERE status = ? ORDER BY updated_at DESC", (status,)
                ).fetchall()
            return [dict(row) for row in rows]

    def advance_goal(self, goal_id: str, *, now: datetime) -> dict | None:
        timestamp = to_utc_iso(now)
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM character_goals WHERE id = ?", (goal_id,)).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE character_goals SET progress_step = progress_step + 1, updated_at = ? WHERE id = ?",
                (timestamp, goal_id),
            )
            updated = connection.execute("SELECT * FROM character_goals WHERE id = ?", (goal_id,)).fetchone()
            return dict(updated)

    def upsert_commitment(self, *, kind: str, content: str, now: datetime) -> str:
        timestamp = to_utc_iso(now)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM character_commitments WHERE kind = ? AND content = ?", (kind, content)
            ).fetchone()
            if existing is not None:
                return existing["id"]
            cid = str(uuid4())
            connection.execute(
                "INSERT INTO character_commitments(id, kind, content, created_at) VALUES (?, ?, ?, ?)",
                (cid, kind, content, timestamp),
            )
            return cid

    def list_commitments(self) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM character_commitments ORDER BY created_at").fetchall()
            return [dict(row) for row in rows]

    def upsert_schedule(self, *, day_type: str, start_time: str, end_time: str, activity: str, now: datetime) -> None:
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM character_schedule WHERE day_type = ? AND start_time = ?",
                (day_type, start_time),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    "UPDATE character_schedule SET activity = ?, end_time = ? WHERE id = ?",
                    (activity, end_time, existing["id"]),
                )
                return
            connection.execute(
                "INSERT INTO character_schedule(id, day_type, start_time, end_time, activity, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid4()), day_type, start_time, end_time, activity, to_utc_iso(now)),
            )

    def list_schedule(self, day_type: str) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM character_schedule WHERE day_type = ? ORDER BY start_time", (day_type,)
            ).fetchall()
            return [dict(row) for row in rows]

    def set_current_activity(self, activity: str | None, *, now: datetime) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE character_state SET current_activity = ?, updated_at = ?, version = version + 1 WHERE character_id = 'suwan'",
                (activity, to_utc_iso(now)),
            )

    def record_action_intent(self, *, goal_id: str | None, candidates: list[str], chosen: str,
                             decided_by: str, validation: dict | None, sim_time: datetime, now: datetime) -> str:
        intent_id = str(uuid4())
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO action_intents(id, goal_id, candidate_actions, chosen_action, decided_by, validation, status, sim_time, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'executed', ?, ?)""",
                (intent_id, goal_id, json.dumps(candidates, ensure_ascii=False), chosen, decided_by,
                 json.dumps(validation or {}, ensure_ascii=False), to_utc_iso(sim_time), to_utc_iso(now)),
            )
        return intent_id

    def pending_stale_intents(self, *, older_than_minutes: int, now: datetime) -> int:
        """崩溃恢复：滞留过久的 pending intent 标记 skipped（不猜测结果）。"""
        cutoff = to_utc_iso(now - timedelta(minutes=older_than_minutes))
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE action_intents SET status = 'skipped' WHERE status = 'pending' AND created_at < ?",
                (cutoff,),
            )
            return cursor.rowcount

    def update_attention(self, *, topic: str, current_focus: str | None, disclosure: list[str], now: datetime) -> None:
        """维护注意力单行状态（upsert）；属于第一阶段的注意力解耦。"""
        timestamp = to_utc_iso(now)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO attention_state(id, current_focus, topic_hint, disclosure, updated_at)
                   VALUES (1, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     current_focus = excluded.current_focus,
                     topic_hint = excluded.topic_hint,
                     disclosure = excluded.disclosure,
                     updated_at = excluded.updated_at""",
                (current_focus, topic, json.dumps(disclosure, ensure_ascii=False), timestamp),
            )

    def get_attention(self) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM attention_state WHERE id = 1").fetchone()
            return dict(row) if row is not None else None

    def retract_user_message(self, message_id: str, *, now: datetime) -> dict:
        """撤回用户最近发送的一条消息：置 redacted，她不再看到该轮内容。

        只允许撤回自己最近 10 分钟内的 user 消息；已被移除的不能再撤。
        """
        _validate_uuid4(message_id, "message_id")
        timestamp = to_utc_iso(now)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT id, session_id, role, content, redacted, created_at FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                raise LookupError("message not found")
            if row["role"] != "user":
                raise PermissionError("only user messages can be retracted")
            if row["redacted"] or row["content"] is None:
                raise PermissionError("message is already retracted")
            created = parse_db_datetime(row["created_at"])
            age_seconds = (parse_db_datetime(timestamp) - created).total_seconds()
            if age_seconds > 600:
                raise PermissionError("retract window has expired")
            connection.execute(
                "UPDATE messages SET content = NULL, redacted = 1 WHERE id = ?",
                (message_id,),
            )
            return {
                "id": message_id,
                "session_id": row["session_id"],
                "retracted": True,
                "created_at": row["created_at"],
                "retracted_at": timestamp,
            }

    def record_call_metric(self, metric: CallMetric) -> None:
        """Persist call metadata in its own short transaction; never store prompt text."""
        if metric.operation_id is not None:
            _validate_uuid4(metric.operation_id, "operation_id")
        with self.transaction() as connection:
            if metric.operation_id is not None and connection.execute(
                "SELECT 1 FROM operations WHERE id = ?", (metric.operation_id,)
            ).fetchone() is None:
                raise ValueError("operation_id does not reference an existing operation")
            connection.execute(
                """
                INSERT INTO call_metrics(
                    id, operation_id, profile, attempt, status, latency_ms,
                    input_tokens, output_tokens, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    metric.operation_id,
                    metric.profile,
                    metric.attempt,
                    metric.status,
                    metric.latency_ms,
                    metric.input_tokens,
                    metric.output_tokens,
                    metric.created_at,
                ),
            )

    def diagnostics_call_metrics(self) -> dict:
        with self.connection() as connection:
            summary = connection.execute(
                """SELECT COUNT(*) AS call_count,
                          COALESCE(SUM(input_tokens), 0) AS input_tokens,
                          COALESCE(SUM(output_tokens), 0) AS output_tokens,
                          COALESCE(AVG(latency_ms), 0) AS average_latency_ms,
                          COALESCE(SUM(CASE WHEN status IN ('failed', 'invalid') THEN 1 ELSE 0 END), 0) AS degraded_count
                   FROM call_metrics"""
            ).fetchone()
            recent = connection.execute(
                """SELECT id, operation_id, profile, attempt, status, latency_ms,
                          input_tokens, output_tokens, created_at
                   FROM call_metrics ORDER BY created_at DESC, id DESC LIMIT 20"""
            ).fetchall()
            return {
                "call_count": summary["call_count"],
                "input_tokens": summary["input_tokens"],
                "output_tokens": summary["output_tokens"],
                "average_latency_ms": round(summary["average_latency_ms"]),
                "degraded_count": summary["degraded_count"],
                "recent_calls": [dict(row) for row in recent],
            }

    def recover_pending_operations(self, now: datetime | None = None) -> int:
        timestamp = to_utc_iso(now or self.clock.now())
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE operations
                SET status = 'interrupted',
                    error_code = 'PROCESS_INTERRUPTED',
                    request_text = NULL,
                    updated_at = ?
                WHERE status = 'pending'
                """,
                (timestamp,),
            )
            return cursor.rowcount

    _MATERIAL_PILLARS = frozenset(
        {"work_diary", "photo_aesthetics", "night_emotion", "cat_ai_girl", "uncategorized"}
    )
    _MATERIAL_HOOKS = frozenset(
        {"contrast", "specific_number", "suspense_question", "identity_resonance", "cat_hook", "scene_hook", "none"}
    )

    def save_material(
        self,
        *,
        message_id: str,
        pillar: str,
        hook_type: str = "none",
        note: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        _validate_uuid4(message_id, "message_id")
        if pillar not in self._MATERIAL_PILLARS:
            raise ValueError("invalid material pillar")
        if hook_type not in self._MATERIAL_HOOKS:
            raise ValueError("invalid material hook type")
        if note is not None and (not isinstance(note, str) or len(note) > 200):
            raise ValueError("material note must be empty or at most 200 characters")
        timestamp = to_utc_iso(now or self.clock.now())
        material_id = str(uuid4())
        with self.transaction() as connection:
            message = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            if message is None:
                raise LookupError("message not found")
            if message["redacted"] or message["content"] is None or not message["content"].strip():
                raise PermissionError("message content is unavailable")
            try:
                connection.execute(
                    """
                    INSERT INTO materials(
                        id, message_id, role, session_id, snapshot_text,
                        pillar, hook_type, note, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'saved', ?, ?)
                    """,
                    (
                        material_id,
                        message_id,
                        message["role"],
                        message["session_id"],
                        message["content"],
                        pillar,
                        hook_type,
                        note,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("message is already saved as a material") from exc
            row = connection.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
            assert row is not None
            return dict(row)

    def list_materials(self, *, status: str = "saved", pillar: str | None = None, limit: int = 200) -> list[dict]:
        if status not in {"saved", "discarded", "invalidated"}:
            raise ValueError("invalid material status filter")
        if pillar is not None and pillar not in self._MATERIAL_PILLARS:
            raise ValueError("invalid material pillar filter")
        with self.connection() as connection:
            sql = "SELECT * FROM materials WHERE status = ?"
            params: list[object] = [status]
            if pillar is not None:
                sql += " AND pillar = ?"
                params.append(pillar)
            sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
            params.append(max(0, int(limit)))
            rows = connection.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def update_material(
        self,
        material_id: str,
        *,
        pillar: str | None = None,
        hook_type: str | None = None,
        note: str | None = None,
        status: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        _validate_uuid4(material_id, "material_id")
        if pillar is not None and pillar not in self._MATERIAL_PILLARS:
            raise ValueError("invalid material pillar")
        if hook_type is not None and hook_type not in self._MATERIAL_HOOKS:
            raise ValueError("invalid material hook type")
        if note is not None and (not isinstance(note, str) or len(note) > 200):
            raise ValueError("material note must be empty or at most 200 characters")
        if status is not None and status not in {"saved", "discarded"}:
            raise ValueError("invalid material status")
        timestamp = to_utc_iso(now or self.clock.now())
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
            if row is None:
                raise LookupError("material not found")
            if row["status"] == "invalidated":
                raise PermissionError("invalidated material cannot be edited")
            updates: list[str] = []
            params: list[object] = []
            if pillar is not None:
                updates.append("pillar = ?")
                params.append(pillar)
            if hook_type is not None:
                updates.append("hook_type = ?")
                params.append(hook_type)
            if note is not None:
                updates.append("note = ?")
                params.append(note)
            if status is not None:
                updates.append("status = ?")
                params.append(status)
            if not updates:
                return dict(row)
            updates.append("updated_at = ?")
            params.append(timestamp)
            params.append(material_id)
            connection.execute(f"UPDATE materials SET {', '.join(updates)} WHERE id = ?", tuple(params))
            updated = connection.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
            assert updated is not None
            return dict(updated)
