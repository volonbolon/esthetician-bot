import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

VERSION_PREFIX = "v1"
MAX_LEN = 200

VALID_KINDS: frozenset[str] = frozenset(
    {
        "svc",
        "day",
        "slot",
        "more",
        "confirm",
        "mine",
        "cancel",
        "resched",
        "rem:confirm",
        "rem:resched",
        "rem:cancel",
        "vip_noslot",
        "menu",
        "talk",
    }
)

_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_FORBIDDEN_VAL_CHARS = frozenset({";", "=", "\n", "\r", "\0"})


@dataclass(frozen=True)
class Payload:
    kind: str
    params: dict[str, str] = field(default_factory=dict)

    def __getitem__(self, key: str) -> str:
        return self.params[key]

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.params.get(key, default)


def encode(
    kind: str | Payload,
    params: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> str:
    if isinstance(kind, Payload):
        merged: dict[str, Any] = {**kind.params, **(params or {}), **kwargs}
        kind_str = kind.kind
    else:
        merged = {**(params or {}), **kwargs}
        kind_str = kind

    if kind_str not in VALID_KINDS:
        raise ValueError(f"Unknown payload kind: {kind_str!r}")

    parts: list[str] = []
    for k, v in merged.items():
        if v is None:
            continue
        if not _KEY_RE.match(str(k)):
            raise ValueError(f"Invalid payload key: {k!r}")
        val_str = str(v)
        if any(c in _FORBIDDEN_VAL_CHARS for c in val_str):
            raise ValueError(f"Invalid characters in payload value for {k!r}: {val_str!r}")
        parts.append(f"{k}={val_str}")

    body = f"{VERSION_PREFIX}:{kind_str}" if not parts else f"{VERSION_PREFIX}:{kind_str}:{';'.join(parts)}"
    if len(body) > MAX_LEN:
        raise ValueError(f"Payload exceeds {MAX_LEN} chars ({len(body)}): {body!r}")
    return body


def decode(raw: str) -> Payload:
    if not raw or len(raw) > MAX_LEN or any(c in ("\n", "\r", "\0") for c in raw):
        raise ValueError(f"Invalid payload: {raw!r}")

    prefix = f"{VERSION_PREFIX}:"
    if not raw.startswith(prefix):
        raise ValueError(f"Invalid payload version prefix: {raw!r}")

    rest = raw[len(prefix) :]
    matched_kind: str | None = None
    params_str = ""
    for candidate in sorted(VALID_KINDS, key=len, reverse=True):
        if rest == candidate:
            matched_kind = candidate
            break
        if rest.startswith(f"{candidate}:"):
            matched_kind = candidate
            params_str = rest[len(candidate) + 1 :]
            if not params_str:
                raise ValueError(f"Trailing colon with empty params in payload: {raw!r}")
            break

    if matched_kind is None:
        raise ValueError(f"Unknown payload kind in: {raw!r}")

    params: dict[str, str] = {}
    if params_str:
        for pair in params_str.split(";"):
            if "=" not in pair:
                raise ValueError(f"Malformed param {pair!r} in payload: {raw!r}")
            k, v = pair.split("=", 1)
            if not _KEY_RE.match(k) or k in params:
                raise ValueError(f"Invalid or duplicate param key {k!r} in payload: {raw!r}")
            params[k] = v

    return Payload(kind=matched_kind, params=params)
