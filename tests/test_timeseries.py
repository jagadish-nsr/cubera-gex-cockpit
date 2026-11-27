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
    # Demo path now creates synthetic per-strike OI, so it returns rows.
    data = load_dashboard(window="all", use_demo=True)
    # Demo data no longer populates oi_strikes (relies on payload), so mock it for the test
    data.oi_strikes = [{"strike": 24000.0, "call_oi": 100.0, "put_oi": 100.0, "call_vol": 0, "put_vol": 0}]
    rows = ts.build_strike_rows(data)
    assert len(rows) > 0
    data.oi_strikes = []
    assert ts.build_strike_rows(data) == []
    assert ts.append_strike_rows([]) is None


def test_build_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "TS_DIR", tmp_path)        # write into a temp dir
    data = load_dashboard(window="all", use_demo=True)               # offline, deterministic

    row = ts.build_row(data)
    assert set(row) == set(ts.FIELDS)                  # keys stay in lock-step
    assert row["spot"] == round(data.levels.spot, 2)
    assert row["source"] == "DEMO-SYNTHETIC"

    path = ts.append_row(row)
    assert path.parent.parent == tmp_path
    assert path.parent.name == data.symbol
    ts.append_row(row)                                  # second point, same day

    df = ts.load_day(row["date_ist"])
    assert len(df) == 2
    assert list(df.columns) == ts.FIELDS
    assert df["date_ist"].iloc[0] == row["date_ist"]
    assert row["date_ist"] in ts.available_days()


def test_append_if_due_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "TS_DIR", tmp_path)
    row = ts.build_row(load_dashboard(window="all", use_demo=True))

    p1, w1 = ts.append_if_due(row, min_gap_minutes=10)
    assert w1 and p1.exists()                       # first row writes

    p2, w2 = ts.append_if_due(row, min_gap_minutes=10)
    assert not w2                                    # same timestamp < 10 min -> skipped
    assert len(ts.load_day(row["date_ist"])) == 1

    _, w3 = ts.append_if_due(row, min_gap_minutes=0)
    assert w3                                        # guard disabled -> writes
    assert len(ts.load_day(row["date_ist"])) == 2


def test_third_identical_spot_is_not_appended(tmp_path, monkeypatch):
    """16 Nov repeated 25,220.05 at 15:16, 15:22 and 15:27. The third print is stuck."""
    monkeypatch.setattr(ts, "TS_DIR", tmp_path)
    row = ts.build_row(load_dashboard(window="all", use_demo=True))
    stamps = (
        "2026-11-16T09:46:00Z",
        "2026-11-16T09:51:00Z",
        "2026-11-16T09:56:00Z",
    )
    written = []
    for stamp in stamps:
        nxt = dict(row)
        nxt["timestamp_utc"] = stamp
        _, ok = ts.append_if_due(nxt, min_gap_minutes=0)
        written.append(ok)
    assert written == [True, True, False]
    assert len(ts.load_day(row["date_ist"])) == 2


def _published_pickle(path, fetched):
    import pickle
    from types import SimpleNamespace
    path.parent.mkdir(parents=True, exist_ok=True)
    bundle = SimpleNamespace(
        symbol="NIFTY",
        source="dhan",
        levels=SimpleNamespace(spot=23222.3),
        fetched_at=fetched,
    )
    with open(path, "wb") as handle:
        pickle.dump({"data": bundle, "fetched_at": fetched}, handle)
    return bundle


def test_published_book_rejects_a_stale_file(tmp_path, monkeypatch):
    from datetime import timedelta
    import scripts.log_timeseries as log_ts
    monkeypatch.setattr(log_ts, "ROOT", tmp_path)
    fetched = datetime.now(timezone.utc) - timedelta(minutes=10)
    _published_pickle(tmp_path / "data" / "journal" / "latest_snap_NIFTY.pkl", fetched)
    assert log_ts.published_book("NIFTY") is None


def test_published_book_returns_a_fresh_file(tmp_path, monkeypatch):
    import scripts.log_timeseries as log_ts
    monkeypatch.setattr(log_ts, "ROOT", tmp_path)
    fetched = datetime.now(timezone.utc)
    bundle = _published_pickle(tmp_path / "data" / "journal" / "latest_snap_NIFTY.pkl", fetched)
    got = log_ts.published_book("NIFTY")
    assert got is not None
    assert got.levels.spot == bundle.levels.spot


def test_empty_chain_records_the_published_book(monkeypatch):
    import scripts.log_timeseries as log_ts
    fetched = datetime.now(timezone.utc)
    book = SimpleNamespace(
        symbol="NIFTY",
        source="nse",
        fetched_at=fetched,
        expiries=[],
    )

    def _empty(**_kwargs):
        raise ValueError("snapshot has no contracts (empty chain)")

    monkeypatch.setattr(log_ts, "load_dashboard", _empty)
    monkeypatch.setattr(log_ts, "published_book", lambda symbol, max_age_s=180: book)
    monkeypatch.setattr(log_ts, "build_row", lambda data: {
        "date_ist": "2026-11-02", "time_ist": "10:18", "spot": 23222.3,
        "regime": "POSITIVE", "mode_label": "CALL-HEAVY",
        "put_wall": 23200, "call_wall": 23300, "magnet": 23300,
        "pcr_oi": 1.1, "news_count": 0, "news_impact": 0,
    })
    monkeypatch.setattr(log_ts, "append_if_due", lambda *a, **k: (SimpleNamespace(name="2026-11-02.csv"), True))
    monkeypatch.setattr(log_ts, "build_strike_rows", lambda data: [])
    monkeypatch.setattr(log_ts, "append_strike_rows", lambda *a, **k: None)
    monkeypatch.setattr(log_ts, "_is_expiry_day", lambda data: False)
    monkeypatch.setattr(
        "app.futures_buildup.capture_futures_from_ws",
        lambda **k: None,
    )
    msg, data = log_ts.capture_once(symbol="NIFTY")
    assert data is book
    assert "[published book]" in msg


def test_capture_prefers_published_book_over_live_fetch(monkeypatch):
    """18-Nov gap: live fetch starved BN/SX — prefer fresh pickle first."""
    import scripts.log_timeseries as log_ts

    fetched = datetime.now(timezone.utc)
    books = {
        "BANKNIFTY": SimpleNamespace(
            symbol="BANKNIFTY", source="dhan", fetched_at=fetched, expiries=[],
            levels=SimpleNamespace(spot=62000.0, near_book_expired=False),
        ),
        "SENSEX": SimpleNamespace(
            symbol="SENSEX", source="dhan", fetched_at=fetched, expiries=[],
            levels=SimpleNamespace(spot=82000.0, near_book_expired=False),
        ),
    }

    def _live(**kwargs):
        raise AssertionError("live fetch must not run when pickle is fresh")

    written: list[str] = []

    def _append(row, min_gap_minutes=10.0, symbol=None):
        written.append(str(symbol))
        return SimpleNamespace(name="2026-11-18.csv"), True

    monkeypatch.setattr(log_ts, "load_dashboard", _live)
    monkeypatch.setattr(
        log_ts, "published_book",
        lambda symbol, max_age_s=180: books.get(str(symbol or "").upper()),
    )
    monkeypatch.setattr(log_ts, "build_row", lambda data: {
        "date_ist": "2026-11-18", "time_ist": "10:18", "spot": float(data.levels.spot),
        "regime": "POSITIVE", "mode_label": "CALL-HEAVY",
        "put_wall": 61000, "call_wall": 62000, "magnet": 61500,
        "pcr_oi": 1.0, "news_count": 0, "news_impact": 0,
    })
    monkeypatch.setattr(log_ts, "append_if_due", _append)
    monkeypatch.setattr(log_ts, "build_strike_rows", lambda data: [])
    monkeypatch.setattr(log_ts, "append_strike_rows", lambda *a, **k: None)
    monkeypatch.setattr(log_ts, "_is_expiry_day", lambda data: False)
    monkeypatch.setattr("app.futures_buildup.capture_futures_from_ws", lambda **k: None)

    for sym in ("BANKNIFTY", "SENSEX"):
        msg, data = log_ts.capture_once(symbol=sym)
        assert data.symbol == sym
        assert "[published book]" in msg
    assert written == ["BANKNIFTY", "SENSEX"]


# ---- Quote audit: bid/ask/IV/expiry and mid-vs-last source per strike ----------------

def test_nearest_expiry_quotes_marks_mid_and_last(tmp_path):
    from datetime import date
    from data.nse import nearest_expiry_quotes
    rows = [
        # both sides quoted -> mid
        {"expiry": date(2026, 7, 2), "strike": 100.0, "type": "C", "price": 5.0,
         "bid": 4.8, "ask": 5.2, "iv": 0.14},
        # put has only a last trade (no bid) -> last fallback
        {"expiry": date(2026, 7, 2), "strike": 100.0, "type": "P", "price": 4.0,
         "bid": None, "ask": 4.4, "iv": 0.15},
        # later expiry is ignored
        {"expiry": date(2026, 7, 9), "strike": 100.0, "type": "C", "price": 9.0,
         "bid": 8.0, "ask": 10.0, "iv": 0.2},
    ]
    q = nearest_expiry_quotes(rows)
    assert q[100.0] == {
        "expiry": "2026-07-02",
        "call_bid": 4.8, "call_ask": 5.2, "call_iv": 0.14, "call_src": "mid",
        "put_bid": None, "put_ask": 4.4, "put_iv": 0.15, "put_src": "last",
    }
    assert nearest_expiry_quotes([]) == {}


def test_parse_keeps_bid_and_ask_on_each_side():
    from data.nse import parse_nse_option_chain
    payload = {"records": {"underlyingValue": 100.0, "data": [{
        "strikePrice": 100.0, "expiryDate": "02-Jul-2026",
        "CE": {"strikePrice": 100.0, "expiryDate": "02-Jul-2026", "lastPrice": 5.0,
               "buyPrice1": 4.8, "sellPrice1": 5.2, "impliedVolatility": 14.0},
        "PE": {"strikePrice": 100.0, "expiryDate": "02-Jul-2026", "lastPrice": 4.0,
               "buyPrice1": 0, "sellPrice1": 4.4, "impliedVolatility": 15.0},
    }]}}
    rows, _ = parse_nse_option_chain(payload)
    ce = next(r for r in rows if r["type"] == "C")
    pe = next(r for r in rows if r["type"] == "P")
    assert (ce["bid"], ce["ask"], ce["price"]) == (4.8, 5.2, 5.0)
    assert pe["bid"] is None and pe["ask"] == 4.4        # zero bid is "not quoted", not 0
    assert pe["price"] == 4.0                            # last-trade fallback (no bid)


def test_strike_rows_roundtrip_quote_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "OISTRIKES_DIR", tmp_path)
    data = SimpleNamespace(
        fetched_at=datetime(2026, 6, 30, 5, 0, tzinfo=timezone.utc),
        levels=SimpleNamespace(spot=24010.7),
        oi_strikes=[
            {"strike": 24000.0, "call_oi": 1, "put_oi": 3, "call_ltp": 120.5,
             "put_ltp": 110.25, "net_gex": 999, "expiry": "2026-07-02",
             "call_bid": 119.0, "call_ask": 122.0, "call_iv": 0.1412345, "call_src": "mid",
             "put_bid": None, "put_ask": 111.0, "put_iv": 0.15, "put_src": "last"},
        ],
    )
    rows = ts.build_strike_rows(data)
    assert set(rows[0]) == set(ts.STRIKE_FIELDS)
    ts.append_strike_rows(rows)
    df = ts.load_strikes_day("2026-06-30")
    assert list(df.columns) == ts.STRIKE_FIELDS
    r = df.iloc[0]
    assert r["expiry"] == "2026-07-02"
    assert (r["call_bid"], r["call_ask"], r["call_src"]) == (119.0, 122.0, "mid")
    assert round(float(r["call_iv"]), 4) == 0.1412
    assert math.isnan(float(r["put_bid"])) and r["put_src"] == "last"
    assert r["call_ltp"] == 120.5 and r["net_gex"] == 999   # old columns unchanged


def test_append_to_old_header_file_does_not_shift_columns(tmp_path, monkeypatch):
    import csv
    monkeypatch.setattr(ts, "OISTRIKES_DIR", tmp_path)
    old_header = ts.STRIKE_FIELDS[:ts.STRIKE_FIELDS.index("expiry")]   # pre-change schema
    path = ts.strikes_path_for("2026-06-30", None, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=old_header)
        w.writeheader()
        w.writerow({k: "" for k in old_header} | {"date_ist": "2026-06-30", "strike": 1.0})
    data = SimpleNamespace(
        fetched_at=datetime(2026, 6, 30, 5, 0, tzinfo=timezone.utc),
        levels=SimpleNamespace(spot=24010.7),
        oi_strikes=[{"strike": 24000.0, "call_ltp": 120.5, "put_ltp": 110.25,
                     "expiry": "2026-07-02", "call_bid": 119.0}],
    )
    ts.append_strike_rows(ts.build_strike_rows(data))
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == old_header
    assert all(len(r) == len(old_header) for r in rows)        # no shifted cells
    assert rows[2][old_header.index("call_ltp")] == "120.5"


def test_degraded_chain_marks_the_row_stale():
    """25/26-Nov: chains served degraded all day while the stale column stayed False."""
    data = load_dashboard(window="all", use_demo=True)
    assert ts.build_row(data)["stale"] is False
    data.is_degraded = True
    assert ts.build_row(data)["stale"] is True


def test_frozen_spot_with_moving_atm_premium_marks_the_row_stale(monkeypatch):
    import scripts.log_timeseries as log_ts

    fetched = datetime.now(timezone.utc)
    book = SimpleNamespace(symbol="NIFTY", source="dhan", fetched_at=fetched, expiries=[],
                           levels=SimpleNamespace(spot=23731.8, near_book_expired=False))
    rows_written = []
    monkeypatch.setattr(log_ts, "published_book", lambda symbol, max_age_s=180: book)
    monkeypatch.setattr(log_ts, "build_row", lambda data: {
        "date_ist": "2026-11-03", "time_ist": "10:20", "spot": 23731.8, "stale": False,
        "regime": "POSITIVE", "mode_label": "CALL-HEAVY", "put_wall": 23600, "call_wall": 23800,
        "magnet": 23700, "pcr_oi": 1.0, "news_count": 0, "news_impact": 0,
    })
    # 3 Nov: spot sat on 23,731.8 while the 23,700 call went from 49 to 55.
    monkeypatch.setattr(log_ts, "build_strike_rows", lambda data: [
        {"spot": 23731.8, "strike": 23700.0, "call_ltp": 55.0, "put_ltp": 20.0}])
    monkeypatch.setattr("app.timeseries.last_strike_snapshot", lambda path: [
        {"spot": 23731.8, "strike": 23700.0, "call_ltp": 49.0, "put_ltp": 22.0}])
    monkeypatch.setattr(log_ts, "append_if_due",
                        lambda row, **k: (rows_written.append(row), (SimpleNamespace(name="x.csv"), True))[1])
    monkeypatch.setattr(log_ts, "append_strike_rows", lambda *a, **k: None)
    monkeypatch.setattr(log_ts, "_is_expiry_day", lambda data: False)
    monkeypatch.setattr("app.futures_buildup.capture_futures_from_ws", lambda **k: None)
    log_ts.capture_once(symbol="NIFTY")
    assert rows_written[0]["stale"] is True
