import re

_ALLOWED_RE = re.compile(r"^\+?[\d\s().-]+$")
_AR_RE = re.compile(
    r"^(?:00)?(?:549?)?0?(?:(11)(?:15)?(\d{8})|([23]\d{2})(?:15)?(\d{7})|([23]\d{3})(?:15)?(\d{6}))$"
)


def normalize_phone(raw: str) -> str:
    s = raw.strip()
    if not _ALLOWED_RE.match(s) or not (m := _AR_RE.match(re.sub(r"\D", "", s))):
        raise ValueError(f"Invalid phone number: {raw!r}")
    return f"+549{''.join(g for g in m.groups() if g)}"
