from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import load_config
from app.llm import FakeClient
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]

MATERIAL_META = {"pillar": "work_diary", "hook_type": "specific_number", "note": "金句"}


@pytest.fixture
def client_factory(tmp_path: Path):
    clients: list[TestClient] = []

    def make(fake: FakeClient | None = None):
        app = create_app(
            load_config(ROOT / "config.example.yaml"),
            project_dir=ROOT,
            data_dir=tmp_path / f"material-data-{len(clients)}",
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


def _chat_once(client: TestClient) -> dict:
    session = client.post("/api/v1/sessions", json={"title": "素材测试"}).json()
    operation_id = str(uuid4())
    response = client.post(
        "/api/v1/chat",
        json={"operation_id": operation_id, "session_id": session["id"], "text": "需求改了第八版"},
    )
    assert response.status_code == 200
    return response.json()


def test_material_save_list_update_and_duplicate_guard(client_factory):
    app, client = client_factory()
    turn = _chat_once(client)
    assistant_id = turn["assistant_message"]["id"]

    saved = client.post("/api/v1/materials", json={"message_id": assistant_id, **MATERIAL_META})
    assert saved.status_code == 201
    material = saved.json()
    assert material["role"] == "assistant"
    assert material["snapshot_text"] == turn["assistant_message"]["content"]
    assert material["status"] == "saved"

    duplicate = client.post("/api/v1/materials", json={"message_id": assistant_id, **MATERIAL_META})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "MATERIAL_DUPLICATE"

    items = client.get("/api/v1/materials").json()["items"]
    assert [item["id"] for item in items] == [material["id"]]

    by_pillar = client.get("/api/v1/materials", params={"pillar": "cat_ai_girl"}).json()["items"]
    assert by_pillar == []

    updated = client.post(
        f"/api/v1/materials/{material['id']}/update",
        json={"pillar": "night_emotion", "status": "discarded"},
    )
    assert updated.status_code == 200
    assert updated.json()["pillar"] == "night_emotion"
    assert updated.json()["status"] == "discarded"

    saved_items = client.get("/api/v1/materials").json()["items"]
    assert saved_items == []
    discarded = client.get("/api/v1/materials", params={"status": "discarded"}).json()["items"]
    assert len(discarded) == 1 and discarded[0]["id"] == material["id"]


def test_material_rejects_unknown_message_redacted_and_bad_pillar(client_factory):
    app, client = client_factory()
    missing = client.post("/api/v1/materials", json={"message_id": str(uuid4()), **MATERIAL_META})
    assert missing.status_code == 404

    turn = _chat_once(client)
    bad_pillar = client.post(
        "/api/v1/materials",
        json={"message_id": turn["assistant_message"]["id"], "pillar": "not_a_pillar"},
    )
    assert bad_pillar.status_code == 422

    with app.state.repository.transaction() as connection:
        connection.execute(
            "UPDATE messages SET content = NULL, redacted = 1 WHERE id = ?",
            (turn["user_message"]["id"],),
        )
    redacted = client.post(
        "/api/v1/materials",
        json={"message_id": turn["user_message"]["id"], **MATERIAL_META},
    )
    assert redacted.status_code == 409
    assert redacted.json()["error"]["code"] == "SOURCE_REDACTED"


def test_forgetting_redacts_message_and_invalidates_its_material(client_factory):
    app, client = client_factory()
    turn = _chat_once(client)
    user_id = turn["user_message"]["id"]

    saved = client.post("/api/v1/materials", json={"message_id": user_id, **MATERIAL_META})
    assert saved.status_code == 201
    material_id = saved.json()["id"]

    # A manual fact backed by that user message, so forget redacts the message.
    manual = client.post("/api/v1/memory", json={"key": "plan.personal", "value": "换工作"})
    assert manual.status_code == 201
    fact_id = manual.json()["id"]
    with app.state.repository.connection() as connection:
        connection.execute(
            "INSERT INTO fact_sources(fact_id, message_id) VALUES (?, ?)",
            (fact_id, user_id),
        )

    forgotten = client.post(f"/api/v1/memory/{fact_id}/forget", json={"redact_session_history": False})
    assert forgotten.status_code == 200

    invalidated = client.get("/api/v1/materials", params={"status": "invalidated"}).json()["items"]
    assert [item["id"] for item in invalidated] == [material_id]
    edit = client.post(
        f"/api/v1/materials/{material_id}/update",
        json={"note": "试着改一下"},
    )
    assert edit.status_code == 409
    assert edit.json()["error"]["code"] == "MATERIAL_UNAVAILABLE"


def test_material_endpoints_require_token(client_factory):
    _, client = client_factory()
    open_client = TestClient(client.app, base_url="http://127.0.0.1:8000")
    response = open_client.get("/api/v1/materials")
    assert response.status_code == 401
