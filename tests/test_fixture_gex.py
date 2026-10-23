"""Hand-checkable GEX fixture (CLAUDE.md #3, gex-math skill).

5 strikes, spot=100, contract_size=100 -> scale factor F = spot^2 * 0.01 * 100 = 10,000.
BS gamma is identical for a call and a put at the same strike, so one gamma column.

  call_gex = +gamma * call_OI * F
  put_gex  = -gamma * put_OI  * F     (dealer short put gamma)
  net_gex  = call_gex + put_gex

Worked by hand:

  strike  gamma  callOI  putOI |   call_gex      put_gex      net_gex
   90     0.010   1000    500  |   100,000      -50,000      +50,000
   95     0.020   2000   5000  |   400,000   -1,000,000     -600,000
  100     0.030   1500   2000  |   450,000     -600,000     -150,000
  105     0.020   3000   1500  |   600,000     -300,000     +300,000
  110     0.010    800   1000  |    80,000     -100,000      -20,000

Expected:
  call wall  = 105   (max call_gex   = 600,000)
  put wall   = 95    (max |put_gex|  = 1,000,000)  -> below spot = support
  magnet     = 95    (max |net_gex|  = 600,000)
  total      = -420,000  -> NEGATIVE-gamma regime
"""

import pandas as pd

from gex import core


def _fixture_contracts() -> pd.DataFrame:
    rows = [
        # strike, type, gamma, oi
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


def test_per_strike_gex_matches_hand_calc():
    df = core.per_strike_gex(
        _fixture_contracts(),
        spot=SPOT,
        contract_size=100,
        dealer_sign_model="long_call_short_put",
    )
    by_strike = df.set_index("strike")

    assert by_strike.loc[90, "call_gex"] == 100_000
    assert by_strike.loc[95, "call_gex"] == 400_000
    assert by_strike.loc[100, "call_gex"] == 450_000
    assert by_strike.loc[105, "call_gex"] == 600_000
    assert by_strike.loc[110, "call_gex"] == 80_000

    assert by_strike.loc[90, "put_gex"] == -50_000
    assert by_strike.loc[95, "put_gex"] == -1_000_000
    assert by_strike.loc[100, "put_gex"] == -600_000
    assert by_strike.loc[105, "put_gex"] == -300_000
    assert by_strike.loc[110, "put_gex"] == -100_000

    assert by_strike.loc[95, "net_gex"] == -600_000
    assert by_strike.loc[105, "net_gex"] == 300_000


def test_call_wall():
    df = core.per_strike_gex(_fixture_contracts(), SPOT, 100, "long_call_short_put")
    assert core.call_wall(df) == 105


def test_put_wall():
    df = core.per_strike_gex(_fixture_contracts(), SPOT, 100, "long_call_short_put")
    assert core.put_wall(df) == 95


def test_magnet():
    df = core.per_strike_gex(_fixture_contracts(), SPOT, 100, "long_call_short_put")
    assert core.magnet(df) == 95


def test_total_net_gex_and_regime():
    df = core.per_strike_gex(_fixture_contracts(), SPOT, 100, "long_call_short_put")
    assert core.total_net_gex(df) == -420_000
    assert core.regime(df) == "NEGATIVE"


def test_scsp_writer_imbalance_matches_us_net():
    """SCSP stores both legs negative; regime must use call-minus-put, not sum(net)."""
    us = core.per_strike_gex(_fixture_contracts(), SPOT, 100, "long_call_short_put")
    scsp = core.per_strike_gex(_fixture_contracts(), SPOT, 100, "short_call_short_put")
    assert (scsp["call_gex"] <= 0).all()
    assert (scsp["put_gex"] <= 0).all()
    assert (scsp["net_gex"] <= 0).all()
    # Naive sum is always negative and is NOT the regime signal.
    assert core.total_net_gex(scsp) < 0
    imbalance = core.writer_imbalance_net(scsp, "short_call_short_put")
    assert imbalance == core.total_net_gex(us) == -420_000
    assert core.regime(scsp, "short_call_short_put") == "NEGATIVE"
    assert core.call_wall(scsp) == core.call_wall(us) == 105
    assert core.put_wall(scsp) == core.put_wall(us) == 95
