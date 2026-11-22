"""Deribit public WebSocket collector — option tickers + trades.

Reconnects with exponential backoff. Persists every raw notification.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from crypto.feeds.state import LiveFeedState
from crypto.providers.deribit.parsers import (
    FIELD_FINDINGS,
    parse_trade,
    parse_ws_subscription,
    unwrap_trades,
)
from crypto.providers.deribit.rest_snapshot import (
    DEFAULT_REST,
    build_chain_snapshot,
    overlay_ticker_on_chain,
    select_instruments,
    list_active_options,
    fetch_index_price,
)
from crypto.providers.types import utcnow

log = logging.getLogger(__name__)

DEFAULT_WS = "wss://www.deribit.com/ws/api/v2"


class DeribitWsCollector:
    def __init__(
        self,
        state: LiveFeedState,
        *,
        ws_url: str = DEFAULT_WS,
        rest_url: str = DEFAULT_REST,
        currency: str = "BTC",
        max_expiries: int = 2,
        strike_band_pct: float = 0.08,
        persist: Optional[Callable[[str, str, dict[str, Any], Any], None]] = None,
        reconnect_base_s: float = 1.0,
        reconnect_max_s: float = 30.0,
    ) -> None:
        self.state = state
        self.ws_url = ws_url
        self.rest_url = rest_url
        self.currency = currency
        self.max_expiries = max_expiries
        self.strike_band_pct = strike_band_pct
        self.persist = persist
        self.reconnect_base_s = reconnect_base_s
        self.reconnect_max_s = reconnect_max_s
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._instrument_names: list[str] = []

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.state.set_field_findings({"deribit": FIELD_FINDINGS})
        self._task = asyncio.create_task(self._run_forever(), name="deribit-ws")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def refresh_snapshot(self) -> None:
        snap = await build_chain_snapshot(
            currency=self.currency,
            base_url=self.rest_url,
            max_expiries=self.max_expiries,
            strike_band_pct=self.strike_band_pct,
        )
        self.state.set_deribit_chain(snap)
        if self.persist:
            self.persist(
                "deribit",
                "option_chain_snapshot",
                {
                    "spot": snap.spot,
                    "n_contracts": len(snap.contracts),
                    "source": snap.source,
                },
                snap.exchange_ts,
            )
        log.info(
            "Deribit REST snapshot: spot=%.2f contracts=%d",
            snap.spot,
            len(snap.contracts),
        )

    async def _run_forever(self) -> None:
        delay = self.reconnect_base_s
        while not self._stop.is_set():
            try:
                await self.refresh_snapshot()
                await self._session()
                delay = self.reconnect_base_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Deribit WS session ended: %s", exc)
                self.state.note_error("deribit", str(exc))
                self.state.note_reconnect("deribit")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(self.reconnect_max_s, delay * 2)

    async def _session(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets package required for live feeds") from exc

        channels = await self._build_channels()
        self.state.set_channels("deribit", channels)
        log.info("Deribit WS connecting (%d channels)", len(channels))

        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            # Subscribe in chunks (Deribit channel list limits)
            for i in range(0, len(channels), 40):
                chunk = channels[i : i + 40]
                await ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1000 + i,
                            "method": "public/subscribe",
                            "params": {"channels": chunk},
                        }
                    )
                )
            self.state.health["deribit"].connected = True
            self.state.health["deribit"].mark_msg()

            while not self._stop.is_set():
                raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
                msg = json.loads(raw)
                await self._handle(msg)

    async def _build_channels(self) -> list[str]:
        import httpx

        async with httpx.AsyncClient(timeout=20.0) as client:
            spot = await fetch_index_price(client, self.rest_url, self.currency)
            instruments = await list_active_options(client, self.rest_url, self.currency)
        selected = select_instruments(
            instruments,
            spot,
            max_expiries=self.max_expiries,
            strike_band_pct=self.strike_band_pct,
        )
        self._instrument_names = [r["instrument_name"] for r in selected]
        channels = [
            f"ticker.{self.currency}-PERPETUAL.100ms",
            f"trades.option.{self.currency}.100ms",
        ]
        channels.extend(f"ticker.{name}.100ms" for name in self._instrument_names)
        return channels

    async def _handle(self, msg: dict[str, Any]) -> None:
        if "error" in msg:
            log.warning("Deribit RPC error: %s", msg["error"])
            return
        parsed = parse_ws_subscription(msg)
        if parsed is None:
            return
        channel, data = parsed
        if self.persist:
            payload = data if isinstance(data, dict) else {"items": data}
            try:
                self.persist("deribit", channel.split(".")[0], payload, utcnow())
                self.state.bump_ticks("deribit")
            except Exception as exc:  # noqa: BLE001
                log.debug("persist failed: %s", exc)

        if channel.startswith("ticker."):
            if isinstance(data, dict):
                snap = self.state.get_deribit_chain()
                if snap is not None:
                    self.state.set_deribit_chain(overlay_ticker_on_chain(snap, data))
                else:
                    self.state.health["deribit"].mark_msg()
        elif channel.startswith("trades."):
            for row in unwrap_trades(data):
                try:
                    trade = parse_trade(row)
                    self.state.append_trade(trade)
                except Exception as exc:  # noqa: BLE001
                    log.debug("trade parse failed: %s", exc)
