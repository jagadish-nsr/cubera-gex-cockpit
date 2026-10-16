"""tests/test_commentary_new_detectors.py — Unit tests for level-down detectors & session narrative synthesis."""

from __future__ import annotations

import datetime as dt
import pytest

from commentary.models import PointInTimeView, StoryState
from commentary.detectors.secondary_walls import SecondaryWallDetector
from commentary.detectors.pcr_flow import PcrFlowDetector
from commentary.detectors.vwap_dynamics import VwapDynamicsDetector
from commentary.detectors.sector_breadth import SectorBreadthDetector
from commentary.detectors.straddle_decay import StraddleDecayDetector
from commentary.engine import SessionCommentaryEngine

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def test_secondary_wall_detector():
    t0 = dt.datetime(2026, 10, 15, 10, 0, tzinfo=IST)
    state = StoryState(phase="morning")
    det = SecondaryWallDetector()

    pit = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=22805.0,
        prior_close=22700.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=23000.0,
        put_wall=22600.0,
        gamma_flip=22750.0,
        max_pain=22800.0,
        net_gex_sign=1,
        extra_facts={
            "call_wall_2": 22800.0,
            "put_wall_2": 22700.0,
        },
    )

    evts = det.detect(pit, state)
    assert any(e.type == "SECONDARY_WALL_TEST" for e in evts)
    sec_evt = next(e for e in evts if e.type == "SECONDARY_WALL_TEST")
    assert sec_evt.facts["strike"] == 22800.0
    assert sec_evt.facts["wall_type"] == "SECONDARY_CALL_WALL"


def test_pcr_flow_detector():
    t0 = dt.datetime(2026, 10, 15, 10, 15, tzinfo=IST)
    state = StoryState(phase="morning")
    det = PcrFlowDetector()

    # Base reading
    pit1 = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=22850.0,
        prior_close=22700.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=23000.0,
        put_wall=22700.0,
        gamma_flip=22800.0,
        max_pain=22850.0,
        net_gex_sign=1,
        extra_facts={"pcr": 0.95},
    )
    det.detect(pit1, state)

    # Bullish surge reading
    pit2 = PointInTimeView(
        as_of=t0 + dt.timedelta(minutes=15),
        symbol="NIFTY",
        spot=22900.0,
        prior_close=22700.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=23000.0,
        put_wall=22700.0,
        gamma_flip=22800.0,
        max_pain=22850.0,
        net_gex_sign=1,
        extra_facts={"pcr": 1.22},
    )
    evts2 = det.detect(pit2, state)
    assert any(e.type == "PCR_SHIFT" for e in evts2)
    pcr_evt = next(e for e in evts2 if e.type == "PCR_SHIFT")
    assert pcr_evt.facts["direction"] == "BULLISH"
    assert pcr_evt.facts["new_pcr"] == 1.22


def test_vwap_dynamics_detector():
    t0 = dt.datetime(2026, 10, 15, 11, 0, tzinfo=IST)
    state = StoryState(phase="morning")
    det = VwapDynamicsDetector()

    # VWAP Defense
    pit = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=22915.0,
        prior_close=22800.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=23000.0,
        put_wall=22700.0,
        gamma_flip=22850.0,
        max_pain=22900.0,
        net_gex_sign=1,
        extra_facts={"session_vwap": 22910.0},
    )
    evts = det.detect(pit, state)
    assert any(e.type == "VWAP_HOLD_BOUNCE" for e in evts)


def test_sector_breadth_detector():
    t0 = dt.datetime(2026, 10, 15, 11, 30, tzinfo=IST)
    state = StoryState(phase="morning")
    det = SectorBreadthDetector()

    pit = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=22950.0,
        prior_close=22800.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=23000.0,
        put_wall=22700.0,
        gamma_flip=22850.0,
        max_pain=22900.0,
        net_gex_sign=1,
        extra_facts={
            "sectors": {
                "rows": [
                    {"name": "Nifty Bank", "change_pct": 1.35},
                    {"name": "Nifty Financial Services", "change_pct": 1.15},
                    {"name": "Nifty Auto", "change_pct": 0.65},
                    {"name": "Nifty IT", "change_pct": -0.45},
                ]
            }
        },
    )
    evts = det.detect(pit, state)
    assert any(e.type == "SECTOR_LEADERSHIP" for e in evts)
    sec_evt = next(e for e in evts if e.type == "SECTOR_LEADERSHIP")
    assert sec_evt.facts["leader"] == "Nifty Bank"


def test_straddle_decay_detector():
    t0 = dt.datetime(2026, 10, 15, 12, 15, tzinfo=IST)
    state = StoryState(phase="midday")
    det = StraddleDecayDetector()

    candles_30m = [
        {"high": 22955.0, "low": 22935.0, "close": 22945.0}
        for _ in range(35)
    ]

    pit = PointInTimeView(
        as_of=t0,
        symbol="NIFTY",
        spot=22945.0,
        prior_close=22800.0,
        atm_iv=0.14,
        expected_move_1d=120.0,
        call_wall=23000.0,
        put_wall=22700.0,
        gamma_flip=22850.0,
        max_pain=22900.0,
        net_gex_sign=1,
        candles_1m=candles_30m,
        extra_facts={
            "straddle_decay": {"current_straddle": 142.0, "decay_pts": 18.0, "decay_pct": 11.2}
        },
    )
    evts = det.detect(pit, state)
    assert any(e.type in ("RANGE_COMPRESSION", "ATM_STRADDLE_DECAY") for e in evts)
