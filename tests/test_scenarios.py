"""Unit tests for Phase 3: Scenario scoring and IV forecasting."""

import math
from datetime import datetime
import pytest

from target_engine.contracts import FeatureVector
from target_engine.iv_forecast import forecast_iv
from target_engine.scenarios import score_scenarios


def test_scenario_probabilities_sum_to_one():
    fv = FeatureVector(
        ts=datetime.now(),
        expiry="2026-10-15",
        dte=3.0,
        spot=25000.0,
        fut=25020.0,
        features={
            "F1_call_wall_prox": -0.2,
            "F2_put_wall_prox": 0.8,
            "F4_aggregate_buildup": 0.6,
            "F8_futures_buildup": 0.5,
        },
        quality_flags=[],
    )

    scenario_set = score_scenarios(fv)
    probs = [s.prob for s in scenario_set.scenarios]

    # Must sum to 1.0 within floating point precision
    assert math.isclose(sum(probs), 1.0, abs_tol=1e-6)

    # Bull probability should be dominant given strong positive inputs
    assert scenario_set.dominant_scenario == "bull"
    assert scenario_set.get_prob("bull") > scenario_set.get_prob("bear")


def test_no_edge_declared_when_conviction_low():
    # Conflicting or near-zero features
    fv = FeatureVector(
        ts=datetime.now(),
        expiry="2026-10-15",
        dte=3.0,
        spot=25000.0,
        fut=25000.0,
        features={},
        quality_flags=["stale_snapshot", "thin_chain"],
    )

    scenario_set = score_scenarios(fv)
    assert scenario_set.no_edge is True


def test_iv_forecast_additivity_and_crush():
    # Up move on bull scenario compresses vol
    fc_bull = forecast_iv(scenario="bull", horizon_minutes=60, iv_now=0.16, spot_move_pct=0.01)
    assert fc_bull.iv_at_horizon <= 0.16

    # Down move on bear scenario expands vol
    fc_bear = forecast_iv(scenario="bear", horizon_minutes=60, iv_now=0.16, spot_move_pct=-0.01)
    assert fc_bear.iv_at_horizon >= 0.16

    # Post event crush reduces vol
    fc_event = forecast_iv(
        scenario="base",
        horizon_minutes=60,
        iv_now=0.18,
        event_passed_inside_horizon=True,
    )
    assert fc_event.iv_at_horizon < 0.18
    assert fc_event.components["event"] < 0.0
