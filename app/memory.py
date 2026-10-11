from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.models import FactCandidate
from app.state import parse_db_datetime


FACT_KEYS: tuple[str, ...] = (
    "profile.preferred_name",
    "profile.birthday",
    "preference.drink",
    "preference.food",
    "preference.hobby",
    "preference.chat_style",
    "plan.personal",
)

# Identity facts are part of how 苏晚 addresses the user and never fade;
# ordinary preferences slowly lose recall weight when not revisited
# (MemoryBank-style time × importance decay, recency floor is 0.6).
_FACT_IMPORTANCE: dict[str, float] = {
    "profile.preferred_name": 1.0,
    "profile.birthday": 1.0,
    "preference.drink": 0.7,
    "preference.food": 0.7,
    "preference.hobby": 0.7,
    "preference.chat_style": 0.8,
    "plan.personal": 0.9,
}


def fact_importance(key: str) -> float:
    return _FACT_IMPORTANCE.get(key, 0.7)


IDENTITY_FACT_KEYS = ("profile.preferred_name", "profile.birthday")

# Explicit category cues complement value bigrams (e.g. asking about a birthday
# without repeating its date). They describe the current query, not a model guess.
_FACT_TOPIC_CUES = {
    "profile.preferred_name": ("称呼", "昵称", "名字", "叫我"),
    "profile.birthday": ("生日", "生辰"),
    "preference.drink": ("喝", "饮料", "饮品"),
    "preference.food": ("吃", "食物", "口味"),
    "preference.hobby": ("爱好", "兴趣"),
    "preference.chat_style": ("聊天", "说话", "回复"),
    "plan.personal": ("计划", "打算", "安排"),
}
_TOPIC_SCORE = 0.35


def fact_rank_key(fact: Mapping[str, Any]) -> tuple[float, float, str]:
    """One ordering for recall, prompt selection and budget trimming.

    score remains the original Dice similarity. rank_score adds explicit topic
    relevance, importance and recency; legacy prompt callers keep score semantics.
    """
    rank_score = fact.get("rank_score")
    if rank_score is None:
        rank_score = float(fact.get("score", 0.0)) * float(fact.get("decay_weight", 1.0))
    updated = fact.get("updated_at")
    timestamp = (
        updated if isinstance(updated, datetime) else parse_db_datetime(updated)
    ) if updated else None
    return (-float(rank_score), -timestamp.timestamp() if timestamp else float("inf"), str(fact["id"]))


def select_ranked_facts(
    facts: Sequence[Mapping[str, Any]], top_k: int,
) -> list[Mapping[str, Any]]:
    """Reserve identity coverage, then fill with the best ordinary matches.

    Both identities survive ordinary preferences whenever at least two slots
    exist. With one slot, the address/name wins; output still follows rank order.
    """
    if top_k <= 0:
        return []
    ranked = sorted(
        (fact for fact in facts if fact.get("status", "active") == "active"),
        key=fact_rank_key,
    )
    protected = [
        fact for key in IDENTITY_FACT_KEYS for fact in ranked if fact["key"] == key
    ][:top_k]
    protected_ids = {fact["id"] for fact in protected}
    selected = protected + [fact for fact in ranked if fact["id"] not in protected_ids][:top_k - len(protected)]
    return sorted(selected, key=fact_rank_key)


def lowest_priority_fact_index(facts: Sequence[Mapping[str, Any]]) -> int:
    # Preferences yield to both identities under budget pressure; name yields last.
    ordinary = [i for i, fact in enumerate(facts) if fact["key"] not in IDENTITY_FACT_KEYS]
    non_name = [i for i, fact in enumerate(facts) if fact["key"] != "profile.preferred_name"]
    return max(ordinary or non_name or list(range(len(facts))), key=lambda i: fact_rank_key(facts[i]))

_SENSITIVE_PATTERNS = (
    re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE),
    re.compile(r"(?<!\d)(?!\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?!\d))(?!\d{1,2}[-/.]\d{1,2}[-/.]\d{4}(?!\d))\+?\d(?:[\s()\-./,，、·]*\d){7,}(?!\d)"),
    re.compile(r"(?:密码|口令|密钥|api[_ -]?key|token|验证码|银行卡|身份证|手机号|电话号码|住址|地址|公司|工作单位|病史|疾病|诊断|病人|症状|过敏|医疗|药物|保险|账户|账号|工资|薪资|贷款|信用卡)", re.IGNORECASE),
    re.compile(r"(?:[\u4e00-\u9fff]{2,}(?:路|街|巷|弄)\s*\d{1,4}(?:号)?|(?<!\d)\d{1,4}(?:号|栋|单元|室)|[\u4e00-\u9fff]{2,}(?:小区|大厦))"),
)
_NICKNAME_CUES = ("昵称", "网名", "可以叫我", "叫我", "称呼我")


class MemoryValidationError(ValueError):
    pass


class MemoryDataUnavailable(RuntimeError):
    """The memory index could not be trusted; callers should continue without facts."""


def normalize_memory_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(char for char in normalized if char.isalnum())


def character_bigrams(value: str) -> set[str]:
    normalized = normalize_memory_text(value)
    if not normalized:
        return set()
    if len(normalized) == 1:
        return {normalized}
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def canonical_fact_hash(key: str, value: str) -> str:
    if key not in FACT_KEYS:
        raise MemoryValidationError("unsupported fact key")
    normalized = normalize_memory_text(value)
    if not normalized:
        raise MemoryValidationError("fact value is empty after normalization")
    return hashlib.sha256(f"{key}|{normalized}".encode("utf-8")).hexdigest()


def _match_sensitive_pattern(text: str) -> str | None:
    """返回命中的敏感信息类别名（用户可读），未命中返回 None。"""
    labels = (
        ("邮箱地址", 0),
        ("电话号码", 1),
        ("敏感词（密码、证件、账户、住址、公司、健康医疗等）", 2),
        ("详细住址", 3),
    )
    for label, index in labels:
        if _SENSITIVE_PATTERNS[index].search(text):
            return label
    return None


def validate_fact_value(key: str, value: str) -> tuple[str, str]:
    if key not in FACT_KEYS:
        raise MemoryValidationError("unsupported fact key")
    if not isinstance(value, str):
        raise MemoryValidationError("fact value must be text")
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= 120:
        raise MemoryValidationError("fact value must contain 1 to 120 characters")
    privacy_probe = "".join(
        character
        for character in unicodedata.normalize("NFKC", cleaned)
        if unicodedata.category(character) not in {"Cf", "Cc"}
    )
    sensitive_label = _match_sensitive_pattern(privacy_probe)
    if sensitive_label is not None:
        raise MemoryValidationError(
            f"fact value contains disallowed private information: {sensitive_label}"
        )
    if key == "profile.birthday":
        try:
            month_text, day_text = cleaned.split("-")
            if len(month_text) != 2 or len(day_text) != 2:
                raise ValueError
            datetime(2000, int(month_text), int(day_text))
        except (ValueError, TypeError) as exc:
            raise MemoryValidationError("birthday must be a valid MM-DD date") from exc
    return cleaned, canonical_fact_hash(key, cleaned)


def validate_fact_candidates(candidates: Sequence[Any], user_text: str) -> list[FactCandidate]:
    if not isinstance(user_text, str):
        raise MemoryValidationError("user_text must be text")
    if len(candidates) > 2:
        raise MemoryValidationError("at most two fact candidates are allowed")
    accepted: list[FactCandidate] = []
    for candidate in candidates:
        try:
            parsed = candidate if isinstance(candidate, FactCandidate) else FactCandidate.model_validate(candidate)
            value, _ = validate_fact_value(parsed.key, parsed.value)
            if parsed.evidence_quote not in user_text:
                raise MemoryValidationError("evidence_quote must be a substring of the current user message")
            if parsed.value not in parsed.evidence_quote:
                raise MemoryValidationError("fact value must be explicitly present in its evidence quote")
            if parsed.key == "profile.preferred_name" and not any(cue in parsed.evidence_quote for cue in _NICKNAME_CUES):
                raise MemoryValidationError("preferred_name requires explicit nickname evidence")
            accepted.append(FactCandidate(key=parsed.key, value=value, evidence_quote=parsed.evidence_quote))
        except (ValidationError, MemoryValidationError, TypeError, ValueError):
            # A bad candidate is ignored; it cannot turn an otherwise usable reply into a fact.
            continue
    return accepted


def normalized_payload_signature(kind: str, payload: Mapping[str, Any]) -> str:
    if kind == "user_fact":
        key = payload.get("key")
        value = payload.get("value")
        if not isinstance(key, str) or not isinstance(value, str):
            raise MemoryValidationError("invalid user fact payload")
        normalized: dict[str, Any] = {"key": key, "value": normalize_memory_text(value)}
        quote = payload.get("evidence_quote")
        if isinstance(quote, str):
            normalized["evidence_quote"] = normalize_memory_text(quote)
    elif kind == "persona":
        field = payload.get("field")
        value = payload.get("value")
        if not isinstance(field, str) or not isinstance(value, str):
            raise MemoryValidationError("invalid persona payload")
        normalized = {"field": field, "value": normalize_memory_text(value)}
    else:
        raise MemoryValidationError("invalid proposal kind")
    import json

    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def retrieve_facts(
    query: str,
    facts: Sequence[Mapping[str, Any]],
    top_k: int = 5,
    *,
    now: datetime | None = None,
    debug: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return validated recall rows; optional debug explains every candidate.

    Debug is local, carries no fact values, and is never persisted or sent to the
    model. Invalid active rows still invalidate the entire index as before.
    """
    if not isinstance(query, str):
        raise MemoryDataUnavailable("memory query is invalid")
    if not 1 <= top_k <= 5:
        raise ValueError("top_k must be between 1 and 5")
    query_grams = character_bigrams(query)
    normalized_query = normalize_memory_text(query)
    now = now or datetime.now(timezone.utc)
    validated: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    try:
        for fact in facts:
            if fact.get("status", "active") != "active":
                excluded.append({"id": fact.get("id"), "key": fact.get("key"), "selected": False, "reason": "inactive"})
                continue
            fact_id = fact.get("id")
            parsed_id = UUID(str(fact_id))
            if parsed_id.version != 4 or str(parsed_id) != str(fact_id):
                raise ValueError("fact id must be UUID4")
            key = fact.get("key")
            if key not in FACT_KEYS:
                raise ValueError("fact key is invalid")
            if "namespace" in fact and fact["namespace"] != "user":
                raise ValueError("fact namespace is invalid")
            if "subject_id" in fact and fact["subject_id"] != "local-user":
                raise ValueError("fact subject is invalid")
            value, expected_hash = validate_fact_value(key, fact.get("value"))
            if fact.get("canonical_hash") != expected_hash:
                raise ValueError("fact hash does not match its value")
            if key in seen_keys:
                raise ValueError("duplicate active fact key")
            seen_keys.add(key)
            updated = fact.get("updated_at")
            timestamp = parse_db_datetime(updated)
            fact_grams = character_bigrams(value)
            intersection = len(query_grams & fact_grams)
            similarity = (2.0 * intersection / (len(query_grams) + len(fact_grams))) if query_grams and fact_grams else 0.0
            importance = fact.get("importance")
            if importance is None:
                # Rows from older writers may not carry the column; derive the
                # canonical default for the key instead of failing the whole index.
                importance = fact_importance(key)
            if not isinstance(importance, (int, float)) or not 0.5 <= float(importance) <= 1.0:
                raise ValueError("fact importance is invalid")
            last_prompt = fact.get("last_prompt_at")
            last_prompt_time = (
                parse_db_datetime(last_prompt)
                if isinstance(last_prompt, str) and last_prompt
                else None
            )
            validated.append(
                {
                    **dict(fact),
                    "key": key,
                    "value": value,
                    "updated_at": timestamp,
                    "score": similarity,
                    "importance": float(importance),
                    "last_prompt_time": last_prompt_time,
                }
            )
    except (AttributeError, KeyError, TypeError, ValueError, ValidationError) as exc:
        raise MemoryDataUnavailable("active memory rows are invalid") from exc

    for fact in validated:
        identity = fact["key"] in IDENTITY_FACT_KEYS
        recency = 1.0 if identity else _recency_factor(
            fact["last_prompt_time"], fact["updated_at"], now
        )
        topic_cues = [cue for cue in _FACT_TOPIC_CUES[fact["key"]] if cue in normalized_query]
        topic_score = _TOPIC_SCORE if topic_cues else 0.0
        fact["decay_weight"] = fact["importance"] * recency
        fact["rank_score"] = max(fact["score"], topic_score) * fact["decay_weight"]
        fact["recall_debug"] = {
            "id": fact["id"], "key": fact["key"],
            "kind": "identity" if identity else "ordinary",
            "similarity": fact["score"], "topic_cues": topic_cues,
            "topic_score": topic_score, "importance": fact["importance"],
            "recency_factor": recency, "rank_score": fact["rank_score"],
        }
    eligible = [fact for fact in validated if fact["rank_score"] > 0 or fact["key"] in IDENTITY_FACT_KEYS]
    selected = select_ranked_facts(eligible, top_k)
    selected_ranks = {fact["id"]: i + 1 for i, fact in enumerate(selected)}
    for fact in validated:
        selected_rank = selected_ranks.get(fact["id"])
        identity = fact["key"] in IDENTITY_FACT_KEYS
        fact["recall_debug"].update({
            "selected": selected_rank is not None, "rank": selected_rank,
            "reason": (
                "identity_coverage" if selected_rank is not None and identity
                else "relevance_match" if selected_rank is not None
                else "identity_capacity" if identity
                else "no_relevance" if fact["rank_score"] == 0
                else "capacity_after_identity_coverage"
            ),
        })
    if debug is not None:
        debug.extend([dict(fact["recall_debug"]) for fact in sorted(validated, key=fact_rank_key)] + excluded)
    return [{**fact, "updated_at": fact["updated_at"].isoformat()} for fact in selected]


_DECAY_HALF_LIFE_DAYS = 14.0
_DECAY_FLOOR = 0.6


def _recency_factor(
    last_prompt_time: datetime | None,
    updated_at: datetime,
    now: datetime | None = None,
) -> float:
    """Exponential recency decay with a floor: 1.0 when recently used, sliding
    toward the floor over a two-week half-life. Falls back to updated_at when
    the fact has never been injected into a prompt."""
    reference = last_prompt_time or updated_at
    if now is None:
        now = datetime.now(timezone.utc)
    reference_utc = reference if reference.tzinfo is not None else reference.replace(tzinfo=timezone.utc)
    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    elapsed_days = max(0.0, (now_utc - reference_utc).total_seconds() / 86400.0)
    factor = 0.5 ** (elapsed_days / _DECAY_HALF_LIFE_DAYS)
    return max(_DECAY_FLOOR, factor)
