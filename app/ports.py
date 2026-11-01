from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.clock import Clock
from app.conversation import Button, CalendarEvent, ListRow, ListSection

MAX_TEXT_LEN = 4096
MAX_BODY_LEN = 1024
MAX_LIST_ROWS = 10
MAX_BUTTONS = 3
MAX_ROW_TITLE_LEN = 24
MAX_ROW_DESC_LEN = 72
MAX_SECTION_TITLE_LEN = 24
MAX_BUTTON_LABEL_LEN = 20
MAX_PAYLOAD_ID_LEN = 200


class CalendarError(Exception):
    """Raised when a calendar operation fails."""


def _require_non_empty(value: str, field_name: str, max_len: int | None = None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if max_len is not None and len(value) > max_len:
        raise ValueError(f"{field_name} exceeds {max_len} chars ({len(value)}): {value!r}")


def validate_text(to: str, text: str) -> None:
    _require_non_empty(to, "to")
    _require_non_empty(text, "text", MAX_TEXT_LEN)


def validate_list(
    to: str,
    body: str,
    button_label: str,
    sections: Sequence[ListSection],
) -> None:
    _require_non_empty(to, "to")
    _require_non_empty(body, "body", MAX_BODY_LEN)
    _require_non_empty(button_label, "button_label", MAX_BUTTON_LABEL_LEN)
    if not sections:
        raise ValueError("sections must be non-empty")

    total_rows = 0
    seen_ids: set[str] = set()
    for sec in sections:
        if sec.title and len(sec.title) > MAX_SECTION_TITLE_LEN:
            raise ValueError(
                f"section title exceeds {MAX_SECTION_TITLE_LEN} chars ({len(sec.title)}): {sec.title!r}"
            )
        if not sec.rows:
            raise ValueError("each section must contain at least one row")
        for row in sec.rows:
            total_rows += 1
            _require_non_empty(row.id, "row.id", MAX_PAYLOAD_ID_LEN)
            _require_non_empty(row.title, "row.title", MAX_ROW_TITLE_LEN)
            if row.description and len(row.description) > MAX_ROW_DESC_LEN:
                raise ValueError(
                    f"row.description exceeds {MAX_ROW_DESC_LEN} chars ({len(row.description)})"
                )
            if row.id in seen_ids:
                raise ValueError(f"Duplicate row.id in list: {row.id!r}")
            seen_ids.add(row.id)

    if total_rows > MAX_LIST_ROWS:
        raise ValueError(f"list exceeds {MAX_LIST_ROWS} rows ({total_rows})")


def validate_buttons(
    to: str,
    body: str,
    buttons: Sequence[Button],
) -> None:
    _require_non_empty(to, "to")
    _require_non_empty(body, "body", MAX_BODY_LEN)
    if not buttons:
        raise ValueError("buttons must be non-empty")
    if len(buttons) > MAX_BUTTONS:
        raise ValueError(f"buttons exceeds {MAX_BUTTONS} items ({len(buttons)})")

    seen_ids: set[str] = set()
    for btn in buttons:
        _require_non_empty(btn.id, "button.id", MAX_PAYLOAD_ID_LEN)
        _require_non_empty(btn.title, "button.title", MAX_BUTTON_LABEL_LEN)
        if btn.id in seen_ids:
            raise ValueError(f"Duplicate button.id: {btn.id!r}")
        seen_ids.add(btn.id)


def validate_template(
    to: str,
    name: str,
    params: Sequence[str] = (),
    buttons: Sequence[Button] = (),
) -> None:
    _require_non_empty(to, "to")
    _require_non_empty(name, "template name")
    for idx, p in enumerate(params):
        _require_non_empty(p, f"template param[{idx}]")
    if len(buttons) > MAX_BUTTONS:
        raise ValueError(f"template buttons exceeds {MAX_BUTTONS} items ({len(buttons)})")
    for btn in buttons:
        _require_non_empty(btn.id, "template button.id", MAX_PAYLOAD_ID_LEN)
        if btn.title:
            _require_non_empty(btn.title, "template button.title", MAX_BUTTON_LABEL_LEN)


@runtime_checkable
class Messenger(Protocol):
    async def send_text(self, to: str, text: str) -> None: ...

    async def send_list(
        self,
        to: str,
        body: str,
        button_label: str,
        sections: Sequence[ListSection],
    ) -> None: ...

    async def send_buttons(
        self,
        to: str,
        body: str,
        buttons: Sequence[Button],
    ) -> None: ...

    async def send_template(
        self,
        to: str,
        name: str,
        params: Sequence[str] = (),
        buttons: Sequence[Button] = (),
    ) -> None: ...


@runtime_checkable
class CalendarPort(Protocol):
    async def list_events(
        self,
        cal: str,
        start: datetime,
        end: datetime,
    ) -> list[CalendarEvent]: ...

    async def get_event(self, cal: str, event_id: str) -> CalendarEvent | None: ...

    async def create_event(
        self,
        cal: str,
        start: datetime,
        end: datetime,
        title: str,
        description: str = "",
    ) -> CalendarEvent: ...

    async def patch_event(
        self,
        cal: str,
        event_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> CalendarEvent: ...

    async def delete_event(self, cal: str, event_id: str) -> None: ...


__all__ = [
    "MAX_BODY_LEN",
    "MAX_BUTTONS",
    "MAX_BUTTON_LABEL_LEN",
    "MAX_LIST_ROWS",
    "MAX_PAYLOAD_ID_LEN",
    "MAX_ROW_DESC_LEN",
    "MAX_ROW_TITLE_LEN",
    "MAX_SECTION_TITLE_LEN",
    "MAX_TEXT_LEN",
    "Button",
    "CalendarError",
    "CalendarEvent",
    "CalendarPort",
    "Clock",
    "ListRow",
    "ListSection",
    "Messenger",
    "validate_buttons",
    "validate_list",
    "validate_template",
    "validate_text",
]
