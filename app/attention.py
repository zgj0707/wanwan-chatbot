"""注意力层（CLR 第一阶段）：身份、注意力、对话相关性与表达分离。

职责（对应 CLR 纪要第十一节）：
- 根据当前话题（analysis 产出的 topic hint）对将注入 chat 的事实与事件做相关性筛选；
- 记录每轮"注入了什么、过滤了什么"，供测试 A 对照实验；
- 产出"本轮可自然提及"的候选话题（disclosure），不做硬性频率限制。

身份锚点永远全量注入（不在此处过滤）——过滤只作用于"主动提及倾向"。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.memory import character_bigrams, normalize_memory_text


# 话题关键词表：来自人设扩写文档第六节（话题意义网络），规则基线的启发式依据。
# key = 规范化话题标签；value = 该话题下应当升权的词根（对 facts.value / event.summary 做包含匹配）。
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "电影": ("电影", "侯孝贤", "是枝", "片", "剧"),
    "工作": ("需求", "评审", "加班", "周报", "工位", "开会", "产品", "上线", "版本"),
    "猫": ("年糕", "猫", "毛", "撸", "铲屎"),
    "咖啡": ("咖啡", "手冲", "豆子", "速溶"),
    "拍照": ("拍", "照片", "相机", "作品集", "相册"),
    "做饭": ("做饭", "煮", "厨房", "煎", "翻车", "外卖"),
    "音乐": ("歌", "音乐", "专辑", "播放", "歌单"),
    "游戏": ("游戏", "消消乐", "开黑"),
    "问候": (),
    "闲聊": (),
}

# 降权保护：这些话题与身份标签强绑定，但作为"主动提及"时最容易被判定为复读。
_IDENTITY_LABEL_TERMS = ("年糕", "猫", "需求", "评审", "会议", "周报", "工位", "产品")


def normalize_topic(topic: str | None) -> str:
    text = normalize_memory_text(topic or "")
    if not text:
        return "闲聊"
    for label, terms in _TOPIC_KEYWORDS.items():
        # 标签本身或任一词根命中即归入该话题（如"加班"→工作）
        if normalize_memory_text(label) in text:
            return label
        if any(normalize_memory_text(term) and normalize_memory_text(term) in text for term in terms):
            return label
    return "闲聊"


def _text_similarity(needle_terms: tuple[str, ...], haystack: str) -> int:
    return sum(1 for term in needle_terms if term in haystack)


def _bigram_overlap(left: str, right: str) -> float:
    left_grams = character_bigrams(left)
    right_grams = character_bigrams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / min(len(left_grams), len(right_grams))


def select_relevant(
    *,
    topic_hint: str | None,
    facts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    facts_top_k: int,
    events_top_k: int,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], dict[str, Any]]:
    """按话题相关性重排并筛选将注入 chat 的事实与事件。

    返回 (selected_facts, selected_events, debug)——debug 记录打分依据，
    供测试 A 对照与 attention 日志使用。数量上限沿用 memory_top_k / world_events_top_k。
    """
    topic = normalize_topic(topic_hint)
    topic_terms = _TOPIC_KEYWORDS.get(topic, ())
    debug: dict[str, Any] = {"topic": topic, "fact_scores": {}, "event_scores": {}, "suppressed": []}

    scored_facts: list[tuple[float, Mapping[str, Any]]] = []
    for fact in facts:
        value = str(fact.get("value", ""))
        key = str(fact.get("key", ""))
        score = 1.0
        if topic_terms:
            score += 0.8 * _text_similarity(topic_terms, value)
            score += 0.5 * _bigram_overlap(topic, value)
        # 身份标签类事实（猫、职业）在无关话题下降权——抑制主动提及倾向
        if topic not in ("猫", "工作") and any(term in value for term in _IDENTITY_LABEL_TERMS):
            score -= 0.4
            debug["suppressed"].append({"kind": "fact", "value": value[:30], "reason": "identity_label_off_topic"})
        # 生日/称呼这类身份事实始终保留（回答问题需要），只是不加分
        if key.startswith("profile."):
            score += 0.2
        debug["fact_scores"][key + ":" + value[:16]] = round(score, 3)
        scored_facts.append((score, fact))
    scored_facts.sort(key=lambda pair: (-pair[0], str(pair[1].get("id", ""))))
    selected_facts = [fact for _, fact in scored_facts[: max(0, facts_top_k)]]

    scored_events: list[tuple[float, Mapping[str, Any]]] = []
    for event in events:
        summary = str(event.get("summary", ""))
        score = 1.0
        if topic_terms:
            score += 0.9 * _text_similarity(topic_terms, summary)
            score += 0.5 * _bigram_overlap(topic, summary)
        if topic not in ("工作",) and any(term in summary for term in ("需求", "评审", "版本")):
            score -= 0.4
            debug["suppressed"].append({"kind": "event", "value": summary[:30], "reason": "work_label_off_topic"})
        debug["event_scores"][summary[:20]] = round(score, 3)
        scored_events.append((score, event))
    scored_events.sort(key=lambda pair: (-pair[0], str(pair[1].get("occurred_at", ""))))
    selected_events = [event for _, event in scored_events[: max(0, events_top_k)]]

    return selected_facts, selected_events, debug


def build_disclosure(
    *,
    topic: str,
    events: Sequence[Mapping[str, Any]],
    recent_topics: Sequence[str],
) -> list[str]:
    """信息披露选择：给出"本轮可自然提及"的候选话题（0-2 项）。

    规则基线：
    - 最近 3 轮已提过的话题降权（防"生活汇报腔"）；
    - 只从"与当前话题有桥接点的近期事件"里挑，找不到就给空（允许她什么都不提）。
    """
    recent = list(recent_topics)[-3:]
    candidates: list[str] = []
    for event in events:
        summary = str(event.get("summary", ""))
        if topic and _text_similarity(_TOPIC_KEYWORDS.get(topic, ()), summary):
            candidates.append(summary)
            continue
        for past_topic in recent:
            if past_topic and _text_similarity(_TOPIC_KEYWORDS.get(past_topic, ()), summary):
                break  # 最近刚聊过的话题不再披露
        else:
            continue
    return candidates[:2]
