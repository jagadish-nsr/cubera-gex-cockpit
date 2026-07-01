"""Hand-checkable tests for the per-strike ΔOI positioning footprint."""

import pandas as pd

from app.oi_footprint import (build_footprint, classify_buildup, footprint_series,
                              intraday_premium_change, top_builds)


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


def test_classify_buildup_matrix():
    assert classify_buildup(100, -5) == "writing (short build-up)"   # OI up, prem down
    assert classify_buildup(100, +5) == "long build-up"              # OI up, prem up
    assert classify_buildup(-100, +5) == "short covering"            # OI down, prem up
    assert classify_buildup(-100, -5) == "long unwinding"            # OI down, prem down
    assert classify_buildup(100, 0) == "no clear build"              # premium flat
    assert classify_buildup(100, None) is None                       # missing premium


def test_top_builds_ranks_positive_doi_within_band():
    rows = [
        {"strike": 24000, "put_doi": 143000, "put_ltp": 120.0, "put_vol": 9},
        {"strike": 23900, "put_doi": 108000, "put_ltp": 95.0, "put_vol": 8},
        {"strike": 23800, "put_doi": -5000, "put_ltp": 70.0, "put_vol": 7},   # unwind -> excluded
        {"strike": 20000, "put_doi": 999999, "put_ltp": 5.0, "put_vol": 1},   # far OTM -> banded out
    ]
    top = top_builds(rows, "put", spot=24000, band_pts=500, top_n=6)
    assert [t["strike"] for t in top] == [24000.0, 23900.0]          # sorted desc, positive, in-band
    assert top[0]["doi"] == 143000 and top[0]["ltp"] == 120.0


def test_intraday_premium_change_first_vs_last():
    df = pd.DataFrame({
        "time_ist": ["09:30", "09:30", "15:15", "15:15"],
        "strike": [24000, 24100, 24000, 24100],
        "call_ltp": [50.0, 30.0, 65.0, 20.0], "put_ltp": [40.0, 60.0, 55.0, 70.0],
    })
    ch = intraday_premium_change(df)
    assert ch[24000.0]["call"] == 15.0 and ch[24000.0]["put"] == 15.0   # 65-50, 55-40
    assert ch[24100.0]["call"] == -10.0                                  # 20-30


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
