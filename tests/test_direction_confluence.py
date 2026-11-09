"""Hand-checkable tests for the fork-B direction confluence heatmap."""
from __future__ import annotations

from analytics.direction_confluence import (
    compute_direction_confluence,
    confluence_to_dict,
)


def _by_key(result):
    return {c.key: c for c in result.cells}


def test_bullish_board_leans_up():
    res = compute_direction_confluence(
        spot=24550,
        call_wall=24700,
        put_wall=24300,
        flip=24480,
        in_positive_zone=True,
        regime="POSITIVE",
        pcr=1.40,
        bias_score=1.2,
        max_pain=24600,
        skew_25d=0.02,
        india_vix={"last": 11.5},
        advances=9,
        declines=2,
        breadth_ratio=4.5,
        overnight_state="accumulation",
        live_label="long build-up",
    )
    assert res.verdict == "LEAN UP"
    assert res.up_count >= 7
    assert res.down_count == 0
    assert res.net_votes > 0
    cells = _by_key(res)
    assert cells["pcr"].lean == "UP"
    assert cells["gex_regime"].lean == "UP"
    assert cells["directional_bias"].lean == "UP"


def test_bearish_board_leans_down():
    res = compute_direction_confluence(
        spot=24350,
        call_wall=24700,
        put_wall=24300,
        flip=24480,
        in_positive_zone=False,
        regime="NEGATIVE",
        pcr=0.70,
        bias_score=-1.1,
        max_pain=24200,
        skew_25d=-0.02,
        india_vix={"last": 24.0},
        advances=2,
        declines=9,
        breadth_ratio=0.22,
        overnight_state="distribution",
        live_label="short build-up",
    )
    assert res.verdict == "LEAN DOWN"
    assert res.down_count >= 7
    assert res.up_count == 0
    cells = _by_key(res)
    assert cells["pcr"].lean == "DOWN"
    assert cells["gex_regime"].lean == "DOWN"


def test_mixed_board_when_signals_split():
    res = compute_direction_confluence(
        spot=24500,
        call_wall=24700,
        put_wall=24300,
        flip=24500,
        in_positive_zone=True,
        regime="NEGATIVE",
        pcr=1.05,
        bias_score=0.1,
        max_pain=24500,
        skew_25d=0.0,
        india_vix={"last": 17.0},
        advances=5,
        declines=5,
        breadth_ratio=1.0,
        overnight_state="accumulation",
        live_label="short build-up",
    )
    assert res.verdict in ("MIXED", "MIXED UP", "MIXED DOWN", "NO READ")
    assert res.up_count + res.down_count + res.flat_count + res.na_count == 10


def test_missing_inputs_are_na_not_crash():
    res = compute_direction_confluence()
    assert res.verdict == "NO READ"
    assert res.na_count >= 8
    payload = confluence_to_dict(res)
    assert payload["verdict"] == "NO READ"
    assert len(payload["cells"]) == 10


def test_pcr_india_convention_high_is_up():
    res = compute_direction_confluence(pcr=1.5)
    assert _by_key(res)["pcr"].lean == "UP"
    res2 = compute_direction_confluence(pcr=0.6)
    assert _by_key(res2)["pcr"].lean == "DOWN"


def test_regime_positive_votes_up():
    res = compute_direction_confluence(regime="POSITIVE")
    assert _by_key(res)["gex_regime"].vote == 1
    res2 = compute_direction_confluence(regime="NEGATIVE")
    assert _by_key(res2)["gex_regime"].vote == -1
