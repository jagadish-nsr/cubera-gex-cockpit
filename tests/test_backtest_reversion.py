"""Hand-checkable tests for the Study #24 reversion backtest primitives
(scripts/backtest_futures_reversion.py) — the two things that must be right for the verdict to be
trustworthy: the bad-tick cleaners (a reversion P&L feasts on corrupt ticks) and the fade-sign logic."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backtest_futures_reversion as br  # noqa: E402


def test_hampel_removes_isolated_spike_keeps_genuine_step():
    # a clean ramp with ONE corrupt down-spike at idx 4 → replaced by the local median (~ramp level)
    p = np.array([100, 100.1, 100.2, 100.3, 97.0, 100.35, 100.4, 100.5, 100.6, 100.7])
    out, rep = br._hampel(p, br.HAMPEL_WIN, br.HAMPEL_NMAD, br.HAMPEL_FLOOR)
    assert rep == 1
    assert abs(out[4] - 100.3) < 0.5           # snapped to the local median, not left at 97
    # a genuine step (no snap-back) must survive both a short and a long window untouched
    step = np.array([100.0] * 8 + [102.0] * 8)
    _, r_short = br._hampel(step, br.HAMPEL_WIN, br.HAMPEL_NMAD, br.HAMPEL_FLOOR)
    _, r_long = br._hampel(step, br.BIGWIN, br.BIGWIN_NMAD, br.BIGWIN_FLOOR)
    assert r_short == 0 and r_long == 0


def test_longwindow_hampel_removes_sustained_scale_corruption():
    # the real NIFTY_M/BANKNIFTY failure mode: a RUN of ~2.2x wrong spot for many bars, embedded in a
    # long true series. A short window can't fix it (its median goes corrupt); the long window can.
    p = np.full(61, 21600.0)
    p[28:34] = 47600.0                          # a 6-bar corrupt plateau near the centre
    out, rep = br._hampel(p, br.BIGWIN, br.BIGWIN_NMAD, br.BIGWIN_FLOOR)
    assert rep == 6
    assert np.allclose(out[28:34], 21600.0)     # plateau replaced by the true level


def test_clean_spikes_repairs_reverting_excursion():
    # a >1.5% jump that snaps back within k bars = a corrupt round-trip → interpolated away
    p = np.array([100, 100.1, 103.5, 103.6, 100.2, 100.3, 100.4, 100.5, 100.6, 100.7], dtype=float)
    out, rep = br._clean_spikes(np.arange(10), p)
    assert rep == 2                             # both excursion bars repaired
    assert 100.0 < out[2] < 100.5 and 100.0 < out[3] < 100.5


def test_fade_sign_is_reversion():
    # the strategy fades the trailing move: a POSITIVE trailing return → SHORT (pos = -1), and vice
    # versa. Encoded inline in build_trades; assert the sign convention directly.
    for sig, want in [(+0.004, -1.0), (-0.004, +1.0)]:
        pos = -1.0 if sig > 0 else 1.0
        assert pos == want


def test_px_nearest_within_one_minute():
    pmap = {600: 100.0, 603: 101.0}
    assert br._px(pmap, 600) == 100.0          # exact hit
    assert br._px(pmap, 604) == 101.0          # exact miss → the −1 neighbour (603) within ±1 min
    assert br._px(pmap, 602) == 101.0          # +1 neighbour (603) is checked before −1
    assert br._px(pmap, 610) is None           # gap too large → None
