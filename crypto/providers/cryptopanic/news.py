"""CryptoPanic stub — returns mock events until token + flag are set."""

from __future__ import annotations

from crypto.providers.mock.news import MockNewsProvider
from crypto.providers.types import NewsEvent


class CryptoPanicNewsProvider:
    name = "cryptopanic"

    def __init__(self) -> None:
        self._fallback = MockNewsProvider()

    async def fetch_events(self, limit: int = 20) -> list[NewsEvent]:
        events = await self._fallback.fetch_events(limit)
        for e in events:
            e.source = f"cryptopanic-stub/{e.source}"
        return events
