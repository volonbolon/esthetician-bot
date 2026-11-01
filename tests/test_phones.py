import pytest

from app.phones import normalize_phone


@pytest.mark.parametrize(
    "raw",
    [
        "5491122334455",
        "+54 9 11 2233-4455",
        "011 15 2233-4455",
        "11 2233 4455",
        "+54 11 2233 4455",
        "(011) 15-2233-4455",
        "+5491122334455",
    ],
)
def test_normalize_phone_valid_cases(raw: str):
    assert normalize_phone(raw) == "+5491122334455"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "not-a-phone",
        "12345",
        "11 2233 445",
        "11 2233 44556",
        "+549112233445a",
        "54+91122334455",
    ],
)
def test_normalize_phone_invalid_raises(bad: str):
    with pytest.raises(ValueError):
        normalize_phone(bad)
