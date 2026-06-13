"""Hand-checkable tests for the Phase B analyser.

Synthetic series with a KNOWN answer:
- A POSITIVE day built to mean-revert (alternating up/down) -> autocorr < 0.
- A NEGATIVE day built to trend (same-sign steps)          -> autocorr > 0.
- A frozen post-close repeat must be dropped before stats.
"""

import numpy as np
import pandas as pd

from scripts.analyze_timeseries import (IMPACT_FLAG, autocorr_by_regime,
                                         load_clean_day, load_event_days,
                                         news_day_flag, realized_vol_by_regime)


def _mk(spots, regime, times=None):
    n = len(spots)
    times = times or [f"{9 + i // 4:02d}:{15 + (i % 4) * 15:02d}" for i in range(n)]
    return pd.DataFrame({
        "timestamp_utc": [f"2026-06-12T0{3 + i // 12}:{(45 + i * 5) % 60:02d}:00Z" for i in range(n)],
        "time_ist": times, "spot": spots, "regime": [regime] * n,
        "call_vol": list(range(1, n + 1)),  # always increasing -> never "frozen"
    })


def test_autocorr_sign_mean_revert_vs_momentum():
    # mean-reverting: 100, 102, 100, 102, ... -> returns alternate sign
    pos = _mk([100, 102, 100, 102, 100, 102, 100, 102], "POSITIVE")
    # trending: strictly rising by a steady step -> returns same sign, persistent
    neg = _mk([100, 101, 102.5, 104, 106, 108.5, 111, 114], "NEGATIVE")
    ac = autocorr_by_regime({"pos": pos, "neg": neg})
    assert ac["POSITIVE"]["autocorr"] < 0      # mean-reversion
    assert ac["NEGATIVE"]["autocorr"] > 0      # momentum


def test_realized_vol_buckets_by_prev_regime():
    pos = _mk([100, 101, 100, 101], "POSITIVE")
    rv = realized_vol_by_regime({"d": pos})
    assert rv["POSITIVE"]["n"] == 3            # 4 rows -> 3 intervals
    assert rv["POSITIVE"]["rv_pct"] > 0


def test_frozen_post_close_rows_dropped(tmp_path):
    # 3 genuine in-session rows then 2 frozen repeats (spot + volume unchanged).
    p = tmp_path / "2026-06-12.csv"
    df = pd.DataFrame({
        "timestamp_utc": ["2026-06-12T04:00:00Z", "2026-06-12T04:15:00Z",
                          "2026-06-12T05:00:00Z", "2026-06-12T10:05:00Z",
                          "2026-06-12T10:20:00Z"],
        "time_ist": ["09:30", "09:45", "10:30", "15:35", "15:50"],
        "spot": [100, 101, 102, 102, 102], "regime": ["POSITIVE"] * 5,
        "call_vol": [10, 20, 30, 30, 30],
    })
    df.to_csv(p, index=False)
    clean = load_clean_day(p)
    # last two are out-of-session (15:35, 15:50 IST) AND frozen -> dropped
    assert list(clean["time_ist"]) == ["09:30", "09:45", "10:30"]


def test_news_day_auto_flagged_on_impact_spike():
    # A spike to >= IMPACT_FLAG in one interval auto-tags the day...
    hot = pd.DataFrame({"news_impact": [0, 0, IMPACT_FLAG + 1, 0], "news_count": [1, 0, 2, 1]})
    assert news_day_flag(hot) is not None
    # ...a quiet day (low impact, low count) is not flagged.
    calm = pd.DataFrame({"news_impact": [0, 1, 0], "news_count": [0, 1, 0]})
    assert news_day_flag(calm) is None
    # Days captured before news logging existed have no columns -> never auto-flagged.
    legacy = pd.DataFrame({"spot": [100, 101]})
    assert news_day_flag(legacy) is None


def test_load_event_days_manual_override(tmp_path):
    p = tmp_path / "event_days.txt"
    p.write_text("# comment line\n2026-06-08  war: missile strikes\n\n"
                 "2026-06-10 ceasefire talks\n", encoding="utf-8")
    ev = load_event_days(p)
    assert len(ev) == 2
    assert ev["2026-06-08"].startswith("war")
    assert ev["2026-06-10"] == "ceasefire talks"
    assert load_event_days(tmp_path / "missing.txt") == {}   # absent file -> {}
