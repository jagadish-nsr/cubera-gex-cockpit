"""tests/test_commentary_phase1.py — Unit tests for Phase 1: Models, Config, D1, D2, D9."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from commentary.config_loader import (
    load_commentary_config,
    get_detector_config,
    get_forbidden_phrases,
)
from commentary.detectors.gap import OpeningGapDetector
from commentary.detectors.session_structure import SessionStructureDetector
from commentary.detectors.walls import WallGexShiftDetector
from commentary.models import (
    Event,
    PointInTimeView,
    PointInTimeViolation,
    StoryState,
)

IST = timezone(timedelta(hours=5, minutes=30))


def test_point_in_time_view_guard():
    t0 = datetime(2026, 10, 13, 9, 30, tzinfo=IST)
    pit = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=24800.0,
        prior_close=24700.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
    )
    assert pit.sigma_1d == 120.0

    # Valid access at or before t0
    pit.validate_timestamp(t0)
    pit.validate_timestamp(t0 - timedelta(minutes=5))

    # Look-ahead violation
    with pytest.raises(PointInTimeViolation):
        pit.validate_timestamp(t0 + timedelta(minutes=1))


def test_config_loader():
    cfg = load_commentary_config()
    assert "session" in cfg
    assert "detectors" in cfg
    assert "bites" in cfg

    gap_cfg = get_detector_config("gap")
    assert gap_cfg.get("min_gap_sigma") == 0.35

    forbidden = get_forbidden_phrases()
    assert "buy now" in forbidden
    assert "sure shot" in forbidden


def test_opening_gap_detector():
    t0 = datetime(2026, 10, 13, 9, 15, tzinfo=IST)
    state = StoryState(phase="opening_15m")
    detector = OpeningGapDetector()

    # Prior close 24,700, Open 24,850 (Gap Up +150 pts with sigma=120 -> 1.25 sigma)
    pit = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=24850.0,
        prior_close=24700.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        candles_1m=[{"open": 24850.0, "high": 24860.0, "low": 24840.0, "close": 24855.0}],
    )

    events = detector.detect(pit, state)
    assert len(events) == 1
    evt = events[0]
    assert evt.type == "GAP_UP"
    assert evt.facts["gap_pts"] == 150.0
    assert evt.facts["gap_sigma"] == 1.25
    assert evt.facts["nearest_wall"] == 25000.0

    # Second run without retrace: no duplicate gap event
    events2 = detector.detect(pit, state)
    assert len(events2) == 0

    # Gap fill: price drops to 24,705 (96.6% retraced)
    pit_fill = PointInTimeView(
        as_of=t0 + timedelta(minutes=45),
        symbol="NIFTY",
        spot=24705.0,
        prior_close=24700.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
    )
    fill_events = detector.detect(pit_fill, state)
    assert len(fill_events) == 1
    assert fill_events[0].type == "GAP_FILLED"


def _gap_view(spot, prior, candle_open, em=100.0):
    t0 = datetime(2026, 10, 27, 9, 15, tzinfo=IST)
    candles = []
    if candle_open is not None:
        candles = [{"open": candle_open, "high": candle_open, "low": candle_open, "close": candle_open}]
    return PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=spot,
        prior_close=prior,
        atm_iv=0.14,
        expected_move_1d=em,
        call_wall=spot + 100,
        put_wall=spot - 100,
        gamma_flip=spot,
        max_pain=spot,
        net_gex_sign=-1,
        candles_1m=candles,
    )


def test_opening_gap_rejects_demo_book_against_a_foreign_candle():
    """27 Oct stored headline: spot 24,500, prior 24,500, candle open 22,500.1, gap −1,999.9."""
    detector = OpeningGapDetector()
    events = detector.detect(_gap_view(24500.0, 24500.0, 22500.1), StoryState(phase="opening_15m"))
    assert events == []


def test_opening_gap_rejects_prior_close_from_another_book():
    """Spot is the live index and the prior close is still the 24,500 placeholder."""
    detector = OpeningGapDetector()
    events = detector.detect(_gap_view(22700.0, 24500.0, 22700.0), StoryState(phase="opening_15m"))
    assert events == []


def test_opening_gap_uses_cash_spot_not_the_futures_candle():
    """29 Oct 09:12 stored +81.4 because the futures open was 22,780 against cash 22,690."""
    t0 = datetime(2026, 10, 29, 9, 15, tzinfo=IST)
    pit = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=22690.65,
        prior_close=22698.8,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=23000.0,
        put_wall=22500.0,
        gamma_flip=22670.0,
        max_pain=22700.0,
        net_gex_sign=1,
        candles_1m=[{"start_ts": "2026-10-29T09:15:00+05:30", "open": 22780.2, "high": 22790.0, "low": 22740.0, "close": 22750.0}],
    )
    events = OpeningGapDetector().detect(pit, StoryState(phase="opening_15m"))
    assert events == []


def test_opening_gap_does_not_refire_from_an_afternoon_futures_bar():
    t0 = datetime(2026, 10, 13, 15, 17, tzinfo=IST)
    pit = PointInTimeView(
        as_of=t0,
        symbol="GAPTEST",
        spot=22940.0,
        prior_close=22698.8,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=23000.0,
        put_wall=22800.0,
        gamma_flip=22950.0,
        max_pain=22900.0,
        net_gex_sign=-1,
        candles_1m=[{"start_ts": "2026-10-13T14:17:00+05:30", "open": 22900.0, "high": 22950.0, "low": 22880.0, "close": 22940.0}],
    )
    events = OpeningGapDetector().detect(pit, StoryState(phase="afternoon"))
    assert events == []


def test_opening_gap_restart_keeps_the_published_open(monkeypatch):
    monkeypatch.setattr(
        "commentary.storage.db.find_session_gap",
        lambda symbol, day: {"open": 22690.7, "prior_close": 22698.8, "gap_pts": -8.1},
    )
    t0 = datetime(2026, 10, 13, 15, 17, tzinfo=IST)
    pit = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=22940.0,
        prior_close=22698.8,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=23000.0,
        put_wall=22800.0,
        gamma_flip=22950.0,
        max_pain=22900.0,
        net_gex_sign=-1,
        candles_1m=[{"open": 22900.0, "high": 22950.0, "low": 22880.0, "close": 22940.0}],
    )
    detector = OpeningGapDetector()
    detector._restore_published(pit)
    events = detector.detect(pit, StoryState(phase="afternoon"))
    assert all(e.type != "GAP_UP" for e in events)


def test_call_wall_far_from_spot_is_not_a_break():
    """29 Oct close: Sensex spot 74,151 narrated a break of the 69,300 wall."""
    t0 = datetime(2026, 10, 29, 15, 30, tzinfo=IST)
    detector = WallGexShiftDetector()
    pit = PointInTimeView(
        as_of=t0,
        symbol="SENSEX",
        spot=74151.0,
        prior_close=73200.0,
        atm_iv=0.14,
        expected_move_1d=400.0,
        call_wall=69300.0,
        put_wall=69300.0,
        gamma_flip=74000.0,
        max_pain=74000.0,
        net_gex_sign=-1,
    )
    events = detector.detect(pit, StoryState(phase="close"))
    assert all(e.type != "WALL_BREAK" for e in events)


def test_opening_gap_keeps_a_real_session_gap():
    detector = OpeningGapDetector()
    events = detector.detect(_gap_view(22524.4, 22600.0, 22524.4, em=80.0), StoryState(phase="opening_15m"))
    assert len(events) == 1
    assert events[0].type == "GAP_DOWN"
    assert events[0].facts["prior_close"] == 22600.0
    assert abs(events[0].facts["gap_pts"] - (22524.4 - 22600.0)) < 0.2


def test_walls_and_gex_detector():
    t0 = datetime(2026, 10, 13, 9, 30, tzinfo=IST)
    state = StoryState(phase="morning")
    detector = WallGexShiftDetector()

    # Initial baseline
    pit_init = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=24800.0,
        prior_close=24800.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
    )
    events = detector.detect(pit_init, state)
    assert len(events) == 0

    # 1. Wall Test: Spot tests Call Wall (24,995 vs CW 25,000)
    pit_test = PointInTimeView(
        as_of=t0 + timedelta(minutes=5),
        symbol="NIFTY",
        spot=24995.0,
        prior_close=24800.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
    )
    evts_test = detector.detect(pit_test, state)
    assert any(e.type == "WALL_TEST" and e.facts["wall_type"] == "CALL_WALL" for e in evts_test)

    # 2. Wall Migration: Call Wall shifts to 25,100
    pit_migr = PointInTimeView(
        as_of=t0 + timedelta(minutes=10),
        symbol="NIFTY",
        spot=24950.0,
        prior_close=24800.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25100.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
    )
    evts_migr = detector.detect(pit_migr, state)
    assert any(e.type == "WALL_MIGRATION" and e.facts["new_strike"] == 25100.0 for e in evts_migr)

    # 3. Regime change: net GEX sign flips from +1 to -1
    pit_regime = PointInTimeView(
        as_of=t0 + timedelta(minutes=15),
        symbol="NIFTY",
        spot=24720.0,
        prior_close=24800.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25100.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=-1,
    )
    evts_regime = detector.detect(pit_regime, state)
    assert any(e.type == "REGIME_CHANGE" and e.facts["new_regime"] == "NEGATIVE_GAMMA" for e in evts_regime)


def test_session_structure_detector():
    t0 = datetime(2026, 10, 13, 9, 30, tzinfo=IST)
    state = StoryState(phase="morning")
    detector = SessionStructureDetector()

    # Create 15 1-min candles with High 24,850 and Low 24,780
    candles_15 = [
        {"open": 24800.0, "high": 24820.0, "low": 24790.0, "close": 24810.0}
        for _ in range(14)
    ]
    candles_15.append({"open": 24810.0, "high": 24850.0, "low": 24780.0, "close": 24830.0})

    pit = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=24830.0,
        prior_close=24800.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        candles_1m=candles_15,
        extra_facts={"session_vwap": 24815.0},
    )

    evts = detector.detect(pit, state)
    assert any(e.type == "ORB_ESTABLISHED" for e in evts)
    assert detector._orb_high == 24850.0
    assert detector._orb_low == 24780.0

    # Breakout above ORB High
    pit_break = PointInTimeView(
        as_of=t0 + timedelta(minutes=2),
        symbol="NIFTY",
        spot=24865.0,  # > 24850.0
        prior_close=24800.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        candles_1m=candles_15,
        extra_facts={"session_vwap": 24820.0},
    )
    evts_break = detector.detect(pit_break, state)
    assert any(e.type == "ORB_BREAK_UP" and e.facts["extension_pts"] == 15.0 for e in evts_break)
