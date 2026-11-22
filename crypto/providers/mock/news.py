"""Mock intelligence feed (IMMINENT / BREAKING / WATCH)."""

from __future__ import annotations

from crypto.providers.mock.fixtures import load_fixture
from crypto.providers.types import NewsEvent, utcnow


class MockNewsProvider:
    name = "mock"

    async def fetch_events(self, limit: int = 20) -> list[NewsEvent]:
        rows = load_fixture().get("news") or []
        events = [
            NewsEvent(
                event_id=r["event_id"],
                title=r["title"],
                source=r["source"],
                tier=r["tier"],
                score=float(r["score"]),
                assets=list(r.get("assets") or []),
                url=r.get("url"),
                published_at=utcnow(),
                impact_note=r.get("impact_note") or "",
            )
            for r in rows[:limit]
        ]
        return events
