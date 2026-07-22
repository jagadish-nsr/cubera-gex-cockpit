"""Unit tests for the big-money filter (app/big_money.py) — pure logic + the store round-trip."""

from types import SimpleNamespace as NS

import app.big_money as bm
from app.big_money import (BigMoneyDetector, Event, _BurstTracker, append_event,
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
    drops below and re-crosses."""
    tr = _BurstTracker(window_s=10.0, floor=1000.0)
    assert tr.update(0, 100, 10.0) == (False, 0.0, 0)      # first packet: no delta
    fired, wnot, wqty = tr.update(1, 200, 10.0)            # +100 units × 10 = 1000
    assert fired is True and wnot == 1000.0 and wqty == 100
    assert tr.update(2, 250, 10.0)[0] is False             # still above floor → no re-fire
    # advance past the window so old deltas expire → back below floor
    assert tr.update(15, 260, 10.0)[0] is False
    assert tr.active is False


def test_burst_tracker_ignores_volume_reset():
    tr = _BurstTracker(window_s=10.0, floor=1.0)
    tr.update(0, 500, 10.0)
    fired, wnot, _ = tr.update(1, 100, 10.0)               # volume < last (day reset) → dvol 0
    assert fired is False and wnot == 0.0


CONTRACTS = {61093: {"symbol": "NIFTY", "security_id": 61093, "lot": 65, "expiry": "2026-07-28"}}


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
    monkeypatch.setattr(bm, "STORE_DIR", tmp_path)
    ev = Event(epoch=1784600000, ts_ist="2026-07-22 13:30:00", symbol="NIFTY", kind="print",
               side="buy", qty=32500, lots=500.0, notional=780000000.0, price=24000.0,
               bid=23999.0, ask=24000.0)
    append_event(ev)
    append_event(Event(epoch=1784600060, ts_ist="2026-07-22 13:31:00", symbol="NIFTY", kind="burst",
                       side=None, qty=100, lots=1.5, notional=30000000.0, price=24010.0))
    rows = load_events("NIFTY", "2026-07-22")
    assert len(rows) == 2
    assert rows[0]["kind"] == "burst"          # newest first
    assert rows[1]["side"] == "buy" and rows[1]["notional"] == 780000000.0
    assert load_events("NIFTY", "2026-07-22", limit=1)[0]["kind"] == "burst"
    assert load_events("BANKNIFTY", "2026-07-22") == []
