from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import AppConfig, load_config, project_root
from app.db import InstanceLock, initialize_database
from app.errors import APIProblem
from app.llm import FakeClient, LLMClient
from app.models import APIErrorDetail, APIErrorResponse
from app.repository import Repository
from app.routes import router as api_router
from app.world import WorldService


ALLOWED_HOSTS = {"127.0.0.1:8000", "localhost:8000"}
ALLOWED_ORIGINS = {"http://127.0.0.1:8000", "http://localhost:8000"}


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    operation_id: str | None = None,
) -> JSONResponse:
    body = APIErrorResponse(
        error=APIErrorDetail(
            code=code,
            message=message,
            retryable=retryable,
            operation_id=operation_id,
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _bootstrap_json(payload: dict) -> str:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def create_app(
    config: AppConfig | None = None,
    *,
    project_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
    fake_client: FakeClient | None = None,
) -> FastAPI:
    root = Path(project_dir).resolve() if project_dir is not None else project_root()
    if config is None:
        load_dotenv(root / ".env", override=False)
        config_path = root / "config.yaml"
        if not config_path.is_file():
            config_path = root / "config.example.yaml"
        config = load_config(config_path)

    ui_dir = root / "ui"
    runtime_data_dir = Path(data_dir) if data_dir is not None else root / "data"
    database_path = runtime_data_dir / "app.sqlite3"
    repository = Repository(database_path)
    llm_client = LLMClient(config, fake_client=fake_client, metrics_sink=repository.record_call_metric)
    world_service = WorldService(repository, llm_client, config)
    instance_lock = InstanceLock(runtime_data_dir / "app.lock")
    session_token = secrets.token_hex(32)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        instance_lock.acquire()
        app.state.instance_lock = instance_lock
        try:
            initialize_database(database_path, clock=repository.clock)
            repository.recover_pending_operations()
            # CLR 二阶段：日程/目标/承诺种子 + 生活时间线补算（纯规则，零 LLM 调用）
            try:
                from app.life import run_startup_catchup, seed_goals, seed_schedule

                seed_schedule(repository, now=repository.clock.now())
                seed_goals(repository, now=repository.clock.now())
                app.state.startup_life_events = run_startup_catchup(repository, now=repository.clock.now())
            except Exception:
                app.state.startup_life_events = []
            app.state.repository = repository
            if config.limits.auto_world_update:
                try:
                    app.state.startup_world_update = world_service.advance(now=repository.clock.now())
                except Exception:
                    app.state.startup_world_update = {
                        "advanced": False,
                        "event": None,
                        "warnings": ["WORLD_UPDATE_FAILED"],
                        "elapsed_ms": 0,
                    }
            else:
                app.state.startup_world_update = {
                    "advanced": False,
                    "event": None,
                    "warnings": [],
                    "elapsed_ms": 0,
                }
            yield
        finally:
            instance_lock.release()

    app = FastAPI(
        title="苏晚晚 Chatbot",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.data_dir = runtime_data_dir.resolve()
    app.state.project_dir = root
    app.state.llm_client = llm_client
    app.state.repository = repository
    app.state.world_service = world_service
    app.state.world_updater = lambda session_id, now: world_service.advance(now=now)
    app.state.session_token = session_token

    @app.middleware("http")
    async def local_security_boundary(request: Request, call_next):
        host = request.headers.get("host", "").lower()
        origin = request.headers.get("origin")
        if host not in ALLOWED_HOSTS:
            response = _error_response(400, "INVALID_HOST", "仅允许通过本机地址访问。")
        elif origin is not None and (origin not in ALLOWED_ORIGINS or origin != f"http://{host}"):
            response = _error_response(403, "INVALID_ORIGIN", "请求来源不被允许。")
        elif request.url.path.startswith("/api/v1") and not (
            request.method == "GET" and request.url.path == "/api/v1/health"
        ):
            supplied = request.headers.get("X-Suwan-Token", "")
            if not secrets.compare_digest(supplied, session_token):
                response = _error_response(401, "TOKEN_REQUIRED", "缺少本机会话令牌。")
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)

        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; object-src 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(APIProblem)
    async def api_problem_handler(request: Request, exc: APIProblem):
        return _error_response(
            exc.status_code,
            exc.code,
            exc.message,
            retryable=exc.retryable,
            operation_id=exc.operation_id,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return _error_response(422, "VALIDATION_ERROR", "请求字段无效。")

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return _error_response(404, "NOT_FOUND", "找不到请求的资源。")
        return _error_response(exc.status_code, "HTTP_ERROR", "请求无法处理。")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request) -> HTMLResponse:
        page_path = ui_dir / "index.html"
        page = page_path.read_text(encoding="utf-8")
        bootstrap = _bootstrap_json({"token": session_token, "mode": config.mode})
        page = page.replace("<!-- SUWAN_BOOTSTRAP -->", f'<script id="suwan-bootstrap" type="application/json">{bootstrap}</script>')
        return HTMLResponse(page)

    # 前端文件不加缓存头时浏览器会长期使用旧副本，改版后界面看起来"没变化"；
    # no-cache 让浏览器每次都回源校验，文件一更新普通刷新即可拿到新版。
    static_cache = {"Cache-Control": "no-cache"}

    @app.get("/ui/style.css", include_in_schema=False)
    def styles() -> FileResponse:
        return FileResponse(ui_dir / "style.css", media_type="text/css", headers=static_cache)

    @app.get("/ui/life.css", include_in_schema=False)
    def life_styles() -> FileResponse:
        return FileResponse(ui_dir / "life.css", media_type="text/css", headers=static_cache)

    @app.get("/ui/app.js", include_in_schema=False)
    def script() -> FileResponse:
        return FileResponse(ui_dir / "app.js", media_type="text/javascript", headers=static_cache)

    @app.get("/ui/assets/avatar.png", include_in_schema=False)
    def avatar_image() -> FileResponse:
        return FileResponse(ui_dir / "assets" / "avatar.png", media_type="image/png", headers=static_cache)

    @app.get("/ui/assets/avatar-small.png", include_in_schema=False)
    def avatar_small_image() -> FileResponse:
        return FileResponse(ui_dir / "assets" / "avatar-small.png", media_type="image/png", headers=static_cache)

    mood_background_names = frozenset({
        "sunset-road", "late-night-radio", "cat-sun", "desk-morning", "rainy-window",
    })

    @app.get("/ui/assets/backgrounds/{name}", include_in_schema=False)
    def mood_background(name: str) -> FileResponse:
        base, _, extension = name.rpartition(".")
        if extension != "svg" or base not in mood_background_names:
            from fastapi import HTTPException

            raise HTTPException(status_code=404)
        return FileResponse(
            ui_dir / "assets" / "backgrounds" / name,
            media_type="image/svg+xml",
            headers=static_cache,
        )

    @app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
    def settings() -> HTMLResponse:
        model = escape(config.profiles.chat.model)
        if config.mode == "fake":
            credential_status = "模拟模式不使用 API 密钥。"
        else:
            credential_status = (
                "已检测到模型配置。" if llm_client.has_credentials("chat") and llm_client.has_credentials("analysis")
                else "尚未设置完整模型密钥；聊天请求将返回 CONFIG_MISSING。"
            )
        page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>苏晚晚 Chatbot 设置</title></head>
<body><main><h1>苏晚晚 Chatbot</h1><p>当前模式：{escape(config.mode)}</p><p>Chat profile：{model}</p><p>{credential_status}</p><p>编辑 config.yaml 和 .env 后重启服务。应用关闭期间不会发送消息。</p><p><a href="/">返回聊天</a></p></main></body></html>"""
        return HTMLResponse(page)

    @app.get("/api/v1/health")
    def health() -> dict[str, object]:
        return {"alive": True, "mode": config.mode, "schema_version": config.schema_version}

    app.include_router(api_router)
    # CLR 展示层路由（她的生活摘要）——life_summary 引用 routes.router 装配
    from app import life_summary  # noqa: F401
    return app


app = create_app()
