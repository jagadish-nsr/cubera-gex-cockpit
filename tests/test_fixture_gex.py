"""Hand-checkable GEX fixture.

5 strikes, spot=100, contract_size=100 -> scale F = spot^2 * 0.01 * 100 = 10,000.
Both legs are stored negative. Regime uses call magnitude minus put magnitude,
not the stored sum.

  strike  gamma  callOI  putOI | call_mag  put_mag | stored call | stored put | imbalance
   90     0.010   1000    500  |  100,000   50,000 |   -100,000  |    -50,000 |   +50,000
   95     0.020   2000   5000  |  400,000 1,000,000 |   -400,000  | -1,000,000 |  -600,000
  100     0.030   1500   2000  |  450,000   600,000 |   -450,000  |   -600,000 |  -150,000
  105     0.020   3000   1500  |  600,000   300,000 |   -600,000  |   -300,000 |  +300,000
  110     0.010    800   1000  |   80,000   100,000 |    -80,000  |   -100,000 |   -20,000

  call wall = 105 (largest call magnitude)
  put wall  = 95  (largest put magnitude)
  magnet    = 95  (largest |stored net| = 1,400,000)
  imbalance = -420,000 -> NEGATIVE (put-heavy book)
"""

import pandas as pd
import pytest

from gex import core


def _fixture_contracts() -> pd.DataFrame:
    rows = [
        (90, "C", 0.010, 1000),
        (90, "P", 0.010, 500),
        (95, "C", 0.020, 2000),
        (95, "P", 0.020, 5000),
        (100, "C", 0.030, 1500),
        (100, "P", 0.030, 2000),
        (105, "C", 0.020, 3000),
        (105, "P", 0.020, 1500),
        (110, "C", 0.010, 800),
        (110, "P", 0.010, 1000),
    ]
    return pd.DataFrame(rows, columns=["strike", "type", "gamma", "oi"])


SPOT = 100.0


def _gex() -> pd.DataFrame:
    return core.per_strike_gex(_fixture_contracts(), spot=SPOT, contract_size=100)


def test_per_strike_gex_matches_hand_calc():
    by_strike = _gex().set_index("strike")

    assert by_strike.loc[90, "call_gex"] == -100_000
    assert by_strike.loc[105, "call_gex"] == -600_000
    assert by_strike.loc[95, "put_gex"] == -1_000_000
    assert by_strike.loc[105, "put_gex"] == -300_000
    assert (by_strike["call_gex"] <= 0).all()
    assert (by_strike["put_gex"] <= 0).all()
    assert by_strike.loc[95, "net_gex"] == -1_400_000
    assert by_strike.loc[105, "net_gex"] == -900_000


def test_walls_magnet_and_book_balance():
    df = _gex()
    assert core.call_wall(df) == 105
    assert core.put_wall(df) == 95
    assert core.magnet(df) == 95
    assert core.total_net_gex(df) == -3_680_000
    assert core.writer_imbalance_net(df) == -420_000
    assert core.regime(df) == "NEGATIVE"


def test_other_sign_model_is_rejected():
    with pytest.raises(ValueError, match="short_call_short_put"):
        core.per_strike_gex(
            _fixture_contracts(), spot=SPOT, contract_size=100,
            dealer_sign_model="long_call_short_put",
        )
