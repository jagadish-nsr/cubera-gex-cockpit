"""Deribit option-chain provider.

Default: mock fixture (safe).
When ``feeds.deribit`` is on:
  1. Prefer live cache from the WS/REST collector.
  2. Else one-shot REST snapshot (if CRYPTO_LIVE_FEEDS / live_spike).
  3. Else legacy public ticker spike onto fixture (never blocks UI).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from crypto.config import get_asset_config, get_flag, load_config
from crypto.feeds.state import get_feed_state
from crypto.providers.mock.chain import MockOptionChainProvider
from crypto.providers.types import OptionChainSnapshot, utcnow

log = logging.getLogger(__name__)


class DeribitOptionChainProvider:
    name = "deribit"

    def __init__(self) -> None:
        self._fallback = MockOptionChainProvider()

    async def fetch_chain(self, underlying: str = "BTC") -> OptionChainSnapshot:
        want = (underlying or "BTC").strip().upper() or "BTC"
        if get_flag("feeds.deribit"):
            # Only reuse cache when it matches the active asset — never serve
            # a BTC live book after the operator switches to ETH/SOL.
            live = get_feed_state().get_deribit_chain(for_underlying=want)
            if live is not None and live.contracts:
                return live

            cfg = load_config()
            feeds = cfg.get("feeds") or {}
            spike = bool((cfg.get("live_spike") or {}).get("deribit_ws")) or bool(
                (cfg.get("live_spike") or {}).get("deribit_ticker")
            )
            # One-shot REST when live requested but collector cache empty / wrong asset
            if spike or _live_env():
                try:
                    from crypto.providers.deribit.rest_snapshot import build_chain_snapshot

                    dcfg = feeds.get("deribit") or {}
                    snap = await build_chain_snapshot(
                        currency=want,
                        base_url=dcfg.get("rest_url")
                        or (cfg.get("live_spike") or {}).get("deribit_base_url")
                        or "https://www.deribit.com/api/v2",
                        max_expiries=int(dcfg.get("max_expiries") or 2),
                        strike_band_pct=float(dcfg.get("strike_band_pct") or 0.08),
                    )
                    if snap.contracts and str(snap.underlying or "").upper() == want:
                        get_feed_state().set_deribit_chain(snap)
                        return snap
                except Exception as exc:  # noqa: BLE001
                    log.warning("Deribit REST snapshot failed; fixture fallback: %s", exc)

        # Legacy: optional public ticker overlay on fixture
        snap = await self._fallback.fetch_chain(want)
        snap.source = "deribit-stub"

        cfg = load_config()
        spike = bool((cfg.get("live_spike") or {}).get("deribit_ticker"))
        if not (get_flag("feeds.deribit") and spike):
            return snap

        base = (cfg.get("live_spike") or {}).get(
            "deribit_base_url", "https://www.deribit.com/api/v2"
        )
        asset = get_asset_config(want)
        perp_name = str(asset.get("deribit_perp") or f"{want}-PERPETUAL")
        try:
            ticker = await self._public_ticker(base, perp_name)
            mark = float(ticker.get("mark_price") or ticker.get("last_price") or 0)
            if mark > 0:
                snap.spot = mark
                snap.source = "deribit-public-spike+fixture-chain"
                snap.fetched_at = utcnow()
                snap.exchange_ts = utcnow()
                log.info("Deribit public spike: %s mark=%.2f", perp_name, mark)
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


def _live_env() -> bool:
    import os

    return os.environ.get("CRYPTO_LIVE_FEEDS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
