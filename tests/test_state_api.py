from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.clock import FakeClock
from app.config import load_config
from app.db import to_utc_iso
from app.llm import FakeClient
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]

# 2026-10-08 是周四：10:30（上海）落在工作时段，23:30 属深夜休息时段
NOON_SHANGHAI = datetime(2026, 10, 8, 10, 30, tzinfo=timezone(timedelta(hours=8)))
LATE_NIGHT_SHANGHAI = datetime(2026, 10, 8, 23, 30, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture
def client_factory(tmp_path: Path):
    clients: list[TestClient] = []

    def make(fake: FakeClient | None = None, *, data_dir: Path | None = None):
        app = create_app(
            load_config(ROOT / "config.example.yaml"),
            project_dir=ROOT,
            data_dir=data_dir or tmp_path / f"state-data-{len(clients)}",
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


def set_character_state(app, *, valence: int, energy: int, updated_at: datetime) -> None:
    with app.state.repository.transaction() as connection:
        connection.execute(
            "UPDATE character_state SET valence = ?, energy = ?, updated_at = ?",
            (valence, energy, to_utc_iso(updated_at)),
        )


def set_relationship_state(app, *, closeness: int | None = None, guard_until: datetime | None = None) -> None:
    assignments = ["updated_at = ?"]
    parameters: list[object] = [to_utc_iso(datetime.now(timezone.utc))]
    if closeness is not None:
        assignments.append("closeness = ?")
        parameters.append(closeness)
    if guard_until is None or isinstance(guard_until, datetime):
        assignments.append("guard_until = ?")
        parameters.append(None if guard_until is None else to_utc_iso(guard_until))
    with app.state.repository.transaction() as connection:
        connection.execute(f"UPDATE relationship_state SET {', '.join(assignments)}", tuple(parameters))


def get_state(client: TestClient) -> dict:
    response = client.get("/api/v1/state")
    assert response.status_code == 200
    return response.json()


def test_state_returns_atmosphere_fields_without_numeric_values(client_factory):
    app, client = client_factory()
    app.state.repository.clock = FakeClock(NOON_SHANGHAI)

    body = get_state(client)

    assert set(body) == {"mood", "closeness_level", "late_night", "scene", "guard", "current_activity"}
    # CLR 二阶段：current_activity 由启动 seed+catchup 填充（她此刻在做的事），非空字符串即可
    assert isinstance(body["current_activity"], (str, type(None)))
    for value in body.values():
        assert value is None or isinstance(value, (bool, str))
    assert body["mood"] == "calm"
    assert body["closeness_level"] == "初识"
    assert body["guard"] is False
    assert body["late_night"] is False
    assert body["scene"] == "work_time"


def test_state_reports_late_night_and_rest_scene(client_factory):
    app, client = client_factory()
    app.state.repository.clock = FakeClock(LATE_NIGHT_SHANGHAI)

    body = get_state(client)

    assert body["late_night"] is True
    assert body["scene"] == "rest_time"


def test_state_mood_tiers_follow_projected_state(client_factory):
    app, client = client_factory()
    now = datetime.now(timezone.utc)
    scenarios = [
        ({"valence": 40, "energy": 80}, "happy"),
        ({"valence": -40, "energy": 80}, "sad"),
        ({"valence": 0, "energy": 20}, "tired"),
        ({"valence": 40, "energy": 20}, "tired"),  # energy 优先于 valence
    ]
    for patch, expected in scenarios:
        set_character_state(app, valence=patch["valence"], energy=patch["energy"], updated_at=now)
        assert get_state(client)["mood"] == expected


def test_state_closeness_levels_follow_tier_boundaries(client_factory):
    app, client = client_factory()
    now = datetime.now(timezone.utc)
    set_character_state(app, valence=0, energy=60, updated_at=now)
    for closeness, expected in [(0, "初识"), (29, "初识"), (30, "熟悉"), (59, "熟悉"), (60, "亲近"), (100, "亲近")]:
        set_relationship_state(app, closeness=closeness)
        assert get_state(client)["closeness_level"] == expected


def test_state_guard_flag_is_true_while_guard_is_active(client_factory):
    app, client = client_factory()
    now = datetime.now(timezone.utc)
    set_character_state(app, valence=40, energy=80, updated_at=now)
    set_relationship_state(app, guard_until=now + timedelta(hours=2))

    body = get_state(client)

    assert body["guard"] is True
    assert body["mood"] == "cold"  # 警戒期优先于高 valence

    set_relationship_state(app, guard_until=now - timedelta(minutes=5))
    body = get_state(client)
    assert body["guard"] is False
    assert body["mood"] == "happy"  # 过期的警戒不再生效


def test_state_returns_data_corrupt_when_state_rows_are_missing(client_factory):
    app, client = client_factory()
    now = datetime.now(timezone.utc)

    with app.state.repository.transaction() as connection:
        connection.execute("DELETE FROM character_state")
    response = client.get("/api/v1/state")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_CORRUPT"

    with app.state.repository.transaction() as connection:
        connection.execute(
            "INSERT INTO character_state(character_id, valence, energy, updated_at, version) VALUES ('suwan', 0, 60, ?, 1)",
            (to_utc_iso(now),),
        )
        connection.execute("DELETE FROM relationship_state")
    response = client.get("/api/v1/state")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_CORRUPT"


def test_state_returns_data_corrupt_when_timestamps_are_invalid(client_factory):
    app, client = client_factory()
    with app.state.repository.transaction() as connection:
        connection.execute("UPDATE character_state SET updated_at = 'not-a-timestamp'")

    response = client.get("/api/v1/state")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_CORRUPT"
