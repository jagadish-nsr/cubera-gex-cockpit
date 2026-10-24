"""Hand-checkable tests for Target Engine dial and ranked T1/SL serialization."""

from datetime import datetime, timezone

from target_engine.contracts import (
    GreekAttribution,
    OptionGreeks,
    OptionLeg,
    OptionTarget,
    ScenarioProb,
    ScenarioSet,
    TradeCandidate,
)
from target_engine.presenter import (
    compose_confluence_dial,
    compose_edge_label,
    preferred_scenario_for_structure,
    serialize_ranked_candidates,
)


def _scenario_set(p_bull, p_base, p_bear, conviction=0.7, no_edge=False, dominant=None):
    names = ["bull", "base", "bear"]
    probs = [p_bull, p_base, p_bear]
    if dominant is None:
        dominant = names[int(max(range(3), key=lambda i: probs[i]))]
    return ScenarioSet(
        ts=datetime.now(timezone.utc),
        scenarios=[
            ScenarioProb(name="bull", prob=p_bull, calibrated=False),
            ScenarioProb(name="base", prob=p_base, calibrated=False),
            ScenarioProb(name="bear", prob=p_bear, calibrated=False),
        ],
        dominant_scenario=dominant,
        conviction=conviction,
        calibrated=False,
        model_version="test",
        n_train_samples=0,
        brier_oos=None,
        contributions={},
        no_edge=no_edge,
    )


def test_dial_uses_conviction_not_rank_score():
    sc = _scenario_set(0.15, 0.25, 0.60, conviction=0.72, dominant="bear")
    dial = compose_confluence_dial(sc)
    assert dial["score"] == 72
    assert dial["verdict"] == "CONFLUENCE BEARISH"
    assert dial["conviction_label"] == "HIGH CONVICTION"


def test_range_led_is_not_confirmed_edge():
    sc = _scenario_set(0.20, 0.55, 0.25, conviction=0.40, no_edge=False, dominant="base")
    label, no_edge = compose_edge_label(sc)
    assert no_edge is True
    assert "RANGE" in label
    dial = compose_confluence_dial(sc)
    assert dial["verdict"] == "CONFLUENCE RANGE"


def test_pe_candidate_t1_comes_from_bear_not_bull():
    dummy_g = OptionGreeks(delta=-0.5, gamma=0.01, vega=5.0, theta=-2.0)
    dummy_a = GreekAttribution(0, 0, 0, 0, 0, 0, 0)
    pe_leg = OptionLeg(strike=22500.0, option_type="PE", action="BUY", entry_premium=170.0, iv=0.15)
    cand = TradeCandidate(
        structure_type="LONG_PUT",
        legs=[pe_leg],
        scenario_targets={
            "bull": OptionTarget(22500, "PE", "bull", 120, 170.0, 28.0, -142, -83, dummy_a, dummy_g, dummy_g),
            "bear": OptionTarget(22500, "PE", "bear", 120, 170.0, 240.0, 70, 41, dummy_a, dummy_g, dummy_g),
            "base": OptionTarget(22500, "PE", "base", 120, 170.0, 155.0, -15, -9, dummy_a, dummy_g, dummy_g),
        },
        expected_value=10.0,
        risk_reward=1.8,
        max_loss_pts=80.0,
        rank_score=0.4,
    )
    assert preferred_scenario_for_structure("LONG_PUT", "PE", "bear") == "bear"
    rows = serialize_ranked_candidates([cand], dominant="bear")
    assert len(rows) == 1
    assert rows[0]["target_1"] == 240.0
    assert rows[0]["entry_ltp"] == 170.0
    assert rows[0]["stop_loss"] == 28.0
    assert rows[0]["score"] == 70  # 50 + 50*0.4
    assert rows[0]["scenario"] == "bear"
