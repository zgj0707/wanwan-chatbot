from app.chat_policy import clean_chat_reply, reply_boundary_violation


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
