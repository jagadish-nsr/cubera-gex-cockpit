"""Multi-day magnet drift — the dealer-positioning TREND, sign-free.

The magnet (peak |net_gex| strike) marks where dealer gamma — i.e. big-player
option positioning — concentrates. Tracking how it MIGRATES across days reveals the
positioning drift, and comparing today's point-in-time magnet to the last few days'
anchor says whether positioning is stable, trending, or repositioning.

All of this is SIGN-FREE: the magnet location does not depend on the (unvalidated)
dealer-sign convention, so the drift read is immune to that landmine. Pure
functions here; the reader (dashboard) supplies the daily magnets + today's magnet.
Decision-support only.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd


def _clean_day(df: pd.DataFrame) -> pd.DataFrame:
    """Same hygiene as scripts/analyze_timeseries.load_clean_day: keep only
    in-session rows and drop frozen post-close repeats (spot AND volume unchanged),
    so a stale/frozen-feed day can't corrupt the magnet trend."""
    from app.timeseries import in_nse_session

    if df is None or df.empty or "timestamp_utc" not in df.columns:
        return df if df is not None else pd.DataFrame()

    def _p(t):
        try:
            return datetime.strptime(str(t), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None

    keep = df["timestamp_utc"].apply(lambda t: (_p(t) is not None) and in_nse_session(_p(t)))
    df = df[keep].reset_index(drop=True)
    if df.empty or "spot" not in df.columns or "call_vol" not in df.columns:
        return df
    frozen = (df["spot"].diff() == 0) & (df["call_vol"].diff() == 0)
    return df[~frozen].reset_index(drop=True)


def modal_magnet(df: pd.DataFrame) -> float | None:
    """A day's representative magnet = the MODE of its intraday magnet readings
    (more robust than the last/close reading to a single end-of-day wobble)."""
    if df is None or df.empty or "magnet" not in df.columns:
        return None
    m = pd.to_numeric(df["magnet"], errors="coerce").dropna()
    return float(m.mode().iloc[0]) if len(m) else None


def recent_daily_magnets(symbol: str, before_day: str, n: int = 5) -> list[tuple[str, float]]:
    """The modal magnet for up to the last ``n`` captured days STRICTLY BEFORE
    ``before_day`` (so today's own partial data never leaks into its own trend).
    Applies the standard hygiene (in-session, drop frozen, >=2 usable rows) so a
    stale day is skipped. Reads the per-index time-series journal."""
    from app.timeseries import available_days, load_day

    out: list[tuple[str, float]] = []
    for d in available_days(symbol):
        if d >= before_day:
            continue
        clean = _clean_day(load_day(d, symbol))
        if len(clean) < 2:
            continue
        mm = modal_magnet(clean)
        if mm is not None:
            out.append((d, mm))
    return out[-n:]


def classify_trend(magnets: list[float], current: float | None,
                   tol_frac: float = 0.0025) -> dict:
    """Classify the dealer-positioning trend from recent daily magnets + today's
    point-in-time magnet.

    ``magnets`` are the recent daily magnets in chronological order; ``current`` is
    today's magnet. ``tol_frac`` sets the "meaningful move" band as a fraction of
    the magnet level (default 0.25% ~ a strike or so). Returns a dict with the
    trend (rising/falling/flat), the drift in points, the recent anchor (median),
    and where today sits vs that anchor (+ a repositioning flag when today's magnet
    has jumped well clear of the anchor)."""
    magnets = [float(m) for m in magnets if m is not None and np.isfinite(m)]
    if not magnets:
        return {"trend": "unknown", "n": 0}

    first, last = magnets[0], magnets[-1]
    drift = last - first
    anchor = float(np.median(magnets))
    tol = max(anchor * tol_frac, 1.0)
    trend = "rising" if drift > tol else "falling" if drift < -tol else "flat"

    res = {"trend": trend, "drift_pts": drift, "anchor": anchor,
           "first": first, "last": last, "n": len(magnets)}
    if current is not None and np.isfinite(current):
        gap = float(current) - anchor
        res.update({
            "current": float(current), "current_gap": gap,
            "current_state": "above" if gap > tol else "below" if gap < -tol else "at",
            "repositioning": abs(gap) > 2 * tol,   # today jumped clear of the anchor
        })
    return res
