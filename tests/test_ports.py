import io

import pytest

from app.adapters.console_messenger import ConsoleMessenger
from app.adapters.fake_messenger import FakeMessenger
from app.ports import (
    Button,
    ListRow,
    ListSection,
    Messenger,
    validate_buttons,
    validate_list,
    validate_template,
    validate_text,
)

TO = "+5491122334455"


def test_validate_text():
    validate_text(TO, "Hola")
    with pytest.raises(ValueError):
        validate_text(TO, "   ")
    with pytest.raises(ValueError):
        validate_text("", "Hola")


def test_validate_list_limits():
    rows_10 = tuple(ListRow(id=f"r{i}", title=f"Item {i}") for i in range(10))
    validate_list(TO, "Elegí:", "Ver opciones", (ListSection(title="Sec", rows=rows_10),))

    # >10 rows rejected
    rows_11 = tuple(ListRow(id=f"r{i}", title=f"Item {i}") for i in range(11))
    with pytest.raises(ValueError):
        validate_list(TO, "Elegí:", "Ver opciones", (ListSection(title="Sec", rows=rows_11),))

    # >24 char row title rejected
    with pytest.raises(ValueError):
        validate_list(
            TO,
            "Elegí:",
            "Ver",
            (ListSection(title="Sec", rows=(ListRow(id="r1", title="x" * 25),)),),
        )

    # >20 char button_label rejected
    with pytest.raises(ValueError):
        validate_list(
            TO,
            "Elegí:",
            "x" * 21,
            (ListSection(title="Sec", rows=(ListRow(id="r1", title="Ok"),)),),
        )

    # Duplicate row id rejected
    with pytest.raises(ValueError):
        validate_list(
            TO,
            "Elegí:",
            "Ver",
            (
                ListSection(
                    title="Sec",
                    rows=(ListRow(id="dup", title="A"), ListRow(id="dup", title="B")),
                ),
            ),
        )


def test_validate_buttons_limits():
    btns_3 = tuple(Button(id=f"b{i}", title=f"Btn {i}") for i in range(3))
    validate_buttons(TO, "Elegí:", btns_3)

    # >3 buttons rejected
    btns_4 = tuple(Button(id=f"b{i}", title=f"Btn {i}") for i in range(4))
    with pytest.raises(ValueError):
        validate_buttons(TO, "Elegí:", btns_4)

    # >20 char button title rejected
    with pytest.raises(ValueError):
        validate_buttons(TO, "Elegí:", (Button(id="b1", title="x" * 21),))

    # Duplicate button id rejected
    with pytest.raises(ValueError):
        validate_buttons(
            TO, "Elegí:", (Button(id="b1", title="A"), Button(id="b1", title="B"))
        )


def test_validate_template_limits():
    validate_template(
        TO,
        "recordatorio_turno",
        ("Ana", "Limpieza facial", "Mar 27/10", "10:00"),
        (
            Button(id="v1:rem:confirm:evt=e1", title="Confirmar"),
            Button(id="v1:rem:resched:evt=e1", title="Reprogramar"),
            Button(id="v1:rem:cancel:evt=e1", title="Cancelar"),
        ),
    )

    with pytest.raises(ValueError):
        validate_template(TO, "", ("Ana",))

    with pytest.raises(ValueError):
        validate_template(
            TO,
            "recordatorio_turno",
            ("Ana",),
            tuple(Button(id=f"b{i}", title="Ok") for i in range(4)),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [FakeMessenger, lambda: ConsoleMessenger(io.StringIO())])
async def test_every_messenger_enforces_shared_limits(factory):
    m: Messenger = factory()
    assert isinstance(m, Messenger)

    with pytest.raises(ValueError):
        await m.send_text(TO, "")

    with pytest.raises(ValueError):
        await m.send_list(
            TO,
            "Body",
            "Ver",
            (ListSection(title="S", rows=tuple(ListRow(id=f"i{i}", title="T") for i in range(11))),),
        )

    with pytest.raises(ValueError):
        await m.send_list(
            TO,
            "Body",
            "Ver",
            (ListSection(title="S", rows=(ListRow(id="i1", title="x" * 25),)),),
        )

    with pytest.raises(ValueError):
        await m.send_buttons(
            TO,
            "Body",
            tuple(Button(id=f"b{i}", title="Btn") for i in range(4)),
        )

    with pytest.raises(ValueError):
        await m.send_buttons(TO, "Body", (Button(id="b1", title="x" * 21),))

    with pytest.raises(ValueError):
        await m.send_template(TO, "", ("param",))


@pytest.mark.asyncio
async def test_fake_messenger_records_and_taps():
    m = FakeMessenger()
    await m.send_buttons(
        TO,
        "Menú",
        (
            Button(id="v1:svc", title="Reservar"),
            Button(id="v1:mine", title="Mi turno"),
        ),
    )
    last = m.last_message(TO)
    assert last is not None
    assert last.kind == "buttons"

    inc = m.tap("Reservar", phone=TO)
    assert inc.kind == "button_reply"
    assert inc.payload_id == "v1:svc"
    assert inc.from_phone == TO

    await m.send_list(
        TO,
        "Elegí servicio:",
        "Servicios",
        (
            ListSection(
                title="Servicios",
                rows=(
                    ListRow(id="v1:svc:svc=Facial", title="Facial"),
                    ListRow(id="v1:svc:svc=Peeling", title="Peeling"),
                ),
            ),
        ),
    )
    inc_list = m.tap(2, phone=TO)
    assert inc_list.kind == "list_reply"
    assert inc_list.payload_id == "v1:svc:svc=Peeling"
