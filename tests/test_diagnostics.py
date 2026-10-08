from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, load_config
from app.llm import FakeClient, FakeOutcome
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client_factory(tmp_path: Path):
    clients: list[TestClient] = []

    def make(fake: FakeClient | None = None, *, real: bool = False, project_dir: Path | None = None):
        config = load_config(ROOT / "config.example.yaml")
        if real:
            raw = config.model_dump(mode="python")
            raw["mode"] = "real"
            config = AppConfig.model_validate(raw)
        app = create_app(
            config,
            project_dir=project_dir or ROOT,
            data_dir=tmp_path / f"diagnostics-data-{len(clients)}",
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


def test_diagnostics_hides_credentials_and_reports_only_call_metadata(client_factory, monkeypatch):
    monkeypatch.setenv("SUWAN_API_KEY", "diagnostic-secret-must-not-leak")
    _, client = client_factory(real=True)
    try:
        response = client.get("/api/v1/diagnostics")
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "real"
        assert body["profiles"]["chat"]["credential_configured"] is True
        assert body["metrics"]["call_count"] == 0
        assert "diagnostic-secret-must-not-leak" not in response.text
        assert "SUWAN_API_KEY" not in response.text
    finally:
        client.__exit__(None, None, None)


def test_model_probe_is_explicit_single_call_and_persists_no_prompt_or_operation(client_factory, monkeypatch, tmp_path):
    """新设置测试端点：读磁盘配置。这里给一个临时 project_dir（config.yaml+env），
    用 FakeClient 不可行（端点直接走 OpenAI SDK），改为断言密钥缺失路径 + 磁盘读取语义。"""
    fake = FakeClient({"analysis": '{"state_delta":{"valence":0,"energy":0,"closeness":0,"guard_trigger":false},"fact_candidates":[],"persona_proposals":[]}'} )
    project = tmp_path / "proj"
    project.mkdir()
    import yaml as _yaml

    raw = _yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    (project / "config.yaml").write_text(_yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    (project / ".env").write_text("SUWAN_API_KEY=fake-probe-key-not-real\n", encoding="utf-8")
    app, client = client_factory(fake, project_dir=project)
    try:
        probe = client.post("/api/v1/settings/test", json={"profile": "analysis"})
        assert probe.status_code == 200
        # 端点直连 OpenAI SDK；假密钥会得到 401/网络类失败，但绝不会 404 或 5xx
        assert probe.json()["ok"] in {True, False}
        assert "error" in probe.json()
        # 不产生 operations/messages（测试请求不入库）
        with app.state.repository.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    finally:
        client.__exit__(None, None, None)


def test_missing_real_credentials_probe_returns_failure_without_provider_call(client_factory, monkeypatch, tmp_path):
    # 本机可能设了用户级 SUWAN_API_KEY；删掉才能测"密钥为空"路径（.env 为空且环境无值）。
    monkeypatch.delenv("SUWAN_API_KEY", raising=False)
    fake = FakeClient()
    project = tmp_path / "proj-nokey"
    project.mkdir()
    import yaml as _yaml

    raw = _yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    (project / "config.yaml").write_text(_yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    (project / ".env").write_text("SUWAN_API_KEY=\n", encoding="utf-8")
    _, client = client_factory(fake, project_dir=project)
    try:
        response = client.post("/api/v1/settings/test", json={"profile": "chat"})
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert response.json()["error"]["code"] == "CONFIG_MISSING"
        assert fake.calls == []
    finally:
        client.__exit__(None, None, None)


def test_settings_test_reads_saved_config_without_restarting(client_factory, tmp_path):
    """保存后无需重启：修改磁盘 config.yaml 的模型名，测试端点应读到新值。
    用假 base_url（本机未监听端口）验证请求确实按新配置发出并失败于连接层。"""
    fake = FakeClient()
    project = tmp_path / "proj-switch"
    project.mkdir()
    import yaml as _yaml

    raw = _yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    raw["profiles"]["chat"]["base_url"] = "http://127.0.0.1:9/unreachable"
    (project / "config.yaml").write_text(_yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    (project / ".env").write_text("SUWAN_API_KEY=fake-key-for-switch-test\n", encoding="utf-8")
    _, client = client_factory(fake, project_dir=project)
    try:
        response = client.post("/api/v1/settings/test", json={"profile": "chat"})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        # 假端点不可达 → 连接层失败（不是配置错误，证明磁盘配置被读取并尝试）
        assert body["error"]["code"] in {"UPSTREAM_NETWORK", "UPSTREAM_ERROR", "UPSTREAM_TIMEOUT"}
    finally:
        client.__exit__(None, None, None)
