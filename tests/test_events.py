import pytest

from app.events import (
    ParsedTitle,
    add_confirmed_prefix,
    build_description,
    build_title,
    extract_phone,
    is_confirmed,
    parse_title,
    strip_confirmed_prefix,
)


def test_build_and_parse_title():
    title = build_title("Limpieza facial", "Ana Pérez")
    assert title == "Limpieza facial – Ana Pérez"
    assert parse_title(title) == ParsedTitle("Limpieza facial", "Ana Pérez", confirmed=False)

    confirmed_title = build_title("Peeling", "Carla", confirmed=True)
    assert confirmed_title == "✅ Peeling – Carla"
    assert parse_title(confirmed_title) == ParsedTitle("Peeling", "Carla", confirmed=True)
    assert parse_title("Peeling - Carla") == ParsedTitle("Peeling", "Carla", confirmed=False)


def test_idempotent_confirmed_prefix():
    raw = "Dermaplaning – Lucía"
    once = add_confirmed_prefix(raw)
    twice = add_confirmed_prefix(once)
    thrice = add_confirmed_prefix("✅  ✅ Dermaplaning – Lucía")
    assert once == "✅ Dermaplaning – Lucía"
    assert twice == "✅ Dermaplaning – Lucía"
    assert thrice == "✅ Dermaplaning – Lucía"
    assert is_confirmed(once) is True
    assert is_confirmed(raw) is False
    assert strip_confirmed_prefix(twice) == raw
    assert strip_confirmed_prefix(raw) == raw


def test_invalid_titles_raise():
    with pytest.raises(ValueError):
        build_title("", "Ana")
    with pytest.raises(ValueError):
        build_title("Peeling", "   ")
    with pytest.raises(ValueError):
        parse_title("Solo texto sin separador")
    with pytest.raises(ValueError):
        add_confirmed_prefix("   ")


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        ("+5491122334455", "+5491122334455"),
        ("Cliente manual\nTel: 011 15 2233-4455\nObservaciones", "+5491122334455"),
        ("WhatsApp: +54 9 11 2233-4455", "+5491122334455"),
        ("11 2233 4455", "+5491122334455"),
        ("2026-10-27 11 2233 4455", "+5491122334455"),
        ("Reserva el 2026-10-27 a las 10:00, tel 11 2233 4455", "+5491122334455"),
        ("Sin telefono 12345", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_phone_from_description(desc: str | None, expected: str | None):
    assert extract_phone(desc) == expected


def test_build_description_normalizes_phone():
    assert build_description("011 15 2233-4455") == "+5491122334455"
