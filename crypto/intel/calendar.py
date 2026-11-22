"""Macro calendar + venue maintenance stubs.

Generates IMMINENT-capable windows for CPI / FOMC / options expiry /
Delta maintenance. Relative to ``now`` so tests stay point-in-time correct.
No live exchange status WebSocket yet — stubs only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from crypto.intel.schema import IntelEvent

# Default stub offsets from ``now`` (minutes). Far enough that the default
# mock cockpit still shows GO (blackout window = 30m), but one item sits
# inside the 60m IMMINENT alert horizon for the feed.
DEFAULT_STUBS: list[dict[str, Any]] = [
    {
        "event_id": "cal-cpi-stub",
        "title": "US CPI release window (calendar stub)",
        "event_type": "macro",
        "assets": ["BTC", "ETH", "SOL"],
        "venues": [],
        "offset_minutes": 45,  # IMMINENT alert, outside 30m veto blackout
        "blackout_radius_minutes": 30,
        "impact_note": "Macro print stub — blackout if inside 30m of release.",
    },
    {
        "event_id": "cal-fomc-stub",
        "title": "FOMC decision window (calendar stub)",
        "event_type": "macro",
        "assets": ["BTC", "ETH", "SOL"],
        "venues": [],
        "offset_minutes": 180,
        "blackout_radius_minutes": 30,
        "impact_note": "Scheduled FOMC stub — watch, not yet in blackout.",
    },
    {
        "event_id": "cal-deribit-expiry-stub",
        "title": "Deribit options expiry (08:00 UTC clock stub)",
        "event_type": "expiry",
        "assets": ["BTC", "ETH", "SOL"],
        "venues": ["deribit"],
        "offset_minutes": 360,
        "blackout_radius_minutes": 45,
        "impact_note": "Near-dated gamma peaks at expiry; size down into the window.",
    },
    {
        "event_id": "maint-delta-stub",
        "title": "Delta Exchange India maintenance window (status stub)",
        "event_type": "maintenance",
        "assets": ["BTC", "ETH", "SOL"],
        "venues": ["delta"],
        "offset_minutes": 240,
        "blackout_radius_minutes": 60,
        "impact_note": "Venue maintenance stub — veto when inside blackout window.",
        "source": "delta-status-stub",
    },
]


def build_calendar_stubs(
    now: datetime | None = None,
    *,
    stubs: Optional[list[dict[str, Any]]] = None,
    include_near_blackout: bool = False,
) -> list[IntelEvent]:
    """Return calendar/maintenance IntelEvents anchored to ``now``.

    When ``include_near_blackout`` is True, adds a maintenance window that
    starts immediately (for tests / forced veto demos).
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    rows = list(stubs if stubs is not None else DEFAULT_STUBS)
    if include_near_blackout:
        rows = [
            {
                "event_id": "maint-delta-now",
                "title": "Delta maintenance ACTIVE (forced stub)",
                "event_type": "maintenance",
                "assets": ["BTC"],
                "venues": ["delta"],
                "offset_minutes": 5,
                "blackout_radius_minutes": 30,
                "impact_note": "Active maintenance — hard veto blackout.",
                "source": "delta-status-stub",
            },
            *rows,
        ]

    out: list[IntelEvent] = []
    for r in rows:
        offset = int(r.get("offset_minutes") or 60)
        radius = int(r.get("blackout_radius_minutes") or 30)
        occurs = now + timedelta(minutes=offset)
        start = occurs - timedelta(minutes=radius)
        until = occurs + timedelta(minutes=radius)
        out.append(
            IntelEvent(
                event_id=str(r["event_id"]),
                title=str(r["title"]),
                source=str(r.get("source") or "calendar-stub"),
                tier="WATCH",  # scored later
                score=0.0,
                event_type=str(r.get("event_type") or "macro"),
                assets=list(r.get("assets") or []),
                venues=list(r.get("venues") or []),
                published_at=now,
                ingested_at=now,
                occurs_at=occurs,
                blackout_start=start,
                blackout_until=until,
                novelty=1.0,
                source_reliability=0.75,
                prior_move_proxy=0.7 if r.get("event_type") == "macro" else 0.55,
                impact_note=str(r.get("impact_note") or ""),
                engine_action="",
            )
        )
    return out
