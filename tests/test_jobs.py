import io
from pathlib import Path

import pytest

from app import jobs
from app.adapters.fake_calendar import FakeCalendar
from app.adapters.fake_messenger import FakeMessenger
from app.clock import FakeClock
from app.config import Config
from app.conversation import Incoming
from app.db import init_db
from app.engine import Engine, build_app
from app.sim import Simulator

CLIENT = "+5491122334455"
ADMIN = "+5491100000099"


def _make_setup() -> tuple[Engine, FakeMessenger, FakeCalendar, FakeClock]:
    cfg = Config(
        messenger="fake",
        calendar_backend="fake",
        db_path=":memory:",
        esthetician_name="Lorena",
        admin_phones=(ADMIN,),
    )
    messenger = FakeMessenger()
    calendar = FakeCalendar()
    clock = FakeClock("2026-12-18T09:00:00")
    engine = build_app(
        cfg,
        db=init_db(":memory:"),
        messenger=messenger,
        calendar=calendar,
        clock=clock,
    )
    return engine, messenger, calendar, clock


@pytest.mark.asyncio
async def test_run_sync_and_run_reminders_driven_by_fake_clock_and_explicit_now():
    engine, messenger, calendar, clock = _make_setup()

    # Manual event added in Turnos >48h ahead (2026-12-21T10:00 -> send_at 2026-12-19T10:00)
    ev = await calendar.create_event(
        "turnos",
        "2026-12-21T10:00",
        "2026-12-21T11:00",
        "Limpieza facial – Ana",
        CLIENT,
    )

    # 1. Plain async call jobs.run_sync(engine, now=...) populates appointments + reminders
    synced = await jobs.run_sync(engine, clock.now())
    assert synced == 1
    rem = engine.db.execute(
        "SELECT event_id, send_at, sent_at FROM reminders WHERE event_id = ?",
        (ev.id,),
    ).fetchone()
    assert rem is not None
    assert rem["sent_at"] is None
    assert rem["send_at"].startswith("2026-12-19T10:00")

    # 2. Before send_at -> run_reminders sends 0
    assert await jobs.run_reminders(engine, clock.now()) == 0
    assert messenger.messages_for(CLIENT) == []

    # 3. Advance FakeClock to 2026-12-19T10:00 -> run_reminders(engine) uses engine.clock.now()
    clock.set("2026-12-19T10:00:00")
    assert await jobs.run_reminders(engine) == 1
    msg = messenger.last_message(CLIENT)
    assert msg is not None
    assert msg.kind == "template"
    assert msg.template_name == "recordatorio_turno"
    assert msg.template_params == ("Ana", "Limpieza facial", "Lun 21/12 10:00")
    assert [b.title for b in msg.buttons] == ["Confirmar", "Reprogramar", "Cancelar"]

    # 4. Subsequent call at same or later time does not re-send
    clock.advance(minutes=15)
    assert await jobs.run_reminders(engine) == 0


@pytest.mark.asyncio
async def test_run_sync_and_run_reminders_driven_by_sim_tick(tmp_path: Path):
    buf = io.StringIO()
    sim = Simulator(phone=CLIENT, db_path=tmp_path / "tick_jobs.db", stream=buf)

    # Onboard and book Lun 21/12 10:00 (>48h from 2026-12-18 09:00)
    await sim.handle_line("hola")
    await sim.handle_line("Ana")
    await sim.handle_line("1")  # Reservar
    await sim.handle_line("1")  # Limpieza facial
    await sim.handle_line("3")  # Lun 21/12
    await sim.handle_line("1")  # 10:00
    await sim.handle_line("1")  # Confirmar

    # Move clock to 48h before appointment and run /tick
    await sim.handle_line("/time 2026-12-19T10:00")
    await sim.handle_line("/tick")

    out = buf.getvalue()
    assert "[TEMPLATE recordatorio_turno] (+5491122334455) Ana | Limpieza facial | Lun 21/12 10:00" in out
    assert "[sim] tick @ 2026-12-19T10:00-03:00 (reminders=1)" in out

    # Confirm reminder via numeric tap on button 1 (Confirmar)
    await sim.handle_line("1")
    ev = await sim.calendar.get_event("turnos", "evt_1")
    assert ev is not None
    assert ev.title == "✅ Limpieza facial – Ana"
    assert isinstance(sim.engine, Engine)
    assert Incoming(wamid="x", from_phone=CLIENT).from_phone == CLIENT
