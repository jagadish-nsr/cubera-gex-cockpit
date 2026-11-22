"""Mock perp provider with CVD / funding / imbalance fixtures."""

from __future__ import annotations

from crypto.providers.mock.fixtures import load_fixture
from crypto.providers.types import PerpSnapshot, utcnow


class MockPerpProvider:
    name = "mock"

    async def fetch_perp(self, symbol: str = "BTC-PERP") -> PerpSnapshot:
        # Map BTC-PERP / ETH-PERPETUAL / SOL → fixture underlying
        raw = (symbol or "BTC").upper().replace("-PERPETUAL", "").replace("-PERP", "")
        underlying = raw.split("-")[0] if raw else "BTC"
        p = load_fixture(underlying)["perp"]
        return PerpSnapshot(
            venue=p["venue"],
            symbol=symbol or p["symbol"],
            mark=float(p["mark"]),
            index=float(p["index"]),
            funding_rate=float(p["funding_rate"]),
            open_interest=float(p["open_interest"]),
            cvd_1m=float(p["cvd_1m"]),
            cvd_5m=float(p["cvd_5m"]),
            cvd_15m=float(p["cvd_15m"]),
            book_imbalance=float(p["book_imbalance"]),
            fetched_at=utcnow(),
            source="mock",
        )
