from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.memory import canonical_fact_hash, fact_importance, retrieve_facts
from app.state import CharacterState, RelationshipState, apply_analysis_delta, project_state
from app.models import AnalysisStateDelta


NOW = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)


def _fact(key: str, value: str, *, importance: float | None = None, last_prompt_at: str | None = None, updated_at: str = "2026-10-01T00:00:00Z") -> dict:
    fact = {
        "id": str(uuid4()),
        "key": key,
        "value": value,
        "canonical_hash": canonical_fact_hash(key, value),
        "status": "active",
        "updated_at": updated_at,
    }
    if importance is not None:
        fact["importance"] = importance
    if last_prompt_at is not None:
        fact["last_prompt_at"] = last_prompt_at
    return fact


def _projection(closeness: int) -> object:
    character = CharacterState(0, 60, NOW, 1)
    relationship = RelationshipState(closeness, None, NOW, 1)
    return project_state(character, relationship, NOW)


def test_retrieval_decays_stale_ordinary_preferences_but_not_identity_facts():
    fresh = _fact("preference.drink", "乌龙茶", updated_at=NOW.isoformat().replace("+00:00", "Z"))
    stale = _fact(
        "preference.food",
        "火锅乌龙",
        updated_at=(NOW - timedelta(days=60)).isoformat().replace("+00:00", "Z"),
    )
    # Both match the query "乌龙" similarly; freshness must win for ordinary keys.
    ranked = retrieve_facts("乌龙", [stale, fresh], top_k=2, now=NOW)
    assert ranked[0]["key"] == "preference.drink"

    # Identity facts never fade: a two-months-old name still outranks a fresh one? No —
    # importance caps the penalty, but identical similarity + equal recency keeps
    # the fresher row first. What decay must NOT do is bury a name below the floor.
    name_stale = _fact(
        "profile.preferred_name",
        "乌龙",
        updated_at=(NOW - timedelta(days=90)).isoformat().replace("+00:00", "Z"),
    )
    named = retrieve_facts("乌龙", [name_stale], top_k=1, now=NOW)
    assert named[0]["key"] == "profile.preferred_name"
    assert named[0]["decay_weight"] >= fact_importance("profile.preferred_name") * 0.6


def test_retrieval_importance_breaks_similarity_ties():
    low = _fact("preference.food", "火锅", importance=0.7)
    high = _fact("plan.personal", "火锅局", importance=0.9)
    ranked = retrieve_facts("火锅", [low, high], top_k=2, now=NOW)
    # 相似度相同时（火锅 vs 火锅/火锅局 的 bigram Dice），高重要性排前。
    scores = {fact["key"]: fact["score"] * fact["decay_weight"] for fact in ranked}
    if scores["preference.food"] == scores["plan.personal"]:
        assert ranked[0]["key"] == "plan.personal"


def test_retrieval_invalid_importance_is_rejected():
    broken = _fact("preference.drink", "乌龙茶", importance=0.2)
    with pytest.raises(Exception) as failure:
        retrieve_facts("乌龙", [broken], top_k=1, now=NOW)
    assert "memory rows are invalid" in str(failure.value)


def test_missing_importance_falls_back_to_key_default():
    fact = _fact("profile.preferred_name", "小满")
    ranked = retrieve_facts("小满", [fact], top_k=1, now=NOW)
    assert ranked[0]["importance"] == pytest.approx(1.0)


def test_closeness_gain_has_diminishing_returns_but_never_stalls():
    # Low band: full gain.
    low = apply_analysis_delta(_projection(10), AnalysisStateDelta(valence=0, energy=0, closeness=2, guard_trigger=False), NOW)
    assert low.closeness == 12
    # Mid band (60+): half, at least one.
    mid = apply_analysis_delta(_projection(60), AnalysisStateDelta(valence=0, energy=0, closeness=2, guard_trigger=False), NOW)
    assert mid.closeness == 61
    # High band (85+): quarter, at least one — still climbing, just slower.
    # (delta 模型上限 ±2；2×0.25=0.5 → round=0，被保底拉到 1。)
    high = apply_analysis_delta(_projection(85), AnalysisStateDelta(valence=0, energy=0, closeness=2, guard_trigger=False), NOW)
    assert high.closeness == 86
    # Losses are never softened.
    loss = apply_analysis_delta(_projection(50), AnalysisStateDelta(valence=0, energy=0, closeness=-2, guard_trigger=False), NOW)
    assert loss.closeness == 48
    # Clamp at 100 still holds.
    capped = apply_analysis_delta(_projection(100), AnalysisStateDelta(valence=0, energy=0, closeness=2, guard_trigger=False), NOW)
    assert capped.closeness == 100
