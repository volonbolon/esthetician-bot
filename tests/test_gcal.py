import io
import os
from pathlib import Path
from typing import Any

import pytest

from app.adapters.fake_calendar import FakeCalendar
from app.adapters.gcal import GoogleCalendar, _parse_gcal_dt
from app.clock import _to_tz
from app.config import load_config
from app.ports import CalendarError, CalendarPort
from app.sim import Simulator


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
            items = list(self.store.get(calendarId, {}).values())
            time_min = _to_tz(kwargs["timeMin"]) if "timeMin" in kwargs else None
            time_max = _to_tz(kwargs["timeMax"]) if "timeMax" in kwargs else None
            filtered: list[dict[str, Any]] = []
            for it in items:
                ev_start = _parse_gcal_dt(it["start"])
                ev_end = _parse_gcal_dt(it["end"])
                if time_min is not None and ev_end <= time_min:
                    continue
                if time_max is not None and ev_start >= time_max:
                    continue
                filtered.append(it)
            return {"items": filtered}

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
@pytest.mark.parametrize("backend", ["fake", "google"])
async def test_calendar_contract_suite(backend: str):
    if backend == "google":
        if not (os.environ.get("RUN_GCAL_CONTRACT") or os.environ.get("GCAL_CONTRACT")):
            pytest.skip("Set RUN_GCAL_CONTRACT=1 to run contract suite against GoogleCalendar")
        cal: CalendarPort = GoogleCalendar(sa_json=os.environ["GOOGLE_SA_JSON"])
        cal_id = os.environ["CAL_TURNOS_ID"]
    else:
        cal = FakeCalendar()
        cal_id = "turnos"

    assert await cal.get_event(cal_id, "nonexistent_evt_id") is None

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
        assert fetched.description == "+5491122334455"

        in_window = await cal.list_events(cal_id, "2026-10-27T13:00", "2026-10-27T16:00")
        assert any(e.id == created.id for e in in_window)

        out_of_window = await cal.list_events(cal_id, "2026-10-28T00:00", "2026-10-29T00:00")
        assert all(e.id != created.id for e in out_of_window)

        updated = await cal.patch_event(
            cal_id,
            created.id,
            start="2026-10-27T15:00",
            end="2026-10-27T16:00",
            title="✅ Peeling – Test",
        )
        assert updated.title == "✅ Peeling – Test"
        assert updated.start.hour == 15
    finally:
        await cal.delete_event(cal_id, created.id)

    assert await cal.get_event(cal_id, created.id) is None


@pytest.mark.asyncio
async def test_sim_session_against_google_calendar_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    use_real_gcal = bool(
        (os.environ.get("RUN_GCAL_CONTRACT") or os.environ.get("GCAL_CONTRACT"))
        and os.environ.get("GOOGLE_SA_JSON")
        and os.environ.get("CAL_AVAILABLE_ID")
        and os.environ.get("CAL_TURNOS_ID")
    )
    if use_real_gcal:
        cfg = load_config({**os.environ, "CALENDAR_BACKEND": "google", "MESSENGER": "console"})
    else:
        fake_svc = _FakeGoogleService()
        monkeypatch.setattr("app.adapters.gcal._build_google_service", lambda _sa: fake_svc)
        cfg = load_config(
            {
                "MESSENGER": "console",
                "CALENDAR_BACKEND": "google",
                "GOOGLE_SA_JSON": '{"type":"service_account"}',
                "CAL_AVAILABLE_ID": "test_avail_cal",
                "CAL_TURNOS_ID": "test_turnos_cal",
            }
        )

    buf = io.StringIO()
    sim = Simulator(
        phone="+5491100000001",
        db_path=tmp_path / "gcal_sim.db",
        now="2026-12-18T09:00:00",
        stream=buf,
        config=cfg,
    )
    assert sim.config.calendar_backend == "google"
    assert isinstance(sim.calendar, GoogleCalendar)

    avail_ev = await sim.calendar.create_event(
        cfg.cal_available_id,
        "2026-12-21T09:00",
        "2026-12-21T12:00",
        "Disponible",
    )
    try:
        # Onboard & book a slot against GoogleCalendar
        await sim.handle_line("hola")
        await sim.handle_line("Sofía")
        await sim.handle_line("1")  # Reservar
        await sim.handle_line("1")  # Limpieza facial
        await sim.handle_line("1")  # Lun 21/12
        await sim.handle_line("1")  # 09:00
        await sim.handle_line("1")  # Confirmar

        assert "Reservamos tu turno de Limpieza facial" in buf.getvalue()

        # /cal lists both calendars via GoogleCalendar.list_events
        await sim.handle_line("/cal")
        out = buf.getvalue()
        assert "[cal:available] (1)" in out
        assert "[cal:turnos] (1)" in out
        assert "Limpieza facial – Sofía | +5491100000001" in out

        # Advance time to 24h before appointment -> /tick sends reminder -> client confirms (✅)
        await sim.handle_line("/time 2026-12-20T09:00")
        await sim.handle_line("/tick")
        assert "[TEMPLATE recordatorio_turno]" in buf.getvalue()
        await sim.handle_line("1")  # Confirmar on reminder
        assert "✅ Limpieza facial – Sofía" in (
            await sim.calendar.list_events(
                cfg.cal_turnos_id, "2026-12-21T00:00", "2026-12-22T00:00"
            )
        )[0].title

        # Rewind before 24h cutoff and cancel via Mi turno -> deletes event from GoogleCalendar
        await sim.handle_line("/time 2026-12-19T10:00")
        await sim.handle_line("menu")
        await sim.handle_line("2")  # Mi turno
        await sim.handle_line("2")  # Cancelar
        assert "fue cancelado." in buf.getvalue()
        assert (
            await sim.calendar.list_events(
                cfg.cal_turnos_id, "2026-12-21T00:00", "2026-12-22T00:00"
            )
            == []
        )
    finally:
        await sim.calendar.delete_event(cfg.cal_available_id, avail_ev.id)

