"""Normalized market-intelligence event schema.

One schema for headlines, calendar stubs, and venue maintenance.
Keyword rules are the default; optional Gemma fills event_type / assets /
venues when ``intel.gemma`` is on (schema-validated, timeout → keywords).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

# Alert tiers (design plan)
TIER_IMMINENT = "IMMINENT"
TIER_BREAKING = "BREAKING"
TIER_WATCH = "WATCH"
TIERS = frozenset({TIER_IMMINENT, TIER_BREAKING, TIER_WATCH})

# Keyword-classified event types
EVENT_TYPES = frozenset(
    {
        "hack",
        "listing",
        "delisting",
        "regulation",
        "etf_flow",
        "macro",
        "outage",
        "maintenance",
        "liquidation",
        "expiry",
        "unlock",
        "funding",
        "other",
    }
)

# Score → tier thresholds (unschedules). Scheduled windows force IMMINENT.
BREAKING_SCORE = 0.72
WATCH_SCORE = 0.0  # everything else that passes ingest


@dataclass
class IntelEvent:
    """Normalized event persisted to ``events`` and shown in the cockpit feed."""

    event_id: str
    title: str
    source: str
    tier: str  # IMMINENT | BREAKING | WATCH
    score: float
    event_type: str = "other"
    assets: list[str] = field(default_factory=list)
    venues: list[str] = field(default_factory=list)
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    ingested_at: Optional[datetime] = None
    # Scheduled / maintenance windows (UTC)
    occurs_at: Optional[datetime] = None
    blackout_start: Optional[datetime] = None
    blackout_until: Optional[datetime] = None
    novelty: float = 1.0  # 1.0 = first sighting; decays on near-duplicate
    source_reliability: float = 0.5
    prior_move_proxy: float = 0.5
    market_confirmation: float = 0.0  # 0..1; optional tape confirm
    impact_note: str = ""
    engine_action: str = ""  # blackout | size_down | advisory | none
    raw: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.tier not in TIERS:
            raise ValueError(f"bad tier {self.tier!r}")
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score out of range: {self.score}")
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"bad event_type {self.event_type!r}")


def tier_for_score(
    score: float,
    *,
    scheduled_imminent: bool = False,
) -> str:
    """Map impact score (+ schedule flag) to alert tier."""
    if scheduled_imminent:
        return TIER_IMMINENT
    if score >= BREAKING_SCORE:
        return TIER_BREAKING
    return TIER_WATCH


def event_to_dict(e: IntelEvent) -> dict[str, Any]:
    """JSON-safe dict for API / persistence payload."""

    def _dt(v: Optional[datetime]) -> Optional[str]:
        return v.isoformat() if isinstance(v, datetime) else None

    d = asdict(e)
    for k in (
        "published_at",
        "ingested_at",
        "occurs_at",
        "blackout_start",
        "blackout_until",
    ):
        d[k] = _dt(getattr(e, k))
    return d


def from_news_event_dict(row: dict[str, Any], *, ingested_at: datetime) -> IntelEvent:
    """Build from fixture / provider dict before classify+score (partial OK)."""
    return IntelEvent(
        event_id=str(row["event_id"]),
        title=str(row["title"]),
        source=str(row.get("source") or "unknown"),
        tier=str(row.get("tier") or TIER_WATCH),
        score=float(row.get("score") or 0.0),
        event_type=str(row.get("event_type") or "other"),
        assets=list(row.get("assets") or []),
        venues=list(row.get("venues") or []),
        url=row.get("url"),
        published_at=_parse_dt(row.get("published_at")),
        ingested_at=ingested_at,
        occurs_at=_parse_dt(row.get("occurs_at")),
        blackout_start=_parse_dt(row.get("blackout_start")),
        blackout_until=_parse_dt(row.get("blackout_until")),
        novelty=float(row.get("novelty") or 1.0),
        source_reliability=float(row.get("source_reliability") or 0.5),
        prior_move_proxy=float(row.get("prior_move_proxy") or 0.5),
        market_confirmation=float(row.get("market_confirmation") or 0.0),
        impact_note=str(row.get("impact_note") or ""),
        engine_action=str(row.get("engine_action") or ""),
        raw=dict(row.get("raw") or {}),
    )


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    from datetime import timezone

    s = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
