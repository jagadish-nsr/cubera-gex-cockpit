"""Hand-checkable tests for the per-strike ΔOI positioning footprint."""

import pandas as pd

from app.oi_footprint import build_footprint, footprint_series


def test_build_footprint_weighted_centers():
    rows = [
        {"strike": 100, "put_doi": 10, "call_doi": 0},
        {"strike": 110, "put_doi": 0, "call_doi": 20},
        {"strike": 120, "put_doi": 30, "call_doi": 0},
    ]
    fp = build_footprint(rows)
    # support = put-ΔOI-weighted = (100*10 + 120*30)/(10+30) = 4600/40 = 115
    assert fp["support_build"] == 115.0
    assert fp["resistance_build"] == 110.0          # only 110 has call adds
    assert fp["peak_put_strike"] == 120 and fp["peak_call_strike"] == 110
    assert fp["put_add"] == 40 and fp["call_add"] == 20
    assert fp["net_skew"] == 20                     # put-writing dominant


def test_negative_doi_is_clipped_and_band_limits():
    rows = [
        {"strike": 100, "put_doi": -50, "call_doi": 5},   # put UNWIND -> not a build
        {"strike": 105, "put_doi": 40, "call_doi": 0},
        {"strike": 130, "put_doi": 999, "call_doi": 0},   # far OTM -> banded out
    ]
    fp = build_footprint(rows, spot=103, band_pts=5)      # keep 100 & 105 only
    assert fp["support_build"] == 105.0                   # 100's negative put_doi clipped to 0
    assert fp["net_skew"] == 40 - 5                       # put_add 40, call_add 5


def test_empty_and_no_adds():
    assert build_footprint([]) is None
    fp = build_footprint([{"strike": 100, "put_doi": 0, "call_doi": 0}])
    assert fp["support_build"] is None and fp["resistance_build"] is None


def test_footprint_series_over_snapshots():
    df = pd.DataFrame({
        "time_ist": ["09:30", "09:30", "10:00", "10:00"],
        "spot": [100, 100, 101, 101],
        "strike": [100, 102, 100, 102],
        "put_doi": [10, 0, 5, 0], "call_doi": [0, 10, 0, 20],
    })
    ser = footprint_series(df, band_frac=0.10)
    assert [t for t, *_ in ser] == ["09:30", "10:00"]     # one row per snapshot, sorted
    # 09:30 support center = 100 (only put adds at 100); resistance = 102
    assert ser[0][1] == 100.0 and ser[0][2] == 102.0
