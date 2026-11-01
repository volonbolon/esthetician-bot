from datetime import UTC, datetime, timedelta

from app.clock import TZ, FakeClock, SystemClock


def test_system_clock_returns_aware_buenos_aires_time():
    now = SystemClock().now()
    assert now.tzinfo == TZ
    assert now.utcoffset() == timedelta(hours=-3)


def test_fake_clock_set_and_advance():
    clock = FakeClock("2026-10-26T09:00")
    assert clock.now() == datetime(2026, 10, 26, 9, 0, tzinfo=TZ)

    clock.advance(timedelta(hours=3))
    assert clock.now() == datetime(2026, 10, 26, 12, 0, tzinfo=TZ)

    clock.advance(days=2, minutes=30)
    assert clock.now() == datetime(2026, 10, 28, 12, 30, tzinfo=TZ)

    clock.set(datetime(2026, 11, 2, 15, 0, tzinfo=UTC))
    assert clock.now() == datetime(2026, 11, 2, 12, 0, tzinfo=TZ)
