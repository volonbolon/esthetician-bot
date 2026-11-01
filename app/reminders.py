from datetime import datetime, timedelta

from app.clock import _to_tz

REMINDER_LEAD_H = 48
DEFAULT_CUTOFF_H = 24


def compute_send_at(
    start: datetime | str,
    created_at: datetime | str,
    *,
    lead_h: int = REMINDER_LEAD_H,
) -> datetime | None:
    start_dt = _to_tz(start)
    created_dt = _to_tz(created_at)
    send_at = start_dt - timedelta(hours=lead_h)
    if send_at < created_dt:
        return None
    return send_at


def within_cutoff(
    start: datetime | str,
    now: datetime | str,
    cutoff_h: int = DEFAULT_CUTOFF_H,
) -> bool:
    start_dt = _to_tz(start)
    now_dt = _to_tz(now)
    return (start_dt - now_dt) < timedelta(hours=cutoff_h)
