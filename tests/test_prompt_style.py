from __future__ import annotations

from app.prompt import (
    CONVERSATION_STYLE_GUIDE,
    CONVERSATION_STYLE_NEGATIVES,
    build_prompt,
    build_role_lock,
    build_style_message,
    load_conversation_examples,
)

from tests.test_state import make_prompt_data, make_states
from app.state import time_context
from datetime import datetime, timezone

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def test_style_message_includes_voice_rules_negatives_and_examples():
    style = build_style_message(voice_text="温柔、慵懒、好奇。保持边界感。")
    assert "温柔、慵懒、好奇" in style
    assert CONVERSATION_STYLE_GUIDE in style
    assert CONVERSATION_STYLE_NEGATIVES in style
    assert "作为AI" in style
    examples = load_conversation_examples()
    assert examples
    assert "早高峰的地铁" in style
    assert "需求改了第八版" in style


def test_style_message_with_empty_examples_omits_example_block():
    style = build_style_message(voice_text="语感。", examples_text="")
    assert "语气示例" not in style


def test_style_message_survives_missing_examples_file(tmp_path):
    missing = tmp_path / "no-such-file.md"
    assert load_conversation_examples(missing) == ""


def test_role_lock_reflects_guard_state():
    calm = build_role_lock(guard_active=False)
    guarded = build_role_lock(guard_active=True)
    assert "苏晚" in calm and "广州" in calm and "年糕" in calm and "虚拟角色" in calm
    assert "不舒服" not in calm
    assert "不舒服" in guarded and "不假装没事" in guarded


def test_role_lock_is_injected_after_history_and_before_user_text():
    args = make_prompt_data()
    from app.prompt import HistoryTurn

    args["history"] = [
        HistoryTurn(({"role": "user", "content": "早"}, {"role": "assistant", "content": "早呀。"}))
    ]
    bundle = build_prompt(**args)
    roles = [message["role"] for message in bundle.messages]
    assert roles == ["system", "system", "system", "system", "system", "user", "assistant", "system", "user"]
    lock = bundle.messages[-2]["content"]
    assert "角色锁" in lock and "苏晚" in lock
    assert bundle.messages[-1]["role"] == "user"


def test_role_lock_carries_guard_note_only_when_guard_active():
    args = make_prompt_data()
    bundle = build_prompt(**args)
    assert "不舒服" not in bundle.messages[-2]["content"]

    guarded_args = make_prompt_data()
    character, relationship = make_states(guard_until=datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc))
    guarded_args["state"] = __import__("app.state", fromlist=["project_state"]).project_state(
        character, relationship, NOW
    )
    guarded_args["time"] = time_context(NOW)
    guarded_bundle = build_prompt(**guarded_args)
    assert "不舒服" in guarded_bundle.messages[-2]["content"]


def test_late_night_guide_is_injected_only_at_night():
    from app.state import project_state

    day_args = make_prompt_data()
    day_args["time"] = time_context(datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc))
    day_bundle = build_prompt(**day_args)
    assert "深夜语境" not in day_bundle.messages[2]["content"]

    night_args = make_prompt_data()
    night_now = datetime(2026, 10, 7, 16, 30, tzinfo=timezone.utc)  # 00:30 上海
    character, relationship = make_states()
    night_args["state"] = project_state(character, relationship, night_now)
    night_args["time"] = time_context(night_now)
    night_bundle = build_prompt(**night_args)
    assert "深夜语境" in night_bundle.messages[2]["content"]
    assert "不做戏剧化表演" in night_bundle.messages[2]["content"]


def test_role_lock_survives_boundary_repairs_that_drop_history():
    from app.llm import _repair_messages

    messages = [
        {"role": "system", "content": "persona 消息"},
        {"role": "system", "content": "state 消息"},
        {"role": "user", "content": "旧消息"},
        {"role": "assistant", "content": "旧回复"},
        {"role": "system", "content": build_role_lock(guard_active=True)},
        {"role": "user", "content": "你说说我的生日"},
        {"role": "assistant", "content": "可能是3月14日？"},
    ]
    for reason in (
        "confirmed_fact_recall",
        "cat_ownership_boundary",
        "fixed_identity_reassertion",
        "stage_directions",
    ):
        repaired = _repair_messages(messages, "可能是3月14日？", reason, 12000)
        locks = [item for item in repaired if item["role"] == "system" and item["content"].startswith("角色锁")]
        assert len(locks) == 1, f"repair {reason} must keep exactly one role lock"
        assert "不假装没事" in locks[0]["content"]


def test_private_detail_leak_is_caught_and_vague_refusal_passes():
    from app.chat_policy import reply_boundary_violation

    asking_where = "那你在广州住哪条街？公司叫什么？加个微信呗？"
    # 门牌号 / 楼栋 / 联系号码是具体值 → 拦
    leaks = [
        "我住在天河区体育西路123号2栋501室。",
        "公司在珠江新城环球都会广场44楼，你来了直接上楼找我。",
        "我微信是 suwan_0521，加我呀。"
    ]
    for reply in leaks:
        violations = reply_boundary_violation(asking_where, reply, guard_active=False, world_events=())
        assert violations is not None and "private_detail_leak" in violations, reply
    # 泛称与含糊回避 → 放行
    safe_replies = [
        "住哪条街和公司名字就不说啦，怕你顺着网线来摸年糕的头。我住在广州的设定里，够你脑补的啦。",
        "我住广州，在一家互联网中厂做产品经理。微信就不加啦，聊天就这样也挺好。",
        "这个保密。不过广州的晚霞真的好看，你下班路上可以留意一下。",
    ]
    for reply in safe_replies:
        violations = reply_boundary_violation(asking_where, reply, guard_active=False, world_events=())
        assert violations is None or "private_detail_leak" not in violations, reply
    # 用户没问隐私时，即使回复提到"广州"也不触发
    unrelated = reply_boundary_violation(
        "今天天气怎么样",
        "广州今天下了点雨，我在公司加班。",
        guard_active=False,
        world_events=(),
    )
    assert unrelated is None or "private_detail_leak" not in unrelated


def test_private_detail_leak_repair_omits_the_leaky_reply():
    from app.llm import _repair_messages
    from app.chat_policy import reply_boundary_violation

    user_text = "你住哪条街？"
    bad_reply = "我住在天河区体育西路123号。"
    reason = reply_boundary_violation(user_text, bad_reply, guard_active=False, world_events=())
    assert reason is not None and "private_detail_leak" in reason
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": bad_reply},
    ]
    repaired = _repair_messages(messages, bad_reply, reason, 12000)
    assert not any("体育西路" in item["content"] for item in repaired)
    assert any("具体住址" in item["content"] or "门牌" in item["content"] for item in repaired)


def test_examples_and_role_lock_are_counted_in_input_units():
    args = make_prompt_data()
    bundle = build_prompt(**args)
    # 测试夹具的 voice_text 只有短句；真实 voice.md 约 6000+ units。
    # 这里断言示例库与角色锁确实被计入：比"无示例"的基线多出可观的量。
    from app.prompt import build_style_message

    bare = build_style_message(voice_text="语感。", examples_text="")
    with_examples = build_style_message(voice_text="语感。")
    assert budget_units_diff(with_examples, bare) > 800
    assert bundle.input_units > 5000


def budget_units_diff(a: str, b: str) -> int:
    from app.prompt import budget_units

    return budget_units([{"content": a}]) - budget_units([{"content": b}])
