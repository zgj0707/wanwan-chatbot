from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)


class ConfigError(ValueError):
    """Raised when the local YAML configuration is absent or invalid."""


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ServerConfig(StrictConfigModel):
    host: Literal["127.0.0.1"]
    port: Literal[8000]


class TimeConfig(StrictConfigModel):
    character_timezone: StrictStr
    display_timezone: StrictStr

    @field_validator("character_timezone", "display_timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("must be a valid IANA timezone") from exc
        return value


class LLMProfile(StrictConfigModel):
    base_url: StrictStr = Field(min_length=1, max_length=500)
    model: StrictStr = Field(min_length=1, max_length=200)
    api_key_env: StrictStr = Field(min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]*$")
    temperature: float = Field(ge=0.0, le=1.0)
    # 推理型模型（deepseek/glm 系）会先在 reasoning 里花掉大量 token，
    # max_tokens 必须给思考留余量，否则 content 为空。
    max_tokens: StrictInt = Field(ge=1, le=8000)
    timeout_seconds: StrictInt = Field(ge=1, le=60)
    json_mode: StrictBool
    input_budget_units: StrictInt = Field(ge=1000, le=16000)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise ValueError("must be an HTTPS endpoint or an explicit localhost HTTP endpoint") from exc

        if host is None or parsed.username is not None or parsed.password is not None:
            raise ValueError("endpoint must not contain credentials")
        if parsed.query or parsed.fragment or host in {"0.0.0.0", "*"}:
            raise ValueError("endpoint must not contain query data, fragments, or wildcard hosts")
        if parsed.scheme == "https":
            return value
        if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"} and port is not None:
            return value
        raise ValueError("must be HTTPS, or HTTP on localhost with an explicit port")


class ProfilesConfig(StrictConfigModel):
    chat: LLMProfile
    analysis: LLMProfile
    world: LLMProfile


class LimitsConfig(StrictConfigModel):
    turn_deadline_seconds: StrictInt = Field(ge=10, le=45)
    max_user_chars: StrictInt = Field(ge=1, le=2000)
    max_reply_chars: StrictInt = Field(ge=120, le=400)
    history_turns: StrictInt = Field(ge=1, le=8)
    memory_top_k: StrictInt = Field(ge=1, le=5)
    world_events_top_k: StrictInt = Field(ge=1, le=3)
    initiative_enabled: StrictBool
    initiative_min_gap_hours: StrictInt = Field(ge=6, le=24)
    auto_world_update: StrictBool


class AppConfig(StrictConfigModel):
    schema_version: Literal[1]
    mode: Literal["fake", "real"]
    server: ServerConfig
    time: TimeConfig
    profiles: ProfilesConfig
    limits: LimitsConfig
    weather: "WeatherConfig | None" = None

    @model_validator(mode="after")
    def profile_formats_match_their_roles(self) -> AppConfig:
        if self.profiles.chat.json_mode:
            raise ValueError("chat profile must use plain text responses")
        if not self.profiles.analysis.json_mode or not self.profiles.world.json_mode:
            raise ValueError("analysis and world profiles must use JSON responses")
        return self


class WeatherConfig(StrictConfigModel):
    """Optional ambient weather (Open-Meteo). Failure degrades silently."""
    provider: Literal["open_meteo", "off"] = "off"
    city: StrictStr = Field(default="", max_length=40)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _merge_defaults(defaults: dict, values: dict) -> dict:
    merged = deepcopy(defaults)
    for key, value in values.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_defaults(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path | None = None) -> AppConfig:
    if path is None:
        root = project_root()
        candidate = root / "config.yaml"
        config_path = candidate if candidate.is_file() else root / "config.example.yaml"
    else:
        config_path = Path(path)

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {config_path}") from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"configuration file could not be read: {config_path}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a YAML object")

    defaults_path = project_root() / "config.example.yaml"
    try:
        defaults = yaml.safe_load(defaults_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError("sample configuration defaults could not be read") from exc
    if not isinstance(defaults, dict):
        raise ConfigError("sample configuration defaults must be a YAML object")
    raw = _merge_defaults(defaults, raw)

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        locations = sorted({".".join(str(part) for part in error["loc"]) or "<root>" for error in exc.errors()})
        raise ConfigError("invalid configuration fields: " + ", ".join(locations)) from exc
