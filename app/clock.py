from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _to_tz(value: datetime | str) -> datetime:
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(TZ)


class FakeClock:
    def __init__(self, initial: datetime | str = "2026-10-26T09:00:00") -> None:
        self._now = _to_tz(initial)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime | str) -> datetime:
        self._now = _to_tz(value)
        return self._now

    def advance(self, delta: timedelta | None = None, **kwargs: float) -> datetime:
        if delta is not None:
            self._now += delta
        if kwargs:
            self._now += timedelta(**kwargs)
        return self._now
