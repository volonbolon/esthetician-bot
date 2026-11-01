from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.clock import _to_tz
from app.config import Service, ServicesConfig


@dataclass(frozen=True)
class Block:
    start: datetime
    end: datetime
    title: str = ""

    def __init__(self, start: datetime | str, end: datetime | str, title: str = "") -> None:
        object.__setattr__(self, "start", _to_tz(start))
        object.__setattr__(self, "end", _to_tz(end))
        object.__setattr__(self, "title", title)


def _unpack(item: Any) -> tuple[datetime, datetime, str]:
    if isinstance(item, tuple):
        start, end = item[0], item[1]
        title = item[2] if len(item) > 2 else ""
    elif isinstance(item, dict):
        start, end = item["start"], item["end"]
        title = item.get("title") or item.get("summary") or ""
    else:
        start, end = item.start, item.end
        title = getattr(item, "title", None) or getattr(item, "summary", "") or ""
    return _to_tz(start), _to_tz(end), str(title)


def _align_to_grid(dt: datetime, step_minutes: int) -> datetime:
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_seconds = (dt - midnight).total_seconds()
    step_seconds = step_minutes * 60
    rem = elapsed_seconds % step_seconds
    if rem == 0:
        return dt
    return dt + timedelta(seconds=step_seconds - rem)


def generate(
    blocks: Iterable[Any],
    busy: Iterable[Any],
    services_cfg: ServicesConfig,
    service: Service | str,
    now: datetime | str,
    vip: bool = False,
) -> list[datetime]:
    svc = services_cfg.get(service) if isinstance(service, str) else service
    now_dt = _to_tz(now)
    min_start = now_dt + timedelta(hours=services_cfg.min_notice_h)
    horizon_days = services_cfg.horizon_vip if vip else services_cfg.horizon_regular
    horizon_end = now_dt + timedelta(days=horizon_days)

    span = timedelta(minutes=svc.duration + services_cfg.buffer)
    step = timedelta(minutes=services_cfg.grid_step)
    busy_spans = [(s, e) for s, e, _ in (_unpack(b) for b in busy) if e > s]

    result: set[datetime] = set()
    for raw_block in blocks:
        b_start, b_end, title = _unpack(raw_block)
        if b_end <= b_start:
            continue
        if not vip and "[vip]" in title.lower():
            continue

        t = _align_to_grid(max(b_start, min_start), services_cfg.grid_step)
        while t + span <= b_end and t <= horizon_end:
            end = t + span
            if all(end <= bs or t >= be for bs, be in busy_spans):
                result.add(t)
            t += step

    return sorted(result)


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    next_offset: int | None
    has_more: bool
    include_no_slot: bool


def days_with_slots(slots: Iterable[datetime | str]) -> list[date]:
    return sorted({_to_tz(s).date() for s in slots})


def slots_for_day(slots: Iterable[datetime | str], day: date | str) -> list[datetime]:
    target = date.fromisoformat(day) if isinstance(day, str) else day
    return sorted({_to_tz(s) for s in slots if _to_tz(s).date() == target})


def paginate[T](items: Sequence[T], offset: int = 0, *, vip: bool = False) -> Page[T]:
    if offset < 0:
        raise ValueError(f"Invalid offset: {offset}")
    size = 8 if vip else 9
    sliced = list(items[offset : offset + size])
    has_more = offset + size < len(items)
    return Page(
        items=sliced,
        next_offset=offset + size if has_more else None,
        has_more=has_more,
        include_no_slot=vip,
    )

