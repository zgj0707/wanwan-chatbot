"""Character Life Runtime 第二阶段：独立时间推进与规则事件管线（零 LLM）。

职责（对齐 CLR 方案第二阶段）：
- 日程种子：她的人设时间线（豆包人设第一节）作为工作日/周末两套模板；
- 规则决策器：根据模拟时间 / 精力 / 星期 / 活跃目标，从合法候选中选择行动
  （候选永远包含 noop——维持现状也是选择）；
- 事件管线：时间到达 → 读状态 → 生成候选 → 规则决策 → 校验 → 结果 → 持久化
  （intent + world_event 联合构成可追溯事件日志）；
- 幂等与恢复：同一 sim_time 只执行一次；崩溃残留 pending 由启动例程标 skipped。

本模块不调用任何 LLM（资源红线：阶段二管线 0 次/日）。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.repository import Repository

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# ── 日程种子（来自 docs/苏晚-人设扩写-决策驱动版.md 第一节）──

WEEKDAY_SCHEDULE: list[tuple[str, str, str]] = [
    ("07:10", "07:45", "赖床与挣扎起床"),
    ("07:45", "08:00", "洗漱换衣，喂年糕铲屎"),
    ("08:00", "09:20", "地铁通勤，路上听播客"),
    ("09:20", "12:00", "工位处理消息、写 PRD、开会"),
    ("12:00", "14:00", "午饭与午休"),
    ("14:00", "18:00", "下午盯数据改需求"),
    ("18:00", "19:30", "下班买菜做饭（大概率翻车）"),
    ("19:30", "23:00", "陪年糕、洗澡、随手拍、刷手机"),
    ("23:00", "23:30", "深夜电台时间"),
    ("23:30", "07:10", "睡觉"),
]

WEEKEND_SCHEDULE: list[tuple[str, str, str]] = [
    ("09:30", "11:30", "自然醒赖床，起来先撸猫"),
    ("11:30", "13:00", "磨蹭到中午才吃第一顿"),
    ("13:00", "18:00", "自由时段（拍照外拍 / 窝沙发看老电影 / 手冲失败 / 画画）"),
    ("18:00", "23:00", "做饭或外卖、听歌、陪年糕"),
    ("23:00", "23:30", "深夜电台时间"),
    ("23:30", "09:30", "睡觉"),
]

# 自由时段的候选行动（规则决策从这里选；含 noop）
FREE_TIME_CANDIDATES = {
    "high_energy": ["go_photo_walk", "edit_photos", "watch_old_movie", "brew_coffee", "noop"],
    "mid_energy": ["edit_photos", "watch_old_movie", "brew_coffee", "read", "noop"],
    "low_energy": ["rest", "watch_old_movie", "noop"],
}

ACTIVITY_LABELS = {
    "go_photo_walk": "出门拍照了",
    "edit_photos": "在整理照片",
    "watch_old_movie": "在看老电影",
    "brew_coffee": "在手冲咖啡（大概率要失败）",
    "read": "窝着看书",
    "rest": "瘫着休息",
    "noop": "维持现状",
}

PHOTO_GOAL_TITLE = "整理一组随手拍（作品集）"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def seed_schedule(repository: Repository, *, now: datetime) -> None:
    """写入日程种子（幂等：同 day_type+start_time 覆盖）。"""
    for start, end, activity in WEEKDAY_SCHEDULE:
        repository.upsert_schedule(day_type="workday", start_time=start, end_time=end, activity=activity, now=now)
    for start, end, activity in WEEKEND_SCHEDULE:
        repository.upsert_schedule(day_type="weekend", start_time=start, end_time=end, activity=activity, now=now)


def seed_goals(repository: Repository, *, now: datetime) -> None:
    """目标与承诺种子（来自人设扩写第二、三节；幂等 by title/content）。"""
    repository.upsert_goal(
        title=PHOTO_GOAL_TITLE,
        horizon="long",
        detail="去年秋天开始随手拍，攒了两三千张没筛选；卡在'必须有主题'的完美主义上。每周六下午外拍一次，执行率一半一半。",
        status="active",
        now=now,
    )
    repository.upsert_goal(title="学会一首完整的吉他曲", horizon="short", detail="墙角二手吉他落灰三个月。", status="idea", now=now)
    repository.upsert_goal(title="去潮汕吃一轮", horizon="mid", detail="美食攻略收藏一堆，一直没订票。", status="idea", now=now)
    for kind, content in (
        ("value", "诚实但不伤人：直说问题，但话到嘴边绕一下"),
        ("value", "珍视私人时间：下班后的时间是她自己的"),
        ("value", "不讨好：对谁一个语气"),
        ("value", "体面地认输：翻车就翻车，不硬撑也不过度自嘲"),
        ("value", "对年糕负责：再累也喂粮铲屎陪玩"),
        ("conflict", "希望被认可 vs 讨厌讨好"),
        ("conflict", "想保持边界 vs 其实怕孤单"),
        ("conflict", "想做作品集 vs 完美主义拖延"),
        ("principle", "进家门前在楼下站两分钟，不把工作情绪带进门"),
        ("commitment", "每周至少导一次照片到硬盘"),
    ):
        repository.upsert_commitment(kind=kind, content=content, now=now)


@dataclass(frozen=True)
class DecisionContext:
    sim_time: datetime
    energy: int
    is_weekend: bool
    active_goals: tuple[str, ...]
    schedule_activity: str | None


class RuleDecisionEngine:
    """规则基线决策器（纪要 A.2 方案 A）：可预测、可控、零成本。

    决策规则（刻意简单，这是基线）：
    - 日程内时段 → 直接执行日程活动（noop 语义）；
    - 自由时段 → 按精力档从候选中选；活跃的摄影目标提高 go_photo_walk/edit_photos 权重；
    - 深夜（23-7 点）永远 rest。
    """

    def candidates(self, context: DecisionContext) -> list[str]:
        if context.schedule_activity and "自由时段" not in context.schedule_activity:
            return ["follow_schedule", "noop"]
        if context.sim_time.hour >= 23 or context.sim_time.hour < 7:
            return ["rest", "noop"]
        if context.is_weekend or (context.sim_time.hour >= 18):
            if context.energy >= 60:
                return list(FREE_TIME_CANDIDATES["high_energy"])
            if context.energy >= 30:
                return list(FREE_TIME_CANDIDATES["mid_energy"])
            return list(FREE_TIME_CANDIDATES["low_energy"])
        return ["noop"]

    def decide(self, context: DecisionContext, candidates: list[str], rng: random.Random) -> str:
        if not candidates:
            return "noop"
        # 日程优先（自由时段除外——那是决策的时刻）
        if "follow_schedule" in candidates and context.schedule_activity and "自由时段" not in context.schedule_activity:
            return "follow_schedule"
        # 深夜休息
        if candidates == ["rest", "noop"]:
            return "rest"
        # 自由时段：摄影目标加权
        weights: dict[str, int] = {action: 10 for action in candidates}
        if "finish_personal_photo_project" in context.active_goals or PHOTO_GOAL_TITLE in context.active_goals:
            if "go_photo_walk" in weights and (context.is_weekend or context.sim_time.hour >= 15):
                weights["go_photo_walk"] += 15
            if "edit_photos" in weights:
                weights["edit_photos"] += 8
        # noop 保底权重（允许什么都不做）
        weights["noop"] = max(weights.get("noop", 10), 8)
        total = sum(weights.values())
        pick = rng.uniform(0, total)
        acc = 0
        for action, weight in weights.items():
            acc += weight
            if pick <= acc:
                return action
        return candidates[-1]


def simulate_until(
    repository: Repository,
    *,
    now: datetime,
    last_sim_time: datetime,
    rng: random.Random | None = None,
) -> list[dict]:
    """离散事件推进：从 last_sim_time 到 now，按日程补算生活事件。

    只在有意义的切换点生成事件（日程时段开始、自由时段的行动选择）；
    无法可靠补算的时段保留为日程活动本身，不编造细节（纪要 7.3）。
    返回生成的事件摘要列表。
    """
    rng = rng or random.Random()
    now = _aware(now)
    last = _aware(last_sim_time)
    events: list[dict] = []

    # 补算上限：缺口超过 14 天只推进状态不生成逐时段事件（防海量化）
    if now - last > timedelta(days=14):
        repository.set_current_activity(None, now=now)
        return events

    cursor = last
    guard = 0
    while cursor < now and guard < 500:  # guard：防异常数据死循环
        guard += 1
        day_type = "weekend" if cursor.weekday() >= 5 else "workday"
        schedule = repository.list_schedule(day_type)
        current_activity = _activity_at(schedule, cursor)
        state = _read_state(repository)
        if state.get("current_activity") != current_activity:
            repository.set_current_activity(current_activity, now=cursor)
            events.append({
                "sim_time": cursor.isoformat(),
                "kind": "schedule_transition",
                "activity": current_activity,
            })
            if current_activity and "自由时段" in current_activity:
                context = _build_context(repository, cursor, day_type, current_activity)
                engine = RuleDecisionEngine()
                candidates = engine.candidates(context)
                chosen = engine.decide(context, candidates, rng)
                if chosen not in ("noop", "follow_schedule"):
                    goal_id = _photo_goal_id(repository)
                    repository.record_action_intent(
                        goal_id=goal_id,
                        candidates=candidates,
                        chosen=chosen,
                        decided_by="rule",
                        validation={"within_schedule": True},
                        sim_time=cursor,
                        now=now,
                    )
                    if goal_id and chosen in ("go_photo_walk", "edit_photos"):
                        repository.advance_goal(goal_id, now=cursor)
                    events.append({
                        "sim_time": cursor.isoformat(),
                        "kind": "free_time_action",
                        "action": chosen,
                        "label": ACTIVITY_LABELS.get(chosen, chosen),
                    })
        # 步进到下一个日程边界（活动切换点），而不是固定间隔——切换永不遗漏；
        # 若当前时段一直延续到 now，则一步到位结束。
        next_boundary = _next_schedule_boundary(schedule, cursor, now)
        cursor = next_boundary

    return events


def _next_schedule_boundary(schedule: list[dict], at: datetime, now: datetime) -> datetime:
    """返回 at 之后的下一个日程切换时刻（或 now，取近者）。日程时刻按上海时区。"""
    local_now = at.astimezone(_SHANGHAI)
    candidates: list[datetime] = [now]
    for row in schedule:
        start = _hhmm(row["start_time"])
        end = _hhmm(row["end_time"])
        for minute_mark in (start, end):
            candidate = local_now.replace(hour=minute_mark // 60, minute=minute_mark % 60, second=0, microsecond=0)
            candidate = candidate.astimezone(timezone.utc)
            if candidate <= at:
                candidate += timedelta(days=1)
            candidates.append(candidate)
    nearest = min(candidates)
    return nearest


def _align_final_state(repository: Repository, *, now: datetime) -> dict | None:
    """补算终点：把 now 时刻的活动写进 current_activity（无切换点也保证状态对齐）。"""
    day_type = "weekend" if now.weekday() >= 5 else "workday"
    schedule = repository.list_schedule(day_type)
    activity = _activity_at(schedule, now)
    state = _read_state(repository)
    if state.get("current_activity") != activity:
        repository.set_current_activity(activity, now=now)
        return {"kind": "align", "activity": activity}
    return None


def _activity_at(schedule: list[dict], at: datetime) -> str | None:
    local = at.astimezone(_SHANGHAI)
    minutes = local.hour * 60 + local.minute
    for row in schedule:
        start = _hhmm(row["start_time"])
        end = _hhmm(row["end_time"])
        if start <= minutes < end:
            return row["activity"]
    return None


def _hhmm(value: str) -> int:
    hours, _, minutes = value.partition(":")
    return int(hours) * 60 + int(minutes)


def _read_state(repository: Repository) -> dict:
    with repository.connection() as connection:
        row = connection.execute("SELECT * FROM character_state WHERE character_id = 'suwan'").fetchone()
        return dict(row) if row is not None else {}


def _build_context(repository: Repository, at: datetime, day_type: str, current_activity: str) -> DecisionContext:
    state = _read_state(repository)
    goals = repository.list_goals(status="active")
    return DecisionContext(
        sim_time=at,
        energy=int(state.get("energy", 50)),
        is_weekend=day_type == "weekend",
        active_goals=tuple(goal["title"] for goal in goals),
        schedule_activity=current_activity,
    )


def _photo_goal_id(repository: Repository) -> str | None:
    for goal in repository.list_goals(status="active"):
        if goal["title"] == PHOTO_GOAL_TITLE:
            return goal["id"]
    return None


def run_startup_catchup(repository: Repository, *, now: datetime) -> list[dict]:
    """启动补算入口：读上次模拟时间 → simulate_until → 记录游标。

    游标复用 app_meta 的 world_cursor 语义但独立存键（life_cursor_at），
    与世界事件的游标互不干扰。
    """
    import json as _json

    with repository.connection() as connection:
        row = connection.execute("SELECT value FROM app_meta WHERE key = 'life_cursor_at'").fetchone()
    last = (
        datetime.fromisoformat(row["value"].replace("Z", "+00:00"))
        if row is not None
        else now
    )
    events = simulate_until(repository, now=now, last_sim_time=last)
    aligned = _align_final_state(repository, now=now)
    if aligned is not None:
        events.append({"kind": "align", "activity": aligned["activity"]})
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO app_meta(key, value) VALUES ('life_cursor_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_aware(now).isoformat(),),
        )
    _ = _json  # json 预留：后续事件序列化细化时使用
    return events
