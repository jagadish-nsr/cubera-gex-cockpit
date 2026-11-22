"""Delta Exchange India **live** Confirm execution adapter.

Hard rules:
- Fail closed without DELTA_LIVE_* keys + CRYPTO_LIVE_CONFIRM_ACK + IP ack.
- Production India host only (``api.india.delta.exchange``).
- Bracket-style intents only; never Auto; never soft-fail to sim on live.
- Separate from the paper/demo adapter (``execution.py``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Optional
from urllib import error, request

from crypto.exec.live_gate import (
    DELTA_INDIA_LIVE_REST,
    evaluate_live_gate,
    live_base_url,
    live_credentials,
)
from crypto.providers.types import utcnow

log = logging.getLogger(__name__)


class DeltaLiveExecutionVenue:
    """Live Confirm adapter. Refuses to trade unless the live gate passes."""

    name = "delta_live"

    def __init__(self) -> None:
        self._open: dict[str, dict[str, Any]] = {}

    def _gate(self):
        # Flags checked by ExecutionService; here we require credentials/ack/url.
        return evaluate_live_gate(require_flags=False)

    def _sign(self, method: str, path: str, query: str, body: str, timestamp: str) -> str:
        _, secret = live_credentials()
        if not secret:
            raise RuntimeError("DELTA_LIVE_API_SECRET missing")
        prehash = f"{method}{timestamp}{path}{query}{body}"
        return hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        gate = self._gate()
        if not gate.ok:
            return {
                "success": False,
                "error": {"code": gate.code, "reasons": gate.reasons},
            }
        key, _ = live_credentials()
        assert key is not None
        base = gate.base_url or live_base_url()
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        ts = str(int(time.time()))
        sig = self._sign(method.upper(), path, "", payload, ts)
        url = f"{base}{path}"
        req = request.Request(
            url,
            data=payload.encode() if payload else None,
            method=method.upper(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "api-key": key,
                "timestamp": ts,
                "signature": sig,
                "User-Agent": "cryptocubera-live/0.5",
            },
        )
        try:
            with request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {"success": True}
        except error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            log.warning("Delta live HTTP %s: %s", exc.code, detail[:400])
            return {"success": False, "error": {"http": exc.code, "body": detail[:800]}}
        except Exception as exc:  # noqa: BLE001
            log.warning("Delta live request failed: %s", exc)
            return {"success": False, "error": {"message": str(exc)}}

    async def status(self) -> dict[str, Any]:
        gate = evaluate_live_gate(require_flags=True)
        return {
            "venue": "delta",
            "adapter": "live",
            "mode": "live",
            "can_trade": gate.ok,
            "live_credentials": bool(live_credentials()[0] and live_credentials()[1]),
            "base_url": gate.base_url or DELTA_INDIA_LIVE_REST,
            "open_intents": len(self._open),
            "live_gate": gate.as_dict(),
            "note": (
                "Live Confirm gate open — real capital path."
                if gate.ok
                else "Live Confirm refused: " + "; ".join(gate.reasons[:3])
            ),
            "checked_at": utcnow().isoformat(),
        }

    async def place_bracket(self, order: dict[str, Any]) -> dict[str, Any]:
        """Submit a live bracket. Fail closed — never sim-fill on this path."""
        gate = evaluate_live_gate(require_flags=False)
        if not gate.ok:
            return {
                "ok": False,
                "adapter": "live",
                "status": "refused",
                "client_order_id": order.get("client_order_id"),
                "error": "live_gate_failed",
                "reasons": gate.reasons,
                "live_gate": gate.as_dict(),
            }

        client_order_id = str(order.get("client_order_id") or f"cc{uuid.uuid4().hex[:30]}")
        instrument = str(order.get("instrument") or order.get("product_symbol") or "BTCUSD")
        side = str(order.get("side") or "buy").lower()
        entry = float(order.get("entry") or 0)
        target = order.get("target")
        stop = order.get("stop")
        size_usd = float(order.get("size_usd") or 0)
        contracts = max(1, int(round(size_usd / max(entry, 1.0))))

        body = {
            "product_symbol": instrument,
            "size": contracts,
            "side": side,
            "order_type": "limit_order",
            "limit_price": str(round(entry, 2)),
            "client_order_id": client_order_id[:32],
            "bracket_stop_loss_price": str(stop) if stop is not None else None,
            "bracket_take_profit_price": str(target) if target is not None else None,
            "bracket_stop_trigger_method": "mark_price",
        }
        body = {k: v for k, v in body.items() if v is not None}
        resp = self._request("POST", "/v2/orders", body=body)
        ok = bool(resp.get("success"))
        if not ok:
            return {
                "ok": False,
                "adapter": "live",
                "status": "rejected",
                "client_order_id": client_order_id,
                "venue_error": resp,
                "note": "Live venue rejected order — not recorded as filled.",
            }

        intent = {
            "client_order_id": client_order_id,
            "instrument": instrument,
            "side": side,
            "entry": entry,
            "target": float(target) if target is not None else None,
            "stop": float(stop) if stop is not None else None,
            "size_usd": size_usd,
            "submitted_at": utcnow().isoformat(),
        }
        self._open[client_order_id] = {**intent, "venue_response": resp}
        return {
            "ok": True,
            "adapter": "live",
            "status": "submitted",
            "client_order_id": client_order_id,
            "fill_price": None,
            "venue_response": resp,
            "intent": intent,
        }

    async def cancel_intent(self, client_order_id: str) -> dict[str, Any]:
        intent = self._open.pop(client_order_id, None)
        resp = self._request(
            "DELETE",
            "/v2/orders/client_order_id/" + client_order_id[:32],
        )
        return {
            "ok": bool(resp.get("success", True)),
            "adapter": "live",
            "client_order_id": client_order_id,
            "status": "cancelled",
            "had_open": intent is not None,
            "venue_response": resp,
        }

    async def cancel_all(self) -> dict[str, Any]:
        ids = list(self._open.keys())
        results = []
        for cid in ids:
            results.append(await self.cancel_intent(cid))
        # Best-effort venue-side cancel-all
        venue = self._request("DELETE", "/v2/orders/all")
        self._open.clear()
        return {
            "ok": True,
            "cancelled": len(results),
            "client_order_ids": ids,
            "results": results,
            "venue_response": venue,
            "checked_at": utcnow().isoformat(),
        }
