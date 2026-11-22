"""Paper risk sizing and daily-loss → Advisory flip.

Risk is enforced in code (not only UI). Live Auto is out of scope.
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


def risk_fraction() -> float:
    return float(_risk_cfg().get("risk_fraction") or 0.005)


def daily_loss_limit_usd() -> float:
    return float(_risk_cfg().get("daily_loss_limit_usd") or 250.0)


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


def resolve_exec_mode(*, daily_loss_hit: bool = False) -> dict[str, Any]:
    """Advisory default; Paper Confirm only when both paper+confirm flags on and risk ok."""
    paper = get_flag("exec.paper", default=False)
    confirm = get_flag("exec.confirm", default=False)
    if daily_loss_hit:
        return {
            "mode": "advisory",
            "badge": "Advisory",
            "paper": paper,
            "confirm": False,
            "can_confirm": False,
            "reason": "daily loss limit — Confirm disabled",
        }
    if paper and confirm:
        return {
            "mode": "paper_confirm",
            "badge": "Paper Confirm",
            "paper": True,
            "confirm": True,
            "can_confirm": True,
            "reason": "paper Confirm enabled (demo/sim only)",
        }
    if paper:
        return {
            "mode": "advisory",
            "badge": "Advisory",
            "paper": True,
            "confirm": False,
            "can_confirm": False,
            "reason": "paper ledger on; Confirm flag off — Advisory default",
        }
    return {
        "mode": "advisory",
        "badge": "Advisory",
        "paper": False,
        "confirm": False,
        "can_confirm": False,
        "reason": "Advisory only — exec.paper off",
    }
