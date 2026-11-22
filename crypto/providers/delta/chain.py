"""Delta option-chain provider.

Default: mock fixture. When ``feeds.delta`` is on, prefer the live/REST
cache from the Delta collector; otherwise fall back to mock.
"""

from __future__ import annotations

import logging

from crypto.config import get_flag
from crypto.feeds.state import get_feed_state
from crypto.providers.mock.chain import MockOptionChainProvider
from crypto.providers.types import OptionChainSnapshot

log = logging.getLogger(__name__)


class DeltaOptionChainProvider:
    name = "delta"

    def __init__(self) -> None:
        self._fallback = MockOptionChainProvider()

    async def fetch_chain(self, underlying: str = "BTC") -> OptionChainSnapshot:
        want = (underlying or "BTC").strip().upper() or "BTC"
        if get_flag("feeds.delta"):
            live = get_feed_state().get_delta_chain(for_underlying=want)
            if live is not None and live.contracts:
                return live
            # Attempt one-shot REST if collector has not filled cache yet / wrong asset
            try:
                from crypto.providers.delta.ws import DeltaWsCollector

                collector = DeltaWsCollector(get_feed_state())
                await collector.refresh_option_snapshot(want)
                live = get_feed_state().get_delta_chain(for_underlying=want)
                if live is not None and live.contracts:
                    return live
            except Exception as exc:  # noqa: BLE001
                log.warning("Delta live chain miss; fixture fallback: %s", exc)

        snap = await self._fallback.fetch_chain(want)
        snap.source = "delta-stub"
        return snap
