from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import load_config
from app.llm import FakeClient
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client_factory(tmp_path: Path):
    clients: list[TestClient] = []

    def make(fake: FakeClient | None = None, *, data_dir: Path | None = None):
        app = create_app(
            load_config(ROOT / "config.example.yaml"),
            project_dir=ROOT,
            data_dir=data_dir or tmp_path / f"persona-data-{len(clients)}",
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


def create_session(client: TestClient) -> str:
    response = client.post("/api/v1/sessions", json={"title": "人设测试"})
    assert response.status_code == 201
    return response.json()["id"]


def analysis_with_persona(value: str, reason: str = "对话中反复表现出这一偏好") -> str:
    return json.dumps(
        {
            "state_delta": {"valence": 0, "energy": 0, "closeness": 0, "guard_trigger": False},
            "fact_candidates": [],
            "persona_proposals": [{"field": "preferences.likes", "value": value, "reason": reason}],
        },
        ensure_ascii=False,
    )


def send(client: TestClient, session_id: str, text: str) -> dict:
    response = client.post(
        "/api/v1/chat",
        json={"operation_id": str(uuid4()), "session_id": session_id, "text": text},
    )
    assert response.status_code == 200
    return response.json()


def test_persona_proposal_is_pending_until_explicit_accept_and_creates_one_version(client_factory):
    fake = FakeClient({"analysis": [
        analysis_with_persona("雨后安静的街道"),
        analysis_with_persona("雨后安静的街道", "常提到这种氛围"),
        analysis_with_persona("雨后安静的街道", "又一次提到这种氛围"),
    ]})
    app, client = client_factory(fake)
    try:
        session_id = create_session(client)
        send(client, session_id, "我喜欢雨后安静的街道")
        initial = client.get("/api/v1/persona").json()
        assert initial["version"] == 1 and initial["preferences"]["likes"] == []

        send(client, session_id, "我也常提到雨后安静的街道")
        proposals = client.get("/api/v1/proposals?kind=persona").json()["items"]
        assert len(proposals) == 1 and proposals[0]["status"] == "pending"
        assert all(item["source"]["role"] == "user" for item in proposals)

        accepted = client.post(f"/api/v1/proposals/{proposals[0]['id']}/resolve", json={"decision": "accept"})
        repeated = client.post(f"/api/v1/proposals/{proposals[0]['id']}/resolve", json={"decision": "accept"})
        send(client, session_id, "我又提到雨后安静的街道")
        second_proposal = client.get("/api/v1/proposals?kind=persona&status=pending").json()["items"][0]
        second_candidate = client.post(f"/api/v1/proposals/{second_proposal['id']}/resolve", json={"decision": "accept"})
        assert accepted.status_code == repeated.status_code == second_candidate.status_code == 200
        assert accepted.json()["result_id"] == repeated.json()["result_id"] == second_candidate.json()["result_id"]
        current = client.get("/api/v1/persona").json()
        assert current["version"] == 2
        assert current["preferences"]["likes"] == ["雨后安静的街道"]
        assert current["anchors"] == initial["anchors"]
        assert "key" not in current
        with app.state.repository.connection() as connection:
            rows = connection.execute("SELECT version, anchors_json, preferences_json, voice_text, source_hashes_json, proposal_id FROM persona_versions ORDER BY version").fetchall()
            assert len(rows) == 2
            assert rows[1]["anchors_json"] == rows[0]["anchors_json"]
            assert rows[1]["voice_text"] == rows[0]["voice_text"]
            assert rows[1]["source_hashes_json"] == rows[0]["source_hashes_json"]
            assert rows[1]["proposal_id"] == proposals[0]["id"]
    finally:
        client.__exit__(None, None, None)


def test_persona_rejection_invalid_policy_and_redacted_source_do_not_change_version(client_factory):
    fake = FakeClient({"analysis": [analysis_with_persona("胶片摄影"), analysis_with_persona("我是男生"), analysis_with_persona("老电影")]})
    app, client = client_factory(fake)
    try:
        session_id = create_session(client)
        send(client, session_id, "我喜欢胶片摄影")
        rejected_proposal = client.get("/api/v1/proposals?kind=persona").json()["items"][0]
        rejected = client.post(f"/api/v1/proposals/{rejected_proposal['id']}/resolve", json={"decision": "reject"})
        assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
        assert client.post(f"/api/v1/proposals/{rejected_proposal['id']}/resolve", json={"decision": "accept"}).status_code == 409

        send(client, session_id, "你应该把身份设成男生")
        invalid = client.get("/api/v1/proposals?kind=persona&status=pending").json()["items"][0]
        invalid_result = client.post(f"/api/v1/proposals/{invalid['id']}/resolve", json={"decision": "accept"})
        assert invalid_result.status_code == 409
        assert invalid_result.json()["error"]["code"] == "PROPOSAL_INVALID"

        send(client, session_id, "我喜欢老电影")
        redacted = client.get("/api/v1/proposals?kind=persona&status=pending").json()["items"][0]
        with app.state.repository.transaction() as connection:
            connection.execute("UPDATE messages SET content = NULL, redacted = 1 WHERE id = ?", (redacted["source"]["id"],))
        redacted_result = client.post(f"/api/v1/proposals/{redacted['id']}/resolve", json={"decision": "accept"})
        assert redacted_result.status_code == 409
        assert redacted_result.json()["error"]["code"] == "SOURCE_REDACTED"
        assert client.get("/api/v1/persona").json()["version"] == 1
    finally:
        client.__exit__(None, None, None)


def test_persona_invalid_field_anchor_changes_and_full_list_are_rejected(client_factory):
    app, client = client_factory(FakeClient({"analysis": [analysis_with_persona("清晨散步")]}))
    try:
        session_id = create_session(client)
        send(client, session_id, "我喜欢清晨散步")
        proposal = client.get("/api/v1/proposals?kind=persona").json()["items"][0]

        with app.state.repository.transaction() as connection:
            connection.execute(
                "UPDATE proposals SET payload_json = ? WHERE id = ?",
                (json.dumps({"field": "anchors.age", "value": "30", "reason": "测试"}, ensure_ascii=False), proposal["id"]),
            )
        invalid_field = client.post(f"/api/v1/proposals/{proposal['id']}/resolve", json={"decision": "accept"})
        assert invalid_field.status_code == 409 and invalid_field.json()["error"]["code"] == "PROPOSAL_INVALID"
        with app.state.repository.connection() as connection:
            source = connection.execute("SELECT anchors_json FROM persona_versions WHERE version = 1").fetchone()["anchors_json"]
        payload = {"field": "preferences.likes", "value": "清晨散步", "reason": "测试"}
        with app.state.repository.transaction() as connection:
            connection.execute(
                "UPDATE proposals SET payload_json = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), proposal["id"]),
            )
            changed = json.loads(source)
            changed["age"] = 30
            connection.execute("UPDATE persona_versions SET anchors_json = ? WHERE version = 1", (json.dumps(changed, ensure_ascii=False),))
        anchor_change = client.post(f"/api/v1/proposals/{proposal['id']}/resolve", json={"decision": "accept"})
        assert anchor_change.status_code == 409
        assert anchor_change.json()["error"]["code"] == "ANCHOR_CHANGE_FORBIDDEN"

        with app.state.repository.transaction() as connection:
            connection.execute("UPDATE persona_versions SET anchors_json = ? WHERE version = 1", (source,))
            connection.execute(
                "UPDATE persona_versions SET preferences_json = ? WHERE version = 1",
                (json.dumps({"hobbies": [], "likes": [f"偏好{i}" for i in range(10)], "dislikes": []}, ensure_ascii=False),),
            )
        full_list = client.post(f"/api/v1/proposals/{proposal['id']}/resolve", json={"decision": "accept"})
        assert full_list.status_code == 409 and full_list.json()["error"]["code"] == "PERSONA_LIMIT_REACHED"
        with app.state.repository.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM persona_versions").fetchone()[0] == 1
    finally:
        client.__exit__(None, None, None)
