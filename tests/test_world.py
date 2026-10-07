from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.clock import FakeClock
from app.config import AppConfig, load_config
from app.llm import FakeClient, FakeOutcome
from app.main import create_app
from app.pipeline import request_hash


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config.example.yaml"
SATURDAY_NOON_GUANGZHOU = datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc)
SATURDAY_EIGHT_PM_GUANGZHOU = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
WORLD_SUMMARY = "我先挑出几张光线柔和的照片，放到一边慢慢整理。"
INITIATIVE_REPLY = "刚才整理照片时，我发现几张光线很像，放在一起还挺舒服的。"


def world_result(summary: str = WORLD_SUMMARY) -> str:
    return json.dumps({"summary": summary}, ensure_ascii=False)


@pytest.fixture
def client_factory(tmp_path: Path):
    clients: list[TestClient] = []

    def make(
        fake: FakeClient | None = None,
        *,
        config: AppConfig | None = None,
        start: datetime = SATURDAY_NOON_GUANGZHOU,
    ):
        clock = FakeClock(start)
        fake_client = fake or FakeClient(clock=clock)
        fake_client.clock = clock
        app = create_app(
            config or load_config(EXAMPLE),
            project_dir=ROOT,
            data_dir=tmp_path / f"world-data-{len(clients)}",
            fake_client=fake_client,
        )
        app.state.repository.clock = clock
        app.state.llm_client.clock = clock
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        client.__enter__()
        client.headers.update({"X-Suwan-Token": app.state.session_token})
        clients.append(client)
        return client, app, fake_client, clock

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def create_session(client: TestClient, title: str = "世界测试") -> str:
    response = client.post("/api/v1/sessions", json={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


def advance_clock(clock: FakeClock, value: datetime) -> None:
    clock.set(value)


def seed_photo_events(client: TestClient, clock: FakeClock, count: int = 1) -> list[dict]:
    events = []
    for index in range(count):
        advance_clock(clock, SATURDAY_EIGHT_PM_GUANGZHOU + timedelta(days=index))
        response = client.post("/api/v1/world/advance", json={})
        assert response.status_code == 200
        assert response.json()["advanced"] is True
        events.append(response.json()["event"])
    return events


def test_world_seed_slot_transition_fallback_and_deduplication(client_factory):
    client, app, fake, clock = client_factory(FakeClient(clock=FakeClock(SATURDAY_NOON_GUANGZHOU)))
    session_id = create_session(client)

    seeded = client.get("/api/v1/world")
    assert seeded.status_code == 200
    assert len(seeded.json()["threads"]) == 3
    assert seeded.json()["events"] == []
    assert {thread["step"] for thread in seeded.json()["threads"]} == {0}

    advance_clock(clock, SATURDAY_EIGHT_PM_GUANGZHOU)
    first = client.post("/api/v1/world/advance", json={})
    assert first.status_code == 200
    body = first.json()
    assert body["advanced"] is True
    assert body["event"]["slot_key"] == "2026-10-03|19:30"
    assert body["event"]["stage_before"] == "planned"
    assert body["event"]["stage_after"] == "started"
    assert body["event"]["summary"] == body["event"]["summary"].strip()
    assert 10 <= len(body["event"]["summary"]) <= 80
    assert "WORLD_FALLBACK" in body["warnings"]
    assert fake.calls[-1]["profile"] == "world"

    rows = app.state.repository.get_world_snapshot()
    photo = next(row for row in rows["threads"] if row["template_key"] == "photo_selection")
    assert (photo["step"], photo["stage"]) == (1, "started")
    assert rows["character"]["valence"] == 1
    assert rows["character"]["energy"] == 60
    assert body["event"]["state_before_json"]
    assert body["event"]["state_after_json"]

    repeated = [client.post("/api/v1/world/advance", json={}).json() for _ in range(100)]
    assert all(result["advanced"] is False and result["event"] is None for result in repeated)
    final = client.get("/api/v1/world").json()
    assert len(final["events"]) == 1
    assert next(thread for thread in final["threads"] if thread["template_key"] == "photo_selection")["step"] == 1
    assert client.get("/api/v1/sessions").json()["items"]


def test_world_updates_at_most_one_slot_after_long_gaps(client_factory):
    client, _app, fake, clock = client_factory(FakeClient({"world": world_result()}, clock=FakeClock(SATURDAY_NOON_GUANGZHOU)))
    initial = client.post("/api/v1/world/advance", json={}).json()
    assert initial["advanced"] is False
    assert fake.calls == []

    advance_clock(clock, SATURDAY_NOON_GUANGZHOU + timedelta(hours=1))
    one_hour = client.post("/api/v1/world/advance", json={}).json()
    assert one_hour["advanced"] is False

    advance_clock(clock, SATURDAY_EIGHT_PM_GUANGZHOU + timedelta(days=1))
    one_day = client.post("/api/v1/world/advance", json={}).json()
    assert one_day["advanced"] is True
    assert one_day["event"]["slot_key"] == "2026-10-04|19:30"

    advance_clock(clock, SATURDAY_EIGHT_PM_GUANGZHOU + timedelta(days=30))
    thirty_days = client.post("/api/v1/world/advance", json={}).json()
    assert thirty_days["advanced"] is True
    assert thirty_days["event"]["occurred_at"] >= (clock.now() - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    assert len(client.get("/api/v1/world").json()["events"]) == 2
    assert sum(call["profile"] == "world" for call in fake.calls) == 2


def test_world_transition_pause_resume_restart_and_completed_stays_done(client_factory):
    client, app, _fake, clock = client_factory(FakeClient({"world": [world_result()] * 3}, clock=FakeClock(SATURDAY_NOON_GUANGZHOU)))
    thread_id = next(row["id"] for row in app.state.repository.get_world_snapshot()["threads"] if row["template_key"] == "photo_selection")
    first = seed_photo_events(client, clock, 1)[0]

    paused = client.post(f"/api/v1/world/threads/{thread_id}/control", json={"action": "pause"})
    assert paused.status_code == 200
    assert paused.json()["stage"] == "paused" and paused.json()["step"] == 1 and paused.json()["active"] is False
    resumed = client.post(f"/api/v1/world/threads/{thread_id}/control", json={"action": "resume"})
    assert resumed.status_code == 200
    assert resumed.json()["stage"] == "started" and resumed.json()["active"] is True

    advance_clock(clock, SATURDAY_EIGHT_PM_GUANGZHOU + timedelta(days=1))
    second = client.post("/api/v1/world/advance", json={}).json()
    advance_clock(clock, SATURDAY_EIGHT_PM_GUANGZHOU + timedelta(days=2))
    third = client.post("/api/v1/world/advance", json={}).json()
    assert second["advanced"] is True and third["advanced"] is True
    assert [event["step_after"] for event in [first, second["event"], third["event"]]] == [1, 2, 3]
    completed = client.get("/api/v1/world").json()
    done_thread = next(row for row in completed["threads"] if row["id"] == thread_id)
    assert (done_thread["step"], done_thread["stage"]) == (3, "done")
    assert client.post(f"/api/v1/world/threads/{thread_id}/control", json={"action": "resume"}).status_code == 409

    advance_clock(clock, clock.now() + timedelta(hours=8))
    no_restart = client.post("/api/v1/world/advance", json={}).json()
    assert no_restart["advanced"] is False
    assert next(row for row in client.get("/api/v1/world").json()["threads"] if row["id"] == thread_id)["cycle"] == 0

    restarted = client.post(f"/api/v1/world/threads/{thread_id}/control", json={"action": "restart"})
    assert restarted.status_code == 200
    assert (restarted.json()["cycle"], restarted.json()["step"], restarted.json()["stage"]) == (1, 0, "planned")
    assert len(client.get("/api/v1/world").json()["events"]) == 3


def test_world_fallback_is_single_call_and_uses_stable_stage_copy(client_factory):
    bad = FakeOutcome(error_code="UPSTREAM_TIMEOUT", retryable=True, latency_ms=2)
    client, _app, fake, clock = client_factory(FakeClient({"world": bad}, clock=FakeClock(SATURDAY_NOON_GUANGZHOU)))
    advance_clock(clock, SATURDAY_EIGHT_PM_GUANGZHOU)
    first = client.post("/api/v1/world/advance", json={}).json()
    assert first["advanced"] is True
    assert "WORLD_FALLBACK" in first["warnings"]
    assert first["event"]["summary"]
    assert sum(call["profile"] == "world" for call in fake.calls) == 1


def test_retract_latest_event_redacts_chat_reply_and_preserves_prior_history(client_factory):
    fake = FakeClient({
        "world": world_result(),
        "analysis": json.dumps({
            "state_delta": {"valence": 0, "energy": 0, "closeness": 0, "guard_trigger": False},
            "fact_candidates": [], "persona_proposals": [],
        }, ensure_ascii=False),
        "chat": "嗯，慢慢整理时找到相似的光线还挺有意思。",
    }, clock=FakeClock(SATURDAY_NOON_GUANGZHOU))
    client, app, _fake, clock = client_factory(fake)
    event = seed_photo_events(client, clock, 1)[0]
    session_id = create_session(client)
    operation_id = str(uuid4())
    chat = client.post("/api/v1/chat", json={
        "operation_id": operation_id,
        "session_id": session_id,
        "text": "你刚才整理照片时有什么发现？",
    })
    assert chat.status_code == 200
    assistant_id = chat.json()["assistant_message"]["id"]
    with app.state.repository.connection() as refs:
        assert refs.execute(
            "SELECT 1 FROM prompt_refs WHERE assistant_message_id = ? AND ref_type = 'world_event' AND ref_id = ?",
            (assistant_id, event["id"]),
        ).fetchone()

    retracted = client.post(f"/api/v1/world/events/{event['id']}/retract", json={})
    assert retracted.status_code == 200
    assert retracted.json()["status"] == "retracted"
    assert retracted.json()["redacted_message_count"] == 1
    replay = client.get(f"/api/v1/operations/{operation_id}").json()
    assert replay["assistant_message"]["redacted"] is True
    assert replay["assistant_message"]["content"] is None
    assert replay["user_message"]["content"] == "你刚才整理照片时有什么发现？"
    assert client.get("/api/v1/world").json()["events"][0]["status"] == "retracted"


def test_cannot_retract_an_event_with_a_successor(client_factory):
    client, _app, _fake, clock = client_factory(FakeClient({"world": [world_result()] * 2}, clock=FakeClock(SATURDAY_NOON_GUANGZHOU)))
    first, _second = seed_photo_events(client, clock, 2)
    response = client.post(f"/api/v1/world/events/{first['id']}/retract", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EVENT_HAS_SUCCESSOR"


def test_initiative_skip_reasons_do_not_call_chat(client_factory):
    quiet_start = datetime(2026, 10, 3, 18, 0, tzinfo=timezone.utc)  # 02:00 in Guangzhou.
    quiet_client, _app, quiet_fake, _clock = client_factory(start=quiet_start)
    quiet_session = create_session(quiet_client)
    quiet = quiet_client.post("/api/v1/initiative", json={"operation_id": str(uuid4()), "session_id": quiet_session}).json()
    assert (quiet["sent"], quiet["reason"], quiet["call_count"]) == (False, "QUIET_HOURS", 0)
    assert not any(call["profile"] == "chat" for call in quiet_fake.calls)

    disabled_config = load_config(EXAMPLE).model_copy(
        update={"limits": load_config(EXAMPLE).limits.model_copy(update={"initiative_enabled": False})}
    )
    disabled_client, _app, disabled_fake, _clock = client_factory(config=disabled_config)
    disabled_session = create_session(disabled_client)
    disabled = disabled_client.post("/api/v1/initiative", json={"operation_id": str(uuid4()), "session_id": disabled_session}).json()
    assert disabled["reason"] == "DISABLED"
    assert not any(call["profile"] == "chat" for call in disabled_fake.calls)

    normal_client, app, normal_fake, clock = client_factory()
    session_id = create_session(normal_client)
    no_event = normal_client.post("/api/v1/initiative", json={"operation_id": str(uuid4()), "session_id": session_id}).json()
    assert no_event["reason"] == "NO_RECENT_EVENT"
    assert not any(call["profile"] == "chat" for call in normal_fake.calls)

    with app.state.repository.transaction() as connection:
        connection.execute("UPDATE character_state SET energy = 20 WHERE character_id = 'suwan'")
    low_energy = normal_client.post("/api/v1/initiative", json={"operation_id": str(uuid4()), "session_id": session_id}).json()
    assert low_energy["reason"] == "LOW_ENERGY"
    with app.state.repository.transaction() as connection:
        connection.execute("UPDATE character_state SET energy = 60 WHERE character_id = 'suwan'")
        connection.execute("UPDATE relationship_state SET guard_until = ? WHERE user_id = 'local-user'", ((clock.now() + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),))
    guarded = normal_client.post("/api/v1/initiative", json={"operation_id": str(uuid4()), "session_id": session_id}).json()
    assert guarded["reason"] == "GUARD_ACTIVE"


def test_initiative_success_is_one_assistant_message_replays_and_obeys_cooldown(client_factory):
    fake = FakeClient({"world": world_result(), "chat": INITIATIVE_REPLY}, clock=FakeClock(SATURDAY_NOON_GUANGZHOU))
    client, app, fake, clock = client_factory(fake)
    seed_photo_events(client, clock, 1)
    fake.calls.clear()
    session_id = create_session(client)
    operation_id = str(uuid4())
    with app.state.repository.connection() as before_relationship:
        closeness_before = before_relationship.execute("SELECT closeness FROM relationship_state WHERE user_id = 'local-user'").fetchone()[0]

    sent = client.post("/api/v1/initiative", json={"operation_id": operation_id, "session_id": session_id})
    assert sent.status_code == 200
    result = sent.json()
    assert result["sent"] is True and result["reason"] == "SENT"
    assert result["assistant_message"]["role"] == "assistant"
    assert result["call_count"] == 1
    assert [call["profile"] for call in fake.calls[-1:]] == ["chat"]
    messages = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"]
    assert len(messages) == 1 and messages[0]["role"] == "assistant"
    replay = client.post("/api/v1/initiative", json={"operation_id": operation_id, "session_id": session_id}).json()
    assert replay["replayed"] is True and replay["assistant_message"]["id"] == result["assistant_message"]["id"]
    assert len(fake.calls) == 1

    with app.state.repository.connection() as connection:
        closeness_after = connection.execute("SELECT closeness FROM relationship_state WHERE user_id = 'local-user'").fetchone()[0]
        prompt_refs = connection.execute(
            "SELECT ref_type, ref_id FROM prompt_refs WHERE assistant_message_id = ? ORDER BY ref_type",
            (result["assistant_message"]["id"],),
        ).fetchall()
    assert closeness_after == closeness_before
    assert {row["ref_type"] for row in prompt_refs} == {"persona_version", "world_event"}

    later = client.post("/api/v1/initiative", json={"operation_id": str(uuid4()), "session_id": session_id}).json()
    assert later["reason"] == "COOLDOWN"
    second_session = create_session(client, "切换后的会话")
    across_sessions = client.post(
        "/api/v1/initiative", json={"operation_id": str(uuid4()), "session_id": second_session}
    ).json()
    assert across_sessions["reason"] == "COOLDOWN"
    assert len([call for call in fake.calls if call["profile"] == "chat"]) == 1
    with app.state.repository.transaction() as connection:
        connection.execute(
            "UPDATE app_meta SET value = ? WHERE key = 'initiative_last_at'",
            ((clock.now() - timedelta(hours=7)).isoformat().replace("+00:00", "Z"),),
        )
    waiting = client.post("/api/v1/initiative", json={"operation_id": str(uuid4()), "session_id": session_id}).json()
    assert waiting["reason"] == "WAITING_USER"


def test_initiative_failure_does_not_consume_cooldown_and_replay_keeps_timing(client_factory):
    failed_chat = FakeOutcome(error_code="UPSTREAM_TIMEOUT", retryable=True, latency_ms=3)
    fake = FakeClient(
        {"world": world_result(), "chat": [failed_chat, INITIATIVE_REPLY]},
        clock=FakeClock(SATURDAY_NOON_GUANGZHOU),
    )
    client, app, fake, clock = client_factory(fake)
    seed_photo_events(client, clock, 1)
    session_id = create_session(client)
    before = app.state.repository.get_meta("initiative_last_at")
    operation_id = str(uuid4())

    failed = client.post("/api/v1/initiative", json={"operation_id": operation_id, "session_id": session_id}).json()
    assert (failed["status"], failed["sent"], failed["reason"], failed["call_count"]) == (
        "failed", False, "MODEL_FAILED", 1
    )
    assert failed["world_update_ms"] >= 0
    assert app.state.repository.get_meta("initiative_last_at") == before

    replay = client.post("/api/v1/initiative", json={"operation_id": operation_id, "session_id": session_id}).json()
    assert replay["replayed"] is True
    assert replay["world_update_ms"] == failed["world_update_ms"]
    assert len([call for call in fake.calls if call["profile"] == "chat"]) == 1

    next_attempt = client.post(
        "/api/v1/initiative", json={"operation_id": str(uuid4()), "session_id": session_id}
    ).json()
    assert next_attempt["sent"] is True
    assert app.state.repository.get_meta("initiative_last_at") == clock.now().isoformat().replace("+00:00", "Z")


def test_world_commit_timestamp_uses_clock_after_the_model_call(client_factory, monkeypatch):
    client, app, _fake, clock = client_factory(FakeClient({"world": world_result()}, clock=FakeClock(SATURDAY_NOON_GUANGZHOU)))
    advance_clock(clock, SATURDAY_EIGHT_PM_GUANGZHOU)
    original_generate = app.state.llm_client.generate

    def delayed_world_call(*args, **kwargs):
        result = original_generate(*args, **kwargs)
        clock.advance(seconds=5)
        return result

    monkeypatch.setattr(app.state.llm_client, "generate", delayed_world_call)
    response = client.post("/api/v1/world/advance", json={})
    assert response.status_code == 200
    event = response.json()["event"]
    snapshot = app.state.repository.get_world_snapshot()
    assert event["occurred_at"] < event["created_at"]
    assert event["created_at"] == clock.now().isoformat().replace("+00:00", "Z")
    assert snapshot["character"]["updated_at"] == event["created_at"]
    assert snapshot["world_cursor_at"] == event["created_at"]


def test_chat_auto_advance_is_committed_before_chat_and_included_in_result(client_factory):
    analysis = json.dumps({
        "state_delta": {"valence": 0, "energy": 0, "closeness": 0, "guard_trigger": False},
        "fact_candidates": [], "persona_proposals": [],
    }, ensure_ascii=False)
    fake = FakeClient({"world": world_result(), "analysis": analysis, "chat": "嗯，整理照片时把相似的光线放在一起了。"}, clock=FakeClock(SATURDAY_NOON_GUANGZHOU))
    client, _app, _fake, clock = client_factory(fake)
    session_id = create_session(client)
    advance_clock(clock, SATURDAY_EIGHT_PM_GUANGZHOU)
    response = client.post("/api/v1/chat", json={
        "operation_id": str(uuid4()),
        "session_id": session_id,
        "text": "你现在在忙什么？",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["world_update_ms"] >= 0
    assert body["call_count"] == 3
    assert body["warnings"] == []
    with _app.state.repository.connection() as connection:
        refs = connection.execute(
            "SELECT ref_type FROM prompt_refs WHERE assistant_message_id = ?",
            (body["assistant_message"]["id"],),
        ).fetchall()
    assert "world_event" in {row["ref_type"] for row in refs}


def test_all_paused_threads_advance_cursor_without_creating_events(client_factory):
    client, app, _fake, clock = client_factory(FakeClient(clock=FakeClock(SATURDAY_NOON_GUANGZHOU)))
    threads = app.state.repository.get_world_snapshot()["threads"]
    for thread in threads:
        paused = client.post(f"/api/v1/world/threads/{thread['id']}/control", json={"action": "pause"})
        assert paused.status_code == 200 and paused.json()["active"] is False

    clock.set(SATURDAY_EIGHT_PM_GUANGZHOU + timedelta(days=1))
    response = client.post("/api/v1/world/advance", json={})
    assert response.status_code == 200
    assert response.json()["advanced"] is False and response.json()["event"] is None
    updated = app.state.repository.get_world_snapshot()
    assert updated["events"] == []
    assert updated["world_cursor_at"] == clock.now().isoformat().replace("+00:00", "Z")


def test_no_eligible_slot_advances_cursor_without_creating_an_event(client_factory):
    client, app, _fake, clock = client_factory(
        FakeClient(clock=FakeClock(SATURDAY_EIGHT_PM_GUANGZHOU)), start=SATURDAY_EIGHT_PM_GUANGZHOU
    )
    clock.set(SATURDAY_EIGHT_PM_GUANGZHOU + timedelta(hours=6))
    response = client.post("/api/v1/world/advance", json={})
    assert response.status_code == 200
    assert response.json()["advanced"] is False and response.json()["event"] is None
    snapshot = app.state.repository.get_world_snapshot()
    assert snapshot["events"] == []
    assert snapshot["world_cursor_at"] == clock.now().isoformat().replace("+00:00", "Z")
    assert all(thread["active"] for thread in snapshot["threads"])


def test_initiative_hash_separates_it_from_turn_operation():
    session_id = str(uuid4())
    assert request_hash(session_id, "") != request_hash(session_id, "", kind="initiative")
