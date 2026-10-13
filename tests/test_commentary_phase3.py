"""tests/test_commentary_phase3.py — Unit tests for Phase 3: D3 Footprint, D6 News, D11 Pinned Monitor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from commentary.detectors.footprint import InstitutionalFootprintDetector
from commentary.detectors.news import NewsCatalystDetector
from commentary.detectors.pinned import PinnedPositionDetector
from commentary.models import PointInTimeView, StoryState

IST = timezone(timedelta(hours=5, minutes=30))


def test_institutional_footprint_detector():
    t0 = datetime(2026, 10, 13, 10, 0, tzinfo=IST)
    state = StoryState(phase="morning")
    detector = InstitutionalFootprintDetector()

    # Sentinel absorption signal
    pit = PointInTimeView(
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
        sentinel_signals=[{
            "signal_type": "ABSORPTION_BULL",
            "score": 85.0,
            "rvol": 2.4,
            "delta_ratio": -0.28,
        }],
        extra_facts={"futures_buildup_state": "LONG_BUILDUP", "futures_oi_change_pct": 5.2},
    )

    evts = detector.detect(pit, state)
    assert any(e.type == "INSTITUTIONAL_FOOTPRINT" for e in evts)
    assert any(e.type == "INSTITUTIONAL_BUILDUP" for e in evts)
    footprint_evt = next(e for e in evts if e.type == "INSTITUTIONAL_FOOTPRINT")
    assert "passive absorption at support" in footprint_evt.facts["footprint_label"]


def test_news_catalyst_detector():
    t0 = datetime(2026, 10, 13, 11, 0, tzinfo=IST)
    state = StoryState(phase="morning")
    detector = NewsCatalystDetector()

    # News 1: Published 4 minutes ago (within match window) with impact score 5
    # News 2: Published 35 minutes ago (outside match window)
    news_items = [
        {
            "id": "news_1",
            "headline": "RBI Governor announces surprise liquidity injection for banking sector",
            "source": "ET Markets",
            "published_ts": t0 - timedelta(minutes=4),
            "impact_score": 5,
        },
        {
            "id": "news_2",
            "headline": "Global crude oil drops 1% in Asian morning trade",
            "source": "TOI",
            "published_ts": t0 - timedelta(minutes=35),
            "impact_score": 4,
        },
    ]

    pit = PointInTimeView(
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
        news_items=news_items,
    )

    evts = detector.detect(pit, state)
    assert len(evts) == 1
    assert evts[0].type == "NEWS_CATALYST"
    assert "RBI Governor" in evts[0].facts["headline"]


def test_pinned_recommendations_monitor():
    t0 = datetime(2026, 10, 13, 11, 30, tzinfo=IST)
    state = StoryState(phase="morning")
    detector = PinnedPositionDetector()

    pinned_card = {
        "rec_id": "NIFTY_CE_24800_MOMENTUM",
        "symbol": "NIFTY",
        "side": "CE",
        "strike": 24800.0,
        "play_badge": "MOMENTUM",
        "initial_spot": 24800.0,
        "target_1_spot": 24880.0,
        "target_2_spot": 24950.0,
        "spot_invalidation": 24740.0,
        "quality_score": 88,
    }

    # Case 1: Spot approaching Target 1 (spot = 24,870 vs T1 = 24,880)
    pit_approach = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=24870.0,
        prior_close=24750.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        pinned_recommendations=[pinned_card],
    )
    evts_app = detector.detect(pit_approach, state)
    assert any(e.type == "PINNED_TARGET_APPROACH" for e in evts_app)

    # Case 2: Target 1 Hit (spot = 24,885 >= 24,880)
    pit_hit = PointInTimeView(
        as_of=t0 + timedelta(minutes=5),
        symbol="NIFTY",
        spot=24885.0,
        prior_close=24750.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        pinned_recommendations=[pinned_card],
    )
    evts_hit = detector.detect(pit_hit, state)
    assert any(e.type == "PINNED_TARGET_HIT" for e in evts_hit)
    hit_evt = next(e for e in evts_hit if e.type == "PINNED_TARGET_HIT")
    assert hit_evt.facts["gain_pts"] == 85.0

    # Case 3: Invalidation Warning on a separate Put card
    pinned_pe = {
        "rec_id": "NIFTY_PE_24700_FADE",
        "symbol": "NIFTY",
        "side": "PE",
        "strike": 24700.0,
        "play_badge": "ICEBERG-FADE",
        "initial_spot": 24800.0,
        "target_1_spot": 24700.0,
        "spot_invalidation": 24860.0,
        "quality_score": 82,
    }
    pit_inval = PointInTimeView(
        as_of=t0 + timedelta(minutes=10),
        symbol="NIFTY",
        spot=24855.0,  # within 15 pts of 24,860 invalidation
        prior_close=24750.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=25000.0,
        put_wall=24600.0,
        gamma_flip=24750.0,
        max_pain=24800.0,
        net_gex_sign=1,
        pinned_recommendations=[pinned_pe],
    )
    evts_inval = detector.detect(pit_inval, state)
    assert any(e.type == "PINNED_INVALIDATION_WARNING" for e in evts_inval)
