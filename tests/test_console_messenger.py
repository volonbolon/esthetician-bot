import pytest

from app.adapters.console_messenger import ConsoleMessenger
from app.ports import Button, ListRow, ListSection

TO = "+5491122334455"


@pytest.mark.asyncio
async def test_console_messenger_prints_to_stdout(capsys: pytest.CaptureFixture[str]):
    m = ConsoleMessenger()

    await m.send_text(TO, "Hola Ana!")
    await m.send_list(
        TO,
        "Elegí un servicio:",
        "Servicios",
        (
            ListSection(
                title="Disponibles",
                rows=(
                    ListRow(id="v1:svc:svc=Facial", title="Facial", description="60 min"),
                    ListRow(id="v1:svc:svc=Peeling", title="Peeling", description="45 min"),
                ),
            ),
        ),
    )
    await m.send_buttons(
        TO,
        "¿Qué querés hacer?",
        (
            Button(id="v1:svc", title="Reservar"),
            Button(id="v1:mine", title="Mi turno"),
        ),
    )
    await m.send_template(
        TO,
        "recordatorio_turno",
        ("Ana", "Facial", "Mar 27/10", "10:00"),
        (
            Button(id="v1:rem:confirm:evt=e1", title="Confirmar"),
            Button(id="v1:rem:cancel:evt=e1", title="Cancelar"),
        ),
    )

    out = capsys.readouterr().out
    assert "Hola Ana!" in out
    assert "1. Facial — 60 min" in out
    assert "2. Peeling — 45 min" in out
    assert "1. [Reservar]" in out
    assert "2. [Mi turno]" in out
    assert f"[TEMPLATE recordatorio_turno] ({TO}) Ana | Facial | Mar 27/10 | 10:00" in out
    assert "1. [Confirmar]" in out
