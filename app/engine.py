import asyncio
import sqlite3
from datetime import datetime, timedelta

from app.adapters.console_messenger import ConsoleMessenger
from app.adapters.fake_calendar import DEFAULT_SEED_PATH, FakeCalendar
from app.adapters.fake_messenger import FakeMessenger
from app.adapters.gcal import GoogleCalendar
from app.clock import Clock, SystemClock, _to_tz
from app.config import Config, ServicesConfig, load_config, load_services
from app.conversation import (
    Action,
    AlertAdmin,
    Book,
    Cancel,
    ClientState,
    ConfirmEvent,
    ConversationContext,
    Incoming,
    Reschedule,
    SendButtons,
    SendList,
    SendTemplate,
    SendText,
    SetMute,
    SetName,
    handle,
)
from app.db import init_db
from app.events import add_confirmed_prefix, build_description, build_title
from app.payloads import decode
from app.phones import normalize_phone
from app.ports import CalendarError, CalendarPort, Messenger
from app.reminders import compute_send_at

RETRY_LATER_TEXT = (
    "Hubo un problema con la agenda. Por favor intentá de nuevo en unos minutos."
)
ADMIN_HELP_TEXT = "Comandos disponibles: vip <tel>, unvip <tel>, vips, bot <tel>"
BOOKING_LOCK = asyncio.Lock()


class Engine:
    def __init__(
        self,
        *,
        config: Config,
        services_cfg: ServicesConfig,
        db: sqlite3.Connection,
        messenger: Messenger,
        calendar: CalendarPort,
        clock: Clock | None = None,
        booking_lock: asyncio.Lock | None = None,
    ) -> None:
        self.config = config
        self.services_cfg = services_cfg
        self.db = db
        self.messenger = messenger
        self.calendar = calendar
        self.clock: Clock = clock if clock is not None else SystemClock()
        self.booking_lock = booking_lock if booking_lock is not None else BOOKING_LOCK
        self._admin_phones = frozenset(
            normalize_phone(p) for p in config.admin_phones if p.strip()
        )

    def _get_client(self, phone: str) -> ClientState | None:
        row = self.db.execute(
            "SELECT phone, name, vip, muted_until, awaiting_name FROM clients WHERE phone = ?",
            (phone,),
        ).fetchone()
        if row is None:
            return None
        return ClientState(
            phone=row["phone"],
            name=row["name"],
            vip=bool(row["vip"]),
            muted_until=_to_tz(row["muted_until"]) if row["muted_until"] else None,
            awaiting_name=bool(row["awaiting_name"]),
        )

    def _get_last_vip_alert(self, phone: str) -> datetime | None:
        row = self.db.execute(
            "SELECT last_alert_at FROM vip_alerts WHERE phone = ?",
            (phone,),
        ).fetchone()
        return _to_tz(row["last_alert_at"]) if row and row["last_alert_at"] else None

    def _ensure_client_row(self, phone: str, now: datetime) -> None:
        self.db.execute(
            """
            INSERT OR IGNORE INTO clients (phone, name, vip, muted_until, awaiting_name, created_at)
            VALUES (?, NULL, 0, NULL, 1, ?)
            """,
            (phone, now.isoformat()),
        )

    async def _send_admin_alert(self, text: str) -> None:
        for admin_phone in sorted(self._admin_phones):
            await self.messenger.send_template(admin_phone, "alerta_admin", (text,))

    async def _handle_calendar_failure(self, client_phone: str, exc: CalendarError) -> None:
        await self.messenger.send_text(client_phone, RETRY_LATER_TEXT)
        await self._send_admin_alert(
            f"Error de Google Calendar atendiendo a {client_phone}: {exc}"
        )

    async def _handle_admin_command(self, admin_phone: str, text: str) -> None:
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        now = self.clock.now()

        if cmd == "vips" and not arg:
            rows = self.db.execute(
                "SELECT phone, name FROM clients WHERE vip = 1 ORDER BY phone"
            ).fetchall()
            if not rows:
                await self.messenger.send_text(admin_phone, "No hay clientas VIP registradas.")
                return
            lines = [
                f"- {r['name']} ({r['phone']})" if r["name"] else f"- {r['phone']}"
                for r in rows
            ]
            await self.messenger.send_text(admin_phone, "Clientas VIP:\n" + "\n".join(lines))
            return

        if cmd in ("vip", "unvip", "bot") and arg:
            try:
                target = normalize_phone(arg)
            except ValueError:
                await self.messenger.send_text(admin_phone, ADMIN_HELP_TEXT)
                return

            self._ensure_client_row(target, now)
            if cmd == "vip":
                self.db.execute("UPDATE clients SET vip = 1 WHERE phone = ?", (target,))
                self.db.commit()
                await self.messenger.send_text(admin_phone, f"VIP activado para {target}.")
                return
            if cmd == "unvip":
                self.db.execute("UPDATE clients SET vip = 0 WHERE phone = ?", (target,))
                self.db.commit()
                await self.messenger.send_text(admin_phone, f"VIP desactivado para {target}.")
                return
            if cmd == "bot":
                self.db.execute(
                    "UPDATE clients SET muted_until = NULL WHERE phone = ?", (target,)
                )
                self.db.commit()
                await self.messenger.send_text(admin_phone, f"Bot reactivado para {target}.")
                return

        await self.messenger.send_text(admin_phone, ADMIN_HELP_TEXT)

    def _needs_calendar(self, client: ClientState | None, incoming: Incoming) -> bool:
        if incoming.kind == "echo":
            return False
        if client is None or client.awaiting_name or client.name is None:
            return False
        if incoming.kind not in ("list_reply", "button_reply", "template_button"):
            return False
        try:
            payload = decode(incoming.payload_id)
        except ValueError:
            return False
        return payload.kind not in ("menu", "talk", "vip_noslot") and not (
            payload.kind == "svc" and not payload.get("svc")
        )

    def _is_confirm_tap(self, incoming: Incoming) -> bool:
        if incoming.kind not in ("list_reply", "button_reply", "template_button"):
            return False
        try:
            return decode(incoming.payload_id).kind in ("confirm", "resched_confirm")
        except ValueError:
            return False

    async def _build_context(
        self, client: ClientState | None, phone: str, *, fetch_calendar: bool
    ) -> ConversationContext:
        now = self.clock.now()
        avail = ()
        turnos = ()
        if fetch_calendar:
            horizon_days = max(
                self.services_cfg.horizon_regular, self.services_cfg.horizon_vip
            )
            end = now + timedelta(days=horizon_days + 1)
            avail = await self.calendar.list_events(self.config.cal_available_id, now, end)
            turnos = await self.calendar.list_events(self.config.cal_turnos_id, now, end)
        return ConversationContext(
            now=now,
            services_cfg=self.services_cfg,
            esthetician_name=self.config.esthetician_name,
            available_blocks=avail,
            turnos_events=turnos,
            last_vip_alert_at=self._get_last_vip_alert(phone),
        )

    def _save_appointment_and_reminder(
        self, event_id: str, phone: str, start: datetime, now: datetime
    ) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO appointments (event_id, phone, start_at) VALUES (?, ?, ?)",
            (event_id, phone, _to_tz(start).isoformat()),
        )
        send_at = compute_send_at(start, now)
        if send_at is not None:
            self.db.execute(
                "INSERT OR REPLACE INTO reminders (event_id, send_at, sent_at) VALUES (?, ?, NULL)",
                (event_id, send_at.isoformat()),
            )
        else:
            self.db.execute("DELETE FROM reminders WHERE event_id = ?", (event_id,))

    def _remove_appointment_and_reminder(self, event_id: str) -> None:
        self.db.execute("DELETE FROM appointments WHERE event_id = ?", (event_id,))
        self.db.execute("DELETE FROM reminders WHERE event_id = ?", (event_id,))

    async def execute_actions(
        self, actions: list[Action], now: datetime | None = None
    ) -> None:
        now_dt = now if now is not None else self.clock.now()
        # Execute state/calendar mutations first so CalendarError aborts before any success message
        outbound: list[Action] = []
        for act in actions:
            if isinstance(act, SetName):
                self._ensure_client_row(act.phone, now_dt)
                self.db.execute(
                    "UPDATE clients SET name = ?, awaiting_name = ? WHERE phone = ?",
                    (act.name, 1 if act.awaiting_name else 0, act.phone),
                )
                self.db.commit()
            elif isinstance(act, SetMute):
                self._ensure_client_row(act.phone, now_dt)
                self.db.execute(
                    "UPDATE clients SET muted_until = ? WHERE phone = ?",
                    (act.muted_until.isoformat() if act.muted_until else None, act.phone),
                )
                self.db.commit()
            elif isinstance(act, Book):
                svc = self.services_cfg.get(act.service)
                end = act.start + timedelta(minutes=svc.duration)
                ev = await self.calendar.create_event(
                    self.config.cal_turnos_id,
                    act.start,
                    end,
                    build_title(act.service, act.client_name),
                    build_description(act.phone),
                )
                self._save_appointment_and_reminder(ev.id, act.phone, act.start, now_dt)
                self.db.commit()
            elif isinstance(act, Reschedule):
                svc = self.services_cfg.get(act.service)
                end = act.new_start + timedelta(minutes=svc.duration)
                ev = await self.calendar.create_event(
                    self.config.cal_turnos_id,
                    act.new_start,
                    end,
                    build_title(act.service, act.client_name),
                    build_description(act.phone),
                )
                await self.calendar.delete_event(self.config.cal_turnos_id, act.old_event_id)
                self._remove_appointment_and_reminder(act.old_event_id)
                self._save_appointment_and_reminder(ev.id, act.phone, act.new_start, now_dt)
                self.db.commit()
            elif isinstance(act, Cancel):
                await self.calendar.delete_event(self.config.cal_turnos_id, act.event_id)
                self._remove_appointment_and_reminder(act.event_id)
                self.db.commit()
            elif isinstance(act, ConfirmEvent):
                ev = await self.calendar.get_event(self.config.cal_turnos_id, act.event_id)
                if ev is None:
                    raise CalendarError(f"Event {act.event_id!r} not found")
                await self.calendar.patch_event(
                    self.config.cal_turnos_id,
                    act.event_id,
                    title=add_confirmed_prefix(ev.title),
                )
            else:
                outbound.append(act)

        for act in outbound:
            if isinstance(act, SendText):
                await self.messenger.send_text(act.to, act.text)
            elif isinstance(act, SendList):
                await self.messenger.send_list(
                    act.to, act.body, act.button_label, act.sections
                )
            elif isinstance(act, SendButtons):
                await self.messenger.send_buttons(act.to, act.body, act.buttons)
            elif isinstance(act, SendTemplate):
                await self.messenger.send_template(
                    act.to, act.name, act.params, act.buttons
                )
            elif isinstance(act, AlertAdmin):
                if act.vip_alert_phone:
                    self.db.execute(
                        "INSERT OR REPLACE INTO vip_alerts (phone, last_alert_at) VALUES (?, ?)",
                        (act.vip_alert_phone, now_dt.isoformat()),
                    )
                    self.db.commit()
                await self._send_admin_alert(act.text)

    async def handle_incoming(self, incoming: Incoming) -> bool:
        now = self.clock.now()
        if incoming.wamid:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO processed_messages (wamid, received_at) VALUES (?, ?)",
                (incoming.wamid, now.isoformat()),
            )
            self.db.commit()
            if cur.rowcount == 0:
                return False

        if incoming.kind == "echo":
            target_phone = normalize_phone(incoming.to_phone or incoming.from_phone)
            client_state = self._get_client(target_phone)
            ctx = ConversationContext(
                now=now,
                services_cfg=self.services_cfg,
                esthetician_name=self.config.esthetician_name,
            )
            actions = handle(client_state, incoming, ctx)
            await self.execute_actions(actions, now)
            return True

        sender = normalize_phone(incoming.from_phone)
        if sender in self._admin_phones and incoming.kind == "text":
            await self._handle_admin_command(sender, incoming.text)
            return True

        client_state = self._get_client(sender)
        try:
            if self._is_confirm_tap(incoming):
                async with self.booking_lock:
                    ctx = await self._build_context(
                        client_state, sender, fetch_calendar=True
                    )
                    actions = handle(client_state, incoming, ctx)
                    await self.execute_actions(actions, now)
            else:
                fetch_cal = self._needs_calendar(client_state, incoming)
                ctx = await self._build_context(
                    client_state, sender, fetch_calendar=fetch_cal
                )
                actions = handle(client_state, incoming, ctx)
                await self.execute_actions(actions, now)
        except CalendarError as exc:
            await self._handle_calendar_failure(sender, exc)

        return True


def build_app(
    config: Config | None = None,
    *,
    services_cfg: ServicesConfig | None = None,
    db: sqlite3.Connection | None = None,
    messenger: Messenger | None = None,
    calendar: CalendarPort | None = None,
    clock: Clock | None = None,
) -> Engine:
    cfg = config if config is not None else load_config()
    svcs = services_cfg if services_cfg is not None else load_services()
    conn = db if db is not None else init_db(cfg.db_path)

    if messenger is None:
        if cfg.messenger == "console":
            messenger = ConsoleMessenger()
        elif cfg.messenger == "fake":
            messenger = FakeMessenger()
        else:
            raise NotImplementedError("CloudApiMessenger is wired in Track B")

    if calendar is None:
        if cfg.calendar_backend == "fake":
            calendar = FakeCalendar(seed_path=DEFAULT_SEED_PATH)
        else:
            calendar = GoogleCalendar(sa_json=cfg.google_sa_json)

    return Engine(
        config=cfg,
        services_cfg=svcs,
        db=conn,
        messenger=messenger,
        calendar=calendar,
        clock=clock,
    )
