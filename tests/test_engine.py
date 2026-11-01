import asyncio

import pytest

from app.adapters.fake_calendar import FakeCalendar
from app.adapters.fake_messenger import FakeMessenger
from app.clock import FakeClock
from app.config import Config
from app.conversation import Incoming
from app.db import init_db
from app.engine import RETRY_LATER_TEXT, Engine, build_app
from app.payloads import encode

CLIENT_A = "+5491122334455"
CLIENT_B = "+5491166778899"
ADMIN = "+5491100000001"


def _make_engine() -> tuple[Engine, FakeMessenger, FakeCalendar, FakeClock]:
    cfg = Config(
        messenger="fake",
        calendar_backend="fake",
        db_path=":memory:",
        esthetician_name="Valeria",
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
async def test_concurrent_confirm_race_allows_only_one_booking():
    engine, messenger, calendar, _ = _make_engine()
    await calendar.create_event(
        "available", "2026-10-29T09:00", "2026-10-29T10:00", "Disponible"
    )

    # Onboard both clients
    for idx, (phone, name) in enumerate([(CLIENT_A, "Ana"), (CLIENT_B, "Bea")], start=1):
        await engine.handle_incoming(Incoming(wamid=f"init_{idx}a", from_phone=phone, text="hi"))
        await engine.handle_incoming(Incoming(wamid=f"init_{idx}b", from_phone=phone, text=name))

    confirm_payload = encode("confirm", svc="Limpieza facial", t="2026-10-29T09:00")
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

    turnos = await calendar.list_events("turnos", "2026-10-29T00:00", "2026-10-30T00:00")
    assert len(turnos) == 1
    texts = [
        m.text
        for p in (CLIENT_A, CLIENT_B)
        for m in messenger.messages_for(p)
        if m.kind == "text"
    ]
    assert any("Reservamos tu turno" in t for t in texts)
    assert any("Ese horario ya no está disponible." in t for t in texts)


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
