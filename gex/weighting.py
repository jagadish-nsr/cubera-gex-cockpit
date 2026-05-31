"""Expiry weighting for GEX aggregation.

Near-dated gamma dominates intraday price action, so we optionally weight each
contract's GEX contribution by its time-to-expiry. This is an EXPLICIT, LOGGED,
tunable assumption (CLAUDE.md #5) — never a silent default. Tuning the scheme /
half-life belongs in the offline research loop, not an in-app optimiser.

Schemes (weight w applied to each contract's dollar GEX):
  'equal'   : w = 1                              (all expiries equal)
  'exp'     : w = 0.5 ** (days / halflife)       (exponential decay by days)
  'inverse' : w = 1 / max(days, floor_days)      (1/time)
  'nearest' : w = 1 for the nearest expiry in the set, else 0

Note: a single-expiry chain gets a uniform weight, which does not change the
walls or flip (a common scalar cancels in argmax / the zero-crossing). Weighting
only bites when multiple expiries are aggregated.
"""

from __future__ import annotations

import numpy as np


def expiry_weights(
    days_to_expiry,
    scheme: str = "equal",
    halflife_days: float = 5.0,
    floor_days: float = 0.5,
):
    """Vectorised weights for an array of days-to-expiry."""
    d = np.maximum(np.asarray(days_to_expiry, dtype=float), 0.0)
    if scheme == "equal":
        return np.ones_like(d)
    if scheme == "exp":
        return 0.5 ** (d / halflife_days)
    if scheme == "inverse":
        return 1.0 / np.maximum(d, floor_days)
    if scheme == "nearest":
        dmin = d.min()
        return (d <= dmin + 1e-9).astype(float)
    raise ValueError(
        f"unknown EXPIRY_WEIGHTING {scheme!r}; use equal|exp|inverse|nearest"
    )
