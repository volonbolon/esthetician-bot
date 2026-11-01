import re
from typing import NamedTuple

from app.phones import normalize_phone

CONFIRMED_PREFIX = "✅"
_PHONE_CANDIDATE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


class ParsedTitle(NamedTuple):
    service: str
    name: str
    confirmed: bool = False


def is_confirmed(title: str) -> bool:
    return title.lstrip().startswith(CONFIRMED_PREFIX)


def strip_confirmed_prefix(title: str) -> str:
    s = title.strip()
    while s.startswith(CONFIRMED_PREFIX):
        s = s[len(CONFIRMED_PREFIX) :].lstrip()
    return s


def add_confirmed_prefix(title: str) -> str:
    base = strip_confirmed_prefix(title)
    if not base:
        raise ValueError("Event title cannot be empty")
    return f"{CONFIRMED_PREFIX} {base}"


def build_title(service: str, name: str, *, confirmed: bool = False) -> str:
    svc = service.strip()
    client = name.strip()
    if not svc or not client:
        raise ValueError("service and name must be non-empty")
    base = f"{svc} – {client}"
    return add_confirmed_prefix(base) if confirmed else base


def parse_title(title: str) -> ParsedTitle:
    confirmed = is_confirmed(title)
    body = strip_confirmed_prefix(title)
    for sep in (" – ", " - ", "–"):
        if sep in body:
            svc, client = body.split(sep, 1)
            svc, client = svc.strip(), client.strip()
            if svc and client:
                return ParsedTitle(service=svc, name=client, confirmed=confirmed)
    raise ValueError(f"Invalid event title format: {title!r}")


def build_description(phone: str) -> str:
    return normalize_phone(phone)


def extract_phone(description: str | None) -> str | None:
    if not description:
        return None
    for match in _PHONE_CANDIDATE_RE.finditer(description):
        raw = match.group(0)
        parts = raw.split()
        for i in range(len(parts)):
            try:
                return normalize_phone(" ".join(parts[i:]))
            except ValueError:
                continue
    return None
