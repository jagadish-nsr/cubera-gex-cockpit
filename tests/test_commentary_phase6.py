"""tests/test_commentary_phase6.py — Verification of Phase 6 Suggestion Bites Subsystem."""

from __future__ import annotations

import datetime as dt
from commentary.models import PointInTimeView, Event, ScoredEvent, SuggestionBite
from commentary.bites.gating import check_risk_gating, apply_mode_and_calibration_gating
from commentary.bites.manage import generate_manage_bites
from commentary.bites.engine import BiteEngine
from commentary.bites.lifecycle import BiteLifecycleEvaluator


def test_bites_risk_gating():
    now = dt.datetime.now(dt.timezone.utc)
    # Bite with too much risk: 25,000 INR vs 7,500 INR max allowed (1.5% of 500,000)
    bite_risky = SuggestionBite(
        id="b1",
        entry_id="e1",
        ts=now,
        type="ACT-IF",
        headline="Risky Play",
        condition={"val": 24800.0},
        action_frame="Long Spread",
        structure={},
        levels={"trigger": 24800.0, "invalidation": 24700.0, "target": 25000.0},
        risk={"max_loss_per_lot": 25000.0, "rr": 2.0},
        valid_until=now + dt.timedelta(minutes=30),
        scenario_link="bull",
        badge={},
        facts_used=[],
    )
    passed, reasons = check_risk_gating(bite_risky, capital=500000.0, max_risk_pct=1.5)
    assert not passed
    assert any("exceeds maximum allowed risk" in r for r in reasons)

    # Low R:R ratio (< 1.2)
    bite_low_rr = SuggestionBite(
        id="b2",
        entry_id="e2",
        ts=now,
        type="ACT-IF",
        headline="Low RR Play",
        condition={"val": 24800.0},
        action_frame="Long Spread",
        structure={},
        levels={"trigger": 24800.0, "invalidation": 24700.0, "target": 24850.0},
        risk={"max_loss_per_lot": 2000.0, "rr": 0.8},
        valid_until=now + dt.timedelta(minutes=30),
        scenario_link="bull",
        badge={},
        facts_used=[],
    )
    passed_rr, reasons_rr = check_risk_gating(bite_low_rr)
    assert not passed_rr
    assert any("below minimum" in r for r in reasons_rr)


def test_bites_mode_and_calibration_gating():
    now = dt.datetime.now(dt.timezone.utc)
    bite = SuggestionBite(
        id="b3",
        entry_id="e3",
        ts=now,
        type="ACT-IF",
        headline="Breakout Call",
        condition={"val": 24800.0},
        action_frame="Long Spread",
        structure={},
        levels={"trigger": 24800.0, "invalidation": 24700.0, "target": 25000.0},
        risk={"max_loss_per_lot": 2000.0, "rr": 2.0},
        valid_until=now + dt.timedelta(minutes=30),
        scenario_link="bull",
        badge={},
        facts_used=[],
    )
    # Session count = 5 (< 20 required), uncalibrated
    gated = apply_mode_and_calibration_gating(bite, is_calibrated=False, session_count=5)
    assert gated.badge["mode"] == "shadow"
    assert gated.badge["calibrated"] is False
    # ACT-IF should be downgraded to PREPARE when uncalibrated
    assert gated.type == "PREPARE"


def test_pinned_recommendation_manage_bites():
    now = dt.datetime(2026, 10, 13, 10, 30, tzinfo=dt.timezone.utc)
    pinned = [
        {
            "rec_id": "REC-NIFTY-001",
            "side": "CE",
            "initial_spot": 24700.0,
            "target_1_spot": 24800.0,
            "target_2_spot": 24900.0,
            "spot_invalidation": 24650.0,
            "status": "ACTIVE",
            "stop_loss": 1200.0,
        }
    ]

    # 1. Spot reaches Target 1 (24805 >= 24800)
    bites_t1 = generate_manage_bites(current_spot=24805.0, pinned_records=pinned, as_of=now)
    assert len(bites_t1) == 1
    assert bites_t1[0].type == "MANAGE"
    assert "Target 1 Reached" in bites_t1[0].headline
    assert bites_t1[0].levels["invalidation"] == 24700.0  # Trailed stop to entry

    # 2. Spot approaches Invalidation wall (24658 is within 8 pts of 24650)
    bites_inval = generate_manage_bites(current_spot=24658.0, pinned_records=pinned, as_of=now)
    assert len(bites_inval) == 1
    assert bites_inval[0].type == "MANAGE"
    assert "Invalidation Warning" in bites_inval[0].headline


def test_bites_lifecycle_evaluation():
    t0 = dt.datetime(2026, 10, 13, 9, 30, tzinfo=dt.timezone.utc)
    bite = SuggestionBite(
        id="b-life",
        entry_id="e-life",
        ts=t0,
        type="ACT-IF",
        headline="Breakout",
        condition={"field": "close_5m", "op": ">", "val": 24800.0},
        action_frame="Bull Spread",
        structure={},
        levels={"trigger": 24800.0, "invalidation": 24750.0, "target": 24900.0},
        risk={"max_loss_per_lot": 2000.0, "rr": 2.0},
        valid_until=t0 + dt.timedelta(minutes=30),
        scenario_link="bull",
        badge={"mode": "shadow"},
        facts_used=[],
        state="ISSUED",
    )

    # Minute 1: Spot below trigger
    c1 = {"high": 24795.0, "low": 24780.0, "close": 24790.0}
    b1 = BiteLifecycleEvaluator.evaluate(bite, c1, current_spot=24790.0, as_of=t0 + dt.timedelta(minutes=5))
    assert b1.state == "ISSUED"

    # Minute 2: Candle closes above 24800 -> Triggers
    c2 = {"high": 24810.0, "low": 24795.0, "close": 24805.0}
    b2 = BiteLifecycleEvaluator.evaluate(bite, c2, current_spot=24805.0, as_of=t0 + dt.timedelta(minutes=10))
    assert b2.state == "TRIGGERED"

    # Minute 3: Candle surges to target 24905 -> Target Hit
    c3 = {"high": 24905.0, "low": 24810.0, "close": 24890.0}
    b3 = BiteLifecycleEvaluator.evaluate(bite, c3, current_spot=24890.0, as_of=t0 + dt.timedelta(minutes=15))
    assert b3.state == "TARGET_HIT"
    assert b3.outcome["status"] == "TARGET_HIT"
    assert b3.outcome["realized_r"] == 2.0
