"""Unit tests for Phase 2: Underlying Targets, Stops, and Touch Probabilities."""

import pytest
from target_engine.levels import (
    compute_close_probability,
    compute_touch_probability,
    generate_underlying_targets,
)
from target_engine.walls import OptionWall


def test_touch_and_close_probabilities():
    sigma_h = 100.0  # 1-SD is 100 pts
    dist = 100.0

    # At distance = 1-SD, reflection principle gives touch prob approx 2 * (1 - N(1)) = 2 * 0.1587 ~= 0.317
    p_touch = compute_touch_probability(distance=dist, sigma_h=sigma_h)
    assert 0.28 <= p_touch <= 0.35

    # Close prob is 1 - N(1) ~= 0.1587
    p_close = compute_close_probability(distance=dist, sigma_h=sigma_h)
    assert 0.13 <= p_close <= 0.18

    # Touch probability must be strictly greater than close beyond probability
    assert p_touch > p_close


def test_generate_underlying_targets():
    call_walls = [OptionWall(strike=25200.0, side="CALL", oi=500000, oi_share=0.3, oi_change=0, strength=0.3)]
    put_walls = [OptionWall(strike=24800.0, side="PUT", oi=600000, oi_share=0.35, oi_change=0, strength=0.35)]

    targets = generate_underlying_targets(
        spot=25000.0,
        fut=25010.0,
        atm_iv=0.15,
        horizon_minutes=60,
        call_walls=call_walls,
        put_walls=put_walls,
    )

    assert "bull" in targets
    assert "bear" in targets
    assert "base" in targets

    bull = targets["bull"]
    bear = targets["bear"]
    base = targets["base"]

    # Bull target must be above spot, bull stop below spot
    assert bull.target > 25000.0
    assert bull.stop < 25000.0

    # Bear target must be below spot, bear stop above spot
    assert bear.target < 25000.0
    assert bear.stop > 25000.0

    # Base target is pinned near midpoint
    assert 24900.0 <= base.target <= 25100.0

    # Probabilities must be within valid range
    assert 0.0 < bull.p_touch_target < 1.0
    assert 0.0 < bear.p_touch_target < 1.0
