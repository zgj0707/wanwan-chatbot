"""天气查询（Open-Meteo，免费无密钥）。

设计边界：天气只用于界面氛围（光斑、开场语气），失败一律静默降级——
聊天主链路永不因天气阻塞或报错。经纬度来自 config.yaml 的 weather 段。
"""
from __future__ import annotations

from typing import Any

import httpx


class WeatherUnavailable(RuntimeError):
    """Raised when the weather provider cannot be reached; callers degrade silently."""


# Open-Meteo weathercode → 苏晚语境的简短描述（WMO 标准）
_WEATHER_CODES: dict[int, str] = {
    0: "晴", 1: "基本晴", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "阵雪",
    95: "雷雨", 96: "雷雨夹冰雹", 99: "雷雨夹冰雹",
}


def describe_weather(payload: dict[str, Any]) -> dict[str, Any]:
    """Map an Open-Meteo current-weather payload to the UI view model."""
    current = payload.get("current") or {}
    code = current.get("weather_code")
    temperature = current.get("temperature_2m")
    is_day = current.get("is_day", 1)
    condition = _WEATHER_CODES.get(code if isinstance(code, int) else -1, "未知")
    rainy = isinstance(code, int) and (code >= 51 or code in {66, 67})
    snowy = isinstance(code, int) and 71 <= code <= 77 or code in {85, 86}
    return {
        "condition": condition,
        "temperature": round(temperature, 1) if isinstance(temperature, (int, float)) else None,
        "is_day": bool(is_day),
        "rainy": rainy,
        "snowy": bool(snowy),
        "clear": code == 0 or code == 1,
    }


def fetch_weather(latitude: float, longitude: float, *, timeout: float = 4.0) -> dict[str, Any]:
    """Query Open-Meteo; raise WeatherUnavailable on any failure (caller degrades)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "current": "temperature_2m,weather_code,is_day",
        "timezone": "auto",
    }
    try:
        response = httpx.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise WeatherUnavailable("weather provider unreachable") from exc
    view = describe_weather(payload)
    if view["condition"] == "未知" and view["temperature"] is None:
        raise WeatherUnavailable("weather payload unusable")
    return view
