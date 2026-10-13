"""Unit tests for Phase 2: Feature Extraction and Wall Detection."""

import math
from datetime import datetime
import pandas as pd
import pytest

from target_engine.features import extract_features
from target_engine.walls import OptionWall, detect_walls


@pytest.fixture
def mock_chain():
    """Create a realistic options chain around spot 25000."""
    strikes = [24500, 24600, 24700, 24800, 24900, 25000, 25100, 25200, 25300, 25400, 25500]
    rows = []
    for k in strikes:
        # Calls
        c_oi = 500000 if k == 25200 else (300000 if k == 25500 else 80000)
        c_chg = 120000 if k == 25200 else -20000
        rows.append({"strike": k, "type": "CE", "oi": c_oi, "change_oi": c_chg, "ltp": 50.0, "iv": 0.15})
        # Puts
        p_oi = 600000 if k == 24800 else (250000 if k == 24500 else 90000)
        p_chg = 80000 if k == 24800 else 10000
        rows.append({"strike": k, "type": "PE", "oi": p_oi, "change_oi": p_chg, "ltp": 45.0, "iv": 0.15})
    return pd.DataFrame(rows)


def test_detect_walls(mock_chain):
    call_walls, put_walls = detect_walls(mock_chain, spot=25000.0, strike_window=8, min_oi_share=0.15)
    assert len(call_walls) >= 1
    assert len(put_walls) >= 1
    # Check that highest OI call (25200) was detected
    call_strikes = [w.strike for w in call_walls]
    assert 25200 in call_strikes
    # Check that highest OI put (24800) was detected
    put_strikes = [w.strike for w in put_walls]
    assert 24800 in put_strikes


def test_feature_extraction_normalization(mock_chain):
    fv = extract_features(
        spot=25000.0,
        fut=25030.0,
        atm_iv=0.15,
        expiry="2026-10-15",
        contracts_df=mock_chain,
        now=datetime(2026, 10, 12, 10, 0, 0),
    )

    assert fv.spot == 25000.0
    assert fv.fut == 25030.0
    assert isinstance(fv.features, dict)

    # Check that all directional features fall within [-1, 1]
    for key in ["F1_call_wall_prox", "F2_put_wall_prox", "F3_wall_momentum",
                "F4_aggregate_buildup", "F5_pcr_level", "F8_futures_buildup"]:
        val = fv.features[key]
        assert -1.0 <= val <= 1.0, f"Feature {key} out of bounds: {val}"

    # F1 call wall prox must be <= 0 (resistance is bearish)
    assert fv.features["F1_call_wall_prox"] <= 0.0
    # F2 put wall prox must be >= 0 (support is bullish)
    assert fv.features["F2_put_wall_prox"] >= 0.0
