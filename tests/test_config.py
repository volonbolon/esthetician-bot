import pytest

from app.config import load_config, load_services


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


def test_load_services_defaults_and_file():
    cfg = load_services({"services": [{"name": "Facial", "duration": 60}]})
    assert len(cfg.services) == 1
    assert cfg.get("Facial").duration == 60
    assert cfg.grid_step == 15
    assert cfg.buffer == 0
    assert cfg.horizon_regular == 14
    assert cfg.horizon_vip == 30
    assert cfg.min_notice_h == 2
    assert cfg.cancel_cutoff_h == 24

    file_cfg = load_services("services.yaml")
    assert len(file_cfg.services) >= 1


@pytest.mark.parametrize(
    "bad_payload",
    [
        {},
        {"services": []},
        {"services": [{"name": "", "duration": 60}]},
        {"services": [{"name": "Facial", "duration": 0}]},
        {"services": [{"name": "Facial", "duration": True}]},
        {"services": [{"name": "Facial", "duration": 30}, {"name": "Facial", "duration": 45}]},
        {"services": [{"name": "Facial", "duration": 60}], "grid_step": 0},
        {"services": [{"name": "Facial", "duration": 60}], "buffer": -1},
        {"services": [{"name": "Facial", "duration": 60}], "horizon_regular": 0},
        {"services": [{"name": "Facial", "duration": 60}], "horizon_vip": 0},
        {"services": [{"name": "Facial", "duration": 60}], "min_notice_h": -1},
        {"services": [{"name": "Facial", "duration": 60}], "cancel_cutoff_h": -1},
    ],
)
def test_load_services_validation_errors(bad_payload):
    with pytest.raises(ValueError):
        load_services(bad_payload)
