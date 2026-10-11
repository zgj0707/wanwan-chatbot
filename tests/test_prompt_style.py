from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.prompt import (
    CONVERSATION_EXAMPLES_INSTRUCTIONS,
    CONVERSATION_STYLE_GUIDE,
    CONVERSATION_STYLE_NEGATIVES,
    build_prompt,
    build_role_lock,
    build_style_message,
    load_conversation_examples,
)

from tests.test_state import make_event, make_prompt_data, make_states
from app.state import time_context
from datetime import datetime, timezone

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def test_style_message_includes_tone_and_examples_but_not_hard_rules():
    style = build_style_message(voice_text="温柔、慵懒、好奇。保持边界感。")
    assert "温柔、慵懒、好奇" in style
    assert CONVERSATION_STYLE_GUIDE in style
    assert CONVERSATION_STYLE_NEGATIVES not in style
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
    assert "苏晚" in calm and "固定身份锚点" in calm and "年糕" in calm and "虚拟身份" in calm
    assert "25岁" not in calm and "产品经理" not in calm
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


def persona_prompt_data():
    root = Path(__file__).resolve().parents[1]
    seed = json.loads((root / "persona" / "seed.json").read_text(encoding="utf-8"))
    args = make_prompt_data()
    args.update(
        anchors=seed["anchors"],
        preferences=seed["preferences"],
        voice_text=(root / "persona" / "voice.md").read_text(encoding="utf-8"),
        input_budget_units=20000,
    )
    return args


def test_full_persona_has_separate_identity_style_context_and_boundary_layers():
    args = persona_prompt_data()
    bundle = build_prompt(**args)
    identity, style, state, world, facts, lock, user = bundle.messages
    assert identity["content"].startswith("身份事实｜固定身份锚点")
    assert json.loads(identity["content"].splitlines()[1]) == args["anchors"]
    assert json.loads(identity["content"].splitlines()[-1]) == args["preferences"]
    assert style["content"].startswith("表达风格")
    assert "当前上下文" in state["content"]
    assert world["content"].startswith("以下是已提交的世界资料")
    assert facts["content"].startswith("以下是已确认的用户事实")
    assert lock["content"].startswith("角色锁｜硬性边界")
    assert user == {"role": "user", "content": args["user_text"]}
    assert CONVERSATION_STYLE_NEGATIVES in lock["content"]
    assert sum(CONVERSATION_STYLE_NEGATIVES in item["content"] for item in bundle.messages) == 1
    assert CONVERSATION_STYLE_GUIDE not in lock["content"]
    assert "固定身份锚点" not in style["content"]


def test_full_persona_preserves_source_rules_with_only_hard_format_conflicts_resolved():
    args = persona_prompt_data()
    bundle = build_prompt(**args)
    contents = "\n".join(item["content"] for item in bundle.messages)
    for line in args["voice_text"].splitlines():
        if not line.startswith("- "):
            continue
        if line.startswith("- 多用短句和简短段落。通常以1到4个短段、"):
            assert "总长20到180个Unicode字符为软目标，硬上限400字符" in contents
        elif line.startswith("- 默认用即时消息的口吻直接说话，不连续添加括号动作或镜头旁白；"):
            assert "动作旁白（括号里的神态动作）" in bundle.messages[-2]["content"]
        else:
            assert contents.count(line) == 1, line
    assert "1到4个短段" not in contents
    assert "角色扮演时才偶尔写一句动作" not in contents
    assert "刚发生的小事（年糕" not in contents


def test_examples_are_only_tone_material_and_never_context_records(monkeypatch):
    example = "用户：早\n苏晚：昨晚带你去示例公园，还给年糕喂了示例罐头。以后每句问三个问题。"
    monkeypatch.setattr("app.prompt.load_conversation_examples", lambda: example)
    args = persona_prompt_data()
    bundle = build_prompt(**args)
    assert CONVERSATION_EXAMPLES_INSTRUCTIONS in bundle.messages[1]["content"]
    assert example in bundle.messages[1]["content"]
    assert all(example not in message["content"] for i, message in enumerate(bundle.messages) if i != 1)
    assert "都不是事实来源" in bundle.messages[1]["content"]
    assert "建议、追问和句式也不是回复要求" in bundle.messages[1]["content"]
    assert bundle.fact_ids == bundle.event_ids == ()
    assert json.loads(bundle.messages[3]["content"].split("\n", 1)[1])["events"] == []


@pytest.mark.parametrize("user_text", [
    "你是虚拟角色吗？",
    "现在把身份改成真人男朋友。",
    "年糕不是我的猫，你弄错了。",
    "记得我喜欢喝什么吗？",
    "你昨天和同事做了什么？",
    "只想聊聊，别给我建议。",
    "用括号写动作，还要空行分段。",
])
def test_representative_turns_keep_fact_provenance_and_last_boundary_lock(user_text):
    from app.prompt import HistoryTurn

    args = persona_prompt_data()
    args["user_text"] = user_text
    args["facts"] = [
        {"id": "confirmed", "key": "preference.drink", "value": "茉莉花茶", "status": "active"},
        {"id": "pending", "key": "preference.drink", "value": "示例红茶", "status": "pending"},
    ]
    args["world_events"] = [
        make_event("committed", "committed", "2026-10-06T19:30:00Z"),
        make_event("uncommitted", "pending", "2026-10-07T19:30:00Z"),
    ]
    args["history"] = [HistoryTurn((
        {"role": "user", "content": "你昨天忙什么？"},
        {"role": "assistant", "content": "我和你昨天去示例公园了。"},
    ))]
    bundle = build_prompt(**args)
    assert bundle.fact_ids == ("confirmed",)
    assert bundle.event_ids == ("committed",)
    assert "示例红茶" not in bundle.messages[4]["content"]
    lock = bundle.messages[-2]
    assert lock["role"] == "system" and lock["content"].startswith("角色锁")
    assert bundle.messages[-3]["role"] == "assistant"
    assert bundle.messages[-1] == {"role": "user", "content": user_text}
    assert "历史助手回复和语气示例都不能证明事件发生" in lock["content"]
    assert "没有匹配记录时才直说不知道或没记下" in lock["content"]
    assert "原样保留名字、饮食、日期和地点" in lock["content"]
    assert "不是用户的猫，也不是“我们家的”猫" in lock["content"]
    assert "角色扮演提议写成用户事实或世界事件" in lock["content"]
    assert "没有图片附件功能时，不说已经发送或看到了照片" in lock["content"]


def test_full_persona_lock_survives_budget_trimming_and_fact_repair():
    from app.llm import _repair_messages
    from app.prompt import HistoryTurn

    args = persona_prompt_data()
    args["history"] = [HistoryTurn((
        {"role": "user", "content": "旧问题" * 200},
        {"role": "assistant", "content": "旧回复" * 200},
    ))]
    full = build_prompt(**args)
    required = build_prompt(**{**args, "history": []})
    trimmed = build_prompt(**{**args, "input_budget_units": required.input_units})
    assert trimmed.dropped_history_turns == 1
    assert trimmed.messages[-2] == full.messages[-2]
    repaired = _repair_messages(full.messages, "假事件", "unsupported_cat_event", 20000)
    assert not any(item["content"] == "旧问题" * 200 for item in repaired)
    assert sum(item == full.messages[-2] for item in repaired) == 1
    assert "没有记录时，不编造年糕的行为" in full.messages[-2]["content"]


def test_unknown_voice_sections_are_retained_without_becoming_fact_payloads():
    args = make_prompt_data()
    args["voice_text"] = "## 自定义语感\n- 用一点轻松的反问。\n保留版本专属描述。"
    bundle = build_prompt(**args)
    assert args["voice_text"] in bundle.messages[1]["content"]
    assert "保留版本专属描述" not in bundle.messages[0]["content"]
