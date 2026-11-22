"""Binance perp stub for CVD/funding — mock overlay until live WS."""

from __future__ import annotations

from crypto.providers.mock.perp import MockPerpProvider
from crypto.providers.types import PerpSnapshot


class BinancePerpProvider:
    name = "binance"

    def __init__(self) -> None:
        self._fallback = MockPerpProvider()

    async def fetch_perp(self, symbol: str = "BTC-PERP") -> PerpSnapshot:
        snap = await self._fallback.fetch_perp(symbol)
        snap.venue = "binance"
        snap.source = "binance-stub"
        snap.symbol = "BTCUSDT"
        return snap
