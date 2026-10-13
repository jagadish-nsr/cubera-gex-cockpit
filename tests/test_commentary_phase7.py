"""tests/test_commentary_phase7.py — Verification of Phase 7 QA, Replay, Grading & Demotion."""

from __future__ import annotations

import datetime as dt
from commentary.models import PointInTimeView, SuggestionBite
from commentary.qa.replay import ReplayHarness
from commentary.qa.grading import evaluate_bites_performance
from commentary.qa.demotion import DemotionPolicy


def test_grading_report_metrics():
    now = dt.datetime.now(dt.timezone.utc)
    # 6 winning bites (each +2.0R) and 4 losing bites (each -1.0R) -> 10 decisive
    bites: list[SuggestionBite] = []
    for i in range(6):
        b = SuggestionBite(
            id=f"win-{i}", entry_id=f"e-{i}", ts=now, type="ACT-IF", headline="Win",
            condition={}, action_frame="", structure={}, levels={}, risk={"rr": 2.0},
            valid_until=now, scenario_link="bull", badge={}, facts_used=[], state="TARGET_HIT",
            outcome={"status": "TARGET_HIT", "realized_r": 2.0},
        )
        bites.append(b)

    for i in range(4):
        b = SuggestionBite(
            id=f"loss-{i}", entry_id=f"el-{i}", ts=now, type="ACT-IF", headline="Loss",
            condition={}, action_frame="", structure={}, levels={}, risk={"rr": 2.0},
            valid_until=now, scenario_link="bull", badge={}, facts_used=[], state="STOPPED",
            outcome={"status": "STOPPED", "realized_r": -1.0},
        )
        bites.append(b)

    report = evaluate_bites_performance(bites, cost_per_trade_r=0.08)
    assert report.total_bites == 10
    assert report.win_rate == 0.60
    # Win rate 0.60 * 2.0 - 0.40 * 1.0 - 0.08 = 1.20 - 0.40 - 0.08 = 0.72R
    assert report.post_cost_expectancy == 0.72


def test_demotion_policy_triggers():
    now = dt.datetime.now(dt.timezone.utc)
    policy = DemotionPolicy(min_win_rate_threshold=0.50, min_expectancy_threshold=0.15)

    # 12 losing bites and 3 winning bites -> win rate = 3/15 = 20%
    bites: list[SuggestionBite] = []
    for i in range(3):
        b = SuggestionBite(
            id=f"w-{i}", entry_id="e", ts=now, type="ACT-IF", headline="W", condition={},
            action_frame="", structure={}, levels={}, risk={}, valid_until=now, scenario_link="bull",
            badge={}, facts_used=[], state="TARGET_HIT", outcome={"status": "TARGET_HIT", "realized_r": 1.5},
        )
        bites.append(b)

    for i in range(12):
        b = SuggestionBite(
            id=f"l-{i}", entry_id="e", ts=now, type="ACT-IF", headline="L", condition={},
            action_frame="", structure={}, levels={}, risk={}, valid_until=now, scenario_link="bull",
            badge={}, facts_used=[], state="STOPPED", outcome={"status": "STOPPED", "realized_r": -1.0},
        )
        bites.append(b)

    new_mode, reason = policy.evaluate_system_mode(bites, current_mode="live")
    assert new_mode == "shadow"
    assert "Automatic demotion" in reason


def test_replay_harness_execution():
    t0 = dt.datetime(2026, 10, 13, 9, 15, tzinfo=dt.timezone.utc)
    snapshots = [
        PointInTimeView(
            as_of=t0,
            symbol="NIFTY",
            spot=24650.0,
            prior_close=24500.0,
            atm_iv=0.13,
            expected_move_1d=95.0,
            call_wall=24800.0,
            put_wall=24500.0,
            gamma_flip=24600.0,
            max_pain=24600.0,
            net_gex_sign=1,
            candles_1m=[{"open": 24650, "high": 24660, "low": 24645, "close": 24655, "volume": 120000}],
        ),
        PointInTimeView(
            as_of=t0 + dt.timedelta(minutes=1),
            symbol="NIFTY",
            spot=24665.0,
            prior_close=24500.0,
            atm_iv=0.13,
            expected_move_1d=95.0,
            call_wall=24800.0,
            put_wall=24500.0,
            gamma_flip=24600.0,
            max_pain=24600.0,
            net_gex_sign=1,
            candles_1m=[{"open": 24655, "high": 24670, "low": 24655, "close": 24665, "volume": 90000}],
        ),
    ]

    harness = ReplayHarness()
    result = harness.replay_sequence(snapshots)
    assert result["entries_count"] >= 1
    assert len(result["entries"]) >= 1
    assert result["entries"][0].verified is True
