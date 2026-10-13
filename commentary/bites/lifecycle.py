"""commentary/bites/lifecycle.py — Minute-by-Minute Lifecycle Evaluator for Suggestion Bites."""

from __future__ import annotations

import datetime as dt
from typing import Any
from commentary.models import SuggestionBite


class BiteLifecycleEvaluator:
    """Evaluates state transitions and realized performance of Suggestion Bites."""

    @staticmethod
    def evaluate(
        bite: SuggestionBite,
        candle: dict[str, Any],
        current_spot: float,
        as_of: dt.datetime,
    ) -> SuggestionBite:
        """Update bite state based on current candle, spot price, and timestamp."""
        # Terminal states do not transition further
        if bite.state in ("TARGET_HIT", "STOPPED", "INVALIDATED", "EXPIRED"):
            return bite

        high = float(candle.get("high", current_spot))
        low = float(candle.get("low", current_spot))
        close = float(candle.get("close", current_spot))

        trig = float(bite.levels.get("trigger", current_spot))
        inval = float(bite.levels.get("invalidation", current_spot))
        tgt = float(bite.levels.get("target", current_spot))
        is_bullish = bite.scenario_link == "bull"

        # 1. State: ISSUED
        if bite.state == "ISSUED":
            # Expiry check
            if as_of > bite.valid_until:
                bite.state = "EXPIRED"
                bite.resolved_ts = as_of
                bite.outcome = {"status": "EXPIRED", "realized_r": 0.0, "exit_spot": current_spot}
                return bite

            # Invalidation before trigger
            if is_bullish and low <= inval:
                bite.state = "INVALIDATED"
                bite.resolved_ts = as_of
                bite.outcome = {"status": "INVALIDATED", "realized_r": 0.0, "exit_spot": low}
                return bite
            elif not is_bullish and bite.scenario_link == "bear" and high >= inval:
                bite.state = "INVALIDATED"
                bite.resolved_ts = as_of
                bite.outcome = {"status": "INVALIDATED", "realized_r": 0.0, "exit_spot": high}
                return bite

            # Trigger condition evaluation
            op = bite.condition.get("op", ">")
            val = float(bite.condition.get("val", trig))
            field = bite.condition.get("field", "close_5m")

            test_val = close if "close" in field else current_spot
            triggered = False
            if op == ">" and test_val > val:
                triggered = True
            elif op == ">=" and test_val >= val:
                triggered = True
            elif op == "<" and test_val < val:
                triggered = True
            elif op == "<=" and test_val <= val:
                triggered = True

            if triggered:
                bite.state = "TRIGGERED"

        # 2. State: TRIGGERED
        if bite.state == "TRIGGERED":
            rr = float(bite.risk.get("rr", 2.0))

            if is_bullish:
                # Target Hit
                if high >= tgt:
                    bite.state = "TARGET_HIT"
                    bite.resolved_ts = as_of
                    bite.outcome = {"status": "TARGET_HIT", "realized_r": rr, "exit_spot": tgt}
                    return bite
                # Stopped
                elif low <= inval:
                    bite.state = "STOPPED"
                    bite.resolved_ts = as_of
                    bite.outcome = {"status": "STOPPED", "realized_r": -1.0, "exit_spot": inval}
                    return bite

            elif bite.scenario_link == "bear":
                # Target Hit
                if low <= tgt:
                    bite.state = "TARGET_HIT"
                    bite.resolved_ts = as_of
                    bite.outcome = {"status": "TARGET_HIT", "realized_r": rr, "exit_spot": tgt}
                    return bite
                # Stopped
                elif high >= inval:
                    bite.state = "STOPPED"
                    bite.resolved_ts = as_of
                    bite.outcome = {"status": "STOPPED", "realized_r": -1.0, "exit_spot": inval}
                    return bite

            # Expiry while triggered -> evaluate scratch / partial R
            if as_of > bite.valid_until:
                risk_dist = abs(trig - inval)
                if risk_dist > 0:
                    realized_r = (close - trig) / risk_dist if is_bullish else (trig - close) / risk_dist
                else:
                    realized_r = 0.0
                bite.state = "EXPIRED"
                bite.resolved_ts = as_of
                bite.outcome = {
                    "status": "EXPIRED",
                    "realized_r": round(float(realized_r), 2),
                    "exit_spot": close,
                }
                return bite

        return bite
