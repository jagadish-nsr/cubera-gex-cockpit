"""Expiry-day and blast-study flags for a stored session.

These read the feature cache and the blast study file. They do not play a
session back or write a journal.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=8)
def _expiry_cache(symbol: str):
    """(all_days, expiry_days) from the 5-min feature cache. Expiry means open dte < 0.6."""
    try:
        from app.intraday_features import load_features
        frame = load_features(symbol)
        if frame is None or getattr(frame, "empty", True):
            return frozenset(), frozenset()
        dte0 = frame.groupby("day")["dte"].first()
        alld = frozenset(str(d) for d in dte0.index)
        expd = frozenset(str(d) for d, v in dte0.items() if float(v) < 0.6)
        return alld, expd
    except Exception:  # noqa: BLE001
        return frozenset(), frozenset()


def is_expiry_day(symbol: str, day: str) -> bool:
    """True when ``day`` is a 0-DTE expiry. Feature-cache dte first, then the weekly calendar."""
    if not day:
        return False
    alld, expd = _expiry_cache(symbol)
    if str(day) in alld:
        return str(day) in expd
    try:
        from datetime import datetime
        from data.expiry import next_weekly_expiry
        dd = datetime.strptime(str(day), "%Y-%m-%d").date()
        return next_weekly_expiry(dd) == dd
    except Exception:  # noqa: BLE001
        return False


def _label_frame(df) -> dict:
    """{day: 'TRUE'|'FAKE'} from a blast study frame. Afternoon 0-DTE crossings only."""
    aft = df[(df["dte"] < 0.6) & (df["tod"] >= 13 * 60 + 45)]
    if aft.empty:
        return {}
    grouped = aft.groupby(aft["day"].astype(str))["k_true"]
    return {d: ("TRUE" if s.notna().any() else "FAKE") for d, s in grouped}


@functools.lru_cache(maxsize=8)
def _blast_labels(symbol: str):
    """Study tags for expiry days. Empty when the study file is absent."""
    from pathlib import Path
    try:
        import pandas as pd
        path = Path(__file__).resolve().parents[1] / "data" / "journal" / "blast_ml" / f"{symbol}.parquet"
        if not path.exists():
            return {}
        return _label_frame(pd.read_parquet(path, columns=["day", "dte", "tod", "k_true"]))
    except Exception:  # noqa: BLE001
        return {}


def blast_label(symbol: str, day: str) -> str | None:
    """Study tag for an expiry day, or None when the day is unknown."""
    return _blast_labels(symbol).get(str(day)) if day else None
