"""Delta Exchange India public WebSocket collector.

Endpoint: wss://public-socket.india.delta.exchange
Channels: ticker, ob_l1, trades (as available).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

import httpx

from crypto.feeds.state import LiveFeedState
from crypto.providers.delta.parsers import (
    FIELD_FINDINGS,
    parse_rest_option_ticker,
    parse_ws_trade,
)
from crypto.providers.types import OptionChainSnapshot, utcnow

log = logging.getLogger(__name__)

DEFAULT_WS = "wss://public-socket.india.delta.exchange"
DEFAULT_REST = "https://api.india.delta.exchange"


class DeltaWsCollector:
    def __init__(
        self,
        state: LiveFeedState,
        *,
        ws_url: str = DEFAULT_WS,
        rest_url: str = DEFAULT_REST,
        symbols: Optional[list[str]] = None,
        persist: Optional[Callable[[str, str, dict[str, Any], Any], None]] = None,
        reconnect_base_s: float = 1.0,
        reconnect_max_s: float = 30.0,
        snapshot_options: bool = True,
    ) -> None:
        self.state = state
        self.ws_url = ws_url
        self.rest_url = rest_url
        self.symbols = symbols or ["BTCUSD"]
        self.persist = persist
        self.reconnect_base_s = reconnect_base_s
        self.reconnect_max_s = reconnect_max_s
        self.snapshot_options = snapshot_options
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.state.set_field_findings({"delta": FIELD_FINDINGS})
        self._task = asyncio.create_task(self._run_forever(), name="delta-ws")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def refresh_option_snapshot(self, underlying: str = "BTC") -> None:
        """Optional REST options tickers → thin chain for cross-check (not GEX primary)."""
        if not self.snapshot_options:
            return
        url = f"{self.rest_url.rstrip('/')}/v2/tickers"
        params = {
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": underlying,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params=params, headers={"Accept": "application/json"})
            resp.raise_for_status()
            rows = resp.json().get("result") or []
        # Keep a band around spot from BTCUSD ticker if present
        spot = 0.0
        contracts = []
        for row in rows:
            try:
                c = parse_rest_option_ticker(row)
                contracts.append(c)
            except Exception:
                continue
        if contracts:
            # Estimate spot from ATM mid of nearest strikes
            strikes = sorted({c.strike for c in contracts})
            # Prefer mark from a nearby call/put pair average
            spot = strikes[len(strikes) // 2] if strikes else 0.0
        # Also pull perp mark
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.rest_url.rstrip('/')}/v2/tickers",
                    params={
                        "contract_types": "perpetual_futures",
                        "underlying_asset_symbols": underlying,
                    },
                    headers={"Accept": "application/json"},
                )
                perp = (resp.json().get("result") or [None])[0]
                if perp:
                    spot = float(perp.get("mark_price") or perp.get("close") or spot)
        except Exception as exc:  # noqa: BLE001
            log.debug("delta perp mark miss: %s", exc)

        # Band filter ±8%
        if spot > 0:
            contracts = [c for c in contracts if abs(c.strike - spot) / spot <= 0.08]
        # Cap size for store
        contracts = contracts[:120]
        if not contracts or spot <= 0:
            return
        snap = OptionChainSnapshot(
            underlying=underlying,
            spot=spot,
            contracts=contracts,
            source="delta-rest-snapshot",
            fetched_at=utcnow(),
            exchange_ts=utcnow(),
        )
        try:
            snap.validate()
        except Exception:
            return
        self.state.set_delta_chain(snap)
        if self.persist:
            self.persist(
                "delta",
                "option_chain_snapshot",
                {"spot": snap.spot, "n_contracts": len(snap.contracts)},
                snap.exchange_ts,
            )

    async def _run_forever(self) -> None:
        delay = self.reconnect_base_s
        while not self._stop.is_set():
            try:
                try:
                    await self.refresh_option_snapshot()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Delta REST option snapshot failed: %s", exc)
                await self._session()
                delay = self.reconnect_base_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Delta WS session ended: %s", exc)
                self.state.note_error("delta", str(exc))
                self.state.note_reconnect("delta")
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

        channels = [
            {"name": "ticker", "symbols": list(self.symbols)},
            {"name": "ob_l1", "symbols": list(self.symbols)},
            {"name": "trades", "symbols": list(self.symbols)},
        ]
        self.state.set_channels("delta", [c["name"] for c in channels])
        log.info("Delta WS connecting to %s symbols=%s", self.ws_url, self.symbols)

        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({"type": "subscribe", "payload": {"channels": channels}}))
            # Enable heartbeat (recommended by Delta docs)
            await ws.send(json.dumps({"type": "enable_heartbeat"}))
            self.state.health["delta"].connected = True
            self.state.health["delta"].mark_msg()

            while not self._stop.is_set():
                raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
                msg = json.loads(raw)
                await self._handle(msg)

    async def _handle(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype in {"heartbeat", "subscriptions", "success"}:
            self.state.health["delta"].mark_msg()
            return
        if mtype is None:
            return

        if self.persist:
            try:
                self.persist("delta", str(mtype), msg, utcnow())
                self.state.bump_ticks("delta")
            except Exception as exc:  # noqa: BLE001
                log.debug("delta persist failed: %s", exc)
        else:
            self.state.health["delta"].mark_msg()

        if mtype == "trades":
            try:
                trade = parse_ws_trade(msg)
                self.state.append_trade(trade)
            except Exception as exc:  # noqa: BLE001
                log.debug("delta trade parse failed: %s", exc)
