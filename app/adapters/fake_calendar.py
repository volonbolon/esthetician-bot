import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.clock import _to_tz
from app.ports import CalendarError, CalendarEvent

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SEED_PATH = _REPO_ROOT / "dev" / "seed_calendar.yaml"


class FakeCalendar:
    def __init__(
        self,
        *,
        json_path: str | Path | None = None,
        seed_path: str | Path | None = None,
    ) -> None:
        self._calendars: dict[str, dict[str, CalendarEvent]] = defaultdict(dict)
        self._seq = 0
        self._fail_count = 0
        self._json_path = Path(json_path) if json_path is not None else None
        self._seed_path = Path(seed_path) if seed_path is not None else None

        if self._json_path is not None and self._json_path.exists():
            self._load_json(self._json_path)
        elif self._seed_path is not None:
            self.load_seed(self._seed_path)

    def fail_next(self, count: int = 1) -> None:
        self._fail_count = count

    def _check_fail(self) -> None:
        if self._fail_count > 0:
            self._fail_count -= 1
            raise CalendarError("Simulated calendar failure")

    def _next_id(self) -> str:
        while True:
            self._seq += 1
            candidate = f"evt_{self._seq}"
            if all(candidate not in cal for cal in self._calendars.values()):
                return candidate

    def load_seed(self, seed_path: str | Path = DEFAULT_SEED_PATH) -> None:
        p = Path(seed_path)
        if not p.is_absolute() and not p.exists():
            p = _REPO_ROOT / p
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for cal_name, items in raw.items():
            if not isinstance(items, list):
                continue
            for item in items:
                ev_id = str(item.get("id") or self._next_id())
                ev = CalendarEvent(
                    id=ev_id,
                    start=item["start"],
                    end=item["end"],
                    title=str(item.get("title") or ""),
                    description=str(item.get("description") or ""),
                )
                self._calendars[str(cal_name)][ev_id] = ev
        self._save_json()

    def _load_json(self, path: Path) -> None:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        self._seq = int(raw.get("_seq", 0))
        cals = raw.get("calendars", {})
        self._calendars.clear()
        for cal_name, events in cals.items():
            for item in events:
                ev = CalendarEvent(
                    id=item["id"],
                    start=item["start"],
                    end=item["end"],
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                )
                self._calendars[cal_name][ev.id] = ev

    def _save_json(self) -> None:
        if self._json_path is None:
            return
        payload = {
            "_seq": self._seq,
            "calendars": {
                cal_name: [
                    {
                        "id": ev.id,
                        "start": ev.start.isoformat(),
                        "end": ev.end.isoformat(),
                        "title": ev.title,
                        "description": ev.description,
                    }
                    for ev in sorted(events.values(), key=lambda e: (e.start, e.id))
                ]
                for cal_name, events in self._calendars.items()
            },
        }
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        self._json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def reset(self, *, reload_seed: bool = True) -> None:
        self._calendars.clear()
        self._seq = 0
        self._fail_count = 0
        if reload_seed and self._seed_path is not None:
            self.load_seed(self._seed_path)
        else:
            self._save_json()

    async def list_events(
        self,
        cal: str,
        start: datetime | str,
        end: datetime | str,
    ) -> list[CalendarEvent]:
        self._check_fail()
        start_dt = _to_tz(start)
        end_dt = _to_tz(end)
        events = [
            ev
            for ev in self._calendars.get(cal, {}).values()
            if ev.end > start_dt and ev.start < end_dt
        ]
        return sorted(events, key=lambda e: (e.start, e.id))

    async def get_event(self, cal: str, event_id: str) -> CalendarEvent | None:
        self._check_fail()
        return self._calendars.get(cal, {}).get(event_id)

    async def create_event(
        self,
        cal: str,
        start: datetime | str,
        end: datetime | str,
        title: str,
        description: str = "",
        *,
        event_id: str | None = None,
    ) -> CalendarEvent:
        self._check_fail()
        ev_id = event_id or self._next_id()
        ev = CalendarEvent(
            id=ev_id,
            start=start,
            end=end,
            title=title,
            description=description,
        )
        self._calendars[cal][ev_id] = ev
        self._save_json()
        return ev

    async def patch_event(
        self,
        cal: str,
        event_id: str,
        *,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> CalendarEvent:
        self._check_fail()
        existing = self._calendars.get(cal, {}).get(event_id)
        if existing is None:
            raise CalendarError(f"Event not found in {cal!r}: {event_id!r}")
        updated = CalendarEvent(
            id=existing.id,
            start=start if start is not None else existing.start,
            end=end if end is not None else existing.end,
            title=title if title is not None else existing.title,
            description=description if description is not None else existing.description,
        )
        self._calendars[cal][event_id] = updated
        self._save_json()
        return updated

    async def delete_event(self, cal: str, event_id: str) -> None:
        self._check_fail()
        cal_dict = self._calendars.get(cal, {})
        if event_id not in cal_dict:
            raise CalendarError(f"Event not found in {cal!r}: {event_id!r}")
        del cal_dict[event_id]
        self._save_json()
