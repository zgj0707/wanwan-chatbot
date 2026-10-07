from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, load_config
from app.db import initialize_database
from app.llm import FakeClient, FakeOutcome
from app.main import create_app
from app.pipeline import request_hash
from app.repository import Repository, mutation_guard


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config.example.yaml"
ANALYSIS_DELTA = json.dumps(
    {
        "state_delta": {"valence": 4, "energy": 2, "closeness": 1, "guard_trigger": False},
        "fact_candidates": [],
        "persona_proposals": [],
    },
    ensure_ascii=False,
)


@pytest.fixture
def client_factory(tmp_path: Path):
    clients: list[TestClient] = []

    def make(fake: FakeClient | None = None, config: AppConfig | None = None):
        app = create_app(
            config or load_config(EXAMPLE),
            project_dir=ROOT,
            data_dir=tmp_path / f"data-{len(clients)}",
            fake_client=fake or FakeClient(),
        )
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        client.__enter__()
        clients.append(client)
        client.headers.update({"X-Suwan-Token": app.state.session_token})
        return client, app

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def new_session(client: TestClient, title: str = "测试对话") -> str:
    response = client.post("/api/v1/sessions", json={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


def send(client: TestClient, session_id: str, text: str, operation_id: str | None = None, **extra):
    payload = {
        "operation_id": operation_id or str(uuid4()),
        "session_id": session_id,
        "text": text,
        **extra,
    }
    return client.post("/api/v1/chat", json=payload)


def test_success_commits_pair_state_and_prompt_references_atomically(client_factory):
    fake = FakeClient({"analysis": ANALYSIS_DELTA, "chat": "好呀，慢慢说。"})
    client, app = client_factory(fake)
    session_id = new_session(client)
    operation_id = str(uuid4())

    response = send(client, session_id, "今天有点开心", operation_id)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["replayed"] is False
    assert body["user_message"]["content"] == "今天有点开心"
    assert body["assistant_message"]["content"] == "好呀，慢慢说。"
    assert body["user_message"]["seq"] + 1 == body["assistant_message"]["seq"]
    assert body["call_count"] == 2
    assert len(fake.calls) == 2

    with app.state.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages WHERE operation_id = ?", (operation_id,)).fetchone()[0] == 2
        assert tuple(connection.execute("SELECT valence, energy FROM character_state WHERE character_id = 'suwan'").fetchone()) == (4, 62)
        assert connection.execute("SELECT closeness FROM relationship_state WHERE user_id = 'local-user'").fetchone()[0] == 11
        refs = connection.execute("SELECT ref_type, ref_id FROM prompt_refs WHERE assistant_message_id = ?", (body["assistant_message"]["id"],)).fetchall()
        assert {(row["ref_type"], row["ref_id"]) for row in refs} == {("persona_version", connection.execute("SELECT id FROM persona_versions").fetchone()[0])}


def test_successful_replay_rebuilds_from_current_database_and_conflicts_are_409(client_factory):
    fake = FakeClient()
    client, _ = client_factory(fake)
    session_id = new_session(client)
    operation_id = str(uuid4())
    first = send(client, session_id, "你好", operation_id)
    call_count = len(fake.calls)

    replays = [send(client, session_id, "你好", operation_id) for _ in range(20)]
    conflict = send(client, session_id, "换了内容", operation_id)
    other_session_conflict = send(client, new_session(client, "另一个会话"), "你好", operation_id)

    assert first.status_code == 200
    assert all(replay.status_code == 200 and replay.json()["replayed"] is True for replay in replays)
    assert all(replay.json()["assistant_message"] == first.json()["assistant_message"] for replay in replays)
    assert len(fake.calls) == call_count
    assert conflict.status_code == other_session_conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "OPERATION_CONFLICT"


def test_character_and_relationship_state_are_shared_across_sessions(client_factory):
    fake = FakeClient({"analysis": [ANALYSIS_DELTA, FakeClient._defaults["analysis"]]})
    client, app = client_factory(fake)
    session_ids = [new_session(client, f"第 {index + 1} 段") for index in range(20)]

    for index, session_id in enumerate(session_ids):
        response = send(client, session_id, "先说一件开心事" if index == 0 else f"第 {index + 1} 段继续聊")
        assert response.status_code == 200
        with app.state.repository.connection() as connection:
            assert tuple(connection.execute("SELECT valence, energy FROM character_state").fetchone()) == (4, 62)
            assert connection.execute("SELECT closeness FROM relationship_state").fetchone()[0] == 11

    with app.state.repository.connection() as connection:
        assert tuple(connection.execute("SELECT valence, energy FROM character_state").fetchone()) == (4, 62)
        assert connection.execute("SELECT closeness FROM relationship_state").fetchone()[0] == 11
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 40


@pytest.mark.parametrize(
    "trigger_body",
    [
        "BEFORE INSERT ON messages WHEN NEW.role = 'user'",
        "BEFORE INSERT ON messages WHEN NEW.role = 'assistant'",
        "BEFORE UPDATE ON character_state",
        "BEFORE UPDATE ON relationship_state",
        "BEFORE INSERT ON prompt_refs",
        "BEFORE UPDATE ON operations WHEN NEW.status = 'succeeded'",
        "BEFORE UPDATE ON sessions",
    ],
)
def test_chat_commit_faults_roll_back_the_complete_turn(client_factory, trigger_body):
    client, app = client_factory(FakeClient({"analysis": ANALYSIS_DELTA, "chat": "好呀，慢慢说。"}))
    session_id = new_session(client)
    with app.state.repository.transaction() as connection:
        connection.execute(
            f"CREATE TRIGGER injected_failure {trigger_body} BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
        )

    operation_id = str(uuid4())
    response = send(client, session_id, "今天有点开心", operation_id)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_ERROR"
    with app.state.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM prompt_refs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
        assert tuple(connection.execute("SELECT valence, energy FROM character_state").fetchone()) == (0, 60)
        assert connection.execute("SELECT closeness FROM relationship_state").fetchone()[0] == 10
        operation = connection.execute("SELECT status, user_message_id, assistant_message_id FROM operations WHERE id = ?", (operation_id,)).fetchone()
        assert tuple(operation) == ("failed", None, None)
        assert connection.execute("SELECT COUNT(*) FROM operations WHERE status = 'succeeded' AND assistant_message_id IS NULL").fetchone()[0] == 0


def test_chat_failure_leaves_no_half_turn_or_analysis_state_and_retry_reuses_operation(client_factory):
    fake = FakeClient({"analysis": ANALYSIS_DELTA, "chat": FakeOutcome(error_code="UPSTREAM_AUTH")})
    client, app = client_factory(fake)
    session_id = new_session(client)
    operation_id = str(uuid4())

    failed = send(client, session_id, "今天有点开心", operation_id)
    repeated = send(client, session_id, "今天有点开心", operation_id)
    with app.state.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert tuple(connection.execute("SELECT valence, energy FROM character_state").fetchone()) == (0, 60)
        assert connection.execute("SELECT COUNT(*) FROM call_metrics WHERE operation_id = ?", (operation_id,)).fetchone()[0] == 2
        assert tuple(connection.execute("SELECT status, request_text FROM operations WHERE id = ?", (operation_id,)).fetchone()) == ("failed", None)

    retried = send(client, session_id, "今天有点开心", operation_id, retry=True)

    assert failed.status_code == repeated.status_code == 502
    assert failed.json()["error"]["code"] == "UPSTREAM_AUTH"
    assert retried.status_code == 200
    with app.state.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM operations WHERE id = ?", (operation_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM messages WHERE operation_id = ?", (operation_id,)).fetchone()[0] == 2


def test_analysis_candidates_are_saved_pending_with_user_sources(client_factory):
    candidate_result = json.dumps({
        "state_delta": {"valence": 0, "energy": 0, "closeness": 0, "guard_trigger": False},
        "fact_candidates": [{"key": "preference.drink", "value": "喜欢乌龙茶", "evidence_quote": "我喜欢乌龙茶"}],
        "persona_proposals": [{"field": "preferences.likes", "value": "乌龙茶", "reason": "用户明确提到"}],
    }, ensure_ascii=False)
    fake = FakeClient({"analysis": candidate_result})
    client, app = client_factory(fake)
    session_id = new_session(client)
    result = send(client, session_id, "我喜欢乌龙茶")
    assert result.status_code == 200
    with app.state.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
        rows = connection.execute("SELECT kind, payload_json, status, source_message_id FROM proposals ORDER BY kind").fetchall()
        assert len(rows) == 2
        assert {row["kind"] for row in rows} == {"user_fact", "persona"}
        assert all(row["status"] == "pending" for row in rows)
        source = connection.execute("SELECT role, content FROM messages WHERE id = ?", (rows[0]["source_message_id"],)).fetchone()
        assert tuple(source) == ("user", "我喜欢乌龙茶")


def test_analysis_failure_degrades_neutrally_and_chat_can_succeed(client_factory):
    fake = FakeClient({"analysis": "{not-json", "chat": "Va bene."})
    client, app = client_factory(fake)
    session_id = new_session(client)

    response = send(client, session_id, "普通问候")

    assert response.status_code == 200
    assert "ANALYSIS_DEGRADED" in response.json()["warnings"]
    with app.state.repository.connection() as connection:
        assert tuple(connection.execute("SELECT valence, energy FROM character_state").fetchone()) == (0, 60)
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2


def test_prompt_over_budget_returns_413_without_committing_a_turn(client_factory):
    raw = load_config(EXAMPLE).model_dump(mode="python")
    raw["profiles"]["chat"]["input_budget_units"] = 1000
    config = AppConfig.model_validate(raw)
    fake = FakeClient()
    client, app = client_factory(fake, config)
    session_id = new_session(client)

    response = send(client, session_id, "问" * 500)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "CONTEXT_TOO_LARGE"
    assert len(fake.calls) == 1 and fake.calls[0]["profile"] == "analysis"
    with app.state.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert connection.execute("SELECT status FROM operations").fetchone()[0] == "failed"


def test_pending_request_is_202_and_busy_lock_is_nonblocking(client_factory):
    client, app = client_factory()
    session_id = new_session(client)
    operation_id = str(uuid4())
    digest = request_hash(session_id, "待处理")
    app.state.repository.create_pending_operation(
        operation_id=operation_id,
        session_id=session_id,
        request_hash=digest,
        request_text="待处理",
    )
    pending = send(client, session_id, "待处理", operation_id)
    assert pending.status_code == 202 and pending.json()["status"] == "pending"

    busy_operation = str(uuid4())
    with mutation_guard():
        busy = send(client, session_id, "忙锁测试", busy_operation)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "APP_BUSY"
    assert app.state.llm_client.fake_client.calls == []


def test_real_mode_without_both_credentials_fails_before_any_provider_call(client_factory, monkeypatch):
    monkeypatch.delenv("SUWAN_API_KEY", raising=False)
    raw = load_config(EXAMPLE).model_dump(mode="python")
    raw["mode"] = "real"
    config = AppConfig.model_validate(raw)
    fake = FakeClient()
    client, app = client_factory(fake, config)
    session_id = new_session(client)

    response = send(client, session_id, "你好")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CONFIG_MISSING"
    assert fake.calls == []
    operation = app.state.repository.get_operation(response.json()["error"]["operation_id"])
    assert operation["status"] == "failed" and operation["request_text"] is None


def test_startup_marks_pending_operation_interrupted_and_clears_request_text(tmp_path: Path):
    data_dir = tmp_path / "data"
    database_path = data_dir / "app.sqlite3"
    initialize_database(database_path)
    repository = Repository(database_path)
    session_id = repository.create_session("恢复测试")
    operation_id = str(uuid4())
    repository.create_pending_operation(
        operation_id=operation_id,
        session_id=session_id,
        request_hash=request_hash(session_id, "不能自动续发"),
        request_text="不能自动续发",
    )
    fake = FakeClient()
    app = create_app(load_config(EXAMPLE), project_dir=ROOT, data_dir=data_dir, fake_client=fake)

    with TestClient(app, base_url="http://127.0.0.1:8000"):
        operation = app.state.repository.get_operation(operation_id)

    assert operation["status"] == "interrupted"
    assert operation["error_code"] == "PROCESS_INTERRUPTED"
    assert operation["request_text"] is None
    assert fake.calls == []
