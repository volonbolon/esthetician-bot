import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

MessengerBackend = Literal["console", "fake", "cloud"]
CalendarBackend = Literal["fake", "google"]

_MESSENGERS = ("console", "fake", "cloud")
_CALENDARS = ("fake", "google")
_WA_VARS = ("WA_TOKEN", "WA_PHONE_NUMBER_ID", "WA_APP_SECRET", "WA_VERIFY_TOKEN")
_GCAL_VARS = ("GOOGLE_SA_JSON", "CAL_AVAILABLE_ID", "CAL_TURNOS_ID")


@dataclass(frozen=True)
class Config:
    messenger: MessengerBackend = "console"
    calendar_backend: CalendarBackend = "fake"
    db_path: str = "bot.db"
    esthetician_name: str = "Esteticista"
    admin_phones: tuple[str, ...] = ()
    wa_token: str = ""
    wa_phone_number_id: str = ""
    wa_app_secret: str = ""
    wa_verify_token: str = ""
    google_sa_json: str = ""
    cal_available_id: str = "available"
    cal_turnos_id: str = "turnos"


def load_config(env: Mapping[str, str] | None = None) -> Config:
    e = os.environ if env is None else env

    messenger = e.get("MESSENGER", "console")
    if messenger not in _MESSENGERS:
        raise ValueError(f"Invalid MESSENGER={messenger!r}; expected one of {_MESSENGERS}")

    calendar_backend = e.get("CALENDAR_BACKEND", "fake")
    if calendar_backend not in _CALENDARS:
        raise ValueError(
            f"Invalid CALENDAR_BACKEND={calendar_backend!r}; expected one of {_CALENDARS}"
        )

    missing: list[str] = []
    if messenger == "cloud":
        missing.extend(k for k in _WA_VARS if not e.get(k))
    if calendar_backend == "google":
        missing.extend(k for k in _GCAL_VARS if not e.get(k))
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    raw_admins = e.get("ADMIN_PHONES", "")
    admin_phones = tuple(p.strip() for p in raw_admins.split(",") if p.strip())

    return Config(
        messenger=messenger,  # type: ignore[arg-type]
        calendar_backend=calendar_backend,  # type: ignore[arg-type]
        db_path=e.get("DB_PATH", "bot.db"),
        esthetician_name=e.get("ESTHETICIAN_NAME", "Esteticista"),
        admin_phones=admin_phones,
        wa_token=e.get("WA_TOKEN", ""),
        wa_phone_number_id=e.get("WA_PHONE_NUMBER_ID", ""),
        wa_app_secret=e.get("WA_APP_SECRET", ""),
        wa_verify_token=e.get("WA_VERIFY_TOKEN", ""),
        google_sa_json=e.get("GOOGLE_SA_JSON", ""),
        cal_available_id=e.get("CAL_AVAILABLE_ID", "available"),
        cal_turnos_id=e.get("CAL_TURNOS_ID", "turnos"),
    )
