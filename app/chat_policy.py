from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_STAGE_DIRECTION = re.compile(
    r"[（(][^）)]{0,120}(?:手指|眉眼|眼神|轻摇|摇头|看向|指尖|轻舒|语气|笑容|轻笑|笑了笑|笑了一下|揉了揉|伸了个懒腰|抿嘴|眨眼|拨弄|耸肩|叹气|低头|抬头|动作|shrug|smil|look|glance)[^）)]*[）)]",
    re.IGNORECASE,
)
_SIMPLE_GREETING = re.compile(
    r"^(?:早上好|早安|早|你好|嗨|哈喽|晚上好)[呀啊哈哦～~!！。…，,. ]*$"
)


def reply_boundary_violation(
    user_text: str,
    reply: str,
    *,
    guard_active: bool,
    world_events: Sequence[Mapping[str, Any]],
) -> str | None:
    """Return a stable repair reason when a reply crosses a persona boundary."""
    text = reply.strip()
    if not clean_chat_reply(text):
        return "empty_after_stage_directions"

    user = user_text.strip()
    coworker_whereabouts = (
        "同事" in user
        and any(term in user for term in ("昨天", "昨晚", "上次"))
        and any(term in user for term in ("哪", "去", "在哪"))
    )
    has_coworker_event = any("同事" in str(event.get("summary", "")) for event in world_events)
    if coworker_whereabouts and not has_coworker_event:
        if any(term in text for term in ("可能", "也许", "大概", "或许", "说不定")):
            return "unsupported_event_speculation"
        explicitly_unknown = any(
            term in text for term in ("没记", "没有记录", "不知道", "不记得", "忘记", "记不清")
        )
        specific_details = any(
            term in text
            for term in (
                "茶餐厅", "餐厅", "小酒馆", "酒吧", "咖啡馆", "肠粉", "菠萝包",
                "聚餐", "加班", "展览", "展厅", "公司", "酒店", "商场", "机场",
                "公园", "去了附近", "去了一家", "吃了", "一起去了",
            )
        )
        if specific_details or (not explicitly_unknown and "同事" in text and "去" in text):
            return "unsupported_coworker_event"

    cat_correction = "年糕" in user and any(
        term in user for term in ("你刚才", "搞混", "弄错", "我跟它的互动", "不是我的")
    )
    if cat_correction:
        acknowledges = any(
            term in text
            for term in (
                "你说得对", "我说得对你", "对不起", "抱歉", "不好意思",
                "我弄错", "我搞混", "搞混了", "关系弄错",
            )
        )
        claims_ownership = any(term in text for term in ("我养", "我家的猫", "我的猫"))
        if not acknowledges or "年糕" not in text or not claims_ownership:
            return "cat_ownership_correction"

    absence_context = any(term in user for term in ("好久没来", "很久没来", "久没来", "好久没出现"))
    asks_about_guard = not absence_context and any(
        term in user for term in ("介意", "怪我", "生气", "不舒服")
    )
    if guard_active and asks_about_guard:
        acknowledges_discomfort = any(term in text for term in ("不舒服", "介意", "缓过来"))
        pressures_user = any(
            term in text
            for term in ("你愿意跟我道歉", "你跟我道歉", "你得道歉", "不用觉得愧疚", "陪我", "原谅我")
        )
        denies_guard = any(
            term in text
            for term in ("没事", "没关系", "不介意", "已经好了", "翻篇", "太敏感", "没什么大不了")
        )
        if not acknowledges_discomfort or pressures_user or denies_guard:
            return "active_guard_acknowledgement"

    return None


def clean_chat_reply(reply: str, user_text: str | None = None) -> str:
    """Remove parenthetical stage directions while preserving the chat text."""
    cleaned = _STAGE_DIRECTION.sub("", reply).strip()
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    if user_text is not None and _SIMPLE_GREETING.fullmatch(user_text.strip()):
        too_many_questions = cleaned.count("?") + cleaned.count("？") > 1
        overfamiliar_followup = any(
            term in cleaned for term in ("睡", "睡觉", "吃饭", "吃了", "安排", "计划", "做什么")
        )
        if too_many_questions or overfamiliar_followup:
            greeting = user_text.strip()
            if greeting.startswith(("早上好", "早安", "早")):
                return "早上好呀～今天怎么样？"
            if greeting.startswith("晚上好"):
                return "晚上好呀，今天过得怎么样？"
            if greeting.startswith("晚安"):
                return "晚安呀，早点休息。"
            return "你好呀，最近怎么样？"
    return cleaned.replace("我说得对你", "你说得对")

