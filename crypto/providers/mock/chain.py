"""Mock option-chain provider backed by production-shaped fixtures."""

from __future__ import annotations

from crypto.providers.mock.fixtures import load_fixture, parse_ts
from crypto.providers.types import OptionChainSnapshot, OptionContract, utcnow


class MockOptionChainProvider:
    name = "mock"

    async def fetch_chain(self, underlying: str = "BTC") -> OptionChainSnapshot:
        fx = load_fixture(underlying)
        contracts = [
            OptionContract(
                instrument=c["instrument"],
                expiry=parse_ts(c["expiry"]),
                strike=float(c["strike"]),
                option_type=c["option_type"],
                oi=float(c["oi"]),
                iv=float(c["iv"]),
                gamma=float(c["gamma"]),
                delta=float(c["delta"]),
                mid=float(c["mid"]),
                bid=float(c.get("bid", 0)),
                ask=float(c.get("ask", 0)),
                volume=float(c.get("volume", 0)),
                underlying=underlying,
            )
            for c in fx["contracts"]
        ]
        snap = OptionChainSnapshot(
            underlying=underlying,
            spot=float(fx["spot"]),
            contracts=contracts,
            source="mock",
            fetched_at=utcnow(),
            exchange_ts=utcnow(),
            mark_iv=float(fx.get("mark_iv") or 0),
            dvol=float(fx.get("dvol") or 0),
        )
        snap.validate()
        return snap
