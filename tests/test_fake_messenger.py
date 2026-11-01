import pytest

from app.adapters.fake_messenger import FakeMessenger
from app.ports import Button, ListRow, ListSection

PHONE_A = "+5491122334455"
PHONE_B = "+5491166778899"


@pytest.mark.asyncio
async def test_records_outbound_messages_per_phone_and_last_message():
    m = FakeMessenger()
    assert m.last_message() is None
    assert m.last_message(PHONE_A) is None

    await m.send_text(PHONE_A, "Hola A")
    await m.send_text(PHONE_B, "Hola B")
    await m.send_text("011 15 2233-4455", "Segundo A")

    assert len(m.messages_for(PHONE_A)) == 2
    assert len(m.messages_for(PHONE_B)) == 1
    assert m.last_message(PHONE_A).text == "Segundo A"
    assert m.last_message(PHONE_B).text == "Hola B"
    assert m.last_message().text == "Segundo A"


@pytest.mark.asyncio
async def test_tap_row_title_button_and_template_button():
    m = FakeMessenger()

    await m.send_list(
        PHONE_A,
        "Elegí un servicio:",
        "Servicios",
        (
            ListSection(
                title="Servicios",
                rows=(
                    ListRow(id="v1:svc:svc=Limpieza facial", title="Limpieza facial"),
                    ListRow(id="v1:svc:svc=Peeling", title="Peeling"),
                ),
            ),
        ),
    )
    list_tap = m.tap("Peeling")
    assert list_tap.from_phone == PHONE_A
    assert list_tap.kind == "list_reply"
    assert list_tap.text == "Peeling"
    assert list_tap.payload_id == "v1:svc:svc=Peeling"

    await m.send_buttons(
        PHONE_A,
        "¿Confirmás?",
        (
            Button(id="v1:confirm:svc=Peeling;t=2026-10-27T10:00", title="Confirmar"),
            Button(id="v1:menu", title="Cancelar"),
        ),
    )
    btn_tap = m.tap("Confirmar", PHONE_A)
    assert btn_tap.kind == "button_reply"
    assert btn_tap.payload_id == "v1:confirm:svc=Peeling;t=2026-10-27T10:00"

    await m.send_template(
        PHONE_B,
        "recordatorio_turno",
        ("Ana", "Peeling", "Mar 27/10", "10:00"),
        (Button(id="v1:rem:confirm:evt=e1", title="Confirmar"),),
    )
    tpl_tap = m.tap("Confirmar", phone=PHONE_B)
    assert tpl_tap.from_phone == PHONE_B
    assert tpl_tap.kind == "template_button"
    assert tpl_tap.payload_id == "v1:rem:confirm:evt=e1"

    with pytest.raises(LookupError):
        m.tap("No existe", phone=PHONE_A)
