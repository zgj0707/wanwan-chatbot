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

    def make(fake: FakeClient | None = None, *, real: bool = False):
        config = load_config(ROOT / "config.example.yaml")
        if real:
            raw = config.model_dump(mode="python")
            raw["mode"] = "real"
            config = AppConfig.model_validate(raw)
        app = create_app(
            config,
            project_dir=ROOT,
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


def test_model_probe_is_explicit_single_call_and_persists_no_prompt_or_operation(client_factory):
    fake = FakeClient({"analysis": '{"state_delta":{"valence":0,"energy":0,"closeness":0,"guard_trigger":false},"fact_candidates":[],"persona_proposals":[]}'} )
    app, client = client_factory(fake)
    try:
        before = client.get("/api/v1/diagnostics").json()["metrics"]["call_count"]
        probe = client.post("/api/v1/diagnostics/probe", json={"profile": "analysis"})
        assert probe.status_code == 200
        assert probe.json()["ok"] is True and probe.json()["schema_valid"] is True
        assert len(fake.calls) == 1 and fake.calls[0]["profile"] == "analysis"
        metrics = client.get("/api/v1/diagnostics").json()["metrics"]
        assert metrics["call_count"] == before + 1
        assert metrics["recent_calls"][0]["operation_id"] is None
        assert "连接测试" not in probe.text
        with app.state.repository.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    finally:
        client.__exit__(None, None, None)


def test_missing_real_credentials_probe_returns_failure_without_provider_call(client_factory, monkeypatch):
    monkeypatch.delenv("SUWAN_API_KEY", raising=False)
    fake = FakeClient()
    _, client = client_factory(fake, real=True)
    try:
        response = client.post("/api/v1/diagnostics/probe", json={"profile": "chat"})
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert response.json()["error"]["code"] == "CONFIG_MISSING"
        assert fake.calls == []
    finally:
        client.__exit__(None, None, None)


def test_failed_probe_records_error_once_without_retry(client_factory):
    fake = FakeClient({"chat": [FakeOutcome(error_code="UPSTREAM_NETWORK", retryable=True), "unused second attempt"]})
    app, client = client_factory(fake)
    try:
        response = client.post("/api/v1/diagnostics/probe", json={"profile": "chat"})
        assert response.status_code == 200 and response.json()["ok"] is False
        assert response.json()["error"]["code"] == "UPSTREAM_NETWORK"
        assert len(fake.calls) == 1
        metrics = client.get("/api/v1/diagnostics").json()["metrics"]
        assert metrics["call_count"] == 1 and metrics["degraded_count"] == 1
        with app.state.repository.connection() as connection:
            assert connection.execute("SELECT status FROM call_metrics").fetchone()[0] == "failed"
    finally:
        client.__exit__(None, None, None)
