import io
from pathlib import Path

import pytest

from app.adapters.console_messenger import ConsoleMessenger
from app.adapters.fake_calendar import FakeCalendar
from app.clock import FakeClock
from app.conversation import Incoming
from app.payloads import decode
from app.sim import Simulator, _async_main


@pytest.mark.asyncio
async def test_simulator_wires_console_messenger_fake_calendar_fake_clock_and_sqlite_file(
    tmp_path: Path,
):
    db_file = tmp_path / "sim_test.db"
    buf = io.StringIO()
    sim = Simulator(phone="+5491100000001", db_path=db_file, stream=buf)

    assert isinstance(sim.messenger, ConsoleMessenger)
    assert isinstance(sim.calendar, FakeCalendar)
    assert isinstance(sim.clock, FakeClock)
    assert db_file.exists()
    assert sim.active_phone == "+5491100000001"

    await sim.handle_line("Hola")
    await sim.handle_line("Lucía")

    out = buf.getvalue()
    assert "¿Cómo es tu nombre?" in out
    assert "Reservar" in out
    row = sim.db.execute(
        "SELECT name FROM clients WHERE phone = ?", ("+5491100000001",)
    ).fetchone()
    assert row["name"] == "Lucía"


@pytest.mark.asyncio
async def test_typing_number_taps_list_or_button_with_real_payload_and_other_text_is_text_message(
    tmp_path: Path,
):
    buf = io.StringIO()
    sim = Simulator(phone="+5491100000001", db_path=tmp_path / "tap.db", stream=buf)
    recorded: list[Incoming] = []
    orig_handle = sim.engine.handle_incoming

    async def spy_handle(inc: Incoming) -> bool:
        recorded.append(inc)
        return await orig_handle(inc)

    sim.engine.handle_incoming = spy_handle  # type: ignore[method-assign]

    # Free text during onboarding (even if numeric when last message is text)
    await sim.handle_line("hola")
    assert recorded[-1].kind == "text"
    await sim.handle_line("Ana")
    assert recorded[-1].kind == "text"

    # Last message is now 3 buttons (Reservar, Mi turno, Hablar con...)
    # Typing "1" taps button 1 -> button_reply with real payload "v1:svc"
    await sim.handle_line("1")
    assert recorded[-1].kind == "button_reply"
    assert recorded[-1].text == "Reservar"
    assert decode(recorded[-1].payload_id).kind == "svc"

    # Last message is now service list -> typing "1" taps row 1 -> list_reply with real payload
    await sim.handle_line("1")
    assert recorded[-1].kind == "list_reply"
    assert recorded[-1].text == "Limpieza facial"
    p = decode(recorded[-1].payload_id)
    assert p.kind == "svc"
    assert p.get("svc") == "Limpieza facial"

    # Out-of-range number ("99") or arbitrary text ("otra cosa") is sent as a text message
    await sim.handle_line("99")
    assert recorded[-1].kind == "text"
    assert recorded[-1].text == "99"
    await sim.handle_line("otra cosa")
    assert recorded[-1].kind == "text"
    assert recorded[-1].text == "otra cosa"


@pytest.mark.asyncio
async def test_simulator_numeric_taps_and_slash_commands(tmp_path: Path):
    db_file = tmp_path / "sim_cmds.db"
    buf = io.StringIO()
    sim = Simulator(phone="+5491100000001", db_path=db_file, stream=buf)

    # Onboard and book via numeric taps
    await sim.handle_line("hola")
    await sim.handle_line("Ana")
    await sim.handle_line("1")  # Reservar
    await sim.handle_line("1")  # Limpieza facial
    await sim.handle_line("1")  # First available day (2026-10-27)
    await sim.handle_line("1")  # First slot (09:00)
    await sim.handle_line("1")  # Confirmar

    assert "Reservamos tu turno" in buf.getvalue()

    # /db and /cal dump
    await sim.handle_line("/db")
    await sim.handle_line("/cal")
    assert "[db:clients] (1)" in buf.getvalue()
    assert "[cal:turnos] (1)" in buf.getvalue()

    # /time and /tick
    await sim.handle_line("/time +2h")
    await sim.handle_line("/time")
    assert "2026-10-26T11:00" in buf.getvalue()
    await sim.handle_line("/tick")
    assert "[sim] tick @" in buf.getvalue()

    # /phone, /admin, /echo
    await sim.handle_line("/phone +5491122334455")
    assert sim.active_phone == "+5491122334455"
    await sim.handle_line("/admin")
    assert sim.active_phone == sim.admin_phone
    await sim.handle_line("vip +5491100000001")
    assert "VIP activado" in buf.getvalue()
    await sim.handle_line("/echo +5491100000001 Hola Ana!")
    assert "[sim] echo -> +5491100000001: Hola Ana!" in buf.getvalue()

    # /calfail and /reset
    await sim.handle_line("/calfail")
    assert sim.calendar._fail_count == 1
    await sim.handle_line("/reset")
    assert sim.calendar._fail_count == 0
    assert sim.db.execute("SELECT COUNT(*) AS n FROM clients").fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_simulator_cli_main_with_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    db_file = tmp_path / "cli_sim.db"
    monkeypatch.setattr("sys.stdin", io.StringIO("hola\nCamila\n/db\n"))
    rc = await _async_main(["--phone", "+5491100000001", "--db", str(db_file)])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "Camila" in captured
    assert db_file.exists()
