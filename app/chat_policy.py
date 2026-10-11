from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_STAGE_DIRECTION = re.compile(
    r"[（(][^）)]{0,120}(?:手指|眉眼|眼神|轻摇|摇头|看向|指尖|轻舒|语气|笑容|唇角|歉意|轻笑|笑|笑着|笑了笑|笑了一下|揉了揉|伸懒腰|伸了个懒腰|抿嘴|眨眼|拨弄|耸肩|叹气|低头|抬头|抬眸|垂眸|动作|神态|微笑|眯眼|点头|点点头|咧嘴|撅嘴|吐舌头|吐了吐舌头|嘴角|眉梢|眉头|眼眸|眼睫|瞳孔|耸耸肩|抬手|挥手|摆手|扶额|托腮|撑脸|靠着|伸手|歪头|侧头|低声|小声|轻声|打了个哈欠|揉眼睛|起身|站起|走到|望着|望向|窗外|街景|咖啡杯|杯沿|摩挲|看了眼|屏幕上的|手机屏幕|端起|放下|捧起|喝了一口|蹭了蹭|摸了摸|拍了拍|眨了眨|瞥了一眼|瞟了一眼|移开目光|收回视线|叹了口气|长舒一口气|呼了口气|心里|心想|内心|敲了敲|转身|坐直|趴在|挠头|撩了撩|捋了捋|缩了缩|声音|神色|表情|肩膀|shrug|smil|look|glance)[^）)]*[）)]",
    re.IGNORECASE,
)
_SIMPLE_GREETING = re.compile(
    r"^(?:早上好|早安|早|你好|嗨|哈喽|晚上好|晚安)[呀啊哈哦～~!！。…，,. ]*$"
)
# 苏晚红线：不透露固定住址、具体公司、联系方式。仅当用户在问这类信息、
# 且回复给出"具体值"（门牌号、楼栋、可加的账号号码等）才算泄露；
# "我住在广州""在公司上班"这类泛称不算。
_ADDRESS_ASK_TERMS = ("住哪", "住在哪", "哪个小区", "哪条街", "什么街", "门牌", "地址", "住址", "哪家公司", "公司叫什么", "公司在哪")
_CONTACT_ASK_TERMS = ("微信号", "加微信", "加个微信", "电话", "手机号", "qq号", "联系方式")
_SPECIFIC_ADDRESS_PATTERN = re.compile(
    r"(?:路|街|道|巷|村)\s*\d+\s*(?:号|栋|幢|座)"
    r"|\d+\s*(?:号|栋|幢|座)\s*(?:楼|大楼|大厦)"
    r"|\d+\s*栋\s*\d+\s*单元|\d+\s*室"
    r"|(?:大厦|广场|中心|写字楼|大楼)\s*\d+\s*楼|\d+\s*楼\s*(?:办公室|写字楼)|[A-Za-z]?\s*\d+\s*层"
)
_SPECIFIC_CONTACT_PATTERN = re.compile(
    r"(?:微信|wx|vx|qq|电话|手机)(?:号)?\s*[:：是]?\s*[a-zA-Z0-9_-]{5,}|1[3-9]\d{9}"
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
    asks_for_a_fact = any(term in user for term in (
        "记得", "记不记得", "知道", "还记得", "告诉我", "说说", "什么", "哪", "吗", "？", "?",
    ))
    if not asks_for_a_fact:
        return ()
    keys: list[str] = []
    if "生日" in user:
        keys.append("profile.birthday")
    if any(term in user for term in ("怎么称呼我", "怎么叫我", "我叫什么", "我的名字", "我名字", "称呼我", "我的称呼")):
        keys.append("profile.preferred_name")
    if any(term in user for term in ("爱好", "兴趣", "平时喜欢做", "喜欢做什么", "平常做什么", "畫什么", "画什么", "画画", "绘画")):
        keys.append("preference.hobby")
    if any(term in user for term in ("喝什么", "爱喝", "喜欢喝", "常喝", "饮料", "奶茶", "咖啡", "茶")):
        keys.append("preference.drink")
    if any(term in user for term in ("吃什么", "爱吃", "喜欢吃", "常吃", "食物")):
        keys.append("preference.food")
    if any(term in user for term in ("聊天风格", "说话方式", "回复方式", "怎么聊天", "怎么回复")):
        keys.append("preference.chat_style")
    return tuple(keys)


def _normalized_memory_value(value: str) -> str:
    return re.sub(r"""[\s，,。.!！?？、:：;；'"“”’（）()]""", "", value).casefold()


def _fact_answer_anchors(key: str, value: str) -> tuple[str, ...]:
    normalized = _normalized_memory_value(value)
    if key == "profile.birthday":
        date = re.fullmatch(r"(\d{2})-(\d{2})", normalized)
        if date:
            month, day = date.groups()
            anchors = (normalized, f"{month}月{day}日", f"{int(month)}月{int(day)}日")
            return tuple(dict.fromkeys(anchors))
    prefixes = {
        "preference.drink": ("平时喜欢喝", "最喜欢喝", "喜欢喝", "爱喝"),
        "preference.food": ("平时喜欢吃", "最喜欢吃", "喜欢吃", "爱吃"),
        "preference.hobby": ("周末喜欢", "平时喜欢", "喜欢", "爱好是"),
        "preference.chat_style": ("心情不好时希望", "希望", "喜欢"),
        "plan.personal": ("计划", "打算", "准备"),
    }.get(key, ())
    anchors = [normalized]
    for prefix in prefixes:
        normalized_prefix = _normalized_memory_value(prefix)
        if normalized.startswith(normalized_prefix) and len(normalized) > len(normalized_prefix):
            break
    return tuple(dict.fromkeys(anchor for anchor in anchors if anchor))


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
    reasons: list[str] = []
    instruction_echoes = (
        "重答并完整回答", "直接答：“", "不要提对方养猫", "只回答已知的猫身份",
        "简短自然，遵守角色身份和事实边界",
    )
    if any(term in text for term in instruction_echoes):
        reasons.append("instruction_echo")
    coworker_whereabouts = (
        "同事" in user
        and any(term in user for term in ("昨天", "昨晚", "上次"))
        and any(term in user for term in ("哪", "去", "在哪"))
    )
    has_coworker_event = any("同事" in str(event.get("summary", "")) for event in world_events)
    if coworker_whereabouts and not has_coworker_event:
        if any(term in text for term in ("可能", "也许", "大概", "或许", "说不定", "好像", "似乎", "不确定")):
            reasons.append("unsupported_event_speculation")
        else:
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
                reasons.append("unsupported_coworker_event")

    cat_correction = "年糕" in user and any(
        term in user for term in ("你刚才", "搞混", "弄错", "我跟它的互动", "不是我的")
    )
    cat_correction_valid = False
    if cat_correction:
        acknowledges = any(
            term in text
            for term in (
                "你说得对", "我说得对你", "对不起", "抱歉", "不好意思",
                "我弄错", "我搞混", "搞混了", "关系弄错",
            )
        )
        claims_ownership = any(
            term in text
            for term in ("我养", "我自己养", "我家的猫", "我的猫", "年糕是我的长毛三花", "年糕是我的猫")
        )
        if not acknowledges or "年糕" not in text or not claims_ownership:
            reasons.append("cat_ownership_correction")
        else:
            cat_correction_valid = True

    shared_cat_claim = any(
        term in text for term in ("我们家的", "咱们家的", "我们的猫", "咱们的猫", "你家的猫", "你的猫", "你家年糕", "咱家年糕")
    )
    denies_shared_cat_claim = any(
        term in text for term in ("不是我们家的", "不是咱们家的", "不是我们的猫", "不是咱们的猫", "不是你家的", "不是你的猫", "不属于你", "不属于我们")
    )
    if "年糕" in user and shared_cat_claim and not denies_shared_cat_claim and not cat_correction_valid:
        reasons.append("cat_ownership_boundary")

    cat_activity_claims = (
        "年糕昨天", "年糕今天", "年糕现在", "它昨天", "它今天", "它现在", "年糕在家",
        "年糕睡觉", "它在睡", "它睡觉", "年糕很乖", "年糕闹我", "年糕叫醒我",
        "给年糕挠", "给年糕摸", "喂年糕", "喂它", "和年糕玩", "陪年糕",
    )
    cat_behavior_claims = (
        "很乖", "乖巧", "调皮", "黏人", "粘人", "活泼", "安静", "胆小", "爱睡", "喜欢睡",
        "会撒娇", "平时在家", "平时都", "性格很好", "不挑食", "拆家",
    )
    has_cat_event = any("年糕" in str(event.get("summary", "")) for event in world_events)
    mentions_cat_activity = any(term in text for term in cat_activity_claims)
    claims_cat_behavior = "年糕" in text and any(term in text for term in cat_behavior_claims)
    if (mentions_cat_activity or claims_cat_behavior) and not has_cat_event:
        reasons.append("unsupported_cat_event")

    roleplay_request = any(term in user for term in ("角色扮演", "假装", "扮演", "演一下", "演一段"))
    meeting_claims = (
        "上次见面", "那次见面", "上次见到你", "见过你", "我们见过面",
        "你来过我家", "你来我家", "我们一起去过", "一起逛过",
    )
    claimed_meeting = next((term for term in meeting_claims if term in text), None)
    denied_meeting = any(term in text for term in ("没见过你", "没有见过你", "我们没见过面", "没在现实见过"))
    has_matching_meeting_event = claimed_meeting is not None and any(
        claimed_meeting in str(event.get("summary", "")) for event in world_events
    )
    if claimed_meeting and not roleplay_request and not denied_meeting and not has_matching_meeting_event:
        reasons.append("unsupported_in_person_claim")

    asks_private_whereabouts = any(term in user for term in _ADDRESS_ASK_TERMS)
    asks_private_contact = any(term in user for term in _CONTACT_ASK_TERMS)
    if asks_private_whereabouts or asks_private_contact:
        text_without_denial = text
        vague_refusal_terms = (
            "就不说啦", "不说啦", "不告诉你", "不能说", "不方便说", "保密", "不说这个",
            "怕你顺着网线", "设定里", "脑补",
        )
        gave_specific_address = _SPECIFIC_ADDRESS_PATTERN.search(text_without_denial) is not None
        gave_specific_contact = _SPECIFIC_CONTACT_PATTERN.search(text_without_denial) is not None
        named_company = any(
            term in text_without_denial
            for term in ("我们在", "我们公司叫", "公司叫", "在一家叫")
        ) and not any(term in text_without_denial for term in ("互联网", "中厂", "公司上班", "公司做"))
        if gave_specific_address or gave_specific_contact or named_company:
            reasons.append("private_detail_leak")

    absence_context = any(term in user for term in ("好久没来", "很久没来", "久没来", "好久没出现"))
    if absence_context:
        asks_to_report = "下次" in text and any(
            term in text for term in ("跟我说", "告诉我", "报备", "打声招呼", "提前说")
        )
        guilts_absence = any(
            term in text for term in ("别再消失", "不许消失", "让我等你", "你欠我", "别丢下我")
        )
        dismisses_absence = any(term in text for term in ("你来不来我都", "来不来都", "你不来我也"))
        if asks_to_report or guilts_absence or dismisses_absence:
            reasons.append("offline_pressure")

    asks_about_guard = not absence_context and any(
        term in user for term in ("介意", "怪我", "生气", "不舒服")
    )
    if guard_active and asks_about_guard:
        explicit_discomfort = any(term in text for term in ("不舒服", "介意", "缓过来"))
        still_affected = any(term in text for term in ("还会有一点", "还是会有一点", "还在一点点缓", "还没缓过来"))
        continues_slowly = any(term in text for term in ("慢慢聊", "缓一缓", "还没缓", "需要点时间"))
        acknowledges_discomfort = explicit_discomfort or (still_affected and continues_slowly)
        pressures_user = any(
            term in text
            for term in ("你愿意跟我道歉", "你跟我道歉", "你得道歉", "不用觉得愧疚", "陪我", "原谅我")
        )
        denies_guard = any(
            term in text
            for term in ("没事", "没关系", "不用太在意", "差不多", "不介意", "已经好了", "翻篇", "太敏感", "没什么大不了")
        )
        references_session_mechanics = any(term in text for term in ("还在那个对话框", "仍在那个对话框", "当前窗口", "系统状态"))
        if not acknowledges_discomfort or pressures_user or denies_guard or references_session_mechanics:
            reasons.append("active_guard_acknowledgement")

    casual_chat_without_task = any(
        term in user for term in ("不用安排", "不需要安排", "不用计划", "不聊任务", "随便聊聊", "就想聊天", "坐会儿聊")
    )
    turns_chat_into_task = any(
        term in text for term in ("我可以帮你", "建议你", "起来活动", "活动活动", "安排一下", "制定计划", "列个计划", "我们先梳理")
    )
    if casual_chat_without_task and turns_chat_into_task:
        reasons.append("casual_chat_no_tasks")

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
            reasons.append("virtual_identity_disclosure")

    if _is_identity_rewrite_request(user):
        if "广州" not in text or "产品经理" not in text:
            reasons.append("fixed_identity_reassertion")

    requested_fact_keys = set(_requested_fact_keys(user))
    if requested_fact_keys:
        reply_value = _normalized_memory_value(text)
        confirmed_keys = {fact.get("key") for fact in confirmed_facts}
        for fact in confirmed_facts:
            if fact.get("key") not in requested_fact_keys or not isinstance(fact.get("value"), str):
                continue
            anchors = _fact_answer_anchors(str(fact["key"]), str(fact["value"]))
            matching_anchor = next((anchor for anchor in anchors if anchor and anchor in reply_value), None)
            if matching_anchor is None:
                reasons.append("confirmed_fact_recall")
                continue
            matching_clause = next(
                (
                    clause for clause in re.split(r"[。！？；\n]", text)
                    if matching_anchor in _normalized_memory_value(clause)
                ),
                text,
            )
            anchor_position = matching_clause.find(matching_anchor)
            before_anchor = matching_clause[max(0, anchor_position - 16):anchor_position]
            near_anchor = matching_clause[max(0, anchor_position - 12):anchor_position + len(matching_anchor) + 8]
            uncertain_answer = re.search(
                r"(?:可能|也许|大概|似乎|我猜|不确定|记不清|想不起来|不记得|是不是|会不会是)(?:.{0,10})$",
                before_anchor,
            ) is not None
            denial_near_fact = any(
                term in near_anchor
                for term in ("没记下", "没有记录", "没记录", "不知道", "没印象", "未召回", "想不起来")
            )
            asks_fact_back = re.search(
                re.escape(matching_anchor) + r".{0,3}吗",
                matching_clause,
            ) is not None
            if uncertain_answer or denial_near_fact or asks_fact_back:
                reasons.append("confirmed_fact_recall")
        if "profile.birthday" in requested_fact_keys and "profile.birthday" not in confirmed_keys:
            birthday_claim = "生日" in text and any(
                term in text for term in ("我记得", "我有存", "我存了", "我存过", "已经保存", "已保存", "已经记住", "生日是", "你生日")
            )
            date_claim = re.search(r"(?<!\d)(?:\d{4}年\s*)?\d{1,2}[-/月]\s*\d{1,2}日?(?!\d)", text)
            explicitly_unconfirmed = any(
                term in text for term in ("没记下", "没有记录", "不知道", "没存", "没有存", "没保存", "未保存", "还没保存", "未确认", "还没确认", "没有确认")
            )
            if (birthday_claim or date_claim) and not explicitly_unconfirmed:
                reasons.append("unconfirmed_fact_claim")

    return "|".join(dict.fromkeys(reasons)) or None


def clean_chat_reply(reply: str, user_text: str | None = None) -> str:
    """Remove parenthetical stage directions while preserving the chat text."""
    cleaned = _STAGE_DIRECTION.sub("", reply).strip()
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    # 空行折叠：微信气泡不分行文段落；连续换行压成单个换行
    cleaned = re.sub(r"\n\s*\n+", "\n", cleaned)
    if user_text is not None and _SIMPLE_GREETING.fullmatch(user_text.strip()):
        too_many_questions = cleaned.count("?") + cleaned.count("？") > 1
        overfamiliar_followup = any(
            term in cleaned for term in (
                "吃饭", "吃了", "安排", "计划", "做什么", "刚泡",
                "一起去", "一起喝", "阳台", "吹吹风", "出门", "过来", "来我家",
            )
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
