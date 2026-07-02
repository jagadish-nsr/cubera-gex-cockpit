"""Hand-checkable tests for the per-strike ΔOI positioning footprint."""

import json
from pathlib import Path

import pandas as pd

from app.oi_footprint import (build_footprint, classify_buildup, footprint_series,
                              intraday_buildup_streaks, intraday_premium_change,
                              top_builds)
from data.nse import per_strike_nearest_changes

_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "nse_v3_sample.json"


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


def test_per_strike_nearest_changes_on_fixture():
    payload = json.loads(_FIXTURE.read_text())
    nc = per_strike_nearest_changes(payload)
    # All fixture rows share expiry 30-06-2026 (the nearest). CE 23550: dOI +221, chg -76.25.
    c = nc[23550.0]
    assert c["call_doi_near"] == 221.0 and c["call_dprem"] == -76.25
    assert c["put_doi_near"] == 207.0 and c["put_dprem"] == 105.4
    # These aligned inputs give the EXACT tag: OI up + premium down = writing.
    assert classify_buildup(c["call_doi_near"], c["call_dprem"]) == "writing (short build-up)"
    assert classify_buildup(c["put_doi_near"], c["put_dprem"]) == "long build-up"


def test_per_strike_nearest_changes_bands_and_empty():
    payload = json.loads(_FIXTURE.read_text())
    nc = per_strike_nearest_changes(payload, spot=23550, band_pts=60)  # drop 23450 (100 away)
    assert set(nc) == {23500.0, 23550.0, 23600.0}
    assert per_strike_nearest_changes({}) == {}          # defensive: no data -> {}


def test_top_builds_passes_through_nearest_change_fields():
    rows = [{"strike": 24000, "put_doi": 143000, "put_ltp": 120.0, "put_vol": 9,
             "put_doi_near": 90000, "put_dprem": -15.0}]
    top = top_builds(rows, "put", spot=24000, band_pts=500, top_n=6)
    assert top[0]["doi_near"] == 90000 and top[0]["dprem"] == -15.0
    # Exact tag off the aligned nearest fields: OI up + premium down = writing.
    assert classify_buildup(top[0]["doi_near"], top[0]["dprem"]) == "writing (short build-up)"


def _streak_df(oi_seq, ltp_seq, strike=24000, side="call"):
    """Build a one-strike day frame with given OI and LTP per snapshot."""
    n = len(oi_seq)
    return pd.DataFrame({
        "time_ist": [f"09:{30 + i:02d}" for i in range(n)],
        "spot": [24000] * n, "strike": [strike] * n,
        f"{side}_oi": oi_seq, f"{side}_ltp": ltp_seq,
    })


def test_intraday_streaks_detects_red_writing_run():
    # OI rising each step, premium falling each step -> 3 red (writing) intervals.
    df = _streak_df([100000, 105000, 110000, 116000], [200.0, 190.0, 180.0, 170.0])
    fl = intraday_buildup_streaks(df, "call", run=3, min_cum_doi=1000)
    assert len(fl) == 1
    f = fl[0]
    assert f["kind"] == "red" and f["run_len"] == 3 and f["live"] is True
    assert f["cum_doi"] == 16000 and f["start_time"] == "09:30" and f["end_time"] == "09:33"
    assert f["cum_prem_pct"] < 0


def test_intraday_streaks_green_and_run_threshold():
    # Only 2 up-up intervals -> below run=3 -> nothing.
    df = _streak_df([100000, 106000, 112000], [100.0, 108.0, 116.0])
    assert intraday_buildup_streaks(df, "call", run=3, min_cum_doi=1000) == []
    # Extend to 3 up-up intervals -> one green run.
    df2 = _streak_df([100000, 106000, 112000, 118000], [100.0, 108.0, 116.0, 125.0])
    fl = intraday_buildup_streaks(df2, "call", run=3, min_cum_doi=1000)
    assert fl and fl[0]["kind"] == "green" and fl[0]["cum_prem_pct"] > 0


def test_intraday_streaks_min_cum_doi_filters_illiquid():
    # Premium swings hard but OI barely moves (illiquid) -> filtered by the floor.
    df = _streak_df([50, 60, 70, 80], [2.0, 2.4, 2.9, 3.5])
    assert intraday_buildup_streaks(df, "call", run=3, min_cum_doi=2000) == []


def test_intraday_streaks_broken_by_oi_drop():
    # OI falls on the 2nd interval -> breaks the run; longest same-colour run < 3.
    df = _streak_df([100000, 108000, 104000, 110000, 116000],
                    [100.0, 108.0, 103.0, 111.0, 120.0])
    assert intraday_buildup_streaks(df, "call", run=3, min_cum_doi=1000) == []


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
