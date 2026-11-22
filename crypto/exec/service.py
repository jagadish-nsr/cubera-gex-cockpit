"""Orchestrate paper/live Confirm submits, ledger, risk, and kill-switch."""

from __future__ import annotations

from typing import Any, Optional

from crypto.config import get_flag, load_config
from crypto.exec.ledger import TradeLedger
from crypto.exec.live_gate import evaluate_live_gate
from crypto.exec.risk import (
    evaluate_daily_loss,
    live_equity_usd,
    paper_equity_usd,
    resolve_exec_mode,
    size_from_stop,
)
from crypto.providers.types import utcnow


class ExecutionService:
    def __init__(self) -> None:
        self.ledger = TradeLedger()
        self._kill_armed = False

    def _venue(self, *, prefer_live: Optional[bool] = None):
        """Pick execution venue. Live only when prefer_live / live_confirm mode."""
        from crypto.providers.registry import get_registry

        reg = get_registry()
        if prefer_live is None:
            # Status path: prefer live venue when live flag on so gate details surface
            prefer_live = get_flag("exec.live", default=False)
        if prefer_live and get_flag("exec.live", default=False):
            return reg._instantiate("execution", "delta_live")
        if get_flag("exec.paper", default=False):
            # Prefer configured paper adapter (delta paper) over mock
            meta = reg._pick("execution")
            if meta is not None and meta.name != "delta_live":
                return reg._instantiate("execution", meta.name)
            return reg._instantiate("execution", "delta")
        return reg._instantiate("execution", "mock")

    def _exec_active(self) -> bool:
        return bool(
            get_flag("exec.paper", default=False) or get_flag("exec.live", default=False)
        )

    def risk_snapshot(self) -> dict[str, Any]:
        pnl = self.ledger.realized_pnl_on()
        daily = evaluate_daily_loss(pnl, auto_flip=True)
        live_gate = evaluate_live_gate(require_flags=True)
        mode = resolve_exec_mode(
            daily_loss_hit=daily.hit,
            live_gate_ok=live_gate.ok,
        )
        return {
            "daily_loss": {
                "realized_pnl_usd": daily.realized_pnl_usd,
                "limit_usd": daily.limit_usd,
                "hit": daily.hit,
                "flipped_to_advisory": daily.flipped_to_advisory,
                "note": daily.note,
            },
            "mode": mode,
            "live_gate": live_gate.as_dict(),
            "open_intents": len(self.ledger.open_intents()),
            "kill_switch_armed": self._kill_armed,
        }

    async def status(self) -> dict[str, Any]:
        paper = get_flag("exec.paper", default=False)
        live = get_flag("exec.live", default=False)
        risk = self.risk_snapshot()
        venue_status: dict[str, Any] = {
            "venue": "none",
            "can_trade": False,
            "note": "exec.paper / exec.live off — Advisory only.",
        }
        if self._exec_active():
            try:
                # Surface live gate when live flag is on; else paper/sim venue.
                prefer_live = bool(live and risk["mode"].get("mode") == "live_confirm")
                if live and risk["mode"].get("mode") != "live_confirm":
                    # Still expose live gate via live venue status when live requested
                    # but not ready — merge paper venue status with gate.
                    live_status = await self._venue(prefer_live=True).status()
                    paper_status = (
                        await self._venue(prefer_live=False).status()
                        if paper
                        else {
                            "venue": "none",
                            "adapter": "none",
                            "can_trade": False,
                            "note": "live gate blocked; paper off",
                        }
                    )
                    venue_status = {
                        **paper_status,
                        "live_gate": live_status.get("live_gate") or risk["live_gate"],
                        "live_requested": True,
                        "note": (
                            f"{paper_status.get('note') or ''} | "
                            f"live: {live_status.get('note') or 'gate blocked'}"
                        ).strip(" |"),
                    }
                else:
                    venue_status = await self._venue(prefer_live=prefer_live).status()
            except Exception as exc:  # noqa: BLE001
                venue_status = {
                    "venue": "error",
                    "can_trade": False,
                    "note": f"execution venue status failed: {exc}",
                }
        return {
            **venue_status,
            "paper_flag": paper,
            "live_flag": live,
            "confirm_flag": get_flag("exec.confirm", default=False),
            "can_confirm": bool(risk["mode"]["can_confirm"]),
            "exec_mode": risk["mode"],
            "daily_loss": risk["daily_loss"],
            "live_gate": risk["live_gate"],
            "open_intents": risk["open_intents"],
            "kill_switch_armed": self._kill_armed,
            "kill_switch_enabled": self._exec_active(),
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
        """One-tap Confirm: submit paper/sim or live bracket from a GO card."""
        if not self._exec_active():
            return {
                "ok": False,
                "error": "exec.paper and exec.live are off — enable one before Confirm",
                "code": "exec_off",
            }
        risk = self.risk_snapshot()
        mode = risk["mode"]
        if not mode["can_confirm"]:
            return {
                "ok": False,
                "error": mode["reason"],
                "code": "confirm_blocked",
                "mode": mode,
                "live_gate": risk.get("live_gate"),
            }
        if str(card.get("status") or "").upper() != "GO":
            return {
                "ok": False,
                "error": "Only GO cards can be confirmed",
                "code": "not_go",
            }

        # Live path: re-check gate hard (fail closed)
        is_live = mode.get("mode") == "live_confirm"
        if is_live:
            gate = evaluate_live_gate(require_flags=True)
            if not gate.ok:
                return {
                    "ok": False,
                    "error": "Live gate failed — refuse live order",
                    "code": "live_gate_failed",
                    "reasons": gate.reasons,
                    "live_gate": gate.as_dict(),
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

        equity = live_equity_usd() if is_live else paper_equity_usd()
        sized = size_from_stop(
            entry=entry,
            stop=float(stop),
            size_cap_usd=float(card.get("size_cap_usd") or 0),
            equity_usd=equity,
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
        venue = self._venue(prefer_live=is_live)
        result = await venue.place_bracket(order)
        if is_live and not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error") or result.get("note") or "live venue rejected",
                "code": result.get("error") or "live_rejected",
                "venue": result,
                "sizing": sized.__dict__,
                "mode": mode,
                "reasons": result.get("reasons"),
            }

        now = utcnow()
        status = str(result.get("status") or "submitted")
        fill = result.get("fill_price")
        trade_mode = "live_confirm" if is_live else "paper_confirm"
        trade = self.ledger.record_submission(
            {
                "client_order_id": client_order_id,
                "venue": getattr(venue, "name", "paper"),
                "adapter": result.get("adapter") or ("live" if is_live else "sim"),
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
                "mode": trade_mode,
                "submitted_at": now,
                "filled_at": now if status == "sim_filled" else None,
                "payload": {
                    "actor": actor,
                    "sizing": sized.__dict__,
                    "card_score": card.get("score"),
                    "reasons": list(card.get("reasons") or [])[:3],
                    "equity_usd": equity,
                    "live": is_live,
                    "venue_result": {
                        k: result[k]
                        for k in ("ok", "adapter", "note", "venue_error", "error", "reasons")
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
            "mode": mode,
        }

    def arm_kill_switch(self) -> dict[str, Any]:
        self._kill_armed = True
        return {
            "armed": True,
            "note": "Kill switch armed — confirm again to cancel open intents.",
        }

    def disarm_kill_switch(self) -> dict[str, Any]:
        self._kill_armed = False
        return {"armed": False, "note": "Kill switch disarmed."}

    async def kill(self, *, confirmed: bool = False, actor: str = "operator") -> dict[str, Any]:
        """Two-tap kill: first call arms; second with confirmed=True cancels intents."""
        if not self._exec_active():
            return {
                "ok": False,
                "error": "exec.paper / exec.live off",
                "code": "exec_off",
            }

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
        # Cancel on whichever venue is active; also best-effort live if flagged.
        prefer_live = get_flag("exec.live", default=False) and any(
            (r.get("mode") == "live_confirm" or r.get("adapter") == "live") for r in open_rows
        )
        venue = self._venue(prefer_live=prefer_live)
        venue_result = await venue.cancel_all()
        if get_flag("exec.live", default=False) and not prefer_live:
            try:
                live_cancel = await self._venue(prefer_live=True).cancel_all()
                venue_result = {
                    "paper_or_active": venue_result,
                    "live": live_cancel,
                }
            except Exception as exc:  # noqa: BLE001
                venue_result = {**venue_result, "live_cancel_error": str(exc)}
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
            "note": "Open intents cancelled; Confirm flipped to Advisory.",
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
