import pytest

from app.payloads import MAX_LEN, VALID_KINDS, Payload, decode, encode


@pytest.mark.parametrize("kind", sorted(VALID_KINDS))
def test_roundtrip_all_kinds_without_params(kind: str):
    raw = encode(kind)
    assert raw == f"v1:{kind}"
    parsed = decode(raw)
    assert parsed == Payload(kind=kind, params={})


def test_roundtrip_with_params():
    raw = encode("slot", svc="Limpieza facial", t="2026-10-27T10:00", resched="evt_123")
    assert raw == "v1:slot:svc=Limpieza facial;t=2026-10-27T10:00;resched=evt_123"
    parsed = decode(raw)
    assert parsed.kind == "slot"
    assert parsed["svc"] == "Limpieza facial"
    assert parsed["t"] == "2026-10-27T10:00"
    assert parsed.get("resched") == "evt_123"
    assert parsed.get("missing") is None


def test_reminder_colon_kind_with_params():
    raw = encode("rem:cancel", evt="abc123")
    assert raw == "v1:rem:cancel:evt=abc123"
    assert decode(raw) == Payload(kind="rem:cancel", params={"evt": "abc123"})


def test_max_length_enforced():
    exact = encode("confirm", pad="x" * (MAX_LEN - len("v1:confirm:pad=")))
    assert len(exact) == MAX_LEN
    assert decode(exact).kind == "confirm"

    with pytest.raises(ValueError):
        encode("confirm", pad="x" * (MAX_LEN - len("v1:confirm:pad=") + 1))

    with pytest.raises(ValueError):
        decode("v1:confirm:pad=" + "x" * MAX_LEN)


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "menu",
        "v2:menu",
        "v1:unknown",
        "v1:menu:",
        "v1:slot:no_equals",
        "v1:slot:=val",
        "v1:slot:1bad=val",
        "v1:slot:a=1;a=2",
        "v1:slot:a=1;;b=2",
        "v1:menu\nextra",
    ],
)
def test_decode_rejects_garbage(garbage: str):
    with pytest.raises(ValueError):
        decode(garbage)


def test_encode_rejects_invalid_kind_or_delimiters():
    with pytest.raises(ValueError):
        encode("not_a_kind")
    with pytest.raises(ValueError):
        encode("slot", svc="bad;value")
    with pytest.raises(ValueError):
        encode("slot", svc="bad=value")
    with pytest.raises(ValueError):
        encode("slot", **{"bad-key": "1"})
