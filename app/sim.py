import argparse
import asyncio
import difflib
import io
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TextIO

from app.adapters.console_messenger import ConsoleMessenger
from app.adapters.fake_calendar import DEFAULT_SEED_PATH, FakeCalendar
from app.clock import FakeClock, _to_tz
from app.config import Config, load_config
from app.conversation import Incoming
from app.db import init_db
from app.engine import Engine, build_app
from app.jobs import run_reminders, run_sync
from app.phones import normalize_phone
from app.ports import CalendarError

DEFAULT_SIM_PHONE = "+5491100000001"
DEFAULT_ADMIN_PHONE = "+5491100000099"
DEFAULT_SIM_DB = "sim.db"
DEFAULT_SIM_TIME = "2026-12-18T09:00:00"

_OFFSET_RE = re.compile(r"^([+-]?\d+)\s*([dhm])$", re.IGNORECASE)


class Simulator:
    def __init__(
        self,
        *,
        phone: str = DEFAULT_SIM_PHONE,
        db_path: str | Path = DEFAULT_SIM_DB,
        now: datetime | str = DEFAULT_SIM_TIME,
        seed_path: str | Path | None = DEFAULT_SEED_PATH,
        stream: TextIO | None = None,
        config: Config | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self.seed_path = Path(seed_path) if seed_path is not None else None
        self._stream = stream
        self._initial_now = _to_tz(now)
        self._wamid_seq = 0

        base_cfg = config if config is not None else load_config()
        admin_phones = base_cfg.admin_phones or (DEFAULT_ADMIN_PHONE,)
        self.config = Config(
            messenger="console",
            calendar_backend="fake",
            db_path=self.db_path,
            esthetician_name=base_cfg.esthetician_name,
            admin_phones=tuple(normalize_phone(p) for p in admin_phones),
            cal_available_id=base_cfg.cal_available_id,
            cal_turnos_id=base_cfg.cal_turnos_id,
        )
        self.admin_phone = self.config.admin_phones[0]
        self.active_phone = normalize_phone(phone)

        self.clock = FakeClock(self._initial_now)
        self.messenger = ConsoleMessenger(stream=self._stream)
        self.calendar = FakeCalendar(seed_path=self.seed_path)
        self.db = init_db(self.db_path)
        self.engine: Engine = build_app(
            self.config,
            db=self.db,
            messenger=self.messenger,
            calendar=self.calendar,
            clock=self.clock,
        )

    def _print(self, text: str) -> None:
        out = self._stream if self._stream is not None else sys.stdout
        print(text, file=out)

    def _next_wamid(self) -> str:
        self._wamid_seq += 1
        return f"sim_wamid_{self._wamid_seq}"

    def _can_tap_number(self, num: int) -> bool:
        if num < 1:
            return False
        last = self.messenger.last_message(self.active_phone)
        if last is None:
            return False
        if last.kind == "list":
            return num <= len(last.rows)
        if last.kind in ("buttons", "template"):
            return num <= len(last.buttons)
        return False

    async def handle_line(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            return

        if line.startswith("/"):
            await self._handle_slash(line)
            return

        if line.isdigit():
            idx = int(line)
            if self._can_tap_number(idx):
                tapped = self.messenger.tap(
                    idx, phone=self.active_phone, wamid=self._next_wamid()
                )
                inc = Incoming(
                    wamid=tapped.wamid,
                    from_phone=tapped.from_phone,
                    kind=tapped.kind,
                    text=tapped.text,
                    payload_id=tapped.payload_id,
                    timestamp=self.clock.now(),
                )
                await self.engine.handle_incoming(inc)
                return

        inc = Incoming(
            wamid=self._next_wamid(),
            from_phone=self.active_phone,
            kind="text",
            text=line,
            timestamp=self.clock.now(),
        )
        await self.engine.handle_incoming(inc)

    async def _handle_slash(self, line: str) -> None:
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/phone":
            self.active_phone = normalize_phone(rest)
            self._print(f"[sim] phone -> {self.active_phone}")
            return

        if cmd == "/admin":
            self.active_phone = self.admin_phone
            self._print(f"[sim] phone -> {self.active_phone} (admin)")
            return

        if cmd == "/echo":
            echo_parts = rest.split(maxsplit=1)
            if not echo_parts:
                self._print("[sim] usage: /echo <phone> <text>")
                return
            target = normalize_phone(echo_parts[0])
            text = echo_parts[1] if len(echo_parts) > 1 else ""
            inc = Incoming(
                wamid=self._next_wamid(),
                from_phone=self.admin_phone,
                to_phone=target,
                kind="echo",
                text=text,
                timestamp=self.clock.now(),
            )
            await self.engine.handle_incoming(inc)
            self._print(f"[sim] echo -> {target}: {text}")
            return

        if cmd == "/time":
            if not rest:
                self._print(f"[sim] time: {self.clock.now().isoformat(timespec='minutes')}")
                return
            m = _OFFSET_RE.match(rest)
            if m:
                val = int(m.group(1))
                unit = m.group(2).lower()
                if unit == "d":
                    self.clock.advance(days=val)
                elif unit == "h":
                    self.clock.advance(hours=val)
                else:
                    self.clock.advance(minutes=val)
            else:
                self.clock.set(rest)
            self._print(f"[sim] time -> {self.clock.now().isoformat(timespec='minutes')}")
            return

        if cmd == "/tick":
            now = self.clock.now()
            try:
                await run_sync(self.engine, now)
                sent = await run_reminders(self.engine, now)
                self._print(
                    f"[sim] tick @ {now.isoformat(timespec='minutes')} (reminders={sent})"
                )
            except CalendarError as exc:
                await self.engine._send_admin_alert(
                    f"Error de Google Calendar en tareas programadas: {exc}"
                )
                self._print(f"[sim] tick calendar error: {exc}")
            return

        if cmd == "/cal":
            await self._handle_cal(rest)
            return

        if cmd == "/calfail":
            self.calendar.fail_next()
            self._print("[sim] next calendar call will fail")
            return

        if cmd == "/db":
            self._dump_db()
            return

        if cmd == "/reset":
            self._reset()
            return

        self._print(f"[sim] unknown command: {cmd}")

    async def _handle_cal(self, rest: str) -> None:
        if not rest:
            for cal_label, cal_id in (
                ("available", self.config.cal_available_id),
                ("turnos", self.config.cal_turnos_id),
            ):
                evs = sorted(
                    self.calendar._calendars.get(cal_id, {}).values(),
                    key=lambda e: (e.start, e.id),
                )
                self._print(f"[cal:{cal_label}] ({len(evs)})")
                for ev in evs:
                    desc = f" | {ev.description}" if ev.description else ""
                    self._print(
                        f"  {ev.id}: {ev.start.isoformat(timespec='minutes')} -> "
                        f"{ev.end.isoformat(timespec='minutes')} | {ev.title}{desc}"
                    )
            return

        tokens = rest.split()
        sub = tokens[0].lower()

        if sub == "add":
            # /cal add <cal> <start> <end> <title> [| <description>]
            cal_key = tokens[1]
            cal_id = self._resolve_cal_id(cal_key)
            start_s = tokens[2]
            end_s = tokens[3]
            tail = rest.split(None, 4)[4] if len(tokens) > 4 else ""
            if "|" in tail:
                title, description = [p.strip() for p in tail.split("|", 1)]
            else:
                title, description = tail.strip(), ""
            ev = await self.calendar.create_event(cal_id, start_s, end_s, title, description)
            self._print(
                f"[cal] added {ev.id} in {cal_id}: "
                f"{ev.start.isoformat(timespec='minutes')} -> {ev.end.isoformat(timespec='minutes')} "
                f"{ev.title}"
            )
            return

        if sub == "move":
            # Supports:
            # /cal move <id> <new_start> [new_end] [| <description>]
            # /cal move <cal> <id> <new_start> [new_end] [| <description>]
            head, sep, desc_part = rest.partition("|")
            new_desc = desc_part.strip() if sep else None
            move_tokens = head.split()
            if move_tokens[1].lower() in ("available", "avail", "turnos", "turno"):
                cal_id = self._resolve_cal_id(move_tokens[1])
                ev_id = move_tokens[2]
                new_start = move_tokens[3]
                new_end = move_tokens[4] if len(move_tokens) > 4 else None
            else:
                ev_id = move_tokens[1]
                cal_id = self._find_event_cal(ev_id)
                new_start = move_tokens[2]
                new_end = move_tokens[3] if len(move_tokens) > 3 else None

            if new_end is None:
                existing = await self.calendar.get_event(cal_id, ev_id)
                if existing is None:
                    raise ValueError(f"Event {ev_id!r} not found in {cal_id!r}")
                duration = existing.end - existing.start
                new_end_dt = _to_tz(new_start) + duration
                ev = await self.calendar.patch_event(
                    cal_id, ev_id, start=new_start, end=new_end_dt, description=new_desc
                )
            else:
                ev = await self.calendar.patch_event(
                    cal_id, ev_id, start=new_start, end=new_end, description=new_desc
                )
            self._print(
                f"[cal] moved {ev.id}: "
                f"{ev.start.isoformat(timespec='minutes')} -> {ev.end.isoformat(timespec='minutes')}"
            )
            return

        if sub == "delete":
            # /cal delete <event_id> OR /cal delete <cal> <event_id>
            if len(tokens) == 2:
                ev_id = tokens[1]
                cal_id = self._find_event_cal(ev_id)
            else:
                cal_id = self._resolve_cal_id(tokens[1])
                ev_id = tokens[2]
            await self.calendar.delete_event(cal_id, ev_id)
            self._print(f"[cal] deleted {ev_id}")
            return

        self._print(f"[sim] unknown /cal subcommand: {sub}")

    def _resolve_cal_id(self, name: str) -> str:
        low = name.lower()
        if low in ("available", "avail"):
            return self.config.cal_available_id
        if low in ("turnos", "turno"):
            return self.config.cal_turnos_id
        return name

    def _find_event_cal(self, event_id: str) -> str:
        for cal_id, events in self.calendar._calendars.items():
            if event_id in events:
                return cal_id
        return self.config.cal_turnos_id

    def _dump_db(self) -> None:
        clients = self.db.execute(
            "SELECT phone, name, vip, muted_until, awaiting_name FROM clients ORDER BY phone"
        ).fetchall()
        self._print(f"[db:clients] ({len(clients)})")
        for c in clients:
            self._print(
                f"  {c['phone']}: name={c['name']!r} vip={bool(c['vip'])} "
                f"muted_until={c['muted_until']} awaiting_name={bool(c['awaiting_name'])}"
            )

        rems = self.db.execute(
            "SELECT event_id, send_at, sent_at FROM reminders ORDER BY send_at, event_id"
        ).fetchall()
        self._print(f"[db:reminders] ({len(rems)})")
        for r in rems:
            self._print(f"  {r['event_id']}: send_at={r['send_at']} sent_at={r['sent_at']}")

        mutes = [c for c in clients if c["muted_until"]]
        self._print(f"[db:mutes] ({len(mutes)})")
        for m in mutes:
            self._print(f"  {m['phone']}: muted_until={m['muted_until']}")

    def _reset(self) -> None:
        for table in (
            "clients",
            "appointments",
            "reminders",
            "processed_messages",
            "vip_alerts",
        ):
            self.db.execute(f"DELETE FROM {table}")
        self.db.commit()
        self.calendar._calendars.clear()
        self.calendar._seq = 0
        self.calendar._fail_count = 0
        if self.seed_path is not None and self.seed_path.exists():
            self.calendar.load_seed(self.seed_path)
        self.messenger.sent.clear()
        self.messenger.by_phone.clear()
        self.clock.set(self._initial_now)
        self._print("[sim] reset complete")


async def run_script(
    script_path: str | Path,
    *,
    phone: str = DEFAULT_SIM_PHONE,
    now: datetime | str = DEFAULT_SIM_TIME,
    seed_path: str | Path | None = DEFAULT_SEED_PATH,
    stream: TextIO | None = None,
    config: Config | None = None,
) -> str:
    buf = io.StringIO()
    cfg = (
        config
        if config is not None
        else Config(
            messenger="console",
            calendar_backend="fake",
            db_path=":memory:",
            esthetician_name="Lorena",
            admin_phones=(DEFAULT_ADMIN_PHONE,),
        )
    )
    sim = Simulator(
        phone=phone,
        db_path=":memory:",
        now=now,
        seed_path=seed_path,
        stream=buf,
        config=cfg,
    )
    lines = Path(script_path).read_text(encoding="utf-8").splitlines()
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        buf.write(f"> {stripped}\n")
        await sim.handle_line(stripped)

    output = buf.getvalue()
    if stream is not None:
        stream.write(output)
    return output


def _expected_path_for(script_path: Path) -> Path | None:
    candidates = (
        script_path.with_suffix(".expected"),
        script_path.with_suffix(".out"),
        script_path.parent / f"{script_path.name}.expected",
    )
    for cand in candidates:
        if cand.exists():
            return cand
    return None


async def _async_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Esthetician bot terminal simulator")
    parser.add_argument("--phone", default=DEFAULT_SIM_PHONE, help="Initial client phone")
    parser.add_argument("--db", default=DEFAULT_SIM_DB, help="SQLite DB file path")
    parser.add_argument("--time", default=DEFAULT_SIM_TIME, help="Initial fake clock ISO time")
    parser.add_argument("--script", help="Replay a scenario script file")
    args = parser.parse_args(argv)

    if args.script:
        script_file = Path(args.script)
        actual = await run_script(
            script_file,
            phone=args.phone,
            now=args.time,
            stream=sys.stdout,
        )
        exp_file = _expected_path_for(script_file)
        if exp_file is not None:
            expected = exp_file.read_text(encoding="utf-8")
            if actual != expected:
                diff = "".join(
                    difflib.unified_diff(
                        expected.splitlines(keepends=True),
                        actual.splitlines(keepends=True),
                        fromfile=str(exp_file),
                        tofile=str(script_file),
                    )
                )
                sys.stderr.write(diff)
                return 1
        return 0

    sim = Simulator(phone=args.phone, db_path=args.db, now=args.time)
    while True:
        try:
            if sys.stdin.isatty():
                line = input(f"{sim.active_phone}> ")
            else:
                raw = sys.stdin.readline()
                if not raw:
                    break
                line = raw.rstrip("\r\n")
        except (EOFError, KeyboardInterrupt):
            break
        await sim.handle_line(line)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
