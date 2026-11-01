import os
from typing import Any

import pytest

from app.adapters.fake_calendar import FakeCalendar
from app.adapters.gcal import GoogleCalendar
from app.ports import CalendarError, CalendarPort


class _FakeReq:
    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def execute(self) -> Any:
        return self._fn()


class _NotFoundError(Exception):
    status_code = 404


class _FakeEventsResource:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, dict[str, Any]]] = {}
        self.seq = 0
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            self.fail = False
            raise RuntimeError("boom")

    def list(self, *, calendarId: str, **kwargs: Any) -> _FakeReq:
        def _do() -> dict[str, Any]:
            self._check()
            return {"items": list(self.store.get(calendarId, {}).values())}

        return _FakeReq(_do)

    def get(self, *, calendarId: str, eventId: str) -> _FakeReq:
        def _do() -> dict[str, Any]:
            self._check()
            item = self.store.get(calendarId, {}).get(eventId)
            if item is None:
                raise _NotFoundError("not found")
            return item

        return _FakeReq(_do)

    def insert(self, *, calendarId: str, body: dict[str, Any]) -> _FakeReq:
        def _do() -> dict[str, Any]:
            self._check()
            self.seq += 1
            ev_id = f"g_{self.seq}"
            item = {"id": ev_id, **body}
            self.store.setdefault(calendarId, {})[ev_id] = item
            return item

        return _FakeReq(_do)

    def patch(
        self, *, calendarId: str, eventId: str, body: dict[str, Any]
    ) -> _FakeReq:
        def _do() -> dict[str, Any]:
            self._check()
            cal = self.store.get(calendarId, {})
            if eventId not in cal:
                raise _NotFoundError("not found")
            cal[eventId] = {**cal[eventId], **body}
            return cal[eventId]

        return _FakeReq(_do)

    def delete(self, *, calendarId: str, eventId: str) -> _FakeReq:
        def _do() -> None:
            self._check()
            cal = self.store.get(calendarId, {})
            if eventId not in cal:
                raise _NotFoundError("not found")
            del cal[eventId]

        return _FakeReq(_do)


class _FakeGoogleService:
    def __init__(self) -> None:
        self.res = _FakeEventsResource()

    def events(self) -> _FakeEventsResource:
        return self.res


@pytest.mark.asyncio
async def test_google_calendar_adapter_crud_and_error_wrapping():
    svc = _FakeGoogleService()
    gcal = GoogleCalendar(service=svc)
    assert isinstance(gcal, CalendarPort)

    ev = await gcal.create_event(
        "turnos",
        "2026-10-27T09:00",
        "2026-10-27T10:00",
        "Limpieza facial – Ana",
        "+5491122334455",
    )
    assert ev.title == "Limpieza facial – Ana"
    assert await gcal.get_event("turnos", ev.id) == ev

    patched = await gcal.patch_event("turnos", ev.id, title="✅ Limpieza facial – Ana")
    assert patched.title == "✅ Limpieza facial – Ana"

    listed = await gcal.list_events("turnos", "2026-10-27T00:00", "2026-10-28T00:00")
    assert listed == [patched]

    await gcal.delete_event("turnos", ev.id)
    assert await gcal.get_event("turnos", ev.id) is None

    # Underlying client failures are wrapped in CalendarError
    svc.res.fail = True
    with pytest.raises(CalendarError):
        await gcal.list_events("turnos", "2026-10-27T00:00", "2026-10-28T00:00")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["fake", "google_contract"])
async def test_calendar_contract_suite(backend: str):
    if backend == "google_contract":
        if not os.environ.get("RUN_GCAL_CONTRACT"):
            pytest.skip("Set RUN_GCAL_CONTRACT=1 to run against live Google Calendar")
        cal: CalendarPort = GoogleCalendar(sa_json=os.environ["GOOGLE_SA_JSON"])
        cal_id = os.environ["CAL_TURNOS_ID"]
    else:
        cal = FakeCalendar()
        cal_id = "turnos"

    created = await cal.create_event(
        cal_id,
        "2026-10-27T14:00",
        "2026-10-27T15:00",
        "Peeling – Test",
        "+5491122334455",
    )
    try:
        fetched = await cal.get_event(cal_id, created.id)
        assert fetched is not None
        assert fetched.title == "Peeling – Test"

        updated = await cal.patch_event(cal_id, created.id, title="✅ Peeling – Test")
        assert updated.title == "✅ Peeling – Test"
    finally:
        await cal.delete_event(cal_id, created.id)
    assert await cal.get_event(cal_id, created.id) is None
