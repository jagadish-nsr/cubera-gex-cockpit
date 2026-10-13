"""tests/test_commentary_phase2.py — Unit tests for Phase 2: D4, D5, D7, D8 Detectors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from commentary.detectors.divergences import DivergenceDetector
from commentary.detectors.impulse import ImpulsiveMoveDetector
from commentary.detectors.volatility import VolatilityRegimeDetector
from commentary.detectors.expiry import ExpiryDynamicsDetector
from commentary.models import PointInTimeView, StoryState

IST = timezone(timedelta(hours=5, minutes=30))


def test_divergence_detector():
    t0 = datetime(2026, 10, 13, 10, 30, tzinfo=IST)
    state = StoryState(phase="morning")
    detector = DivergenceDetector()

    # Seed baseline
    pit_seed = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=24800.0,
        prior_close=24750.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        candles_1m=[{"open": 24800.0, "high": 24810.0, "low": 24795.0, "close": 24805.0} for _ in range(8)],
        extra_facts={"fut_oi": 10_000_000, "pcr": 1.20},
    )
    events_seed = detector.detect(pit_seed, state)
    assert len(events_seed) == 0

    # Divergence 1: Spot makes new high (24,835) but Futures OI drops to 9,700,000 (-3%)
    pit_div1 = PointInTimeView(
        as_of=t0 + timedelta(minutes=5),
        symbol="NIFTY",
        spot=24835.0,
        prior_close=24750.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        candles_1m=[{"open": 24800.0, "high": 24835.0, "low": 24795.0, "close": 24835.0} for _ in range(8)],
        extra_facts={"fut_oi": 9_700_000, "pcr": 1.20},
    )
    evts_div1 = detector.detect(pit_div1, state)
    assert any(e.type == "DIVERGENCE_PRICE_OI" for e in evts_div1)

    # Divergence 2: Spot rises further, ATM IV rises sharply from 0.14 to 0.165
    pit_div2 = PointInTimeView(
        as_of=t0 + timedelta(minutes=30),  # beyond cooldown
        symbol="NIFTY",
        spot=24865.0,
        prior_close=24750.0,
        atm_iv=0.165,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        candles_1m=[{"open": 24800.0, "high": 24865.0, "low": 24795.0, "close": 24865.0} for _ in range(8)],
        extra_facts={"fut_oi": 9_700_000, "pcr": 1.20},
    )
    evts_div2 = detector.detect(pit_div2, state)
    assert any(e.type == "DIVERGENCE_PRICE_IV" for e in evts_div2)


def test_impulsive_move_detector():
    t0 = datetime(2026, 10, 13, 11, 15, tzinfo=IST)
    state = StoryState(phase="morning")
    detector = ImpulsiveMoveDetector()

    # Prior 15 bars: baseline volume 500, small range
    prior_bars = [{"open": 24800.0, "high": 24805.0, "low": 24795.0, "close": 24800.0, "volume": 500} for _ in range(15)]
    # Recent 5 bars: explosive move +60 pts (sigma_5m ~ 13.8) on volume 1500 (3.0x ratio)
    recent_bars = [{"open": 24800.0, "high": 24860.0, "low": 24800.0, "close": 24860.0, "volume": 1500} for _ in range(5)]
    candles = prior_bars + recent_bars

    pit = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=24860.0,
        prior_close=24750.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        candles_1m=candles,
    )

    evts = detector.detect(pit, state)
    assert len(evts) == 1
    evt = evts[0]
    assert evt.type == "IMPULSIVE_MOVE"
    assert evt.facts["direction"] == "BULLISH"
    assert evt.facts["volume_ratio"] >= 2.2


def test_volatility_regime_detector():
    t0 = datetime(2026, 10, 13, 12, 0, tzinfo=IST)
    state = StoryState(phase="midday")
    detector = VolatilityRegimeDetector()

    # Baseline IV 0.14
    pit_init = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=24800.0,
        prior_close=24750.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        extra_facts={"india_vix": 13.0},
    )
    detector.detect(pit_init, state)

    # ATM IV surges from 0.14 to 0.17 (+21.4% > 15% threshold)
    pit_surge = PointInTimeView(
        as_of=t0 + timedelta(minutes=5),
        symbol="NIFTY",
        spot=24750.0,
        prior_close=24750.0,
        atm_iv=0.17,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=-1,
        extra_facts={"india_vix": 14.2},  # +9.2% surge
    )
    evts = detector.detect(pit_surge, state)
    assert any(e.type == "IV_SPIKE" for e in evts)
    assert any(e.type == "VIX_SURGE" for e in evts)


def test_expiry_dynamics_detector():
    t0 = datetime(2026, 10, 13, 14, 0, tzinfo=IST)
    state = StoryState(phase="afternoon")
    detector = ExpiryDynamicsDetector()

    # 1. Non-expiry day (DTE = 3.0): should remain dormant
    pit_non_exp = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=24805.0,
        prior_close=24750.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        extra_facts={"dte": 3.0},
    )
    assert len(detector.detect(pit_non_exp, state)) == 0

    # 2. Expiry Day (DTE = 0.2): Spot pinned within 5 pts of Max Pain (24,800)
    pit_exp_pin = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=24805.0,
        prior_close=24750.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        extra_facts={"dte": 0.2},
    )
    evts_pin = detector.detect(pit_exp_pin, state)
    assert any(e.type == "MAX_PAIN_PINNING" for e in evts_pin)
    assert "EXPIRY_DAY_HIGH_GAMMA" in evts_pin[0].quality_flags
