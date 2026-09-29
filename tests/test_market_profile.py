"""Hand-checks for the Market Profile (TPO time-at-price) computation."""

import numpy as np
import pandas as pd

from app.market_profile import day_profile


def test_day_profile_poc_and_value_area():
    # 250 bars parked at 100, a brief excursion to 104 and back -> POC at the modal price.
    s = pd.Series([100.0] * 250 + list(np.linspace(100, 104, 10)) + [104.0] * 5
                  + list(np.linspace(104, 101, 35)))
    p = day_profile(s, bin_pts=1.0)
    assert p is not None
    assert 99.5 <= p["poc"] <= 100.5              # POC = the price where time concentrated
    assert p["val"] <= p["poc"] <= p["vah"]       # value area brackets the POC
    assert p["va_width"] <= p["day_range"] + 1e-9  # VA can't exceed the day's range
    assert p["tpoc_ratio"] > 0.5                  # most of the session sat at the mode
    assert p["n_modes"] >= 1
    assert abs(p["hi"] - 104.0) < 1e-6 and abs(p["lo"] - 100.0) < 1e-6


def test_day_profile_value_area_covers_about_70pct():
    # roughly-normal random walk -> VA should be a sensible fraction of the range, POC inside it
    rng = np.random.default_rng(0)
    s = pd.Series(24000 + np.cumsum(rng.normal(0, 3, 375)))
    p = day_profile(s, bin_pts=5.0)
    assert p is not None
    assert 0.2 < p["va_width_pct"] < 1.0          # value area is a real sub-range of the day
    assert p["lo"] <= p["val"] <= p["vah"] <= p["hi"] + 5.0


def test_day_profile_none_on_degenerate():
    assert day_profile(pd.Series([100.0] * 10)) is None    # < 30 bars
    assert day_profile(pd.Series([100.0] * 100)) is None   # flat (hi == lo)
