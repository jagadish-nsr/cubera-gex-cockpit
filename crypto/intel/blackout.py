"""News / calendar blackout detection for the signal veto path.

Hard veto when an IMMINENT (or any) event's blackout window is active, or
when ``occurs_at`` falls inside the next ``window_minutes`` (default 30,
matching the design-plan hard veto).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from crypto.intel.schema import TIER_IMMINENT, IntelEvent

# Design-plan hard veto: scheduled macro / maintenance within 30 minutes
DEFAULT_BLACKOUT_WINDOW_MINUTES = 30


def blackout_active(
    events: Iterable[IntelEvent],
    now: datetime | None = None,
    *,
    window_minutes: int = DEFAULT_BLACKOUT_WINDOW_MINUTES,
) -> bool:
    """True when any event forces a news blackout at ``now``."""
    return blackout_summary(events, now, window_minutes=window_minutes)["active"]


def blackout_summary(
    events: Iterable[IntelEvent],
    now: datetime | None = None,
    *,
    window_minutes: int = DEFAULT_BLACKOUT_WINDOW_MINUTES,
) -> dict:
    """Return active flag, reasons, and the soonest blackout_until."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    reasons: list[str] = []
    until: Optional[datetime] = None
    horizon = now + timedelta(minutes=window_minutes)

    for e in events:
        hit = False
        why = ""

        start = _aware(e.blackout_start) if e.blackout_start else None
        end = _aware(e.blackout_until) if e.blackout_until else None
        occurs = _aware(e.occurs_at) if e.occurs_at else None

        # Explicit window currently covering now
        if end is not None and end > now:
            if start is None or start <= now:
                hit = True
                why = f"{e.event_id}: blackout window active until {end.isoformat()}"
                until = end if until is None or end < until else until

        # Occurs inside the hard-veto horizon (even if tier not yet IMMINENT)
        if not hit and occurs is not None and now <= occurs <= horizon:
            # Prefer scheduled / high-impact types
            if e.tier == TIER_IMMINENT or e.event_type in {
                "macro",
                "maintenance",
                "outage",
                "expiry",
            }:
                hit = True
                why = (
                    f"{e.event_id}: {e.event_type} occurs_at "
                    f"{occurs.isoformat()} within {window_minutes}m"
                )
                est_end = end or (occurs + timedelta(minutes=window_minutes))
                until = est_end if until is None or est_end < until else until

        # IMMINENT tier with score floor (legacy path still honored)
        if not hit and e.tier == TIER_IMMINENT and e.score >= 0.9 and occurs is None and end is None:
            hit = True
            why = f"{e.event_id}: IMMINENT high-score without schedule stamps"

        if hit:
            reasons.append(why or e.title)

    return {
        "active": bool(reasons),
        "reasons": reasons,
        "window_minutes": window_minutes,
        "blackout_until": until.isoformat() if until else None,
        "note": "Advisory blackout — forces NO-GO; never places trades.",
    }


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
