"""Gamma-flip mechanics: a constructed profile with a single zero-crossing, and
a monotone profile that never crosses (must FLAG, not fabricate)."""

import pandas as pd

from gex.flip import gamma_flip_profile

SPOT = 100.0


def _contract(strike, typ, oi, iv=0.20, T=0.05, r=0.0, q=0.0):
    return {"strike": strike, "type": typ, "oi": oi, "iv": iv, "T": T, "r": r, "q": q}


def test_single_crossing_between_put_and_call_clusters():
    # Put cluster below spot (short gamma -> negative), call cluster above spot
    # (long gamma -> positive). Aggregate GEX must cross zero once near spot.
    df = pd.DataFrame([
        _contract(90, "P", 5000),
        _contract(110, "C", 5000),
    ])
    res = gamma_flip_profile(df, SPOT, grid_range=0.10, grid_step=0.0025)

    assert res.flip is not None
    assert not res.flagged
    assert SPOT * 0.90 <= res.flip <= SPOT * 1.10
    # opposite signs at the extremes of the grid confirm a real crossing
    lo = res.profile["total_gex"].iloc[0]
    hi = res.profile["total_gex"].iloc[-1]
    assert lo < 0 < hi


def test_no_crossing_is_flagged_not_fabricated():
    # All calls -> dealer long gamma everywhere -> total GEX > 0 across the grid.
    df = pd.DataFrame([
        _contract(95, "C", 3000),
        _contract(105, "C", 3000),
    ])
    res = gamma_flip_profile(df, SPOT, grid_range=0.10, grid_step=0.0025)

    assert res.flip is None
    assert res.flagged
    assert (res.profile["total_gex"] > 0).all()
