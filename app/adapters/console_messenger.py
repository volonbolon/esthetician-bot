import sys
from collections.abc import Sequence
from typing import TextIO

from app.adapters.fake_messenger import FakeMessenger
from app.conversation import Button, ListSection


class ConsoleMessenger(FakeMessenger):
    def __init__(self, stream: TextIO | None = None) -> None:
        super().__init__()
        self._stream = stream

    def _print(self, line: str) -> None:
        out = self._stream if self._stream is not None else sys.stdout
        print(line, file=out)

    async def send_text(self, to: str, text: str) -> None:
        await super().send_text(to, text)
        self._print(f"[BOT -> {to}] {text}")

    async def send_list(
        self,
        to: str,
        body: str,
        button_label: str,
        sections: Sequence[ListSection],
    ) -> None:
        await super().send_list(to, body, button_label, sections)
        self._print(f"[BOT -> {to}] {body} ({button_label})")
        idx = 1
        for sec in sections:
            if sec.title:
                self._print(f"  -- {sec.title} --")
            for row in sec.rows:
                desc = f" — {row.description}" if row.description else ""
                self._print(f"  {idx}. {row.title}{desc}")
                idx += 1

    async def send_buttons(
        self,
        to: str,
        body: str,
        buttons: Sequence[Button],
    ) -> None:
        await super().send_buttons(to, body, buttons)
        self._print(f"[BOT -> {to}] {body}")
        for idx, btn in enumerate(buttons, start=1):
            self._print(f"  [{idx}] {btn.title}")

    async def send_template(
        self,
        to: str,
        name: str,
        params: Sequence[str] = (),
        buttons: Sequence[Button] = (),
    ) -> None:
        await super().send_template(to, name, params, buttons)
        joined = " | ".join(params)
        self._print(f"[TEMPLATE {name} -> {to}] {joined}")
        for idx, btn in enumerate(buttons, start=1):
            self._print(f"  [{idx}] {btn.title}")
