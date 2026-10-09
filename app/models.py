from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from uuid import UUID


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LLMUsage(StrictModel):
    prompt_tokens: StrictInt | None = Field(default=None, ge=0)
    completion_tokens: StrictInt | None = Field(default=None, ge=0)
    total_tokens: StrictInt | None = Field(default=None, ge=0)


class LLMResult(StrictModel):
    text: StrictStr
    parsed_json: Any | None = None
    usage: LLMUsage | None = None
    latency_ms: StrictInt = Field(ge=0)
    attempt: StrictInt = Field(ge=1)


class CallMetric(StrictModel):
    operation_id: StrictStr | None = None
    profile: Literal["chat", "analysis", "world"]
    attempt: StrictInt = Field(ge=1)
    status: StrictStr = Field(min_length=1)
    latency_ms: StrictInt = Field(ge=0)
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    created_at: StrictStr = Field(min_length=1)

    @field_validator("created_at")
    @classmethod
    def metric_time_must_be_aware_utc(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("created_at must be an ISO timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class AnalysisStateDelta(StrictModel):
    valence: StrictInt = Field(ge=-8, le=8)
    energy: StrictInt = Field(ge=-5, le=5)
    closeness: StrictInt = Field(ge=-2, le=2)
    guard_trigger: StrictBool


FactKey = Literal[
    "profile.preferred_name",
    "profile.birthday",
    "preference.drink",
    "preference.food",
    "preference.hobby",
    "preference.chat_style",
    "plan.personal",
]


class FactCandidate(StrictModel):
    key: FactKey
    value: StrictStr = Field(min_length=1, max_length=120)
    evidence_quote: StrictStr = Field(min_length=1, max_length=200)

    @field_validator("value")
    @classmethod
    def validate_birthday(cls, value: str, info):
        if info.data.get("key") == "profile.birthday":
            try:
                month_text, day_text = value.split("-")
                if len(month_text) != 2 or len(day_text) != 2:
                    raise ValueError
                date(2000, int(month_text), int(day_text))
            except (ValueError, TypeError) as exc:
                raise ValueError("birthday must be a valid MM-DD date") from exc
        return value


class PersonaProposal(StrictModel):
    field: Literal["preferences.likes", "preferences.dislikes"]
    value: StrictStr = Field(min_length=1, max_length=30)
    reason: StrictStr = Field(min_length=1)


class AnalysisResult(StrictModel):
    state_delta: AnalysisStateDelta
    fact_candidates: list[FactCandidate] = Field(max_length=2)
    persona_proposals: list[PersonaProposal] = Field(max_length=1)


class APIErrorDetail(StrictModel):
    code: StrictStr
    message: StrictStr
    retryable: StrictBool
    operation_id: StrictStr | None = None


class APIErrorResponse(StrictModel):
    error: APIErrorDetail


class SessionCreateRequest(StrictModel):
    title: StrictStr = Field(min_length=1, max_length=40)

    @field_validator("title", mode="before")
    @classmethod
    def title_must_be_trimmed_and_nonempty(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SessionArchiveRequest(StrictModel):
    archived: StrictBool


class ChatRequest(StrictModel):
    operation_id: StrictStr
    session_id: StrictStr
    text: StrictStr = Field(min_length=1, max_length=2000)
    retry: StrictBool = False

    @field_validator("operation_id", "session_id")
    @classmethod
    def canonical_uuid4(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("must be a canonical UUID4") from exc
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("must be a canonical UUID4")
        return value

    @field_validator("text", mode="before")
    @classmethod
    def trim_and_validate_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class MemoryCreateRequest(StrictModel):
    key: FactKey
    value: StrictStr = Field(min_length=1, max_length=120)

    @field_validator("value", mode="before")
    @classmethod
    def trim_value(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


MATERIAL_PILLARS = Literal[
    "work_diary", "photo_aesthetics", "night_emotion", "cat_ai_girl", "uncategorized"
]
MATERIAL_HOOKS = Literal[
    "contrast", "specific_number", "suspense_question",
    "identity_resonance", "cat_hook", "scene_hook", "none",
]


class MaterialCreateRequest(StrictModel):
    message_id: StrictStr
    pillar: MATERIAL_PILLARS
    hook_type: MATERIAL_HOOKS = "none"
    note: StrictStr | None = Field(default=None, max_length=200)

    @field_validator("note", mode="before")
    @classmethod
    def empty_note_becomes_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class MaterialUpdateRequest(StrictModel):
    pillar: MATERIAL_PILLARS | None = None
    hook_type: MATERIAL_HOOKS | None = None
    note: StrictStr | None = Field(default=None, max_length=200)
    status: Literal["saved", "discarded"] | None = None

    @field_validator("note", mode="before")
    @classmethod
    def empty_note_becomes_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ChatApiUpdateRequest(StrictModel):
    """Settings-center update for chat/analysis/world endpoints. Key stays local."""
    base_url: StrictStr = Field(min_length=1, max_length=500)
    model: StrictStr = Field(min_length=1, max_length=200)
    model_analysis: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    model_world: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    api_key: StrictStr | None = Field(default=None, max_length=400)
    api_key_env: StrictStr = Field(default="SUWAN_API_KEY", pattern=r"^[A-Z][A-Z0-9_]*$")


class WeatherUpdateRequest(StrictModel):
    provider: Literal["open_meteo", "off"]
    city: StrictStr = Field(default="", max_length=40)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class MemoryEditRequest(StrictModel):
    value: StrictStr = Field(min_length=1, max_length=120)

    @field_validator("value", mode="before")
    @classmethod
    def trim_value(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class MemoryForgetRequest(StrictModel):
    redact_session_history: StrictBool = True


class ProposalResolveRequest(StrictModel):
    decision: Literal["accept", "reject"]


class DiagnosticsProbeRequest(StrictModel):
    profile: Literal["chat", "analysis", "world"]


class WorldAdvanceRequest(StrictModel):
    pass


class BackupRequest(StrictModel):
    pass


class WorldEventRetractRequest(StrictModel):
    pass


class MessageRetractRequest(StrictModel):
    pass


class WorldThreadControlRequest(StrictModel):
    action: Literal["pause", "resume", "restart"]


class InitiativeRequest(StrictModel):
    operation_id: StrictStr
    session_id: StrictStr

    @field_validator("operation_id", "session_id")
    @classmethod
    def canonical_uuid4(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("must be a canonical UUID4") from exc
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("must be a canonical UUID4")
        return value
