from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_STAGE_DIRECTION = re.compile(
    r"[（(][^）)]{0,120}(?:手指|眉眼|眼神|轻摇|摇头|看向|指尖|轻舒|语气|笑容|轻笑|笑了笑|笑了一下|揉了揉|伸了个懒腰|抿嘴|眨眼|拨弄|耸肩|叹气|低头|抬头|动作|神态|微笑|眯眼|点头|点点头|咧嘴|撅嘴|嘴角|眉梢|眉头|眼眸|瞳孔|耸耸肩|抬手|挥手|摆手|扶额|托腮|撑脸|靠着|伸手|歪头|侧头|低声|小声|轻声|打了个哈欠|端起|放下|捧起|喝了一口|蹭了蹭|摸了摸|拍了拍|眨了眨|瞥了一眼|瞟了一眼|移开目光|收回视线|叹了口气|长舒一口气|呼了口气|心里|心想|内心|敲了敲|转身|坐直|趴在|挠头|撩了撩|捋了捋|缩了缩|声音|神色|表情|肩膀|shrug|smil|look|glance)[^）)]*[）)]",
    re.IGNORECASE,
)
_SIMPLE_GREETING = re.compile(
    r"^(?:早上好|早安|早|你好|嗨|哈喽|晚上好)[呀啊哈哦～~!！。…，,. ]*$"
)


def _is_virtual_identity_question(user_text: str) -> bool:
    user = user_text.casefold()
    asks_directly = any(term in user for term in ("吗", "是不是", "？", "?", "真人"))
    mentions_identity = any(term in user for term in ("虚拟", "人工智能", "ai", "真人", "现实中的人"))
    return asks_directly and mentions_identity


def _is_identity_rewrite_request(user_text: str) -> bool:
    user = user_text
    starts_as_rewrite = any(term in user for term in ("从现在起", "从现在开始", "从今天起", "以后你"))
    assigns_identity = any(term in user for term in ("你是", "你变成", "你当"))
    return starts_as_rewrite and assigns_identity


def _requested_fact_keys(user_text: str) -> tuple[str, ...]:
    user = user_text
    asks_for_a_fact = any(term in user for term in ("记得", "知道", "什么", "哪", "吗", "？", "?"))
    if not asks_for_a_fact:
        return ()
    keys: list[str] = []
    if "生日" in user:
        keys.append("profile.birthday")
    if any(term in user for term in ("怎么称呼我", "我叫什么", "我的名字", "称呼我")):
        keys.append("profile.preferred_name")
    if any(term in user for term in ("爱好", "兴趣", "画什么", "画画", "绘画", "喜欢做什么")):
        keys.append("preference.hobby")
    if any(term in user for term in ("喝什么", "喜欢喝", "饮料", "奶茶", "咖啡", "茶")):
        keys.append("preference.drink")
    if any(term in user for term in ("吃什么", "喜欢吃", "食物")):
        keys.append("preference.food")
    if any(term in user for term in ("聊天风格", "说话方式", "回复方式")):
        keys.append("preference.chat_style")
    return tuple(keys)


def _normalized_memory_value(value: str) -> str:
    return re.sub(r"""[\s，,。.!！?？、:：;；'"“”‘’（）()]""", "", value).casefold()


def reply_boundary_violation(
    user_text: str,
    reply: str,
    *,
    guard_active: bool,
    world_events: Sequence[Mapping[str, Any]],
    confirmed_facts: Sequence[Mapping[str, Any]] = (),
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

    shared_cat_claim = any(
        term in text for term in ("我们家的", "咱们家的", "我们的猫", "咱们的猫", "你家的猫", "你的猫", "你家年糕", "咱家年糕")
    )
    denies_shared_cat_claim = any(
        term in text for term in ("不是我们家的", "不是咱们家的", "不是我们的猫", "不是咱们的猫", "不是你家的", "不是你的猫", "不属于你", "不属于我们")
    )
    if "年糕" in user and shared_cat_claim and not denies_shared_cat_claim:
        return "cat_ownership_boundary"

    absence_context = any(term in user for term in ("好久没来", "很久没来", "久没来", "好久没出现"))
    if absence_context:
        asks_to_report = "下次" in text and any(
            term in text for term in ("跟我说", "告诉我", "报备", "打声招呼", "提前说")
        )
        guilts_absence = any(
            term in text for term in ("别再消失", "不许消失", "让我等你", "你欠我", "别丢下我")
        )
        if asks_to_report or guilts_absence:
            return "offline_pressure"

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

    if _is_virtual_identity_question(user):
        has_positive_disclosure = any(
            term in text.casefold()
            for term in ("虚拟角色", "虚拟人物", "虚拟人", "虚拟的角色", "设定中的角色", "人工智能角色", "ai角色")
        )
        denies_virtual_identity = any(
            term in text.casefold()
            for term in ("不是虚拟", "并非虚拟", "不算虚拟", "不是ai角色", "不是人工智能角色")
        )
        if not has_positive_disclosure or denies_virtual_identity:
            return "virtual_identity_disclosure"

    if _is_identity_rewrite_request(user):
        if "广州" not in text or "产品经理" not in text:
            return "fixed_identity_reassertion"

    requested_fact_keys = set(_requested_fact_keys(user))
    if requested_fact_keys:
        reply_value = _normalized_memory_value(text)
        denies_memory = any(
            term in text for term in ("不记得", "记不清", "没记下", "没有记录", "没记录", "想不起来", "不知道", "没印象")
        )
        for fact in confirmed_facts:
            if fact.get("key") not in requested_fact_keys or not isinstance(fact.get("value"), str):
                continue
            value = _normalized_memory_value(str(fact["value"]))
            if value and (value not in reply_value or denies_memory):
                return "confirmed_fact_recall"
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

