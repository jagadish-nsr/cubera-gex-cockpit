"""Hand-checkable tests for the sign-free magnet-drift (dealer-positioning trend)."""

import numpy as np
import pandas as pd

from app.magnet_trend import classify_trend, modal_magnet


def test_modal_magnet_is_the_mode():
    df = pd.DataFrame({"magnet": [24100, 24100, 24200, 24100]})
    assert modal_magnet(df) == 24100.0
    assert modal_magnet(pd.DataFrame({"magnet": []})) is None
    assert modal_magnet(pd.DataFrame({"spot": [1, 2]})) is None   # no magnet column


def test_rising_trend():
    r = classify_trend([23000, 23500, 24000], current=24000)
    assert r["trend"] == "rising"
    assert abs(r["drift_pts"] - 1000) < 1e-6          # fitted slope over the window
    assert r["anchor"] == 23500                       # median of the window
    assert r["current_state"] == "above"              # 24000 vs 23500 anchor


def test_flat_trend_is_anchored():
    # tiny wobbles within the 0.25% band -> flat / anchored.
    r = classify_trend([24200, 24100, 24200], current=24200)
    assert r["trend"] == "flat"
    assert r["current_state"] == "at"                 # today == anchor
    assert not r["repositioning"]


def test_repositioning_flag():
    # today's magnet jumps well clear (>2*tol) of a stable anchor.
    r = classify_trend([24000, 24000, 24000], current=24500)
    assert r["repositioning"] is True
    assert r["current_gap"] == 500


def test_slope_ignores_a_single_endpoint_dip():
    # rose 24000->24300 then a one-day dip to 23900: last<first, but the window
    # leans flat overall -> slope-based drift must NOT call it "falling".
    r = classify_trend([24000, 24100, 24200, 24300, 23900], current=24000)
    assert r["trend"] == "flat"
    assert abs(r["drift_pts"]) < 60          # near-zero fitted slope over the window


def test_falling_and_empty():
    assert classify_trend([24200, 24000, 23700], current=23700)["trend"] == "falling"
    assert classify_trend([], current=24000)["trend"] == "unknown"
    # NaNs are dropped before classifying
    assert classify_trend([np.nan, 24000, 24000], current=24000)["n"] == 2
