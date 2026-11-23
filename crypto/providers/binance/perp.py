"""Binance perp stub for CVD/funding — mock overlay until live WS."""

from __future__ import annotations

from crypto.config import get_asset_config
from crypto.providers.mock.perp import MockPerpProvider
from crypto.providers.types import PerpSnapshot


def _underlying_from_symbol(symbol: str) -> str:
    raw = (symbol or "BTC").upper().replace("-PERPETUAL", "").replace("-PERP", "")
    raw = raw.replace("USDT", "").replace("USD", "")
    return (raw.split("-")[0] if raw else "BTC") or "BTC"


class BinancePerpProvider:
    name = "binance"

    def __init__(self) -> None:
        self._fallback = MockPerpProvider()

    async def fetch_perp(self, symbol: str = "BTC-PERP") -> PerpSnapshot:
        underlying = _underlying_from_symbol(symbol)
        snap = await self._fallback.fetch_perp(symbol)
        snap.venue = "binance"
        snap.source = "binance-stub"
        asset = get_asset_config(underlying)
        snap.symbol = str(asset.get("binance_perp") or f"{underlying}USDT")
        return snap
