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


def test_capture_latest_walks_back(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "FUTURES_DIR", tmp_path)

    def fake_fetch(day):                                 # only 07-06 "published"
        return _BHAV if day.strftime("%Y-%m-%d") == "2026-07-06" else None

    got = capture_latest("NIFTY", today_ist="2026-07-08",
                         fetch_fn=fake_fetch, parse_fn=parse_index_futures, max_back=4)
    assert got and got[0] == "2026-07-06"
