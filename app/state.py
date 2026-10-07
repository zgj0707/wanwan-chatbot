from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping
from zoneinfo import ZoneInfo

from pydantic import Field, StrictInt, StrictStr, ValidationError

from app.models import AnalysisStateDelta
from app.models import StrictModel


@dataclass(frozen=True)
class CharacterState:
    valence: int
    energy: int
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class RelationshipState:
    closeness: int
    guard_until: datetime | None
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class StateProjection:
    character: CharacterState
    relationship: RelationshipState
    valence: int
    energy: int
    closeness: int
    character_updated_at: datetime
    guard_until: datetime | None
    warnings: tuple[str, ...]
    decay_changed: bool
    guard_expired: bool


@dataclass(frozen=True)
class PersistedState:
    valence: int
    energy: int
    character_updated_at: datetime
    character_version: int
    write_character: bool
    closeness: int
    guard_until: datetime | None
    relationship_updated_at: datetime
    relationship_version: int
    write_relationship: bool


@dataclass(frozen=True)
class TimeContext:
    character_time: datetime
    display_time: datetime
    is_late_night: bool
    activity_scene: str


class DataCorrupt(RuntimeError):
    code = "DATA_CORRUPT"


class _CharacterStateRow(StrictModel):
    character_id: StrictStr
    valence: StrictInt = Field(ge=-100, le=100)
    energy: StrictInt = Field(ge=0, le=100)
    updated_at: StrictStr
    version: StrictInt = Field(gt=0)


class _RelationshipStateRow(StrictModel):
    user_id: StrictStr
    character_id: StrictStr
    closeness: StrictInt = Field(ge=0, le=100)
    guard_until: StrictStr | None
    updated_at: StrictStr
    version: StrictInt = Field(gt=0)


def load_state_rows(
    character_row: Mapping[str, object],
    relationship_row: Mapping[str, object],
) -> tuple[CharacterState, RelationshipState]:
    try:
        character_data = _CharacterStateRow.model_validate(dict(character_row))
        relationship_data = _RelationshipStateRow.model_validate(dict(relationship_row))
        if character_data.character_id != "suwan":
            raise ValueError("unexpected character id")
        if (relationship_data.user_id, relationship_data.character_id) != ("local-user", "suwan"):
            raise ValueError("unexpected relationship identity")
        character_updated_at = parse_db_datetime(character_data.updated_at)
        relationship_updated_at = parse_db_datetime(relationship_data.updated_at)
        guard_until = parse_db_datetime(relationship_data.guard_until, nullable=True)
        assert character_updated_at is not None and relationship_updated_at is not None
        return (
            CharacterState(
                character_data.valence,
                character_data.energy,
                character_updated_at,
                character_data.version,
            ),
            RelationshipState(
                relationship_data.closeness,
                guard_until,
                relationship_updated_at,
                relationship_data.version,
            ),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise DataCorrupt("character or relationship state is invalid") from exc


def parse_db_datetime(value: str | None, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("database timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("database timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("database timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def mood_label(
    valence: int,
    energy: int,
    guard_until: datetime | None,
    now: datetime,
) -> str:
    if guard_until is not None and guard_until > _aware_utc(now):
        return "cold"
    if energy < 30:
        return "tired"
    if valence < -25:
        return "sad"
    if valence > 25:
        return "happy"
    return "calm"


def project_state(
    character: CharacterState,
    relationship: RelationshipState,
    now: datetime,
) -> StateProjection:
    now_utc = _aware_utc(now)
    character_updated_at = _aware_utc(character.updated_at)
    if now_utc < character_updated_at:
        elapsed_hours = 0
        warnings = ("CLOCK_BACKWARD",)
        consumed_until = character_updated_at
    else:
        elapsed = now_utc - character_updated_at
        elapsed_hours = elapsed.days * 24 + elapsed.seconds // 3600
        warnings = ()
        consumed_until = character_updated_at + timedelta(hours=elapsed_hours)

    valence = _toward_zero(character.valence, 5 * elapsed_hours)
    energy = _toward(character.energy, 60, 3 * elapsed_hours)
    guard_until = relationship.guard_until
    guard_expired = guard_until is not None and _aware_utc(guard_until) <= now_utc
    if guard_expired:
        guard_until = None

    return StateProjection(
        character=character,
        relationship=relationship,
        valence=valence,
        energy=energy,
        closeness=relationship.closeness,
        character_updated_at=consumed_until,
        guard_until=guard_until,
        warnings=warnings,
        decay_changed=(
            elapsed_hours > 0
            and (valence != character.valence or energy != character.energy or consumed_until != character_updated_at)
        ),
        guard_expired=guard_expired,
    )


def apply_analysis_delta(
    projection: StateProjection,
    delta: AnalysisStateDelta,
    now: datetime,
) -> PersistedState:
    now_utc = _aware_utc(now)
    valence = max(-100, min(100, projection.valence + delta.valence))
    energy = max(0, min(100, projection.energy + delta.energy))
    closeness = max(0, min(100, projection.closeness + delta.closeness))

    write_character = projection.decay_changed or delta.valence != 0 or delta.energy != 0
    character_updated_at = now_utc if delta.valence != 0 or delta.energy != 0 else projection.character_updated_at
    character_version = projection.character.version + int(write_character)

    write_relationship = delta.closeness != 0 or delta.guard_trigger or projection.guard_expired
    guard_until = projection.guard_until
    if delta.guard_trigger:
        proposed_guard_until = now_utc + timedelta(hours=2)
        if guard_until is None or guard_until < proposed_guard_until:
            guard_until = proposed_guard_until

    return PersistedState(
        valence=valence,
        energy=energy,
        character_updated_at=character_updated_at,
        character_version=character_version,
        write_character=write_character,
        closeness=closeness,
        guard_until=guard_until,
        relationship_updated_at=now_utc if write_relationship else projection.relationship.updated_at,
        relationship_version=projection.relationship.version + int(write_relationship),
        write_relationship=write_relationship,
    )


def time_context(
    now: datetime,
    *,
    character_timezone: str = "Asia/Shanghai",
    display_timezone: str = "Asia/Tokyo",
) -> TimeContext:
    now_utc = _aware_utc(now)
    character_time = now_utc.astimezone(ZoneInfo(character_timezone))
    display_time = now_utc.astimezone(ZoneInfo(display_timezone))
    local_time = character_time.timetz().replace(tzinfo=None)
    is_late_night = local_time.hour >= 23 or local_time.hour < 6
    ordinary_workday = character_time.weekday() < 5
    in_work_hours = ordinary_workday and (
        (local_time.hour, local_time.minute) >= (9, 30)
        and (local_time.hour, local_time.minute) < (18, 30)
    )
    activity_scene = "work_time" if in_work_hours else "rest_time"
    return TimeContext(character_time, display_time, is_late_night, activity_scene)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("state timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _toward_zero(value: int, amount: int) -> int:
    if value > 0:
        return max(0, value - amount)
    if value < 0:
        return min(0, value + amount)
    return 0


def _toward(value: int, target: int, amount: int) -> int:
    if value < target:
        return min(target, value + amount)
    if value > target:
        return max(target, value - amount)
    return value
