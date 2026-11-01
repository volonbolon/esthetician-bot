import pytest

from app.ports import (
    Button,
    ListRow,
    ListSection,
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
