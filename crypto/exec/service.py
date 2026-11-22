"""Orchestrate paper Confirm, Manual Decision logging, ledger, risk, kill-switch.

Manual Decision (``exec.live`` + ``exec.confirm``) is decision-only: Confirm
records levels for hand placement on Delta and never POSTs to the exchange.
Paper Confirm may still sim-fill for practice. ``exec.live_place`` stays off;
Confirm refuses any live place path even if that flag is flipped on.
"""

from __future__ import annotations

from typing import Any, Optional

from crypto.config import get_flag
from crypto.exec.decision import build_decision_payload, decision_json, format_decision_levels
from crypto.exec.ledger import TradeLedger
from crypto.exec.live_gate import evaluate_live_gate
from crypto.exec.risk import (
    evaluate_daily_loss,
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
        """Pick execution venue. Live exchange venue is never used by Confirm.

        ``prefer_live`` is retained for status / kill inspection only; Manual
        Decision Confirm does not call ``place_bracket`` on delta_live.
        """
        from crypto.providers.registry import get_registry

        reg = get_registry()
        if prefer_live is None:
            prefer_live = False
        # Confirm path never places live. Status may still inspect the live
        # adapter when prefer_live is explicitly True (gate diagnostics).
        if prefer_live and get_flag("exec.live", default=False):
            return reg._instantiate("execution", "delta_live")
        if get_flag("exec.paper", default=False):
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
        mode_name = risk["mode"].get("mode")
        venue_status: dict[str, Any] = {
            "venue": "none",
            "can_trade": False,
            "note": "exec.paper / exec.live off — Advisory only.",
        }
        if self._exec_active():
            try:
                if mode_name == "manual_decision":
                    # Decision-only — no exchange place. Optional paper venue for
                    # ledger practice when paper flag is also on.
                    if paper:
                        venue_status = await self._venue(prefer_live=False).status()
                        venue_status = {
                            **venue_status,
                            "manual_decision": True,
                            "can_trade": False,
                            "note": (
                                "Manual Decision — copy/export levels; "
                                "Confirm does not place on Delta. "
                                f"{venue_status.get('note') or ''}"
                            ).strip(),
                        }
                    else:
                        venue_status = {
                            "venue": "manual",
                            "adapter": "manual",
                            "can_trade": False,
                            "manual_decision": True,
                            "note": (
                                "Manual Decision — show/copy levels for hand "
                                "placement; no exchange POST from Confirm."
                            ),
                        }
                    venue_status["live_gate"] = risk["live_gate"]
                    venue_status["live_place_refused"] = True
                else:
                    venue_status = await self._venue(prefer_live=False).status()
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
            "live_place_flag": get_flag("exec.live_place", default=False),
            "can_confirm": bool(risk["mode"]["can_confirm"]),
            "exec_mode": risk["mode"],
            "daily_loss": risk["daily_loss"],
            "live_gate": risk["live_gate"],
            "open_intents": risk["open_intents"],
            "kill_switch_armed": self._kill_armed,
            "kill_switch_enabled": bool(paper),  # kill cancels paper intents only
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

    def decision_from_card(
        self,
        card: dict[str, Any],
        *,
        notes: str = "",
    ) -> dict[str, Any]:
        """Build Manual Decision export (levels + JSON) without touching the exchange."""
        entry = self._entry_from_card(card)
        stop = card.get("stop")
        sized = None
        size_usd = None
        if stop is not None and entry > 0:
            sized = size_from_stop(
                entry=entry,
                stop=float(stop),
                size_cap_usd=float(card.get("size_cap_usd") or 0),
                equity_usd=paper_equity_usd(),
            )
            size_usd = sized.size_usd if sized.size_usd > 0 else float(card.get("size_cap_usd") or 0)
        payload = build_decision_payload(card, size_usd=size_usd, notes=notes)
        return {
            "ok": True,
            "decision": payload,
            "levels_text": format_decision_levels(card, size_usd=size_usd),
            "decision_json": decision_json(card, size_usd=size_usd, notes=notes),
            "sizing": sized.__dict__ if sized else None,
        }

    async def confirm_from_card(
        self,
        card: dict[str, Any],
        *,
        actor: str = "operator",
        notes: str = "",
    ) -> dict[str, Any]:
        """Confirm a GO card: paper/sim practice, or Manual Decision log (no live place).

        When mode is ``manual_decision``, records a ledger note with levels for
        hand placement and **never** POSTs to Delta. ``exec.live_place`` cannot
        unlock exchange placement from this path.
        """
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

        # Hard refuse: Confirm never places live exchange orders.
        if get_flag("exec.live_place", default=False):
            # Flag exists for audit / future gating but Confirm stays decision-only.
            pass

        is_manual = mode.get("mode") == "manual_decision"
        if is_manual:
            return self._confirm_manual_decision(card, actor=actor, notes=notes, mode=mode)

        entry = self._entry_from_card(card)
        stop = card.get("stop")
        target = card.get("target")
        if stop is None or entry <= 0:
            return {
                "ok": False,
                "error": "GO card missing entry/stop",
                "code": "incomplete_card",
            }

        equity = paper_equity_usd()
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
        venue = self._venue(prefer_live=False)
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
                    "notes": notes or "",
                    "sizing": sized.__dict__,
                    "card_score": card.get("score"),
                    "reasons": list(card.get("reasons") or [])[:3],
                    "equity_usd": equity,
                    "live": False,
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

    def _confirm_manual_decision(
        self,
        card: dict[str, Any],
        *,
        actor: str,
        notes: str,
        mode: dict[str, Any],
    ) -> dict[str, Any]:
        """Log Manual Decision to ledger — never calls exchange place_bracket."""
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
            equity_usd=paper_equity_usd(),
        )
        size_usd = sized.size_usd if sized.size_usd > 0 else float(card.get("size_cap_usd") or 0)
        decision = build_decision_payload(card, size_usd=size_usd, notes=notes)
        levels_text = format_decision_levels(card, size_usd=size_usd)
        client_order_id = self.ledger.new_client_order_id()
        now = utcnow()
        trade = self.ledger.record_submission(
            {
                "client_order_id": client_order_id,
                "venue": "manual",
                "adapter": "manual",
                "status": "manual_noted",
                "instrument": decision["instrument"] or "BTCUSD",
                "side": decision["side"] or "buy",
                "setup": decision.get("setup") or "",
                "entry": entry,
                "target": float(target) if target is not None else None,
                "stop": float(stop),
                "size_usd": float(size_usd or 0),
                "fill_price": None,
                "realized_pnl_usd": 0.0,
                "signal_status": "GO",
                "mode": "manual_decision",
                "submitted_at": now,
                "filled_at": None,
                "closed_at": now,
                "payload": {
                    "actor": actor,
                    "notes": notes or "",
                    "decision": decision,
                    "levels_text": levels_text,
                    "sizing": sized.__dict__,
                    "card_score": card.get("score"),
                    "reasons": list(card.get("reasons") or [])[:3],
                    "live": False,
                    "exchange_post": False,
                    "note": decision["note"],
                },
            }
        )
        return {
            "ok": True,
            "trade": trade,
            "decision": decision,
            "levels_text": levels_text,
            "decision_json": decision_json(card, size_usd=size_usd, notes=notes),
            "sizing": sized.__dict__,
            "mode": mode,
            "exchange_post": False,
            "note": "Manual Decision logged — place by hand on Delta; no exchange order sent.",
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
