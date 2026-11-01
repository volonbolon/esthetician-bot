import asyncio
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
from app.main import create_app
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


@pytest.mark.asyncio
async def test_reminder_sender_deleted_or_past_event_drops_rows():
    engine, messenger, calendar, clock = _make_setup()

    ev_del = await calendar.create_event(
        "turnos", "2026-12-21T10:00", "2026-12-21T11:00", "Limpieza facial – Ana", CLIENT
    )
    ev_past = await calendar.create_event(
        "turnos", "2026-12-21T12:00", "2026-12-21T13:00", "Peeling – Ana", CLIENT
    )
    engine._save_appointment_and_reminder(ev_del.id, CLIENT, ev_del.start, clock.now())
    engine._save_appointment_and_reminder(ev_past.id, CLIENT, ev_past.start, clock.now())
    engine.db.commit()

    # Delete ev_del in calendar before reminder is due
    await calendar.delete_event("turnos", ev_del.id)

    # Advance clock past ev_del's send_at (2026-12-19T10:00)
    clock.set("2026-12-19T10:05:00")
    assert await jobs.run_reminders(engine) == 0
    assert messenger.messages_for(CLIENT) == []
    assert (
        engine.db.execute(
            "SELECT * FROM reminders WHERE event_id = ?", (ev_del.id,)
        ).fetchone()
        is None
    )
    assert (
        engine.db.execute(
            "SELECT * FROM appointments WHERE event_id = ?", (ev_del.id,)
        ).fetchone()
        is None
    )

    # Advance clock past ev_past's start time (2026-12-21T12:30) without sending earlier
    clock.set("2026-12-21T12:30:00")
    assert await jobs.run_reminders(engine) == 0
    assert messenger.messages_for(CLIENT) == []
    assert (
        engine.db.execute(
            "SELECT * FROM reminders WHERE event_id = ?", (ev_past.id,)
        ).fetchone()
        is None
    )


@pytest.mark.asyncio
async def test_reminder_sender_moved_event_recomputes_send_at():
    engine, messenger, calendar, clock = _make_setup()

    # 1. Event moved LATER in Calendar (from 2026-12-21 10:00 to 2026-12-22 15:00)
    ev = await calendar.create_event(
        "turnos", "2026-12-21T10:00", "2026-12-21T11:00", "Limpieza facial – Ana", CLIENT
    )
    engine._save_appointment_and_reminder(ev.id, CLIENT, ev.start, clock.now())
    engine.db.commit()

    await calendar.patch_event(
        "turnos", ev.id, start="2026-12-22T15:00", end="2026-12-22T16:00"
    )

    # At old send_at (2026-12-19T10:00), run_reminders sees the move, recomputes send_at to 2026-12-20T15:00, and does not send yet
    clock.set("2026-12-19T10:00:00")
    assert await jobs.run_reminders(engine) == 0
    assert messenger.messages_for(CLIENT) == []

    rem = engine.db.execute(
        "SELECT send_at, sent_at FROM reminders WHERE event_id = ?", (ev.id,)
    ).fetchone()
    assert rem is not None
    assert rem["sent_at"] is None
    assert rem["send_at"].startswith("2026-12-20T15:00")
    appt = engine.db.execute(
        "SELECT start_at FROM appointments WHERE event_id = ?", (ev.id,)
    ).fetchone()
    assert appt["start_at"].startswith("2026-12-22T15:00")

    # At new send_at (2026-12-20T15:00), reminder is sent with the updated date/time
    clock.set("2026-12-20T15:00:00")
    assert await jobs.run_reminders(engine) == 1
    msg = messenger.last_message(CLIENT)
    assert msg is not None
    assert msg.template_name == "recordatorio_turno"
    assert msg.template_params == ("Ana", "Limpieza facial", "Mar 22/12 15:00")

    # 2. Event moved EARLIER in Calendar (new_send_at <= now) -> recomputes and sends immediately
    ev2 = await calendar.create_event(
        "turnos", "2026-12-24T12:00", "2026-12-24T13:00", "Peeling – Ana", CLIENT
    )
    engine._save_appointment_and_reminder(ev2.id, CLIENT, ev2.start, clock.now())
    engine.db.commit()

    # Move ev2 earlier to 2026-12-23T11:00 (new_send_at = 2026-12-21T11:00)
    await calendar.patch_event(
        "turnos", ev2.id, start="2026-12-23T11:00", end="2026-12-23T12:00"
    )
    # Run at 2026-12-22T12:00 (when old send_at is due and new_send_at <= now)
    clock.set("2026-12-22T12:00:00")
    assert await jobs.run_reminders(engine) == 1
    msg2 = messenger.last_message(CLIENT)
    assert msg2 is not None
    assert msg2.template_params == ("Ana", "Peeling", "Mié 23/12 11:00")


@pytest.mark.asyncio
async def test_reminder_sender_sends_to_muted_client_and_handles_calendar_error():
    engine, messenger, calendar, clock = _make_setup()

    ev = await calendar.create_event(
        "turnos", "2026-12-21T10:00", "2026-12-21T11:00", "Limpieza facial – Ana", CLIENT
    )
    engine._save_appointment_and_reminder(ev.id, CLIENT, ev.start, clock.now())
    # Mute client for 12h at reminder time
    engine._ensure_client_row(CLIENT, clock.now())
    engine.db.execute(
        "UPDATE clients SET name = 'Ana', muted_until = ? WHERE phone = ?",
        ("2026-12-19T22:00:00-03:00", CLIENT),
    )
    engine.db.commit()

    clock.set("2026-12-19T10:00:00")

    # 1. If calendar fails on get_event -> admin alerted, sent_at remains NULL
    calendar.fail_next()
    assert await jobs.run_reminders(engine) == 0
    assert messenger.messages_for(CLIENT) == []
    admin_alert = messenger.last_message(ADMIN)
    assert admin_alert is not None
    assert admin_alert.template_name == "alerta_admin"
    assert (
        engine.db.execute(
            "SELECT sent_at FROM reminders WHERE event_id = ?", (ev.id,)
        ).fetchone()["sent_at"]
        is None
    )

    # 2. Next run succeeds even while client is muted, and sets sent_at
    assert await jobs.run_reminders(engine) == 1
    msg = messenger.last_message(CLIENT)
    assert msg is not None
    assert msg.template_name == "recordatorio_turno"
    assert msg.template_params == ("Ana", "Limpieza facial", "Lun 21/12 10:00")
    rem = engine.db.execute(
        "SELECT sent_at FROM reminders WHERE event_id = ?", (ev.id,)
    ).fetchone()
    assert rem["sent_at"] == "2026-12-19T10:00:00-03:00"


@pytest.mark.asyncio
async def test_sync_poller_upserts_cache_and_reminders_and_drops_vanished():
    engine, _, calendar, clock = _make_setup()

    # 1. Manual events in Turnos:
    # - ev_far (>48h ahead, local phone format in description) -> cached + reminder row created
    # - ev_near (<48h ahead, parseable phone) -> cached, NO reminder row created
    # - ev_nophone (no phone in description) -> ignored
    ev_far = await calendar.create_event(
        "turnos",
        "2026-12-22T10:00",
        "2026-12-22T11:00",
        "Limpieza facial – Ana",
        "Tel clienta: 011 15 2233-4455",
    )
    ev_near = await calendar.create_event(
        "turnos",
        "2026-12-19T11:00",
        "2026-12-19T12:00",
        "Peeling – Bea",
        "+54 9 11 6677-8899",
    )
    await calendar.create_event(
        "turnos",
        "2026-12-22T14:00",
        "2026-12-22T15:00",
        "Bloqueo personal",
        "Sin telefono",
    )

    synced = await jobs.run_sync(engine, clock.now())
    assert synced == 2

    appts = {
        r["event_id"]: (r["phone"], r["start_at"])
        for r in engine.db.execute("SELECT event_id, phone, start_at FROM appointments").fetchall()
    }
    assert set(appts.keys()) == {ev_far.id, ev_near.id}
    assert appts[ev_far.id][0] == CLIENT
    assert appts[ev_near.id][0] == "+5491166778899"

    rems = {
        r["event_id"]: r["send_at"]
        for r in engine.db.execute("SELECT event_id, send_at FROM reminders").fetchall()
    }
    assert set(rems.keys()) == {ev_far.id}
    assert rems[ev_far.id].startswith("2026-12-20T10:00")

    # 2. Manual move of ev_far in Calendar -> run_sync updates appointments.start_at and reminders.send_at
    await calendar.patch_event(
        "turnos", ev_far.id, start="2026-12-23T16:00", end="2026-12-23T17:00"
    )
    await jobs.run_sync(engine, clock.now())
    appt_row = engine.db.execute(
        "SELECT start_at FROM appointments WHERE event_id = ?", (ev_far.id,)
    ).fetchone()
    rem_row = engine.db.execute(
        "SELECT send_at FROM reminders WHERE event_id = ?", (ev_far.id,)
    ).fetchone()
    assert appt_row["start_at"].startswith("2026-12-23T16:00")
    assert rem_row["send_at"].startswith("2026-12-21T16:00")

    # 3. Manual deletion of ev_far and phone removal on ev_near -> run_sync drops vanished from cache & reminders
    await calendar.delete_event("turnos", ev_far.id)
    await calendar.patch_event("turnos", ev_near.id, description="sin numero")

    synced_after = await jobs.run_sync(engine, clock.now())
    assert synced_after == 0
    assert engine.db.execute("SELECT * FROM appointments").fetchall() == []
    assert engine.db.execute("SELECT * FROM reminders").fetchall() == []


@pytest.mark.asyncio
async def test_reminder_buttons_confirm_resched_cancel_and_cutoff():
    engine, messenger, calendar, clock = _make_setup()
    await calendar.create_event(
        "available",
        "2026-12-20T09:00",
        "2026-12-20T12:00",
        "Disponible",
    )
    await calendar.create_event(
        "available",
        "2026-12-21T10:00",
        "2026-12-21T13:00",
        "Disponible",
    )

    # Onboard Ana and book Lun 21/12 10:00
    engine.db.execute(
        "INSERT INTO clients(phone, name, awaiting_name, created_at) VALUES (?, ?, 0, ?)",
        (CLIENT, "Ana", "2026-12-18T09:00:00-03:00"),
    )
    engine.db.commit()
    ev = await calendar.create_event(
        "turnos",
        "2026-12-21T10:00",
        "2026-12-21T11:00",
        "Limpieza facial – Ana",
        CLIENT,
    )
    await jobs.run_sync(engine, clock.now())

    # 1. Advance to 48h before (2026-12-19T10:00) and send reminder
    clock.set("2026-12-19T10:00:00")
    assert await jobs.run_reminders(engine) == 1
    rem_msg = messenger.last_message(CLIENT)
    assert rem_msg is not None
    btn_confirm, btn_resched, btn_cancel = rem_msg.buttons

    # 2. Tap Confirmar -> ✅ prefix added to event title (idempotent on second tap)
    await engine.handle_incoming(
        Incoming(
            wamid="rem_c1",
            from_phone=CLIENT,
            kind="template_button",
            payload_id=btn_confirm.id,
        )
    )
    ev_after = await calendar.get_event("turnos", ev.id)
    assert ev_after is not None and ev_after.title == "✅ Limpieza facial – Ana"
    assert (
        messenger.last_message(CLIENT).text
        == "Gracias! Tu turno de Limpieza facial el Lun 21/12 10:00 quedó confirmado."
    )

    await engine.handle_incoming(
        Incoming(
            wamid="rem_c2",
            from_phone=CLIENT,
            kind="template_button",
            payload_id=btn_confirm.id,
        )
    )
    ev_after2 = await calendar.get_event("turnos", ev.id)
    assert ev_after2 is not None and ev_after2.title == "✅ Limpieza facial – Ana"

    # 3. Tap Reprogramar outside 24h cutoff -> opens service list carrying resched=ev.id
    await engine.handle_incoming(
        Incoming(
            wamid="rem_r1",
            from_phone=CLIENT,
            kind="template_button",
            payload_id=btn_resched.id,
        )
    )
    svc_msg = messenger.last_message(CLIENT)
    assert svc_msg is not None and svc_msg.kind == "list"
    assert f"resched={ev.id}" in svc_msg.rows[0].id

    # 4. Inside 24h cutoff (2026-12-20T12:00 -> 22h before Lun 21/12 10:00):
    # Reprogramar and Cancelar both refuse and alert admin without deleting the event
    clock.set("2026-12-20T12:00:00")
    await engine.handle_incoming(
        Incoming(
            wamid="rem_r_cut",
            from_phone=CLIENT,
            kind="template_button",
            payload_id=btn_resched.id,
        )
    )
    assert (
        messenger.last_message(CLIENT).text
        == "Faltan menos de 24h para tu turno. Por favor hablá directamente con Lorena para reprogramar."
    )
    assert "intentó reprogramar" in messenger.last_message(ADMIN).template_params[0]

    await engine.handle_incoming(
        Incoming(
            wamid="rem_x_cut",
            from_phone=CLIENT,
            kind="template_button",
            payload_id=btn_cancel.id,
        )
    )
    assert (
        messenger.last_message(CLIENT).text
        == "Faltan menos de 24h para tu turno. Por favor hablá directamente con Lorena para cancelar."
    )
    assert "intentó cancelar" in messenger.last_message(ADMIN).template_params[0]
    assert await calendar.get_event("turnos", ev.id) is not None

    # 5. Outside 24h cutoff -> Cancelar deletes event and alerts admin
    clock.set("2026-12-19T12:00:00")
    await engine.handle_incoming(
        Incoming(
            wamid="rem_x_ok",
            from_phone=CLIENT,
            kind="template_button",
            payload_id=btn_cancel.id,
        )
    )
    assert (
        messenger.last_message(CLIENT).text
        == "Tu turno de Limpieza facial del Lun 21/12 10:00 fue cancelado."
    )
    assert await calendar.get_event("turnos", ev.id) is None


@pytest.mark.asyncio
async def test_production_loop_wrappers_started_from_fastapi_lifespan_outside_sim():
    assert jobs.REMINDER_POLL_INTERVAL_S == 60.0
    assert jobs.SYNC_POLL_INTERVAL_S == 900.0

    # 1. Outside sim (messenger != "console"): lifespan starts reminder_loop + sync_loop
    engine, messenger, calendar, clock = _make_setup()
    ev = await calendar.create_event(
        "turnos",
        "2026-12-20T09:00",
        "2026-12-20T10:00",
        "Limpieza facial – Ana",
        CLIENT,
    )
    app = create_app(engine, reminder_interval_s=0.01, sync_interval_s=0.01)

    async with app.router.lifespan_context(app):
        assert len(app.state.job_tasks) == 2
        assert {t.get_name() for t in app.state.job_tasks} == {
            "reminder_loop",
            "sync_loop",
        }
        await asyncio.sleep(0.05)
        # sync_loop populated reminder and reminder_loop sent it (since send_at == 2026-12-18T09:00 == clock.now())
        rem = engine.db.execute(
            "SELECT sent_at FROM reminders WHERE event_id = ?", (ev.id,)
        ).fetchone()
        assert rem is not None and rem["sent_at"] is not None
        assert messenger.last_message(CLIENT) is not None

    assert all(t.done() for t in app.state.job_tasks)

    # 2. Console / sim mode (messenger == "console"): background loops are NOT started
    console_cfg = Config(
        messenger="console",
        calendar_backend="fake",
        db_path=":memory:",
    )
    console_engine = build_app(
        console_cfg,
        db=init_db(":memory:"),
        messenger=FakeMessenger(),
        calendar=FakeCalendar(),
        clock=clock,
    )
    console_app = create_app(console_engine)
    async with console_app.router.lifespan_context(console_app):
        assert console_app.state.job_tasks == []




