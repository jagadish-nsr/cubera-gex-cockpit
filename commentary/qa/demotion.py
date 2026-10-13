"""commentary/qa/demotion.py — Rolling Performance Monitoring & Automatic Demotion Policy."""

from __future__ import annotations

import logging
from typing import Any
from commentary.models import SuggestionBite
from commentary.qa.grading import evaluate_bites_performance, GradeReport

logger = logging.getLogger(__name__)


class DemotionPolicy:
    """Enforces safety guards by automatically demoting rules or mode when performance slips."""

    def __init__(
        self,
        min_win_rate_threshold: float = 0.48,
        min_expectancy_threshold: float = 0.10,
        rolling_window_size: int = 20,
    ) -> None:
        self.min_win_rate = min_win_rate_threshold
        self.min_expectancy = min_expectancy_threshold
        self.window_size = rolling_window_size
        self.quarantined_rules: set[str] = set()

    def evaluate_system_mode(
        self,
        recent_bites: list[SuggestionBite],
        current_mode: str = "shadow",
    ) -> tuple[str, str]:
        """Check rolling window of completed bites and determine if system should remain live or demote to shadow.

        Returns:
            (recommended_mode, reason)
        """
        if current_mode == "shadow":
            return "shadow", "System operating in baseline shadow mode"

        # Evaluate only triggered/decisive bites
        decisive = [b for b in recent_bites if b.state in ("TARGET_HIT", "STOPPED")][-self.window_size:]
        if len(decisive) < 10:
            return current_mode, f"Insufficient sample size ({len(decisive)} < 10) to trigger demotion"

        report: GradeReport = evaluate_bites_performance(decisive)

        if report.win_rate < self.min_win_rate:
            reason = (
                f"Automatic demotion triggered: rolling win rate ({report.win_rate:.1%}) "
                f"fell below threshold ({self.min_win_rate:.1%})"
            )
            logger.warning(reason)
            return "shadow", reason

        if report.post_cost_expectancy < self.min_expectancy:
            reason = (
                f"Automatic demotion triggered: rolling expectancy ({report.post_cost_expectancy:+.2f}R) "
                f"fell below safety threshold ({self.min_expectancy:+.2f}R)"
            )
            logger.warning(reason)
            return "shadow", reason

        return "live", f"System healthy: win rate {report.win_rate:.1%}, expectancy {report.post_cost_expectancy:+.2f}R"

    def check_rule_quarantine(self, rule_id: str, rule_bites: list[SuggestionBite]) -> bool:
        """Check if a specific strategy rule should be quarantined due to poor performance."""
        decisive = [b for b in rule_bites if b.state in ("TARGET_HIT", "STOPPED")][-10:]
        if len(decisive) >= 10:
            report = evaluate_bites_performance(decisive)
            if report.post_cost_expectancy < 0.0:
                self.quarantined_rules.add(rule_id)
                logger.warning(f"Quarantined rule {rule_id}: negative expectancy {report.post_cost_expectancy:+.2f}R")
                return True
        return False
