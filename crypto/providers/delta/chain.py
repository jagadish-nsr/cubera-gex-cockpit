"""Delta option-chain stub. Falls back to mock until public chain is wired."""

from __future__ import annotations

from crypto.providers.mock.chain import MockOptionChainProvider
from crypto.providers.types import OptionChainSnapshot


class DeltaOptionChainProvider:
    name = "delta"

    def __init__(self) -> None:
        self._fallback = MockOptionChainProvider()

    async def fetch_chain(self, underlying: str = "BTC") -> OptionChainSnapshot:
        snap = await self._fallback.fetch_chain(underlying)
        snap.source = "delta-stub"
        return snap
