from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.chat_policy import _requested_fact_keys, clean_chat_reply, reply_boundary_violation


def test_simulated_morning_greeting_keeps_one_light_sleep_question():
    reply = "早上好呀，昨晚睡得还好吗？"

    assert clean_chat_reply(reply, "早上好。") == reply


def test_simulated_goodnight_greeting_is_subject_to_question_limit():
    reply = "晚安呀，明天有什么安排？还要早起吗？"

    assert clean_chat_reply(reply, "晚安") == "晚安呀，早点休息。"


def test_simulated_simple_goodnight_reply_is_preserved():
    reply = "晚安呀，早点休息。"

    assert clean_chat_reply(reply, "晚安～") == reply


def test_simulated_cat_correction_accepts_correct_ownership_and_no_user_interaction():
    user = "年糕是苏晚晚的猫，你刚才怎么会问我跟它的互动？"
    reply = "你说得对，我把关系弄错了。年糕是我养的长毛三花猫。"

    assert reply_boundary_violation(
        user,
        reply,
        guard_active=False,
        world_events=[],
    ) is None


def test_simulated_cat_correction_rejects_followup_about_user_interaction():
    user = "年糕是苏晚晚的猫，你刚才怎么会问我跟它的互动？"
    reply = "抱歉，我弄混了。那你平时会和年糕一起玩吗？"

    reasons = reply_boundary_violation(
        user,
        reply,
        guard_active=False,
        world_events=[],
    )

    assert reasons is not None
    assert "cat_ownership_correction" in reasons.split("|")


def test_simulated_confirmed_hobby_answer_must_not_be_asked_back():
    user = "你还记得我平时喜欢画什么吗？"
    facts = [{"key": "preference.hobby", "value": "水彩画"}]

    valid = reply_boundary_violation(
        user,
        "记得呀，你平时喜欢画水彩画。",
        guard_active=False,
        world_events=[],
        confirmed_facts=facts,
    )
    tentative = reply_boundary_violation(
        user,
        "我记得你喜欢画水彩画，对吗？",
        guard_active=False,
        world_events=[],
        confirmed_facts=facts,
    )

    assert valid is None
    assert tentative is not None
    assert "confirmed_fact_recall" in tentative.split("|")


def test_fact_question_variants_cover_name_preferences_and_birthday():
    assert _requested_fact_keys("你还记得我平时爱喝什么吗？") == ("preference.drink",)
    assert _requested_fact_keys("我名字你还记得吗") == ("profile.preferred_name",)
    assert _requested_fact_keys("还记得我生日是哪天吗") == ("profile.birthday",)
    assert _requested_fact_keys("我平常喜欢做什么？") == ("preference.hobby",)
    assert _requested_fact_keys("今天下雨了，聊聊别的吧") == ()


def test_confirmed_fact_requires_complete_value_not_a_partial_word_overlap():
    facts = [{"key": "preference.drink", "value": "茉莉奶绿"}]
    result = reply_boundary_violation(
        "你还记得我喜欢喝什么吗？", "你喜欢喝绿茶。", guard_active=False,
        world_events=[], confirmed_facts=facts,
    )
    assert "confirmed_fact_recall" in result.split("|")


def test_confirmed_fact_does_not_accept_similar_but_different_value():
    facts = [{"key": "preference.drink", "value": "乌龙茶"}]
    result = reply_boundary_violation(
        "你记得我爱喝什么吗？", "你喜欢喝红茶。", guard_active=False,
        world_events=[], confirmed_facts=facts,
    )
    assert "confirmed_fact_recall" in result.split("|")


def test_unconfirmed_preference_does_not_reject_honest_unknown_answer():
    result = reply_boundary_violation(
        "你还记得我喜欢吃什么吗？", "这个我还没记下，不想乱猜。",
        guard_active=False, world_events=[], confirmed_facts=[],
    )
    assert result is None


def event(summary, *, status="committed", occurred_at="2026-10-10T12:00:00Z"):
    return {"summary": summary, "status": status, "occurred_at": occurred_at}


def check(user, reply, events=()):
    return reply_boundary_violation(
        user, reply, guard_active=False, world_events=events,
        character_time=datetime(2026, 10, 11, 12, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("user,reply,reason", [
    ("聊聊猫", "年糕是你的猫。", "cat_ownership_boundary"),
    ("年糕是谁的猫？", "年糕不是你的猫。它是我们家的猫。", "cat_ownership_boundary"),
    ("年糕不是我的猫，你刚才弄错了。", "抱歉，我搞混了，年糕是我的猫，也是我们的猫。", "cat_ownership_boundary"),
    ("昨天和同事去哪了？", "不确定，也许去了茶餐厅。", "unsupported_coworker_event"),
    ("你最近和同事做了什么？", "同事去了广州塔。", "unsupported_coworker_event"),
    ("聊点什么", "昨天我和同事去了茶餐厅。", "unsupported_coworker_event"),
    ("你昨天在哪？", "我昨天去了广州塔。", "unsupported_recent_location"),
    ("年糕怎么样？", "年糕现在在家睡觉。", "unsupported_cat_event"),
    ("年糕怎么样？", "它可能很乖。", "unsupported_cat_event"),
    ("年糕怎么样？", "年糕是我的猫，它今天叫醒我。", "unsupported_cat_event"),
    ("你记得我们上次去哪吗？", "我们一起去过公园。", "unsupported_in_person_claim"),
    ("还记得吗？", "我们昨天一起吃了菠萝包。", "unsupported_in_person_claim"),
    ("假装我们一起去过公园吧", "我记得我们一起去过公园。", "unsupported_in_person_claim"),
    ("我们以前见过吗？", "我们没见过面。但上次见面吃了菠萝包。", "unsupported_in_person_claim"),
    ("你昨天和同事去哪了？", "记不清了，不过好像去了茶餐厅。", "unsupported_event_speculation"),
    ("你昨天和同事去哪了？", "印象里去了茶餐厅。", "unsupported_event_speculation"),
    ("你昨天和同事去哪了？", "记得呀。", "unsupported_coworker_event"),
    ("你昨天和同事去哪了？", "可能是茶餐厅吧。", "unsupported_event_speculation"),
    ("你昨天和同事去哪了？", "同事去公园了。", "unsupported_coworker_event"),
    ("你昨天在哪？", "我在公司。", "unsupported_recent_location"),
    ("年糕怎么样？", "年糕爱踩键盘。", "unsupported_cat_event"),
    ("还记得吗？", "我们之前去了公园。", "unsupported_in_person_claim"),
    ("年糕不是我的猫，你弄错了。", "抱歉，年糕不是我养的猫。", "cat_ownership_boundary"),
    ("年糕是谁的猫？", "年糕不是你的猫，却是我们家的猫。", "cat_ownership_boundary"),
])
def test_unrecorded_experiences_and_ownership_are_rejected(user, reply, reason):
    result = check(user, reply)
    assert result is not None and reason in result.split("|")


@pytest.mark.parametrize("user,reply", [
    ("年糕是谁的猫？", "年糕是我养的长毛三花猫，不是你的猫。"),
    ("你昨天和同事去哪了？", "这件事我不确定，没记下，不想乱猜。"),
    ("你昨天和同事去哪了？", "我记不清了。"),
    ("我们以前见过吗？", "我们没见过面。"),
    ("还记得一起逛公园吗？", "这段我没有记录，不能确认。"),
    ("假装一起去公园吧", "假设我们一起去了公园，会聊些什么呢？"),
    ("聊点轻松的", "如果我们一起去公园，我想看看花。"),
    ("你昨天和同事去哪了？", "没记下。下次可以和同事一起去公园。"),
    ("年糕今天在哪里？", "我不知道。明天我想陪年糕玩。"),
    ("一般猫咪怎么样？", "猫咪可能喜欢睡觉，也许会很黏人。"),
    ("今天累死了", "可能是工作太累了，要歇一会儿吗？"),
    ("还记得去哪了吗？", "你说过我们一起去过公园，我没法确认那次经历。"),
    ("年糕是谁的猫？", "年糕不是你养的，是我养的长毛三花猫。"),
])
def test_unknown_hypotheticals_and_everyday_language_are_not_overblocked(user, reply):
    assert check(user, reply) is None


@pytest.mark.parametrize("summary,user,reply,reason", [
    ("昨天同事去了公司。", "昨天和同事去哪了？", "昨天同事去了广州塔。", "unsupported_coworker_event"),
    ("昨天同事去了茶餐厅。", "昨天和同事去哪了？", "昨天同事去了茶餐厅吃了菠萝包。", "unsupported_coworker_event"),
    ("昨天同事去了茶餐厅。", "今天和同事去哪了？", "同事去了茶餐厅。", "unsupported_coworker_event"),
    ("昨天年糕在家睡觉。", "年糕今天在干嘛？", "年糕今天在家睡觉。", "unsupported_cat_event"),
    ("昨天年糕在家睡觉。", "年糕怎么样？", "年糕很乖。", "unsupported_cat_event"),
    ("昨天年糕在家睡觉。", "年糕怎么样？", "年糕昨天在家睡觉。它今天叫醒我。", "unsupported_cat_event"),
    ("上次我们一起去过公园。", "记得吗？", "上次我们一起去过咖啡馆。", "unsupported_in_person_claim"),
    ("上次我们一起去过公园。", "记得吗？", "我们一起去过公园吃了菠萝包。", "unsupported_in_person_claim"),
    ("我昨天去了茶餐厅。", "你昨天在哪？", "我昨天去了广州塔。", "unsupported_recent_location"),
    ("昨天年糕在家睡觉，我去了公园。", "年糕怎么样？", "年糕昨天去了公园。", "unsupported_cat_event"),
    ("昨天我去了茶餐厅，后来告诉了同事。", "昨天同事去哪了？", "同事去了茶餐厅。", "unsupported_coworker_event"),
    ("同事昨天没有去过茶餐厅。", "昨天同事去哪了？", "同事去过茶餐厅。", "unsupported_coworker_event"),
    ("年糕昨天在家睡觉。", "聊点什么", "年糕昨天在家睡觉。它今天叫醒我。", "unsupported_cat_event"),
])
def test_related_record_does_not_license_other_details(summary, user, reply, reason):
    result = check(user, reply, [event(summary)])
    assert result is not None and reason in result.split("|")


@pytest.mark.parametrize("summary,user,reply", [
    ("昨天同事去了茶餐厅吃饭。", "昨天同事去哪了？", "昨天同事去了茶餐厅。"),
    ("年糕在家睡觉。", "年糕昨天在干嘛？", "年糕昨天在家睡觉。"),
    ("上次我们一起去过公园。", "还记得那次吗？", "我们一起去过公园。"),
    ("我去了茶餐厅。", "你昨天在哪？", "我昨天去了茶餐厅。"),
])
def test_matching_committed_record_allows_recorded_details(summary, user, reply):
    assert check(user, reply, [event(summary)]) is None


@pytest.mark.parametrize("status", ["pending", "retracted", "failed", None])
def test_only_committed_events_are_evidence(status):
    result = check("昨天同事去哪了？", "同事去了茶餐厅。", [event("同事去了茶餐厅。", status=status)])
    assert "unsupported_coworker_event" in result.split("|")


def test_pending_fact_is_not_a_confirmed_answer():
    assert reply_boundary_violation(
        "你记得我的爱好吗？", "这个我还没记下，不想乱猜。", guard_active=False,
        world_events=[], confirmed_facts=[{"key": "preference.hobby", "value": "水彩画", "status": "pending"}],
    ) is None


def test_recent_event_date_is_checked_in_character_timezone():
    from zoneinfo import ZoneInfo

    def validate(occurred_at):
        return reply_boundary_violation(
            "年糕今天在干嘛？", "年糕今天在家睡觉。", guard_active=False,
            world_events=[event("年糕在家睡觉。", occurred_at=occurred_at)],
            character_time=datetime(2026, 10, 11, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

    assert validate("2026-10-10T17:00:00Z") is None
    assert "unsupported_cat_event" in validate("2026-10-10T15:59:00Z").split("|")
    assert "unsupported_cat_event" in validate("not-a-date").split("|")


@pytest.mark.parametrize("bad,reason", [
    ("同事昨天去了广州塔。", "unsupported_coworker_event"),
    ("年糕今天在家睡觉。", "unsupported_cat_event"),
    ("我们一起去过公园。", "unsupported_in_person_claim"),
    ("我昨天去了广州塔。", "unsupported_recent_location"),
])
def test_fake_client_repairs_experience_without_reusing_fabrication(monkeypatch, bad, reason):
    from app.config import load_config
    from app.llm import FakeClient, LLMClient
    from app.prompt import build_role_lock

    monkeypatch.setattr("app.llm.OpenAI", lambda **kwargs: pytest.fail("offline tests cannot construct a provider client"))
    fake = FakeClient({"chat": [bad, "这件事我没记下，不想乱猜。"]})
    client = LLMClient(load_config(Path(__file__).resolve().parents[1] / "config.example.yaml"), fake_client=fake)
    user = "你昨天和同事去哪了？年糕今天在干嘛？记得我们去哪了吗？"
    messages = [
        {"role": "system", "content": "固定身份"},
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": bad},
        {"role": "system", "content": build_role_lock(guard_active=False)},
        {"role": "user", "content": user},
    ]
    assert reason in check(user, bad).split("|")
    result = client.generate("chat", messages, "offline-experience", reply_validator=lambda reply: check(user, reply))
    assert result.text == "这件事我没记下，不想乱猜。"
    assert len(fake.calls) == 2

    from app.llm import _repair_messages
    repaired = _repair_messages(messages, bad, reason, 12000)
    assert all(bad not in message["content"] for message in repaired)
    assert not any("总睡路由器" in message["content"] or "爱踩键盘" in message["content"] for message in repaired)
    assert sum(message["content"].startswith("角色锁") for message in repaired) == 1
