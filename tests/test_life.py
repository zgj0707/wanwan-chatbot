"""CLR 二阶段：时间推进、目标连续性、事件管线与幂等测试（纯规则，零 LLM）。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clock import FakeClock
from app.db import initialize_database
from app.life import (
    PHOTO_GOAL_TITLE,
    RuleDecisionEngine,
    DecisionContext,
    run_startup_catchup,
    seed_goals,
    seed_schedule,
    simulate_until,
)
from app.repository import Repository

NOW = datetime(2026, 10, 8, 10, 30, tzinfo=timezone(timedelta(hours=8)))  # 周四 10:30


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    db = tmp_path / "life.sqlite3"
    initialize_database(db, clock=FakeClock(NOW))
    repo = Repository(db)
    repo.clock = FakeClock(NOW)
    seed_schedule(repo, now=NOW)
    seed_goals(repo, now=NOW)
    return repo


def _saturday_afternoon(base: datetime) -> datetime:
    """从 base 所在周推一个周六 15:00。"""
    days_ahead = (5 - base.weekday()) % 7 or 7
    saturday = base + timedelta(days=days_ahead)
    return saturday.replace(hour=15, minute=0, second=0, microsecond=0)


def test_seed_schedule_is_idempotent(repo: Repository):
    seed_schedule(repo, now=NOW)  # 重复 seed 不产生重复行
    workday = repo.list_schedule("workday")
    weekend = repo.list_schedule("weekend")
    starts = [row["start_time"] for row in workday]
    assert len(starts) == len(set(starts))
    assert {"07:10", "09:20", "23:00"} <= set(starts)
    assert any("自由时段" in row["activity"] for row in weekend)


def test_seed_goals_with_causes(repo: Repository):
    goals = repo.list_goals(status=None)
    titles = {goal["title"] for goal in goals}
    assert PHOTO_GOAL_TITLE in titles
    photo = next(goal for goal in goals if goal["title"] == PHOTO_GOAL_TITLE)
    assert photo["horizon"] == "long" and photo["status"] == "active"
    assert "完美主义" in (photo["detail"] or "")
    # 未启动小愿望也在
    assert "去潮汕吃一轮" in titles
    commitments = repo.list_commitments()
    kinds = {row["kind"] for row in commitments}
    assert {"value", "conflict", "principle", "commitment"} <= kinds


def test_goal_survives_unrelated_activity(repo: Repository):
    """测试 C（目标持续性）：连续多轮不提目标，目标仍在且可推进。"""
    goal_id = repo.list_goals(status="active")[0]["id"]
    # 模拟"聊了 20 轮无关话题"——只推进时钟，不触碰目标
    later = NOW + timedelta(days=3)
    repo.clock.set(later)
    advanced = repo.advance_goal(goal_id, now=later)
    assert advanced is not None and advanced["progress_step"] == 1
    assert repo.list_goals(status="active")


def test_simulate_until_updates_activity_and_logs_intent(repo: Repository):
    """测试 E 前半（离线生活连续性）：从周四 10:30 推进到周六 16:00。"""
    saturday = _saturday_afternoon(NOW).replace(hour=16)
    events = simulate_until(repo, now=saturday, last_sim_time=NOW)
    assert events, "应至少产生日程切换事件"
    kinds = {event["kind"] for event in events}
    assert "schedule_transition" in kinds
    # 周六下午自由时段应有规则决策的 intent
    with repo.connection() as connection:
        intents = connection.execute(
            "SELECT chosen_action, decided_by, status FROM action_intents"
        ).fetchall()
    assert intents, "自由时段应产生规则决策"
    assert all(row["decided_by"] == "rule" for row in intents)
    assert all(row["status"] == "executed" for row in intents)
    # 摄影类行动推进了长期目标
    photo_actions = {row["chosen_action"] for row in intents} & {"go_photo_walk", "edit_photos"}
    if photo_actions:
        goals = repo.list_goals(status="active")
        photo = next(goal for goal in goals if goal["title"] == PHOTO_GOAL_TITLE)
        assert photo["progress_step"] >= 1


def test_catchup_over_three_days_is_consistent(repo: Repository):
    """测试 E 后半：三天缺口补算后，游标推进、事件不重复、当前活动合理。

    首次启动（游标缺失）不编造启动前的历史（last=now → 0 事件），这是设计行为；
    所以先用一次 catchup 建立游标，再制造缺口验证补算。
    """
    run_startup_catchup(repo, now=NOW)  # 首次：仅建立游标
    first = run_startup_catchup(repo, now=NOW + timedelta(days=3))
    assert first, "三天缺口应产生补算事件"
    second = run_startup_catchup(repo, now=NOW + timedelta(days=3, minutes=10))
    # 第二次只补 10 分钟：不应重复第一天的全部事件
    assert len(second) <= len(first)
    # 游标已推进
    with repo.connection() as connection:
        cursor = connection.execute("SELECT value FROM app_meta WHERE key = 'life_cursor_at'").fetchone()
    assert cursor is not None and cursor["value"] >= (NOW + timedelta(days=3, minutes=10)).isoformat()


def test_catchup_over_14_days_degrades_gracefully(repo: Repository):
    """超长缺口（>14 天）：只推进状态不逐时段编造（纪要 7.3）。"""
    run_startup_catchup(repo, now=NOW)  # 建立游标
    events = run_startup_catchup(repo, now=NOW + timedelta(days=30))
    assert events == []


def test_same_sim_time_intent_is_unique(repo: Repository):
    """幂等：同一模拟时刻的动作不会重复记录两条 executed。"""
    saturday = _saturday_afternoon(NOW).replace(hour=15, minute=30)
    simulate_until(repo, now=saturday, last_sim_time=NOW)
    with repo.connection() as connection:
        rows = connection.execute(
            "SELECT sim_time, chosen_action, COUNT(*) AS n FROM action_intents "
            "WHERE status = 'executed' GROUP BY sim_time, chosen_action HAVING n > 1"
        ).fetchall()
    assert rows == []


def test_rule_engine_deep_night_always_rests():
    late = DecisionContext(
        sim_time=NOW.replace(hour=23, minute=30),
        energy=90, is_weekend=False, active_goals=(), schedule_activity=None,
    )
    engine = RuleDecisionEngine()
    assert engine.decide(late, engine.candidates(late), __import__("random").Random(1)) == "rest"


def test_rule_engine_photo_goal_boosts_photo_actions():
    import random
    saturday = DecisionContext(
        sim_time=datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc),
        energy=80, is_weekend=True,
        active_goals=(PHOTO_GOAL_TITLE,), schedule_activity="自由时段（拍照外拍 / 窝沙发看老电影 / 手冲失败 / 画画）",
    )
    engine = RuleDecisionEngine()
    candidates = engine.candidates(saturday)
    assert "go_photo_walk" in candidates and "noop" in candidates
    picks = {engine.decide(saturday, candidates, random.Random(seed)) for seed in range(30)}
    # 有摄影目标加权时，摄影类行动应高频出现（不是永远 noop）
    assert picks & {"go_photo_walk", "edit_photos"}
