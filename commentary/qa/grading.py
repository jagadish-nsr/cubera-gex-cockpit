"""commentary/qa/grading.py — Statistical Performance Grading & Expectancy Evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from commentary.models import SuggestionBite


@dataclass
class GradeReport:
    """Summary metrics of suggestion bites execution."""
    total_bites: int
    triggered_count: int
    target_hit_count: int
    stopped_count: int
    expired_count: int
    invalidated_count: int
    win_rate: float                         # target_hit / (target_hit + stopped)
    mean_realized_r: float
    post_cost_expectancy: float             # In R units after estimated 0.08R friction
    is_promotable: bool                     # Win rate >= 52% and Expectancy >= +0.25R
    details: dict[str, Any] = field(default_factory=dict)


def evaluate_bites_performance(
    bites: list[SuggestionBite],
    cost_per_trade_r: float = 0.08,
) -> GradeReport:
    """Calculate statistical performance, hit rate, and post-cost expectancy across bites."""
    total = len(bites)
    if total == 0:
        return GradeReport(
            total_bites=0,
            triggered_count=0,
            target_hit_count=0,
            stopped_count=0,
            expired_count=0,
            invalidated_count=0,
            win_rate=0.0,
            mean_realized_r=0.0,
            post_cost_expectancy=0.0,
            is_promotable=False,
        )

    hits = 0
    stops = 0
    expired = 0
    invalidated = 0
    triggered = 0

    realized_rs: list[float] = []

    for b in bites:
        state = b.state
        if state in ("TRIGGERED", "TARGET_HIT", "STOPPED") or (state == "EXPIRED" and b.outcome and "realized_r" in b.outcome):
            triggered += 1

        if state == "TARGET_HIT":
            hits += 1
            r = float(b.outcome.get("realized_r", 2.0)) if b.outcome else 2.0
            realized_rs.append(r)
        elif state == "STOPPED":
            stops += 1
            realized_rs.append(-1.0)
        elif state == "EXPIRED":
            expired += 1
            if b.outcome and "realized_r" in b.outcome:
                realized_rs.append(float(b.outcome["realized_r"]))
        elif state == "INVALIDATED":
            invalidated += 1

    decisive_trades = hits + stops
    win_rate = (hits / decisive_trades) if decisive_trades > 0 else 0.0
    mean_r = (sum(realized_rs) / len(realized_rs)) if realized_rs else 0.0

    avg_win_r = (sum([r for r in realized_rs if r > 0]) / hits) if hits > 0 else 0.0
    avg_loss_r = 1.0  # Normalized 1R

    expectancy = (win_rate * avg_win_r) - ((1.0 - win_rate) * avg_loss_r) - cost_per_trade_r

    # Promotion criteria: Win rate >= 52%, post-cost expectancy >= +0.25R, min 20 decisive samples
    is_promotable = (win_rate >= 0.52) and (expectancy >= 0.25) and (decisive_trades >= 20)

    return GradeReport(
        total_bites=total,
        triggered_count=triggered,
        target_hit_count=hits,
        stopped_count=stops,
        expired_count=expired,
        invalidated_count=invalidated,
        win_rate=round(win_rate, 4),
        mean_realized_r=round(mean_r, 3),
        post_cost_expectancy=round(expectancy, 3),
        is_promotable=is_promotable,
        details={
            "decisive_trades": decisive_trades,
            "avg_win_r": round(avg_win_r, 2),
            "cost_friction_r": cost_per_trade_r,
        },
    )
