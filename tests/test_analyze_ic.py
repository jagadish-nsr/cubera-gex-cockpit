"""Hand-checkable tests for the IC analyser.

Constructed series with KNOWN answers:
- forward_index pairs t with the row nearest t+horizon, within tolerance.
- A day where spot reverts toward a fixed magnet => magnet_gap predicts the
  forward move with POSITIVE IC (~+1).
- Anchors (open_gap, vwap_gap) are computed from the price path itself.
"""

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from scripts.analyze_ic import (build_day_samples, forward_index, session_split,
                                 spearman_ic)


def _utc(h, m):
    return datetime(2026, 6, 12, h, m, tzinfo=timezone.utc)


def test_forward_index_matches_nearest_within_tol():
    # rows every 15 min; horizon 60 -> pair i with i+4 (exactly on target).
    times = [_utc(3, 45 + 15 * k) if 45 + 15 * k < 60 else _utc(4, (45 + 15 * k) % 60)
             for k in range(8)]
    times = [_utc(3, 45), _utc(4, 0), _utc(4, 15), _utc(4, 30), _utc(4, 45),
             _utc(5, 0), _utc(5, 15), _utc(5, 30)]
    assert forward_index(times, 0, 60) == 4        # 03:45 -> 04:45
    assert forward_index(times, 3, 60) == 7        # 04:30 -> 05:30
    assert forward_index(times, 6, 60) is None     # 05:15 + 60 = 06:15, none within tol


def _day(spots, magnets, t0=(3, 45)):
    n = len(spots)
    times = []
    h, m = t0
    for _ in range(n):
        times.append(f"2026-06-12T{h:02d}:{m:02d}:00Z")
        m += 15
        if m >= 60:
            h += 1
            m -= 60
    return pd.DataFrame({
        "timestamp_utc": times,
        "date_ist": ["2026-06-12"] * n,
        "spot": spots, "magnet": magnets,
        "flip": [np.nan] * n, "net_gex": list(range(n)),
    })


def test_magnet_gap_predicts_reversion_positive_ic():
    # Magnet fixed at 100; spot half-reverts toward it each step (never landing on
    # it, so no zero-signal ties). gap_t=(100-spot_t)/spot_t and the next-step move
    # are both monotone in (100-spot_t) => Spearman IC ~ +1.
    spots, s0 = [], 116.0
    for _ in range(10):
        spots.append(s0)
        s0 = 100 + 0.5 * (s0 - 100)        # half-revert toward the magnet
    s = build_day_samples(_day(spots, [100] * len(spots)), horizon_min=15.0)
    ic, p, n = spearman_ic(s, "magnet_gap")
    assert n >= 8
    assert ic > 0.95           # near-perfect: magnet gap predicts the pull back


def test_open_and_vwap_anchor_columns():
    spots = [100, 110, 90, 100]
    s = build_day_samples(_day(spots, [100] * 4), horizon_min=15.0)
    first = s.iloc[0]
    # open anchor at row 0: open=100, spot=100 -> 0; vwap (expanding mean)=100 -> 0
    assert abs(first["open_gap"]) < 1e-9 and abs(first["vwap_gap"]) < 1e-9
    # row 1: spot=110, open=100 -> open_gap=(100-110)/110 < 0
    assert s.iloc[1]["open_gap"] < 0


def test_session_split_cutoff():
    # 06:15 UTC = 11:45 IST (morning); 06:45 UTC = 12:15 IST (afternoon).
    df = _day([100, 101, 102, 103, 104, 105], [100] * 6, t0=(6, 15))
    s = build_day_samples(df, horizon_min=15.0)
    morning, afternoon = session_split(s)
    assert (morning["min_ist"] < 12 * 60).all()
    assert (afternoon["min_ist"] >= 12 * 60).all()
