"""Unit tests for the big-money filter (app/big_money.py) — pure logic + the store round-trip."""

from types import SimpleNamespace as NS

import app.big_money as bm
from app.big_money import (BigMoneyDetector, Event, _SymbolBurstTracker, append_event, buildup,
                           classify_aggressor, load_events, lots, notional)


def test_notional_and_lots():
    assert notional(23969.5, 65) == 23969.5 * 65          # qty already shares-equiv, no ×lot
    assert notional(None, 65) == 0.0 and notional(10, None) == 0.0
    assert lots(650, 65) == 10.0
    assert lots(65, None) is None


def test_classify_aggressor():
    assert classify_aggressor(101, 99, 101) == "buy"       # at/above ask
    assert classify_aggressor(102, 99, 101) == "buy"
    assert classify_aggressor(99, 99, 101) == "sell"       # at/below bid
    assert classify_aggressor(100, 99, 101) is None        # inside the spread
    assert classify_aggressor(100, None, 101) is None       # no quote (Ticker/Quote tier)


def test_burst_tracker_rising_edge():
    """Fires ONCE when trailing-window traded notional crosses the floor, then not again until it
    drops below and re-crosses. First arg is the contract security_id (per-contract volume delta)."""
    tr = _SymbolBurstTracker(window_s=10.0, floor=1000.0)
    assert tr.update("A", 0, 100, 10.0)[:3] == (False, 0.0, 0)   # first packet for A: no delta
    fired, wnot, wqty, *_ = tr.update("A", 1, 200, 10.0)         # +100 units × 10 = 1000
    assert fired is True and wnot == 1000.0 and wqty == 100
    assert tr.update("A", 2, 250, 10.0)[0] is False              # still above floor → no re-fire
    assert tr.update("A", 15, 260, 10.0)[0] is False             # window expired → below floor
    assert tr.active is False


def test_burst_tracker_aggregates_two_contracts():
    """Near + next month deltas sum into ONE symbol window (the roll aggregation)."""
    tr = _SymbolBurstTracker(window_s=10.0, floor=1000.0)
    tr.update("near", 0, 100, 10.0)                          # prime both contracts
    tr.update("next", 0, 100, 10.0)
    assert tr.update("near", 1, 150, 10.0)[0] is False       # +50×10 = 500 (below 1000)
    fired, wnot, *_ = tr.update("next", 1, 150, 10.0)        # +50×10 = 500 → window now 1000
    assert fired is True and wnot == 1000.0                  # aggregated across the two contracts


def test_burst_tracker_ignores_volume_reset():
    tr = _SymbolBurstTracker(window_s=10.0, floor=1.0)
    tr.update("A", 0, 500, 10.0)
    fired, wnot, *_ = tr.update("A", 1, 100, 10.0)           # volume < last (day reset) → dvol 0
    assert fired is False and wnot == 0.0


def test_burst_tracker_oi_delta():
    """total_oi sums the latest OI across contracts; doi = ΔOI over the (longer) positioning window;
    conv = ΔOI / Δvolume over that window."""
    tr = _SymbolBurstTracker(window_s=10.0, floor=1e12, pos_window_s=180.0)   # never fires — OI only
    _, _, _, oi0, doi0, conv0, dpx0 = tr.update("near", 0, 100, 10.0, oi=1_000_000)
    assert oi0 == 1_000_000 and doi0 is None and conv0 is None      # one sample → no delta yet
    assert dpx0 is None                                             # one price sample → no move yet
    _, _, _, oi1, doi1, conv1, dpx1 = tr.update("near", 60, 200, 11.5, oi=1_500_000)
    assert oi1 == 1_500_000 and doi1 == 500_000                     # +500k OI over the window
    assert conv1 == 500_000 / 100                                    # ΔOI / Δvolume (200−100)
    assert dpx1 == 1.5                                              # price move over the window (11.5−10.0)
    _, _, _, oi2, _, _, _ = tr.update("next", 60, 50, 10.0, oi=300_000)
    assert oi2 == 1_800_000                                          # near 1.5M + next 0.3M
    # an update far beyond the positioning window drops the old samples → no 2-sample delta
    _, _, _, _, doi3, _, dpx3 = tr.update("near", 400, 260, 10.0, oi=1_600_000)
    assert doi3 is None and dpx3 is None                            # only the epoch-400 sample remains


def test_buildup_quadrants():
    assert buildup(1, 100) == "long build-up"                # buy/up + OI up
    assert buildup(1, -100) == "short covering"              # buy/up + OI down
    assert buildup(-1, 100) == "short build-up"              # sell/down + OI up
    assert buildup(-1, -100) == "long unwinding"
    assert buildup(0, 100) is None                           # no direction
    assert buildup(1, None) is None                          # no ΔOI
    assert buildup(1, 0) is None                             # flat OI = churn


def test_bm_quad_dot_colour():
    """The burst-scatter dot colour maps ΔOI sign (via conviction) × price move to the four quadrants,
    greying out churn and missing/flat inputs. Mirrors buildup()."""
    from app.simple_dashboard import _bm_quad
    assert _bm_quad(+0.5, +8) == ("long build-up", "var(--go)")    # OI building, price up
    assert _bm_quad(+0.5, -8) == ("short build-up", "var(--stop)")  # OI building, price down (writing)
    assert _bm_quad(-0.5, +8) == ("short covering", "var(--call)")  # OI falling, price up
    assert _bm_quad(-0.5, -8) == ("long unwinding", "var(--warn)")  # OI falling, price down
    assert _bm_quad(+0.05, +8) == (None, "var(--ink-mute)")         # |conv|<0.15 → churn (grey)
    assert _bm_quad(+0.5, 0) == (None, "var(--ink-mute)")           # flat price → grey
    assert _bm_quad(None, +8) == (None, "var(--ink-mute)")          # no conviction → grey
    assert _bm_quad(+0.5, None) == (None, "var(--ink-mute)")        # no price move → grey


def test_bm_extra_charts_render():
    """The three descriptive tape charts render valid SVG from an analyse-shaped dict, flag the POC,
    colour OI/CVD by direction, and no-op on empty input."""
    from app.simple_dashboard import (_bm_value_profile_html, _bm_oi_trajectory_html, _bm_cvd_html)
    a = {"symbol": "NIFTY", "vp_fut_ref": 23790.0,               # the profile's own contract ref px
         "value_profile": [{"price": 23800.0, "buy": 24.0, "sell": 25.0, "mid": 29.0, "total": 78.0},
                           {"price": 23750.0, "buy": 5.0, "sell": 2.0, "mid": 1.0, "total": 8.0}],
         "time": [{"t": "09:15", "oi": 1.0e7, "net": +3.0, "px": 23770.0},
                  {"t": "09:30", "oi": 1.2e7, "net": -1.0, "px": 23760.0},
                  {"t": "09:45", "oi": 1.3e7, "net": +2.0, "px": 23790.0}]}
    vp = _bm_value_profile_html(a)
    assert "<svg" in vp and "POC" in vp and "23,800" in vp        # heaviest level flagged
    assert "basis" not in vp                                      # no spot column without a spot
    vps = _bm_value_profile_html(a, spot=23740.0)                # basis = vp_fut_ref 23790 − 23740 = +50
    assert "basis +50" in vps and "23,750s" in vps               # POC spot-equiv 23800 − 50 = 23750
    oi = _bm_oi_trajectory_html(a)
    assert "<svg" in oi and "var(--go)" in oi and "+3.00M" in oi  # OI rose 10.0M→13.0M
    cvd = _bm_cvd_html(a)
    assert "<svg" in cvd and "+4.0cr (BUY)" in cvd                # net buy-initiated 3−1+2
    for fn in (_bm_value_profile_html, _bm_oi_trajectory_html, _bm_cvd_html):
        assert fn({}) == "" and fn(None) == ""                    # empty-input guard


def test_conviction():
    from app.big_money import conviction
    assert conviction(500, 1000) == 0.5                      # half the volume was net new OI
    assert conviction(-800, 1000) == -0.8                    # unwinding
    assert conviction(10, 1000) == 0.01                      # ~churn
    assert conviction(500, 0) is None and conviction(None, 1000) is None


CONTRACTS = {61093: {"symbol": "NIFTY", "roll": "near", "security_id": 61093, "lot": 65,
                     "expiry": "2026-07-28"}}


def test_detector_emits_big_print():
    det = BigMoneyDetector(CONTRACTS, print_floor={"NIFTY": 1_00_00_000.0})
    # 500 lots × 65 × 24000 ≈ ₹78cr — a big print, at the ask ⇒ buy-initiated
    tick = NS(security_id=61093, ltp=24000.0, ltq=500 * 65, ltt=1784600000,
              volume=None, bid=23999.0, ask=24000.0)
    evs = det.on_tick(tick)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.kind == "print" and ev.symbol == "NIFTY" and ev.side == "buy"
    assert ev.lots == 500.0 and ev.notional == 24000.0 * 500 * 65
    assert ev.expiry == "2026-07-28"                       # tagged with the contract


def test_detector_print_carries_oi():
    """A print carries the symbol total OI + the window ΔOI (new positioning vs churn)."""
    det = BigMoneyDetector(CONTRACTS, print_floor={"NIFTY": 1_00_00_000.0})
    det.on_tick(NS(security_id=61093, ltp=24000.0, ltq=None, ltt=1, volume=1000,
                   bid=None, ask=None, oi=5_000_000))          # prime OI
    ev = det.on_tick(NS(security_id=61093, ltp=24000.0, ltq=500 * 65, ltt=2, volume=1100,
                        bid=24000.0, ask=24000.0, oi=5_100_000))[0]
    assert ev.oi == 5_100_000 and ev.doi == 100_000           # OI level + window ΔOI (+100k = build)


def test_detector_ignores_small_print():
    det = BigMoneyDetector(CONTRACTS, print_floor={"NIFTY": 1_00_00_000.0})
    tick = NS(security_id=61093, ltp=24000.0, ltq=65, ltt=1784600000,  # 1 lot ≈ ₹15.6L < floor
              volume=None, bid=None, ask=None)
    assert det.on_tick(tick) == []


def test_detector_unknown_instrument():
    det = BigMoneyDetector(CONTRACTS)
    assert det.on_tick(NS(security_id=999, ltp=100.0, ltq=100, ltt=1, volume=None,
                          bid=None, ask=None)) == []


def test_store_round_trip(tmp_path, monkeypatch):
    import app.big_money_db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "bm.db")
    ev = Event(epoch=1784600000, ts_ist="2026-07-22 13:30:00", symbol="NIFTY", kind="print",
               side="buy", qty=32500, lots=500.0, notional=780000000.0, price=24000.0,
               bid=23999.0, ask=24000.0)
    append_event(ev)
    append_event(Event(epoch=1784600060, ts_ist="2026-07-22 13:31:00", symbol="NIFTY", kind="burst",
                       side=None, qty=100, lots=1.5, notional=30000000.0, price=24010.0))
    append_event(ev)                           # exact duplicate → deduped by the UNIQUE key
    rows = load_events("NIFTY", "2026-07-22")
    assert len(rows) == 2                       # not 3
    assert rows[0]["kind"] == "burst"          # newest first
    assert rows[1]["side"] == "buy" and rows[1]["notional"] == 780000000.0
    assert load_events("NIFTY", "2026-07-22", limit=1)[0]["kind"] == "burst"
    assert load_events("BANKNIFTY", "2026-07-22") == []


def test_compact_raw_day(tmp_path, monkeypatch):
    import app.big_money_live as bml
    import pandas as pd
    monkeypatch.setattr(bml, "RAW_DIR", tmp_path)
    d = tmp_path / "NIFTY"
    d.mkdir(parents=True)
    pd.DataFrame({"epoch": [1, 2, 2], "code": [8, 8, 8], "symbol": ["NIFTY"] * 3,
                 "ltp": [1.0, 2.0, 2.0]}).to_csv(d / "2026-07-20.csv", index=False)
    n = bml.compact_raw_day("NIFTY", "2026-07-20")
    assert n == 2                                              # deduped on epoch/code/symbol
    assert (d / "2026-07-20.parquet").exists() and not (d / "2026-07-20.csv").exists()
    got = bml.load_raw_ticks("NIFTY", "2026-07-20")            # reader prefers Parquet
    assert len(got) == 2
    # a re-created CSV merges into the existing Parquet, no earlier rows lost
    pd.DataFrame({"epoch": [3], "code": [8], "symbol": ["NIFTY"], "ltp": [3.0]}).to_csv(
        d / "2026-07-20.csv", index=False)
    assert bml.compact_raw_day("NIFTY", "2026-07-20") == 3


def test_daily_rollup(tmp_path, monkeypatch):
    import app.big_money_db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "bm.db")
    append_event(Event(epoch=1784600000, ts_ist="2026-07-22 13:30:00", symbol="NIFTY", kind="print",
                       side="buy", qty=32500, lots=500.0, notional=8e7, price=24000.0))
    append_event(Event(epoch=1784600060, ts_ist="2026-07-22 13:31:00", symbol="NIFTY", kind="print",
                       side="sell", qty=13000, lots=200.0, notional=3e7, price=23990.0))
    append_event(Event(epoch=1784600120, ts_ist="2026-07-22 13:32:00", symbol="NIFTY", kind="burst",
                       side=None, qty=6500, lots=100.0, notional=1.5e7, price=23995.0))
    roll = db.daily_rollup(days=["2026-07-22"], symbols=["NIFTY"])
    assert len(roll) == 1
    r = roll[0]
    assert r["n"] == 3 and r["n_print"] == 2 and r["n_burst"] == 1
    assert r["buy_notional"] == 8e7 and r["sell_notional"] == 3e7
    assert r["net_notional"] == 5e7 and r["lean"] == "BUY"
    assert abs(r["net_pct"] - (5e7 / 11e7 * 100)) < 1e-6
    assert r["biggest"] == 8e7


# --- near + next month aggregation (Option A) --------------------------------------------------

TWO_CONTRACTS = {
    61093: {"symbol": "NIFTY", "roll": "near", "security_id": 61093, "lot": 65, "expiry": "2026-07-28"},
    61200: {"symbol": "NIFTY", "roll": "next", "security_id": 61200, "lot": 65, "expiry": "2026-08-25"},
}


def test_prints_tagged_per_contract_and_dedupe_by_expiry(tmp_path, monkeypatch):
    import app.big_money_db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "bm.db")
    det = BigMoneyDetector(TWO_CONTRACTS, print_floor={"NIFTY": 1_00_00_000.0})
    near = NS(security_id=61093, ltp=24000.0, ltq=500 * 65, ltt=1784600000, volume=None,
              bid=None, ask=None)
    nxt = NS(security_id=61200, ltp=24080.0, ltq=500 * 65, ltt=1784600000, volume=None,
             bid=None, ask=None)                            # same epoch, DIFFERENT contract
    evs = []
    for t in (near, nxt):
        for ev in det.on_tick(t):
            append_event(ev)
            evs.append(ev)
    day = evs[0].ts_ist[:10]                                 # the day the detector stamped
    rows = load_events("NIFTY", day)
    assert len(rows) == 2                                    # both kept — expiry keeps them distinct
    assert {r["expiry"] for r in rows} == {"2026-07-28", "2026-08-25"}
    # daily_rollup folds both contracts into ONE symbol row
    roll = db.daily_rollup(days=[day], symbols=["NIFTY"])
    assert len(roll) == 1 and roll[0]["n"] == 2


def test_burst_tagged_agg():
    det = BigMoneyDetector(TWO_CONTRACTS, burst_floor={"NIFTY": 1000.0}, burst_window_s=10.0)
    det.on_tick(NS(security_id=61093, ltp=10.0, ltq=None, ltt=1, volume=100, bid=None, ask=None))
    evs = det.on_tick(NS(security_id=61093, ltp=10.0, ltq=None, ltt=2, volume=300, bid=None, ask=None))
    burst = [e for e in evs if e.kind == "burst"]
    assert burst and burst[0].expiry == "AGG"               # symbol-aggregate burst


def test_nearest_futures_two_sorted():
    import pandas as pd
    from datetime import date, timedelta
    from data.dhan import nearest_futures
    # Date-relative so the test doesn't rot when an expiry passes: near = +7d, mid = +35d, far = +65d,
    # plus one already-EXPIRED future (−5d) that must be filtered out. (nearest_futures keeps exp >= today.)
    near = (date.today() + timedelta(days=7)).isoformat()
    mid = (date.today() + timedelta(days=35)).isoformat()
    far = (date.today() + timedelta(days=65)).isoformat()
    past = (date.today() - timedelta(days=5)).isoformat()
    master = pd.DataFrame({
        "SEM_INSTRUMENT_NAME": ["FUTIDX", "FUTIDX", "FUTIDX", "FUTIDX", "OPTIDX"],
        "SEM_TRADING_SYMBOL": ["NIFTY-mid-FUT", "NIFTY-near-FUT", "NIFTY-far-FUT", "NIFTY-past-FUT",
                               "NIFTY-near-24000-CE"],
        "SEM_EXPIRY_DATE": [mid, near, far, past, near],
        "SEM_SMST_SECURITY_ID": [61200, 61093, 61300, 60000, 99999],
        "SEM_LOT_UNITS": [65, 65, 65, 65, 65],
    })
    fs = nearest_futures("NIFTY", n=2, master=master)
    assert [f["expiry"] for f in fs] == [near, mid]        # sorted, front two, no options, no expired
    assert fs[0]["security_id"] == 61093


def test_futures_state_and_rollover(tmp_path, monkeypatch):
    import app.big_money_db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "bm.db")
    db.upsert_futures_state("NIFTY", "near", "2026-07-28", oi=1_000_000, price=24000.0, ts=1)
    db.upsert_futures_state("NIFTY", "next", "2026-08-25", oi=3_000_000, price=24080.0, ts=1)
    db.upsert_futures_state("NIFTY", "near", "2026-07-28", oi=1_000_000, price=24010.0, ts=2)  # upsert
    roll = db.rollover(symbols=["NIFTY"])
    assert len(roll) == 1
    r = roll[0]
    assert r["pct_rolled"] == 75.0                          # 3M / (1M + 3M)
    assert r["near"]["price"] == 24010.0 and r["next"]["expiry"] == "2026-08-25"
