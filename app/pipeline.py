from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from app.chat_policy import clean_chat_reply, reply_boundary_violation
from app.config import AppConfig
from app.errors import APIProblem, problem_for_code
from app.llm import LLMClient, LLMFailure
from app.memory import MemoryDataUnavailable, retrieve_facts, validate_fact_candidates
from app.models import ChatRequest, InitiativeRequest, PersonaProposal
from app.prompt import HistoryTurn, PromptDataCorrupt, PromptTooLarge, build_prompt
from app.repository import Repository, mutation_guard
from app.attention import build_disclosure, select_relevant
from app.state import DataCorrupt, apply_analysis_delta, load_state_rows, project_state, time_context
from app.world import WorldDataCorrupt, build_initiative_messages, evaluate_initiative_reason


WorldUpdater = Callable[[str, datetime], object]


def request_hash(session_id: str, text: str, *, kind: str = "turn") -> str:
    if kind not in {"turn", "initiative"}:
        raise ValueError("unsupported operation kind")
    canonical = json.dumps(
        {"kind": kind, "session_id": session_id, "text": text},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _run_world_update(
    world_updater: WorldUpdater | None,
    *,
    session_id: str,
    now: datetime,
    warnings: list[str],
) -> int:
    if world_updater is None:
        return 0
    started = time.perf_counter()
    try:
        result = world_updater(session_id, now)
        if isinstance(result, Mapping):
            result_warnings = result.get("warnings", ())
            if isinstance(result_warnings, (list, tuple)):
                warnings.extend(str(item) for item in result_warnings)
            duration = result.get("elapsed_ms")
            if isinstance(duration, int) and duration >= 0:
                return duration
        elif isinstance(result, (list, tuple)):
            warnings.extend(str(item) for item in result)
    except Exception:
        warnings.append("WORLD_UPDATE_FAILED")
    return round((time.perf_counter() - started) * 1000)


def _status_response(repository: Repository, operation: dict, *, replayed: bool) -> dict:
    messages = repository.get_operation_messages(operation["id"])
    try:
        result_meta = json.loads(operation["result_meta_json"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        result_meta = {}
    return {
        "operation_id": operation["id"],
        "status": operation["status"],
        "user_message": messages["user"],
        "assistant_message": messages["assistant"],
        "warnings": json.loads(operation["warnings_json"]),
        "elapsed_ms": operation["elapsed_ms"],
        "call_count": operation["call_count"],
        "world_update_ms": result_meta.get("world_update_ms", 0) if isinstance(result_meta, dict) else 0,
        "replayed": replayed,
    }


def _check_existing(operation: dict | None, request: ChatRequest, digest: str) -> dict | None:
    if operation is None:
        return None
    if (
        operation["kind"] != "turn"
        or operation["session_id"] != request.session_id
        or operation["request_hash"] != digest
    ):
        raise APIProblem(409, "OPERATION_CONFLICT", "操作编号已用于不同请求。", operation_id=request.operation_id)
    return operation


def _completed_response(repository: Repository, operation: dict) -> dict:
    result = _status_response(repository, operation, replayed=True)
    result["replayed"] = True
    return result


def _return_or_raise_for_existing(repository: Repository, operation: dict, request: ChatRequest) -> dict:
    if operation["status"] == "succeeded":
        return _completed_response(repository, operation)
    if operation["status"] == "pending":
        return {"operation_id": operation["id"], "status": "pending"}
    if not request.retry:
        raise problem_for_code(operation["error_code"] or "UPSTREAM_ERROR", operation["id"])
    return {}


def _history(messages: list[dict]) -> list[HistoryTurn]:
    grouped: list[HistoryTurn] = []
    active_id: str | None = None
    active_messages: list[dict] = []
    active_kind = "turn"
    active_redacted = False

    def flush() -> None:
        if active_id is None or not active_messages:
            return
        grouped.append(
            HistoryTurn(
                messages=tuple(
                    {"role": item["role"], "content": item["content"] if item["content"] is not None else ""}
                    for item in active_messages
                ),
                kind=active_kind,
                complete=True,
                redacted=active_redacted,
                status="succeeded",
            )
        )

    for item in messages:
        operation_id = item["operation_id"]
        if active_id is not None and operation_id != active_id:
            flush()
            active_messages = []
            active_redacted = False
        if operation_id != active_id:
            active_id = operation_id
            active_kind = item["kind"]
        active_messages.append(item)
        active_redacted = active_redacted or bool(item["redacted"]) or item["content"] is None
    flush()
    return grouped


def handle_turn(
    repository: Repository,
    llm_client: LLMClient,
    config: AppConfig,
    request: ChatRequest,
    *,
    world_updater: WorldUpdater | None = None,
) -> dict:
    digest = request_hash(request.session_id, request.text)
    operation = _check_existing(repository.get_operation(request.operation_id), request, digest)
    if operation is not None:
        immediate = _return_or_raise_for_existing(repository, operation, request)
        if immediate:
            return immediate

    try:
        guard = mutation_guard()
        guard.__enter__()
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY", request.operation_id) from exc
        raise
    try:
        # Recheck after acquiring the global lock in case another request completed meanwhile.
        operation = _check_existing(repository.get_operation(request.operation_id), request, digest)
        if operation is not None:
            immediate = _return_or_raise_for_existing(repository, operation, request)
            if immediate:
                return immediate

        now = repository.clock.now()
        session = repository.get_session(request.session_id)
        if session is None:
            raise problem_for_code("SESSION_NOT_FOUND", request.operation_id)
        if session["archived"]:
            raise problem_for_code("SESSION_ARCHIVED", request.operation_id)

        warnings: list[str] = []
        started = time.perf_counter()
        initial_metric_count = len(llm_client.metrics)
        world_update_ms = 0
        if world_updater is not None and config.limits.auto_world_update:
            world_update_ms = _run_world_update(
                world_updater,
                session_id=request.session_id,
                now=now,
                warnings=warnings,
            )

        if operation is None:
            repository.create_pending_operation(
                operation_id=request.operation_id,
                session_id=request.session_id,
                request_hash=digest,
                request_text=request.text,
                now=now,
            )
        else:
            repository.requeue_operation(
                operation_id=request.operation_id,
                session_id=request.session_id,
                request_hash=digest,
                request_text=request.text,
                now=now,
            )

        pending = True
        call_count = 0

        def finish_failure(problem: APIProblem) -> None:
            nonlocal call_count
            call_count = max(call_count, len(llm_client.metrics) - initial_metric_count)
            repository.fail_operation(
                operation_id=request.operation_id,
                error_code=problem.code,
                warnings=warnings,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                call_count=call_count,
                now=repository.clock.now(),
            )

        try:
            if config.mode == "real" and (
                not llm_client.has_credentials("analysis") or not llm_client.has_credentials("chat")
            ):
                raise problem_for_code("CONFIG_MISSING", request.operation_id)

            deadline = repository.clock.now() + timedelta(seconds=45)
            try:
                context = repository.load_turn_context(request.session_id)
                persona = context["persona"]
                anchors = json.loads(persona["anchors_json"])
                preferences = json.loads(persona["preferences_json"])
                character, relationship = load_state_rows(context["character"], context["relationship"])
                projection = project_state(character, relationship, now)
            except LookupError as exc:
                raise problem_for_code("SESSION_NOT_FOUND", request.operation_id) from exc
            except PermissionError as exc:
                raise problem_for_code("SESSION_ARCHIVED", request.operation_id) from exc
            except (DataCorrupt, json.JSONDecodeError, TypeError, KeyError, RuntimeError) as exc:
                raise problem_for_code("DATA_CORRUPT", request.operation_id) from exc

            warnings.extend(projection.warnings)
            memory_facts = []
            if context.get("memory_unavailable"):
                warnings.append("MEMORY_UNAVAILABLE")
            else:
                try:
                    memory_facts = retrieve_facts(
                        request.text,
                        context["facts"],
                        top_k=config.limits.memory_top_k,
                        now=now,
                    )
                except MemoryDataUnavailable:
                    warnings.append("MEMORY_UNAVAILABLE")
            analysis = llm_client.analyze(request.operation_id, deadline, request.text)
            warnings.extend(analysis.warnings)
            if analysis.error_code and "ANALYSIS_DEGRADED" not in warnings:
                warnings.append("ANALYSIS_DEGRADED")
            persisted_state = apply_analysis_delta(projection, analysis.result.state_delta, now)

            # 注意力层（CLR 一阶段）：话题相关性筛选 + 披露候选 + 状态持久化。
            # 事实检索已按 user_text 相关性排序；这里只对世界事件做话题过滤。
            attention_topic = analysis.result.topic or "闲聊"
            attention_enabled = os.environ.get("SUWAN_ATTENTION_FILTER", "1") == "1"
            if attention_enabled:
                try:
                    attention_facts, attention_events, attention_debug = select_relevant(
                        topic_hint=attention_topic,
                        facts=memory_facts,
                        events=context["events"],
                        facts_top_k=config.limits.memory_top_k,
                        world_events_top_k=config.limits.world_events_top_k,
                    )
                except Exception:
                    attention_facts, attention_events, attention_debug = memory_facts, context["events"], {}
            else:
                attention_facts, attention_events, attention_debug = memory_facts, context["events"], {"disabled": True}
            disclosure = build_disclosure(
                topic=attention_topic,
                events=context["events"],
                recent_topics=[],
            )
            try:
                repository.update_attention(
                    topic=attention_topic,
                    current_focus=None,
                    disclosure=disclosure,
                    now=now,
                )
            except Exception:
                warnings.append("MEMORY_UNAVAILABLE")
            fact_candidates = validate_fact_candidates(analysis.result.fact_candidates, request.text)
            if len(fact_candidates) != len(analysis.result.fact_candidates):
                warnings.append("MEMORY_CANDIDATE_REJECTED")
            persona_proposals = tuple(
                PersonaProposal.model_validate(proposal)
                for proposal in analysis.result.persona_proposals
            )
            projected_for_chat = replace(
                projection,
                valence=persisted_state.valence,
                energy=persisted_state.energy,
                closeness=persisted_state.closeness,
                guard_until=persisted_state.guard_until,
                character_updated_at=persisted_state.character_updated_at,
            )
            local_time = time_context(
                now,
                character_timezone=config.time.character_timezone,
                display_timezone=config.time.display_timezone,
            )
            try:
                bundle = build_prompt(
                    persona_version_id=persona["id"],
                    anchors=anchors,
                    preferences=preferences,
                    voice_text=persona["voice_text"],
                    time=local_time,
                    state=projected_for_chat,
                    life_threads=context["threads"],
                    world_events=attention_events,
                    facts=memory_facts,
                    history=_history(context["messages"]),
                    user_text=request.text,
                    input_budget_units=config.profiles.chat.input_budget_units,
                    history_turns=config.limits.history_turns,
                    memory_top_k=config.limits.memory_top_k,
                    world_events_top_k=config.limits.world_events_top_k,
                )
            except (PromptTooLarge, PromptDataCorrupt) as exc:
                raise problem_for_code(getattr(exc, "code", "CONTEXT_TOO_LARGE"), request.operation_id) from exc
            except (ValueError, TypeError, KeyError) as exc:
                raise problem_for_code("DATA_CORRUPT", request.operation_id) from exc

            try:
                guard_active = (
                    projected_for_chat.guard_until is not None
                    and projected_for_chat.guard_until > local_time.character_time
                )
                reply = llm_client.generate(
                    "chat",
                    bundle.messages,
                    request.operation_id,
                    deadline,
                    reply_validator=lambda text: reply_boundary_violation(
                        request.text,
                        text,
                        guard_active=guard_active,
                        world_events=context["events"],
                        confirmed_facts=[
                            fact for fact in memory_facts if fact.get("id") in bundle.fact_ids
                        ],
                    ),
                )
            except LLMFailure as exc:
                raise problem_for_code(exc.code, request.operation_id) from exc

            call_count = len(llm_client.metrics) - initial_metric_count
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            user_message, assistant_message = repository.complete_turn(
                operation_id=request.operation_id,
                session_id=request.session_id,
                user_text=request.text,
                assistant_text=clean_chat_reply(reply.text, request.text),
                now=now,
                state=persisted_state,
                persona_version_id=bundle.persona_version_id,
                fact_ids=bundle.fact_ids,
                event_ids=bundle.event_ids,
                warnings=warnings,
                elapsed_ms=elapsed_ms,
                call_count=call_count,
                world_update_ms=world_update_ms,
                fact_candidates=fact_candidates,
                persona_proposals=persona_proposals,
            )
            pending = False
            return {
                "operation_id": request.operation_id,
                "status": "succeeded",
                "user_message": user_message,
                "assistant_message": assistant_message,
                "warnings": warnings,
                "elapsed_ms": elapsed_ms,
                "call_count": call_count,
                "world_update_ms": world_update_ms,
                "replayed": False,
            }
        except APIProblem as problem:
            if pending:
                finish_failure(problem)
            raise
        except Exception as exc:
            problem = problem_for_code("DATABASE_ERROR", request.operation_id)
            if pending:
                finish_failure(problem)
            raise problem from exc
    finally:
        guard.__exit__(None, None, None)


def _initiative_response(repository: Repository, operation: dict, *, replayed: bool) -> dict:
    try:
        result_meta = json.loads(operation["result_meta_json"])
        warnings = json.loads(operation["warnings_json"])
        if not isinstance(result_meta, dict) or not isinstance(warnings, list):
            raise ValueError("initiative result metadata is invalid")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise problem_for_code("DATA_CORRUPT", operation["id"]) from exc
    messages = repository.get_operation_messages(operation["id"])
    sent = bool(result_meta.get("sent", False))
    reason = result_meta.get("reason")
    if reason not in {
        "SENT", "DISABLED", "QUIET_HOURS", "GUARD_ACTIVE", "LOW_ENERGY",
        "COOLDOWN", "NO_RECENT_EVENT", "WAITING_USER", "MODEL_FAILED",
    }:
        reason = "MODEL_FAILED" if operation["status"] in {"failed", "interrupted"} else None
    return {
        "operation_id": operation["id"],
        "status": operation["status"],
        "sent": sent,
        "reason": reason,
        "assistant_message": messages["assistant"],
        "warnings": warnings,
        "elapsed_ms": operation["elapsed_ms"],
        "call_count": operation["call_count"],
        "world_update_ms": max(0, result_meta.get("world_update_ms", 0)) if isinstance(result_meta.get("world_update_ms", 0), int) else 0,
        "replayed": replayed,
    }


def _initiative_reply_is_valid(text: str) -> bool:
    normalized = text.strip()
    if not 10 <= len(normalized) <= 120 or "\n" in normalized or "\r" in normalized:
        return False
    if normalized.count("?") + normalized.count("？") > 1:
        return False
    forbidden_pressure = ("不回我", "怎么不回", "为什么不回", "不理我", "快回我", "赶紧回", "等你回复")
    return not any(phrase in normalized for phrase in forbidden_pressure)


def handle_initiative(
    repository: Repository,
    llm_client: LLMClient,
    config: AppConfig,
    request: InitiativeRequest,
    *,
    world_updater: WorldUpdater | None = None,
) -> dict:
    digest = request_hash(request.session_id, "", kind="initiative")
    operation = repository.get_operation(request.operation_id)
    if operation is not None:
        if (
            operation["kind"] != "initiative"
            or operation["session_id"] != request.session_id
            or operation["request_hash"] != digest
        ):
            raise APIProblem(409, "OPERATION_CONFLICT", "操作编号已用于不同请求。", operation_id=request.operation_id)
        if operation["status"] == "pending":
            return {
                "operation_id": operation["id"],
                "status": "pending",
                "sent": False,
                "reason": None,
                "assistant_message": None,
                "replayed": True,
            }
        return _initiative_response(repository, operation, replayed=True)

    try:
        guard = mutation_guard()
        guard.__enter__()
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY", request.operation_id) from exc
        raise

    operation_created = False
    try:
        operation = repository.get_operation(request.operation_id)
        if operation is not None:
            if (
                operation["kind"] != "initiative"
                or operation["session_id"] != request.session_id
                or operation["request_hash"] != digest
            ):
                raise APIProblem(409, "OPERATION_CONFLICT", "操作编号已用于不同请求。", operation_id=request.operation_id)
            if operation["status"] == "pending":
                return {
                    "operation_id": operation["id"],
                    "status": "pending",
                    "sent": False,
                    "reason": None,
                    "assistant_message": None,
                    "replayed": True,
                }
            return _initiative_response(repository, operation, replayed=True)

        session = repository.get_session(request.session_id)
        if session is None:
            raise problem_for_code("SESSION_NOT_FOUND", request.operation_id)
        if session["archived"]:
            raise problem_for_code("SESSION_ARCHIVED", request.operation_id)

        total_started = time.perf_counter()
        world_warnings: list[str] = []
        world_update_ms = 0
        if world_updater is not None and config.limits.auto_world_update:
            world_update_ms = _run_world_update(
                world_updater,
                session_id=request.session_id,
                now=repository.clock.now(),
                warnings=world_warnings,
            )
        operation_now = repository.clock.now()
        repository.create_pending_operation(
            operation_id=request.operation_id,
            session_id=request.session_id,
            request_hash=digest,
            request_text=None,
            kind="initiative",
            now=operation_now,
        )
        operation_created = True
        initial_metric_count = len(llm_client.metrics)

        try:
            snapshot = repository.get_world_snapshot()
            latest_activity = repository.latest_session_activity(request.session_id)
            reason, event, projected_state = evaluate_initiative_reason(
                config=config,
                session=session,
                snapshot=snapshot,
                latest_activity=latest_activity,
                now=operation_now,
            )
            if reason == "SESSION_ARCHIVED":
                raise problem_for_code("SESSION_ARCHIVED", request.operation_id)
            if reason != "SENT":
                elapsed_ms = round((time.perf_counter() - total_started) * 1000)
                repository.complete_skipped_initiative(
                    operation_id=request.operation_id,
                    session_id=request.session_id,
                    reason=reason,
                    warnings=world_warnings,
                    now=repository.clock.now(),
                    elapsed_ms=elapsed_ms,
                    world_update_ms=world_update_ms,
                )
                completed = repository.get_operation(request.operation_id)
                assert completed is not None
                return _initiative_response(repository, completed, replayed=False)

            persona = snapshot["persona"]
            messages = build_initiative_messages(
                persona=persona,
                state=projected_state,
                event=event,
                now=operation_now,
                character_timezone=config.time.character_timezone,
            )
            deadline = repository.clock.now() + timedelta(seconds=20)
            try:
                reply = llm_client.generate(
                    "chat",
                    messages,
                    request.operation_id,
                    deadline,
                    max_attempts_override=1,
                    reply_validator=_initiative_reply_is_valid,
                )
            except LLMFailure as exc:
                call_count = len(llm_client.metrics) - initial_metric_count
                repository.fail_initiative(
                    operation_id=request.operation_id,
                    error_code=exc.code,
                    elapsed_ms=round((time.perf_counter() - total_started) * 1000),
                    call_count=call_count,
                    now=repository.clock.now(),
                    warnings=world_warnings,
                    world_update_ms=world_update_ms,
                )
                failed = repository.get_operation(request.operation_id)
                assert failed is not None
                return _initiative_response(repository, failed, replayed=False)

            call_count = len(llm_client.metrics) - initial_metric_count
            assistant_message = repository.complete_initiative(
                operation_id=request.operation_id,
                session_id=request.session_id,
                assistant_text=reply.text.strip(),
                persona_version_id=persona["id"],
                event_id=event["id"],
                now=repository.clock.now(),
                elapsed_ms=round((time.perf_counter() - total_started) * 1000),
                call_count=call_count,
                world_update_ms=world_update_ms,
            )
            completed = repository.get_operation(request.operation_id)
            assert completed is not None
            response = _initiative_response(repository, completed, replayed=False)
            response["assistant_message"] = assistant_message
            return response
        except APIProblem as problem:
            if operation_created:
                repository.fail_initiative(
                    operation_id=request.operation_id,
                    error_code=problem.code,
                    elapsed_ms=round((time.perf_counter() - total_started) * 1000),
                    call_count=len(llm_client.metrics) - initial_metric_count,
                    now=repository.clock.now(),
                    warnings=world_warnings,
                    world_update_ms=world_update_ms,
                )
            raise
        except Exception as exc:
            if operation_created:
                repository.fail_initiative(
                    operation_id=request.operation_id,
                    error_code="DATA_CORRUPT" if isinstance(exc, WorldDataCorrupt) else "DATABASE_ERROR",
                    elapsed_ms=round((time.perf_counter() - total_started) * 1000),
                    call_count=len(llm_client.metrics) - initial_metric_count,
                    now=repository.clock.now(),
                    warnings=world_warnings,
                    world_update_ms=world_update_ms,
                )
            if isinstance(exc, WorldDataCorrupt):
                raise problem_for_code("DATA_CORRUPT", request.operation_id) from exc
            raise problem_for_code("DATABASE_ERROR", request.operation_id) from exc
    finally:
        guard.__exit__(None, None, None)
