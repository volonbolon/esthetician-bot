from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from app.clock import _to_tz
from app.config import ServicesConfig
from app.events import extract_phone, parse_title
from app.payloads import Payload, decode, encode
from app.phones import normalize_phone
from app.reminders import within_cutoff
from app.slots import days_with_slots, generate, paginate, slots_for_day

IncomingKind = Literal["text", "list_reply", "button_reply", "template_button", "echo"]

_WEEKDAYS_ES = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
_MUTE_DURATION = timedelta(hours=12)
_VIP_ALERT_COOLDOWN = timedelta(hours=24)

DATA_NOTICE = (
    "Hola! Guardamos tu número y nombre únicamente para gestionar tus turnos. "
    "¿Cómo es tu nombre?"
)
STALE_TAP_TEXT = "Ese horario ya no está disponible."
ADMIN_HELP_TEXT = "Comandos disponibles: vip <tel>, unvip <tel>, vips, bot <tel>"


@dataclass(frozen=True)
class Incoming:
    wamid: str
    from_phone: str
    kind: IncomingKind = "text"
    text: str = ""
    payload_id: str = ""
    to_phone: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class ClientState:
    phone: str
    name: str | None = None
    vip: bool = False
    muted_until: datetime | None = None
    awaiting_name: bool = False


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    start: datetime
    end: datetime
    title: str = ""
    description: str = ""

    def __init__(
        self,
        id: str,
        start: datetime | str,
        end: datetime | str,
        title: str = "",
        description: str = "",
    ) -> None:
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "start", _to_tz(start))
        object.__setattr__(self, "end", _to_tz(end))
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "description", description)


@dataclass(frozen=True)
class ConversationContext:
    now: datetime
    services_cfg: ServicesConfig
    esthetician_name: str = "Lorena"
    available_blocks: Sequence[Any] = ()
    turnos_events: Sequence[Any] = ()
    last_vip_alert_at: datetime | None = None
    admin_phones: Sequence[str] = ()
    vip_clients: Sequence[ClientState] = ()

    def __init__(
        self,
        now: datetime | str,
        services_cfg: ServicesConfig,
        esthetician_name: str = "Lorena",
        available_blocks: Sequence[Any] = (),
        turnos_events: Sequence[Any] = (),
        last_vip_alert_at: datetime | str | None = None,
        admin_phones: Sequence[str] = (),
        vip_clients: Sequence[ClientState] = (),
    ) -> None:
        object.__setattr__(self, "now", _to_tz(now))
        object.__setattr__(self, "services_cfg", services_cfg)
        object.__setattr__(self, "esthetician_name", esthetician_name)
        object.__setattr__(self, "available_blocks", tuple(available_blocks))
        object.__setattr__(self, "turnos_events", tuple(turnos_events))
        object.__setattr__(
            self,
            "last_vip_alert_at",
            _to_tz(last_vip_alert_at) if last_vip_alert_at is not None else None,
        )
        object.__setattr__(
            self,
            "admin_phones",
            tuple(normalize_phone(p) for p in admin_phones if p.strip()),
        )
        object.__setattr__(self, "vip_clients", tuple(vip_clients))


@dataclass(frozen=True)
class ListRow:
    id: str
    title: str
    description: str = ""


@dataclass(frozen=True)
class ListSection:
    title: str
    rows: tuple[ListRow, ...]


@dataclass(frozen=True)
class Button:
    id: str
    title: str


@dataclass(frozen=True)
class SendText:
    to: str
    text: str


@dataclass(frozen=True)
class SendList:
    to: str
    body: str
    button_label: str
    sections: tuple[ListSection, ...]

    @property
    def rows(self) -> tuple[ListRow, ...]:
        return tuple(r for s in self.sections for r in s.rows)


@dataclass(frozen=True)
class SendButtons:
    to: str
    body: str
    buttons: tuple[Button, ...]


@dataclass(frozen=True)
class SendTemplate:
    to: str
    name: str
    params: tuple[str, ...] = field(default_factory=tuple)
    buttons: tuple[Button, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AlertAdmin:
    text: str
    vip_alert_phone: str | None = None


@dataclass(frozen=True)
class SetMute:
    phone: str
    muted_until: datetime | None


@dataclass(frozen=True)
class SetName:
    phone: str
    name: str | None
    awaiting_name: bool = False


@dataclass(frozen=True)
class SetVip:
    phone: str
    vip: bool


@dataclass(frozen=True)
class Book:
    phone: str
    service: str
    start: datetime
    client_name: str


@dataclass(frozen=True)
class Cancel:
    phone: str
    event_id: str


@dataclass(frozen=True)
class Reschedule:
    phone: str
    old_event_id: str
    service: str
    new_start: datetime
    client_name: str


@dataclass(frozen=True)
class ConfirmEvent:
    phone: str
    event_id: str


Action = (
    SendText
    | SendList
    | SendButtons
    | SendTemplate
    | AlertAdmin
    | SetMute
    | SetName
    | SetVip
    | Book
    | Cancel
    | Reschedule
    | ConfirmEvent
)


def _format_day(d: date) -> str:
    return f"{_WEEKDAYS_ES[d.weekday()]} {d.day:02d}/{d.month:02d}"


def _format_dt(dt: datetime) -> str:
    local = _to_tz(dt)
    return f"{_format_day(local.date())} {local:%H:%M}"


def _talk_label(esthetician_name: str) -> str:
    full = f"Hablar con {esthetician_name.strip()}"
    return full if len(full) <= 20 else full[:20].rstrip()


def _main_menu(phone: str, ctx: ConversationContext) -> SendButtons:
    return SendButtons(
        to=phone,
        body="¿En qué te puedo ayudar?",
        buttons=(
            Button(id=encode("svc"), title="Reservar"),
            Button(id=encode("mine"), title="Mi turno"),
            Button(id=encode("talk"), title=_talk_label(ctx.esthetician_name)),
        ),
    )


def _service_list(
    phone: str, ctx: ConversationContext, *, resched: str | None = None
) -> SendList:
    rows = tuple(
        ListRow(
            id=encode("svc", svc=svc.name, resched=resched),
            title=svc.name[:24],
            description=f"{svc.duration} min",
        )
        for svc in ctx.services_cfg.services
    )
    return SendList(
        to=phone,
        body="Elegí un servicio:",
        button_label="Servicios",
        sections=(ListSection(title="Servicios", rows=rows),),
    )


def _busy_excluding(ctx: ConversationContext, exclude_event_id: str | None) -> list[Any]:
    if not exclude_event_id:
        return list(ctx.turnos_events)
    return [e for e in ctx.turnos_events if getattr(e, "id", None) != exclude_event_id]


def _compute_slots(
    ctx: ConversationContext,
    svc_name: str,
    *,
    vip: bool,
    resched: str | None = None,
) -> list[datetime]:
    busy = _busy_excluding(ctx, resched)
    return generate(
        ctx.available_blocks,
        busy,
        ctx.services_cfg,
        svc_name,
        ctx.now,
        vip=vip,
    )


def _day_list_actions(
    phone: str,
    client: ClientState,
    ctx: ConversationContext,
    svc_name: str,
    *,
    offset: int = 0,
    resched: str | None = None,
) -> list[Action]:
    all_slots = _compute_slots(ctx, svc_name, vip=client.vip, resched=resched)
    days = days_with_slots(all_slots)
    page = paginate(days, offset=offset, vip=client.vip)

    if not page.items and offset > 0:
        return [
            SendText(to=phone, text=STALE_TAP_TEXT),
            *_day_list_actions(phone, client, ctx, svc_name, resched=resched),
        ]

    if not page.items and not client.vip:
        return [
            SendText(to=phone, text="No hay horarios disponibles en este momento."),
            _main_menu(phone, ctx),
        ]

    rows: list[ListRow] = [
        ListRow(
            id=encode("day", svc=svc_name, d=d.isoformat(), resched=resched),
            title=_format_day(d),
        )
        for d in page.items
    ]
    if page.has_more and page.next_offset is not None:
        rows.append(
            ListRow(
                id=encode("more", step="day", svc=svc_name, off=page.next_offset, resched=resched),
                title="Ver más",
            )
        )
    if page.include_no_slot:
        rows.append(
            ListRow(
                id=encode("vip_noslot", svc=svc_name),
                title="No encuentro horario",
            )
        )

    return [
        SendList(
            to=phone,
            body=f"Elegí un día para {svc_name}:",
            button_label="Ver días",
            sections=(ListSection(title="Días disponibles", rows=tuple(rows)),),
        )
    ]


def _time_list_actions(
    phone: str,
    client: ClientState,
    ctx: ConversationContext,
    svc_name: str,
    day_str: str,
    *,
    offset: int = 0,
    resched: str | None = None,
) -> list[Action]:
    all_slots = _compute_slots(ctx, svc_name, vip=client.vip, resched=resched)
    day_slots = slots_for_day(all_slots, day_str)
    if not day_slots:
        return [
            SendText(to=phone, text=STALE_TAP_TEXT),
            *_day_list_actions(phone, client, ctx, svc_name, resched=resched),
        ]

    page = paginate(day_slots, offset=offset, vip=client.vip)
    if not page.items:
        return [
            SendText(to=phone, text=STALE_TAP_TEXT),
            *_time_list_actions(phone, client, ctx, svc_name, day_str, resched=resched),
        ]

    rows: list[ListRow] = [
        ListRow(
            id=encode("slot", svc=svc_name, t=s.strftime("%Y-%m-%dT%H:%M"), resched=resched),
            title=s.strftime("%H:%M"),
        )
        for s in page.items
    ]
    if page.has_more and page.next_offset is not None:
        rows.append(
            ListRow(
                id=encode(
                    "more",
                    step="slot",
                    svc=svc_name,
                    d=day_str,
                    off=page.next_offset,
                    resched=resched,
                ),
                title="Ver más",
            )
        )
    if page.include_no_slot:
        rows.append(
            ListRow(
                id=encode("vip_noslot", svc=svc_name),
                title="No encuentro horario",
            )
        )

    return [
        SendList(
            to=phone,
            body=f"Elegí un horario para {svc_name}:",
            button_label="Ver horarios",
            sections=(ListSection(title="Horarios", rows=tuple(rows)),),
        )
    ]


def _stale_slot_actions(
    phone: str,
    client: ClientState,
    ctx: ConversationContext,
    svc_name: str,
    slot_dt: datetime,
    *,
    resched: str | None = None,
) -> list[Action]:
    day_str = slot_dt.date().isoformat()
    all_slots = _compute_slots(ctx, svc_name, vip=client.vip, resched=resched)
    if slots_for_day(all_slots, day_str):
        return [
            SendText(to=phone, text=STALE_TAP_TEXT),
            *_time_list_actions(phone, client, ctx, svc_name, day_str, resched=resched),
        ]
    return [
        SendText(to=phone, text=STALE_TAP_TEXT),
        *_day_list_actions(phone, client, ctx, svc_name, resched=resched),
    ]


def _find_event_by_id(ctx: ConversationContext, event_id: str) -> Any | None:
    for ev in ctx.turnos_events:
        if getattr(ev, "id", None) == event_id:
            return ev
    return None


def _get_owned_future_event(
    ctx: ConversationContext, phone: str, event_id: str | None
) -> Any | None:
    if not event_id:
        return None
    ev = _find_event_by_id(ctx, event_id)
    if ev is None or _to_tz(ev.start) <= ctx.now:
        return None
    if extract_phone(getattr(ev, "description", "")) != normalize_phone(phone):
        return None
    return ev


def _find_active_appointment(
    ctx: ConversationContext, phone: str, *, exclude_event_id: str | None = None
) -> Any | None:
    norm = normalize_phone(phone)
    candidates: list[Any] = []
    for ev in ctx.turnos_events:
        if exclude_event_id and getattr(ev, "id", None) == exclude_event_id:
            continue
        start = _to_tz(ev.start)
        if start <= ctx.now:
            continue
        ev_phone = extract_phone(getattr(ev, "description", ""))
        if ev_phone == norm:
            candidates.append(ev)
    candidates.sort(key=lambda e: _to_tz(e.start))
    return candidates[0] if candidates else None


def _event_service_name(ev: Any) -> str:
    title = getattr(ev, "title", "") or ""
    try:
        return parse_title(title).service
    except ValueError:
        return title or "Turno"


def _handle_payload(
    phone: str, client: ClientState, payload: Payload, ctx: ConversationContext
) -> list[Action]:
    kind = payload.kind

    resched_id = payload.get("resched")
    resched_ev = None
    if resched_id is not None:
        resched_ev = _get_owned_future_event(ctx, phone, resched_id)
        if resched_ev is None:
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]

    if kind == "menu":
        return [_main_menu(phone, ctx)]

    if kind == "talk":
        muted_until = ctx.now + _MUTE_DURATION
        client_label = client.name or phone
        return [
            SetMute(phone=phone, muted_until=muted_until),
            AlertAdmin(text=f"{client_label} ({phone}) quiere hablar con vos."),
            SendText(
                to=phone,
                text=f"Le avisé a {ctx.esthetician_name}. Te va a responder por acá en breve.",
            ),
        ]

    if kind == "svc":
        svc_name = payload.get("svc")
        resched = payload.get("resched")
        if not svc_name:
            return [_service_list(phone, ctx, resched=resched)]
        try:
            ctx.services_cfg.get(svc_name)
        except KeyError:
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]
        return _day_list_actions(phone, client, ctx, svc_name, resched=resched)

    if kind == "day":
        svc_name = payload.get("svc")
        day_str = payload.get("d")
        resched = payload.get("resched")
        if not svc_name or not day_str:
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]
        try:
            ctx.services_cfg.get(svc_name)
            date.fromisoformat(day_str)
        except (KeyError, ValueError):
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]
        return _time_list_actions(phone, client, ctx, svc_name, day_str, resched=resched)

    if kind == "more":
        step = payload.get("step")
        svc_name = payload.get("svc")
        resched = payload.get("resched")
        try:
            offset = int(payload.get("off") or "0")
            if not svc_name:
                raise ValueError
            ctx.services_cfg.get(svc_name)
        except (KeyError, ValueError):
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]

        if step == "day":
            return _day_list_actions(
                phone, client, ctx, svc_name, offset=offset, resched=resched
            )
        if step == "slot":
            day_str = payload.get("d")
            if not day_str:
                return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]
            return _time_list_actions(
                phone, client, ctx, svc_name, day_str, offset=offset, resched=resched
            )
        return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]

    if kind == "slot":
        svc_name = payload.get("svc")
        t_str = payload.get("t")
        resched = payload.get("resched")
        if not svc_name or not t_str:
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]
        try:
            ctx.services_cfg.get(svc_name)
            slot_dt = _to_tz(t_str)
        except (KeyError, ValueError):
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]

        valid_slots = _compute_slots(ctx, svc_name, vip=client.vip, resched=resched)
        if slot_dt not in valid_slots:
            return _stale_slot_actions(phone, client, ctx, svc_name, slot_dt, resched=resched)
        return [
            SendButtons(
                to=phone,
                body=f"¿Confirmás {svc_name} el {_format_dt(slot_dt)}?",
                buttons=(
                    Button(
                        id=encode("confirm", svc=svc_name, t=t_str, resched=resched),
                        title="Confirmar",
                    ),
                    Button(id=encode("menu"), title="Cancelar"),
                ),
            )
        ]

    if kind == "confirm":
        svc_name = payload.get("svc")
        t_str = payload.get("t")
        resched = payload.get("resched")
        if not svc_name or not t_str:
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]
        try:
            ctx.services_cfg.get(svc_name)
            slot_dt = _to_tz(t_str)
        except (KeyError, ValueError):
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]

        if (
            resched
            and resched_ev is not None
            and within_cutoff(resched_ev.start, ctx.now, ctx.services_cfg.cancel_cutoff_h)
        ):
            return _inside_cutoff_actions(
                phone, client, resched_ev, ctx, action_label="reprogramar"
            )

        existing = _find_active_appointment(ctx, phone, exclude_event_id=resched)
        if existing is not None:
            return [
                SendText(
                    to=phone,
                    text=f"Ya tenés un turno activo el {_format_dt(existing.start)}.",
                ),
                _main_menu(phone, ctx),
            ]

        valid_slots = _compute_slots(ctx, svc_name, vip=client.vip, resched=resched)
        if slot_dt not in valid_slots:
            return _stale_slot_actions(phone, client, ctx, svc_name, slot_dt, resched=resched)

        client_name = client.name or phone
        if resched:
            return [
                Reschedule(
                    phone=phone,
                    old_event_id=resched,
                    service=svc_name,
                    new_start=slot_dt,
                    client_name=client_name,
                ),
                SendText(
                    to=phone,
                    text=f"Listo! Reprogramamos tu turno de {svc_name} para el {_format_dt(slot_dt)}.",
                ),
                AlertAdmin(
                    text=f"Turno reprogramado: {svc_name} – {client_name} ({phone}) para {_format_dt(slot_dt)}."
                ),
            ]
        return [
            Book(
                phone=phone,
                service=svc_name,
                start=slot_dt,
                client_name=client_name,
            ),
            SendText(
                to=phone,
                text=f"Listo! Reservamos tu turno de {svc_name} para el {_format_dt(slot_dt)}.",
            ),
            AlertAdmin(
                text=f"Nuevo turno: {svc_name} – {client_name} ({phone}) el {_format_dt(slot_dt)}."
            ),
        ]

    if kind == "vip_noslot":
        if not client.vip:
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]
        svc_name = payload.get("svc") or "turno"
        client_name = client.name or phone
        actions: list[Action] = []
        if (
            ctx.last_vip_alert_at is None
            or (ctx.now - ctx.last_vip_alert_at) >= _VIP_ALERT_COOLDOWN
        ):
            actions.append(
                AlertAdmin(
                    text=f"VIP sin horario: {client_name} ({phone}) busca {svc_name}.",
                    vip_alert_phone=phone,
                )
            )
        actions.append(
            SendText(
                to=phone,
                text=f"Le avisé a {ctx.esthetician_name} que buscás turno para {svc_name}. Te va a escribir pronto!",
            )
        )
        return actions

    if kind == "mine":
        active = _find_active_appointment(ctx, phone)
        if active is None:
            return [
                SendButtons(
                    to=phone,
                    body="No tenés ningún turno agendado.",
                    buttons=(
                        Button(id=encode("svc"), title="Reservar"),
                        Button(id=encode("menu"), title="Menú"),
                    ),
                )
            ]
        svc_name = _event_service_name(active)
        return [
            SendButtons(
                to=phone,
                body=f"Tu turno: {svc_name} el {_format_dt(active.start)}.",
                buttons=(
                    Button(id=encode("resched", evt=active.id), title="Reprogramar"),
                    Button(id=encode("cancel", evt=active.id), title="Cancelar"),
                ),
            )
        ]

    if kind in ("cancel", "resched", "rem:confirm", "rem:resched", "rem:cancel"):
        ev = _get_owned_future_event(ctx, phone, payload.get("evt"))
        if ev is None:
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]

        svc_name = _event_service_name(ev)
        client_name = client.name or phone

        if kind == "rem:confirm":
            return [
                ConfirmEvent(phone=phone, event_id=ev.id),
                SendText(
                    to=phone,
                    text=f"Gracias! Tu turno de {svc_name} el {_format_dt(ev.start)} quedó confirmado.",
                ),
            ]

        if kind in ("cancel", "rem:cancel"):
            if within_cutoff(ev.start, ctx.now, ctx.services_cfg.cancel_cutoff_h):
                return _inside_cutoff_actions(phone, client, ev, ctx, action_label="cancelar")
            return [
                Cancel(phone=phone, event_id=ev.id),
                SendText(
                    to=phone,
                    text=f"Tu turno de {svc_name} del {_format_dt(ev.start)} fue cancelado.",
                ),
                AlertAdmin(
                    text=f"Turno cancelado: {svc_name} – {client_name} ({phone}) del {_format_dt(ev.start)}."
                ),
            ]

        # resched / rem:resched
        if within_cutoff(ev.start, ctx.now, ctx.services_cfg.cancel_cutoff_h):
            return _inside_cutoff_actions(phone, client, ev, ctx, action_label="reprogramar")
        return [_service_list(phone, ctx, resched=ev.id)]

    return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]


def _inside_cutoff_actions(
    phone: str,
    client: ClientState,
    ev: Any,
    ctx: ConversationContext,
    *,
    action_label: str,
) -> list[Action]:
    svc_name = _event_service_name(ev)
    client_name = client.name or phone
    return [
        SendText(
            to=phone,
            text=(
                f"Faltan menos de {ctx.services_cfg.cancel_cutoff_h}h para tu turno. "
                f"Por favor hablá directamente con {ctx.esthetician_name} para {action_label}."
            ),
        ),
        AlertAdmin(
            text=(
                f"{client_name} ({phone}) intentó {action_label} su turno de {svc_name} "
                f"del {_format_dt(ev.start)} con menos de {ctx.services_cfg.cancel_cutoff_h}h."
            )
        ),
    ]


def _handle_admin_command(
    admin_phone: str, text: str, ctx: ConversationContext
) -> list[Action]:
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "vips" and not arg:
        if not ctx.vip_clients:
            return [SendText(to=admin_phone, text="No hay clientas VIP registradas.")]
        lines = [
            f"- {c.name} ({c.phone})" if c.name else f"- {c.phone}"
            for c in sorted(ctx.vip_clients, key=lambda c: c.phone)
        ]
        return [SendText(to=admin_phone, text="Clientas VIP:\n" + "\n".join(lines))]

    if cmd in ("vip", "unvip", "bot") and arg:
        try:
            target = normalize_phone(arg)
        except ValueError:
            return [SendText(to=admin_phone, text=ADMIN_HELP_TEXT)]
        if cmd == "vip":
            return [
                SetVip(phone=target, vip=True),
                SendText(to=admin_phone, text=f"VIP activado para {target}."),
            ]
        if cmd == "unvip":
            return [
                SetVip(phone=target, vip=False),
                SendText(to=admin_phone, text=f"VIP desactivado para {target}."),
            ]
        return [
            SetMute(phone=target, muted_until=None),
            SendText(to=admin_phone, text=f"Bot reactivado para {target}."),
        ]

    return [SendText(to=admin_phone, text=ADMIN_HELP_TEXT)]


def handle(
    client_state: ClientState | None,
    incoming: Incoming,
    ctx: ConversationContext,
) -> list[Action]:
    if incoming.kind == "echo":
        raw_target = incoming.to_phone or (client_state.phone if client_state else incoming.from_phone)
        target_phone = normalize_phone(raw_target)
        new_mute = ctx.now + _MUTE_DURATION
        if client_state and client_state.muted_until is not None:
            new_mute = max(new_mute, _to_tz(client_state.muted_until))
        return [SetMute(phone=target_phone, muted_until=new_mute)]

    phone = normalize_phone(incoming.from_phone)

    if phone in ctx.admin_phones and incoming.kind == "text":
        return _handle_admin_command(phone, incoming.text, ctx)

    if client_state is None or (client_state.name is None and not client_state.awaiting_name):
        return [
            SetName(phone=phone, name=None, awaiting_name=True),
            SendText(to=phone, text=DATA_NOTICE),
        ]

    if client_state.awaiting_name or client_state.name is None:
        if incoming.kind == "text" and incoming.text.strip():
            clean_name = incoming.text.strip()
            return [
                SetName(phone=phone, name=clean_name, awaiting_name=False),
                _main_menu(phone, ctx),
            ]
        return [SendText(to=phone, text="Por favor escribí tu nombre para continuar.")]

    is_muted = (
        client_state.muted_until is not None and _to_tz(client_state.muted_until) > ctx.now
    )
    if is_muted and incoming.kind == "text":
        if incoming.text.strip().lower() == "menu":
            return [
                SetMute(phone=phone, muted_until=None),
                _main_menu(phone, ctx),
            ]
        return []

    if incoming.kind in ("list_reply", "button_reply", "template_button"):
        try:
            payload = decode(incoming.payload_id)
        except ValueError:
            return [SendText(to=phone, text=STALE_TAP_TEXT), _main_menu(phone, ctx)]
        return _handle_payload(phone, client_state, payload, ctx)

    return [_main_menu(phone, ctx)]
