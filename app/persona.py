from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import StrictStr, ValidationError

from app.config import project_root
from app.models import StrictModel


class PersonaSeedError(ValueError):
    """Raised when the first-run persona seed or its sources are invalid."""


class PersonaAnchors(StrictModel):
    name: Literal["苏晚"]
    nickname: Literal["晚晚"]
    age: Literal[25]
    city: Literal["广州"]
    occupation: Literal["互联网中厂产品经理"]
    cat_name: Literal["年糕"]
    cat_breed: Literal["长毛三花猫"]
    virtual_identity: Literal[True]
    temperament: list[StrictStr]
    boundary: Literal["温柔但有距离感，不讨好，不黏人"]
    catchphrases: list[StrictStr]


class PersonaPreferences(StrictModel):
    hobbies: list[StrictStr]
    likes: list[StrictStr]
    dislikes: list[StrictStr]


class PersonaSeed(StrictModel):
    schema_version: Literal[1]
    character_id: Literal["suwan"]
    anchors: PersonaAnchors
    preferences: PersonaPreferences


EXPECTED_ANCHORS = {
    "name": "苏晚",
    "nickname": "晚晚",
    "age": 25,
    "city": "广州",
    "occupation": "互联网中厂产品经理",
    "cat_name": "年糕",
    "cat_breed": "长毛三花猫",
    "virtual_identity": True,
    "temperament": ["温柔", "慵懒", "好奇", "机灵"],
    "boundary": "温柔但有距离感，不讨好，不黏人",
    "catchphrases": ["嗯…让我想想", "好无聊啊", "才不是呢", "再睡五分钟", "这个需求……再改改吧"],
}

EXPECTED_HOBBIES = ["拍照", "分享日常", "画画", "胶片摄影", "老电影", "独立音乐", "香薰", "手冲咖啡"]
SOURCE_FILENAMES = (
    "苏晚Chatbot-可执行PRD_v1.md",
    "苏晚-完整人设_当前版本.md",
    "苏晚-对话角色包.md",
)
_FORBIDDEN_IDENTITY_TERMS = (
    "我是", "我叫", "身份", "年龄", "性别", "职业", "关系类型", "猫叫", "猫名",
    "25岁", "苏晚", "年糕", "产品经理", "长毛三花猫", "虚拟身份", "真实身份",
)


class PersonaDataUnavailable(RuntimeError):
    """The persisted persona snapshot cannot be trusted."""


class PersonaPolicyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PersonaSnapshot:
    seed: PersonaSeed
    anchors_json: str
    preferences_json: str
    voice_text: str
    source_hashes_json: str
    anchors_hash: str


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def apply_persona_proposal(
    *,
    anchors_json: str,
    preferences_json: str,
    field: str,
    value: str,
) -> tuple[str, str, bool]:
    """Return immutable anchors, the updated preference snapshot, and whether it was already present."""
    try:
        raw_anchors = json.loads(anchors_json)
        if not isinstance(raw_anchors, dict):
            raise ValueError("persona anchors must be an object")
        canonical_anchors = _canonical_json(raw_anchors)
        stored_anchor_hash = hashlib.sha256(canonical_anchors.encode("utf-8")).hexdigest()
        expected_anchors = _canonical_json(EXPECTED_ANCHORS)
        expected_anchor_hash = hashlib.sha256(expected_anchors.encode("utf-8")).hexdigest()
        if stored_anchor_hash != expected_anchor_hash:
            raise PersonaPolicyError("ANCHOR_CHANGE_FORBIDDEN", "fixed persona anchors have changed")
        anchors = PersonaAnchors.model_validate(raw_anchors)
        canonical_anchors = _canonical_json(anchors.model_dump(mode="json"))

        raw_preferences = json.loads(preferences_json)
        preferences = PersonaPreferences.model_validate(raw_preferences).model_dump(mode="json")
        if set(preferences) != {"hobbies", "likes", "dislikes"}:
            raise ValueError("preference fields are invalid")
        if len(preferences["likes"]) > 10 or len(preferences["dislikes"]) > 10:
            raise ValueError("preference list exceeds its limit")
        for preference_values in preferences.values():
            if any(not isinstance(item, str) or not 1 <= len(item.strip()) <= 30 for item in preference_values):
                raise ValueError("persisted preference value is invalid")
    except PersonaPolicyError:
        raise
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        raise PersonaDataUnavailable("persisted persona snapshot is invalid") from exc

    if field not in {"preferences.likes", "preferences.dislikes"}:
        raise PersonaPolicyError("PROPOSAL_INVALID", "persona field is not mutable")
    if not isinstance(value, str):
        raise PersonaPolicyError("PROPOSAL_INVALID", "persona value must be text")
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= 30:
        raise PersonaPolicyError("PROPOSAL_INVALID", "persona value must contain 1 to 30 characters")
    if any(term in cleaned for term in _FORBIDDEN_IDENTITY_TERMS):
        raise PersonaPolicyError("PROPOSAL_INVALID", "persona value contains a fixed identity term")

    target_key = field.split(".", 1)[1]
    current = preferences[target_key]
    normalized_value = "".join(character.lower() for character in cleaned if character.isalnum())
    if any(
        "".join(character.lower() for character in item if character.isalnum()) == normalized_value
        for item in current
    ):
        return canonical_anchors, _canonical_json(preferences), True
    if len(current) >= 10:
        raise PersonaPolicyError("PERSONA_LIMIT_REACHED", "persona preference list is full")
    current.append(cleaned)
    return canonical_anchors, _canonical_json(preferences), False


def load_seed(path: str | Path | None = None) -> PersonaSeed:
    seed_path = Path(path) if path is not None else project_root() / "persona" / "seed.json"
    try:
        raw = json.loads(seed_path.read_text(encoding="utf-8"))
        seed = PersonaSeed.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise PersonaSeedError(f"persona seed is missing or invalid: {seed_path}") from exc

    if seed.anchors.model_dump(mode="json") != EXPECTED_ANCHORS:
        raise PersonaSeedError("persona seed anchors do not match the PRD v1 fixed anchors")
    preferences = seed.preferences.model_dump(mode="json")
    if preferences != {"hobbies": EXPECTED_HOBBIES, "likes": [], "dislikes": []}:
        raise PersonaSeedError("persona seed preferences do not match the PRD v1 initial preferences")
    return seed


def load_persona_snapshot(
    *,
    seed_path: str | Path | None = None,
    voice_path: str | Path | None = None,
    sources_dir: str | Path | None = None,
) -> PersonaSnapshot:
    root = project_root()
    seed = load_seed(seed_path)
    resolved_voice_path = Path(voice_path) if voice_path is not None else root / "persona" / "voice.md"
    resolved_sources_dir = Path(sources_dir) if sources_dir is not None else root / "docs" / "source"

    try:
        voice_text = resolved_voice_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PersonaSeedError(f"persona voice file is missing or unreadable: {resolved_voice_path}") from exc
    if not voice_text.strip():
        raise PersonaSeedError("persona voice file must not be empty")

    source_hashes: dict[str, str] = {}
    for filename in SOURCE_FILENAMES:
        source_path = resolved_sources_dir / filename
        try:
            digest = hashlib.sha256(source_path.read_bytes()).hexdigest().upper()
        except OSError as exc:
            raise PersonaSeedError(f"persona source file is missing or unreadable: {source_path}") from exc
        source_hashes[filename] = digest

    anchors_json = _canonical_json(seed.anchors.model_dump(mode="json"))
    return PersonaSnapshot(
        seed=seed,
        anchors_json=anchors_json,
        preferences_json=_canonical_json(seed.preferences.model_dump(mode="json")),
        voice_text=voice_text,
        source_hashes_json=_canonical_json(source_hashes),
        anchors_hash=hashlib.sha256(anchors_json.encode("utf-8")).hexdigest(),
    )
