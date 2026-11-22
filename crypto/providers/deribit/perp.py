"""Deribit perp stub — optional public ticker spike."""

from __future__ import annotations

import logging

import httpx

from crypto.config import get_asset_config, get_flag, load_config
from crypto.providers.mock.perp import MockPerpProvider
from crypto.providers.types import PerpSnapshot, utcnow

log = logging.getLogger(__name__)


def _underlying_from_symbol(symbol: str) -> str:
    raw = (symbol or "BTC").upper().replace("-PERPETUAL", "").replace("-PERP", "")
    return (raw.split("-")[0] if raw else "BTC") or "BTC"


class DeribitPerpProvider:
    name = "deribit"

    def __init__(self) -> None:
        self._fallback = MockPerpProvider()

    async def fetch_perp(self, symbol: str = "BTC-PERP") -> PerpSnapshot:
        snap = await self._fallback.fetch_perp(symbol)
        snap.source = "deribit-stub"
        snap.venue = "deribit"

        cfg = load_config()
        spike = bool((cfg.get("live_spike") or {}).get("deribit_ticker"))
        if not (get_flag("feeds.deribit") and spike):
            return snap

        base = (cfg.get("live_spike") or {}).get(
            "deribit_base_url", "https://www.deribit.com/api/v2"
        )
        underlying = _underlying_from_symbol(symbol)
        asset = get_asset_config(underlying)
        instrument = str(asset.get("deribit_perp") or f"{underlying}-PERPETUAL")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{base.rstrip('/')}/public/ticker",
                    params={"instrument_name": instrument},
                )
                resp.raise_for_status()
                result = (resp.json() or {}).get("result") or {}
            mark = float(result.get("mark_price") or 0)
            index = float(result.get("index_price") or mark)
            funding = float(result.get("current_funding") or snap.funding_rate)
            oi = float(result.get("open_interest") or snap.open_interest)
            if mark > 0:
                snap.mark = mark
                snap.index = index
                snap.funding_rate = funding
                snap.open_interest = oi
                snap.symbol = instrument
                snap.source = "deribit-public"
                snap.fetched_at = utcnow()
        except Exception as exc:  # noqa: BLE001
            log.warning("Deribit perp spike failed (%s): %s", instrument, exc)
        return snap
