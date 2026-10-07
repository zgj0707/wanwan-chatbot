from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


UTC = timezone.utc


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware UTC timestamp."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Clock timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass
class FakeClock:
    _current: datetime

    def __post_init__(self) -> None:
        self._current = _to_utc(self._current)

    def now(self) -> datetime:
        return self._current

    def set(self, value: datetime) -> None:
        self._current = _to_utc(value)

    def advance(self, **delta: float) -> datetime:
        self._current += timedelta(**delta)
        return self._current
