from __future__ import annotations

import json
import time
from datetime import timedelta
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from app.backup import BackupError, export_backup
from app.errors import APIProblem, problem_for_code
from app.memory import MemoryValidationError, validate_fact_value
from app.llm import LLMFailure
from app.models import (
    ChatRequest,
    BackupRequest,
    DiagnosticsProbeRequest,
    InitiativeRequest,
    MemoryCreateRequest,
    MemoryEditRequest,
    MemoryForgetRequest,
    ProposalResolveRequest,
    SessionArchiveRequest,
    SessionCreateRequest,
    WorldAdvanceRequest,
    WorldEventRetractRequest,
    WorldThreadControlRequest,
)
from app.pipeline import handle_initiative, handle_turn
from app.prompt import build_analysis_prompt
from app.persona import PersonaDataUnavailable, PersonaPolicyError
from app.repository import ProposalInvalid, ProposalSourceInvalid, mutation_guard
from app.world import WorldDataCorrupt


router = APIRouter(prefix="/api/v1")


@router.post("/backup")
def create_backup(payload: BackupRequest, request: Request):
    try:
        with mutation_guard():
            archive_path = export_backup(request.app.state.data_dir, now=request.app.state.repository.clock.now())
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY") from exc
        if isinstance(exc, BackupError):
            raise problem_for_code("BACKUP_FAILED") from exc
        raise problem_for_code("DATABASE_ERROR") from exc
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=archive_path.name,
    )


@router.get("/sessions")
def list_sessions(request: Request, include_archived: bool = False) -> dict:
    items = request.app.state.repository.list_sessions(include_archived=include_archived)
    return {"items": [{**item, "archived": bool(item["archived"])} for item in items]}


@router.post("/sessions", status_code=201)
def create_session(payload: SessionCreateRequest, request: Request) -> dict:
    repository = request.app.state.repository
    try:
        with mutation_guard():
            session_id = repository.create_session(payload.title)
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY") from exc
        raise problem_for_code("DATABASE_ERROR") from exc
    session = repository.get_session(session_id)
    assert session is not None
    return {"id": session_id, "title": session["title"], "archived": False}


@router.post("/sessions/{session_id}/archive")
def archive_session(session_id: str, payload: SessionArchiveRequest, request: Request) -> dict:
    repository = request.app.state.repository
    try:
        with mutation_guard():
            session = repository.set_session_archived(session_id, payload.archived)
    except ValueError as exc:
        raise APIProblem(422, "VALIDATION_ERROR", "session_id 必须是 UUID4。") from exc
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY") from exc
        raise problem_for_code("DATABASE_ERROR") from exc
    if session is None:
        raise problem_for_code("SESSION_NOT_FOUND")
    return {"id": session_id, "archived": bool(session["archived"])}


@router.get("/sessions/{session_id}/messages")
def list_messages(
    session_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    before_id: str | None = None,
) -> dict:
    try:
        parsed_session = UUID(session_id)
        if parsed_session.version != 4 or str(parsed_session) != session_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise APIProblem(422, "VALIDATION_ERROR", "session_id 必须是 UUID4。") from exc
    try:
        items, next_before_id = request.app.state.repository.list_session_messages(
            session_id,
            limit=limit,
            before_id=before_id,
        )
    except LookupError as exc:
        raise problem_for_code("SESSION_NOT_FOUND") from exc
    except ValueError as exc:
        raise APIProblem(400, "INVALID_CURSOR", "before_id 必须属于当前对话。") from exc
    return {"items": items, "next_before_id": next_before_id}


@router.post("/chat")
def chat(payload: ChatRequest, request: Request):
    result = handle_turn(
        request.app.state.repository,
        request.app.state.llm_client,
        request.app.state.config,
        payload,
        world_updater=getattr(request.app.state, "world_updater", None),
    )
    if result["status"] == "pending":
        return JSONResponse(status_code=202, content=result)
    return result


@router.post("/initiative")
def initiative(payload: InitiativeRequest, request: Request):
    return handle_initiative(
        request.app.state.repository,
        request.app.state.llm_client,
        request.app.state.config,
        payload,
        world_updater=getattr(request.app.state, "world_updater", None),
    )


@router.get("/operations/{operation_id}")
def get_operation(operation_id: str, request: Request) -> dict:
    repository = request.app.state.repository
    try:
        operation = repository.get_operation(operation_id)
    except ValueError as exc:
        raise APIProblem(422, "VALIDATION_ERROR", "operation_id 必须是 UUID4。") from exc
    if operation is None:
        raise problem_for_code("OPERATION_NOT_FOUND", operation_id)
    try:
        messages = repository.get_operation_messages(operation_id)
        warnings = json.loads(operation["warnings_json"])
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise problem_for_code("DATA_CORRUPT", operation_id) from exc
    return {
        "operation_id": operation_id,
        "status": operation["status"],
        "message": messages["assistant"],
        "user_message": messages["user"],
        "assistant_message": messages["assistant"],
        "error_code": operation["error_code"],
        "warnings": warnings,
        "elapsed_ms": operation["elapsed_ms"],
        "call_count": operation["call_count"],
        "replayed": False,
    }


@router.get("/memory")
def list_memory(
    request: Request,
    status: Literal["active", "superseded", "forgotten", "all"] = "active",
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: str | None = None,
) -> dict:
    repository = request.app.state.repository
    try:
        rows = repository.list_memory_facts(None if status == "all" else status)
        for row in rows:
            if row["status"] != "forgotten":
                value, expected_hash = validate_fact_value(row["key"], row["value"])
                if expected_hash != row["canonical_hash"] or value != row["value"]:
                    raise MemoryValidationError("fact row does not match its hash")
    except (MemoryValidationError, ValueError, TypeError) as exc:
        raise problem_for_code("DATA_CORRUPT") from exc
    except Exception as exc:
        raise problem_for_code("DATABASE_ERROR") from exc
    if q:
        needle = q.casefold()
        rows = [row for row in rows if needle in f"{row['key']} {row['value'] or ''}".casefold()]
    total = len(rows)
    items = [
        {
            "id": row["id"],
            "key": row["key"],
            "value": row["value"],
            "status": row["status"],
            "origin": row["origin"],
            "source_message_ids": row["source_message_ids"],
            "sources": row["sources"],
            "created_at": row["created_at"],
        }
        for row in rows[offset : offset + limit]
    ]
    return {"items": items, "total": total}


@router.post("/memory")
def create_memory(payload: MemoryCreateRequest, request: Request):
    repository = request.app.state.repository
    try:
        value, _ = validate_fact_value(payload.key, payload.value)
    except MemoryValidationError as exc:
        raise problem_for_code("FACT_VALUE_INVALID") from exc
    try:
        with mutation_guard():
            fact, created = repository.add_manual_fact(payload.key, value)
    except MemoryValidationError as exc:
        raise problem_for_code("FACT_VALUE_INVALID") from exc
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY") from exc
        raise problem_for_code("DATABASE_ERROR") from exc
    body = {"id": fact["id"], "key": fact["key"], "value": fact["value"], "status": fact["status"], "origin": fact["origin"]}
    return JSONResponse(status_code=201 if created else 200, content=body)


@router.post("/memory/{fact_id}/edit")
def edit_memory(fact_id: str, payload: MemoryEditRequest, request: Request):
    repository = request.app.state.repository
    try:
        value, _ = validate_fact_value("preference.drink", payload.value)
    except MemoryValidationError as exc:
        # Validate generic length/privacy here; repository revalidates against the immutable fact key.
        raise problem_for_code("FACT_VALUE_INVALID") from exc
    try:
        with mutation_guard():
            fact, _created = repository.edit_manual_fact(fact_id, value)
    except MemoryValidationError as exc:
        raise problem_for_code("FACT_VALUE_INVALID") from exc
    except ValueError as exc:
        raise APIProblem(422, "VALIDATION_ERROR", "fact_id 必须是 UUID4。") from exc
    except LookupError as exc:
        raise problem_for_code("FACT_NOT_FOUND") from exc
    except PermissionError as exc:
        raise problem_for_code("FACT_NOT_ACTIVE") from exc
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY") from exc
        raise problem_for_code("DATABASE_ERROR") from exc
    return {"id": fact["id"], "key": fact["key"], "value": fact["value"], "status": fact["status"], "origin": fact["origin"]}


@router.post("/memory/{fact_id}/forget")
def forget_memory(fact_id: str, payload: MemoryForgetRequest, request: Request) -> dict:
    repository = request.app.state.repository
    try:
        with mutation_guard():
            result = repository.forget_fact(fact_id, redact_session_history=payload.redact_session_history)
    except ValueError as exc:
        raise APIProblem(422, "VALIDATION_ERROR", "fact_id 必须是 UUID4。") from exc
    except LookupError as exc:
        raise problem_for_code("FACT_NOT_FOUND") from exc
    except RuntimeError as exc:
        raise problem_for_code("DATA_CORRUPT") from exc
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY") from exc
        raise problem_for_code("DATABASE_ERROR") from exc
    return {
        **result,
        "notice": "较早导出的备份仍可能包含这些内容；从旧备份恢复会恢复当时的数据。",
    }


@router.get("/persona")
def get_persona(request: Request) -> dict:
    try:
        row = request.app.state.repository.get_current_persona()
        if row is None:
            raise PersonaDataUnavailable("current persona version is missing")
        anchors = json.loads(row["anchors_json"])
        preferences = json.loads(row["preferences_json"])
    except (PersonaDataUnavailable, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise problem_for_code("DATA_CORRUPT") from exc
    except Exception as exc:
        raise problem_for_code("DATABASE_ERROR") from exc
    return {
        "id": row["id"],
        "version": row["version"],
        "anchors": anchors,
        "preferences": preferences,
        "voice_summary": row["voice_text"][:200],
        "created_at": row["created_at"],
    }


@router.get("/world")
def get_world(request: Request) -> dict:
    try:
        return request.app.state.world_service.view()
    except WorldDataCorrupt as exc:
        raise problem_for_code("DATA_CORRUPT") from exc
    except Exception as exc:
        raise problem_for_code("DATABASE_ERROR") from exc


@router.post("/world/advance")
def advance_world(payload: WorldAdvanceRequest, request: Request) -> dict:
    try:
        with mutation_guard():
            return request.app.state.world_service.advance()
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY") from exc
        if isinstance(exc, WorldDataCorrupt):
            raise problem_for_code("DATA_CORRUPT") from exc
        raise problem_for_code("DATABASE_ERROR") from exc


@router.post("/world/events/{event_id}/retract")
def retract_world_event(event_id: str, payload: WorldEventRetractRequest, request: Request) -> dict:
    try:
        parsed = UUID(event_id)
        if parsed.version != 4 or str(parsed) != event_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise APIProblem(422, "VALIDATION_ERROR", "event_id 必须是 UUID4。") from exc
    try:
        with mutation_guard():
            return request.app.state.world_service.retract(event_id)
    except LookupError as exc:
        raise problem_for_code("WORLD_EVENT_NOT_FOUND") from exc
    except PermissionError as exc:
        raise problem_for_code("EVENT_HAS_SUCCESSOR") from exc
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY") from exc
        if isinstance(exc, WorldDataCorrupt):
            raise problem_for_code("DATA_CORRUPT") from exc
        raise problem_for_code("DATABASE_ERROR") from exc


@router.post("/world/threads/{thread_id}/control")
def control_world_thread(thread_id: str, payload: WorldThreadControlRequest, request: Request) -> dict:
    try:
        parsed = UUID(thread_id)
        if parsed.version != 4 or str(parsed) != thread_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise APIProblem(422, "VALIDATION_ERROR", "thread_id 必须是 UUID4。") from exc
    try:
        with mutation_guard():
            return request.app.state.world_service.control_thread(thread_id, payload.action)
    except LookupError as exc:
        raise problem_for_code("WORLD_THREAD_NOT_FOUND") from exc
    except PermissionError as exc:
        if payload.action == "restart":
            raise problem_for_code("THREAD_NOT_RESTARTABLE") from exc
        raise problem_for_code("THREAD_COMPLETED") from exc
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY") from exc
        if isinstance(exc, WorldDataCorrupt):
            raise problem_for_code("DATA_CORRUPT") from exc
        raise problem_for_code("DATABASE_ERROR") from exc


@router.get("/proposals")
def list_proposals(
    request: Request,
    kind: Literal["user_fact", "persona"] | None = None,
    status: Literal["pending", "accepted", "rejected", "invalidated"] = "pending",
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    try:
        rows = request.app.state.repository.list_proposals(kind=kind, status=status)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise problem_for_code("DATA_CORRUPT") from exc
    except Exception as exc:
        raise problem_for_code("DATABASE_ERROR") from exc
    items = [
        {
            "id": row["id"],
            "kind": row["kind"],
            "payload": row["payload"],
            "source": row["source"],
            "status": row["status"],
            "result_id": row["result_id"],
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
        }
        for row in rows[offset : offset + limit]
    ]
    return {"items": items, "total": len(rows)}


@router.post("/proposals/{proposal_id}/resolve")
def resolve_proposal(proposal_id: str, payload: ProposalResolveRequest, request: Request) -> dict:
    repository = request.app.state.repository
    try:
        with mutation_guard():
            return repository.resolve_fact_proposal(proposal_id, payload.decision)
    except ValueError as exc:
        raise APIProblem(422, "VALIDATION_ERROR", "proposal_id 必須是 UUID4。") from exc
    except LookupError as exc:
        raise problem_for_code("PROPOSAL_NOT_FOUND") from exc
    except PermissionError as exc:
        message = str(exc)
        if "redacted" in message:
            raise problem_for_code("SOURCE_REDACTED") from exc
        if "opposite" in message:
            raise problem_for_code("PROPOSAL_CONFLICT") from exc
        if "user fact" in message:
            raise problem_for_code("PERSONA_RESOLUTION_UNAVAILABLE") from exc
        raise problem_for_code("PROPOSAL_NOT_PENDING") from exc
    except ProposalSourceInvalid as exc:
        raise problem_for_code("SOURCE_INVALID") from exc
    except ProposalInvalid as exc:
        raise problem_for_code("PROPOSAL_INVALID") from exc
    except PersonaPolicyError as exc:
        raise problem_for_code(exc.code) from exc
    except PersonaDataUnavailable as exc:
        raise problem_for_code("DATA_CORRUPT") from exc
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY") from exc
        raise problem_for_code("DATABASE_ERROR") from exc


@router.get("/diagnostics")
def get_diagnostics(request: Request) -> dict:
    config = request.app.state.config
    llm_client = request.app.state.llm_client
    repository = request.app.state.repository
    profiles = {}
    for name in ("chat", "analysis", "world"):
        profile = getattr(config.profiles, name)
        profiles[name] = {
            "model": profile.model,
            "provider": urlsplit(profile.base_url).hostname,
            "credential_configured": llm_client.has_credentials(name) if config.mode == "real" else None,
        }
    try:
        metrics = repository.diagnostics_call_metrics()
        app_meta = repository.get_meta("schema_version")
    except Exception as exc:
        raise problem_for_code("DATA_CORRUPT") from exc
    if app_meta is None:
        raise problem_for_code("DATA_CORRUPT")
    try:
        schema_version = int(app_meta)
    except (TypeError, ValueError) as exc:
        raise problem_for_code("DATA_CORRUPT") from exc
    return {
        "mode": config.mode,
        "schema_version": schema_version,
        "health": {"alive": True, "checked_at": repository.clock.now().isoformat().replace("+00:00", "Z")},
        "profiles": profiles,
        "metrics": metrics,
        "configuration": "编辑 config.yaml 和 .env 后重启应用；页面不会读取或修改密钥内容。",
    }


@router.post("/diagnostics/probe")
def probe_model(payload: DiagnosticsProbeRequest, request: Request) -> dict:
    repository = request.app.state.repository
    llm_client = request.app.state.llm_client
    config = request.app.state.config
    profile_name = payload.profile
    if profile_name == "analysis":
        user_text = "连接测试"
        messages = build_analysis_prompt(user_text)
    elif profile_name == "world":
        user_text = None
        messages = (
            {"role": "system", "content": "仅为连接测试返回一个最小 JSON 对象。不要生成任何真实生活事件。"},
            {"role": "user", "content": "返回 {\"probe\":true}。"},
        )
    else:
        user_text = None
        messages = (
            {"role": "system", "content": "你正在执行连接测试。只用简短中文说明连接测试通过，不生成个人记忆或生活事件。"},
            {"role": "user", "content": "请回复：连接测试通过。"},
        )
    try:
        with mutation_guard():
            started = time.perf_counter()
            try:
                result = llm_client.generate(
                    profile_name,
                    messages,
                    None,
                    repository.clock.now() + timedelta(seconds=getattr(config.profiles, profile_name).timeout_seconds + 2),
                    user_text=user_text,
                    max_attempts_override=1,
                )
            except LLMFailure as exc:
                return {
                    "profile": profile_name,
                    "ok": False,
                    "latency_ms": exc.latency_ms if exc.latency_ms is not None else round((time.perf_counter() - started) * 1000),
                    "schema_valid": False if profile_name != "chat" else None,
                    "error": {"code": exc.code, "message": "连接测试未通过；请查看本地配置和最近诊断记录。"},
                }
            schema_valid = True if profile_name == "chat" else isinstance(result.parsed_json, dict)
            return {
                "profile": profile_name,
                "ok": schema_valid,
                "latency_ms": result.latency_ms,
                "schema_valid": schema_valid,
                "error": None if schema_valid else {"code": "REPLY_INVALID", "message": "模型没有返回有效的 JSON 对象。"},
            }
    except Exception as exc:
        from app.db import MutationBusy

        if isinstance(exc, MutationBusy):
            raise problem_for_code("APP_BUSY") from exc
        raise problem_for_code("DATABASE_ERROR") from exc
