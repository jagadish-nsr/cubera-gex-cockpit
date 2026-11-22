"""Delta Exchange India **demo/testnet** paper execution adapter.

Hard rules:
- Never targets production India trading (`api.india.delta.exchange`) for orders.
- Without DELTA_PAPER_API_KEY / DELTA_PAPER_API_SECRET, falls back to local sim fills.
- Bracket-style paper intents only; no live capital path in this module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from typing import Any, Optional
from urllib import error, request

from crypto.providers.types import utcnow

log = logging.getLogger(__name__)

# Demo / testnet only — production trading URL is intentionally absent.
DELTA_TESTNET_REST = "https://cdn-ind.testnet.deltaex.org"
_FORBIDDEN_LIVE_HOSTS = (
    "api.india.delta.exchange",
    "api.delta.exchange",
)


def _paper_credentials() -> tuple[Optional[str], Optional[str]]:
    key = (os.environ.get("DELTA_PAPER_API_KEY") or "").strip() or None
    secret = (os.environ.get("DELTA_PAPER_API_SECRET") or "").strip() or None
    return key, secret


def _base_url() -> str:
    raw = (os.environ.get("DELTA_PAPER_REST_URL") or DELTA_TESTNET_REST).strip().rstrip("/")
    lower = raw.lower()
    for host in _FORBIDDEN_LIVE_HOSTS:
        if host in lower:
            raise RuntimeError(
                f"Refusing live Delta host {host!r} for paper adapter — "
                f"use testnet ({DELTA_TESTNET_REST}) only."
            )
    return raw


class DeltaPaperExecutionVenue:
    """Paper Confirm adapter: demo REST when keys present, else local sim."""

    name = "delta_paper"

    def __init__(self) -> None:
        self._open: dict[str, dict[str, Any]] = {}

    def _has_demo_keys(self) -> bool:
        key, secret = _paper_credentials()
        return bool(key and secret)

    def _sign(self, method: str, path: str, query: str, body: str, timestamp: str) -> str:
        _, secret = _paper_credentials()
        assert secret is not None
        prehash = f"{method}{timestamp}{path}{query}{body}"
        return hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        key, _ = _paper_credentials()
        assert key is not None
        base = _base_url()
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
                "User-Agent": "cryptocubera-paper/0.4",
            },
        )
        try:
            with request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {"success": True}
        except error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            log.warning("Delta paper HTTP %s: %s", exc.code, detail[:400])
            return {"success": False, "error": {"http": exc.code, "body": detail[:800]}}
        except Exception as exc:  # noqa: BLE001 — fail soft to sim
            log.warning("Delta paper request failed: %s", exc)
            return {"success": False, "error": {"message": str(exc)}}

    async def status(self) -> dict[str, Any]:
        demo = self._has_demo_keys()
        return {
            "venue": "delta",
            "adapter": "demo" if demo else "sim",
            "mode": "paper",
            "can_trade": True,
            "demo_credentials": demo,
            "base_url": _base_url() if demo else None,
            "open_intents": len(self._open),
            "note": (
                "Delta demo/testnet keys present — paper REST enabled."
                if demo
                else "No DELTA_PAPER_* keys — local sim fills only. Never live capital."
            ),
            "checked_at": utcnow().isoformat(),
        }

    async def place_bracket(self, order: dict[str, Any]) -> dict[str, Any]:
        """Submit a paper bracket. Always records a client_order_id; never live prod."""
        client_order_id = str(order.get("client_order_id") or f"cc{uuid.uuid4().hex[:30]}")
        instrument = str(order.get("instrument") or order.get("product_symbol") or "BTCUSD")
        side = str(order.get("side") or "buy").lower()
        entry = float(order.get("entry") or 0)
        target = order.get("target")
        stop = order.get("stop")
        size_usd = float(order.get("size_usd") or 0)

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

        if self._has_demo_keys():
            # Convert USD notional to a coarse contract count for demo; size is advisory.
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
            # Drop nulls — Delta rejects unknown nulls on some fields
            body = {k: v for k, v in body.items() if v is not None}
            resp = self._request("POST", "/v2/orders", body=body)
            ok = bool(resp.get("success"))
            if not ok:
                # Soft-fail to sim so cockpit still records the paper intent
                log.info("Delta demo order failed; recording sim fill instead: %s", resp)
                return self._sim_fill(intent, fallback_error=resp)
            self._open[client_order_id] = {**intent, "venue_response": resp}
            return {
                "ok": True,
                "adapter": "demo",
                "status": "submitted",
                "client_order_id": client_order_id,
                "fill_price": None,
                "venue_response": resp,
                "intent": intent,
            }

        return self._sim_fill(intent)

    def _sim_fill(
        self,
        intent: dict[str, Any],
        *,
        fallback_error: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        fill = float(intent["entry"])
        self._open[intent["client_order_id"]] = {**intent, "fill_price": fill}
        out: dict[str, Any] = {
            "ok": True,
            "adapter": "sim",
            "status": "sim_filled",
            "client_order_id": intent["client_order_id"],
            "fill_price": fill,
            "intent": intent,
            "note": "Local sim fill — demo credentials absent or demo REST soft-failed.",
        }
        if fallback_error is not None:
            out["venue_error"] = fallback_error
        return out

    async def cancel_intent(self, client_order_id: str) -> dict[str, Any]:
        intent = self._open.pop(client_order_id, None)
        if self._has_demo_keys() and intent is not None:
            resp = self._request(
                "DELETE",
                "/v2/orders/client_order_id/" + client_order_id[:32],
            )
            return {
                "ok": True,
                "adapter": "demo",
                "client_order_id": client_order_id,
                "status": "cancelled",
                "venue_response": resp,
            }
        return {
            "ok": True,
            "adapter": "sim" if not self._has_demo_keys() else "demo",
            "client_order_id": client_order_id,
            "status": "cancelled",
            "had_open": intent is not None,
        }

    async def cancel_all(self) -> dict[str, Any]:
        ids = list(self._open.keys())
        results = []
        for cid in ids:
            results.append(await self.cancel_intent(cid))
        if self._has_demo_keys():
            # Best-effort venue-side cancel-all (demo). Ignore shape mismatches.
            self._request("DELETE", "/v2/orders/all")
        self._open.clear()
        return {
            "ok": True,
            "cancelled": len(results),
            "client_order_ids": ids,
            "results": results,
            "checked_at": utcnow().isoformat(),
        }
