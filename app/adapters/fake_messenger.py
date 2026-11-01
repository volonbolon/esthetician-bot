from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.conversation import Button, Incoming, ListRow, ListSection
from app.phones import normalize_phone
from app.ports import (
    validate_buttons,
    validate_list,
    validate_template,
    validate_text,
)

OutboundKind = Literal["text", "list", "buttons", "template"]


@dataclass(frozen=True)
class OutboundMessage:
    to: str
    kind: OutboundKind
    text: str = ""
    button_label: str = ""
    sections: tuple[ListSection, ...] = field(default_factory=tuple)
    buttons: tuple[Button, ...] = field(default_factory=tuple)
    template_name: str = ""
    template_params: tuple[str, ...] = field(default_factory=tuple)

    @property
    def rows(self) -> tuple[ListRow, ...]:
        return tuple(r for s in self.sections for r in s.rows)


class FakeMessenger:
    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []
        self.by_phone: dict[str, list[OutboundMessage]] = defaultdict(list)
        self._seq = 0

    def _record(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)
        self.by_phone[msg.to].append(msg)

    async def send_text(self, to: str, text: str) -> None:
        validate_text(to, text)
        norm = normalize_phone(to)
        self._record(OutboundMessage(to=norm, kind="text", text=text))

    async def send_list(
        self,
        to: str,
        body: str,
        button_label: str,
        sections: Sequence[ListSection],
    ) -> None:
        validate_list(to, body, button_label, sections)
        norm = normalize_phone(to)
        self._record(
            OutboundMessage(
                to=norm,
                kind="list",
                text=body,
                button_label=button_label,
                sections=tuple(sections),
            )
        )

    async def send_buttons(
        self,
        to: str,
        body: str,
        buttons: Sequence[Button],
    ) -> None:
        validate_buttons(to, body, buttons)
        norm = normalize_phone(to)
        self._record(
            OutboundMessage(
                to=norm,
                kind="buttons",
                text=body,
                buttons=tuple(buttons),
            )
        )

    async def send_template(
        self,
        to: str,
        name: str,
        params: Sequence[str] = (),
        buttons: Sequence[Button] = (),
    ) -> None:
        validate_template(to, name, params, buttons)
        norm = normalize_phone(to)
        self._record(
            OutboundMessage(
                to=norm,
                kind="template",
                template_name=name,
                template_params=tuple(params),
                buttons=tuple(buttons),
            )
        )

    def messages_for(self, phone: str) -> list[OutboundMessage]:
        return list(self.by_phone.get(normalize_phone(phone), ()))

    def last_message(self, phone: str | None = None) -> OutboundMessage | None:
        if phone is not None:
            msgs = self.by_phone.get(normalize_phone(phone), [])
            return msgs[-1] if msgs else None
        return self.sent[-1] if self.sent else None

    def tap(
        self,
        target: str | int,
        *,
        phone: str | None = None,
        wamid: str | None = None,
    ) -> Incoming:
        pool = self.by_phone.get(normalize_phone(phone), []) if phone else self.sent
        for msg in reversed(pool):
            if msg.kind == "list":
                rows = msg.rows
                row = self._match_item(rows, target)
                if row is not None:
                    self._seq += 1
                    return Incoming(
                        wamid=wamid or f"wamid_tap_{self._seq}",
                        from_phone=msg.to,
                        kind="list_reply",
                        text=row.title,
                        payload_id=row.id,
                    )
            elif msg.kind in ("buttons", "template") and msg.buttons:
                btn = self._match_item(msg.buttons, target)
                if btn is not None:
                    self._seq += 1
                    reply_kind = "template_button" if msg.kind == "template" else "button_reply"
                    return Incoming(
                        wamid=wamid or f"wamid_tap_{self._seq}",
                        from_phone=msg.to,
                        kind=reply_kind,
                        text=btn.title,
                        payload_id=btn.id,
                    )
        raise LookupError(f"No matching interactive option for {target!r}")

    @staticmethod
    def _match_item[T: (ListRow, Button)](items: Sequence[T], target: str | int) -> T | None:
        if isinstance(target, int):
            if 1 <= target <= len(items):
                return items[target - 1]
            return None
        for item in items:
            if item.title == target or item.title.lower() == target.lower():
                return item
        return None
