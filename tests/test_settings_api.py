from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import load_config
from app.llm import FakeClient
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client_factory(tmp_path: Path):
    clients: list[TestClient] = []

    def make(project_dir: Path | None = None):
        app = create_app(
            load_config(ROOT / "config.example.yaml"),
            project_dir=project_dir or ROOT,
            data_dir=tmp_path / f"settings-data-{len(clients)}",
            fake_client=FakeClient(),
        )
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        client.__enter__()
        clients.append(client)
        client.headers.update({"X-Suwan-Token": app.state.session_token})
        return app, client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def writable_project(tmp_path: Path) -> Path:
    """A project dir with config.yaml + .env copied from the examples."""
    source_yaml = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "config.yaml").write_text(
        yaml.safe_dump(source_yaml, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (project / ".env").write_text("SUWAN_API_KEY=\n", encoding="utf-8")
    return project


def _secret_in_files(project: Path, secret: str) -> bool:
    config_text = (project / "config.yaml").read_text(encoding="utf-8")
    env_text = (project / ".env").read_text(encoding="utf-8")
    return secret in config_text or secret in env_text


def test_settings_api_read_is_redacted_and_lists_templates(client_factory):
    # 注意：本机项目目录现在可能有真实 config.yaml（用户跑过 setup/导入），
    # 所以只断言"模板无条件返回"与"无密钥泄漏"，不断言 config_file_exists 的具体值。
    _, client = client_factory()
    data = client.get("/api/v1/settings/api").json()
    assert "sk-" not in json.dumps(data)
    assert data["config_file_exists"] in {True, False}
    templates = data["templates"]
    assert set(templates) >= {"newapi", "aliyun", "deepseek", "custom"}
    assert templates["aliyun"]["model"] == "qwen-flash-character"
    assert templates["aliyun"]["base_url"].endswith("/compatible-mode/v1")


def test_update_chat_api_writes_local_files_and_response_has_no_secret(client_factory, writable_project):
    app, client = client_factory(project_dir=writable_project)
    secret = "sk-test-secret-value-123456"
    response = client.post(
        "/api/v1/settings/api",
        json={
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": secret,
            "api_key_env": "SUWAN_API_KEY",
        },
    )
    assert response.status_code == 200
    body = json.dumps(response.json())
    assert secret not in body
    assert secret not in client.get("/api/v1/settings/api").text

    # 本机文件确实写入了（这就是"不上 GitHub"的前提：两个文件都被 .gitignore）
    assert _secret_in_files(writable_project, secret)
    config_raw = yaml.safe_load((writable_project / "config.yaml").read_text(encoding="utf-8"))
    for name in ("chat", "analysis", "world"):
        assert config_raw["profiles"][name]["base_url"] == "https://api.deepseek.com/v1"
        assert config_raw["profiles"][name]["model"] == "deepseek-chat"
    env_text = (writable_project / ".env").read_text(encoding="utf-8")
    assert f"SUWAN_API_KEY={secret}" in env_text
    # 读取视图只说"已配置"，不回显
    view = client.get("/api/v1/settings/api").json()
    assert view["chat"]["api_key_set"] is True
    assert secret not in json.dumps(view)


def test_update_chat_api_keeps_key_when_only_url_changed(client_factory, writable_project):
    _, client = client_factory(project_dir=writable_project)
    secret = "sk-keep-me-9999"
    client.post("/api/v1/settings/api", json={
        "base_url": "https://api.newapi.pro/v1", "model": "gpt-4o-mini", "api_key": secret,
    })
    # 第二次只改 URL，不带密钥 → 密钥保留
    client.post("/api/v1/settings/api", json={
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-flash-character",
    })
    assert _secret_in_files(writable_project, secret)
    config_raw = yaml.safe_load((writable_project / "config.yaml").read_text(encoding="utf-8"))
    assert config_raw["profiles"]["chat"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_update_chat_api_rejects_http_non_localhost(client_factory, writable_project):
    _, client = client_factory(project_dir=writable_project)
    response = client.post("/api/v1/settings/api", json={
        "base_url": "http://example.com/v1", "model": "x",
    })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SETTINGS_INVALID"


def test_weather_settings_roundtrip_and_weather_endpoint_degrades(client_factory, writable_project):
    _, client = client_factory(project_dir=writable_project)
    saved = client.post("/api/v1/settings/weather", json={
        "provider": "open_meteo", "city": "广州", "latitude": 23.13, "longitude": 113.26,
    })
    assert saved.status_code == 200
    assert saved.json()["weather"]["city"] == "广州"
    config_raw = yaml.safe_load((writable_project / "config.yaml").read_text(encoding="utf-8"))
    assert config_raw["weather"]["provider"] == "open_meteo"

    view = client.get("/api/v1/settings/api").json()
    assert view["weather"]["provider"] == "open_meteo"

    # 天气端点读的是启动时加载的内存配置——刚保存的 weather 要重启才生效，
    # 所以运行中查询仍报 not_configured（这正是"重启后生效"语义的验证）。
    weather = client.get("/api/v1/weather").json()
    assert weather == {"available": False, "reason": "not_configured"}

    # 但写盘确实发生了：新起一个 app（模拟重启）就能看到新配置。
    restarted = create_app(
        load_config(writable_project / "config.yaml"),
        project_dir=writable_project,
        data_dir=writable_project / "data-restart",
        fake_client=FakeClient(),
    )
    restart_client = TestClient(restarted, base_url="http://127.0.0.1:8000")
    restart_client.__enter__()
    restart_client.headers.update({"X-Suwan-Token": restarted.state.session_token})
    try:
        restarted_view = restart_client.get("/api/v1/settings/api").json()
        assert restarted_view["weather"]["provider"] == "open_meteo"
        assert restarted_view["weather"]["city"] == "广州"
    finally:
        restart_client.__exit__(None, None, None)


def test_weather_endpoint_without_config_reports_not_configured(client_factory, writable_project):
    # config.yaml 存在但没有 weather 段 → not_configured（而不是 5xx）
    _, client = client_factory(project_dir=writable_project)
    weather = client.get("/api/v1/weather").json()
    assert weather == {"available": False, "reason": "not_configured"}


def test_settings_endpoints_require_token(client_factory):
    _, client = client_factory()
    open_client = TestClient(client.app, base_url="http://127.0.0.1:8000")
    assert open_client.get("/api/v1/settings/api").status_code == 401
    assert open_client.post("/api/v1/settings/api", json={}).status_code == 401


def test_open_meteo_payload_mapping():
    from app.weather import describe_weather

    view = describe_weather({"current": {"temperature_2m": 23.44, "weather_code": 61, "is_day": 1}})
    assert view == {"condition": "小雨", "temperature": 23.4, "is_day": True, "rainy": True, "snowy": False, "clear": False}
    clear_view = describe_weather({"current": {"temperature_2m": 30, "weather_code": 0, "is_day": 0}})
    assert clear_view["clear"] is True and clear_view["rainy"] is False
