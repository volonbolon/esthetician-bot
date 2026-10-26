import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

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


@dataclass(frozen=True)
class Service:
    name: str
    duration: int


@dataclass(frozen=True)
class ServicesConfig:
    services: tuple[Service, ...]
    grid_step: int = 15
    buffer: int = 0
    horizon_regular: int = 14
    horizon_vip: int = 30
    min_notice_h: int = 2
    cancel_cutoff_h: int = 24

    def get(self, name: str) -> Service:
        for svc in self.services:
            if svc.name == name:
                return svc
        raise KeyError(f"Unknown service: {name!r}")


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


def _int_field(data: Mapping[str, Any], key: str, default: int, *, min_val: int) -> int:
    val = data.get(key, default)
    if isinstance(val, bool) or not isinstance(val, int) or val < min_val:
        raise ValueError(f"Invalid {key}={val!r}; expected int >= {min_val}")
    return val


def load_services(source: str | Path | Mapping[str, Any] = "services.yaml") -> ServicesConfig:
    if isinstance(source, (str, Path)):
        raw = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
    else:
        raw = source

    if not isinstance(raw, Mapping) or not isinstance(raw.get("services"), list) or not raw["services"]:
        raise ValueError("services must be a non-empty list")

    services: list[Service] = []
    seen: set[str] = set()
    for item in raw["services"]:
        if not isinstance(item, Mapping) or "name" not in item or "duration" not in item:
            raise ValueError("each service must be a mapping with name and duration")
        name = item.get("name")
        duration = item.get("duration")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid service name: {name!r}")
        name = name.strip()
        if name in seen:
            raise ValueError(f"Duplicate service name: {name!r}")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            raise ValueError(f"Invalid duration for {name!r}: {duration!r}")
        seen.add(name)
        services.append(Service(name=name, duration=duration))

    return ServicesConfig(
        services=tuple(services),
        grid_step=_int_field(raw, "grid_step", 15, min_val=1),
        buffer=_int_field(raw, "buffer", 0, min_val=0),
        horizon_regular=_int_field(raw, "horizon_regular", 14, min_val=1),
        horizon_vip=_int_field(raw, "horizon_vip", 30, min_val=1),
        min_notice_h=_int_field(raw, "min_notice_h", 2, min_val=0),
        cancel_cutoff_h=_int_field(raw, "cancel_cutoff_h", 24, min_val=0),
    )
