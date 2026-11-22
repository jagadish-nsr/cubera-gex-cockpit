"""Stale-data helpers for the signal path.

Hard veto threshold from the design plan: data older than 2 seconds.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

STALE_THRESHOLD_S = 2.0


def age_seconds(ts: Optional[datetime], *, now: Optional[datetime] = None) -> float:
    if ts is None:
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return max(0.0, (ref - ts).total_seconds())


def is_stale(
    ts: Optional[datetime],
    *,
    threshold_s: float = STALE_THRESHOLD_S,
    now: Optional[datetime] = None,
) -> bool:
    return age_seconds(ts, now=now) > threshold_s
