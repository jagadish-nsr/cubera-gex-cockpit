"""Phase A time-series: OI/volume aggregation (hand-checkable) + CSV round-trip."""

import math
from datetime import datetime, timezone
from types import SimpleNamespace

import app.timeseries as ts
from app.data_access import load_dashboard
from data.nse import aggregate_oi_volume, nearest_expiry_premiums, per_strike_oi


def test_aggregate_oi_volume_hand_checkable():
    # Two strikes; one PE missing on the second -> exercises the skip path.
    payload = {"records": {"underlyingValue": 105, "data": [
        {"strikePrice": 100,
         "CE": {"openInterest": 10, "changeinOpenInterest": 4, "totalTradedVolume": 100},
         "PE": {"openInterest": 20, "changeinOpenInterest": -1, "totalTradedVolume": 50}},
        {"strikePrice": 110,
         "CE": {"openInterest": 5, "changeinOpenInterest": 1, "totalTradedVolume": 7}},
    ]}}
    a = aggregate_oi_volume(payload)
    assert a["call_oi"] == 15 and a["put_oi"] == 20
    assert a["call_doi"] == 5 and a["put_doi"] == -1
    assert a["call_vol"] == 107 and a["put_vol"] == 50
    assert math.isclose(a["pcr_oi"], 20 / 15)


def test_aggregate_oi_volume_empty_is_safe():
    a = aggregate_oi_volume({"records": {"data": []}})
    assert a["call_oi"] == 0 and a["put_oi"] == 0
    assert math.isnan(a["pcr_oi"])          # no calls -> PCR undefined, not a crash


def test_per_strike_oi_hand_checkable():
    # Same strike 100 appears in two expiries -> summed; 110 is outside the band.
    payload = {"records": {"underlyingValue": 100, "data": [
        {"strikePrice": 100,
         "CE": {"openInterest": 10, "changeinOpenInterest": 4, "totalTradedVolume": 100},
         "PE": {"openInterest": 20, "changeinOpenInterest": -1, "totalTradedVolume": 50}},
        {"strikePrice": 100,   # second expiry, same strike -> sums in
         "CE": {"openInterest": 5, "changeinOpenInterest": 2, "totalTradedVolume": 7}},
        {"strikePrice": 110,   # 10 pts away -> excluded by band_pts=5
         "PE": {"openInterest": 99, "changeinOpenInterest": 9, "totalTradedVolume": 9}},
    ]}}
    rows = per_strike_oi(payload, spot=100, band_pts=5)
    assert [r["strike"] for r in rows] == [100.0]          # 110 filtered out
    r = rows[0]
    assert r["call_oi"] == 15 and r["call_doi"] == 6 and r["call_vol"] == 107
    assert r["put_oi"] == 20 and r["put_doi"] == -1 and r["put_vol"] == 50

    # No band -> all strikes returned, sorted.
    assert [r["strike"] for r in per_strike_oi(payload)] == [100.0, 110.0]


def test_nearest_expiry_premiums():
    from datetime import date
    rows = [
        {"expiry": date(2026, 7, 2), "strike": 100.0, "type": "C", "price": 5.0},
        {"expiry": date(2026, 7, 2), "strike": 100.0, "type": "P", "price": 4.0},
        {"expiry": date(2026, 7, 2), "strike": 110.0, "type": "C", "price": 1.5},
        # later expiry -> ignored (we only want the expiring contract's premium)
        {"expiry": date(2026, 7, 9), "strike": 100.0, "type": "C", "price": 9.0},
    ]
    prem = nearest_expiry_premiums(rows)
    assert prem[100.0] == {"call_ltp": 5.0, "put_ltp": 4.0}   # 7/2 only, not 9.0
    assert prem[110.0] == {"call_ltp": 1.5, "put_ltp": None}  # no PE row
    assert nearest_expiry_premiums([]) == {}


def test_strike_rows_build_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "OISTRIKES_DIR", tmp_path)
    data = SimpleNamespace(
        fetched_at=datetime(2026, 6, 30, 5, 0, tzinfo=timezone.utc),
        levels=SimpleNamespace(spot=24010.7),
        oi_strikes=[
            {"strike": 24000.0, "call_oi": 1, "call_doi": 2, "put_oi": 3,
             "put_doi": 4, "call_vol": 5, "put_vol": 6,
             "call_ltp": 120.5, "put_ltp": 110.25, "net_gex": 999},
            {"strike": 24100.0, "call_oi": 7, "call_doi": 8, "put_oi": 9,
             "put_doi": 10, "call_vol": 11, "put_vol": 12,
             "call_ltp": 60.0, "put_ltp": 150.0, "net_gex": -42},
        ],
    )
    rows = ts.build_strike_rows(data)
    assert len(rows) == 2
    assert set(rows[0]) == set(ts.STRIKE_FIELDS)            # keys in lock-step
    assert rows[0]["date_ist"] == "2026-06-30"             # 05:00 UTC -> 10:30 IST
    assert rows[0]["spot"] == 24010.7 and rows[0]["strike"] == 24000.0
    assert rows[0]["call_ltp"] == 120.5 and rows[0]["net_gex"] == 999

    ts.append_strike_rows(rows)
    df = ts.load_strikes_day("2026-06-30")
    assert len(df) == 2 and list(df.columns) == ts.STRIKE_FIELDS
    assert df["put_doi"].tolist() == [4, 10]
    assert df["put_ltp"].tolist() == [110.25, 150.0]


def test_strike_rows_empty_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "OISTRIKES_DIR", tmp_path)
    # Demo path carries no raw payload -> no per-strike OI -> nothing written.
    data = load_dashboard(use_demo=True)
    assert ts.build_strike_rows(data) == []
    assert ts.append_strike_rows([]) is None


def test_build_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "TS_DIR", tmp_path)        # write into a temp dir
    data = load_dashboard(use_demo=True)               # offline, deterministic

    row = ts.build_row(data)
    assert set(row) == set(ts.FIELDS)                  # keys stay in lock-step
    assert row["spot"] == round(data.levels.spot, 2)
    assert row["source"] == "DEMO-SYNTHETIC"

    path = ts.append_row(row)
    assert path.parent == tmp_path / "NIFTY"            # per-index subfolder
    ts.append_row(row)                                  # second point, same day

    df = ts.load_day(row["date_ist"])
    assert len(df) == 2
    assert list(df.columns) == ts.FIELDS
    assert df["date_ist"].iloc[0] == row["date_ist"]
    assert row["date_ist"] in ts.available_days()


def test_append_if_due_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "TS_DIR", tmp_path)
    row = ts.build_row(load_dashboard(use_demo=True))

    p1, w1 = ts.append_if_due(row, min_gap_minutes=10)
    assert w1 and p1.exists()                       # first row writes

    p2, w2 = ts.append_if_due(row, min_gap_minutes=10)
    assert not w2                                    # same timestamp < 10 min -> skipped
    assert len(ts.load_day(row["date_ist"])) == 1

    _, w3 = ts.append_if_due(row, min_gap_minutes=0)
    assert w3                                        # guard disabled -> writes
    assert len(ts.load_day(row["date_ist"])) == 2
