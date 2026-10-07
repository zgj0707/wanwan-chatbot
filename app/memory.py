from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime
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
    if any(pattern.search(privacy_probe) for pattern in _SENSITIVE_PATTERNS):
        raise MemoryValidationError("fact value contains disallowed private information")
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
) -> list[dict[str, Any]]:
    if not isinstance(query, str):
        raise MemoryDataUnavailable("memory query is invalid")
    if not 1 <= top_k <= 5:
        raise ValueError("top_k must be between 1 and 5")
    query_grams = character_bigrams(query)
    validated: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    try:
        for fact in facts:
            if fact.get("status", "active") != "active":
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
            score = (2.0 * intersection / (len(query_grams) + len(fact_grams))) if query_grams and fact_grams else 0.0
            validated.append({**dict(fact), "key": key, "value": value, "updated_at": timestamp, "score": score})
    except (AttributeError, KeyError, TypeError, ValueError, ValidationError) as exc:
        raise MemoryDataUnavailable("active memory rows are invalid") from exc

    matches = [fact for fact in validated if fact["score"] > 0]
    matches.sort(key=lambda fact: fact["id"])
    matches.sort(key=lambda fact: fact["updated_at"], reverse=True)
    matches.sort(key=lambda fact: fact["score"], reverse=True)
    selected = matches[:top_k]
    preferred = next((fact for fact in validated if fact["key"] == "profile.preferred_name"), None)
    if preferred is not None and all(fact["id"] != preferred["id"] for fact in selected):
        if len(selected) >= top_k:
            selected[-1] = preferred
        else:
            selected.append(preferred)
    return [{**fact, "updated_at": fact["updated_at"].isoformat()} for fact in selected]
