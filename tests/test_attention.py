"""CLR 一阶段：注意力层（话题相关性筛选与披露选择）单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.attention import build_disclosure, normalize_topic, select_relevant


def _fact(id: str, key: str, value: str, updated_at: str = "2026-10-01T00:00:00Z") -> dict:
    return {"id": id, "key": key, "value": value, "status": "active", "updated_at": updated_at, "score": 0.5}


def _event(id: str, summary: str, occurred_at: str = "2026-10-05T12:00:00Z") -> dict:
    return {"id": id, "status": "committed", "summary": summary, "occurred_at": occurred_at}


FACTS = [
    _fact("f1", "preference.drink", "茉莉奶绿"),
    _fact("f2", "profile.preferred_name", "林小满"),
    _fact("f3", "plan.personal", "周末去东山口拍照"),
]

EVENTS = [
    _event("e1", "苏晚在工位上改需求，评审会又被打了回来。"),
    _event("e2", "苏晚整理了一组随手拍，挑出几张光线柔和的。"),
]


def test_normalize_topic_maps_keywords():
    assert normalize_topic("聊聊电影") == "电影"
    assert normalize_topic("今天加班好累") == "工作"
    assert normalize_topic("年糕最近怎么样") == "猫"
    assert normalize_topic(None) == "闲聊"
    assert normalize_topic("完全陌生的话题xyz") == "闲聊"


def test_off_topic_identity_events_are_suppressed():
    # 聊电影：工作事件应被降权（分数更低，但仍可能因 top_k 保留——只验证相对顺序）
    facts, events, debug = select_relevant(
        topic_hint="推荐个电影",
        facts=FACTS,
        events=EVENTS,
        facts_top_k=3,
        events_top_k=2,
    )
    scores = debug["event_scores"]
    photo_score = next(v for k, v in scores.items() if "随手拍" in k)
    work_score = next(v for k, v in scores.items() if "评审会" in k)
    assert photo_score > work_score
    assert any(item["reason"] == "work_label_off_topic" for item in debug["suppressed"])


def test_on_topic_events_are_boosted():
    # 聊工作：工作事件应被升权并排在前面
    _, events, debug = select_relevant(
        topic_hint="今天加班了",
        facts=FACTS,
        events=EVENTS,
        facts_top_k=3,
        events_top_k=1,
    )
    assert events[0]["id"] == "e1"


def test_top_k_limits_respected():
    many_events = [_event(f"e{i}", f"事件{i}号，拍照片。", occurred_at=f"2026-10-0{i + 1}T00:00:00Z") for i in range(5)]
    _, events, _ = select_relevant(
        topic_hint="拍照",
        facts=[],
        events=many_events,
        facts_top_k=5,
        events_top_k=2,
    )
    assert len(events) == 2


def test_identity_fact_downweight_off_topic():
    # 聊电影时，含"年糕/工作"字样的事实应被降权；称呼类身份事实靠 profile 前缀保底
    facts, _, debug = select_relevant(
        topic_hint="聊聊天",
        facts=FACTS,
        events=[],
        facts_top_k=3,
        events_top_k=0,
    )
    scores = debug["fact_scores"]
    # 称呼事实（profile 前缀）有 +0.2 保底
    assert scores["profile.preferred_name:林小满"] >= 1.2


def test_disclosure_respects_recent_topics():
    disclosure = build_disclosure(
        topic="问候",
        events=EVENTS,
        recent_topics=["工作", "工作"],  # 最近聊过工作 → 工作事件不再披露
    )
    assert all("需求" not in item for item in disclosure)

    disclosure2 = build_disclosure(topic="拍照", events=EVENTS, recent_topics=["问候"])
    assert any("随手拍" in item for item in disclosure2)
    assert len(disclosure2) <= 2


def test_disclosure_empty_when_nothing_relevant():
    assert build_disclosure(topic="问候", events=[], recent_topics=[]) == []
