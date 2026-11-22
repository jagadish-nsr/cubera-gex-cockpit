"""Orchestrate paper Confirm submits, ledger, risk, and kill-switch."""

from __future__ import annotations

from typing import Any, Optional

from crypto.config import get_flag, load_config
from crypto.exec.ledger import TradeLedger
from crypto.exec.risk import (
    evaluate_daily_loss,
    resolve_exec_mode,
    size_from_stop,
)
from crypto.providers.types import utcnow


class ExecutionService:
    def __init__(self) -> None:
        self.ledger = TradeLedger()
        self._kill_armed = False

    def _venue(self):
        from crypto.providers.registry import get_registry

        return get_registry().execution()

    def risk_snapshot(self) -> dict[str, Any]:
        pnl = self.ledger.realized_pnl_on()
        daily = evaluate_daily_loss(pnl, auto_flip=True)
        mode = resolve_exec_mode(daily_loss_hit=daily.hit)
        return {
            "daily_loss": {
                "realized_pnl_usd": daily.realized_pnl_usd,
                "limit_usd": daily.limit_usd,
                "hit": daily.hit,
                "flipped_to_advisory": daily.flipped_to_advisory,
                "note": daily.note,
            },
            "mode": mode,
            "open_intents": len(self.ledger.open_intents()),
            "kill_switch_armed": self._kill_armed,
        }

    async def status(self) -> dict[str, Any]:
        paper = get_flag("exec.paper", default=False)
        risk = self.risk_snapshot()
        venue_status: dict[str, Any] = {
            "venue": "none",
            "can_trade": False,
            "note": "exec.paper off — Advisory only.",
        }
        if paper:
            try:
                venue_status = await self._venue().status()
            except Exception as exc:  # noqa: BLE001
                venue_status = {
                    "venue": "error",
                    "can_trade": False,
                    "note": f"execution venue status failed: {exc}",
                }
        return {
            **venue_status,
            "paper_flag": paper,
            "confirm_flag": get_flag("exec.confirm", default=False),
            "can_confirm": bool(risk["mode"]["can_confirm"]),
            "exec_mode": risk["mode"],
            "daily_loss": risk["daily_loss"],
            "open_intents": risk["open_intents"],
            "kill_switch_armed": self._kill_armed,
            "kill_switch_enabled": paper,
        }

    def _entry_from_card(self, card: dict[str, Any]) -> float:
        zone = card.get("entry_zone") or []
        if isinstance(zone, (list, tuple)) and zone:
            nums = [float(x) for x in zone if x is not None]
            if nums:
                return sum(nums) / len(nums)
        spot = card.get("spot")
        if spot is not None:
            return float(spot)
        return 0.0

    async def confirm_from_card(
        self,
        card: dict[str, Any],
        *,
        actor: str = "operator",
    ) -> dict[str, Any]:
        """One-tap Confirm: submit paper/sim bracket from a GO card."""
        if not get_flag("exec.paper", default=False):
            return {
                "ok": False,
                "error": "exec.paper is off — enable paper before Confirm",
                "code": "paper_off",
            }
        risk = self.risk_snapshot()
        if not risk["mode"]["can_confirm"]:
            return {
                "ok": False,
                "error": risk["mode"]["reason"],
                "code": "confirm_blocked",
                "mode": risk["mode"],
            }
        if str(card.get("status") or "").upper() != "GO":
            return {
                "ok": False,
                "error": "Only GO cards can be confirmed",
                "code": "not_go",
            }

        entry = self._entry_from_card(card)
        stop = card.get("stop")
        target = card.get("target")
        if stop is None or entry <= 0:
            return {
                "ok": False,
                "error": "GO card missing entry/stop",
                "code": "incomplete_card",
            }

        sized = size_from_stop(
            entry=entry,
            stop=float(stop),
            size_cap_usd=float(card.get("size_cap_usd") or 0),
        )
        if sized.size_usd <= 0:
            return {
                "ok": False,
                "error": sized.reason or "size resolved to zero",
                "code": "size_zero",
                "sizing": sized.__dict__,
            }

        client_order_id = self.ledger.new_client_order_id()
        order = {
            "client_order_id": client_order_id,
            "instrument": card.get("instrument") or "BTCUSD",
            "side": (card.get("side") or "buy").lower(),
            "entry": entry,
            "target": target,
            "stop": stop,
            "size_usd": sized.size_usd,
        }
        venue = self._venue()
        result = await venue.place_bracket(order)
        now = utcnow()
        status = str(result.get("status") or "submitted")
        fill = result.get("fill_price")
        trade = self.ledger.record_submission(
            {
                "client_order_id": client_order_id,
                "venue": getattr(venue, "name", "paper"),
                "adapter": result.get("adapter") or "sim",
                "status": status,
                "instrument": order["instrument"],
                "side": order["side"],
                "setup": card.get("setup") or "",
                "entry": entry,
                "target": float(target) if target is not None else None,
                "stop": float(stop),
                "size_usd": sized.size_usd,
                "fill_price": float(fill) if fill is not None else None,
                "realized_pnl_usd": 0.0,
                "signal_status": "GO",
                "mode": "paper_confirm",
                "submitted_at": now,
                "filled_at": now if status == "sim_filled" else None,
                "payload": {
                    "actor": actor,
                    "sizing": sized.__dict__,
                    "card_score": card.get("score"),
                    "reasons": list(card.get("reasons") or [])[:3],
                    "venue_result": {
                        k: result[k]
                        for k in ("ok", "adapter", "note", "venue_error")
                        if k in result
                    },
                },
            }
        )
        return {
            "ok": True,
            "trade": trade,
            "venue": result,
            "sizing": sized.__dict__,
            "mode": risk["mode"],
        }

    def arm_kill_switch(self) -> dict[str, Any]:
        self._kill_armed = True
        return {
            "armed": True,
            "note": "Kill switch armed — confirm again to cancel open paper intents.",
        }

    def disarm_kill_switch(self) -> dict[str, Any]:
        self._kill_armed = False
        return {"armed": False, "note": "Kill switch disarmed."}

    async def kill(self, *, confirmed: bool = False, actor: str = "operator") -> dict[str, Any]:
        """Two-tap kill: first call arms; second with confirmed=True cancels intents."""
        if not get_flag("exec.paper", default=False):
            return {"ok": False, "error": "exec.paper off", "code": "paper_off"}

        if not confirmed:
            return {"ok": True, **self.arm_kill_switch(), "cancelled": 0}

        if not self._kill_armed:
            # Allow explicit confirmed=True after UI already armed, or re-arm first
            self._kill_armed = True
            return {
                "ok": True,
                "armed": True,
                "cancelled": 0,
                "note": "Armed on first confirm tap — send confirmed again to execute.",
            }

        open_rows = self.ledger.open_intents()
        venue = self._venue()
        venue_result = await venue.cancel_all()
        cancelled = []
        now = utcnow()
        for row in open_rows:
            updated = self.ledger.update_status(
                row["client_order_id"],
                status="cancelled",
                cancel_reason=f"kill_switch:{actor}",
                closed_at=now,
                payload_patch={"killed_by": actor},
            )
            if updated:
                cancelled.append(updated["client_order_id"])

        self._kill_armed = False
        # Daily-loss style safety: drop Confirm after kill
        from crypto.config import set_flag

        if get_flag("exec.confirm", default=False):
            set_flag("exec.confirm", False, actor=f"kill:{actor}")

        return {
            "ok": True,
            "armed": False,
            "cancelled": len(cancelled),
            "client_order_ids": cancelled,
            "venue": venue_result,
            "note": "Open paper intents cancelled; Confirm flipped to Advisory.",
            "mode": resolve_exec_mode(daily_loss_hit=False),
        }


_EXEC: ExecutionService | None = None


def get_execution_service() -> ExecutionService:
    global _EXEC
    if _EXEC is None:
        _EXEC = ExecutionService()
    return _EXEC


def reset_execution_service() -> None:
    """Test helper."""
    global _EXEC
    _EXEC = None
