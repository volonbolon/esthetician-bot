from datetime import datetime, timedelta

from app.clock import _to_tz
from app.conversation import Button, _format_dt
from app.engine import Engine
from app.events import extract_phone, parse_title
from app.payloads import encode
from app.ports import CalendarError
from app.reminders import REMINDER_LEAD_H, compute_send_at


async def run_reminders(engine: Engine, now: datetime | str | None = None) -> int:
    now_dt = _to_tz(now) if now is not None else engine.clock.now()
    rows = engine.db.execute(
        """
        SELECT event_id, send_at
        FROM reminders
        WHERE sent_at IS NULL AND send_at <= ?
        ORDER BY send_at, event_id
        """,
        (now_dt.isoformat(),),
    ).fetchall()

    sent_count = 0
    for row in rows:
        event_id = row["event_id"]
        try:
            ev = await engine.calendar.get_event(engine.config.cal_turnos_id, event_id)
        except CalendarError as exc:
            await engine._send_admin_alert(
                f"Error de Google Calendar enviando recordatorio ({event_id}): {exc}"
            )
            continue

        if ev is None or _to_tz(ev.start) <= now_dt:
            engine.db.execute("DELETE FROM reminders WHERE event_id = ?", (event_id,))
            engine.db.execute("DELETE FROM appointments WHERE event_id = ?", (event_id,))
            engine.db.commit()
            continue

        ev_start = _to_tz(ev.start)
        new_send_at = ev_start - timedelta(hours=REMINDER_LEAD_H)
        old_send_at = _to_tz(row["send_at"])
        if new_send_at != old_send_at:
            # Event was moved in Calendar; recompute send_at
            engine.db.execute(
                "UPDATE appointments SET start_at = ? WHERE event_id = ?",
                (ev_start.isoformat(), event_id),
            )
            engine.db.execute(
                "UPDATE reminders SET send_at = ? WHERE event_id = ?",
                (new_send_at.isoformat(), event_id),
            )
            engine.db.commit()
            if new_send_at > now_dt:
                continue

        phone = extract_phone(ev.description)
        if phone is None:
            appt = engine.db.execute(
                "SELECT phone FROM appointments WHERE event_id = ?", (event_id,)
            ).fetchone()
            phone = appt["phone"] if appt else None
        if phone is None:
            engine.db.execute("DELETE FROM reminders WHERE event_id = ?", (event_id,))
            engine.db.commit()
            continue

        try:
            parsed = parse_title(ev.title)
            svc_name = parsed.service
            client_name = parsed.name
        except ValueError:
            svc_name = ev.title.strip() or "Turno"
            client = engine._get_client(phone)
            client_name = (client.name if client and client.name else None) or "clienta"

        buttons = (
            Button(id=encode("rem:confirm", evt=ev.id), title="Confirmar"),
            Button(id=encode("rem:resched", evt=ev.id), title="Reprogramar"),
            Button(id=encode("rem:cancel", evt=ev.id), title="Cancelar"),
        )
        await engine.messenger.send_template(
            phone,
            "recordatorio_turno",
            (client_name, svc_name, _format_dt(ev_start)),
            buttons,
        )
        engine.db.execute(
            "UPDATE reminders SET sent_at = ? WHERE event_id = ?",
            (now_dt.isoformat(), event_id),
        )
        engine.db.commit()
        sent_count += 1

    return sent_count


async def run_sync(engine: Engine, now: datetime | str | None = None) -> int:
    now_dt = _to_tz(now) if now is not None else engine.clock.now()
    horizon_days = max(
        engine.services_cfg.horizon_regular, engine.services_cfg.horizon_vip
    )
    end_dt = now_dt + timedelta(days=horizon_days + 1)
    events = await engine.calendar.list_events(
        engine.config.cal_turnos_id, now_dt, end_dt
    )

    seen_ids: set[str] = set()
    for ev in events:
        ev_start = _to_tz(ev.start)
        if ev_start <= now_dt:
            continue
        phone = extract_phone(ev.description)
        if phone is None:
            continue
        seen_ids.add(ev.id)

        engine.db.execute(
            "INSERT OR REPLACE INTO appointments (event_id, phone, start_at) VALUES (?, ?, ?)",
            (ev.id, phone, ev_start.isoformat()),
        )

        rem = engine.db.execute(
            "SELECT send_at, sent_at FROM reminders WHERE event_id = ?", (ev.id,)
        ).fetchone()
        if rem is None:
            send_at = compute_send_at(ev_start, now_dt)
            if send_at is not None:
                engine.db.execute(
                    "INSERT INTO reminders (event_id, send_at, sent_at) VALUES (?, ?, NULL)",
                    (ev.id, send_at.isoformat()),
                )
        elif rem["sent_at"] is None:
            expected_send_at = ev_start - timedelta(hours=REMINDER_LEAD_H)
            if _to_tz(rem["send_at"]) != expected_send_at:
                engine.db.execute(
                    "UPDATE reminders SET send_at = ? WHERE event_id = ?",
                    (expected_send_at.isoformat(), ev.id),
                )

    # Drop vanished future appointments and unsent reminders
    cached_rows = engine.db.execute(
        "SELECT event_id, start_at FROM appointments"
    ).fetchall()
    for row in cached_rows:
        ev_id = row["event_id"]
        start_at = _to_tz(row["start_at"])
        if start_at > now_dt and ev_id not in seen_ids:
            engine.db.execute("DELETE FROM appointments WHERE event_id = ?", (ev_id,))
            engine.db.execute(
                "DELETE FROM reminders WHERE event_id = ? AND sent_at IS NULL", (ev_id,)
            )

    rem_rows = engine.db.execute(
        "SELECT event_id FROM reminders WHERE sent_at IS NULL"
    ).fetchall()
    for row in rem_rows:
        ev_id = row["event_id"]
        if ev_id not in seen_ids:
            ev_check = await engine.calendar.get_event(engine.config.cal_turnos_id, ev_id)
            if (
                ev_check is None
                or _to_tz(ev_check.start) <= now_dt
                or extract_phone(ev_check.description) is None
            ):
                engine.db.execute("DELETE FROM reminders WHERE event_id = ?", (ev_id,))

    engine.db.commit()
    return len(seen_ids)
