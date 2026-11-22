"""Impact scoring → IMMINENT / BREAKING / WATCH.

score ≈ source_reliability × novelty × prior_move_proxy × (0.5 + 0.5×market_confirmation)
Scheduled windows inside the imminent horizon force IMMINENT regardless of score.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from crypto.intel.schema import (
    TIER_IMMINENT,
    IntelEvent,
    tier_for_score,
)

# Design: IMMINENT = scheduled inside next 60 minutes
IMMINENT_HORIZON_MINUTES = 60


def compute_impact_score(
    *,
    source_reliability: float,
    novelty: float,
    prior_move_proxy: float,
    market_confirmation: float = 0.0,
) -> float:
    """Bounded [0, 1] impact score.

    Core product is reliability × novelty × prior_move. Market confirmation
    is optional and only boosts (never required for BREAKING).
    """
    conf = max(0.0, min(1.0, market_confirmation))
    base = (
        max(0.0, min(1.0, source_reliability))
        * max(0.0, min(1.0, novelty))
        * max(0.0, min(1.0, prior_move_proxy))
    )
    # Confirmation adds up to +25% when tape agrees within ~60s (hook for later).
    scored = base * (1.0 + 0.25 * conf)
    return round(min(1.0, scored), 4)


def is_scheduled_imminent(
    event: IntelEvent,
    now: datetime,
    *,
    horizon_minutes: int = IMMINENT_HORIZON_MINUTES,
) -> bool:
    """True when occurs_at or blackout window intersects [now, now+horizon]."""
    now = _aware(now)
    horizon_end = now + timedelta(minutes=horizon_minutes)

    if event.occurs_at is not None:
        occ = _aware(event.occurs_at)
        if now <= occ <= horizon_end:
            return True

    start = _aware(event.blackout_start) if event.blackout_start else None
    until = _aware(event.blackout_until) if event.blackout_until else None
    if until is not None:
        # Active or starting within horizon
        if until > now and (start is None or start <= horizon_end):
            if start is None or start <= horizon_end:
                # Window overlaps [now, horizon_end]
                win_start = start or now
                if win_start <= horizon_end and until > now:
                    return True
    return False


def score_and_tier(
    event: IntelEvent,
    now: datetime,
    *,
    horizon_minutes: int = IMMINENT_HORIZON_MINUTES,
) -> IntelEvent:
    """Mutate event with score, tier, and engine_action."""
    event.score = compute_impact_score(
        source_reliability=event.source_reliability,
        novelty=event.novelty,
        prior_move_proxy=event.prior_move_proxy,
        market_confirmation=event.market_confirmation,
    )
    scheduled = is_scheduled_imminent(event, now, horizon_minutes=horizon_minutes)
    # Calendar / maintenance types with an occurs_at always get schedule treatment
    if event.event_type in {"macro", "maintenance", "outage", "expiry"} and event.occurs_at:
        scheduled = scheduled or is_scheduled_imminent(
            event, now, horizon_minutes=horizon_minutes
        )

    event.tier = tier_for_score(event.score, scheduled_imminent=scheduled)

    if event.tier == TIER_IMMINENT:
        event.engine_action = "blackout"
        if not event.impact_note:
            event.impact_note = "Scheduled window — signal engine blackout / size down."
    elif event.tier == "BREAKING":
        event.engine_action = "advisory"
        if not event.impact_note:
            event.impact_note = "High-score unscheduled event — advisory only; never auto-trades."
    else:
        event.engine_action = event.engine_action or "none"
        if not event.impact_note:
            event.impact_note = "Digest item."

    event.validate()
    return event


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
