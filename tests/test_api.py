from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import load_config
from app.llm import FakeClient
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def make_client(tmp_path: Path):
    app = create_app(
        load_config(ROOT / "config.example.yaml"),
        project_dir=ROOT,
        data_dir=tmp_path / "data",
        fake_client=FakeClient(),
    )
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    client.__enter__()
    client.headers.update({"X-Suwan-Token": app.state.session_token})
    return app, client


def test_local_token_host_origin_and_static_whitelist(tmp_path: Path):
    app, client = make_client(tmp_path)
    try:
        assert client.get("/api/v1/health").json() == {"alive": True, "mode": "fake", "schema_version": 1}
        missing = client.get("/api/v1/sessions", headers={"X-Suwan-Token": ""})
        assert missing.status_code == 401 and missing.json()["error"]["code"] == "TOKEN_REQUIRED"
        mismatch = client.get("/api/v1/sessions", headers={"X-Suwan-Token": app.state.session_token, "Origin": "http://localhost:8000"})
        assert mismatch.status_code == 403 and mismatch.json()["error"]["code"] == "INVALID_ORIGIN"
        root = client.get("/")
        assert root.status_code == 200
        assert f'"token":"{app.state.session_token}"' in root.text
        assert "\\u003c" in root.text or "suwan-bootstrap" in root.text
        assert "script-src 'self'" in root.headers["Content-Security-Policy"]
        assert client.get("/ui/app.js").status_code == 200
        assert client.get("/app/main.py").status_code == 404
    finally:
        client.__exit__(None, None, None)

    hostile = TestClient(app, base_url="http://attacker:8000")
    with hostile:
        response = hostile.get("/api/v1/health")
    assert response.status_code == 400 and response.json()["error"]["code"] == "INVALID_HOST"


def test_session_archive_restore_messages_and_cursor_validation(tmp_path: Path):
    _, client = make_client(tmp_path)
    try:
        create = client.post("/api/v1/sessions", json={"title": "  对话 A  "})
        assert create.status_code == 201 and create.json()["title"] == "对话 A"
        session_id = create.json()["id"]
        other_id = client.post("/api/v1/sessions", json={"title": "对话 B"}).json()["id"]
        for i in range(3):
            response = client.post("/api/v1/chat", json={"operation_id": str(uuid4()), "session_id": session_id, "text": f"第 {i} 条"})
            assert response.status_code == 200
        page = client.get(f"/api/v1/sessions/{session_id}/messages?limit=3").json()
        assert [item["seq"] for item in page["items"]] == [4, 5, 6]
        assert page["next_before_id"] == page["items"][0]["id"]
        older = client.get(f"/api/v1/sessions/{session_id}/messages?limit=3&before_id={page['next_before_id']}").json()
        assert [item["seq"] for item in older["items"]] == [1, 2, 3]
        wrong_session = client.get(f"/api/v1/sessions/{other_id}/messages?before_id={page['next_before_id']}")
        assert wrong_session.status_code == 400
        invalid_title = client.post("/api/v1/sessions", json={"title": "  "})
        assert invalid_title.status_code == 422 and "error" in invalid_title.json()
        archived = client.post(f"/api/v1/sessions/{session_id}/archive", json={"archived": True})
        assert archived.json() == {"id": session_id, "archived": True}
        blocked = client.post("/api/v1/chat", json={"operation_id": str(uuid4()), "session_id": session_id, "text": "不能发"})
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "SESSION_ARCHIVED"
        restored = client.post(f"/api/v1/sessions/{session_id}/archive", json={"archived": False})
        assert restored.json()["archived"] is False
    finally:
        client.__exit__(None, None, None)


def test_messages_and_frontend_render_untrusted_text_as_text(tmp_path: Path):
    _, client = make_client(tmp_path)
    try:
        session_id = client.post("/api/v1/sessions", json={"title": "安全"}).json()["id"]
        payload = {"operation_id": str(uuid4()), "session_id": session_id, "text": "<img src=x onerror=alert(1)>"}
        response = client.post("/api/v1/chat", json=payload)
        assert response.status_code == 200
        messages = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"]
        assert messages[0]["content"] == payload["text"]
        source = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
        assert "textContent" in source
        assert "innerHTML" not in source
        assert "event.isComposing" in source and "event.keyCode !== 229" in source
        assert "crypto.randomUUID()" in source
    finally:
        client.__exit__(None, None, None)


def test_frontend_pending_state_is_attached_to_message_flow():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    source = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    assert "pending-area" not in html
    assert "message-flow-pending" in source
    assert "clearMessageFlowPending" in source
    assert "outbound-status" in source
    assert "停止等待不会取消服务端处理" in source


def test_operation_status_rebuilds_messages_from_database(tmp_path: Path):
    _, client = make_client(tmp_path)
    try:
        session_id = client.post("/api/v1/sessions", json={"title": "状态查询"}).json()["id"]
        operation_id = str(uuid4())
        posted = client.post("/api/v1/chat", json={"operation_id": operation_id, "session_id": session_id, "text": "你好"})
        status = client.get(f"/api/v1/operations/{operation_id}")
        assert posted.status_code == status.status_code == 200
        assert status.json()["status"] == "succeeded"
        assert status.json()["message"] == posted.json()["assistant_message"]
        assert status.json()["user_message"] == posted.json()["user_message"]
    finally:
        client.__exit__(None, None, None)
