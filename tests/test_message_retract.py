from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.clock import FakeClock
from app.config import load_config
from app.llm import FakeClient
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
NOON_SHANGHAI = datetime(2026, 10, 8, 10, 30, tzinfo=timezone(timedelta(hours=8)))


def _analysis() -> str:
    return json.dumps({
        "state_delta": {"valence": 0, "energy": 0, "closeness": 0, "guard_trigger": False},
        "fact_candidates": [], "persona_proposals": [],
    }, ensure_ascii=False)


def _make(tmp_path: Path, name: str, clock: FakeClock) -> tuple[object, TestClient]:
    app = create_app(
        load_config(ROOT / "config.example.yaml"),
        project_dir=ROOT,
        data_dir=tmp_path / name,
        fake_client=FakeClient({"analysis": _analysis(), "chat": "嗯，我在。"}, clock=clock),
    )
    app.state.repository.clock = clock
    app.state.llm_client.clock = clock
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    client.__enter__()
    client.headers.update({"X-Suwan-Token": app.state.session_token})
    return app, client


def _send_message(client: TestClient, session_id: str, text: str) -> dict:
    response = client.post("/api/v1/chat", json={
        "operation_id": str(uuid4()), "session_id": session_id, "text": text,
    })
    assert response.status_code == 200
    return response.json()


def test_retract_user_message_hides_content_from_history(tmp_path: Path):
    clock = FakeClock(NOON_SHANGHAI)
    app, client = _make(tmp_path, "retract-ok", clock)
    session_id = client.post("/api/v1/sessions", json={"title": "撤回"}).json()["id"]
    body = _send_message(client, session_id, "这句话马上要撤回")
    user_message = body["user_message"]

    result = client.post(f"/api/v1/messages/{user_message['id']}/retract", json={})
    assert result.status_code == 200
    assert result.json()["retracted"] is True

    messages = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"]
    retracted = next(item for item in messages if item["id"] == user_message["id"])
    assert retracted["redacted"] is True
    assert retracted["content"] is None

    # 历史轮次不再包含该消息：下一轮她看不到被撤回的内容
    _send_message(client, session_id, "你还看得到上一条吗")
    with app.state.repository.connection() as connection:
        rows = connection.execute(
            "SELECT content FROM messages WHERE session_id = ? AND role = 'user' AND redacted = 0",
            (session_id,),
        ).fetchall()
    assert all(row["content"] != "这句话马上要撤回" for row in rows)


def test_retract_rejects_after_window_and_other_roles(tmp_path: Path):
    clock = FakeClock(NOON_SHANGHAI)
    app, client = _make(tmp_path, "retract-guard", clock)
    session_id = client.post("/api/v1/sessions", json={"title": "撤回边界"}).json()["id"]
    body = _send_message(client, session_id, "一分钟后就过期")
    message_id = body["user_message"]["id"]

    # 助手消息不能撤
    assistant_id = body["assistant_message"]["id"]
    assert client.post(f"/api/v1/messages/{assistant_id}/retract", json={}).status_code == 403

    # 快进 11 分钟：窗口关闭
    clock.set(NOON_SHANGHAI + timedelta(minutes=11))
    response = client.post(f"/api/v1/messages/{message_id}/retract", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RETRACT_WINDOW_EXPIRED"

    # 窗口内正常撤回，再撤一次 → 已撤回
    clock.set(NOON_SHANGHAI + timedelta(minutes=1))
    assert client.post(f"/api/v1/messages/{message_id}/retract", json={}).status_code == 200
    assert client.post(f"/api/v1/messages/{message_id}/retract", json={}).status_code == 409
    assert client.post(f"/api/v1/messages/{message_id}/retract", json={}).json()["error"]["code"] == "MESSAGE_ALREADY_RETRACTED"

    # 不存在的消息
    assert client.post(f"/api/v1/messages/{uuid4()}/retract", json={}).status_code == 404

    # 非法 UUID
    assert client.post("/api/v1/messages/not-a-uuid/retract", json={}).status_code == 422
