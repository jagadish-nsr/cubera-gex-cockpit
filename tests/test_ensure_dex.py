"""Pre-DEX pickled Levels must get DEX backfilled on serve."""

import pandas as pd

from gex.bs import bs_delta
from gex.levels import Levels, ensure_dex


def _levels_without_dex(spot=100.0) -> Levels:
    return Levels(
        spot=spot,
        call_wall=105.0,
        put_wall=95.0,
        magnet=95.0,
        flip=100.0,
        max_pain=100.0,
        total_net_gex=-1.0,
        regime="NEGATIVE",
        dealer_sign_model="short_call_short_put",
        expiry_window="weekly",
        oi_timestamp=None,
        oi_stale=False,
        source="test",
        per_strike=pd.DataFrame({
            "strike": [95.0, 100.0, 105.0],
            "call_gex": [-1.0, -2.0, -3.0],
            "put_gex": [-4.0, -5.0, -6.0],
            "net_gex": [-5.0, -7.0, -9.0],
        }),
        total_net_dex=0.0,
        call_delta_wall=0.0,
        put_delta_wall=0.0,
    )


def _contracts(spot=100.0) -> pd.DataFrame:
    rows = []
    for K, oi_c, oi_p in [(95, 2000, 5000), (100, 1500, 2000), (105, 3000, 1500)]:
        for typ, oi in (("C", oi_c), ("P", oi_p)):
            d = float(bs_delta(spot, K, 0.25, 0.20, option_type=typ))
            rows.append({
                "strike": K, "type": typ, "oi": oi, "iv": 0.20, "T": 0.25,
                "r": 0.0, "q": 0.0, "delta": d,
            })
    return pd.DataFrame(rows)


def test_ensure_dex_backfills_zero_snapshot():
    levels = _levels_without_dex()
    out = ensure_dex(levels, _contracts(), contract_size=100)
    assert abs(out.total_net_dex) > 0
    assert out.call_delta_wall > 0
    assert out.put_delta_wall > 0
    assert "net_dex" in out.per_strike.columns
    assert any("DEX backfilled" in f for f in out.flags)


def test_ensure_dex_noop_when_already_populated():
    levels = _levels_without_dex()
    levels.total_net_dex = 12345.0
    levels.per_strike["net_dex"] = [1.0, 2.0, 3.0]
    out = ensure_dex(levels, _contracts(), contract_size=100)
    assert out.total_net_dex == 12345.0
