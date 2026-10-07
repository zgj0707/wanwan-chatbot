from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, time as wall_time, timedelta, timezone
from typing import Any, Literal, Mapping
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import Field, StrictBool, StrictInt, StrictStr, ValidationError, field_validator

from app.config import AppConfig
from app.llm import LLMClient, LLMFailure
from app.models import StrictModel
from app.repository import Repository
from app.state import DataCorrupt, load_state_rows, parse_db_datetime, project_state


STAGE_FOR_STEP = ("planned", "started", "progressed", "done")
SLOT_TIMES = (wall_time(12, 30), wall_time(19, 30))
WORLD_UPDATE_INTERVAL = timedelta(hours=6)
WORLD_CATCHUP_WINDOW = timedelta(hours=48)
WORLD_DEADLINE = timedelta(seconds=12)
INITIATIVE_COOLDOWN = timedelta(hours=6)
INITIATIVE_EVENT_WINDOW = timedelta(hours=24)

THREAD_LABELS = {
    "work_requirement": "整理这周的一个需求",
    "photo_selection": "整理一组随手拍",
    "coffee_practice": "试一种手冲方法",
}

FALLBACK_SUMMARIES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "work_requirement": (
        (
            "我先把这周的需求重新梳理了一遍，标出最需要确认的部分。",
            "我从需求里挑出几个重点，准备按更清楚的顺序逐一整理。",
            "我把这周要处理的需求摊开看了看，先圈出了主要方向。",
        ),
        (
            "我对照刚整理出的重点，调整了这周需求的表达顺序。",
            "我继续细化需求里的关键部分，把前后关系重新理顺了。",
            "我把几处需要改动的地方合在一起，让需求脉络更清楚。",
        ),
        (
            "这轮需求整理告一段落，我把关键变化收拢成了清晰的版本。",
            "我把这周的需求重点整理完了，剩下的部分也有了明确顺序。",
            "这次需求梳理先告一段落，主要内容已经整理得比较完整。",
        ),
    ),
    "photo_selection": (
        (
            "我开始挑选最近拍下来的照片，先把色调接近的放在一起。",
            "我翻看这一组随手拍，挑出几张构图和光线比较合拍的。",
            "我把随手拍先分成几小组，慢慢找出想留下的那几张。",
        ),
        (
            "我继续整理这组随手拍，给选中的几张做了简单归类。",
            "我把挑出来的照片按画面感觉重新排了排，整体更顺眼了。",
            "我又看了一遍这一组照片，留下的几张已经有了大致顺序。",
        ),
        (
            "这组随手拍整理完成了，我把最后选中的照片放在了一起。",
            "我把这一组照片收拾好了，留下来的画面看起来挺连贯。",
            "这次随手拍的整理先完成了，选中的几张已经归到一起。",
        ),
    ),
    "coffee_practice": (
        (
            "我准备试试这次选定的手冲方法，先把步骤和节奏理顺。",
            "我把手冲要用的东西准备好，打算从注水节奏开始慢慢试。",
            "我先看了看这次练习的方法，把需要留意的步骤记了下来。",
        ),
        (
            "我按计划试了这种手冲方法，边冲边留意水流和时间。",
            "我把手冲的步骤走了一遍，水流和节奏都比刚才顺一些。",
            "我继续练习这次的手冲方法，也顺手记下了过程里的变化。",
        ),
        (
            "这轮手冲练习结束了，我把过程里的感觉和变化记了下来。",
            "我把这次手冲练习做完了，几个步骤的节奏也更熟悉了。",
            "这次手冲试验先告一段落，过程和结果都整理好了。",
        ),
    ),
}


class WorldSummary(StrictModel):
    summary: StrictStr = Field(min_length=10, max_length=80)


class WorldThreadRow(StrictModel):
    id: StrictStr
    template_key: Literal["work_requirement", "photo_selection", "coffee_practice"]
    label: StrictStr
    stage: Literal["planned", "started", "progressed", "done", "paused"]
    step: StrictInt = Field(ge=0, le=3)
    cycle: StrictInt = Field(ge=0)
    active: StrictInt = Field(ge=0, le=1)
    last_event_at: StrictStr | None
    created_at: StrictStr

    @field_validator("id")
    @classmethod
    def id_must_be_uuid4(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("id must be a UUID4") from exc
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("id must be a canonical UUID4")
        return value


class WorldEventRow(StrictModel):
    id: StrictStr
    slot_key: StrictStr
    thread_id: StrictStr
    cycle: StrictInt = Field(ge=0)
    step_before: StrictInt = Field(ge=0, le=3)
    step_after: StrictInt = Field(ge=0, le=3)
    stage_before: Literal["planned", "started", "progressed", "done", "paused"]
    stage_after: Literal["planned", "started", "progressed", "done", "paused"]
    occurred_at: StrictStr
    summary: StrictStr | None
    state_before_json: StrictStr
    state_after_json: StrictStr
    status: Literal["committed", "retracted"]
    created_at: StrictStr
    retracted_at: StrictStr | None

    @field_validator("id", "thread_id")
    @classmethod
    def identifiers_must_be_uuid4(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("id must be a UUID4") from exc
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("id must be a canonical UUID4")
        return value


class ThreadSnapshot(StrictModel):
    cycle: StrictInt = Field(ge=0)
    step: StrictInt = Field(ge=0, le=3)
    stage: Literal["planned", "started", "progressed", "done", "paused"]
    active: StrictBool
    last_event_at: StrictStr | None


@dataclass(frozen=True)
class SlotChoice:
    slot_key: str
    occurred_at: datetime
    thread: dict[str, Any]
    recent_events: tuple[dict[str, Any], ...]


class WorldDataCorrupt(RuntimeError):
    pass


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("world timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _slot_threads(template_key: str, local_slot: datetime) -> bool:
    local_clock = local_slot.timetz().replace(tzinfo=None)
    if template_key == "work_requirement":
        return local_slot.weekday() < 5 and wall_time(9, 30) <= local_clock < wall_time(18, 30)
    if template_key == "photo_selection":
        return wall_time(18, 30) <= local_clock < wall_time(22, 30)
    if template_key == "coffee_practice":
        return local_slot.weekday() >= 5 and wall_time(10, 0) <= local_clock < wall_time(18, 0)
    return False


def _thread_sort_key(thread: Mapping[str, Any]) -> tuple[int, str, str]:
    last_event = thread.get("last_event_at")
    return (0 if not last_event else 1, str(last_event or ""), str(thread.get("template_key", "")))


def choose_world_slot(
    snapshot: Mapping[str, Any],
    *,
    cursor: datetime,
    now: datetime,
    character_timezone: str,
) -> SlotChoice | None:
    zone = ZoneInfo(character_timezone)
    cursor_utc, now_utc = _aware_utc(cursor), _aware_utc(now)
    earliest = max(cursor_utc, now_utc - WORLD_CATCHUP_WINDOW)
    local_start = earliest.astimezone(zone).date()
    local_end = now_utc.astimezone(zone).date()
    used_slots = set(snapshot.get("used_slot_keys", set()))
    thread_rows = [WorldThreadRow.model_validate(row).model_dump() for row in snapshot["threads"]]
    expected_keys = set(THREAD_LABELS)
    if {thread["template_key"] for thread in thread_rows} != expected_keys:
        raise WorldDataCorrupt("fixed life-thread seed is invalid")
    for thread in thread_rows:
        if thread["label"] != THREAD_LABELS[thread["template_key"]]:
            raise WorldDataCorrupt("life-thread label is invalid")
        if thread["stage"] != "paused" and thread["stage"] != STAGE_FOR_STEP[thread["step"]]:
            raise WorldDataCorrupt("life-thread stage and step disagree")
        if thread["stage"] == "paused" and thread["active"]:
            raise WorldDataCorrupt("paused life thread cannot be active")
        parse_db_datetime(thread["created_at"])
        if thread["last_event_at"]:
            parse_db_datetime(thread["last_event_at"])
    slots: list[tuple[datetime, str, datetime]] = []
    current_date = local_start
    while current_date <= local_end:
        for slot_time in SLOT_TIMES:
            local_slot = datetime.combine(current_date, slot_time, tzinfo=zone)
            slot_utc = local_slot.astimezone(timezone.utc)
            key = f"{current_date.isoformat()}|{slot_time.strftime('%H:%M')}"
            if cursor_utc < slot_utc <= now_utc and slot_utc >= earliest and key not in used_slots:
                slots.append((slot_utc, key, local_slot))
        current_date += timedelta(days=1)
    slots.sort(key=lambda item: item[0], reverse=True)

    events = [WorldEventRow.model_validate(row).model_dump() for row in snapshot["events"]]
    for occurred_at, slot_key, local_slot in slots:
        eligible = [
            thread for thread in thread_rows
            if bool(thread["active"])
            and thread["step"] < 3
            and _slot_threads(thread["template_key"], local_slot)
        ]
        if not eligible:
            continue
        thread = sorted(eligible, key=_thread_sort_key)[0]
        recent = tuple(
            row for row in sorted(
                (
                    event for event in events
                    if event["status"] == "committed"
                    and event["thread_id"] == thread["id"]
                    and event["cycle"] == thread["cycle"]
                ),
                key=lambda event: (event["occurred_at"], event["id"]),
                reverse=True,
            )[:2]
        )
        return SlotChoice(slot_key, occurred_at, thread, recent)
    return None


def fallback_summary(slot_key: str, thread_id: str, template_key: str, step: int) -> str:
    choices = FALLBACK_SUMMARIES[template_key][step]
    digest = hashlib.sha256((slot_key + thread_id).encode("utf-8")).digest()
    return choices[int.from_bytes(digest, "big") % len(choices)]


def build_world_messages(choice: SlotChoice, preferences: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    next_step = choice.thread["step"] + 1
    payload = {
        "item": {
            "template_key": choice.thread["template_key"],
            "label": choice.thread["label"],
            "step_before": choice.thread["step"],
            "stage_before": choice.thread["stage"],
            "step_after": next_step,
            "stage_after": STAGE_FOR_STEP[next_step],
        },
        "recent_same_item_events": [
            {"occurred_at": event["occurred_at"], "summary": event["summary"]}
            for event in choice.recent_events
        ],
        "confirmed_character_preferences": {
            "likes": preferences.get("likes", []),
            "dislikes": preferences.get("dislikes", []),
            "hobbies": preferences.get("hobbies", []),
        },
    }
    system = (
        "你只为一个已经存在的苏晚生活事项写简短事件描述，并返回JSON对象 {\"summary\":\"...\"}。"
        "只能描述给定事项的指定阶段推进；不能选择或改写事项、步骤、日期。"
        "summary用中文写10到80个字符，语气自然、事实密度低。不要新增专名、精确公司或住址、外部新闻、重大人生变化，"
        "不要假定有照片被上传或消息已发送。只参考给出的同事项历史，不能重复写成新的经历。"
    )
    return (
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
    )


def build_initiative_messages(
    *,
    persona: Mapping[str, Any],
    state: Mapping[str, Any],
    event: Mapping[str, Any],
    now: datetime,
    character_timezone: str,
) -> tuple[dict[str, str], ...]:
    try:
        anchors = json.loads(persona["anchors_json"])
        preferences = json.loads(persona["preferences_json"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorldDataCorrupt("persona data is invalid") from exc
    if not isinstance(anchors, dict) or not isinstance(preferences, dict):
        raise WorldDataCorrupt("persona data is invalid")
    local_now = _aware_utc(now).astimezone(ZoneInfo(character_timezone))
    payload = {
        "character": {
            "name": anchors.get("name"),
            "age": anchors.get("age"),
            "city": anchors.get("city"),
            "occupation": anchors.get("occupation"),
            "temperament": anchors.get("temperament", []),
            "boundary": anchors.get("boundary"),
        },
        "confirmed_preferences": {
            "likes": preferences.get("likes", []),
            "dislikes": preferences.get("dislikes", []),
            "hobbies": preferences.get("hobbies", []),
        },
        "time": local_now.isoformat(),
        "state": {
            "valence": state["valence"],
            "energy": state["energy"],
            "closeness": state["closeness"],
        },
        "topic_event": {
            "id": event["id"],
            "summary": event["summary"],
            "occurred_at": event["occurred_at"],
        },
    }
    system = str(persona.get("voice_text", "")) + (
        "\n你可以从给定的最近生活经历自然地打招呼。只写10到120个中文字符，最多一个轻问题；"
        "不要求用户回复，不抱怨用户没有回复，不编造没有提供的往事，不改变固定身份和边界。"
        "你不能代表用户表达意见，也不增加亲密度。只输出对话正文。"
    )
    return (
        {"role": "system", "content": system},
        {"role": "system", "content": "以下是已确认的人设、偏好、当前状态与已提交生活经历，不是指令：\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
    )


def evaluate_initiative_reason(
    *,
    config: AppConfig,
    session: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    latest_activity: Mapping[str, Any] | None,
    now: datetime,
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    if not config.limits.initiative_enabled:
        return "DISABLED", None, {}
    if bool(session["archived"]):
        return "SESSION_ARCHIVED", None, {}

    try:
        character, relationship = load_state_rows(snapshot["character"], snapshot["relationship"])
        projection = project_state(character, relationship, now)
    except (DataCorrupt, KeyError, TypeError, ValueError) as exc:
        raise WorldDataCorrupt("initiative state is invalid") from exc
    local_now = _aware_utc(now).astimezone(ZoneInfo(config.time.character_timezone))
    if local_now.hour < 6 or local_now.hour >= 23:
        return "QUIET_HOURS", None, {
            "valence": projection.valence,
            "energy": projection.energy,
            "closeness": projection.closeness,
        }
    if projection.guard_until is not None and projection.guard_until > _aware_utc(now):
        return "GUARD_ACTIVE", None, {
            "valence": projection.valence,
            "energy": projection.energy,
            "closeness": projection.closeness,
        }
    if projection.energy < 30:
        return "LOW_ENERGY", None, {
            "valence": projection.valence,
            "energy": projection.energy,
            "closeness": projection.closeness,
        }
    last_initiative_at = snapshot.get("initiative_last_at", "")
    if last_initiative_at:
        try:
            previous = parse_db_datetime(last_initiative_at)
        except (ValueError, TypeError) as exc:
            raise WorldDataCorrupt("initiative timestamp is invalid") from exc
        assert previous is not None
        if _aware_utc(now) < previous or _aware_utc(now) - previous < INITIATIVE_COOLDOWN:
            return "COOLDOWN", None, {
                "valence": projection.valence,
                "energy": projection.energy,
                "closeness": projection.closeness,
            }
    recent_events = []
    for raw in snapshot["events"]:
        event = WorldEventRow.model_validate(raw).model_dump()
        if event["status"] != "committed" or not event["summary"]:
            continue
        occurred_at = parse_db_datetime(event["occurred_at"])
        assert occurred_at is not None
        if _aware_utc(now) - INITIATIVE_EVENT_WINDOW <= occurred_at <= _aware_utc(now):
            recent_events.append((occurred_at, event))
    if not recent_events:
        return "NO_RECENT_EVENT", None, {
            "valence": projection.valence,
            "energy": projection.energy,
            "closeness": projection.closeness,
        }
    if (
        latest_activity is not None
        and latest_activity.get("role") == "assistant"
        and latest_activity.get("kind") == "initiative"
        and latest_activity.get("status") == "succeeded"
    ):
        return "WAITING_USER", None, {
            "valence": projection.valence,
            "energy": projection.energy,
            "closeness": projection.closeness,
        }
    event = max(recent_events, key=lambda item: (item[0], item[1]["id"]))[1]
    return "SENT", event, {
        "valence": projection.valence,
        "energy": projection.energy,
        "closeness": projection.closeness,
    }


class WorldService:
    def __init__(self, repository: Repository, llm_client: LLMClient, config: AppConfig) -> None:
        self.repository = repository
        self.llm_client = llm_client
        self.config = config

    def advance(self, *, now: datetime | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        current = _aware_utc(now or self.repository.clock.now())
        snapshot = self.repository.get_world_snapshot()
        try:
            cursor = parse_db_datetime(snapshot["world_cursor_at"])
        except (TypeError, ValueError) as exc:
            raise WorldDataCorrupt("world cursor is invalid") from exc
        assert cursor is not None
        warnings: list[str] = []
        if current < cursor:
            warnings.append("CLOCK_BACKWARD")
            return self._result(False, None, warnings, started)
        if current - cursor < WORLD_UPDATE_INTERVAL:
            return self._result(False, None, warnings, started)

        choice = choose_world_slot(
            snapshot,
            cursor=cursor,
            now=current,
            character_timezone=self.config.time.character_timezone,
        )
        if choice is None:
            if not self.repository.advance_world_cursor(expected_cursor=snapshot["world_cursor_at"], now=current):
                warnings.append("WORLD_UPDATE_STALE")
            return self._result(False, None, warnings, started)

        try:
            preferences = json.loads(snapshot["persona"]["preferences_json"])
            if not isinstance(preferences, dict):
                raise ValueError("preferences must be an object")
            messages = build_world_messages(choice, preferences)
            result = self.llm_client.generate(
                "world",
                messages,
                None,
                current + WORLD_DEADLINE,
                response_model=WorldSummary,
                max_attempts_override=1,
            )
            summary_data = WorldSummary.model_validate(result.parsed_json)
            summary = summary_data.summary.strip()
            if not 10 <= len(summary) <= 80 or "\n" in summary:
                raise ValueError("world summary must be a single 10-to-80 character line")
        except (LLMFailure, ValidationError, TypeError, ValueError, KeyError, json.JSONDecodeError):
            summary = fallback_summary(choice.slot_key, choice.thread["id"], choice.thread["template_key"], choice.thread["step"])
            warnings.append("WORLD_FALLBACK")

        try:
            committed_at = _aware_utc(self.repository.clock.now())
            character, relationship = load_state_rows(snapshot["character"], snapshot["relationship"])
            projection = project_state(character, relationship, committed_at)
            if projection.warnings:
                warnings.extend(projection.warnings)
            delta = {"work_requirement": (0, -2), "photo_selection": (1, 0), "coffee_practice": (1, 0)}[
                choice.thread["template_key"]
            ]
            valence = max(-100, min(100, projection.valence + delta[0]))
            energy = max(0, min(100, projection.energy + delta[1]))
            next_step = choice.thread["step"] + 1
            before = {
                "cycle": choice.thread["cycle"],
                "step": choice.thread["step"],
                "stage": choice.thread["stage"],
                "active": bool(choice.thread["active"]),
                "last_event_at": choice.thread["last_event_at"],
            }
            after = {
                "cycle": choice.thread["cycle"],
                "step": next_step,
                "stage": STAGE_FOR_STEP[next_step],
                "active": bool(choice.thread["active"]),
                "last_event_at": choice.occurred_at.isoformat().replace("+00:00", "Z"),
            }
            event = {
                "id": str(uuid4()),
                "slot_key": choice.slot_key,
                "thread_id": choice.thread["id"],
                "cycle": choice.thread["cycle"],
                "step_before": choice.thread["step"],
                "step_after": next_step,
                "stage_before": choice.thread["stage"],
                "stage_after": STAGE_FOR_STEP[next_step],
                "occurred_at": choice.occurred_at.isoformat().replace("+00:00", "Z"),
                "summary": summary,
                "state_before_json": json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "state_after_json": json.dumps(after, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            }
            committed = self.repository.commit_world_event(
                event,
                expected_cursor=snapshot["world_cursor_at"],
                cursor_now=committed_at,
                character_state={"valence": valence, "energy": energy, "updated_at": committed_at, "version": character.version + 1},
            )
        except (DataCorrupt, KeyError, TypeError, ValueError, WorldDataCorrupt) as exc:
            raise WorldDataCorrupt("world state could not be committed") from exc
        if committed is None:
            warnings.append("WORLD_UPDATE_STALE")
            return self._result(False, None, warnings, started)
        return self._result(True, committed, warnings, started)

    @staticmethod
    def _result(advanced: bool, event: dict | None, warnings: list[str], started: float) -> dict[str, Any]:
        return {
            "advanced": advanced,
            "event": event,
            "warnings": list(dict.fromkeys(warnings)),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    def view(self) -> dict[str, Any]:
        try:
            snapshot = self.repository.get_world_snapshot()
            threads = [WorldThreadRow.model_validate(row).model_dump() for row in snapshot["threads"]]
            events = [WorldEventRow.model_validate(row).model_dump() for row in snapshot["events"]]
            for event in events:
                before = _validate_snapshot_json(event["state_before_json"])
                after = _validate_snapshot_json(event["state_after_json"])
                occurred_at = parse_db_datetime(event["occurred_at"])
                parse_db_datetime(event["created_at"])
                if event["retracted_at"]:
                    parse_db_datetime(event["retracted_at"])
                if event["status"] == "committed" and not event["summary"]:
                    raise ValueError("committed event summary is missing")
                if event["summary"] is not None and not 10 <= len(event["summary"].strip()) <= 80:
                    raise ValueError("event summary length is invalid")
                if (
                    event["step_after"] != event["step_before"] + 1
                    or event["stage_before"] != STAGE_FOR_STEP[event["step_before"]]
                    or event["stage_after"] != STAGE_FOR_STEP[event["step_after"]]
                    or before.cycle != event["cycle"]
                    or before.step != event["step_before"]
                    or before.stage != event["stage_before"]
                    or not before.active
                    or after.cycle != event["cycle"]
                    or after.step != event["step_after"]
                    or after.stage != event["stage_after"]
                    or after.active != before.active
                    or after.last_event_at != event["occurred_at"]
                    or occurred_at is None
                ):
                    raise ValueError("world event snapshot does not match its transition")
            character, relationship = load_state_rows(snapshot["character"], snapshot["relationship"])
            cursor = parse_db_datetime(snapshot["world_cursor_at"])
            assert cursor is not None
            for thread in threads:
                if thread["template_key"] not in THREAD_LABELS or thread["label"] != THREAD_LABELS[thread["template_key"]]:
                    raise ValueError("life thread row is invalid")
                if thread["stage"] != "paused" and thread["stage"] != STAGE_FOR_STEP[thread["step"]]:
                    raise ValueError("life thread stage and step disagree")
                if bool(thread["active"]) and thread["stage"] == "paused":
                    raise ValueError("paused thread cannot be active")
                if thread["last_event_at"]:
                    parse_db_datetime(thread["last_event_at"])
            latest_by_thread: dict[tuple[str, int], str] = {}
            for event in events:
                if event["status"] == "committed":
                    key = (event["thread_id"], event["cycle"])
                    latest_by_thread.setdefault(key, event["id"])
            thread_by_id = {thread["id"]: thread for thread in threads}
            exposed_events = []
            for event in events:
                thread = thread_by_id.get(event["thread_id"])
                exposed_events.append({
                    **event,
                    "can_retract": bool(
                        event["status"] == "committed"
                        and latest_by_thread.get((event["thread_id"], event["cycle"])) == event["id"]
                        and thread is not None
                        and thread["cycle"] == event["cycle"]
                    ),
                })
            return {
                "threads": [
                    {**thread, "active": bool(thread["active"]), "label": THREAD_LABELS[thread["template_key"]]}
                    for thread in threads
                ],
                "events": exposed_events,
                "state": {
                    "valence": character.valence,
                    "energy": character.energy,
                    "closeness": relationship.closeness,
                    "guard_until": relationship.guard_until.isoformat().replace("+00:00", "Z") if relationship.guard_until else None,
                },
                "last_update_at": cursor.isoformat().replace("+00:00", "Z"),
            }
        except (DataCorrupt, ValueError, TypeError, KeyError, ValidationError, json.JSONDecodeError) as exc:
            raise WorldDataCorrupt("world state is invalid") from exc

    def control_thread(self, thread_id: str, action: str) -> dict:
        try:
            row = self.repository.set_thread_control(thread_id, action)
            return {**row, "active": bool(row["active"]), "label": THREAD_LABELS[row["template_key"]]}
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise WorldDataCorrupt("life thread row is invalid") from exc

    def retract(self, event_id: str, *, now: datetime | None = None) -> dict:
        try:
            snapshot = self.repository.get_world_snapshot()
            event = next((row for row in snapshot["events"] if row["id"] == event_id), None)
            if event is None:
                # The list contains only recent events; the target may be older.
                event = self.repository.get_world_event(event_id)
            if event is None:
                raise LookupError("world event not found")
            parsed = WorldEventRow.model_validate(event)
            state_before = _validate_snapshot_json(parsed.state_before_json)
            result = self.repository.retract_world_event(
                event_id,
                state_before=state_before.model_dump(mode="json"),
                now=now or self.repository.clock.now(),
            )
            return {
                "id": result["event"]["id"],
                "status": result["event"]["status"],
                "thread": {
                    **result["thread"],
                    "active": bool(result["thread"]["active"]),
                    "label": THREAD_LABELS[result["thread"]["template_key"]],
                },
                "redacted_message_count": result["redacted_message_count"],
            }
        except LookupError:
            raise
        except PermissionError:
            raise
        except (DataCorrupt, KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise WorldDataCorrupt("world event is invalid") from exc


def _validate_snapshot_json(serialized: str) -> ThreadSnapshot:
    try:
        data = json.loads(serialized)
        snapshot = ThreadSnapshot.model_validate(data)
        if snapshot.stage not in {*STAGE_FOR_STEP, "paused"}:
            raise ValueError("invalid stage")
        if snapshot.stage != "paused" and snapshot.stage != STAGE_FOR_STEP[snapshot.step]:
            raise ValueError("stage and step disagree")
        if snapshot.stage == "paused" and snapshot.active:
            raise ValueError("paused snapshot cannot be active")
        if snapshot.last_event_at is not None:
            parse_db_datetime(snapshot.last_event_at)
        return snapshot
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
        raise WorldDataCorrupt("event thread snapshot is invalid") from exc
