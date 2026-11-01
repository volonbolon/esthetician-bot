import asyncio
import io

import pytest

from app.adapters.console_messenger import ConsoleMessenger
from app.adapters.fake_calendar import FakeCalendar
from app.adapters.fake_messenger import FakeMessenger
from app.adapters.gcal import GoogleCalendar
from app.clock import FakeClock, _to_tz
from app.config import Config
from app.conversation import (
    AlertAdmin,
    Cancel,
    ConfirmEvent,
    Incoming,
    Reschedule,
    SendTemplate,
)
from app.db import init_db
from app.engine import BOOKING_LOCK, RETRY_LATER_TEXT, Engine, build_app
from app.payloads import encode
from app.ports import CalendarError

CLIENT_A = "+5491122334455"
CLIENT_B = "+5491166778899"
ADMIN = "+5491100000001"


def _make_engine() -> tuple[Engine, FakeMessenger, FakeCalendar, FakeClock]:
    cfg = Config(
        messenger="fake",
        calendar_backend="fake",
        db_path=":memory:",
        esthetician_name="Lorena",
        admin_phones=(ADMIN,),
    )
    messenger = FakeMessenger()
    calendar = FakeCalendar()
    clock = FakeClock("2026-10-26T09:00:00")
    engine = build_app(
        cfg,
        db=init_db(":memory:"),
        messenger=messenger,
        calendar=calendar,
        clock=clock,
    )
    return engine, messenger, calendar, clock


@pytest.mark.asyncio
async def test_dedupe_by_wamid():
    engine, messenger, _, _ = _make_engine()
    inc = Incoming(wamid="w_dup", from_phone=CLIENT_A, kind="text", text="hola")

    assert await engine.handle_incoming(inc) is True
    assert len(messenger.messages_for(CLIENT_A)) == 1

    # Duplicate wamid is ignored
    assert await engine.handle_incoming(inc) is False
    assert len(messenger.messages_for(CLIENT_A)) == 1


@pytest.mark.asyncio
async def test_onboarding_and_booking_e2e_with_admin_alert():
    engine, messenger, calendar, _ = _make_engine()
    await calendar.create_event(
        "available", "2026-10-29T09:00", "2026-10-29T12:00", "Disponible"
    )

    # 1. First contact -> data notice
    await engine.handle_incoming(
        Incoming(wamid="w1", from_phone=CLIENT_A, kind="text", text="hola")
    )
    assert "nombre" in messenger.last_message(CLIENT_A).text

    # 2. Send name -> stored + main menu
    await engine.handle_incoming(
        Incoming(wamid="w2", from_phone=CLIENT_A, kind="text", text="Ana")
    )
    assert messenger.last_message(CLIENT_A).kind == "buttons"

    # 3. Tap Reservar -> service list -> day list -> time list -> slot -> confirm
    await engine.handle_incoming(messenger.tap("Reservar", CLIENT_A))
    await engine.handle_incoming(messenger.tap("Limpieza facial", CLIENT_A))
    await engine.handle_incoming(messenger.tap(1, CLIENT_A))
    await engine.handle_incoming(messenger.tap("09:00", CLIENT_A))
    await engine.handle_incoming(messenger.tap("Confirmar", CLIENT_A))

    # Event created in Turnos calendar
    turnos = await calendar.list_events("turnos", "2026-10-29T00:00", "2026-10-30T00:00")
    assert len(turnos) == 1
    assert turnos[0].title == "Limpieza facial – Ana"
    assert turnos[0].description == CLIENT_A

    # Reminder row created (since 2026-10-29 09:00 is >48h after 2026-10-26 09:00)
    rem = engine.db.execute("SELECT * FROM reminders").fetchone()
    assert rem is not None
    assert rem["event_id"] == turnos[0].id

    # Admin alerted via alerta_admin template
    admin_msg = messenger.last_message(ADMIN)
    assert admin_msg is not None
    assert admin_msg.kind == "template"
    assert admin_msg.template_name == "alerta_admin"


@pytest.mark.asyncio
async def test_calendar_error_tells_client_to_retry_and_alerts_admin_without_claiming_success():
    engine, messenger, calendar, _ = _make_engine()
    await calendar.create_event(
        "available", "2026-10-29T09:00", "2026-10-29T12:00", "Disponible"
    )

    # Onboard client
    await engine.handle_incoming(Incoming(wamid="w1", from_phone=CLIENT_A, text="hola"))
    await engine.handle_incoming(Incoming(wamid="w2", from_phone=CLIENT_A, text="Ana"))

    # Make the next calendar call fail when confirming booking
    calendar.fail_next()
    await engine.handle_incoming(
        Incoming(
            wamid="w3",
            from_phone=CLIENT_A,
            kind="button_reply",
            payload_id=encode("confirm", svc="Limpieza facial", t="2026-10-29T09:00"),
        )
    )

    assert messenger.last_message(CLIENT_A).text == RETRY_LATER_TEXT
    admin_msg = messenger.last_message(ADMIN)
    assert admin_msg is not None
    assert admin_msg.kind == "template"
    assert admin_msg.template_name == "alerta_admin"


@pytest.mark.asyncio
async def test_calendar_error_on_create_event_aborts_before_success_message():
    engine, messenger, calendar, _ = _make_engine()
    await calendar.create_event(
        "available", "2026-10-29T09:00", "2026-10-29T12:00", "Disponible"
    )

    await engine.handle_incoming(Incoming(wamid="w1", from_phone=CLIENT_A, text="hola"))
    await engine.handle_incoming(Incoming(wamid="w2", from_phone=CLIENT_A, text="Ana"))

    async def failing_create_event(*args, **kwargs):
        raise CalendarError("Failed to create event in Turnos")

    calendar.create_event = failing_create_event  # type: ignore[method-assign]

    await engine.handle_incoming(
        Incoming(
            wamid="w3",
            from_phone=CLIENT_A,
            kind="button_reply",
            payload_id=encode("confirm", svc="Limpieza facial", t="2026-10-29T09:00"),
        )
    )

    client_texts = [m.text for m in messenger.messages_for(CLIENT_A) if m.kind == "text"]
    assert not any("Reservamos tu turno" in t for t in client_texts)
    assert messenger.last_message(CLIENT_A).text == RETRY_LATER_TEXT
    assert engine.db.execute("SELECT * FROM appointments").fetchone() is None
    assert engine.db.execute("SELECT * FROM reminders").fetchone() is None
    admin_msgs = messenger.messages_for(ADMIN)
    assert len(admin_msgs) == 1
    assert "Error de Google Calendar" in admin_msgs[0].template_params[0]


@pytest.mark.asyncio
async def test_execute_actions_reschedule_cancel_confirm_event_and_vip_alert():
    engine, messenger, calendar, clock = _make_engine()
    old_ev = await calendar.create_event(
        "turnos",
        "2026-10-29T09:00",
        "2026-10-29T10:00",
        "Limpieza facial – Ana",
        CLIENT_A,
    )
    engine._save_appointment_and_reminder(
        old_ev.id, CLIENT_A, _to_tz("2026-10-29T09:00"), clock.now()
    )
    engine.db.commit()

    # 1. ConfirmEvent prefixes title with ✅
    await engine.execute_actions([ConfirmEvent(phone=CLIENT_A, event_id=old_ev.id)])
    updated_old = await calendar.get_event("turnos", old_ev.id)
    assert updated_old is not None
    assert updated_old.title.startswith("✅ ")

    # 2. Reschedule creates new event FIRST, then deletes old event, and updates DB rows
    call_order: list[str] = []
    orig_create = calendar.create_event
    orig_delete = calendar.delete_event

    async def tracked_create(*args, **kwargs):
        call_order.append("create")
        return await orig_create(*args, **kwargs)

    async def tracked_delete(*args, **kwargs):
        call_order.append("delete")
        return await orig_delete(*args, **kwargs)

    calendar.create_event = tracked_create  # type: ignore[method-assign]
    calendar.delete_event = tracked_delete  # type: ignore[method-assign]

    await engine.execute_actions(
        [
            Reschedule(
                phone=CLIENT_A,
                client_name="Ana",
                service="Limpieza facial",
                old_event_id=old_ev.id,
                new_start=_to_tz("2026-10-30T11:00"),
            ),
            AlertAdmin(text="Reprogramado", vip_alert_phone=CLIENT_A),
            SendTemplate(
                to=CLIENT_A,
                name="recordatorio_turno",
                params=("Ana", "Limpieza facial", "viernes 30/10 11:00"),
            ),
        ]
    )
    assert call_order == ["create", "delete"]
    assert await calendar.get_event("turnos", old_ev.id) is None
    turnos = await calendar.list_events("turnos", "2026-10-30T00:00", "2026-10-31T00:00")
    assert len(turnos) == 1
    new_ev = turnos[0]
    assert engine.db.execute(
        "SELECT event_id FROM appointments WHERE event_id = ?", (new_ev.id,)
    ).fetchone() is not None
    assert engine.db.execute(
        "SELECT phone FROM vip_alerts WHERE phone = ?", (CLIENT_A,)
    ).fetchone() is not None
    assert messenger.last_message(CLIENT_A).kind == "template"

    # 3. Cancel deletes event and removes DB rows
    await engine.execute_actions([Cancel(phone=CLIENT_A, event_id=new_ev.id)])
    assert await calendar.get_event("turnos", new_ev.id) is None
    assert engine.db.execute("SELECT * FROM appointments").fetchone() is None
    assert engine.db.execute("SELECT * FROM reminders").fetchone() is None


@pytest.mark.asyncio
async def test_concurrent_confirm_race_allows_only_one_booking():
    engine, messenger, calendar, clock = _make_engine()
    clock.set("2026-12-18T09:00:00")
    assert engine.booking_lock is BOOKING_LOCK
    await calendar.create_event(
        "available", "2026-12-21T10:00", "2026-12-21T12:00", "Disponible"
    )

    # Onboard both clients
    for idx, (phone, name) in enumerate([(CLIENT_A, "Ana"), (CLIENT_B, "Bea")], start=1):
        await engine.handle_incoming(Incoming(wamid=f"init_{idx}a", from_phone=phone, text="hi"))
        await engine.handle_incoming(Incoming(wamid=f"init_{idx}b", from_phone=phone, text=name))

    orig_list_events = calendar.list_events
    orig_create_event = calendar.create_event

    async def yielding_list_events(cal_id, start, end):
        await asyncio.sleep(0)
        return await orig_list_events(cal_id, start, end)

    async def yielding_create_event(*args, **kwargs):
        await asyncio.sleep(0)
        return await orig_create_event(*args, **kwargs)

    calendar.list_events = yielding_list_events  # type: ignore[method-assign]
    calendar.create_event = yielding_create_event  # type: ignore[method-assign]

    confirm_payload = encode("confirm", svc="Limpieza facial", t="2026-12-21T10:00")
    await asyncio.gather(
        engine.handle_incoming(
            Incoming(
                wamid="race_a",
                from_phone=CLIENT_A,
                kind="button_reply",
                payload_id=confirm_payload,
            )
        ),
        engine.handle_incoming(
            Incoming(
                wamid="race_b",
                from_phone=CLIENT_B,
                kind="button_reply",
                payload_id=confirm_payload,
            )
        ),
    )

    # Exactly one Turnos event, one cached appointment row, one reminder row, and one admin alert
    turnos = await orig_list_events("turnos", "2026-12-21T00:00", "2026-12-22T00:00")
    assert len(turnos) == 1
    assert len(engine.db.execute("SELECT * FROM appointments").fetchall()) == 1
    assert len(engine.db.execute("SELECT * FROM reminders").fetchall()) == 1
    assert len(messenger.messages_for(ADMIN)) == 1

    # Winner gets confirmation; loser gets "Ese horario ya no está disponible." + fresh 11:00 slot
    texts = [
        m.text
        for p in (CLIENT_A, CLIENT_B)
        for m in messenger.messages_for(p)
        if m.kind == "text"
    ]
    assert sum("Reservamos tu turno" in t for t in texts) == 1
    assert sum(t == "Ese horario ya no está disponible." for t in texts) == 1
    loser_lists = [
        m
        for p in (CLIENT_A, CLIENT_B)
        for m in messenger.messages_for(p)
        if m.kind == "list"
    ]
    assert len(loser_lists) == 1
    assert [r.title for r in loser_lists[0].rows] == ["11:00"]


@pytest.mark.asyncio
async def test_admin_commands_and_echo_routing():
    engine, messenger, _, _ = _make_engine()

    # vip command
    await engine.handle_incoming(
        Incoming(wamid="a1", from_phone=ADMIN, kind="text", text=f"vip {CLIENT_A}")
    )
    assert "VIP activado" in messenger.last_message(ADMIN).text

    # vips command
    await engine.handle_incoming(
        Incoming(wamid="a2", from_phone=ADMIN, kind="text", text="vips")
    )
    assert CLIENT_A in messenger.last_message(ADMIN).text

    # echo extends mute on CLIENT_A
    await engine.handle_incoming(
        Incoming(wamid="e1", from_phone=ADMIN, to_phone=CLIENT_A, kind="echo", text="Te respondo")
    )
    assert engine._get_client(CLIENT_A).muted_until is not None

    # bot command unmutes CLIENT_A
    await engine.handle_incoming(
        Incoming(wamid="a3", from_phone=ADMIN, kind="text", text=f"bot {CLIENT_A}")
    )
    assert engine._get_client(CLIENT_A).muted_until is None

    # unvip command
    await engine.handle_incoming(
        Incoming(wamid="a4", from_phone=ADMIN, kind="text", text=f"unvip {CLIENT_A}")
    )
    assert engine._get_client(CLIENT_A).vip is False

    # unknown command -> help
    await engine.handle_incoming(
        Incoming(wamid="a5", from_phone=ADMIN, kind="text", text="foobar")
    )
    assert "Comandos disponibles" in messenger.last_message(ADMIN).text


@pytest.mark.asyncio
async def test_admin_alerts_use_alerta_admin_template_in_console_mode():
    buf = io.StringIO()
    cfg = Config(
        messenger="console",
        calendar_backend="fake",
        db_path=":memory:",
        esthetician_name="Lorena",
        admin_phones=(ADMIN,),
    )
    engine = build_app(
        cfg,
        messenger=ConsoleMessenger(stream=buf),
        calendar=FakeCalendar(),
        clock=FakeClock("2026-10-26T09:00:00"),
    )

    await engine.handle_incoming(Incoming(wamid="c1", from_phone=CLIENT_A, text="hola"))
    await engine.handle_incoming(Incoming(wamid="c2", from_phone=CLIENT_A, text="Ana"))
    await engine.handle_incoming(
        Incoming(
            wamid="c3",
            from_phone=CLIENT_A,
            kind="button_reply",
            payload_id=encode("talk"),
        )
    )

    out = buf.getvalue()
    assert f"[TEMPLATE alerta_admin] ({ADMIN})" in out
    assert "Ana (+5491122334455) quiere hablar con vos." in out


def test_build_app_wires_adapters_from_config():
    console_engine = build_app(
        Config(messenger="console", calendar_backend="fake", db_path=":memory:")
    )
    assert isinstance(console_engine.messenger, ConsoleMessenger)
    assert isinstance(console_engine.calendar, FakeCalendar)

    fake_gcal_engine = build_app(
        Config(
            messenger="fake",
            calendar_backend="google",
            db_path=":memory:",
            google_sa_json="{}",
            cal_available_id="avail_id",
            cal_turnos_id="turnos_id",
        )
    )
    assert isinstance(fake_gcal_engine.messenger, FakeMessenger)
    assert isinstance(fake_gcal_engine.calendar, GoogleCalendar)

    with pytest.raises(NotImplementedError):
        build_app(
            Config(
                messenger="cloud",
                calendar_backend="fake",
                db_path=":memory:",
                wa_phone_number_id="1",
                wa_token="t",
                wa_app_secret="s",
                wa_verify_token="v",
            )
        )
