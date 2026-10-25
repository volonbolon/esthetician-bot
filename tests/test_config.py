import pytest

from app.config import load_config


def test_defaults_require_no_wa_or_gcal_vars():
    cfg = load_config({})
    assert cfg.messenger == "console"
    assert cfg.calendar_backend == "fake"
    assert cfg.wa_token == ""


def test_cloud_messenger_requires_wa_vars():
    with pytest.raises(ValueError, match="WA_TOKEN"):
        load_config({"MESSENGER": "cloud"})

    cfg = load_config(
        {
            "MESSENGER": "cloud",
            "WA_TOKEN": "tok",
            "WA_PHONE_NUMBER_ID": "123",
            "WA_APP_SECRET": "sec",
            "WA_VERIFY_TOKEN": "ver",
        }
    )
    assert cfg.messenger == "cloud"
    assert cfg.wa_token == "tok"


def test_google_calendar_requires_gcal_vars():
    with pytest.raises(ValueError, match="GOOGLE_SA_JSON"):
        load_config({"CALENDAR_BACKEND": "google"})


def test_invalid_switches_raise():
    with pytest.raises(ValueError, match="MESSENGER"):
        load_config({"MESSENGER": "invalid"})
    with pytest.raises(ValueError, match="CALENDAR_BACKEND"):
        load_config({"CALENDAR_BACKEND": "invalid"})
