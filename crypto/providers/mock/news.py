"""Mock intelligence feed — fixture headlines for offline UI review."""

from __future__ import annotations

from crypto.config import get_active_underlying
from crypto.providers.mock.fixtures import load_fixture, parse_ts
from crypto.providers.types import NewsEvent, utcnow


class MockNewsProvider:
    name = "mock"

    async def fetch_events(self, limit: int = 20) -> list[NewsEvent]:
        underlying = get_active_underlying()
        rows = load_fixture(underlying).get("news") or []
        now = utcnow()
        events: list[NewsEvent] = []
        for r in rows[:limit]:
            events.append(
                NewsEvent(
                    event_id=r["event_id"],
                    title=r["title"],
                    source=r["source"],
                    tier=r.get("tier") or "WATCH",
                    score=float(r.get("score") or 0.0),
                    assets=list(r.get("assets") or []),
                    url=r.get("url"),
                    published_at=parse_ts(r.get("published_at")) if r.get("published_at") else now,
                    impact_note=r.get("impact_note") or "",
                    event_type=r.get("event_type") or "other",
                    venues=list(r.get("venues") or []),
                    occurs_at=parse_ts(r["occurs_at"]) if r.get("occurs_at") else None,
                    blackout_start=parse_ts(r["blackout_start"]) if r.get("blackout_start") else None,
                    blackout_until=parse_ts(r["blackout_until"]) if r.get("blackout_until") else None,
                    novelty=float(r.get("novelty") or 1.0),
                    source_reliability=float(r.get("source_reliability") or 0.0),
                    prior_move_proxy=float(r.get("prior_move_proxy") or 0.0),
                    market_confirmation=float(r.get("market_confirmation") or 0.0),
                    raw={"provider": "mock", "mode": "fixture", "underlying": underlying},
                )
            )
        return events
