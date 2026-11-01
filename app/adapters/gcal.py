import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.clock import TZ, _to_tz
from app.ports import CalendarError, CalendarEvent

SCOPES = ("https://www.googleapis.com/auth/calendar",)


def _build_google_service(sa_json: str | Path) -> Any:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    raw = str(sa_json).strip()
    if raw.startswith("{"):
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(raw, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _parse_gcal_dt(dt_obj: dict[str, Any]) -> datetime:
    if "dateTime" in dt_obj:
        return _to_tz(dt_obj["dateTime"])
    if "date" in dt_obj:
        return _to_tz(f"{dt_obj['date']}T00:00:00")
    raise CalendarError(f"Missing start/end time in Google Calendar event: {dt_obj!r}")


def _to_calendar_event(item: dict[str, Any]) -> CalendarEvent:
    return CalendarEvent(
        id=str(item["id"]),
        start=_parse_gcal_dt(item.get("start") or {}),
        end=_parse_gcal_dt(item.get("end") or {}),
        title=str(item.get("summary") or ""),
        description=str(item.get("description") or ""),
    )


def _is_not_found(exc: Exception) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status == 404 or getattr(exc, "status_code", None) == 404


class GoogleCalendar:
    def __init__(
        self,
        sa_json: str | Path | None = None,
        *,
        service: Any | None = None,
    ) -> None:
        if service is None and not sa_json:
            raise ValueError("Either sa_json or service must be provided")
        self._sa_json = sa_json
        self._service = service

    def _get_service(self) -> Any:
        if self._service is None:
            self._service = _build_google_service(self._sa_json)  # type: ignore[arg-type]
        return self._service

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except CalendarError:
            raise
        except Exception as exc:
            raise CalendarError(str(exc)) from exc

    async def list_events(
        self,
        cal: str,
        start: datetime | str,
        end: datetime | str,
    ) -> list[CalendarEvent]:
        start_dt = _to_tz(start)
        end_dt = _to_tz(end)

        def _call() -> list[dict[str, Any]]:
            svc = self._get_service()
            items: list[dict[str, Any]] = []
            page_token: str | None = None
            while True:
                resp = (
                    svc.events()
                    .list(
                        calendarId=cal,
                        timeMin=start_dt.isoformat(),
                        timeMax=end_dt.isoformat(),
                        singleEvents=True,
                        orderBy="startTime",
                        pageToken=page_token,
                    )
                    .execute()
                )
                items.extend(
                    it for it in resp.get("items", []) if it.get("status") != "cancelled"
                )
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            return items

        raw_items = await self._run(_call)
        events = [_to_calendar_event(it) for it in raw_items]
        return sorted(events, key=lambda e: (e.start, e.id))

    async def get_event(self, cal: str, event_id: str) -> CalendarEvent | None:
        def _call() -> dict[str, Any] | None:
            svc = self._get_service()
            try:
                item = svc.events().get(calendarId=cal, eventId=event_id).execute()
            except Exception as exc:
                if _is_not_found(exc):
                    return None
                raise
            if item.get("status") == "cancelled":
                return None
            return item

        raw = await self._run(_call)
        return _to_calendar_event(raw) if raw is not None else None

    async def create_event(
        self,
        cal: str,
        start: datetime | str,
        end: datetime | str,
        title: str,
        description: str = "",
    ) -> CalendarEvent:
        start_dt = _to_tz(start)
        end_dt = _to_tz(end)
        body: dict[str, Any] = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": TZ.key},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": TZ.key},
        }

        def _call() -> dict[str, Any]:
            svc = self._get_service()
            return svc.events().insert(calendarId=cal, body=body).execute()

        raw = await self._run(_call)
        return _to_calendar_event(raw)

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
        body: dict[str, Any] = {}
        if start is not None:
            body["start"] = {"dateTime": _to_tz(start).isoformat(), "timeZone": TZ.key}
        if end is not None:
            body["end"] = {"dateTime": _to_tz(end).isoformat(), "timeZone": TZ.key}
        if title is not None:
            body["summary"] = title
        if description is not None:
            body["description"] = description

        def _call() -> dict[str, Any]:
            svc = self._get_service()
            return svc.events().patch(calendarId=cal, eventId=event_id, body=body).execute()

        raw = await self._run(_call)
        return _to_calendar_event(raw)

    async def delete_event(self, cal: str, event_id: str) -> None:
        def _call() -> None:
            svc = self._get_service()
            svc.events().delete(calendarId=cal, eventId=event_id).execute()

        await self._run(_call)


CalendarClient = GoogleCalendar
