"""Start / stop live collectors from feature flags + env."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from crypto.config import get_active_underlying, get_asset_config, get_flag, load_config
from crypto.feeds.state import LiveFeedState, get_feed_state
from crypto.feeds.stale import STALE_THRESHOLD_S
from crypto.providers.delta.ws import DEFAULT_REST as DELTA_REST
from crypto.providers.delta.ws import DEFAULT_WS as DELTA_WS
from crypto.providers.delta.ws import DeltaWsCollector
from crypto.providers.deribit.ws import DEFAULT_REST as DERIBIT_REST
from crypto.providers.deribit.ws import DEFAULT_WS as DERIBIT_WS
from crypto.providers.deribit.ws import DeribitWsCollector
from crypto.store.writer import StoreWriter

log = logging.getLogger(__name__)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def live_feeds_requested() -> bool:
    """Master env gate — keeps default mock path quiet."""
    return _env_truthy("CRYPTO_LIVE_FEEDS") or _env_truthy("CRYPTO_LIVE_SPIKE_DERIBIT")


def is_live_market_data_source(source: str | None) -> bool:
    """True only for real Deribit MD — stub/fixture/mock soft-fallback is not live.

    Used so CRYPTO_LIVE_FEEDS=1 still keeps mock stale rules when WS/REST miss
    and providers serve fixtures (paper desk must stay usable offline).
    """
    src = (source or "").lower()
    return (
        "deribit" in src
        and "stub" not in src
        and "fixture" not in src
        and "mock" not in src
        and "fallback" not in src
    )


class FeedManager:
    def __init__(self, state: LiveFeedState | None = None, store: StoreWriter | None = None) -> None:
        self.state = state or get_feed_state()
        self.store = store
        self.deribit: Optional[DeribitWsCollector] = None
        self.delta: Optional[DeltaWsCollector] = None
        self._started = False

    def _persist(self, venue: str, channel: str, payload: dict[str, Any], exchange_ts: Any) -> None:
        if self.store is None:
            try:
                self.store = StoreWriter()
            except Exception as exc:  # noqa: BLE001
                log.error("store init failed (ticks will drop): %s", exc)
                return
        try:
            self.store.write_tick(venue, channel, payload, exchange_ts=exchange_ts)
        except Exception as exc:  # noqa: BLE001 — never kill the WS loop
            log.error("persist tick failed %s/%s: %s", venue, channel, exc)

    async def start(self) -> dict[str, Any]:
        cfg = load_config()
        feeds_cfg = cfg.get("feeds") or {}
        self.state.stale_threshold_s = float(
            feeds_cfg.get("stale_threshold_s") or STALE_THRESHOLD_S
        )

        want_live = live_feeds_requested()
        deribit_on = get_flag("feeds.deribit", default=False) and (
            want_live or bool((cfg.get("live_spike") or {}).get("deribit_ws"))
        )
        delta_on = get_flag("feeds.delta", default=False) and (
            want_live or bool((cfg.get("live_spike") or {}).get("delta_ws"))
        )

        summary = {
            "live_env": want_live,
            "deribit": False,
            "delta": False,
            "stale_threshold_s": self.state.stale_threshold_s,
        }

        underlying = get_active_underlying()
        asset = get_asset_config(underlying)
        summary["underlying"] = underlying

        if deribit_on:
            dcfg = feeds_cfg.get("deribit") or {}
            currency = str(asset.get("deribit_currency") or underlying)
            self.deribit = DeribitWsCollector(
                self.state,
                ws_url=dcfg.get("ws_url") or DERIBIT_WS,
                rest_url=dcfg.get("rest_url") or DERIBIT_REST,
                currency=currency,
                max_expiries=int(dcfg.get("max_expiries") or 2),
                strike_band_pct=float(dcfg.get("strike_band_pct") or 0.08),
                persist=self._persist,
            )
            await self.deribit.start()
            summary["deribit"] = True
            summary["deribit_currency"] = currency
            log.info("Deribit live feed collector started for %s", currency)

        if delta_on:
            dcfg = feeds_cfg.get("delta") or {}
            symbols = list(asset.get("delta_symbols") or dcfg.get("symbols") or ["BTCUSD"])
            self.delta = DeltaWsCollector(
                self.state,
                ws_url=dcfg.get("ws_url") or DELTA_WS,
                rest_url=dcfg.get("rest_url") or DELTA_REST,
                symbols=symbols,
                persist=self._persist,
                snapshot_options=bool(dcfg.get("snapshot_options", True)),
            )
            await self.delta.start()
            summary["delta"] = True
            summary["delta_symbols"] = symbols
            log.info("Delta live feed collector started for %s", symbols)

        self._started = summary["deribit"] or summary["delta"]
        if not self._started:
            log.info(
                "Live feeds idle (mock default). Set CRYPTO_LIVE_FEEDS=1 and "
                "feeds.deribit / feeds.delta to enable."
            )
        return summary

    async def stop(self) -> None:
        if self.deribit:
            await self.deribit.stop()
        if self.delta:
            await self.delta.stop()
        self._started = False

    def status(self) -> dict[str, Any]:
        return {
            "started": self._started,
            "live_env": live_feeds_requested(),
            **self.state.status(),
        }


_MANAGER: FeedManager | None = None


def get_feed_manager(*, reload: bool = False) -> FeedManager:
    global _MANAGER
    if _MANAGER is None or reload:
        _MANAGER = FeedManager()
    return _MANAGER
