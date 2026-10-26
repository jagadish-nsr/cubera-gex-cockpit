"""Expiry weighting: hand-checkable weights, and that near-dated walls win.

Exp decay with a 5-day half-life: 0 DTE -> 1.0, 5 DTE -> 0.5, 10 DTE -> 0.25.
Single-expiry chains are unaffected (uniform weight cancels in argmax/flip).
"""

import numpy as np
import pandas as pd

from gex import core
from gex.weighting import expiry_weights


def test_exp_halflife():
    w = expiry_weights([0, 5, 10], scheme="exp", halflife_days=5.0)
    assert np.allclose(w, [1.0, 0.5, 0.25])


def test_equal_and_nearest():
    assert np.allclose(expiry_weights([1, 30], scheme="equal"), [1.0, 1.0])
    assert np.allclose(expiry_weights([2, 9, 2], scheme="nearest"), [1.0, 0.0, 1.0])


def test_unknown_scheme_raises():
    try:
        expiry_weights([1.0], scheme="bogus")
        assert False, "should have raised"
    except ValueError:
        pass


def test_near_expiry_wall_outweighs_far():
    # Two strikes: a FAR-expiry call wall at 110 (weight 0.25) vs a NEAR-expiry
    # call wall at 105 (weight 1.0). Raw call GEX is larger at 110, but after
    # weighting the NEAR strike (105) should win.
    rows = [
        # strike, type, gamma, oi, weight
        (105, "C", 0.02, 3000, 1.00),   # near: 0.02*3000*1.00 = 60
        (110, "C", 0.02, 3500, 0.25),   # far : 0.02*3500*0.25 = 17.5
    ]
    df = pd.DataFrame(rows, columns=["strike", "type", "gamma", "oi", "weight"])
    per = core.per_strike_gex(df, spot=100, contract_size=100)
    assert core.call_wall(per) == 105

    # Without weighting (equal), the far strike (110, higher raw) would win.
    df_equal = df.drop(columns=["weight"])
    per_equal = core.per_strike_gex(df_equal, spot=100, contract_size=100)
    assert core.call_wall(per_equal) == 110
