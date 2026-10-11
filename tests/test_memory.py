from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import load_config
from app.llm import FakeClient
from app.main import create_app
from app.repository import Repository
from app.memory import (
    MemoryDataUnavailable,
    canonical_fact_hash,
    character_bigrams,
    normalize_memory_text,
    retrieve_facts,
    validate_fact_candidates,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client_factory(tmp_path: Path):
    clients: list[TestClient] = []

    def make(fake: FakeClient | None = None, *, data_dir: Path | None = None):
        app = create_app(
            load_config(ROOT / "config.example.yaml"),
            project_dir=ROOT,
            data_dir=data_dir or tmp_path / f"memory-data-{len(clients)}",
            fake_client=fake or FakeClient(),
        )
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        client.__enter__()
        clients.append(client)
        client.headers.update({"X-Suwan-Token": app.state.session_token})
        return app, client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def create_session(client: TestClient, title: str = "记忆测试") -> str:
    response = client.post("/api/v1/sessions", json={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


def fact_analysis(candidates: list[dict]) -> str:
    return json.dumps(
        {
            "state_delta": {"valence": 0, "energy": 0, "closeness": 0, "guard_trigger": False},
            "fact_candidates": candidates,
            "persona_proposals": [],
        },
        ensure_ascii=False,
    )


def chat(client: TestClient, session_id: str, text: str, operation_id: str | None = None):
    return client.post(
        "/api/v1/chat",
        json={"operation_id": operation_id or str(uuid4()), "session_id": session_id, "text": text},
    )


def test_memory_normalization_and_bigrams_follow_nfkc_lower_and_punctuation_rules():
    assert normalize_memory_text(" Ａ，咖啡! ") == "a咖啡"
    assert character_bigrams("我") == {"我"}
    assert character_bigrams("手冲咖啡") == {"手冲", "冲咖", "咖啡"}
    assert character_bigrams(" ！！！ ") == set()
    assert canonical_fact_hash("preference.drink", "手冲咖啡") == canonical_fact_hash("preference.drink", "手 冲咖啡！")

    valid = validate_fact_candidates(
        [
            {"key": "profile.preferred_name", "value": "小满", "evidence_quote": "以后可以叫我小满"},
            {"key": "preference.drink", "value": "乌龙茶", "evidence_quote": "我喜欢乌龙茶"},
        ],
        "以后可以叫我小满；我喜欢乌龙茶",
    )
    assert [(item.key, item.value) for item in valid] == [("profile.preferred_name", "小满"), ("preference.drink", "乌龙茶")]
    assert validate_fact_candidates(
        [{"key": "profile.preferred_name", "value": "林某", "evidence_quote": "我叫林某"}],
        "我叫林某",
    ) == []
    assert validate_fact_candidates(
        [{"key": "preference.food", "value": "公司食堂", "evidence_quote": "我喜欢公司食堂"}],
        "我喜欢公司食堂",
    ) == []
    assert validate_fact_candidates(
        [{"key": "preference.drink", "value": "咖啡", "evidence_quote": "不是原句"}],
        "我喜欢乌龙茶",
    ) == []
    assert [(item.key, item.value) for item in validate_fact_candidates(
        [{"key": "profile.birthday", "value": "02-29", "evidence_quote": "我的生日是02-29"}],
        "我的生日是02-29",
    )] == [("profile.birthday", "02-29")]
    assert validate_fact_candidates(
        [{"key": "plan.personal", "value": "2026-10-07看展", "evidence_quote": "我计划2026-10-07看展"}],
        "我计划2026-10-07看展",
    )
    with pytest.raises(ValueError):
        validate_fact_candidates([{}, {}, {}], "测试")
    invalid_data = [{"id": str(uuid4()), "key": "preference.drink", "value": "咖啡", "canonical_hash": "0" * 64, "status": "active", "updated_at": "2026-10-07T00:00:00Z"}]
    with pytest.raises(MemoryDataUnavailable):
        retrieve_facts("咖啡", invalid_data)
    wrong_subject = [{
        "id": str(uuid4()), "namespace": "user", "subject_id": "another-user",
        "key": "preference.drink", "value": "咖啡", "canonical_hash": canonical_fact_hash("preference.drink", "咖啡"),
        "status": "active", "updated_at": "2026-10-07T00:00:00Z",
    }]
    with pytest.raises(MemoryDataUnavailable):
        retrieve_facts("咖啡", wrong_subject)


def test_manual_memory_replay_conflict_edit_and_validation(client_factory):
    _, client = client_factory()
    try:
        first = client.post("/api/v1/memory", json={"key": "preference.drink", "value": "乌龙茶"})
        replay = client.post("/api/v1/memory", json={"key": "preference.drink", "value": "乌龙 茶！"})
        replacement = client.post("/api/v1/memory", json={"key": "preference.drink", "value": "红茶"})
        assert first.status_code == 201
        assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
        assert replacement.status_code == 201 and replacement.json()["id"] != first.json()["id"]

        edited = client.post(
            f"/api/v1/memory/{replacement.json()['id']}/edit",
            json={"value": "普洱茶"},
        )
        assert edited.status_code == 200 and edited.json()["value"] == "普洱茶"
        listing = client.get("/api/v1/memory?status=all&q=普洱")
        assert listing.json()["total"] == 1 and listing.json()["items"][0]["id"] == edited.json()["id"]
        all_rows = client.get("/api/v1/memory?status=all").json()["items"]
        assert [(row["status"], row["value"]) for row in all_rows if row["key"] == "preference.drink"] == [
            ("active", "普洱茶"), ("superseded", "红茶"), ("superseded", "乌龙茶")
        ]
        assert client.post("/api/v1/memory", json={"key": "profile.address", "value": "某路1号"}).status_code == 422
        assert client.post("/api/v1/memory", json={"key": "profile.birthday", "value": "02-30"}).status_code == 422
        assert client.post("/api/v1/memory", json={"key": "preference.chat_style", "value": "手机号 13800138000"}).status_code == 422
        zero_width_phone = "138\u200b001\u200b38000"
        assert canonical_fact_hash("plan.personal", zero_width_phone) == canonical_fact_hash("plan.personal", "13800138000")
        assert client.post("/api/v1/memory", json={"key": "plan.personal", "value": zero_width_phone}).status_code == 422
        for formatted_phone in ("138.001.38000", "138/001/38000", "138，001，38000"):
            assert client.post("/api/v1/memory", json={"key": "plan.personal", "value": formatted_phone}).status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_fact_proposal_confirmation_rejection_conflict_and_duplicate_active_value(client_factory):
    candidates = [
        {"key": "preference.drink", "value": "乌龙茶", "evidence_quote": "我喜欢乌龙茶"},
    ]
    fake = FakeClient({"analysis": [fact_analysis(candidates), fact_analysis(candidates)]})
    app, client = client_factory(fake)
    try:
        session_id = create_session(client)
        first_turn = chat(client, session_id, "我喜欢乌龙茶")
        assert first_turn.status_code == 200
        proposal = client.get("/api/v1/proposals?kind=user_fact").json()["items"][0]
        assert proposal["status"] == "pending"
        assert proposal["source"]["role"] == "user"
        accepted = client.post(f"/api/v1/proposals/{proposal['id']}/resolve", json={"decision": "accept"})
        repeated = client.post(f"/api/v1/proposals/{proposal['id']}/resolve", json={"decision": "accept"})
        opposite = client.post(f"/api/v1/proposals/{proposal['id']}/resolve", json={"decision": "reject"})
        assert accepted.status_code == repeated.status_code == 200
        assert accepted.json()["result_id"] == repeated.json()["result_id"]
        assert opposite.status_code == 409

        second_turn = chat(client, session_id, "我喜欢乌龙茶")
        assert second_turn.status_code == 200
        assert client.get("/api/v1/proposals?kind=user_fact").json()["total"] == 0
        assert client.get("/api/v1/memory").json()["total"] == 1
    finally:
        client.__exit__(None, None, None)


def test_one_hundred_mixed_candidate_sources_stay_pending_until_confirmation(client_factory):
    prefixes = ("我喜欢", "假设我喜欢", "角色设定为喜欢", "引用你刚才说我喜欢")
    turns = []
    analyses = []
    for index in range(100):
        value = f"第{index:03d}项兴趣"
        text = f"{prefixes[index % len(prefixes)]}{value}。"
        turns.append((text, value))
        analyses.append(fact_analysis([{"key": "preference.hobby", "value": value, "evidence_quote": text}]))
    app, client = client_factory(FakeClient({"analysis": analyses}))
    try:
        session_id = create_session(client)
        for text, _value in turns:
            assert chat(client, session_id, text).status_code == 200

        proposals = client.get("/api/v1/proposals?kind=user_fact&limit=100").json()["items"]
        assert len(proposals) == 100
        assert all(proposal["status"] == "pending" for proposal in proposals)
        assert all(proposal["source"]["role"] == "user" for proposal in proposals)
        assert client.get("/api/v1/memory?status=active").json()["total"] == 0
        with app.state.repository.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM messages WHERE role = 'assistant'").fetchone()[0] == 100
            assert connection.execute("SELECT COUNT(*) FROM messages WHERE role = 'user'").fetchone()[0] == 100
            assert connection.execute("SELECT COUNT(*) FROM facts WHERE status = 'active'").fetchone()[0] == 0
    finally:
        client.__exit__(None, None, None)


def test_duplicate_pending_candidates_merge_and_conflict_acceptance_supersedes(client_factory):
    oolong = {"key": "preference.drink", "value": "乌龙茶", "evidence_quote": "我喜欢乌龙茶"}
    black = {"key": "preference.drink", "value": "红茶", "evidence_quote": "我改喜欢红茶"}
    fake = FakeClient({"analysis": [fact_analysis([oolong]), fact_analysis([oolong]), fact_analysis([black])]})
    app, client = client_factory(fake)
    try:
        session_id = create_session(client)
        assert chat(client, session_id, "我喜欢乌龙茶").status_code == 200
        assert chat(client, session_id, "我喜欢乌龙茶").status_code == 200
        proposals = client.get("/api/v1/proposals?kind=user_fact").json()["items"]
        assert len(proposals) == 1
        first_fact = client.post(f"/api/v1/proposals/{proposals[0]['id']}/resolve", json={"decision": "accept"}).json()["result_id"]

        assert chat(client, session_id, "我改喜欢红茶").status_code == 200
        next_proposal = client.get("/api/v1/proposals?kind=user_fact").json()["items"][0]
        replacement = client.post(f"/api/v1/proposals/{next_proposal['id']}/resolve", json={"decision": "accept"})
        assert replacement.status_code == 200
        assert replacement.json()["result_id"] != first_fact
        with app.state.repository.connection() as connection:
            rows = connection.execute("SELECT id, value, status, supersedes_id FROM facts WHERE \"key\" = 'preference.drink'").fetchall()
        by_id = {row["id"]: row for row in rows}
        assert by_id[first_fact]["status"] == "superseded"
        assert by_id[replacement.json()["result_id"]]["status"] == "active"
        assert by_id[replacement.json()["result_id"]]["supersedes_id"] == first_fact
    finally:
        client.__exit__(None, None, None)


def test_wrong_and_redacted_proposal_sources_cannot_be_confirmed(client_factory):
    candidate = {"key": "preference.drink", "value": "乌龙茶", "evidence_quote": "我喜欢乌龙茶"}
    fake = FakeClient({"analysis": [fact_analysis([candidate]), fact_analysis([candidate])]})
    app, client = client_factory(fake)
    try:
        session_id = create_session(client)
        original = chat(client, session_id, "我喜欢乌龙茶")
        proposal = client.get("/api/v1/proposals?kind=user_fact").json()["items"][0]
        with app.state.repository.transaction() as connection:
            connection.execute(
                "UPDATE messages SET content = '无关的来源文本' WHERE id = ?",
                (proposal["source"]["id"],),
            )
        invalid_source = client.post(f"/api/v1/proposals/{proposal['id']}/resolve", json={"decision": "accept"})
        assert invalid_source.status_code == 409
        assert invalid_source.json()["error"]["code"] == "SOURCE_INVALID"
        assert client.post(f"/api/v1/proposals/{proposal['id']}/resolve", json={"decision": "reject"}).status_code == 200

        second = chat(client, session_id, "我喜欢乌龙茶", str(uuid4()))
        proposals = client.get("/api/v1/proposals?kind=user_fact").json()["items"]
        fresh = next(item for item in proposals if item["source"]["id"] == second.json()["user_message"]["id"])
        with app.state.repository.transaction() as connection:
            connection.execute(
                "UPDATE messages SET content = NULL, redacted = 1 WHERE id = ?",
                (fresh["source"]["id"],),
            )
        redacted_source = client.post(f"/api/v1/proposals/{fresh['id']}/resolve", json={"decision": "accept"})
        assert redacted_source.status_code == 409
        assert redacted_source.json()["error"]["code"] == "SOURCE_REDACTED"
        assert original.status_code == 200
    finally:
        client.__exit__(None, None, None)


def test_forgetting_closes_source_dependencies_redacts_session_replays_and_restart(client_factory, tmp_path: Path):
    candidates = [
        {"key": "preference.drink", "value": "乌龙茶", "evidence_quote": "我喜欢乌龙茶"},
        {"key": "preference.food", "value": "米糕", "evidence_quote": "我也喜欢米糕"},
    ]
    third = [{"key": "plan.personal", "value": "周末逛书店", "evidence_quote": "我计划周末逛书店"}]
    neutral = json.dumps({"state_delta": {"valence": 0, "energy": 0, "closeness": 0, "guard_trigger": False}, "fact_candidates": [], "persona_proposals": []})
    fake = FakeClient({"analysis": [fact_analysis(candidates), neutral, fact_analysis(third), neutral]})
    data_dir = tmp_path / "persistent-data"
    app, client = client_factory(fake, data_dir=data_dir)
    session_id = create_session(client, "来源会话")
    first_op, context_op, pending_op = str(uuid4()), str(uuid4()), str(uuid4())
    try:
        first = chat(client, session_id, "我喜欢乌龙茶，我也喜欢米糕", first_op)
        assert first.status_code == 200
        proposals = client.get("/api/v1/proposals?kind=user_fact").json()["items"]
        assert len(proposals) == 2
        accepted_ids = []
        for proposal in proposals:
            result = client.post(f"/api/v1/proposals/{proposal['id']}/resolve", json={"decision": "accept"})
            assert result.status_code == 200
            accepted_ids.append(result.json()["result_id"])

        context_turn = chat(client, session_id, "聊聊乌龙茶和米糕", context_op)
        assert context_turn.status_code == 200
        pending_turn = chat(client, session_id, "我计划周末逛书店", pending_op)
        assert pending_turn.status_code == 200
        pending_proposal = client.get("/api/v1/proposals?kind=user_fact").json()["items"][0]

        refs = app.state.repository.connection
        with refs() as connection:
            injected = {
                row["ref_id"]
                for row in connection.execute(
                    "SELECT ref_id FROM prompt_refs WHERE assistant_message_id = ? AND ref_type = 'fact'",
                    (context_turn.json()["assistant_message"]["id"],),
                )
            }
            assert injected == set(accepted_ids)

        forgotten = client.post(f"/api/v1/memory/{accepted_ids[0]}/forget", json={"redact_session_history": True})
        assert forgotten.status_code == 200
        body = forgotten.json()
        assert set(body["forgotten_fact_ids"]) == set(accepted_ids)
        assert body["redacted_message_count"] == 6
        assert body["memory_epoch"] == 1
        assert "旧备份" in body["notice"]
        assert client.get("/api/v1/memory?status=all").json()["total"] == 2
        assert all(item["value"] is None and item["status"] == "forgotten" for item in client.get("/api/v1/memory?status=all").json()["items"])
        assert client.get("/api/v1/proposals?kind=user_fact&status=invalidated").json()["total"] == 1
        blocked = client.post(f"/api/v1/proposals/{pending_proposal['id']}/resolve", json={"decision": "accept"})
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "SOURCE_REDACTED"

        replay = chat(client, session_id, "我喜欢乌龙茶，我也喜欢米糕", first_op)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert replay.json()["user_message"]["content"] is None and replay.json()["user_message"]["redacted"] is True
        assert replay.json()["assistant_message"]["content"] is None and replay.json()["assistant_message"]["redacted"] is True
        repeated_forget = client.post(f"/api/v1/memory/{accepted_ids[0]}/forget", json={})
        assert repeated_forget.json()["forgotten_fact_ids"] == []
        assert repeated_forget.json()["redacted_message_count"] == 0
        assert repeated_forget.json()["memory_epoch"] == 1
        with app.state.repository.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM operations WHERE request_text IS NOT NULL").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM messages WHERE redacted = 0 AND session_id = ?", (session_id,)).fetchone()[0] == 0
    finally:
        client.__exit__(None, None, None)

    restarted, client2 = client_factory(FakeClient(), data_dir=data_dir)
    try:
        messages = client2.get(f"/api/v1/sessions/{session_id}/messages").json()["items"]
        assert len(messages) == 6 and all(item["redacted"] and item["content"] is None for item in messages)
        assert client2.post(f"/api/v1/proposals/{pending_proposal['id']}/resolve", json={"decision": "accept"}).status_code == 409
        operation_status = client2.get(f"/api/v1/operations/{first_op}").json()
        assert operation_status["assistant_message"]["content"] is None
        assert restarted.state.repository.get_meta("memory_epoch") == "1"
    finally:
        client2.__exit__(None, None, None)


def test_retrieval_uses_bigram_dice_score_fixed_order_and_preferred_name_fallback():
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    values = [
        ("preference.drink", "手冲咖啡"),
        ("preference.food", "火锅"),
        ("preference.hobby", "胶片摄影"),
        ("preference.chat_style", "简短直接"),
        ("profile.preferred_name", "小满"),
    ]
    facts = [
        {
            "id": f"00000000-0000-4000-8000-{index + 1:012d}", "key": key, "value": value,
            "canonical_hash": canonical_fact_hash(key, value),
            "status": "active", "updated_at": f"2026-10-0{index + 1}T00:00:00Z",
        }
        for index, (key, value) in enumerate(values)
    ]
    query = retrieve_facts("冲泡咖啡", facts, top_k=5, now=now)
    assert query[0]["key"] == "preference.drink"
    assert query[0]["score"] == pytest.approx(2 / 6)
    with_fallback = retrieve_facts("火锅", facts, top_k=5, now=now)
    assert with_fallback[0]["key"] == "preference.food"
    assert any(item["key"] == "profile.preferred_name" for item in with_fallback)
    only_name = retrieve_facts("完全无关", facts, top_k=1, now=now)
    assert len(only_name) == 1 and only_name[0]["key"] == "profile.preferred_name"

    query_cases = [
        ("手冲咖啡", "preference.drink"), ("咖啡", "preference.drink"),
        ("冲泡咖啡", "preference.drink"), ("火锅", "preference.food"),
        ("喜欢火锅", "preference.food"), ("胶片摄影", "preference.hobby"),
        ("摄影", "preference.hobby"), ("简短直接", "preference.chat_style"),
        ("小满", "profile.preferred_name"), ("完全无关", None),
    ]
    outcomes = [
        bool((results := retrieve_facts(query_text, facts, top_k=5, now=now)) and results[0]["key"] == expected)
        for query_text, expected in query_cases
    ]
    # Freeze each positive case, not just the aggregate; the unrelated query
    # intentionally returns the name fallback and is the historical tenth miss.
    assert outcomes == [True] * 9 + [False]
    assert sum(outcomes) == 9


def test_corrupt_active_memory_warns_and_does_not_block_the_main_chat(client_factory):
    app, client = client_factory()
    try:
        session_id = create_session(client)
        manual = client.post("/api/v1/memory", json={"key": "preference.drink", "value": "乌龙茶"})
        assert manual.status_code == 201
        with app.state.repository.transaction() as connection:
            connection.execute("UPDATE facts SET canonical_hash = ? WHERE id = ?", ("f" * 64, manual.json()["id"]))

        response = chat(client, session_id, "聊聊乌龙茶")
        assert response.status_code == 200
        assert "MEMORY_UNAVAILABLE" in response.json()["warnings"]
        with app.state.repository.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
            assert connection.execute(
                "SELECT COUNT(*) FROM prompt_refs WHERE ref_type = 'fact' AND assistant_message_id = ?",
                (response.json()["assistant_message"]["id"],),
            ).fetchone()[0] == 0
    finally:
        client.__exit__(None, None, None)


def test_identity_coverage_persists_only_injected_fact_refs_and_refreshes(client_factory):
    app, client = client_factory(FakeClient())
    try:
        session_id = create_session(client)
        facts = {}
        for key, value in (
            ("profile.preferred_name", "小满"), ("profile.birthday", "02-29"),
            ("preference.drink", "咖啡"), ("preference.food", "咖啡"),
            ("preference.hobby", "咖啡"), ("preference.chat_style", "咖啡"),
            ("plan.personal", "咖啡"),
        ):
            response = client.post("/api/v1/memory", json={"key": key, "value": value})
            assert response.status_code == 201
            facts[key] = response.json()["id"]
        response = chat(client, session_id, "咖啡")
        assert response.status_code == 200
        with app.state.repository.connection() as connection:
            referenced = {row[0] for row in connection.execute(
                "SELECT ref_id FROM prompt_refs WHERE ref_type = 'fact' AND assistant_message_id = ?",
                (response.json()["assistant_message"]["id"],),
            )}
            refreshed = {row[0] for row in connection.execute(
                "SELECT id FROM facts WHERE last_prompt_at IS NOT NULL",
            )}
        assert len(referenced) == 5
        assert {facts["profile.preferred_name"], facts["profile.birthday"]} <= referenced
        assert referenced == refreshed
    finally:
        client.__exit__(None, None, None)


def test_memory_candidate_storage_sql_failure_does_not_fail_chat(client_factory, monkeypatch):
    candidate = [{"key": "preference.drink", "value": "乌龙茶", "evidence_quote": "我喜欢乌龙茶"}]
    app, client = client_factory(FakeClient({"analysis": [fact_analysis(candidate)]}))
    try:
        session_id = create_session(client)

        def fail_candidate_storage(*_args, **_kwargs):
            raise sqlite3.OperationalError("no such table: facts")

        monkeypatch.setattr(Repository, "_persist_turn_proposals", staticmethod(fail_candidate_storage))
        response = chat(client, session_id, "我喜欢乌龙茶")
        assert response.status_code == 200
        assert "MEMORY_UNAVAILABLE" in response.json()["warnings"]
        assert client.get("/api/v1/proposals?kind=user_fact").json()["total"] == 0
        with app.state.repository.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    finally:
        client.__exit__(None, None, None)
