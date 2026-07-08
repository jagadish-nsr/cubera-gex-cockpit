"""Hand-checkable tests for the Nifty index-futures OI build-up (sign-free)."""

import app.futures_buildup as fb
from app.futures_buildup import (backfill, buildup_bias, capture_latest,
                                 classify_futures_buildup, futures_series,
                                 load_futures_day, save_futures_day)
from data.nse import parse_index_futures

# Minimal F&O-bhavcopy CSV: 2 NIFTY index futures (near + next), plus a NIFTY option
# and a stock future that must be IGNORED. Only the columns the parser reads matter.
_HEADER = ("TradDt,TckrSymb,FinInstrmTp,XpryDt,ClsPric,PrvsClsgPric,"
           "OpnIntrst,ChngInOpnIntrst,TtlTradgVol,UndrlygPric")
_BHAV = "\n".join([
    _HEADER,
    "2026-07-07,NIFTY,IDF,2026-07-28,24440.30,24482.90,163107,-3192,45637,24398.7",  # near
    "2026-07-07,NIFTY,IDF,2026-08-25,24529.80,24571.00,16040,+2500,4391,24398.7",     # next
    "2026-07-07,NIFTY,IDO,2026-07-28,120.00,150.00,999999,999,888,24398.7",           # option -> ignore
    "2026-07-07,RELIANCE,STF,2026-07-28,3000.00,2990.00,5000,100,200,3000",           # stock -> ignore
])


def test_classify_futures_buildup_matrix():
    assert classify_futures_buildup(+10, +5) == "long build-up"      # price up, OI up
    assert classify_futures_buildup(-10, +5) == "short build-up"     # price down, OI up
    assert classify_futures_buildup(+10, -5) == "short covering"     # price up, OI down
    assert classify_futures_buildup(-10, -5) == "long unwinding"     # price down, OI down
    assert classify_futures_buildup(0, +5) == "flat"                 # price flat
    assert classify_futures_buildup(+10, 0) == "flat"                # OI flat
    assert classify_futures_buildup(None, +5) is None
    assert buildup_bias("long build-up") == "bullish"
    assert buildup_bias("short build-up") == "bearish"
    assert buildup_bias(None) is None


def test_parse_index_futures_near_and_aggregate():
    rec = parse_index_futures(_BHAV, "NIFTY")
    assert rec["n_contracts"] == 2                       # only the 2 IDF rows
    assert rec["expiry"] == "2026-07-28"                 # earliest = near month
    assert rec["close"] == 24440.30 and rec["prev_close"] == 24482.90
    assert abs(rec["dprice"] - (-42.6)) < 1e-6           # near close vs prev close
    assert rec["near_oi"] == 163107 and rec["near_doi"] == -3192
    # aggregate sums across the two IDF contracts (rollover-netted)
    assert rec["agg_oi"] == 163107 + 16040
    assert rec["agg_doi"] == -3192 + 2500                # front bleeds, back builds
    assert rec["agg_vol"] == 45637 + 4391
    assert rec["spot"] == 24398.7
    # near-month reads bearish unwinding; aggregate ΔOI is still net negative here too
    assert classify_futures_buildup(rec["dprice"], rec["near_doi"]) == "long unwinding"


def test_parse_index_futures_absent_symbol():
    assert parse_index_futures(_BHAV, "BANKNIFTY") is None
    assert parse_index_futures("", "NIFTY") is None


def test_save_load_roundtrip_and_series(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "FUTURES_DIR", tmp_path)
    rec = parse_index_futures(_BHAV, "NIFTY")
    save_futures_day("2026-07-07", rec)
    save_futures_day("2026-07-07", rec)                  # idempotent -> single row
    loaded = load_futures_day("2026-07-07", "NIFTY")
    assert loaded["near_doi"] == -3192 and loaded["agg_oi"] == 179147
    assert loaded["n_contracts"] == 2
    ser = futures_series("NIFTY", n=5)
    assert len(ser) == 1 and ser[0]["agg_buildup"] == "long unwinding"
    assert ser[0]["agg_bias"] == "bearish (weaker/late)"


def test_backfill_idempotent_with_injected_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "FUTURES_DIR", tmp_path)
    calls = {"n": 0}

    def fake_fetch(day):
        calls["n"] += 1
        return _BHAV if day.strftime("%Y-%m-%d") == "2026-07-07" else None  # one trading day

    got = backfill("NIFTY", n_days=3, today_ist="2026-07-07",
                   fetch_fn=fake_fetch, parse_fn=parse_index_futures)
    assert got == ["2026-07-07"]
    # re-run: already cached -> no new captures (and the cached day isn't re-fetched)
    got2 = backfill("NIFTY", n_days=3, today_ist="2026-07-07",
                    fetch_fn=fake_fetch, parse_fn=parse_index_futures)
    assert got2 == []


def _live_payload(near_oi=250000, near_vol=2900000, price=24410.0, change=-72.9):
    """Minimal liveEquity-derivatives payload: 3 index futures (unsorted expiries)."""
    return {"data": [
        {"instrumentType": "FUTIDX", "expiryDate": "25-Aug-2026", "lastPrice": 24503.8,
         "change": -67.2, "openInterest": 24000, "volume": 400000, "underlyingValue": 24398.7},
        {"instrumentType": "FUTIDX", "expiryDate": "28-Jul-2026", "lastPrice": price,
         "change": change, "openInterest": near_oi, "volume": near_vol, "underlyingValue": 24398.7},
        {"instrumentType": "FUTIDX", "expiryDate": "29-Sep-2026", "lastPrice": 24676.5,
         "change": -66.5, "openInterest": 3000, "volume": 2000, "underlyingValue": 24398.7},
    ]}


def test_parse_futures_live_near_and_aggregate():
    from data.nse import parse_futures_live
    rec = parse_futures_live(_live_payload(), "NIFTY")
    assert rec["expiry"] == "28-Jul-2026" and rec["n_contracts"] == 3   # near = earliest
    assert rec["price"] == 24410.0 and rec["oi"] == 250000
    assert rec["agg_oi"] == 250000 + 24000 + 3000
    assert rec["agg_vol"] == 2900000 + 400000 + 2000
    assert parse_futures_live({}, "NIFTY") is None


def test_intraday_capture_and_buildup(tmp_path, monkeypatch):
    import pandas as pd
    monkeypatch.setattr(fb, "FUTURES_INTRADAY_DIR", tmp_path)
    from data.nse import parse_futures_live
    # Two snapshots: price rises, aggregate OI rises -> intraday LONG build-up.
    from datetime import datetime, timezone
    t0 = datetime(2026, 7, 8, 5, 0, tzinfo=timezone.utc)    # 10:30 IST
    t1 = datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc)    # 11:30 IST
    fb.capture_futures_snapshot("NIFTY", now_utc=t0, parse_fn=parse_futures_live,
                                fetch_fn=lambda sym: _live_payload(near_oi=250000, price=24400.0))
    fb.capture_futures_snapshot("NIFTY", now_utc=t1, parse_fn=parse_futures_live,
                                fetch_fn=lambda sym: _live_payload(near_oi=270000, price=24460.0))
    df = fb.load_futures_intraday_day("2026-07-08", "NIFTY")
    assert df is not None and len(df) == 2
    intr = fb.intraday_futures_buildup(df)
    assert intr["dprice"] == 60.0 and intr["doi"] == 20000.0
    assert intr["buildup"] == "long build-up" and intr["bias"] == "bullish"
    assert abs(intr["doi_pct"] - 20000.0 / 277000.0 * 100) < 1e-6      # agg OI build since open %
    assert intr["turnover_pct"] is not None                            # vol ÷ OI (conviction)


def test_intraday_futures_steps_trajectory():
    import pandas as pd
    # 3 snapshots: price+OI up (long build-up), then price up + OI down (short covering).
    df = pd.DataFrame({
        "time_ist": ["09:20", "09:25", "09:30"],
        "price": [24400.0, 24420.0, 24440.0],
        "agg_oi": [250000.0, 260000.0, 255000.0],
        "agg_vol": [100000.0, 180000.0, 250000.0],
    })
    steps = fb.intraday_futures_steps(df)
    assert [s["state"] for s in steps] == ["long build-up", "short covering"]
    assert steps[0]["doi"] == 10000.0 and steps[1]["doi"] == -5000.0
    assert steps[0]["dvol"] == 80000.0
    assert abs(steps[0]["doi_pct"] - 10000.0 / 250000.0 * 100) < 1e-6   # per-interval ΔOI%
    assert fb.intraday_futures_steps(df.head(1)) == []      # <2 snapshots -> empty


def test_intraday_min_gap_skips_double_write(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "FUTURES_INTRADAY_DIR", tmp_path)
    from data.nse import parse_futures_live
    from datetime import datetime, timezone
    t = datetime(2026, 7, 8, 5, 0, tzinfo=timezone.utc)
    fb.capture_futures_snapshot("NIFTY", now_utc=t, parse_fn=parse_futures_live,
                                fetch_fn=lambda s: _live_payload())
    # 2 min later with a 10-min gap guard -> skipped (not written).
    from datetime import timedelta
    got = fb.capture_futures_snapshot("NIFTY", now_utc=t + timedelta(minutes=2),
                                      parse_fn=parse_futures_live, min_gap_minutes=10.0,
                                      fetch_fn=lambda s: _live_payload())
    assert got is not None and got[1] is False
    assert len(fb.load_futures_intraday_day("2026-07-08", "NIFTY")) == 1


def test_capture_latest_walks_back(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "FUTURES_DIR", tmp_path)

    def fake_fetch(day):                                 # only 07-06 "published"
        return _BHAV if day.strftime("%Y-%m-%d") == "2026-07-06" else None

    got = capture_latest("NIFTY", today_ist="2026-07-08",
                         fetch_fn=fake_fetch, parse_fn=parse_index_futures, max_back=4)
    assert got and got[0] == "2026-07-06"
