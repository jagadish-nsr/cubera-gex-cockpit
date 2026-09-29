"""analytics/zscore.py — Rolling Z-score helpers for positioning analytics.

Pure functions: no network, no streamlit. Input is a plain list of floats.
Used by the NSE Positioning tab to normalise FII / Client net-futures exposure
against a trailing window so the signal is comparable across different market regimes.

Separation guarantee: imports nothing from the data or app layers.
"""

from __future__ import annotations

import math


# Minimum history required before emitting a Z-score (< this → "insufficient_history")
MIN_HISTORY = 20


def rolling_zscore(
    series: list[float | None],
    window: int = 60,
    min_history: int = MIN_HISTORY,
) -> float | str:
    """Z-score of the LAST value in ``series`` against the prior ``window`` values.

    Args:
        series:      Chronological list of values (oldest first). The LAST entry
                     is scored against the prior ``window`` entries (not including itself).
        window:      How many prior sessions to use for μ / σ.
        min_history: Minimum number of prior values required. If fewer are available,
                     returns the string ``"insufficient_history"`` instead of a float.

    Returns:
        float — Z-score, or ``"insufficient_history"`` when history is too short.

    Interpretation:
        |Z| < 1.0  : normal
        |Z| ≥ 1.5  : noteworthy — positioning is stretched relative to recent history
        |Z| ≥ 2.0  : extreme — rare; high-conviction contrarian signal
    """
    clean = [float(v) for v in series if v is not None and math.isfinite(float(v))]
    if len(clean) < 2:
        return "insufficient_history"

    current = clean[-1]
    history = clean[-(window + 1):-1]   # up to `window` prior values, excluding current

    if len(history) < min_history:
        return "insufficient_history"

    n = len(history)
    mu = sum(history) / n
    variance = sum((x - mu) ** 2 for x in history) / n
    sigma = math.sqrt(variance)

    if sigma == 0.0:
        return 0.0  # all prior values identical (e.g. first days of data)

    return (current - mu) / sigma


def zscore_label(z: float | str) -> str:
    """Human-readable label for a Z-score value.

    Returns one of: 'extreme long', 'stretched long', 'normal', 'stretched short',
    'extreme short', or 'insufficient history'.
    """
    if isinstance(z, str):
        return "insufficient history"
    if z >= 2.0:
        return "extreme long"
    if z >= 1.5:
        return "stretched long"
    if z <= -2.0:
        return "extreme short"
    if z <= -1.5:
        return "stretched short"
    return "normal"


def build_zscore_series(
    days: list[str],
    load_fn,
    participant: str,
    field: str,
    window: int = 60,
    min_history: int = MIN_HISTORY,
) -> list[dict]:
    """Build a per-day Z-score series for one participant + field combination.

    Args:
        days:        Sorted list of date strings (YYYY-MM-DD) with available data.
        load_fn:     Callable(date_ist: str) → parsed dict | None. Used to load each day.
        participant: e.g. "FII", "Client".
        field:       e.g. "net_fut" — must be a key in the derive_signals per-participant dict.
        window:      Rolling window for Z-score.
        min_history: Minimum prior sessions before emitting a numeric Z-score.

    Returns:
        Chronological list of dicts:
            {"date": str, "value": float | None, "zscore": float | "insufficient_history"}
    """
    from app.participant_oi import derive_signals

    raw_series: list[float | None] = []
    out: list[dict] = []

    for d in days:
        parsed = load_fn(d)
        if parsed is None:
            continue
        sigs = (derive_signals(parsed) or {}).get("per_participant", {})
        val = sigs.get(participant, {}).get(field)
        raw_series.append(float(val) if val is not None else None)
        z = rolling_zscore(raw_series, window=window, min_history=min_history)
        out.append({"date": d, "value": val, "zscore": z})

    return out
