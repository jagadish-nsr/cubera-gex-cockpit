"""Hand-checkable DEX fixture.

5 strikes, spot=100, contract_size=100 -> scale D = spot * 0.01 * 100 = 100.
short_call_short_put: inventory δ = −δ · OI · scale on each leg.

  strike  δ_call  callOI | call_dex              δ_put  putOI | put_dex
   90      0.80    1000  | −(0.80·1000·100)=−80,000   −0.20   500 | −(−0.20·500·100)=+10,000
   95      0.60    2000  | −120,000                    −0.40  5000 | +200,000
  100      0.50    1500  | −75,000                     −0.50  2000 | +100,000
  105      0.40    3000  | −120,000                    −0.60  1500 | +90,000
  110      0.20     800  | −16,000                     −0.80  1000 | +80,000

  call δ-wall = 95 or 105 (both |call_dex|=120,000) — first max wins via idxmax → 95
  put  δ-wall = 95 (|put_dex|=200,000)
  net DEX     = Σ net_dex = −70k + 80k + 25k − 30k + 64k = +69,000
"""

import pandas as pd
import pytest

from gex import core
from gex.bs import bs_delta


def _fixture_contracts() -> pd.DataFrame:
    rows = [
        (90, "C", 0.80, 1000),
        (90, "P", -0.20, 500),
        (95, "C", 0.60, 2000),
        (95, "P", -0.40, 5000),
        (100, "C", 0.50, 1500),
        (100, "P", -0.50, 2000),
        (105, "C", 0.40, 3000),
        (105, "P", -0.60, 1500),
        (110, "C", 0.20, 800),
        (110, "P", -0.80, 1000),
    ]
    return pd.DataFrame(rows, columns=["strike", "type", "delta", "oi"])


SPOT = 100.0


def _dex() -> pd.DataFrame:
    return core.per_strike_dex(_fixture_contracts(), spot=SPOT, contract_size=100)


def test_per_strike_dex_matches_hand_calc():
    by_strike = _dex().set_index("strike")

    assert by_strike.loc[90, "call_dex"] == -80_000
    assert by_strike.loc[90, "put_dex"] == 10_000
    assert by_strike.loc[95, "call_dex"] == -120_000
    assert by_strike.loc[95, "put_dex"] == 200_000
    assert by_strike.loc[100, "net_dex"] == 25_000  # -75k + 100k
    assert by_strike.loc[105, "call_dex"] == -120_000
    assert by_strike.loc[110, "put_dex"] == 80_000


def test_delta_walls_and_aggregate():
    df = _dex()
    assert core.call_delta_wall(df) == 95  # tied 95/105; idxmax keeps first
    assert core.put_delta_wall(df) == 95
    assert core.total_net_dex(df) == 69_000


def test_other_sign_model_is_rejected():
    with pytest.raises(ValueError, match="short_call_short_put"):
        core.per_strike_dex(
            _fixture_contracts(), spot=SPOT, contract_size=100,
            dealer_sign_model="long_call_short_put",
        )


def test_bs_delta_call_put_parity_identity():
    # put δ = call δ − e^{-qT}; with q=0, put = call − 1
    S, K, T, sig = 100.0, 100.0, 0.25, 0.20
    call = float(bs_delta(S, K, T, sig, option_type="C"))
    put = float(bs_delta(S, K, T, sig, option_type="P"))
    assert abs(call - 0.5) < 0.02  # roughly ATM
    assert abs(put - (call - 1.0)) < 1e-9


def test_bs_delta_degenerate_is_zero():
    assert float(bs_delta(100.0, 100.0, 0.0, 0.2, option_type="C")) == 0.0
    assert float(bs_delta(100.0, 100.0, 0.25, 0.0, option_type="P")) == 0.0


def test_merge_gex_dex_joins_on_strike():
    gex = pd.DataFrame({
        "strike": [100.0, 105.0],
        "call_gex": [-1.0, -2.0],
        "put_gex": [-3.0, -4.0],
        "net_gex": [-4.0, -6.0],
    })
    dex = pd.DataFrame({
        "strike": [100.0, 105.0],
        "call_dex": [-10.0, -20.0],
        "put_dex": [5.0, 8.0],
        "net_dex": [-5.0, -12.0],
    })
    merged = core.merge_gex_dex(gex, dex)
    assert list(merged.columns) == [
        "strike", "call_gex", "put_gex", "net_gex", "call_dex", "put_dex", "net_dex",
    ]
    assert merged.loc[0, "net_dex"] == -5.0
