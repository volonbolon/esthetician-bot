from pathlib import Path

import pytest

from app.adapters.fake_calendar import DEFAULT_SEED_PATH, FakeCalendar
from app.ports import CalendarError, CalendarPort


@pytest.mark.asyncio
async def test_fake_calendar_crud_and_seed():
    cal = FakeCalendar(seed_path=DEFAULT_SEED_PATH)
    assert isinstance(cal, CalendarPort)

    avail = await cal.list_events("available", "2026-12-18T00:00", "2027-02-01T00:00")
    assert len(avail) >= 4
    assert any("[VIP]" in e.title for e in avail)

    ev = await cal.create_event(
        "turnos",
        "2026-12-19T09:00",
        "2026-12-19T10:00",
        "Limpieza facial – Ana",
        "+5491122334455",
    )
    assert ev.id == "evt_1"
    assert await cal.get_event("turnos", ev.id) == ev

    patched = await cal.patch_event("turnos", ev.id, title="✅ Limpieza facial – Ana")
    assert patched.title == "✅ Limpieza facial – Ana"
    assert (await cal.get_event("turnos", ev.id)).title == "✅ Limpieza facial – Ana"

    await cal.delete_event("turnos", ev.id)
    assert await cal.get_event("turnos", ev.id) is None

    with pytest.raises(CalendarError):
        await cal.delete_event("turnos", ev.id)


@pytest.mark.asyncio
async def test_fake_calendar_json_persistence_and_fail_next(tmp_path: Path):
    json_file = tmp_path / "cal.json"
    cal1 = FakeCalendar(json_path=json_file, seed_path=DEFAULT_SEED_PATH)
    ev = await cal1.create_event(
        "turnos",
        "2026-10-27T10:00",
        "2026-10-27T11:00",
        "Peeling – Carla",
        "+5491166778899",
    )

    # Reload from JSON file in a new FakeCalendar instance
    cal2 = FakeCalendar(json_path=json_file)
    loaded = await cal2.get_event("turnos", ev.id)
    assert loaded == ev

    # fail_next() switch causes the next call to raise CalendarError, then recovers
    cal2.fail_next()
    with pytest.raises(CalendarError):
        await cal2.list_events("turnos", "2026-10-27T00:00", "2026-10-28T00:00")

    events = await cal2.list_events("turnos", "2026-10-27T00:00", "2026-10-28T00:00")
    assert events == [ev]
