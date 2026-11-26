"""Gamma price ladder: per-strike Net GEX is the call-minus-put balance.

Hand-checkable numbers come from the 2026-11-25 audit of the NIFTY book at
strike 24,500 (call_gex = -15,815,701,771.1, put_gex = -20,111,252,007.1).
"""

import pandas as pd
import pytest

from gex import core
from web_server import _strike_balance_gex


def test_balance_at_24500_is_call_minus_put_magnitude():
    # |call| 15.816B, |put| 20.111B -> put-heavy by 4.296B.
    # The old column showed the stored sum, -35.927B, which is not a balance.
    bal = _strike_balance_gex(-15_815_701_771.1, -20_111_252_007.1)
    assert bal == pytest.approx(-4_295_550_236.0, abs=1.0)
    assert bal < 0


def test_call_heavy_strike_is_positive():
    # 24,600: |call| 27.707B, |put| 3.911B -> call-heavy by 23.795B.
    bal = _strike_balance_gex(-27_706_597_465.8, -3_911_248_008.9)
    assert bal == pytest.approx(23_795_349_457.0, abs=1.0)
    assert bal > 0


def test_stored_total_net_is_never_the_ladder_column():
    # Both legs negative -> stored call+put is <= 0 at every strike.
    # The balance can be either sign, so the column must not use the stored sum.
    stored_sum = -15_815_701_771.1 + -20_111_252_007.1
    assert stored_sum < 0
    assert _strike_balance_gex(-27_706_597_465.8, -3_911_248_008.9) > 0


def test_strike_balances_sum_to_writer_imbalance():
    # Summing the per-strike balance must give the KPI writer imbalance (call - put).
    df = pd.DataFrame({
        "strike": [24400.0, 24500.0, 24600.0],
        "call_gex": [-3.118e9, -15.816e9, -27.707e9],
        "put_gex": [-34.360e9, -20.111e9, -3.911e9],
        "net_gex": [0.0, 0.0, 0.0],
    })
    per_strike_balance = df.apply(lambda r: _strike_balance_gex(r["call_gex"], r["put_gex"]), axis=1)
    kpi = core.writer_imbalance_net(df[["strike", "call_gex", "put_gex"]])
    assert per_strike_balance.sum() == pytest.approx(kpi, rel=1e-12)
