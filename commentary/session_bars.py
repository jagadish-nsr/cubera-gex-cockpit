"""commentary/session_bars.py — Session-bound 1m bars and the futures basis price.

Why this exists (25-Nov-2026 commentary audit):
  * The 1m query took the 60 most recent bars with no session bound. At 09:15 those
    were 20-Nov 14:38-14:52, so the "Opening 15m Range" was yesterday's afternoon.
  * Index spot was compared with a futures VWAP. Spot and futures differ by the
    basis, so the stretch had the wrong size and sometimes the wrong sign.

Rules:
  * Session bars are 1m bars whose IST start is on the as_of IST date and at or
    after 09:15 and at or before as_of. Nothing else is a session bar.
  * The basis price for session-structure detectors is the futures close of the last
    session bar. It is the same series as the futures VWAP, so both sides of a
    comparison share one basis. Spot is used only when there are no session bars.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_OPEN = time(9, 15)
ORB_MINUTES = 15


def as_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def bar_start_ist(bar: dict[str, Any]) -> datetime | None:
    """IST start of a bar, or None when the stamp cannot be parsed."""
    raw = bar.get("start_ts")
    if isinstance(raw, datetime):
        return as_ist(raw)
    if isinstance(raw, str) and raw:
        try:
            return as_ist(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def session_bars(bars: list[dict[str, Any]] | None, as_of: datetime) -> list[dict[str, Any]]:
    """Keep only today's session bars (09:15 IST onward, start <= as_of), oldest first."""
    if not bars:
        return []
    as_of_ist = as_ist(as_of)
    day = as_of_ist.date()
    kept: list[tuple[datetime, dict[str, Any]]] = []
    for bar in bars:
        start = bar_start_ist(bar)
        if start is None:
            continue
        if start.date() != day:
            continue
        if start.timetz().replace(tzinfo=None) < SESSION_OPEN:
            continue
        if start > as_of_ist:
            continue
        kept.append((start, bar))
    kept.sort(key=lambda pair: pair[0])
    return [bar for _, bar in kept]


def session_open_iso(as_of: datetime) -> str:
    """ISO stamp of 09:15 IST on as_of's date, for an SQL lower bound on start_ts."""
    day = as_ist(as_of).date()
    return datetime.combine(day, SESSION_OPEN, tzinfo=IST).isoformat()


def futures_basis_price(pit_view: Any) -> float:
    """Futures close of the last session bar, or spot when there is none."""
    candles = getattr(pit_view, "candles_1m", None) or []
    if candles:
        try:
            close = float(candles[-1].get("close") or 0.0)
        except (TypeError, ValueError, AttributeError):
            close = 0.0
        if close > 0:
            return close
    return float(pit_view.spot)
