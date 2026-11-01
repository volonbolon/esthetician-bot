from datetime import datetime

from app.clock import TZ
from app.config import Service, ServicesConfig
from app.conversation import (
    DATA_NOTICE,
    STALE_TAP_TEXT,
    AlertAdmin,
    Book,
    Button,
    CalendarEvent,
    Cancel,
    ClientState,
    ConfirmEvent,
    ConversationContext,
    Incoming,
    Reschedule,
    SendButtons,
    SendList,
    SendText,
    SetMute,
    SetName,
    handle,
)
from app.payloads import encode
from app.slots import Block

PHONE = "+5491122334455"
OTHER_PHONE = "+5491199998888"


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def _cfg() -> ServicesConfig:
    return ServicesConfig(
        services=(
            Service(name="Limpieza facial", duration=60),
            Service(name="Peeling", duration=45),
        ),
        grid_step=30,
        buffer=0,
        horizon_regular=14,
        horizon_vip=30,
        min_notice_h=2,
        cancel_cutoff_h=24,
    )


def _ctx(
    *,
    now: str = "2026-10-26T09:00",
    blocks: list[Block] | None = None,
    turnos: list[CalendarEvent] | None = None,
    last_vip_alert_at: str | None = None,
    esthetician_name: str = "Lorena",
) -> ConversationContext:
    return ConversationContext(
        now=now,
        services_cfg=_cfg(),
        esthetician_name=esthetician_name,
        available_blocks=blocks
        if blocks is not None
        else [Block("2026-10-27T09:00", "2026-10-27T11:00")],
        turnos_events=turnos or [],
        last_vip_alert_at=last_vip_alert_at,
    )


def _client(
    *,
    name: str | None = "Ana",
    vip: bool = False,
    muted_until: str | None = None,
    awaiting_name: bool = False,
) -> ClientState:
    return ClientState(
        phone=PHONE,
        name=name,
        vip=vip,
        muted_until=_dt(muted_until) if muted_until else None,
        awaiting_name=awaiting_name,
    )


def test_new_number_sends_data_notice_and_asks_name():
    actions = handle(
        None,
        Incoming(wamid="w1", from_phone=PHONE, kind="text", text="hola"),
        _ctx(),
    )
    assert actions == [
        SetName(phone=PHONE, name=None, awaiting_name=True),
        SendText(to=PHONE, text=DATA_NOTICE),
    ]


def test_awaiting_name_stores_name_and_shows_main_menu():
    state = _client(name=None, awaiting_name=True)
    actions = handle(
        state,
        Incoming(wamid="w2", from_phone="011 15 2233-4455", kind="text", text="  Ana Pérez  "),
        _ctx(),
    )
    assert actions[0] == SetName(phone=PHONE, name="Ana Pérez", awaiting_name=False)
    assert isinstance(actions[1], SendButtons)
    assert [b.title for b in actions[1].buttons] == ["Reservar", "Mi turno", "Hablar con Lorena"]


def test_awaiting_name_blank_text_reprompts_and_pre_vip_gets_data_notice():
    # Pre-created VIP row with name=None, awaiting_name=False still gets DATA_NOTICE first
    pre_vip = _client(name=None, vip=True, awaiting_name=False)
    first_actions = handle(
        pre_vip,
        Incoming(wamid="w1", from_phone=PHONE, kind="text", text="hola"),
        _ctx(),
    )
    assert first_actions == [
        SetName(phone=PHONE, name=None, awaiting_name=True),
        SendText(to=PHONE, text=DATA_NOTICE),
    ]

    # Blank text while awaiting_name reprompts
    awaiting = _client(name=None, vip=True, awaiting_name=True)
    reprompt = handle(
        awaiting,
        Incoming(wamid="w2", from_phone=PHONE, kind="text", text="   "),
        _ctx(),
    )
    assert reprompt == [
        SendText(to=PHONE, text="Por favor escribí tu nombre para continuar.")
    ]


def test_off_script_message_shows_main_menu():
    actions = handle(
        _client(),
        Incoming(wamid="w3", from_phone=PHONE, kind="text", text="cuanto sale?"),
        _ctx(),
    )
    assert actions == [
        SendButtons(
            to=PHONE,
            body="¿En qué te puedo ayudar?",
            buttons=(
                Button(id=encode("svc"), title="Reservar"),
                Button(id=encode("mine"), title="Mi turno"),
                Button(id=encode("talk"), title="Hablar con Lorena"),
            ),
        )
    ]

    # Explicit menu button tap and 20-char button label truncation
    menu_tap = handle(
        _client(),
        Incoming(wamid="w3b", from_phone=PHONE, kind="button", payload_id=encode("menu")),
        _ctx(esthetician_name="María Antonieta"),
    )
    assert isinstance(menu_tap[0], SendButtons)
    assert menu_tap[0].buttons[2].title == "Hablar con María Ant"
    assert len(menu_tap[0].buttons[2].title) <= 20


def test_muted_silences_menu_and_menu_keyword_unmutes():
    muted = _client(muted_until="2026-10-26T21:00")
    assert handle(
        muted,
        Incoming(wamid="w4", from_phone=PHONE, kind="text", text="hola"),
        _ctx(),
    ) == []

    unmute_actions = handle(
        muted,
        Incoming(wamid="w5", from_phone=PHONE, kind="text", text="MENU"),
        _ctx(),
    )
    assert unmute_actions[0] == SetMute(phone=PHONE, muted_until=None)
    assert isinstance(unmute_actions[1], SendButtons)


def test_echo_extends_mute_and_never_replies():
    state = _client()
    actions = handle(
        state,
        Incoming(wamid="w_echo", from_phone="+5491100000000", to_phone=PHONE, kind="echo", text="Hola Ana"),
        _ctx(now="2026-10-26T09:00"),
    )
    assert actions == [SetMute(phone=PHONE, muted_until=_dt("2026-10-26T21:00"))]


def test_talk_sets_mute_and_alerts_admin():
    actions = handle(
        _client(),
        Incoming(wamid="w6", from_phone=PHONE, kind="button_reply", payload_id=encode("talk")),
        _ctx(now="2026-10-26T09:00"),
    )
    assert actions[0] == SetMute(phone=PHONE, muted_until=_dt("2026-10-26T21:00"))
    assert isinstance(actions[1], AlertAdmin)
    assert isinstance(actions[2], SendText)


def test_booking_flow_service_day_time_confirm():
    ctx = _ctx()
    client = _client()

    # 1. Tap Reservar -> service list
    svc_list_actions = handle(
        client,
        Incoming(wamid="w10", from_phone=PHONE, kind="button_reply", payload_id=encode("svc")),
        ctx,
    )
    assert len(svc_list_actions) == 1
    assert isinstance(svc_list_actions[0], SendList)
    assert [r.title for r in svc_list_actions[0].rows] == ["Limpieza facial", "Peeling"]

    # 2. Tap service -> day list
    day_list_actions = handle(
        client,
        Incoming(
            wamid="w11",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=encode("svc", svc="Limpieza facial"),
        ),
        ctx,
    )
    assert isinstance(day_list_actions[0], SendList)
    assert [r.title for r in day_list_actions[0].rows] == ["Mar 27/10"]

    # 3. Tap day -> time list
    time_list_actions = handle(
        client,
        Incoming(
            wamid="w12",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=encode("day", svc="Limpieza facial", d="2026-10-27"),
        ),
        ctx,
    )
    assert isinstance(time_list_actions[0], SendList)
    assert [r.title for r in time_list_actions[0].rows] == ["09:00", "09:30", "10:00"]

    # 4. Tap slot -> confirm buttons
    slot_actions = handle(
        client,
        Incoming(
            wamid="w13",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=encode("slot", svc="Limpieza facial", t="2026-10-27T09:30"),
        ),
        ctx,
    )
    assert isinstance(slot_actions[0], SendButtons)

    # 5. Tap confirm -> Book + SendText + AlertAdmin
    confirm_actions = handle(
        client,
        Incoming(
            wamid="w14",
            from_phone=PHONE,
            kind="button_reply",
            payload_id=encode("confirm", svc="Limpieza facial", t="2026-10-27T09:30"),
        ),
        ctx,
    )
    assert confirm_actions[0] == Book(
        phone=PHONE,
        service="Limpieza facial",
        start=_dt("2026-10-27T09:30"),
        client_name="Ana",
    )
    assert isinstance(confirm_actions[1], SendText)
    assert isinstance(confirm_actions[2], AlertAdmin)


def test_booking_ver_mas_paging_and_vip_horizon_and_blocks():
    # 11 regular days (Oct 27 .. Nov 6), with Oct 27 having 13 slots (09:00..16:00),
    # plus a [VIP] block on Oct 28 evening and a block on Nov 15 (+20d, VIP horizon only).
    blocks = [
        Block("2026-10-27T09:00", "2026-10-27T16:00"),
        *[
            Block(f"2026-10-{d:02d}T09:00", f"2026-10-{d:02d}T10:00")
            for d in range(28, 32)
        ],
        *[
            Block(f"2026-11-{d:02d}T09:00", f"2026-11-{d:02d}T10:00")
            for d in range(1, 7)
        ],
        Block("2026-10-28T18:00", "2026-10-28T20:00", title="[VIP]"),
        Block("2026-11-15T09:00", "2026-11-15T11:00"),
    ]
    ctx = _ctx(now="2026-10-26T09:00", blocks=blocks)
    reg_client = _client(vip=False)
    vip_client = _client(vip=True)

    # Regular client: page 1 of days has 9 days + "Ver más" (10 rows total)
    reg_days_p1 = handle(
        reg_client,
        Incoming(
            wamid="p1",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=encode("svc", svc="Limpieza facial"),
        ),
        ctx,
    )[0]
    assert isinstance(reg_days_p1, SendList)
    assert len(reg_days_p1.rows) == 10
    assert reg_days_p1.rows[-1].title == "Ver más"

    # Tap "Ver más" for days -> remaining 2 regular days (Nov 5, Nov 6); Nov 15 excluded by 14d horizon
    reg_days_p2 = handle(
        reg_client,
        Incoming(
            wamid="p2",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=reg_days_p1.rows[-1].id,
        ),
        ctx,
    )[0]
    assert isinstance(reg_days_p2, SendList)
    assert [r.title for r in reg_days_p2.rows] == ["Jue 05/11", "Vie 06/11"]

    # Regular client on Oct 28 only sees 09:00 (not the [VIP] 18:00..20:00 block)
    reg_oct28 = handle(
        reg_client,
        Incoming(
            wamid="p3",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=encode("day", svc="Limpieza facial", d="2026-10-28"),
        ),
        ctx,
    )[0]
    assert isinstance(reg_oct28, SendList)
    assert [r.title for r in reg_oct28.rows] == ["09:00"]

    # Regular client on Oct 27: 13 slots -> page 1 has 9 slots + "Ver más", page 2 has 4 slots
    reg_times_p1 = handle(
        reg_client,
        Incoming(
            wamid="p4",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=encode("day", svc="Limpieza facial", d="2026-10-27"),
        ),
        ctx,
    )[0]
    assert isinstance(reg_times_p1, SendList)
    assert len(reg_times_p1.rows) == 10
    assert reg_times_p1.rows[-1].title == "Ver más"

    reg_times_p2 = handle(
        reg_client,
        Incoming(
            wamid="p5",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=reg_times_p1.rows[-1].id,
        ),
        ctx,
    )[0]
    assert isinstance(reg_times_p2, SendList)
    assert [r.title for r in reg_times_p2.rows] == ["13:30", "14:00", "14:30", "15:00"]

    # VIP client on Oct 28 sees both regular 09:00 and [VIP] 18:00..19:00 + "No encuentro horario"
    vip_oct28 = handle(
        vip_client,
        Incoming(
            wamid="p6",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=encode("day", svc="Limpieza facial", d="2026-10-28"),
        ),
        ctx,
    )[0]
    assert isinstance(vip_oct28, SendList)
    assert [r.title for r in vip_oct28.rows] == [
        "09:00",
        "18:00",
        "18:30",
        "19:00",
        "No encuentro horario",
    ]

    # VIP client sees Nov 15 (+20d, within 30d VIP horizon) on page 2 of days
    vip_days_p1 = handle(
        vip_client,
        Incoming(
            wamid="p7",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=encode("svc", svc="Limpieza facial"),
        ),
        ctx,
    )[0]
    assert isinstance(vip_days_p1, SendList)
    assert len(vip_days_p1.rows) == 10
    assert vip_days_p1.rows[-2].title == "Ver más"
    assert vip_days_p1.rows[-1].title == "No encuentro horario"

    vip_days_p2 = handle(
        vip_client,
        Incoming(
            wamid="p8",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=vip_days_p1.rows[-2].id,
        ),
        ctx,
    )[0]
    assert isinstance(vip_days_p2, SendList)
    assert "Dom 15/11" in [r.title for r in vip_days_p2.rows]


def test_stale_tap_returns_unavailable_and_fresh_options():
    ctx = _ctx(
        blocks=[
            Block("2026-10-27T09:00", "2026-10-27T10:00"),
            Block("2026-10-28T09:00", "2026-10-28T10:00"),
        ]
    )
    # Tap a slot at 15:00 on Oct 27 -> returns STALE_TAP_TEXT + remaining fresh slots on Oct 27
    actions = handle(
        _client(),
        Incoming(
            wamid="w15",
            from_phone=PHONE,
            kind="button_reply",
            payload_id=encode("confirm", svc="Limpieza facial", t="2026-10-27T15:00"),
        ),
        ctx,
    )
    assert actions[0] == SendText(to=PHONE, text=STALE_TAP_TEXT)
    assert isinstance(actions[1], SendList)
    assert [r.title for r in actions[1].rows] == ["09:00"]

    # Tap a slot on a day with no slots left -> returns STALE_TAP_TEXT + fresh day list
    day_gone_actions = handle(
        _client(),
        Incoming(
            wamid="w15b",
            from_phone=PHONE,
            kind="button_reply",
            payload_id=encode("confirm", svc="Limpieza facial", t="2026-10-29T09:00"),
        ),
        ctx,
    )
    assert day_gone_actions[0] == SendText(to=PHONE, text=STALE_TAP_TEXT)
    assert isinstance(day_gone_actions[1], SendList)
    assert [r.title for r in day_gone_actions[1].rows] == ["Mar 27/10", "Mié 28/10"]

    # One-active rule on confirm: client already has an active appointment
    existing_ev = CalendarEvent(
        id="evt_existing",
        start="2026-10-28T09:00",
        end="2026-10-28T10:00",
        title="Limpieza facial – Ana",
        description=PHONE,
    )
    one_active_actions = handle(
        _client(),
        Incoming(
            wamid="w15c",
            from_phone=PHONE,
            kind="button_reply",
            payload_id=encode("confirm", svc="Limpieza facial", t="2026-10-27T09:00"),
        ),
        _ctx(blocks=[Block("2026-10-27T09:00", "2026-10-27T10:00")], turnos=[existing_ev]),
    )
    assert not any(isinstance(a, Book) for a in one_active_actions)
    assert isinstance(one_active_actions[0], SendText)
    assert "Ya tenés un turno activo" in one_active_actions[0].text
    assert isinstance(one_active_actions[1], SendButtons)

    # Garbage payload
    garbage_actions = handle(
        _client(),
        Incoming(wamid="w16", from_phone=PHONE, kind="button_reply", payload_id="garbage"),
        ctx,
    )
    assert garbage_actions[0] == SendText(to=PHONE, text=STALE_TAP_TEXT)
    assert isinstance(garbage_actions[1], SendButtons)


def test_cancel_outside_and_inside_cutoff_and_ownership():
    ev = CalendarEvent(
        id="evt1",
        start="2026-10-28T10:00",
        end="2026-10-28T11:00",
        title="Limpieza facial – Ana",
        description=PHONE,
    )
    # Outside 24h cutoff -> Cancel allowed
    outside_ctx = _ctx(now="2026-10-26T09:00", turnos=[ev])
    outside_actions = handle(
        _client(),
        Incoming(
            wamid="w17",
            from_phone=PHONE,
            kind="button_reply",
            payload_id=encode("cancel", evt="evt1"),
        ),
        outside_ctx,
    )
    assert outside_actions[0] == Cancel(phone=PHONE, event_id="evt1")
    assert isinstance(outside_actions[1], SendText)
    assert isinstance(outside_actions[2], AlertAdmin)

    # Inside 24h cutoff -> tell client to contact her + AlertAdmin, no Cancel action
    inside_ctx = _ctx(now="2026-10-27T12:00", turnos=[ev])
    inside_actions = handle(
        _client(),
        Incoming(
            wamid="w18",
            from_phone=PHONE,
            kind="button_reply",
            payload_id=encode("cancel", evt="evt1"),
        ),
        inside_ctx,
    )
    assert not any(isinstance(a, Cancel) for a in inside_actions)
    assert isinstance(inside_actions[0], SendText)
    assert isinstance(inside_actions[1], AlertAdmin)

    # Ownership mismatch -> rejected as stale tap
    other_client = ClientState(phone=OTHER_PHONE, name="Otra")
    wrong_owner = handle(
        other_client,
        Incoming(
            wamid="w19",
            from_phone=OTHER_PHONE,
            kind="button_reply",
            payload_id=encode("cancel", evt="evt1"),
        ),
        outside_ctx,
    )
    assert wrong_owner[0] == SendText(to=OTHER_PHONE, text=STALE_TAP_TEXT)
    assert not any(isinstance(a, Cancel) for a in wrong_owner)


def test_reschedule_and_reminder_confirm():
    ev = CalendarEvent(
        id="evt1",
        start="2026-10-29T10:00",
        end="2026-10-29T11:00",
        title="Limpieza facial – Ana",
        description=PHONE,
    )
    ctx = _ctx(now="2026-10-26T09:00", turnos=[ev])

    # Reminder confirm button
    rem_actions = handle(
        _client(),
        Incoming(
            wamid="w20",
            from_phone=PHONE,
            kind="template_button",
            payload_id=encode("rem:confirm", evt="evt1"),
        ),
        ctx,
    )
    assert rem_actions[0] == ConfirmEvent(phone=PHONE, event_id="evt1")
    assert isinstance(rem_actions[1], SendText)

    # Reschedule confirm excludes old event from one-active rule
    resched_actions = handle(
        _client(),
        Incoming(
            wamid="w21",
            from_phone=PHONE,
            kind="button_reply",
            payload_id=encode(
                "confirm", svc="Limpieza facial", t="2026-10-27T09:00", resched="evt1"
            ),
        ),
        ctx,
    )
    assert resched_actions[0] == Reschedule(
        phone=PHONE,
        old_event_id="evt1",
        service="Limpieza facial",
        new_start=_dt("2026-10-27T09:00"),
        client_name="Ana",
    )


def test_vip_sees_no_encuentro_horario_row_and_rate_limits_alert():
    vip_client = _client(vip=True)
    ctx = _ctx()
    day_actions = handle(
        vip_client,
        Incoming(
            wamid="w22",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=encode("svc", svc="Limpieza facial"),
        ),
        ctx,
    )
    assert isinstance(day_actions[0], SendList)
    assert day_actions[0].rows[-1].title == "No encuentro horario"

    # Tap No encuentro horario -> AlertAdmin + SendText
    alert_actions = handle(
        vip_client,
        Incoming(
            wamid="w23",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=encode("vip_noslot", svc="Limpieza facial"),
        ),
        ctx,
    )
    assert any(isinstance(a, AlertAdmin) for a in alert_actions)
    assert any(isinstance(a, SendText) for a in alert_actions)

    # Within 24h of last VIP alert -> SendText only, no AlertAdmin
    throttled_ctx = _ctx(now="2026-10-26T09:00", last_vip_alert_at="2026-10-26T01:00")
    throttled_actions = handle(
        vip_client,
        Incoming(
            wamid="w24",
            from_phone=PHONE,
            kind="list_reply",
            payload_id=encode("vip_noslot", svc="Limpieza facial"),
        ),
        throttled_ctx,
    )
    assert not any(isinstance(a, AlertAdmin) for a in throttled_actions)
    assert len(throttled_actions) == 1
    assert isinstance(throttled_actions[0], SendText)


def test_active_appointment_lookup_by_normalized_phone_in_turnos_descriptions():
    # Past event for same client (ignored), manual event with local phone format,
    # later event for same client, and event with no phone / other phone.
    turnos = [
        CalendarEvent(
            id="evt_past",
            start="2026-10-26T08:00",
            end="2026-10-26T09:00",
            title="Limpieza facial – Ana",
            description=PHONE,
        ),
        CalendarEvent(
            id="evt_other",
            start="2026-10-27T09:00",
            end="2026-10-27T10:00",
            title="Peeling – Otra",
            description="Sin telefono",
        ),
        CalendarEvent(
            id="evt_later",
            start="2026-10-30T11:00",
            end="2026-10-30T12:00",
            title="Limpieza facial – Ana",
            description="+54 11 2233-4455",
        ),
        CalendarEvent(
            id="evt_manual",
            start="2026-10-28T10:30",
            end="2026-10-28T11:15",
            title="Peeling – Ana",
            description="Anotado manual: 011 15-2233-4455 (seña ok)",
        ),
    ]
    ctx = _ctx(now="2026-10-26T09:00", turnos=turnos)

    # Mi turno finds earliest future matching event (evt_manual) via normalized phone
    mine_actions = handle(
        _client(),
        Incoming(wamid="w25", from_phone=PHONE, kind="button_reply", payload_id=encode("mine")),
        ctx,
    )
    assert len(mine_actions) == 1
    assert isinstance(mine_actions[0], SendButtons)
    assert mine_actions[0].body == "Tu turno: Peeling el Mié 28/10 10:30."
    assert mine_actions[0].buttons[0].id == encode("resched", evt="evt_manual")
    assert mine_actions[0].buttons[1].id == encode("cancel", evt="evt_manual")

    # Client with no matching future event sees "No tenés ningún turno agendado."
    none_actions = handle(
        ClientState(phone=OTHER_PHONE, name="Carla"),
        Incoming(wamid="w26", from_phone=OTHER_PHONE, kind="button_reply", payload_id=encode("mine")),
        ctx,
    )
    assert isinstance(none_actions[0], SendButtons)
    assert none_actions[0].body == "No tenés ningún turno agendado."


def test_mi_turno_with_reprogramar_and_cancelar_buttons():
    # 1. No active appointment -> Reservar / Menú buttons
    empty_actions = handle(
        _client(),
        Incoming(wamid="m1", from_phone=PHONE, kind="button_reply", payload_id=encode("mine")),
        _ctx(now="2026-10-26T09:00", turnos=[]),
    )
    assert empty_actions == [
        SendButtons(
            to=PHONE,
            body="No tenés ningún turno agendado.",
            buttons=(
                Button(id=encode("svc"), title="Reservar"),
                Button(id=encode("menu"), title="Menú"),
            ),
        )
    ]

    # 2. Active appointment (including ✅ prefix) -> Reprogramar / Cancelar buttons
    ev = CalendarEvent(
        id="evt_99",
        start="2026-10-29T10:00",
        end="2026-10-29T11:00",
        title="✅ Limpieza facial – Ana",
        description=PHONE,
    )
    ctx = _ctx(now="2026-10-26T09:00", turnos=[ev])
    mine_actions = handle(
        _client(),
        Incoming(wamid="m2", from_phone=PHONE, kind="button_reply", payload_id=encode("mine")),
        ctx,
    )
    assert mine_actions == [
        SendButtons(
            to=PHONE,
            body="Tu turno: Limpieza facial el Jue 29/10 10:00.",
            buttons=(
                Button(id=encode("resched", evt="evt_99"), title="Reprogramar"),
                Button(id=encode("cancel", evt="evt_99"), title="Cancelar"),
            ),
        )
    ]

    # 3. Tap Reprogramar button -> opens service list carrying resched=evt_99
    resched_tap = handle(
        _client(),
        Incoming(
            wamid="m3",
            from_phone=PHONE,
            kind="button_reply",
            payload_id=mine_actions[0].buttons[0].id,
        ),
        ctx,
    )
    assert len(resched_tap) == 1
    assert isinstance(resched_tap[0], SendList)
    assert resched_tap[0].rows[0].id == encode("svc", svc="Limpieza facial", resched="evt_99")

    # 4. Tap Cancelar button -> Cancel + SendText + AlertAdmin
    cancel_tap = handle(
        _client(),
        Incoming(
            wamid="m4",
            from_phone=PHONE,
            kind="button_reply",
            payload_id=mine_actions[0].buttons[1].id,
        ),
        ctx,
    )
    assert cancel_tap[0] == Cancel(phone=PHONE, event_id="evt_99")
    assert isinstance(cancel_tap[1], SendText)
    assert isinstance(cancel_tap[2], AlertAdmin)


def test_ownership_check_on_every_event_referencing_tap():
    ev = CalendarEvent(
        id="evt_owned",
        start="2026-10-29T10:00",
        end="2026-10-29T11:00",
        title="Limpieza facial – Ana",
        description=PHONE,
    )
    ctx = _ctx(now="2026-10-26T09:00", turnos=[ev])
    intruder = ClientState(phone=OTHER_PHONE, name="Intrusa")

    event_payloads = [
        encode("cancel", evt="evt_owned"),
        encode("resched", evt="evt_owned"),
        encode("rem:confirm", evt="evt_owned"),
        encode("rem:resched", evt="evt_owned"),
        encode("rem:cancel", evt="evt_owned"),
        encode("svc", resched="evt_owned"),
        encode("svc", svc="Limpieza facial", resched="evt_owned"),
        encode("day", svc="Limpieza facial", d="2026-10-27", resched="evt_owned"),
        encode("more", step="day", svc="Limpieza facial", off=0, resched="evt_owned"),
        encode(
            "more",
            step="slot",
            svc="Limpieza facial",
            d="2026-10-27",
            off=0,
            resched="evt_owned",
        ),
        encode("slot", svc="Limpieza facial", t="2026-10-27T09:00", resched="evt_owned"),
        encode("confirm", svc="Limpieza facial", t="2026-10-27T09:00", resched="evt_owned"),
    ]

    for idx, pid in enumerate(event_payloads):
        actions = handle(
            intruder,
            Incoming(
                wamid=f"own_{idx}",
                from_phone=OTHER_PHONE,
                kind="button_reply",
                payload_id=pid,
            ),
            ctx,
        )
        assert actions[0] == SendText(to=OTHER_PHONE, text=STALE_TAP_TEXT)
        assert isinstance(actions[1], SendButtons)
        assert not any(
            isinstance(a, (Book, Reschedule, Cancel, ConfirmEvent)) for a in actions
        )
