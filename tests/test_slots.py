from datetime import datetime

from app.clock import TZ
from app.config import Service, ServicesConfig
from app.slots import Block, generate


def _cfg(
    *,
    duration: int = 60,
    grid_step: int = 15,
    buffer: int = 0,
    horizon_regular: int = 14,
    horizon_vip: int = 30,
    min_notice_h: int = 2,
) -> ServicesConfig:
    return ServicesConfig(
        services=(Service(name="Facial", duration=duration),),
        grid_step=grid_step,
        buffer=buffer,
        horizon_regular=horizon_regular,
        horizon_vip=horizon_vip,
        min_notice_h=min_notice_h,
    )


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def test_fits_inside_block_and_crosses_block_end():
    cfg = _cfg(duration=60, grid_step=15)
    blocks = [Block("2026-10-27T09:00", "2026-10-27T10:30")]
    slots = generate(blocks, [], cfg, "Facial", now="2026-10-26T09:00")
    assert slots == [
        _dt("2026-10-27T09:00"),
        _dt("2026-10-27T09:15"),
        _dt("2026-10-27T09:30"),
    ]


def test_overlaps_busy():
    cfg = _cfg(duration=60, grid_step=30)
    blocks = [Block("2026-10-27T09:00", "2026-10-27T13:00")]
    busy = [Block("2026-10-27T10:00", "2026-10-27T11:00")]
    slots = generate(blocks, busy, cfg, "Facial", now="2026-10-26T09:00")
    assert slots == [
        _dt("2026-10-27T09:00"),
        _dt("2026-10-27T11:00"),
        _dt("2026-10-27T11:30"),
        _dt("2026-10-27T12:00"),
    ]


def test_buffer_checked_against_block_and_busy():
    cfg = _cfg(duration=60, grid_step=15, buffer=15)
    blocks = [Block("2026-10-27T09:00", "2026-10-27T12:15")]
    busy = [Block("2026-10-27T10:15", "2026-10-27T11:00")]
    slots = generate(blocks, busy, cfg, "Facial", now="2026-10-26T09:00")
    assert slots == [
        _dt("2026-10-27T09:00"),
        _dt("2026-10-27T11:00"),
    ]


def test_grid_alignment():
    cfg = _cfg(duration=60, grid_step=15)
    blocks = [Block("2026-10-27T09:05", "2026-10-27T10:30")]
    slots = generate(blocks, [], cfg, "Facial", now="2026-10-26T09:00")
    assert slots == [
        _dt("2026-10-27T09:15"),
        _dt("2026-10-27T09:30"),
    ]


def test_vip_blocks_hidden_for_regulars_and_shown_for_vip():
    cfg = _cfg(duration=60, grid_step=60)
    blocks = [
        Block("2026-10-27T09:00", "2026-10-27T10:00", title="Disponible"),
        Block("2026-10-27T10:00", "2026-10-27T11:00", title="Disponible [VIP]"),
    ]
    assert generate(blocks, [], cfg, "Facial", now="2026-10-26T09:00", vip=False) == [
        _dt("2026-10-27T09:00")
    ]
    assert generate(blocks, [], cfg, "Facial", now="2026-10-26T09:00", vip=True) == [
        _dt("2026-10-27T09:00"),
        _dt("2026-10-27T10:00"),
    ]


def test_horizon_14_vs_30():
    cfg = _cfg(duration=60, grid_step=60, horizon_regular=14, horizon_vip=30)
    blocks = [
        Block("2026-11-05T10:00", "2026-11-05T11:00"),  # +10d
        Block("2026-11-15T10:00", "2026-11-15T11:00"),  # +20d
        Block("2026-12-01T10:00", "2026-12-01T11:00"),  # +36d
    ]
    assert generate(blocks, [], cfg, "Facial", now="2026-10-26T09:00", vip=False) == [
        _dt("2026-11-05T10:00")
    ]
    assert generate(blocks, [], cfg, "Facial", now="2026-10-26T09:00", vip=True) == [
        _dt("2026-11-05T10:00"),
        _dt("2026-11-15T10:00"),
    ]


def test_min_notice_2h():
    cfg = _cfg(duration=60, grid_step=15, min_notice_h=2)
    blocks = [Block("2026-10-26T09:00", "2026-10-26T12:30")]
    slots = generate(blocks, [], cfg, "Facial", now="2026-10-26T09:05")
    assert slots == [
        _dt("2026-10-26T11:15"),
        _dt("2026-10-26T11:30"),
    ]


def test_empty_result():
    cfg = _cfg(duration=60, grid_step=15)
    assert generate([], [], cfg, "Facial", now="2026-10-26T09:00") == []
    too_short = [Block("2026-10-27T09:00", "2026-10-27T09:45")]
    assert generate(too_short, [], cfg, "Facial", now="2026-10-26T09:00") == []
