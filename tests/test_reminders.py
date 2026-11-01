from datetime import datetime

from app.clock import TZ
from app.reminders import compute_send_at, within_cutoff


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def test_compute_send_at_48h_or_more_returns_start_minus_48h():
    assert compute_send_at("2026-10-30T10:00", "2026-10-27T15:00") == _dt("2026-10-28T10:00")
    assert compute_send_at("2026-10-28T10:00", "2026-10-26T10:00") == _dt("2026-10-26T10:00")


def test_compute_send_at_less_than_48h_returns_none():
    assert compute_send_at("2026-10-28T10:00", "2026-10-26T10:01") is None
    assert compute_send_at("2026-10-27T10:00", "2026-10-26T18:00") is None


def test_within_cutoff_24h():
    start = "2026-10-28T10:00"
    assert within_cutoff(start, "2026-10-27T09:59") is False
    assert within_cutoff(start, "2026-10-27T10:00") is False
    assert within_cutoff(start, "2026-10-27T10:01") is True
    assert within_cutoff(start, "2026-10-28T11:00") is True
    assert within_cutoff(start, "2026-10-27T15:00", cutoff_h=12) is False
    assert within_cutoff(start, "2026-10-27T23:00", cutoff_h=12) is True
