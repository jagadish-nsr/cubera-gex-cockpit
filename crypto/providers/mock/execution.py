"""Paper / stub execution venue — local sim only, never live capital."""

from __future__ import annotations

import uuid
from typing import Any

from crypto.providers.types import utcnow


class MockExecutionVenue:
    name = "mock"

    def __init__(self) -> None:
        self._open: dict[str, dict[str, Any]] = {}

    async def status(self) -> dict[str, Any]:
        return {
            "venue": "mock",
            "adapter": "sim",
            "mode": "paper",
            "can_trade": True,
            "demo_credentials": False,
            "open_intents": len(self._open),
            "note": "Local sim paper venue. Kill switch cancels open sim intents.",
            "checked_at": utcnow().isoformat(),
        }

    async def place_bracket(self, order: dict[str, Any]) -> dict[str, Any]:
        client_order_id = str(order.get("client_order_id") or f"cc{uuid.uuid4().hex[:30]}")
        entry = float(order.get("entry") or 0)
        intent = {
            "client_order_id": client_order_id,
            "instrument": str(order.get("instrument") or "BTCUSD"),
            "side": str(order.get("side") or "buy").lower(),
            "entry": entry,
            "target": order.get("target"),
            "stop": order.get("stop"),
            "size_usd": float(order.get("size_usd") or 0),
            "submitted_at": utcnow().isoformat(),
        }
        self._open[client_order_id] = {**intent, "fill_price": entry}
        return {
            "ok": True,
            "adapter": "sim",
            "status": "sim_filled",
            "client_order_id": client_order_id,
            "fill_price": entry,
            "intent": intent,
            "note": "Mock sim fill.",
        }

    async def cancel_intent(self, client_order_id: str) -> dict[str, Any]:
        had = self._open.pop(client_order_id, None) is not None
        return {
            "ok": True,
            "adapter": "sim",
            "client_order_id": client_order_id,
            "status": "cancelled",
            "had_open": had,
        }

    async def cancel_all(self) -> dict[str, Any]:
        ids = list(self._open.keys())
        self._open.clear()
        return {
            "ok": True,
            "cancelled": len(ids),
            "client_order_ids": ids,
            "checked_at": utcnow().isoformat(),
        }
