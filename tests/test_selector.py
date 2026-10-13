"""Unit tests for Phase 4: Candidate Selection and Sizing."""

import math
from datetime import datetime
import pandas as pd
import pytest

from target_engine.contracts import (
    FeatureVector,
    IVForecast,
    OptionLeg,
    ScenarioProb,
    ScenarioSet,
    UnderlyingTarget,
)
from target_engine.risk import (
    calculate_breakevens_and_payoff,
    calculate_transaction_costs,
    compute_recommended_sizing,
)
from target_engine.selector import select_trade_candidates


@pytest.fixture
def sample_chain():
    strikes = [24800, 24900, 25000, 25100, 25200]
    rows = []
    for k in strikes:
        rows.append({"strike": k, "type": "CE", "ltp": max(10.0, 25000 - k + 60.0), "iv": 0.15, "oi": 20000})
        rows.append({"strike": k, "type": "PE", "ltp": max(10.0, k - 25000 + 60.0), "iv": 0.15, "oi": 20000})
    return pd.DataFrame(rows)


def test_calculate_breakevens_and_cost():
    legs = [OptionLeg(strike=25000.0, option_type="CE", action="BUY", quantity=1, entry_premium=80.0)]
    cost = calculate_transaction_costs(legs, lot_size=65, num_lots=1)
    assert cost > 0.0

    bes, max_loss, max_profit = calculate_breakevens_and_payoff("LONG_CALL", legs)
    assert bes[0] == 25080.0
    assert max_loss == 80.0
    assert max_profit is None


def test_select_trade_candidates(sample_chain):
    sc_set = ScenarioSet(
        ts=datetime(2026, 10, 12, 10, 0, 0),
        scenarios=[
            ScenarioProb(name="bull", prob=0.60),
            ScenarioProb(name="base", prob=0.25),
            ScenarioProb(name="bear", prob=0.15),
        ],
        dominant_scenario="bull",
        conviction=0.75,
    )

    targets = {
        "bull": UnderlyingTarget("bull", 120, 25150.0, 24900.0, 0.45, 0.10, 0.25, "Call Wall"),
        "base": UnderlyingTarget("base", 120, 25000.0, 24950.0, 0.30, 0.20, 0.15, "Range"),
        "bear": UnderlyingTarget("bear", 120, 24850.0, 25100.0, 0.10, 0.40, 0.05, "Put Wall"),
    }

    iv_fcs = {
        "bull": IVForecast("bull", 120, 0.15, 0.145, "rule"),
        "base": IVForecast("base", 120, 0.15, 0.150, "rule"),
        "bear": IVForecast("bear", 120, 0.15, 0.160, "rule"),
    }

    candidates = select_trade_candidates(
        scenario_set=sc_set,
        underlying_targets=targets,
        iv_forecasts=iv_fcs,
        contracts_df=sample_chain,
        spot=25000.0,
        fut=25010.0,
        expiry="2026-10-15",
        horizon_minutes=120,
    )

    assert len(candidates) >= 1
    top = candidates[0]
    assert top.structure_type in ("LONG_CALL", "LONG_CALL_OTM", "BULL_CALL_SPREAD")
    assert top.max_loss_inr > 0.0
    assert len(top.explain) > 0


def test_sizing():
    lots = compute_recommended_sizing(max_loss_per_lot_inr=5000.0, risk_budget_inr=15000.0)
    assert lots == 3
