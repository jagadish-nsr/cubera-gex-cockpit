"""sentinel/engine/classifier.py — OrderFlow Sentinel Signal Classifiers & Scoring.

Implements Sentinel §7.5:
  - Classifiers:
      * CONTINUATION_BULL / CONTINUATION_BEAR
      * ABSORPTION_BULL / ABSORPTION_BEAR
      * EXHAUSTION_UP / EXHAUSTION_DOWN
      * CVD_DIVERGENCE_BEAR / CVD_DIVERGENCE_BULL
  - 0–100 Confidence Scoring Engine with GEX Wall & VWAP context bonuses
  - 3-Bar Cooldown Enforcement per signal type
  - Concrete Price Invalidation Cutoff
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sentinel.models import Bar, SentinelSignal
from sentinel.engine.context import MarketContext
from sentinel.engine.rvol import compute_tod_rvol

log = logging.getLogger(__name__)

COOLDOWN_BARS = 3


class SignalClassifierEngine:
    """Classifies completed bars into actionable order flow signals."""

    def __init__(self) -> None:
        self.last_signal_bar_idx: dict[str, int] = {}
        self.bar_counter: int = 0
        self.prev_bar: Bar | None = None
        self.prev_high: float = 0.0
        self.prev_low: float = float("inf")
        self.prev_cvd: float = 0.0

    def evaluate_bar(
        self,
        bar: Bar,
        context: MarketContext,
    ) -> SentinelSignal | None:
        """Evaluate a completed bar and return a SentinelSignal if conditions trigger."""
        self.bar_counter += 1
        rvol = compute_tod_rvol(bar.start_ts, float(bar.volume_units), instrument=bar.instrument_id)
        tags = context.get_context_tags(bar)

        bar_range = max(1.0, bar.high - bar.low)
        close_pos = (bar.close - bar.low) / bar_range  # 0.0 = low, 1.0 = high
        upper_wick = (bar.high - max(bar.open, bar.close)) / bar_range
        lower_wick = (min(bar.open, bar.close) - bar.low) / bar_range

        sig_type: str | None = None
        category: str = "NEUTRAL"
        bias: str = "NEUTRAL"
        base_score = 45
        insight = ""
        invalidation = bar.low - 5.0
        gex_alignment = "NEUTRAL"

        # -------------------------------------------------------------------
        # 1. ABSORPTION (Limit order wall absorbs market aggression)
        # -------------------------------------------------------------------
        if rvol >= 1.4 and bar.delta_ratio > 0.20 and (close_pos <= 0.50 or upper_wick >= 0.40):
            if "NEAR_CALL_WALL" in tags or "ABOVE_VWAP_2SIGMA" in tags or "AT_DAY_HIGH" in tags:
                sig_type = "ABSORPTION_BEAR"
                category = "BEARISH"
                bias = "SHORT"
                base_score = 65
                invalidation = bar.high + 5.0
                wall_ref = f" at Call Wall ({context.call_wall:,.0f})" if "NEAR_CALL_WALL" in tags else ""
                insight = (
                    f"Heavy aggressive buying (Δ {bar.delta:+,.0f}, {rvol:.1f}x volume) absorbed by passive sellers{wall_ref}. "
                    f"Price failed to advance higher."
                )
                gex_alignment = "CONFIRMED_AT_WALL" if "NEAR_CALL_WALL" in tags else "NEUTRAL"

        elif rvol >= 1.4 and bar.delta_ratio < -0.20 and (close_pos >= 0.50 or lower_wick >= 0.40):
            if "NEAR_PUT_WALL" in tags or "BELOW_VWAP_2SIGMA" in tags or "AT_DAY_LOW" in tags:
                sig_type = "ABSORPTION_BULL"
                category = "BULLISH"
                bias = "LONG"
                base_score = 65
                invalidation = bar.low - 5.0
                wall_ref = f" at Put Wall ({context.put_wall:,.0f})" if "NEAR_PUT_WALL" in tags else ""
                insight = (
                    f"Aggressive selling (Δ {bar.delta:+,.0f}, {rvol:.1f}x volume) absorbed by passive buyers{wall_ref}. "
                    f"Price refused to break lower."
                )
                gex_alignment = "CONFIRMED_AT_WALL" if "NEAR_PUT_WALL" in tags else "NEUTRAL"

        # -------------------------------------------------------------------
        # 2. CONTINUATION (High-volume impulse in direction of delta)
        # -------------------------------------------------------------------
        elif rvol >= 1.5 and bar.delta_ratio >= 0.25 and close_pos >= 0.70:
            if "ABOVE_VWAP" in tags or "ABOVE_ORB_HIGH" in tags:
                sig_type = "CONTINUATION_BULL"
                category = "BULLISH"
                bias = "LONG"
                base_score = 60
                invalidation = bar.low - 5.0
                insight = (
                    f"Strong buyer continuation: {rvol:.1f}x norm volume, delta ratio {bar.delta_ratio:+.2f}, "
                    f"firm close near high above VWAP."
                )
                gex_alignment = "COUNTER_TREND" if "NEAR_CALL_WALL" in tags else "NEUTRAL"

        elif rvol >= 1.5 and bar.delta_ratio <= -0.25 and close_pos <= 0.30:
            if "BELOW_VWAP" in tags or "BELOW_ORB_LOW" in tags:
                sig_type = "CONTINUATION_BEAR"
                category = "BEARISH"
                bias = "SHORT"
                base_score = 60
                invalidation = bar.high + 5.0
                insight = (
                    f"Strong seller continuation: {rvol:.1f}x norm volume, delta ratio {bar.delta_ratio:+.2f}, "
                    f"firm close near low below VWAP."
                )
                gex_alignment = "COUNTER_TREND" if "NEAR_PUT_WALL" in tags else "NEUTRAL"

        # -------------------------------------------------------------------
        # 3. EXHAUSTION (Volume climax with immediate rejection)
        # -------------------------------------------------------------------
        elif rvol >= 2.5:
            if close_pos <= 0.35 and upper_wick >= 0.45:
                sig_type = "EXHAUSTION_UP"
                category = "BEARISH"
                bias = "SHORT"
                base_score = 62
                invalidation = bar.high + 5.0
                insight = f"Buying climax exhausted: {rvol:.1f}x extreme volume rejected at high with long upper tail."
            elif close_pos >= 0.65 and lower_wick >= 0.45:
                sig_type = "EXHAUSTION_DOWN"
                category = "BULLISH"
                bias = "LONG"
                base_score = 62
                invalidation = bar.low - 5.0
                insight = f"Selling climax exhausted: {rvol:.1f}x extreme volume rejected at low with long lower tail."

        # -------------------------------------------------------------------
        # 4. CVD DIVERGENCE
        # -------------------------------------------------------------------
        elif self.prev_bar is not None:
            if bar.high > self.prev_bar.high and bar.cvd < self.prev_bar.cvd and rvol >= 1.2:
                sig_type = "CVD_DIVERGENCE_BEAR"
                category = "BEARISH"
                bias = "SHORT"
                base_score = 58
                invalidation = bar.high + 5.0
                insight = f"Bearish CVD Divergence: Price made higher high ({bar.high:,.1f}) while CVD drifted lower."
            elif bar.low < self.prev_bar.low and bar.cvd > self.prev_bar.cvd and rvol >= 1.2:
                sig_type = "CVD_DIVERGENCE_BULL"
                category = "BULLISH"
                bias = "LONG"
                base_score = 58
                invalidation = bar.low - 5.0
                insight = f"Bullish CVD Divergence: Price made lower low ({bar.low:,.1f}) while CVD drifted higher."

        self.prev_bar = bar

        if not sig_type:
            return None

        # Check 3-bar cooldown
        last_seen = self.last_signal_bar_idx.get(sig_type, -100)
        if (self.bar_counter - last_seen) < COOLDOWN_BARS:
            log.debug("Signal %s suppressed by 3-bar cooldown", sig_type)
            return None
        self.last_signal_bar_idx[sig_type] = self.bar_counter

        # Calculate final 0–100 score
        score = base_score
        if rvol >= 2.0:
            score += 15
        elif rvol >= 1.5:
            score += 8

        if gex_alignment == "CONFIRMED_AT_WALL":
            score += 15
        if abs(bar.delta_ratio) >= 0.35:
            score += 10

        final_score = min(100, max(0, score))
        confidence = "HIGH" if final_score >= 70 else "MEDIUM" if final_score >= 50 else "LOW"

        ts_str = bar.start_ts.strftime("%Y%m%d_%H%M%S")
        event_id = f"sig_{ts_str}_{bar.instrument_id}_{bar.timeframe}_{sig_type}"

        return SentinelSignal(
            event_id=event_id,
            instrument_id=bar.instrument_id,
            timeframe=bar.timeframe,
            bar_start_ts=bar.start_ts,
            type=sig_type,
            category=category,
            bias=bias,
            score=final_score,
            confidence=confidence,
            rvol_tod=rvol,
            delta=bar.delta,
            delta_ratio=bar.delta_ratio,
            oi_change=bar.delta_oi,
            context_tags=tags,
            insight_text=insight,
            invalidation_level=round(invalidation, 2),
            rule_set_version="v2.0",
            status="NEW",
            gex_alignment=gex_alignment,
        )
