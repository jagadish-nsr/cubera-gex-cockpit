"""Deribit option-chain provider.

By default returns the mock fixture (safe). When `feeds.deribit` and
`live_spike.deribit_ticker` are on, spikes one public ticker read for BTC-PERPETUAL
and overlays spot onto the fixture chain so the UI stays rich without a full
book rebuild.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from crypto.config import get_flag, load_config
from crypto.providers.mock.chain import MockOptionChainProvider
from crypto.providers.types import OptionChainSnapshot, utcnow

log = logging.getLogger(__name__)


class DeribitOptionChainProvider:
    name = "deribit"

    def __init__(self) -> None:
        self._fallback = MockOptionChainProvider()

    async def fetch_chain(self, underlying: str = "BTC") -> OptionChainSnapshot:
        snap = await self._fallback.fetch_chain(underlying)
        snap.source = "deribit-stub"

        cfg = load_config()
        spike = bool((cfg.get("live_spike") or {}).get("deribit_ticker"))
        if not (get_flag("feeds.deribit") and spike):
            return snap

        base = (cfg.get("live_spike") or {}).get(
            "deribit_base_url", "https://www.deribit.com/api/v2"
        )
        try:
            ticker = await self._public_ticker(base, "BTC-PERPETUAL")
            mark = float(ticker.get("mark_price") or ticker.get("last_price") or 0)
            if mark > 0:
                snap.spot = mark
                snap.source = "deribit-public-spike+fixture-chain"
                snap.fetched_at = utcnow()
                snap.exchange_ts = utcnow()
                log.info("Deribit public spike: BTC-PERPETUAL mark=%.2f", mark)
        except Exception as exc:  # noqa: BLE001 — never block UI
            log.warning("Deribit live spike failed; using fixture: %s", exc)
            snap.source = "deribit-stub-fallback"
        return snap

    async def _public_ticker(self, base: str, instrument: str) -> dict[str, Any]:
        url = f"{base.rstrip('/')}/public/ticker"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params={"instrument_name": instrument})
            resp.raise_for_status()
            body = resp.json()
            return body.get("result") or {}
