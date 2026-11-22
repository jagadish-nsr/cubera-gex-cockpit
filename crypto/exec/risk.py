"""Risk sizing and daily-loss → Advisory flip.

Risk is enforced in code (not only UI). Live Auto is out of scope.
Live Confirm uses the same fraction/caps with ``live_equity_usd`` when gated on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

from crypto.config import get_flag, load_config, set_flag


@dataclass
class SizeResult:
    size_usd: float
    risk_budget_usd: float
    stop_distance: float
    capped: bool
    reason: str = ""


@dataclass
class DailyLossState:
    realized_pnl_usd: float
    limit_usd: float
    hit: bool
    flipped_to_advisory: bool
    note: str = ""


def _risk_cfg() -> dict[str, Any]:
    return dict((load_config().get("risk") or {}))


def paper_equity_usd() -> float:
    return float(_risk_cfg().get("paper_equity_usd") or 10_000.0)


def live_equity_usd() -> float:
    return float(_risk_cfg().get("live_equity_usd") or _risk_cfg().get("paper_equity_usd") or 10_000.0)


def risk_fraction() -> float:
    return float(_risk_cfg().get("risk_fraction") or 0.005)


def daily_loss_limit_usd() -> float:
    return float(_risk_cfg().get("daily_loss_limit_usd") or 250.0)


def size_cap_usd() -> float:
    return float(_risk_cfg().get("size_cap_usd") or 2500.0)


def size_from_stop(
    *,
    entry: float,
    stop: float,
    size_cap_usd: float,
    equity_usd: Optional[float] = None,
    risk_frac: Optional[float] = None,
) -> SizeResult:
    """Size notional from stop distance, capped by GO-card size_cap."""
    eq = float(equity_usd if equity_usd is not None else paper_equity_usd())
    frac = float(risk_frac if risk_frac is not None else risk_fraction())
    cap = max(0.0, float(size_cap_usd or 0.0))
    stop_dist = abs(float(entry) - float(stop))
    if entry <= 0 or stop_dist <= 0 or cap <= 0:
        return SizeResult(
            size_usd=0.0,
            risk_budget_usd=eq * frac,
            stop_distance=stop_dist,
            capped=False,
            reason="invalid entry/stop or zero size_cap",
        )
    risk_budget = eq * frac
    # notional * (stop_dist / entry) ≈ risk_budget
    notional = risk_budget * (float(entry) / stop_dist)
    capped = notional > cap
    size = min(notional, cap)
    return SizeResult(
        size_usd=round(size, 2),
        risk_budget_usd=round(risk_budget, 2),
        stop_distance=stop_dist,
        capped=capped,
        reason="capped by size_cap" if capped else "risk_fraction × equity / stop%",
    )


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def evaluate_daily_loss(
    realized_pnl_today: float,
    *,
    limit_usd: Optional[float] = None,
    auto_flip: bool = True,
) -> DailyLossState:
    """If daily realized loss exceeds limit, force Advisory (clear exec.confirm)."""
    lim = float(limit_usd if limit_usd is not None else daily_loss_limit_usd())
    loss = -min(0.0, float(realized_pnl_today))
    hit = lim > 0 and loss >= lim
    flipped = False
    note = ""
    if hit:
        note = f"daily loss {loss:.2f} ≥ limit {lim:.2f} → Advisory"
        if auto_flip and get_flag("exec.confirm", default=False):
            set_flag("exec.confirm", False, actor="risk.daily_loss")
            flipped = True
    return DailyLossState(
        realized_pnl_usd=float(realized_pnl_today),
        limit_usd=lim,
        hit=hit,
        flipped_to_advisory=flipped,
        note=note or "within daily loss limit",
    )


def resolve_exec_mode(
    *,
    daily_loss_hit: bool = False,
    live_gate_ok: Optional[bool] = None,
) -> dict[str, Any]:
    """Advisory default; Paper / Manual Decision when flags + risk allow.

    ``exec.live`` + ``exec.confirm`` → **Manual Decision** (decision card for
    placing by hand on Delta). Never Live Auto. Exchange POST is not part of
    Confirm; optional ``exec.live_place`` stays off and is refused from Confirm.

    When both paper and live flags are on with confirm: Manual Decision takes
    priority for the badge (decision-only). Paper Confirm remains available
    for practice when live is off. Auto is never enabled.
    """
    paper = get_flag("exec.paper", default=False)
    live = get_flag("exec.live", default=False)
    confirm = get_flag("exec.confirm", default=False)
    auto = get_flag("exec.auto", default=False)
    live_place = get_flag("exec.live_place", default=False)
    # live_gate_ok retained for call-site compatibility; Manual Decision
    # does not require the gate (decision-only Confirm).
    _ = live_gate_ok

    if auto:
        return {
            "mode": "advisory",
            "badge": "Advisory",
            "paper": paper,
            "live": live,
            "confirm": False,
            "can_confirm": False,
            "live_place": False,
            "reason": "exec.auto is refused — Auto not supported; Confirm cleared path blocked",
        }

    if daily_loss_hit:
        return {
            "mode": "advisory",
            "badge": "Advisory",
            "paper": paper,
            "live": live,
            "confirm": False,
            "can_confirm": False,
            "live_place": False,
            "reason": "daily loss limit — Confirm disabled",
        }

    # Manual Decision: decision support for hand-placed trades (no exchange POST).
    # Live keys / gate are NOT required — Confirm logs / exports levels only.
    if live and confirm:
        return {
            "mode": "manual_decision",
            "badge": "Manual Decision",
            "paper": paper,
            "live": True,
            "confirm": True,
            "can_confirm": True,
            "live_place": bool(live_place),
            "reason": (
                "Manual Decision — show/copy levels for hand placement on Delta; "
                "Confirm does not place live orders"
                + (
                    " (exec.live_place is on but Confirm still refuses exchange POST)"
                    if live_place
                    else ""
                )
            ),
        }

    if paper and confirm:
        return {
            "mode": "paper_confirm",
            "badge": "Paper",
            "paper": True,
            "live": False,
            "confirm": True,
            "can_confirm": True,
            "live_place": False,
            "reason": "Paper Confirm enabled (local sim / demo practice only)",
        }

    if live and not confirm:
        return {
            "mode": "advisory",
            "badge": "Advisory",
            "paper": paper,
            "live": True,
            "confirm": False,
            "can_confirm": False,
            "live_place": False,
            "reason": "exec.live on; Confirm flag off — Advisory default",
        }

    if paper:
        return {
            "mode": "advisory",
            "badge": "Advisory",
            "paper": True,
            "live": False,
            "confirm": False,
            "can_confirm": False,
            "live_place": False,
            "reason": "paper ledger on; Confirm flag off — Advisory default",
        }

    return {
        "mode": "advisory",
        "badge": "Advisory",
        "paper": False,
        "live": False,
        "confirm": False,
        "can_confirm": False,
        "live_place": False,
        "reason": "Advisory only — exec.paper / exec.live off",
    }
