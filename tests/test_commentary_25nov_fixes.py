"""Regression tests for the 25-Nov-2026 session-commentary audit fixes.

Each test pins one wrong card from docs/session-commentary-audit-2026-11-25.md:
  * prior-session bars must never feed today's opening range, VWAP or impulse;
  * ORB is published only after its 15th bar closes (09:30), from 09:15 bars;
  * VWAP stretch, VWAP cross and ORB breaks use one basis (futures close vs futures);
  * gap uses the 09:15 cash print and is labelled late after 09:30;
  * pre-open breaks do not fire on the carried-over prior close;
  * wall migration cards chain (from = the wall the reader last saw);
  * PCR surge headline renders one sign;
  * wall-chase body: one kill (ledger), distinct walls, sentence-cut drivers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import commentary.detectors.gap as gap_mod
from commentary.detectors.gap import OpeningGapDetector
from commentary.detectors.session_structure import SessionStructureDetector
from commentary.detectors.vwap_dynamics import VwapDynamicsDetector
from commentary.detectors.walls import WallGexShiftDetector
from commentary.detectors.pcr_flow import PcrFlowDetector
from commentary.models import Event, PointInTimeView, StoryState
from commentary.narrator.templates import render_event_template as render_event
from commentary.session_bars import futures_basis_price, session_bars

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2026-11-25"
STATE = StoryState(phase="opening_15m")


def _bar(hhmm, o, h, l, c, day=DAY, vwap=None, volume=1000.0):
    bar = {"start_ts": f"{day}T{hhmm}:00+05:30", "open": o, "high": h, "low": l, "close": c, "volume": volume}
    if vwap is not None:
        bar["vwap"] = vwap
    return bar


def _view(as_of, spot, candles, prior_close=82436.0, extra=None, **kw):
    return PointInTimeView(
        as_of=as_of,
        symbol=kw.pop("symbol", "SENSEX"),
        spot=spot,
        prior_close=prior_close,
        atm_iv=0.14,
        expected_move_1d=kw.pop("expected_move_1d", 120.0),
        call_wall=kw.pop("call_wall", 84000.0),
        put_wall=kw.pop("put_wall", 83000.0),
        gamma_flip=kw.pop("gamma_flip", 83500.0),
        max_pain=kw.pop("max_pain", 83500.0),
        net_gex_sign=1,
        candles_1m=candles,
        extra_facts=extra or {},
    )


# --- session_bars ---------------------------------------------------------------

def test_prior_session_bars_are_not_session_bars():
    """25-Nov 09:15: the 135.3-pt Sensex range was built from 20-Nov 14:38-14:52 bars."""
    prior = [_bar(f"14:{38 + i:02d}", 83900, 83950, 83850, 83900, day="2026-11-20") for i in range(15)]
    now = datetime(2026, 11, 25, 9, 15, 20, tzinfo=IST)
    assert session_bars(prior, now) == []


def test_session_bars_keep_only_today_from_0915_up_to_as_of():
    bars = [
        _bar("09:14", 1, 1, 1, 1),  # pre-open: not a session bar
        _bar("09:15", 2, 2, 2, 2),
        _bar("09:29", 3, 3, 3, 3),
        _bar("09:31", 4, 4, 4, 4),  # after as_of
    ]
    got = session_bars(bars, datetime(2026, 11, 25, 9, 30, 5, tzinfo=IST))
    assert [b["open"] for b in got] == [2, 3]


def test_futures_basis_price_uses_last_session_close_and_falls_back_to_spot():
    view = _view(datetime(2026, 11, 25, 10, 0, tzinfo=IST), 83808.4, [_bar("09:59", 1, 1, 1, 83932.6)])
    assert futures_basis_price(view) == 83932.6
    empty = _view(datetime(2026, 11, 25, 10, 0, tzinfo=IST), 83808.4, [])
    assert futures_basis_price(empty) == 83808.4


# --- opening range -------------------------------------------------------------

def test_orb_waits_for_the_window_then_uses_futures_bars():
    det = SessionStructureDetector()
    bars = [_bar(f"09:{15 + i:02d}", 83700, 83800, 83650, 83750) for i in range(15)]
    bars[3] = _bar("09:18", 83700, 83947.35, 83650, 83750)  # the high of the window
    bars[7] = _bar("09:22", 83700, 83800, 83400.0, 83750)  # the low of the window
    # 09:29 bar is the 15th; before 09:30 nothing is final.
    early = _view(datetime(2026, 11, 25, 9, 29, 50, tzinfo=IST), 83750, bars[:14])
    assert not any(e.type == "ORB_ESTABLISHED" for e in det.detect(early, STATE))
    late = _view(datetime(2026, 11, 25, 9, 30, 5, tzinfo=IST), 83750, bars)
    evts = [e for e in det.detect(late, STATE) if e.type == "ORB_ESTABLISHED"]
    assert len(evts) == 1
    assert evts[0].facts["orb_high"] == pytest.approx(83947.35, abs=0.1)
    assert evts[0].facts["orb_low"] == 83400.0
    assert evts[0].facts["window"] == "09:15-09:29"
    assert evts[0].facts["basis"] == "FUT"


def test_orb_breakout_uses_futures_close_not_index_spot():
    det = SessionStructureDetector()
    bars = [_bar(f"09:{15 + i:02d}", 25700, 25800, 25650, 25750) for i in range(15)]
    bars[0] = _bar("09:15", 25700, 25893.8, 25650, 25750)
    t = datetime(2026, 11, 25, 9, 30, 5, tzinfo=IST)
    det.detect(_view(t, 25790.2, bars, symbol="NIFTY"), STATE)  # establish the range
    # Index spot 25,900 is above the 25,893.8 futures high, but the futures close (25,745) is not.
    after = _view(t + timedelta(minutes=3), 25900.0, bars + [_bar("09:30", 25750, 25760, 25740, 25745)], symbol="NIFTY")
    assert not any(e.type == "ORB_BREAK_UP" for e in det.detect(after, STATE))


# --- VWAP on one basis -----------------------------------------------------------

def test_vwap_stretch_uses_futures_close_against_futures_vwap_and_sign_is_real():
    """25-Nov 09:31 BANKNIFTY: index 63,876 minus futures VWAP 63,970 read 'Oversold'.
    On futures the close 64,220.8 is +251 over VWAP: Overbought."""
    bars = [_bar("09:30", 64000, 64250, 63990, 64220.8, vwap=63969.8)]
    view = _view(datetime(2026, 11, 25, 9, 31, 25, tzinfo=IST), 63876.3, bars, symbol="BANKNIFTY",
                 extra={"session_vwap": 63969.8}, expected_move_1d=300.0, prior_close=63000.0)
    evts = VwapDynamicsDetector().detect(view, STATE)
    stretch = [e for e in evts if e.type == "VWAP_BAND_EXTENSION"]
    assert len(stretch) == 1
    assert stretch[0].facts["direction"] == "OVERBOUGHT"
    assert stretch[0].facts["extension_pts"] == pytest.approx(251.0, abs=0.1)
    assert stretch[0].facts["basis"] == "FUT"


def test_inside_35_pt_minimum_is_not_a_stretch():
    """25-Nov 10:10 NIFTY: futures +1.2 vs VWAP was printed as a -113.5 stretch."""
    bars = [_bar("10:09", 25840, 25860, 25835, 25851.2, vwap=25850.0)]
    view = _view(datetime(2026, 11, 25, 10, 10, 20, tzinfo=IST), 25736.5, bars, symbol="NIFTY",
                 extra={"session_vwap": 25850.0}, expected_move_1d=200.0, prior_close=25349.65)
    assert not any(e.type == "VWAP_BAND_EXTENSION" for e in VwapDynamicsDetector().detect(view, STATE))


def test_vwap_loss_not_fired_when_futures_sit_above_vwap():
    """25-Nov 09:59 SENSEX: 'Session VWAP lost' with futures 83,932.6 above VWAP 83,840."""
    det = SessionStructureDetector()
    t = datetime(2026, 11, 25, 9, 58, 30, tzinfo=IST)
    below = [_bar("09:58", 83830, 83840, 83820, 83835.0)]
    det.detect(_view(t, 83820.0, below, extra={"session_vwap": 83840.0}), STATE)
    above = below + [_bar("09:59", 83835, 83940, 83830, 83932.6)]
    evts = det.detect(_view(t + timedelta(seconds=50), 83838.2, above, extra={"session_vwap": 83840.0}), STATE)
    assert not any(e.type == "VWAP_LOSS" for e in evts)


# --- gap -----------------------------------------------------------------------

def test_gap_is_not_taken_from_the_first_live_spot(monkeypatch):
    """25-Nov 09:15:20 Sensex: spot 83,808 at first poll; the 09:15 cash print was 83,744.15."""
    monkeypatch.setattr(gap_mod, "_journal_cash_open", lambda symbol, day: 0.0)
    det = OpeningGapDetector()
    view = _view(datetime(2026, 11, 25, 9, 15, 20, tzinfo=IST), 83808.4, [], prior_close=82435.99)
    assert det.detect(view, STATE) == []


def test_gap_uses_the_cash_print_and_marks_late_publication(monkeypatch):
    monkeypatch.setattr(gap_mod, "_journal_cash_open", lambda symbol, day: 83744.15)
    det = OpeningGapDetector()
    view = _view(datetime(2026, 11, 25, 11, 43, 50, tzinfo=IST), 83808.4, [], prior_close=82435.99)
    evts = det.detect(view, STATE)
    assert len(evts) == 1
    assert evts[0].facts["open"] == pytest.approx(83744.15, abs=0.1)
    assert evts[0].facts["gap_pts"] == pytest.approx(1308.2, abs=0.1)
    assert evts[0].facts["late_publish"] is True
    _, body = render_event(evts[0])
    assert "Published late" in body


def test_gap_on_time_has_no_late_note(monkeypatch):
    monkeypatch.setattr(gap_mod, "_journal_cash_open", lambda symbol, day: 83744.15)
    det = OpeningGapDetector()
    view = _view(datetime(2026, 11, 25, 9, 16, 10, tzinfo=IST), 83808.4, [], prior_close=82435.99)
    evts = det.detect(view, STATE)
    assert evts and evts[0].facts["late_publish"] is False
    _, body = render_event(evts[0])
    assert "Published late" not in body


# --- pre-open -------------------------------------------------------------------

def test_no_wall_break_before_the_open_on_carried_over_spot():
    """25-Nov 09:14:50 NIFTY: 'Put Wall Broken at 25,500' with spot = prior close 25,349.65."""
    det = WallGexShiftDetector()
    pre = _view(datetime(2026, 11, 25, 9, 14, 50, tzinfo=IST), 25349.65, [], prior_close=25349.65,
                symbol="NIFTY", call_wall=25800.0, put_wall=25500.0)
    assert not any(e.type == "WALL_BREAK" for e in det.detect(pre, STATE))


def test_wall_break_after_the_open_still_fires():
    det = WallGexShiftDetector()
    base = _view(datetime(2026, 11, 25, 9, 20, 0, tzinfo=IST), 25480.0, [], symbol="NIFTY",
                 call_wall=25800.0, put_wall=25500.0, prior_close=25349.65)
    evts = det.detect(base, STATE)
    assert any(e.type == "WALL_BREAK" for e in evts)


# --- wall migration chain -------------------------------------------------------

def test_migration_from_matches_the_last_wall_shown():
    """25-Nov: 'Put Wall Migrated 25,500 -> 25,700' after the previous card ended at 25,800."""
    det = WallGexShiftDetector()
    t = datetime(2026, 11, 25, 13, 0, 0, tzinfo=IST)
    det.detect(_view(t, 25810.0, [], symbol="NIFTY", call_wall=26000.0, put_wall=25800.0, prior_close=25810.0), STATE)
    # Put wall moves to 25,500 while spot is far away (>2%): no card, but the reader still sees 25,800.
    det.detect(_view(t + timedelta(minutes=5), 26500.0, [], symbol="NIFTY", call_wall=26000.0, put_wall=25500.0, prior_close=25810.0), STATE)
    # Spot comes back near the put wall; the next card must start at 25,800, not 25,500.
    evts = det.detect(_view(t + timedelta(minutes=10), 25700.0, [], symbol="NIFTY", call_wall=26000.0, put_wall=25700.0, prior_close=25810.0), STATE)
    mig = [e for e in evts if e.type == "WALL_MIGRATION"]
    assert len(mig) == 1
    assert mig[0].facts["old_strike"] == 25800.0
    assert mig[0].facts["new_strike"] == 25700.0


# --- narrator text ------------------------------------------------------------------

def test_pcr_surge_headline_has_one_sign():
    """25-Nov 09:23:50 SENSEX rendered '(++0.17)'."""
    det = PcrFlowDetector()
    t = datetime(2026, 11, 25, 9, 23, 50, tzinfo=IST)
    det.detect(_view(t - timedelta(minutes=8), 83800.0, [], extra={"pcr": 0.76}), STATE)
    evts = det.detect(_view(t, 83800.0, [], extra={"pcr": 0.93}), STATE)
    surge = [e for e in evts if e.type == "PCR_SHIFT"]
    assert surge
    headline, _ = render_event(surge[0])
    assert "++" not in headline
    assert headline.endswith("(+0.17)")


def test_orb_set_names_the_basis_and_window():
    ev = Event(
        id="x", ts=datetime(2026, 11, 25, 9, 30, 5, tzinfo=IST), type="ORB_ESTABLISHED",
        facts={"orb_high": 83947.4, "orb_low": 83400.0, "orb_range_pts": 547.4, "basis": "FUT", "window": "09:15-09:29"},
        magnitude_sigma=0.5,
    )
    headline, body = render_event(ev)
    assert headline == "Opening 15m Range Set: 547.4 pts"
    assert "09:15-09:29" in body and "futures basis" in body


# --- wall-chase card helpers ----------------------------------------------------------

def test_wall_chase_helpers_dedupe_and_cut_on_sentences():
    import web_server as ws

    events = [
        "Call Wall Broken at ₹84,000",
        "Call Wall Broken at ₹84,000",
        "Call Wall Broken at ₹84,200",
    ]
    assert ws._distinct_chase_events(events) == [
        "Call Wall Broken at ₹84,000",
        "Call Wall Broken at ₹84,200",
    ]
    text = "The tape printed a bid. " * 30
    cut = ws._cut_sentences(text, 280)
    assert len(cut) <= 280
    assert cut.endswith(".")
    assert ws._cut_sentences("short.", 280) == "short."


def test_wall_chase_card_opens_its_watch_episode_with_one_ledger_kill(monkeypatch, tmp_path):
    """The card body and the ledger must agree, and the episode must actually open.

    A stale name inside the episode try-block was swallowed by `except Exception`, which would
    silently skip the ledger. This test fails if the episode does not open.
    """
    import web_server as ws
    import analytics.watch_episode as we

    monkeypatch.setattr(we, "_journal_path", lambda: tmp_path)
    monkeypatch.setattr(ws, "_WALL_CHASE_COMMENTARY_SEEN", {})
    monkeypatch.setattr(ws, "_WALL_CHASE_COMMENTARY_AT", {})
    saved: list = []

    def _fake_save(entry, symbol="NIFTY"):  # noqa: ANN001
        saved.append(entry)
        return True

    import commentary.storage.db as cdb

    monkeypatch.setattr(cdb, "save_commentary_entry", _fake_save)
    monkeypatch.setattr(ws, "_get_ist_now", lambda: datetime(2026, 11, 25, 10, 40, 44, tzinfo=IST))

    row = {
        "symbol": "SENSEX", "notify": True, "side": "CALL", "stance": "CALL WATCH",
        "n_events": 2, "badge": "🏃 WALL CHASE ×2", "scenario": "WALL_CHASE",
        "outcome": "", "drivers": "Same-side wall break/migrate printed 2×. " * 12,
        "risk": "", "trigger": "Hold above ₹84,000 with tape agreement.",
        "kill": "Price gets back through ₹84,075.", "signal_type": "CONTINUATION_BULL",
        "signal_score": 69, "confidence": "HIGH", "spot": 84147.0,
        "call_wall": 84000.0, "put_wall": 83800.0, "magnet": 84000.0, "flip": 83977.0,
        "zone": "ABOVE_CALL_WALL",
        "stretch": {
            "event_headlines": ["Call Wall Broken at ₹84,000", "Call Wall Broken at ₹84,000", "Call Wall Migrated from ₹84,500 to ₹84,000"],
            "next_map_strike": 84600.0, "strike_step": 100.0, "note": "ladder",
        },
    }
    out = ws._log_wall_chase_to_commentary(row)
    assert out and out.get("status") == "ok", out
    assert out.get("episode_id"), "watch episode did not open"
    assert out.get("kill_level") is not None
    entry = saved[-1]
    assert entry.headline == "🏃 WALL CHASE · CALL WATCH · SENSEX"
    # One kill line, the ledger value. The Drivers text is cut on a sentence boundary.
    assert entry.body.count("(ledger)") == 1
    assert "Kill " + f"{out['kill_level']:,.0f}" in entry.body
    assert entry.body.count("Price gets back through") == 0
    assert "Walls moved (distinct): Call Wall Broken at ₹84,000 · Call Wall Migrated" in entry.body
    assert len(entry.body) < 1200


def test_tape_alert_card_has_one_ledger_kill_and_no_wall_chase_chip(monkeypatch, tmp_path):
    """25-Nov 11:06 Sensex: printed kill 84,400 vs ledger 84,270.1; a WALL CHASE chip on a tape card."""
    import asyncio

    import web_server as ws
    import analytics.watch_episode as we
    import commentary.storage.db as cdb

    monkeypatch.setattr(we, "_journal_path", lambda: tmp_path)
    monkeypatch.setattr(ws, "_WALL_CHASE_COMMENTARY_AT", {})
    monkeypatch.setattr(ws, "_get_ist_now", lambda: datetime(2026, 11, 25, 11, 6, 20, tzinfo=IST))
    saved: list = []
    monkeypatch.setattr(cdb, "save_commentary_entry", lambda entry, symbol="NIFTY": saved.append(entry) or True)

    payload = {
        "symbol": "SENSEX",
        "scenario": "TAPE_CVD_DIVERGENCE_BULL",
        "badge": "🏃 WALL CHASE ×3 · 📡 CVD DIVERGENCE BULL · ⚖️ BETWEEN WALLS · MED",
        "outcome": "CALL WATCH · Confidence MED. Price sits between the put wall and the call wall.",
        "drivers": "The orderflow tape just printed: Bullish CVD Divergence: Price made lower low (84,450.0) while CVD drifted higher. Breadth is 11 up and 3 down.",
        "risk": "Trigger: Hold the bullish tape idea; clear ₹85,000 if walls are split. Kill: Price gets back through ₹84,400. Stop beyond the wall, never at the magnet.",
        "trigger": "Hold the bullish tape idea; clear ₹85,000 if walls are split.",
        "kill": "Price gets back through ₹84,400",
        "stance": "CALL WATCH",
        "confidence": "MED",
        "zone": "ABOVE_CALL_WALL",  # a chase zone: the server opens a watch episode here
        "spot": 84330.0,
        "call_wall": 85000.0,
        "put_wall": 84000.0,
    }
    out = asyncio.run(ws.log_market_alert(payload))
    assert out.get("status") == "ok", out
    entry = saved[-1]
    assert "WALL CHASE" not in entry.headline
    assert entry.headline == "📡 CVD DIVERGENCE BULL · ⚖️ BETWEEN WALLS · MED"
    assert "Price gets back through" not in entry.body
    assert entry.body.count("(ledger)") == 1
    assert f"Kill {out['kill_level']:,.0f} (ledger)" in entry.body
    assert entry.body.count("Outcome:") == 1
    assert "Triggered at 11:06:20 IST" in entry.body, entry.body
    assert "Breadth is 11 up and 3 down." in entry.body  # short drivers are kept whole
