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

        inst = bar.instrument_id.upper()
        if "SENSEX" in inst:
            min_buf = 50.0
            strike_step = 100
        elif "BANKNIFTY" in inst:
            min_buf = 35.0
            strike_step = 100
        else:
            min_buf = 15.0
            strike_step = 50

        # Structural volatility buffer: at least min_buf pts, scaled with bar range noise
        vol_buffer = max(min_buf, round(bar_range * 0.5, 1))

        sig_type: str | None = None
        category: str = "NEUTRAL"
        bias: str = "NEUTRAL"
        base_score = 45
        insight = ""
        invalidation = bar.low - vol_buffer
        gex_alignment = "NEUTRAL"

        # -------------------------------------------------------------------
        # 1. TRAPPED VOLUME & ABSORPTION (Institutional wall defense)
        # -------------------------------------------------------------------
        # Trapped buyers / Ask absorption at resistance
        if (rvol >= 1.4 and bar.delta_ratio > 0.18 and (close_pos <= 0.45 or upper_wick >= 0.40)) or (
            rvol >= 1.3 and bar.delta_ratio > 0.15 and bar.depth_imbalance <= -0.20 and "NEAR_CALL_WALL" in tags
        ):
            if "NEAR_CALL_WALL" in tags or "ABOVE_VWAP_2SIGMA" in tags or "AT_DAY_HIGH" in tags:
                sig_type = "ABSORPTION_BEAR" if "NEAR_CALL_WALL" in tags else "TRAPPED_BUYERS_EXHAUSTION"
                category = "BEARISH"
                bias = "SHORT"
                base_score = 70 if "NEAR_CALL_WALL" in tags else 65
                invalidation = bar.high + vol_buffer
                wall_ref = f" at Call Wall ({context.call_wall:,.0f})" if "NEAR_CALL_WALL" in tags else ""
                insight = (
                    f"Aggressive buyers trapped (Δ {bar.delta:+,.0f}, {rvol:.1f}x vol, depth imbalance {bar.depth_imbalance:+.2f}){wall_ref}. "
                    f"Price rejected from highs."
                )
                gex_alignment = "CONFIRMED_AT_WALL" if "NEAR_CALL_WALL" in tags else "NEUTRAL"

        # Trapped sellers / Bid absorption at support
        elif (rvol >= 1.4 and bar.delta_ratio < -0.18 and (close_pos >= 0.55 or lower_wick >= 0.40)) or (
            rvol >= 1.3 and bar.delta_ratio < -0.15 and bar.depth_imbalance >= 0.20 and "NEAR_PUT_WALL" in tags
        ):
            if "NEAR_PUT_WALL" in tags or "BELOW_VWAP_2SIGMA" in tags or "AT_DAY_LOW" in tags:
                sig_type = "ABSORPTION_BULL" if "NEAR_PUT_WALL" in tags else "TRAPPED_SELLERS_ABSORPTION"
                category = "BULLISH"
                bias = "LONG"
                base_score = 70 if "NEAR_PUT_WALL" in tags else 65
                invalidation = bar.low - vol_buffer
                wall_ref = f" at Put Wall ({context.put_wall:,.0f})" if "NEAR_PUT_WALL" in tags else ""
                insight = (
                    f"Aggressive sellers absorbed (Δ {bar.delta:+,.0f}, {rvol:.1f}x vol, depth imbalance {bar.depth_imbalance:+.2f}){wall_ref}. "
                    f"Bids defended support."
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
                base_score = 62
                invalidation = bar.low - vol_buffer
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
                base_score = 62
                invalidation = bar.high + vol_buffer
                insight = (
                    f"Strong seller continuation: {rvol:.1f}x norm volume, delta ratio {bar.delta_ratio:+.2f}, "
                    f"firm close near low below VWAP."
                )
                gex_alignment = "COUNTER_TREND" if "NEAR_PUT_WALL" in tags else "NEUTRAL"

        # -------------------------------------------------------------------
        # 3. EXHAUSTION (Volume climax with immediate rejection)
        # -------------------------------------------------------------------
        elif rvol >= 2.2:
            if close_pos <= 0.35 and upper_wick >= 0.40:
                sig_type = "EXHAUSTION_UP"
                category = "BEARISH"
                bias = "SHORT"
                base_score = 64
                invalidation = bar.high + vol_buffer
                insight = f"Buying climax exhausted: {rvol:.1f}x extreme volume rejected at high with long upper tail."
            elif close_pos >= 0.65 and lower_wick >= 0.40:
                sig_type = "EXHAUSTION_DOWN"
                category = "BULLISH"
                bias = "LONG"
                base_score = 64
                invalidation = bar.low - vol_buffer
                insight = f"Selling climax exhausted: {rvol:.1f}x extreme volume rejected at low with long lower tail."

        # -------------------------------------------------------------------
        # 4. CVD DIVERGENCE
        # -------------------------------------------------------------------
        elif self.prev_bar is not None:
            if bar.high > self.prev_bar.high and bar.cvd < self.prev_bar.cvd and rvol >= 1.2:
                sig_type = "CVD_DIVERGENCE_BEAR"
                category = "BEARISH"
                bias = "SHORT"
                base_score = 60
                invalidation = bar.high + vol_buffer
                insight = f"Bearish CVD Divergence: Price made higher high ({bar.high:,.1f}) while CVD drifted lower."
            elif bar.low < self.prev_bar.low and bar.cvd > self.prev_bar.cvd and rvol >= 1.2:
                sig_type = "CVD_DIVERGENCE_BULL"
                category = "BULLISH"
                bias = "LONG"
                base_score = 60
                invalidation = bar.low - vol_buffer
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
        if abs(getattr(bar, "depth_imbalance", 0.0)) >= 0.20:
            score += 8

        final_score = min(100, max(0, score))
        confidence = "HIGH" if final_score >= 70 else "MEDIUM" if final_score >= 50 else "LOW"

        entry_price = bar.close
        is_long = bias == "LONG"
        risk_pts = max(10.0, abs(entry_price - invalidation))
        target_1 = round(risk_pts * 1.5, 1)
        target_2 = round(risk_pts * 2.5, 1)
        rr_ratio = round(target_1 / risk_pts, 2)

        # Calibrate derivative action setup
        atm_strike = round(entry_price / strike_step) * strike_step
        if is_long:
            play = f"Bull Put Spread {int(atm_strike)}/{int(atm_strike - strike_step)} PE"
        else:
            play = f"Bear Call Spread {int(atm_strike)}/{int(atm_strike + strike_step)} CE"

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
            entry_price=round(entry_price, 2),
            target_1_pts=target_1,
            target_2_pts=target_2,
            risk_reward_ratio=rr_ratio,
            actionable_play=play,
            trigger_speed="CONFIRMED_BAR",
            rule_set_version="v2.1",
            status="NEW",
            gex_alignment=gex_alignment,
        )

    def evaluate_micro_burst(
        self,
        burst: dict,
        context: MarketContext,
    ) -> SentinelSignal | None:
        """Evaluate a sub-second / intra-bar orderflow volume burst for high-speed alert generation."""
        inst = str(burst.get("instrument_id", "")).upper()
        if "SENSEX" in inst:
            min_buf = 50.0
            strike_step = 100
        elif "BANKNIFTY" in inst:
            min_buf = 35.0
            strike_step = 100
        else:
            min_buf = 15.0
            strike_step = 50

        delta_ratio = float(burst.get("delta_ratio_15s", 0.0))
        depth_imb = float(burst.get("depth_imbalance", 0.0))
        cur_px = float(burst.get("latest_price", 0.0))
        now_dt = burst.get("timestamp") or datetime.now(timezone.utc)

        # Check alignment against context
        near_call_wall = context.call_wall > 0 and abs(cur_px - context.call_wall) <= 30.0
        near_put_wall = context.put_wall > 0 and abs(cur_px - context.put_wall) <= 30.0

        sig_type: str | None = None
        category = "NEUTRAL"
        bias = "NEUTRAL"
        base_score = 65

        if delta_ratio >= 0.40:
            if near_call_wall and depth_imb <= -0.15:
                sig_type = "ABSORPTION_BEAR"
                category = "BEARISH"
                bias = "SHORT"
                base_score = 75
                invalidation = cur_px + min_buf
                insight = f"⚡ Instant Wall Absorption: Massive aggressive buying into Call Wall ({context.call_wall:,.0f}) absorbed by passive limit asks."
            else:
                sig_type = "CONTINUATION_BULL"
                category = "BULLISH"
                bias = "LONG"
                base_score = 68
                invalidation = cur_px - min_buf
                insight = f"⚡ Instant Volume Surge: Rapid buyer sweep (+{burst['volume_15s']} units in 15s, Δ {burst['delta_15s']:+,.0f})."

        elif delta_ratio <= -0.40:
            if near_put_wall and depth_imb >= 0.15:
                sig_type = "ABSORPTION_BULL"
                category = "BULLISH"
                bias = "LONG"
                base_score = 75
                invalidation = cur_px - min_buf
                insight = f"⚡ Instant Wall Defense: Aggressive selling into Put Wall ({context.put_wall:,.0f}) absorbed by passive limit bids."
            else:
                sig_type = "CONTINUATION_BEAR"
                category = "BEARISH"
                bias = "SHORT"
                base_score = 68
                invalidation = cur_px + min_buf
                insight = f"⚡ Instant Volume Surge: Rapid seller sweep ({burst['volume_15s']} units in 15s, Δ {burst['delta_15s']:+,.0f})."

        if not sig_type:
            return None

        # Check cooldown
        last_seen = self.last_signal_bar_idx.get(sig_type, -100)
        if (self.bar_counter - last_seen) < 2:
            return None
        self.last_signal_bar_idx[sig_type] = self.bar_counter

        atm_strike = round(cur_px / strike_step) * strike_step
        play = f"Bull Put Spread {int(atm_strike)}/{int(atm_strike - strike_step)} PE" if bias == "LONG" else f"Bear Call Spread {int(atm_strike)}/{int(atm_strike + strike_step)} CE"

        ts_str = now_dt.strftime("%Y%m%d_%H%M%S")
        event_id = f"sig_{ts_str}_{inst}_micro_{sig_type}"

        return SentinelSignal(
            event_id=event_id,
            instrument_id=inst,
            timeframe="15s",
            bar_start_ts=now_dt,
            type=sig_type,
            category=category,
            bias=bias,
            score=base_score,
            confidence="HIGH" if base_score >= 70 else "MEDIUM",
            rvol_tod=2.0,
            delta=burst["delta_15s"],
            delta_ratio=delta_ratio,
            oi_change=0,
            context_tags=["MICRO_BURST", "INSTANT_TAPE"],
            insight_text=insight,
            invalidation_level=round(invalidation, 2),
            entry_price=round(cur_px, 2),
            target_1_pts=round(min_buf * 1.5, 1),
            target_2_pts=round(min_buf * 2.5, 1),
            risk_reward_ratio=1.5,
            actionable_play=play,
            trigger_speed="MICRO_BURST",
            rule_set_version="v2.1",
            status="NEW",
            gex_alignment="CONFIRMED_AT_WALL" if (near_call_wall or near_put_wall) else "NEUTRAL",
        )
