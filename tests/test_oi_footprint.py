"""Hand-checkable tests for the per-strike ΔOI positioning footprint."""

import json
from pathlib import Path

import pandas as pd

from app.oi_footprint import (build_footprint, classify_buildup, footprint_series,
                              intraday_buildup_streaks, intraday_premium_change,
                              oi_weighted_levels, top_builds, window_footprint)
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


def test_oi_weighted_levels_weighted_and_top():
    rows = [
        {"strike": 23900, "put_oi": 300, "call_oi": 10},
        {"strike": 24000, "put_oi": 100, "call_oi": 100},
        {"strike": 24100, "put_oi": 0, "call_oi": 500},
        {"strike": 25000, "put_oi": 9999, "call_oi": 0},   # far OTM -> banded out
    ]
    lv = oi_weighted_levels(rows, spot=24000, band_pts=200)   # keep 23900/24000/24100
    # support (put OI) weighted = (23900*300 + 24000*100)/(400) = 9,570,000/400 = 23925
    assert lv["support_w"] == 23925.0
    assert lv["support_top"] == 23900.0                       # max put OI in band
    # resistance (call OI) weighted = (23900*10 + 24000*100 + 24100*500)/610 = 14,689,000/610 ≈ 24080.33
    assert abs(lv["resistance_w"] - 24080.328) < 0.01
    assert lv["resistance_top"] == 24100.0                     # max call OI in band
    assert oi_weighted_levels([]) is None


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


def _streak_df(oi_seq, ltp_seq, strike=24000, side="call", vol_seq=None):
    """Build a one-strike day frame with given OI and LTP (and optional cumulative
    volume) per snapshot."""
    n = len(oi_seq)
    d = {
        "time_ist": [f"09:{30 + i:02d}" for i in range(n)],
        "spot": [24000] * n, "strike": [strike] * n,
        f"{side}_oi": oi_seq, f"{side}_ltp": ltp_seq,
    }
    if vol_seq is not None:
        d[f"{side}_vol"] = vol_seq
    return pd.DataFrame(d)


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


def test_intraday_streaks_only_live_drops_stale_runs():
    # A 3-red run EARLY (09:30-09:33) then the last two intervals go neutral/covering.
    # default (longest) surfaces the stale morning run; only_live hides it.
    df = _streak_df([100000, 105000, 110000, 116000, 116000, 112000],
                    [200.0, 190.0, 180.0, 170.0, 170.0, 175.0])
    default = intraday_buildup_streaks(df, "call", run=3, min_cum_doi=1000)
    assert default and default[0]["kind"] == "red" and default[0]["live"] is False
    assert intraday_buildup_streaks(df, "call", run=3, min_cum_doi=1000, only_live=True) == []


def test_intraday_streaks_only_live_keeps_trailing_run():
    # A quiet start then a live 3-green run ending at the latest snapshot.
    df = _streak_df([100000, 100000, 106000, 112000, 118000],
                    [100.0, 100.0, 108.0, 116.0, 125.0])
    fl = intraday_buildup_streaks(df, "call", run=3, min_cum_doi=1000, only_live=True)
    assert len(fl) == 1 and fl[0]["kind"] == "green" and fl[0]["live"] is True
    assert fl[0]["run_len"] == 3 and fl[0]["end_time"] == "09:34"


def test_intraday_streaks_turnover_from_volume():
    # Cumulative volume over the 3-interval run = 300000-30000 = 270000; OI end 116000.
    df = _streak_df([100000, 105000, 110000, 116000], [200.0, 190.0, 180.0, 170.0],
                    vol_seq=[30000, 120000, 210000, 300000])
    f = intraday_buildup_streaks(df, "call", run=3, min_cum_doi=1000)[0]
    assert abs(f["turnover"] - 270000 / 116000) < 1e-6
    # No volume column -> turnover is None (back-compat with old CSVs).
    df2 = _streak_df([100000, 105000, 110000, 116000], [200.0, 190.0, 180.0, 170.0])
    assert intraday_buildup_streaks(df2, "call", run=3, min_cum_doi=1000)[0]["turnover"] is None


def _win_df():
    """Two-strike day frame across four snapshots (09:00..09:45), with nearest-expiry
    ΔOI (vs prev close) rising for puts and roughly flat for calls near spot."""
    t = ["09:00", "09:00", "09:15", "09:15", "09:30", "09:30", "09:45", "09:45"]
    strike = [24000, 24100] * 4
    # cumulative ΔOI-vs-prevclose per snapshot: puts build, calls barely move
    put_near = [1000, 500, 3000, 1200, 6000, 2000, 9000, 2500]
    call_near = [800, 900, 900, 1100, 1100, 1300, 1200, 1600]
    return pd.DataFrame({"time_ist": t, "spot": [24050] * 8, "strike": strike,
                         "put_doi_near": put_near, "call_doi_near": call_near,
                         "put_oi": put_near, "call_oi": call_near})


def test_window_footprint_60min_put_writing():
    # 60-min trailing window at 09:45 has no snapshot ≤ 08:45, so it uses the earliest
    # (09:00) as ref -> partial. Put adds dominate -> put-writing (support building).
    w = window_footprint(_win_df(), 60, spot=24050, band_pts=500, now_hm=9 * 60 + 45)
    assert w["label"] == "put-writing" and w["near"] is True and w["partial"] is True
    # put adds: (9000-1000)+(2500-500)=10000 ; call adds: (1200-800)+(1600-900)=1100
    assert w["put_add"] == 10000.0 and w["call_add"] == 1100.0
    assert w["net"] == 8900.0 and w["frac"] > 0.2


def test_window_footprint_15min_ref_selection():
    # 15-min window at 09:45 -> ref is the 09:30 snapshot (last ≤ 09:30), not partial.
    w = window_footprint(_win_df(), 15, spot=24050, band_pts=500, now_hm=9 * 60 + 45)
    assert w["partial"] is False and w["span_min"] == 15
    # put adds over 09:30->09:45: (9000-6000)+(2500-2000)=3500 ; calls: (1200-1100)+(1600-1300)=400
    assert w["put_add"] == 3500.0 and w["call_add"] == 400.0 and w["label"] == "put-writing"


def test_window_footprint_band_and_fallback_to_all_expiry():
    df = _win_df().drop(columns=["put_doi_near", "call_doi_near"])   # force the level fallback
    w = window_footprint(df, 15, spot=24050, band_pts=500, now_hm=9 * 60 + 45)
    assert w["near"] is False and w["label"] == "put-writing"


def test_window_footprint_turnover_weighting():
    # Two strikes: puts build MORE contracts but on a CHEAP option; calls build fewer
    # contracts on an EXPENSIVE option. Raw-OI says put-writing; TURNOVER flips to calls.
    t = ["09:30", "09:30", "09:45", "09:45"]
    df = pd.DataFrame({"time_ist": t, "spot": [24050] * 4, "strike": [24000, 24100] * 2,
                       "put_doi_near": [0, 0, 10000, 0], "call_doi_near": [0, 0, 0, 2000],
                       "put_ltp": [5, 5, 5, 5], "call_ltp": [200, 200, 200, 200]})
    oi = window_footprint(df, 15, spot=24050, band_pts=500, now_hm=9 * 60 + 45)  # raw OI
    assert oi["label"] == "put-writing" and oi["unit"] == "lots"          # 10000 puts > 2000 calls
    tv = window_footprint(df, 15, spot=24050, band_pts=500, now_hm=9 * 60 + 45, lot_size=75)
    # put turnover = 10000·5·75 = 3.75M ; call turnover = 2000·200·75 = 30M -> calls dominate
    assert tv["unit"] == "inr" and tv["label"] == "call-writing"
    assert tv["call_add"] == 2000 * 200 * 75 and tv["put_add"] == 10000 * 5 * 75
    # the biggest single-strike ₹ build is the 24100 call (30M > the 3.75M put)
    assert tv["peak_strike"] == 24100.0 and tv["peak_side"] == "call"
    assert tv["peak_val"] == 2000 * 200 * 75


def test_window_footprint_too_few_snapshots():
    df = _win_df()
    df = df[df["time_ist"] == "09:00"]
    assert window_footprint(df, 15, spot=24050, band_pts=500) is None


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
