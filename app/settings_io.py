"""设置中心的 API 配置与天气配置读写。

安全边界：
- 密钥只写本机文件（config.yaml 的 api_key_env 指向的环境变量名 + .env 的值），
  两个文件都在 .gitignore；本模块的任何返回值不包含密钥明文。
- config.yaml 必须已由 setup.bat 创建；本模块只在存在时原地更新，
  绝不生成新的 config.yaml（避免把 example 的占位密钥环境变量写死）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


class SettingsError(ValueError):
    """Raised when the local settings files cannot be safely updated."""


# 首批聊天 API 模板：只预填 base_url 与默认模型名，密钥由用户自行填写。
# （价格与模型名以 2026-10 官方页面为准；模板可在设置页随时改。）
CHAT_TEMPLATES: dict[str, dict[str, str]] = {
    "newapi": {
        "label": "New API（自建/聚合网关）",
        "base_url": "https://api.newapi.pro/v1",
        "model": "glm-5.3-flash",
        "hint": "填你的网关地址；默认模型 glm-5.3-flash 可在网关上换任意模型名",
    },
    "aliyun": {
        "label": "阿里云百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-flash-character",
        "hint": "角色扮演专用模型；密钥在百炼控制台「API-KEY」页创建",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "hint": "通用对话模型；官方端点。若用 cc-switch 网关也可选 deepseek-v4-flash 等模型",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "base_url": "",
        "model": "",
        "hint": "任何 OpenAI 兼容端点（含本地 Ollama：http://localhost:11434/v1）",
    },
}

ENV_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_WEATHER_PROVIDERS = frozenset({"open_meteo", "off"})


def chat_templates() -> dict[str, dict[str, str]]:
    """Templates with all user-fillable fields blanked; safe for API responses."""
    return {
        key: {"label": tpl["label"], "base_url": tpl["base_url"], "model": tpl["model"], "hint": tpl["hint"]}
        for key, tpl in CHAT_TEMPLATES.items()
    }


def _settings_paths(project_dir: Path) -> tuple[Path, Path]:
    return project_dir / "config.yaml", project_dir / ".env"


def read_api_settings(project_dir: Path) -> dict[str, Any]:
    """Redacted view of the current chat/analysis/world/weather configuration."""
    config_path, env_path = _settings_paths(project_dir)
    # 模板无条件返回（它们是常量，不依赖本机配置是否存在）
    view: dict[str, Any] = {
        "config_file_exists": config_path.is_file(),
        "mode": None,
        "chat": None,
        "analysis": None,
        "world": None,
        "weather": None,
        "templates": chat_templates(),
    }
    if not config_path.is_file():
        return view
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles") or {}
    weather = raw.get("weather") or {}
    view["mode"] = raw.get("mode")

    def profile_view(name: str) -> dict[str, Any] | None:
        profile = profiles.get(name)
        if not isinstance(profile, dict):
            return None
        key_env = profile.get("api_key_env", "")
        return {
            "base_url": profile.get("base_url", ""),
            "model": profile.get("model", ""),
            "api_key_env": key_env,
            "api_key_set": bool(key_env) and bool(_read_env_value(env_path, key_env)),
        }

    view["chat"] = profile_view("chat")
    view["analysis"] = profile_view("analysis")
    view["world"] = profile_view("world")
    view["weather"] = {
        "provider": weather.get("provider", "off"),
        "city": weather.get("city", ""),
        "latitude": weather.get("latitude"),
        "longitude": weather.get("longitude"),
    }
    return view


def _read_env_value(env_path: Path, key: str) -> str | None:
    if not key or not env_path.is_file():
        return None
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except (OSError, UnicodeError):
        return None
    return None


def _validate_base_url(value: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    host = parsed.hostname
    if not value or host is None or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SettingsError("Base URL 无效。")
    if parsed.scheme == "https":
        return value
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"} and parsed.port:
        return value
    raise SettingsError("Base URL 必须是 HTTPS，或本机地址（如 Ollama）。")


def update_chat_api(
    project_dir: Path,
    *,
    base_url: str,
    model: str,
    api_key: str | None,
    api_key_env: str = "SUWAN_API_KEY",
    model_analysis: str | None = None,
    model_world: str | None = None,
) -> dict[str, Any]:
    """Update profiles.* and the .env key; return a redacted view."""
    config_path, env_path = _settings_paths(project_dir)
    if not config_path.is_file():
        raise SettingsError("config.yaml 不存在。请先运行 setup.bat 生成基础配置。")
    base_url = _validate_base_url(base_url.strip())
    model = model.strip()
    if not 1 <= len(model) <= 200:
        raise SettingsError("模型名不能为空。")
    if not ENV_KEY_PATTERN.fullmatch(api_key_env):
        raise SettingsError("环境变量名必须是合法格式（大写字母开头）。")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        raise SettingsError("config.yaml 缺少 profiles 段。")
    for name in ("chat", "analysis", "world"):
        profile = profiles.get(name)
        if not isinstance(profile, dict):
            raise SettingsError(f"config.yaml 缺少 profiles.{name} 段。")
        profile["base_url"] = base_url
        profile["api_key_env"] = api_key_env
    profiles["chat"]["model"] = model
    # 分析/生活事件模型可选：不给则与聊天模型保持一致。
    profiles["analysis"]["model"] = (model_analysis or "").strip() or model
    profiles["world"]["model"] = (model_world or "").strip() or model
    _atomic_write(config_path, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    if api_key is not None and api_key.strip():
        _upsert_env(env_path, api_key_env, api_key.strip())
    return {
        "saved": True,
        "base_url": base_url,
        "model": model,
        "model_analysis": profiles["analysis"]["model"],
        "model_world": profiles["world"]["model"],
        "api_key_env": api_key_env,
        "api_key_set": api_key is not None and bool(api_key.strip()),
    }


def update_weather(
    project_dir: Path,
    *,
    provider: str,
    city: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, Any]:
    if provider not in _WEATHER_PROVIDERS:
        raise SettingsError("不支持的天气来源。")
    if provider == "open_meteo":
        for label, value in (("纬度", latitude), ("经度", longitude)):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not -180 <= float(value) <= 180:
                raise SettingsError(f"{label}无效：请填数字（例：23.13）。经度范围 -180 到 180，纬度范围 -90 到 90。")
        if abs(float(latitude)) > 90:
            raise SettingsError("纬度范围是 -90 到 90。")
    config_path, _ = _settings_paths(project_dir)
    if not config_path.is_file():
        raise SettingsError("config.yaml 不存在。")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw["weather"] = {
        "provider": provider,
        "city": city.strip()[:40],
        "latitude": latitude,
        "longitude": longitude,
    }
    _atomic_write(config_path, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    return {"saved": True, "weather": raw["weather"]}


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _upsert_env(env_path: Path, key: str, value: str) -> None:
    lines: list[str] = []
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    _atomic_write(env_path, "\n".join(lines) + "\n")


def read_ccswitch_providers() -> list[dict[str, Any]]:
    """Read third-party providers from the local cc-switch database (local-to-local).

    Returns redacted views (token length only, never the token itself) for the
    settings list UI; use import_ccswitch_provider to actually apply one.
    """
    db_path = Path.home() / ".cc-switch" / "cc-switch.db"
    if not db_path.is_file():
        return []
    try:
        import sqlite3

        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT name, app_type, settings_config FROM providers"
        ).fetchall()
    except (sqlite3.Error, OSError):
        return []
    providers: list[dict[str, Any]] = []
    for row in rows:
        try:
            config = json.loads(row["settings_config"])
        except (TypeError, ValueError):
            continue
        env = config.get("env") or {}
        base_url = env.get("ANTHROPIC_BASE_URL", "")
        token = env.get("ANTHROPIC_AUTH_TOKEN", "")
        if not base_url or not token:
            continue
        providers.append(
            {
                "id": f"ccswitch:{row['app_type']}:{row['name']}",
                "name": row["name"],
                "app_type": row["app_type"],
                "base_url": base_url,
                "token_set": True,
                "token_length": len(token),
            }
        )
    return providers


def import_ccswitch_provider(project_dir: Path, provider_id: str) -> dict[str, Any]:
    """Import a cc-switch provider into this app's config.yaml + .env.

    The token travels from the local cc-switch DB to the local .env only.
    Returns a redacted view; the OpenAI-compatible base_url is derived from
    the stored Anthropic one (same host, /v1), verified by a /v1/models probe.
    """
    if not provider_id.startswith("ccswitch:"):
        raise SettingsError("无效的导入来源。")
    _, app_type, name = provider_id.split(":", 2)
    db_path = Path.home() / ".cc-switch" / "cc-switch.db"
    if not db_path.is_file():
        raise SettingsError("本机没有找到 cc-switch 数据库。")
    try:
        import sqlite3

        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT settings_config FROM providers WHERE app_type = ? AND name = ?",
            (app_type, name),
        ).fetchone()
    except sqlite3.Error as exc:
        raise SettingsError("读取 cc-switch 数据失败。") from exc
    if row is None:
        raise SettingsError("cc-switch 里没有这条配置。")
    try:
        config = json.loads(row["settings_config"])
    except (TypeError, ValueError) as exc:
        raise SettingsError("cc-switch 配置无法解析。") from exc
    env = config.get("env") or {}
    anthropic_base = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    token = env.get("ANTHROPIC_AUTH_TOKEN") or ""
    if not anthropic_base or not token:
        raise SettingsError("这条配置缺少接口地址或令牌。")

    openai_base = anthropic_base if anthropic_base.endswith("/v1") else anthropic_base + "/v1"
    openai_base = _validate_base_url(openai_base)

    # 探测可用模型清单（失败不阻塞导入——有些网关关掉了 models 列表）
    models: list[str] = []
    try:
        import httpx

        response = httpx.get(
            openai_base + "/models",
            headers={"Authorization": f"Bearer {token}"},
            timeout=8.0,
        )
        if response.status_code == 200:
            payload = response.json()
            models = [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]
    except (httpx.HTTPError, ValueError):
        models = []

    applied = update_chat_api(
        project_dir,
        base_url=openai_base,
        model=models[0] if models else "gpt-4o-mini",
        api_key=token,
    )
    return {**applied, "source": "cc-switch", "available_models": models[:100]}
