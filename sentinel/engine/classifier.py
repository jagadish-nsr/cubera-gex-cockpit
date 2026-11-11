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
from datetime import datetime, timezone, timedelta
from typing import Any

from sentinel.models import Bar, SentinelSignal
from sentinel.engine.context import MarketContext
from sentinel.engine.rvol import compute_tod_rvol

log = logging.getLogger(__name__)

COOLDOWN_BARS = 3
_IST = timezone(timedelta(hours=5, minutes=30))


def _suppress_continuation_near_magnet(
    bar: Bar,
    tags: list[str],
    context: MarketContext,
) -> bool:
    """True when 0-DTE afternoon continuation into the magnet should not mint.

    Leaves room for CVD / absorption. Outside expiry afternoon the gate is off;
    NEAR_MAGNET still marks COUNTER_TREND when a continuation is allowed.
    """
    if "NEAR_MAGNET" not in tags:
        return False
    ts = bar.start_ts
    ist = ts.astimezone(_IST) if ts.tzinfo is not None else ts.replace(tzinfo=_IST)
    mins = ist.hour * 60 + ist.minute
    try:
        import config
        fno_close = int(getattr(config, "FNO_CLOSE_MIN", 15 * 60 + 40))
    except Exception:
        fno_close = 15 * 60 + 40
    if not (13 * 60 <= mins < fno_close):
        return False
    try:
        import config
        from data.session_flags import is_expiry_day

        sym = context.symbol or config.index_key(bar.instrument_id)
        return bool(is_expiry_day(sym, ist.strftime("%Y-%m-%d")))
    except Exception:
        return False


# Elevated chop (below CHOP_HIGH) still kills continuation when price is boxed
# inside the opening range — 9-Nov V-reversal fired strong ABOVE_VWAP closes
# that were still trapped in the ORB (CONTINUATION_BULL 7.7% WR).
_CHOP_ELEVATED = 55.0


def _suppress_continuation_choppy(
    context: MarketContext,
    tags: list[str] | None = None,
) -> bool:
    """True when the path-shape meter says the tape is boxed.

    Dreiss ≥ 61.8 (chop_high) always blocks. Elevated chop (≥ 55) blocks only
    when still INSIDE_ORB — open-space breakouts keep their continuation.
    """
    if bool(getattr(context, "chop_high", False)):
        return True
    tags = tags or []
    if "INSIDE_ORB" not in tags:
        return False
    chop = getattr(context, "chop_value", None)
    try:
        return chop is not None and float(chop) >= _CHOP_ELEVATED
    except (TypeError, ValueError):
        return False


def _continuation_score_penalty(tags: list[str], gex_alignment: str, score: int) -> int:
    """Down-rank boxed / counter-trend continuation so it stays below the rec floor.

    9 Nov: CONTINUATION_BULL avg score 93 with 8% WR — RVol/Δ bonuses loved the
    strong close into the pin. COUNTER_TREND or INSIDE_ORB → penalize and cap 69
    (MIN_SIGNAL_SCORE is 70). ORB breakout continuations keep their full stack.
    """
    boxed = "INSIDE_ORB" in tags
    counter = gex_alignment == "COUNTER_TREND"
    if counter:
        score -= 20
    if boxed:
        score -= 15
    if counter or boxed:
        score = min(score, 69)
    return max(0, min(100, score))


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
        # 1. ABSORPTION — only at a named gamma strike.
        #     14d prod tape: TRAPPED_BUYERS without a wall was 17% hit / −52 pts
        #     at +5m. VWAP-2σ / day-extreme alone is not a fade.
        # -------------------------------------------------------------------
        if (
            rvol >= 1.4
            and bar.delta_ratio > 0.18
            and (close_pos <= 0.45 or upper_wick >= 0.40)
            and "NEAR_CALL_WALL" in tags
        ):
            sig_type = "ABSORPTION_BEAR"
            category = "BEARISH"
            bias = "SHORT"
            base_score = 65
            invalidation = bar.high + vol_buffer
            insight = (
                f"Aggressive buying was absorbed (Δ {bar.delta:+,.0f}, {rvol:.1f}x vol, "
                f"depth imbalance {bar.depth_imbalance:+.2f}) "
                f"at the call-gamma strike ({context.call_wall:,.0f}). "
                "The wick is tape. The strike is not a ceiling."
            )
            gex_alignment = "CONFIRMED_AT_WALL"

        elif (
            rvol >= 1.4
            and bar.delta_ratio < -0.18
            and (close_pos >= 0.55 or lower_wick >= 0.40)
            and "NEAR_PUT_WALL" in tags
        ):
            sig_type = "ABSORPTION_BULL"
            category = "BULLISH"
            bias = "LONG"
            base_score = 65
            invalidation = bar.low - vol_buffer
            insight = (
                f"Aggressive selling was absorbed (Δ {bar.delta:+,.0f}, {rvol:.1f}x vol, "
                f"depth imbalance {bar.depth_imbalance:+.2f}) "
                f"at the put-gamma strike ({context.put_wall:,.0f}). "
                "The wick is tape. The strike is not a floor."
            )
            gex_alignment = "CONFIRMED_AT_WALL"

        # -------------------------------------------------------------------
        # 2. CONTINUATION (High-volume impulse in direction of delta)
        #     Requires a live OI tape. A flat futures OI reprint is not buildup.
        #     On 0-DTE afternoon near the magnet, skip so CVD/absorption can fire.
        # -------------------------------------------------------------------
        elif (
            getattr(bar, "oi_fresh", True)
            and rvol >= 1.5
            and bar.delta_ratio >= 0.25
            and close_pos >= 0.70
            and ("ABOVE_VWAP" in tags or "ABOVE_ORB_HIGH" in tags)
            and not _suppress_continuation_near_magnet(bar, tags, context)
            and not _suppress_continuation_choppy(context, tags)
        ):
            sig_type = "CONTINUATION_BULL"
            category = "BULLISH"
            bias = "LONG"
            base_score = 62
            invalidation = bar.low - vol_buffer
            insight = (
                f"Strong buyer continuation: {rvol:.1f}x norm volume, delta ratio {bar.delta_ratio:+.2f}, "
                f"firm close near high above VWAP."
            )
            gex_alignment = (
                "COUNTER_TREND"
                if ("NEAR_CALL_WALL" in tags or "NEAR_MAGNET" in tags)
                else "NEUTRAL"
            )

        elif (
            getattr(bar, "oi_fresh", True)
            and rvol >= 1.5
            and bar.delta_ratio <= -0.25
            and close_pos <= 0.30
            and ("BELOW_VWAP" in tags or "BELOW_ORB_LOW" in tags)
            and not _suppress_continuation_near_magnet(bar, tags, context)
            and not _suppress_continuation_choppy(context, tags)
        ):
            sig_type = "CONTINUATION_BEAR"
            category = "BEARISH"
            bias = "SHORT"
            base_score = 62
            invalidation = bar.high + vol_buffer
            insight = (
                f"Strong seller continuation: {rvol:.1f}x norm volume, delta ratio {bar.delta_ratio:+.2f}, "
                f"firm close near low below VWAP."
            )
            gex_alignment = (
                "COUNTER_TREND"
                if ("NEAR_PUT_WALL" in tags or "NEAR_MAGNET" in tags)
                else "NEUTRAL"
            )

        # -------------------------------------------------------------------
        # 3. EXHAUSTION (Volume climax with immediate rejection)
        #     EXHAUSTION_UP without a call wall was 9% hit / −133 pts at +5m on
        #     the 14d prod tape (mostly trend-day Sensex). Keep the fade only
        #     when the print is at the call-gamma strike. EXHAUSTION_DOWN held
        #     +153 pts at +5m and stays open.
        # -------------------------------------------------------------------
        elif rvol >= 2.2:
            if close_pos <= 0.35 and upper_wick >= 0.40 and "NEAR_CALL_WALL" in tags:
                sig_type = "EXHAUSTION_UP"
                category = "BEARISH"
                bias = "SHORT"
                base_score = 64
                invalidation = bar.high + vol_buffer
                gex_alignment = "CONFIRMED_AT_WALL"
                insight = (
                    f"Buying climax: {rvol:.1f}x volume with a long upper tail "
                    f"at the call-gamma strike ({context.call_wall:,.0f}). "
                    "That is tape, not a ceiling."
                )
            elif close_pos >= 0.65 and lower_wick >= 0.40:
                sig_type = "EXHAUSTION_DOWN"
                category = "BULLISH"
                bias = "LONG"
                base_score = 64
                invalidation = bar.low - vol_buffer
                insight = f"Selling climax: {rvol:.1f}x volume with a long lower tail. That is tape, not a floor."

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

        if sig_type.startswith("CONTINUATION"):
            score = _continuation_score_penalty(tags, gex_alignment, score)

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

        # Micro sweeps are not bar continuations. On 2026-11-05 Nifty journaled
        # 133 CONTINUATION_BULL at score 68 from this path. Only keep a micro
        # print when it sits on a named gamma strike, and score it into the
        # tape band (≥ 70) so the floor-68 flood cannot return.
        if delta_ratio >= 0.40 and near_call_wall:
            sig_type = "CONTINUATION_BULL"
            category = "BULLISH"
            bias = "LONG"
            base_score = 75
            invalidation = cur_px - min_buf
            insight = (
                f"⚡ Buyer sweep (+{burst['volume_15s']} units in 15s, Δ {burst['delta_15s']:+,.0f}). "
                f"Print is at the call-gamma strike ({context.call_wall:,.0f}). "
                "That is a location, not a ceiling."
            )

        elif delta_ratio <= -0.40 and near_put_wall:
            sig_type = "CONTINUATION_BEAR"
            category = "BEARISH"
            bias = "SHORT"
            base_score = 75
            invalidation = cur_px + min_buf
            insight = (
                f"⚡ Seller sweep ({burst['volume_15s']} units in 15s, Δ {burst['delta_15s']:+,.0f}). "
                f"Print is at the put-gamma strike ({context.put_wall:,.0f}). "
                "That is a location, not a floor."
            )

        if not sig_type:
            return None

        # Check cooldown (stricter for micro: 5 completed bars)
        last_seen = self.last_signal_bar_idx.get(sig_type, -100)
        if (self.bar_counter - last_seen) < 5:
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
            gex_alignment="CONFIRMED_AT_WALL",
        )
