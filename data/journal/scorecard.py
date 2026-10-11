"""data/journal/scorecard.py — Aggregate descriptive stats for paper trades.

Design rules (from wife-dashboard-scope.md):
  - DESCRIPTIVE only — no optimisation, no auto-parameter-tuning.
  - Costs always included in every P&L figure.
  - Segmented by: GEX regime (calm/wild/mid), strategy type, and outcome.
  - This data feeds Naveen's OFFLINE, OUT-OF-SAMPLE research — never an in-app
    optimiser.

No streamlit import — pure computation, unit-testable offline.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.journal.store import PaperTrade


def compute_scorecard(trades: list["PaperTrade"]) -> dict:
    """Compute aggregate stats over a list of CLOSED PaperTrade objects.

    Returns a dict with:
      total_trades    int
      win_rate        float (0-100)
      total_net_pnl   float (₹, net of all costs)
      avg_winner      float (₹)
      avg_loser       float (₹)
      profit_factor   float | None  (gross_wins / |gross_losses|)
      by_regime       {regime_label: {n, wins, net_pnl, win_rate}}
      by_strategy     {strategy_name: {n, wins, net_pnl, win_rate}}
      equity_curve    [float]  cumulative net P&L per closed trade (oldest first)
    """
    closed = [t for t in trades if t.status == "CLOSED" and t.net_realized_pnl is not None]

    if not closed:
        return _empty_scorecard()

    wins = [t for t in closed if (t.net_realized_pnl or 0.0) > 0]
    losses = [t for t in closed if (t.net_realized_pnl or 0.0) <= 0]

    total_net = sum(t.net_realized_pnl for t in closed)  # type: ignore[arg-type]
    gross_wins = sum(t.net_realized_pnl for t in wins) if wins else 0.0
    gross_loss = sum(abs(t.net_realized_pnl) for t in losses) if losses else 0.0  # type: ignore[arg-type]

    profit_factor = (gross_wins / gross_loss) if gross_loss > 0 else None

    # Equity curve (cumulative net P&L, chronological)
    sorted_closed = sorted(closed, key=lambda t: t.exit_ts or "")
    equity: list[float] = []
    running = 0.0
    for t in sorted_closed:
        running += t.net_realized_pnl or 0.0
        equity.append(round(running, 2))

    # Breakdown by GEX regime
    by_regime: dict = {}
    for t in closed:
        regime = (t.entry_regime.get("regime") or
                  t.entry_regime.get("vol_regime") or "unknown")
        _add_bucket(by_regime, regime, t)

    # Breakdown by strategy name
    by_strategy: dict = {}
    for t in closed:
        _add_bucket(by_strategy, t.strategy_name, t)

    return {
        "total_trades": len(closed),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
        "total_net_pnl": round(total_net, 2),
        "avg_winner": round(gross_wins / len(wins), 2) if wins else 0.0,
        "avg_loser": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "by_regime": by_regime,
        "by_strategy": by_strategy,
        "equity_curve": equity,
    }


def _add_bucket(buckets: dict, key: str, trade: "PaperTrade") -> None:
    if key not in buckets:
        buckets[key] = {"n": 0, "wins": 0, "net_pnl": 0.0, "win_rate": 0.0}
    b = buckets[key]
    b["n"] += 1
    pnl = trade.net_realized_pnl or 0.0
    if pnl > 0:
        b["wins"] += 1
    b["net_pnl"] = round(b["net_pnl"] + pnl, 2)
    b["win_rate"] = round(b["wins"] / b["n"] * 100, 1)


def _empty_scorecard() -> dict:
    return {
        "total_trades": 0,
        "win_rate": 0.0,
        "total_net_pnl": 0.0,
        "avg_winner": 0.0,
        "avg_loser": 0.0,
        "profit_factor": None,
        "by_regime": {},
        "by_strategy": {},
        "equity_curve": [],
    }
