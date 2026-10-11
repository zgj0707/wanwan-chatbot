from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.memory import canonical_fact_hash, fact_importance, retrieve_facts
from app.attention import select_relevant
from app.prompt import build_prompt
from app.state import CharacterState, RelationshipState, apply_analysis_delta, project_state
from app.models import AnalysisStateDelta
from tests.test_state import make_prompt_data


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

    # Identity facts never fade, even when last injected months ago.
    name_stale = _fact(
        "profile.preferred_name",
        "乌龙",
        updated_at=(NOW - timedelta(days=90)).isoformat().replace("+00:00", "Z"),
    )
    named = retrieve_facts("乌龙", [name_stale], top_k=1, now=NOW)
    assert named[0]["key"] == "profile.preferred_name"
    assert named[0]["decay_weight"] == fact_importance("profile.preferred_name")


def test_retrieval_importance_breaks_similarity_ties():
    low = _fact("preference.food", "火锅", importance=0.7)
    high = _fact("plan.personal", "火锅", importance=0.9)
    ranked = retrieve_facts("火锅", [low, high], top_k=2, now=NOW)
    assert ranked[0]["key"] == "plan.personal"
    assert ranked[0]["score"] == ranked[1]["score"] == 1.0
    assert ranked[0]["rank_score"] > ranked[1]["rank_score"]


def test_retrieval_invalid_importance_is_rejected():
    broken = _fact("preference.drink", "乌龙茶", importance=0.2)
    with pytest.raises(Exception) as failure:
        retrieve_facts("乌龙", [broken], top_k=1, now=NOW)
    assert "memory rows are invalid" in str(failure.value)


def test_missing_importance_falls_back_to_key_default():
    fact = _fact("profile.preferred_name", "小满")
    ranked = retrieve_facts("小满", [fact], top_k=1, now=NOW)
    assert ranked[0]["importance"] == pytest.approx(1.0)


@pytest.mark.parametrize("top_k", [1, 2, 3, 5])
def test_identity_coverage_survives_all_matching_ordinary_facts(top_k):
    name = _fact("profile.preferred_name", "小满", updated_at="2026-01-01T00:00:00Z")
    birthday = _fact("profile.birthday", "02-29", updated_at="2026-01-01T00:00:00Z")
    ordinary = [_fact(key, "咖啡") for key in (
        "preference.drink", "preference.food", "preference.hobby", "preference.chat_style", "plan.personal",
    )]
    ranked = retrieve_facts("咖啡", ordinary + [birthday, name], top_k=top_k, now=NOW)
    assert len(ranked) == top_k
    assert name["id"] in {fact["id"] for fact in ranked}
    assert (birthday["id"] in {fact["id"] for fact in ranked}) == (top_k >= 2)
    for fact in ranked:
        if fact["key"].startswith("profile."):
            assert fact["decay_weight"] == 1.0


def test_explicit_topic_cues_recall_values_without_overlap_and_explain_misses():
    drink = _fact("preference.drink", "乌龙茶")
    food = _fact("preference.food", "火锅")
    hobby = _fact("preference.hobby", "胶片摄影")
    inactive = dict(_fact("plan.personal", "喝茶"), status="superseded")
    debug = []
    ranked = retrieve_facts("我喜欢喝什么？", [drink, food, hobby, inactive], now=NOW, debug=debug)
    assert [fact["id"] for fact in ranked] == [drink["id"]]
    assert ranked[0]["score"] == 0.0  # Original Dice meaning is unchanged.
    assert ranked[0]["rank_score"] > 0
    by_id = {item["id"]: item for item in debug}
    assert by_id[drink["id"]]["topic_cues"] == ["喝"]
    assert by_id[drink["id"]]["reason"] == "relevance_match"
    assert by_id[food["id"]]["reason"] == by_id[hobby["id"]]["reason"] == "no_relevance"
    assert by_id[inactive["id"]]["reason"] == "inactive"
    assert all("value" not in item for item in debug)
    assert "rank_score" not in drink  # Caller rows are not mutated.


def test_capacity_miss_reasons_and_unrelated_identity_fallback_are_inspectable():
    name = _fact("profile.preferred_name", "小满")
    birthday = _fact("profile.birthday", "02-29")
    drink = _fact("preference.drink", "咖啡")
    debug = []
    ranked = retrieve_facts("咖啡", [drink, birthday, name], top_k=1, now=NOW, debug=debug)
    assert ranked[0]["id"] == name["id"]
    reasons = {item["id"]: item["reason"] for item in debug}
    assert reasons == {
        name["id"]: "identity_coverage", birthday["id"]: "identity_capacity",
        drink["id"]: "capacity_after_identity_coverage",
    }
    unrelated = retrieve_facts("完全无关", [drink, birthday, name], now=NOW)
    assert {fact["id"] for fact in unrelated} == {name["id"], birthday["id"]}


def test_decay_floor_last_prompt_refresh_and_future_timestamps():
    stale = _fact("preference.food", "乌龙茶", updated_at="2026-01-01T00:00:00Z")
    refreshed = _fact("preference.drink", "乌龙茶", updated_at=stale["updated_at"], last_prompt_at=NOW.isoformat())
    future = _fact("preference.hobby", "乌龙茶", updated_at=(NOW + timedelta(days=1)).isoformat())
    ranked = retrieve_facts("乌龙茶", [stale, refreshed, future], now=NOW)
    by_key = {fact["key"]: fact for fact in ranked}
    assert by_key["preference.food"]["decay_weight"] == pytest.approx(0.7 * 0.6)
    assert by_key["preference.drink"]["decay_weight"] == pytest.approx(0.7)
    assert by_key["preference.hobby"]["decay_weight"] == pytest.approx(0.7)
    assert ranked[-1]["id"] == stale["id"]


def test_rank_ties_use_normalized_time_then_id_independent_of_input_order():
    first = _fact("preference.drink", "咖啡", updated_at="2026-10-01T08:00:00+08:00")
    second = _fact("preference.food", "咖啡", updated_at="2026-10-01T00:00:00Z")
    first["id"] = "00000000-0000-4000-8000-000000000001"
    second["id"] = "00000000-0000-4000-8000-000000000002"
    for facts in ([first, second], [second, first]):
        assert [fact["id"] for fact in retrieve_facts("咖啡", facts, now=NOW)] == [first["id"], second["id"]]


def test_prompt_preserves_weighted_recall_after_attention_and_trims_lowest_weight():
    # The stale value has higher raw similarity; the fresh value wins after decay.
    stale = _fact("preference.food", "乌龙茶", updated_at="2026-01-01T00:00:00Z")
    fresh = _fact("preference.drink", "乌龙茶会", updated_at=NOW.isoformat())
    ranked = retrieve_facts("乌龙茶", [stale, fresh], now=NOW)
    assert [fact["id"] for fact in ranked] == [fresh["id"], stale["id"]]
    assert ranked[0]["score"] < ranked[1]["score"]
    attended, _, _ = select_relevant(topic_hint="闲聊", facts=list(reversed(ranked)), events=[], facts_top_k=5, events_top_k=3)
    args = dict(make_prompt_data(), facts=attended, input_budget_units=100000)
    bundle = build_prompt(**args)
    assert bundle.fact_ids == (fresh["id"], stale["id"])
    fit = build_prompt(**dict(args, facts=[ranked[0]]))
    trimmed = build_prompt(**dict(args, input_budget_units=fit.input_units))
    assert trimmed.fact_ids == (fresh["id"],)
    assert trimmed.dropped_fact_ids == (stale["id"],)
    payload = json.loads(bundle.messages[4]["content"].split("\n", 1)[1])
    assert [item["id"] for item in payload] == list(bundle.fact_ids)
    assert all(set(item) == {"id", "key", "value"} for item in payload)


def test_prompt_budget_drops_preferences_then_birthday_then_name_without_false_refs():
    name = _fact("profile.preferred_name", "小满")
    birthday = _fact("profile.birthday", "02-29")
    drink = _fact("preference.drink", "手冲咖啡")
    ranked = retrieve_facts("手冲咖啡", [name, birthday, drink], now=NOW)
    args = dict(make_prompt_data(), facts=ranked, input_budget_units=100000)
    identity_rows = [fact for fact in ranked if fact["key"].startswith("profile.")]
    identity_fit = build_prompt(**dict(args, facts=identity_rows))
    trimmed = build_prompt(**dict(args, input_budget_units=identity_fit.input_units))
    assert set(trimmed.fact_ids) == {name["id"], birthday["id"]}
    assert trimmed.dropped_fact_ids == (drink["id"],)
    name_fit = build_prompt(**dict(args, facts=[fact for fact in ranked if fact["id"] == name["id"]]))
    trimmed_again = build_prompt(**dict(args, input_budget_units=name_fit.input_units))
    assert trimmed_again.fact_ids == (name["id"],)
    assert set(trimmed_again.dropped_fact_ids) == {drink["id"], birthday["id"]}


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
