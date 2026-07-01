"""Hand-checkable tests for the multi-factor OOS-IC machinery.

Constructed with a KNOWN answer: forward return is a stable linear function of two
factors across all days, so leave-one-day-out recovers it and the 2-factor combo
beats either factor alone out-of-sample. A pure-noise factor must NOT help.
"""

import numpy as np
import pandas as pd

from scripts.analyze_multifactor import build_mf_day, lodo_ic


def _samples(n_days=8, per_day=10, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        gex = rng.normal(size=per_day)
        pcr = rng.normal(size=per_day)
        noise = rng.normal(size=per_day) * 0.15
        fwd = 0.6 * gex + 0.4 * pcr + noise          # stable across days
        for k in range(per_day):
            rows.append({"day": f"d{d}", "min_ist": 600 + k,
                         "gex": gex[k], "pcr": pcr[k],
                         "vix": rng.normal(), "fwd": fwd[k]})
    return pd.DataFrame(rows)


def test_lodo_combo_beats_single_factor_oos():
    s = _samples()
    gex = lodo_ic(s, ["gex"])
    combo = lodo_ic(s, ["gex", "pcr"])
    assert gex is not None and combo is not None
    assert combo["oos_ic"] > gex["oos_ic"]       # the extra REAL factor helps OOS
    assert combo["oos_ic"] > 0.7                  # near-perfect: signal is clean & stable


def test_lodo_noise_factor_does_not_help_oos():
    # 'vix' here is pure noise -> adding it should not improve OOS IC materially.
    s = _samples()
    combo = lodo_ic(s, ["gex", "pcr"])
    with_noise = lodo_ic(s, ["gex", "pcr", "vix"])
    assert with_noise["oos_ic"] <= combo["oos_ic"] + 0.05   # no real gain from noise


def test_lodo_needs_enough_days():
    s = _samples(n_days=2)
    assert lodo_ic(s, ["gex"]) is None            # < 3 days -> no OOS estimate


def test_build_mf_day_columns():
    df = pd.DataFrame({
        "timestamp_utc": ["2026-06-12T03:45:00Z", "2026-06-12T04:00:00Z",
                          "2026-06-12T04:45:00Z"],
        "date_ist": ["2026-06-12"] * 3,
        "spot": [100.0, 101.0, 102.0], "magnet": [103, 103, 103],
        "pcr_oi": [0.9, 0.95, 1.0], "india_vix": [13.0, 13.2, 13.1],
        "vix_pct_chg": [0.0, 1.5, -0.8],
    })
    out = build_mf_day(df, horizon_min=60.0)
    assert not out.empty
    assert {"gex", "pcr", "vix", "vix_chg", "fwd", "day"}.issubset(out.columns)
    # gex = (magnet-spot)/spot at t=0 -> (103-100)/100 = 0.03
    assert abs(out.iloc[0]["gex"] - 0.03) < 1e-9
