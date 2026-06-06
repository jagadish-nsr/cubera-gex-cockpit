"""Phase A time-series: OI/volume aggregation (hand-checkable) + CSV round-trip."""

import math

import app.timeseries as ts
from app.data_access import load_dashboard
from data.nse import aggregate_oi_volume


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


def test_build_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "TS_DIR", tmp_path)        # write into a temp dir
    data = load_dashboard(use_demo=True)               # offline, deterministic

    row = ts.build_row(data)
    assert set(row) == set(ts.FIELDS)                  # keys stay in lock-step
    assert row["spot"] == round(data.levels.spot, 2)
    assert row["source"] == "DEMO-SYNTHETIC"

    path = ts.append_row(row)
    assert path.parent == tmp_path
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
