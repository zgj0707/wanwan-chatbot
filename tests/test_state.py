from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.prompt import HistoryTurn, PromptDataCorrupt, PromptTooLarge, budget_units, build_prompt
from app.db import initialize_database
from app.repository import Repository
from app.state import (
    CharacterState,
    DataCorrupt,
    RelationshipState,
    apply_analysis_delta,
    mood_label,
    project_state,
    load_state_rows,
    time_context,
)
from app.models import AnalysisStateDelta


NOW = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)


def make_states(
    *,
    valence: int = 40,
    energy: int = 42,
    closeness: int = 17,
    guard_until: datetime | None = None,
    updated_at: datetime = NOW,
):
    character = CharacterState(valence, energy, updated_at, 3)
    relationship = RelationshipState(closeness, guard_until, updated_at, 5)
    return character, relationship


def make_prompt_data():
    character, relationship = make_states()
    threads = [
        {
            "template_key": key,
            "label": label,
            "stage": "planned",
            "step": 0,
            "active": 1,
        }
        for key, label in (
            ("coffee_practice", "试一种手冲方法"),
            ("photo_selection", "整理一组随手拍"),
            ("work_requirement", "整理这周的一个需求"),
        )
    ]
    args = {
        "persona_version_id": "persona-v1",
        "anchors": {"name": "苏晚", "virtual_identity": True},
        "preferences": {"hobbies": ["拍照"], "likes": [], "dislikes": []},
        "voice_text": "温柔、慵懒、好奇。保持边界感。",
        "time": time_context(NOW),
        "state": project_state(character, relationship, NOW),
        "life_threads": threads,
        "world_events": [],
        "facts": [],
        "history": [],
        "user_text": "你在忙吗？",
        "input_budget_units": 10000,
    }
    return args


def make_event(event_id: str, status: str, occurred_at: str, summary: str = "整理了一些照片，光线看起来很舒服。"):
    return {
        "id": event_id,
        "status": status,
        "thread_id": "thread-photo",
        "slot_key": occurred_at[:10] + "|19:30",
        "cycle": 0,
        "step_before": 0,
        "step_after": 1,
        "stage_before": "planned",
        "stage_after": "started",
        "occurred_at": occurred_at,
        "summary": summary,
    }


def test_state_decay_consumes_full_hours_and_keeps_the_remainder():
    character, relationship = make_states()
    now = NOW + timedelta(hours=2, minutes=59)

    result = project_state(character, relationship, now)

    assert (result.valence, result.energy, result.closeness) == (30, 48, 17)
    assert result.character_updated_at == NOW + timedelta(hours=2)
    assert result.decay_changed is True
    assert (character.valence, character.energy, character.updated_at, character.version) == (40, 42, NOW, 3)


def test_decay_in_segments_matches_one_projection_and_does_not_lose_partial_hour():
    character, relationship = make_states(valence=-31, energy=75)
    one_shot = project_state(character, relationship, NOW + timedelta(hours=24, minutes=30))

    first = project_state(character, relationship, NOW + timedelta(hours=12, minutes=15))
    persisted_at_first = CharacterState(first.valence, first.energy, first.character_updated_at, character.version + 1)
    second = project_state(persisted_at_first, relationship, NOW + timedelta(hours=24, minutes=30))

    assert (one_shot.valence, one_shot.energy, one_shot.character_updated_at) == (
        second.valence,
        second.energy,
        second.character_updated_at,
    )
    assert one_shot.character_updated_at == NOW + timedelta(hours=24)


def test_state_decay_is_clamped_toward_neutral_targets():
    character, relationship = make_states(valence=-3, energy=58)
    result = project_state(character, relationship, NOW + timedelta(hours=2))
    assert (result.valence, result.energy) == (0, 60)


def test_clock_backwards_warns_and_does_not_decay():
    character, relationship = make_states(updated_at=NOW)
    result = project_state(character, relationship, NOW - timedelta(minutes=1))

    assert (result.valence, result.energy) == (40, 42)
    assert result.character_updated_at == NOW
    assert result.warnings == ("CLOCK_BACKWARD",)


def test_character_and_relationship_state_are_global_across_sessions(tmp_path):
    database_path = tmp_path / "data" / "app.sqlite3"
    initialize_database(database_path)
    repository = Repository(database_path)
    first_session = repository.create_session("第一段")
    second_session = repository.create_session("第二段")
    assert first_session != second_session

    with repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM character_state").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM relationship_state").fetchone()[0] == 1


def test_loaded_state_rows_are_strict_and_corruption_is_not_silently_replaced():
    character_row = {
        "character_id": "suwan",
        "valence": 0,
        "energy": 60,
        "updated_at": "2026-10-07T00:00:00Z",
        "version": 1,
    }
    relationship_row = {
        "user_id": "local-user",
        "character_id": "suwan",
        "closeness": 10,
        "guard_until": None,
        "updated_at": "2026-10-07T00:00:00Z",
        "version": 1,
    }
    character, relationship = load_state_rows(character_row, relationship_row)
    assert (character.valence, character.energy, relationship.closeness) == (0, 60, 10)

    broken = dict(character_row, valence="0")
    with pytest.raises(DataCorrupt) as failure:
        load_state_rows(broken, relationship_row)
    assert failure.value.code == "DATA_CORRUPT"

    broken_time = dict(relationship_row, updated_at="not-a-timestamp")
    with pytest.raises(DataCorrupt):
        load_state_rows(character_row, broken_time)


def test_analysis_delta_clamps_state_and_sets_guard_for_two_hours():
    character, relationship = make_states(valence=98, energy=98, closeness=99)
    projection = project_state(character, relationship, NOW)
    delta = AnalysisStateDelta(valence=8, energy=5, closeness=2, guard_trigger=True)

    result = apply_analysis_delta(projection, delta, NOW)

    assert (result.valence, result.energy, result.closeness) == (100, 100, 100)
    assert result.guard_until == NOW + timedelta(hours=2)
    assert result.character_updated_at == result.relationship_updated_at == NOW
    assert (result.character_version, result.relationship_version) == (4, 6)
    assert result.write_character is result.write_relationship is True


def test_guard_extends_but_false_does_not_clear_before_expiration():
    old_guard = NOW + timedelta(hours=5)
    character, relationship = make_states(guard_until=old_guard)
    projection = project_state(character, relationship, NOW)
    result = apply_analysis_delta(
        projection,
        AnalysisStateDelta(valence=0, energy=0, closeness=0, guard_trigger=True),
        NOW,
    )
    assert result.guard_until == old_guard

    false_delta = AnalysisStateDelta(valence=0, energy=0, closeness=0, guard_trigger=False)
    retained = apply_analysis_delta(projection, false_delta, NOW)
    assert retained.guard_until == old_guard
    assert retained.write_relationship is False

    expired_projection = project_state(character, relationship, NOW + timedelta(hours=5))
    cleared = apply_analysis_delta(expired_projection, false_delta, NOW + timedelta(hours=5))
    assert cleared.guard_until is None
    assert cleared.write_relationship is True


def test_mood_diagnostic_priority_is_guard_energy_valence_then_calm():
    assert mood_label(50, 10, NOW + timedelta(hours=1), NOW) == "cold"
    assert mood_label(-50, 10, None, NOW) == "tired"
    assert mood_label(-26, 60, None, NOW) == "sad"
    assert mood_label(26, 60, None, NOW) == "happy"
    assert mood_label(25, 60, None, NOW) == "calm"


@pytest.mark.parametrize(
    ("local_time", "expected_late_night"),
    [
        (datetime(2026, 10, 7, 14, 59, tzinfo=timezone.utc), False),
        (datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc), True),
        (datetime(2026, 10, 6, 21, 59, tzinfo=timezone.utc), True),
        (datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc), False),
    ],
)
def test_time_context_uses_character_and_display_timezones(local_time, expected_late_night):
    context = time_context(local_time)
    assert context.is_late_night is expected_late_night
    assert context.character_time.tzinfo.key == "Asia/Shanghai"
    assert context.display_time.tzinfo.key == "Asia/Tokyo"
    assert context.display_time.hour == (context.character_time.hour + 1) % 24


def test_prompt_order_and_references_include_only_injected_context():
    args = make_prompt_data()
    args["facts"] = [
        {"id": "fact-1", "key": "profile.preferred_name", "value": "小林", "score": 1, "updated_at": "2026-10-07T00:00:00Z", "status": "active"}
    ]
    args["world_events"] = [
        make_event("event-1", "committed", "2026-10-06T19:30:00Z")
    ]
    args["history"] = [HistoryTurn(({"role": "user", "content": "你好"}, {"role": "assistant", "content": "嗯，我在。"}))]

    bundle = build_prompt(**args)

    assert [message["role"] for message in bundle.messages] == [
        "system", "system", "system", "system", "system", "user", "assistant", "system", "user"
    ]
    assert "固定身份锚点" in bundle.messages[0]["content"]
    assert "简单问候最多一个轻松问题" in bundle.messages[0]["content"]
    assert "只引用已确认事实" in bundle.messages[0]["content"]
    assert "年糕归属" in bundle.messages[0]["content"]
    assert "最多一个" in bundle.messages[0]["content"]
    assert "已经确认的人设偏好" in bundle.messages[1]["content"]
    assert "character_time" in bundle.messages[2]["content"]
    life = json.loads(bundle.messages[3]["content"].split("\n", 1)[1])
    assert [thread["template_key"] for thread in life["threads"]] == [
        "coffee_practice", "photo_selection", "work_requirement"
    ]
    assert life["events"][0]["id"] == "event-1"
    assert json.loads(bundle.messages[4]["content"].split("\n", 1)[1])[0]["id"] == "fact-1"
    assert "角色锁" in bundle.messages[-2]["content"]
    assert "苏晚" in bundle.messages[-2]["content"]
    assert "微信" in bundle.messages[-2]["content"]
    assert bundle.persona_version_id == "persona-v1"
    assert bundle.fact_ids == ("fact-1",)
    assert bundle.event_ids == ("event-1",)
    assert bundle.input_units == budget_units(bundle.messages)


def test_prompt_excludes_incomplete_failed_pending_and_redacted_history_and_events():
    args = make_prompt_data()
    args["history"] = [
        HistoryTurn(({"role": "user", "content": "old"}, {"role": "assistant", "content": "reply"})),
        HistoryTurn(({"role": "user", "content": "redacted"},), redacted=True),
        HistoryTurn(({"role": "user", "content": "pending"},), complete=False),
        HistoryTurn(({"role": "user", "content": "failed"},), status="failed"),
    ]
    args["world_events"] = [
        make_event("ok", "committed", "2026-10-06T19:30:00Z"),
        make_event("bad", "retracted", "2026-10-06T20:00:00Z"),
    ]

    bundle = build_prompt(**args)

    assert bundle.event_ids == ("ok",)
    assert any(message["content"] == "old" for message in bundle.messages)
    assert not any(message["content"] in {"redacted", "pending", "failed"} for message in bundle.messages)


def test_prompt_can_keep_an_initiative_assistant_message_without_fabricating_a_user_turn():
    args = make_prompt_data()
    args["history"] = [
        HistoryTurn(({"role": "assistant", "content": "今天广州下了点雨。"},), kind="initiative")
    ]

    bundle = build_prompt(**args)

    assert [message["role"] for message in bundle.messages[-3:]] == ["assistant", "system", "user"]
    assert bundle.messages[-3]["content"] == "今天广州下了点雨。"


def test_prompt_applies_fact_and_world_event_top_k_ordering():
    args = make_prompt_data()
    args["memory_top_k"] = 2
    args["world_events_top_k"] = 2
    args["facts"] = [
        {"id": "f1", "key": "preference.drink", "value": "乌龙茶", "score": 0.9, "updated_at": "2026-10-01T00:00:00Z", "status": "active"},
        {"id": "f2", "key": "preference.food", "value": "面", "score": 0.8, "updated_at": "2026-10-02T00:00:00Z", "status": "active"},
        {"id": "f3", "key": "profile.preferred_name", "value": "小林", "score": 0.1, "updated_at": "2026-10-01T00:00:00Z", "status": "active"},
    ]
    args["world_events"] = [
        make_event("e-old", "committed", "2026-10-01T12:30:00Z"),
        make_event("e-new", "committed", "2026-10-07T12:30:00Z"),
        make_event("e-mid", "committed", "2026-10-06T12:30:00Z"),
        make_event("e-retracted", "retracted", "2026-10-08T12:30:00Z"),
    ]

    bundle = build_prompt(**args)

    assert bundle.fact_ids == ("f1", "f3")
    assert bundle.event_ids == ("e-mid", "e-new")
    life_message = bundle.messages[3]["content"]
    assert life_message.index("e-mid") < life_message.index("e-new")
    assert "e-retracted" not in life_message


def test_prompt_keeps_only_the_most_recent_eight_complete_turns():
    args = make_prompt_data()
    args["history"] = [
        HistoryTurn(({"role": "user", "content": f"用户消息{i}"}, {"role": "assistant", "content": f"回复{i}"}))
        for i in range(10)
    ]

    bundle = build_prompt(**args)
    history_contents = [message["content"] for message in bundle.messages if message["role"] != "system"]

    assert "用户消息0" not in history_contents and "回复1" not in history_contents
    assert "用户消息2" in history_contents and "回复9" in history_contents


def test_prompt_rejects_corrupt_committed_event_instead_of_silently_omitting_it():
    args = make_prompt_data()
    args["world_events"] = [make_event("broken", "committed", "2026-10-06T19:30:00Z", "太短")]

    with pytest.raises(PromptDataCorrupt) as failure:
        build_prompt(**args)

    assert failure.value.code == "DATA_CORRUPT"


def test_prompt_budget_drops_oldest_history_then_lowest_fact_then_oldest_event():
    args = make_prompt_data()
    args["history"] = [
        HistoryTurn(({"role": "user", "content": "旧" * 400}, {"role": "assistant", "content": "答" * 400}))
    ]
    args["facts"] = [
        {"id": "fact-low", "key": "preference.food", "value": "食物", "score": 0.1, "updated_at": "2026-10-01T00:00:00Z", "status": "active"},
        {"id": "fact-high", "key": "preference.drink", "value": "乌龙茶", "score": 0.9, "updated_at": "2026-10-06T00:00:00Z", "status": "active"},
    ]
    args["world_events"] = [
        make_event("event-old", "committed", "2026-10-01T00:00:00Z", "旧" * 65 + "旧照片。"),
        make_event("event-new", "committed", "2026-10-06T00:00:00Z", "新" * 65 + "新照片。"),
    ]
    args["input_budget_units"] = 100000
    full = build_prompt(**args)

    without_history = dict(args, history=[], input_budget_units=100000)
    history_fit = build_prompt(**without_history)
    args["input_budget_units"] = history_fit.input_units
    first_trim = build_prompt(**args)
    assert first_trim.dropped_history_turns == 1
    assert first_trim.fact_ids == ("fact-high", "fact-low")
    assert first_trim.event_ids == ("event-old", "event-new")

    without_low_fact = dict(
        without_history,
        facts=[args["facts"][1]],
        input_budget_units=100000,
    )
    fact_fit = build_prompt(**without_low_fact)
    args["input_budget_units"] = fact_fit.input_units
    second_trim = build_prompt(**args)
    assert second_trim.dropped_history_turns == 1
    assert second_trim.dropped_fact_ids == ("fact-low",)
    assert second_trim.event_ids == ("event-old", "event-new")

    newest_only = dict(
        without_history,
        facts=args["facts"],
        world_events=[args["world_events"][1]],
        input_budget_units=100000,
    )
    event_fit = build_prompt(**newest_only)
    args["input_budget_units"] = event_fit.input_units
    final_trim = build_prompt(**args)
    assert final_trim.dropped_history_turns == 1
    assert "fact-low" in final_trim.dropped_fact_ids
    assert final_trim.dropped_event_ids == ("event-old",)
    assert final_trim.event_ids == ("event-new",)
    assert full.input_units > final_trim.input_units


def test_required_prompt_content_over_budget_raises_context_too_large():
    args = make_prompt_data()
    args["input_budget_units"] = 1
    with pytest.raises(PromptTooLarge) as failure:
        build_prompt(**args)
    assert failure.value.code == "CONTEXT_TOO_LARGE"
    assert failure.value.status_code == 413
