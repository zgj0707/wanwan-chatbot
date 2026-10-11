from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from app.state import parse_db_datetime


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


_EVENT_TIME = re.compile(r"前天|昨天|昨晚|今天|今晚|刚才|现在|上次|那次|最近|\d{1,2}月\d{1,2}日")
_EVENT_ACTION = re.compile(
    r"去过|去了|去到|去(?=[^，。！？；\n]{1,16}(?:了|馆|厅|园|场|店|公司|广州塔))|"
    r"在家|在睡|在(?:[^，。！？；\n]{1,16})(?:睡|吃|玩)|"
    r"在(?=[^，。！？；\n]{0,16}(?:馆|厅|园|场|店|公司|家|广州塔))|"
    r"(?:是)?(?:茶餐厅|咖啡馆|广州塔|公园|公司|机场|酒店|小酒馆|酒吧)|"
    r"来过|来我家|吃过|吃了|喝过|喝了|逛过|逛了|见过|见面|见到|聚餐|加班|出差|"
    r"睡觉|睡了|睡在|睡路由器|躺在|趴在|踩键盘|跳上|叫醒|闹我|挠|摸|喂|陪|玩过|玩了|一起(?:去|逛|吃|喝|玩)|"
    r"很乖|乖巧|调皮|黏人|粘人|活泼|安静|胆小|爱睡|喜欢睡|会撒娇|不挑食|拆家"
)
_UNKNOWN = re.compile(r"没记|没有记录|不知道|不记得|忘记|记不清|想不起来|没印象|不确定")
_TENTATIVE = re.compile(r"可能|也许|大概|或许|说不定|好像|似乎|印象里|隐约记得")
_SHARED = re.compile(r"我们(?!公司|部门|团队|同事)|咱们|和你|跟你|你和我|你来(?:过)?我家|见过你|见到你|带你|上次见面|那次见面|一起去过|一起逛过")


def _non_event_clause(clause: str) -> bool:
    """Only local denials/explicit hypotheticals bypass past-event checks."""
    if re.match(r"^(?:如果|要是|假如|假设|想象|比如|角色扮演[：:]|假装[：:])", clause):
        return True
    if re.match(r"^(?:你(?:刚才|之前)?(?:说过|提过)|怕你|希望|我想(?:明天|下次|以后|陪|喂|去))", clause):
        return True
    if re.search(r"(?:明天|以后|下次|将来).{0,12}(?:想|可以|打算|准备|一起|去|带)", clause):
        return True
    return re.search(
        r"(?:没|没有|未曾|不曾|没法确认|不能确认|不确定是否).{0,5}"
        r"(?:见过|见面|见到|去过|去了|一起|喂|睡|吃|逛|出差)", clause,
    ) is not None and not re.search(r"但是|不过|其实|却|但", clause)


def _event_time_matches(event: Mapping[str, Any], time: str, character_time: datetime | None) -> bool:
    if character_time is not None and time in {"前天", "昨天", "昨晚", "今天", "今晚"}:
        try:
            occurred = parse_db_datetime(str(event.get("occurred_at", ""))).astimezone(character_time.tzinfo)
        except (ValueError, TypeError):
            return False
        days = {"前天": 2, "昨天": 1, "昨晚": 1, "今天": 0, "今晚": 0}[time]
        return occurred.date() == (character_time - timedelta(days=days)).date()
    return time in str(event.get("summary", ""))


def _event_supported(
    clause: str, subject: str, events: Sequence[Mapping[str, Any]], times: Sequence[str],
    character_time: datetime | None,
) -> bool:
    """Conservative literal predicate matching, never a subject-only permit.

    This is a targeted guard, not a general semantic entailment engine. Time
    words must match the record's character-local date when a clock is supplied.
    Unmatched paraphrases can be repaired naturally.
    """
    action = _EVENT_ACTION.search(clause)
    if action is None:
        return False
    detail = _normalized_memory_value(clause[action.start():]).rstrip("呀呢啦哦啊")
    for event in events:
        summary = str(event.get("summary", ""))
        if not all(_event_time_matches(event, time, character_time) for time in times):
            continue
        evidence_subject = ""
        for evidence in re.split(r"[，,。！？；\n]|但是|不过", summary):
            if "年糕" in evidence:
                evidence_subject = "年糕"
            elif "同事" in evidence:
                evidence_subject = "同事"
            elif _SHARED.search(evidence):
                evidence_subject = "共同"
            elif "我" in evidence:
                evidence_subject = "我"
            if evidence_subject != subject or _non_event_clause(evidence.strip()):
                continue
            if detail and detail in _normalized_memory_value(evidence):
                return True
    return False


def _experience_violations(
    user: str, reply: str, events: Sequence[Mapping[str, Any]], character_time: datetime | None,
) -> list[str]:
    committed = [event for event in events if event.get("status") == "committed"]
    coworker_question = "同事" in user and bool(_EVENT_TIME.search(user)) and any(
        term in user for term in ("哪", "去哪", "做了什么", "干嘛", "行程", "忙什么")
    )
    location_question = bool(_EVENT_TIME.search(user)) and any(
        term in user for term in ("你在哪", "你在哪里", "你去了", "你去哪", "你昨天在哪", "地点")
    )
    reasons: list[str] = []
    subject = ""
    for sentence in re.split(r"[。！？；\n]", reply):
        times: list[str] = []
        for clause in re.split(r"[，,]|但是|不过|但(?=我|你|年糕|它|我们|同事)", sentence):
            clause = clause.strip()
            if not clause:
                continue
            if "年糕" in clause:
                subject = "年糕"
            elif "同事" in clause:
                subject = "同事"
            elif _SHARED.search(clause):
                subject = "共同"
            elif re.match(r"(?:我|你)(?!们)", clause) and not clause.startswith(("我记得", "我好像", "我印象")):
                subject = ""
            clause_times = _EVENT_TIME.findall(clause)
            if clause_times:
                times = clause_times
            if _non_event_clause(clause):
                continue
            action = _EVENT_ACTION.search(clause)
            if not action:
                continue
            current_subject = subject
            if not current_subject and "年糕" in user and clause.startswith("它"):
                current_subject = "年糕"
            elif not current_subject and re.match(r"我(?:前天|昨天|昨晚|今天|今晚|刚才|现在)", clause):
                current_subject = "我"
            elif not current_subject and coworker_question:
                current_subject = "同事"
            elif not current_subject and (location_question or (times and "我" in clause)):
                current_subject = "我"
            if not current_subject:
                continue
            if not times:
                question = next((part for part in re.split(r"[。！？；\n]", user) if current_subject in part), "")
                if current_subject == "我" and location_question:
                    question = user
                times = _EVENT_TIME.findall(question)
            # Acknowledging a missing record never licenses details after it.
            if _event_supported(clause, current_subject, committed, times, character_time):
                continue
            reason = {
                "同事": "unsupported_coworker_event", "年糕": "unsupported_cat_event",
                "共同": "unsupported_in_person_claim", "我": "unsupported_recent_location",
            }[current_subject]
            reasons.append(reason)
            if _TENTATIVE.search(clause):
                reasons.append("unsupported_event_speculation")
    if coworker_question and not reasons and not any("同事" in str(e.get("summary", "")) for e in committed):
        # 'Maybe' alone is harmless in '不确定，不想乱猜'. A positive vague
        # recollection still needs evidence even when it contains no venue.
        if not _UNKNOWN.search(reply) and re.search(r"记得|当然|是的|有印象", reply):
            reasons.append("unsupported_coworker_event")
    return reasons


def reply_boundary_violation(
    user_text: str,
    reply: str,
    *,
    guard_active: bool,
    world_events: Sequence[Mapping[str, Any]],
    confirmed_facts: Sequence[Mapping[str, Any]] = (),
    character_time: datetime | None = None,
) -> str | None:
    """Return a stable repair reason when a reply crosses a persona boundary."""
    text = reply.strip()
    if not clean_chat_reply(text):
        return "empty_after_stage_directions"

    user = user_text.strip()
    confirmed_facts = [fact for fact in confirmed_facts if fact.get("status", "active") == "active"]
    reasons: list[str] = []
    instruction_echoes = (
        "重答并完整回答", "直接答：“", "不要提对方养猫", "只回答已知的猫身份",
        "简短自然，遵守角色身份和事实边界",
    )
    if any(term in text for term in instruction_echoes):
        reasons.append("instruction_echo")
    reasons.extend(_experience_violations(user, text, world_events, character_time))

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
        claims_ownership = any(
            term in text
            for term in ("我养", "我自己养", "我家的猫", "我的猫", "年糕是我的长毛三花", "年糕是我的猫")
        )
        if not acknowledges or "年糕" not in text or not claims_ownership:
            reasons.append("cat_ownership_correction")
    if "年糕" in user or "年糕" in text:
        for clause in re.split(r"[，,。！？；\n]|但|不过|却", text):
            wrong_owner = re.search(
                r"我们家的|咱们家的|我们的猫|咱们的猫|你家的猫|你的猫|你家年糕|咱家年糕|年糕.{0,4}(?:你养|你家|我们养)",
                clause,
            )
            if wrong_owner and not re.search(r"不是|并非|不属于|不能说成|不该说成", clause[:wrong_owner.end()]):
                reasons.append("cat_ownership_boundary")
            if re.search(r"(?:不是|并非)(?:我养的|我的猫|我家的猫|苏晚(?:晚)?的猫)|不属于我", clause):
                reasons.append("cat_ownership_boundary")

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
