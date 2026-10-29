"""analytics/trade_ideas.py — Institutional Options Recommendation Engine (Score >= 75).

Synthesizes:
1. Real-time Spot & GEX Structure (Spot, Call Wall, Put Wall, Gamma Flip, Max Pain)
2. OrderFlow Sentinel Microstructure (Session VWAP, Sigma Envelopes, Aggressive vs Passive Absorption)
3. Institutional Buildup & Strike Velocity (OI changes, Short Covering, Long Buildup)
4. Macro Context & Directional Bias (-10.0 to +10.0 conviction score)

Evaluates 7 institutional options strategies for Calls (CE) and Puts (PE):
- Strategy 1: ATM Momentum Play (Delta ~0.50, closest to Spot)
- Strategy 2: Structure at a gamma strike (a concentration, not a floor or a ceiling)
- Strategy 3: Strike break (convex break at a gamma strike, delta about 0.28)
- Strategy 4: OrderFlow Iceberg Absorption (tape absorption; the name keeps FADE as an orderflow label)
- Strategy 5: Expiry-afternoon vanna and charm (high gamma because time is short; not a pin harvest)
- Strategy 6: Volatility Dispersion / Long Gamma Explosion (Compressed IV range-breakout convexity)
- Strategy 7: Zero-Cost Ratio Risk Reversal (Synthetic 1x2 ratio asymmetry targeting walls)

Filters recommendations to only surface High-Conviction trade ideas (Score >= 75).
Preserves pure analytical and recommendation logic, unit-testable offline without Streamlit.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import threading
import time
from typing import Any, Sequence

import config

# Deduplication cache for WebSocket option strike subscriptions: (symbol, strike)
_SUBSCRIBED_REC_STRIKES: set[tuple[str, float]] = set()

# Background persistence executor to avoid blocking Streamlit rendering loop with SQLite writes
_BG_PERSIST_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="rec_persist")
_PENDING_PERSIST_FUTURES: list[concurrent.futures.Future] = []
_MEM_SCORE_HISTORY: dict[str, list[dict]] = {}


def flush_telemetry(rec_id: str | None = None, timeout: float = 1.0) -> None:
    """Flush pending asynchronous SQLite persistence writes for recommendations and telemetry."""
    global _PENDING_PERSIST_FUTURES
    pending = [f for f in _PENDING_PERSIST_FUTURES if not f.done()]
    if pending:
        try:
            concurrent.futures.wait(pending, timeout=timeout)
        except Exception:
            pass
    _PENDING_PERSIST_FUTURES = [f for f in _PENDING_PERSIST_FUTURES if not f.done()]



@dataclass
class PositionSizeInfo:
    suggested_lots: int
    total_quantity: int
    capital_outlay_inr: float
    portfolio_pct: float
    max_risk_inr: float
    risk_pct_portfolio: float
    target_1_pnl_inr: float
    target_2_pnl_inr: float
    is_outlay_capped: bool = False
    sizing_rationale: str = ""


@dataclass
class TradeIdea:
    symbol: str
    side: str                          # "CE" | "PE"
    play_type: str                     # e.g. "ATM Momentum Play", "OrderFlow Iceberg Absorption Fade"
    play_badge: str                    # "MOMENTUM" | "STRUCTURE" | "STRIKE BREAK" | "ICEBERG FADE" | "VANNA-CHARM" | "VOL DISPERSION" | "RATIO REVERSAL"
    strike: float
    option_symbol: str                 # e.g. "NIFTY 24500 CE"
    current_ltp: float                 # Estimated or live Option Premium
    delta_approx: float                # Delta estimate (e.g. 0.50 or -0.50)
    entry_trigger: str                 # Microstructure trigger
    spot_invalidation: float           # Spot price level that invalidates the trade
    spot_invalidation_text: str        # Plain-English spot invalidation condition
    target_1: float                    # Option price target 1
    target_1_spot: float               # Spot level corresponding to Target 1
    target_2: float                    # Option price target 2
    target_2_spot: float               # Spot level corresponding to Target 2
    stop_loss: float                   # Option premium stop loss
    risk_reward_ratio: str             # e.g. "1 : 2.5", "1 : 4.5"
    quality_score: int                 # 0 to 100
    conviction_label: str              # "HIGH CONVICTION (Grade A+)", etc.
    conviction_color: str              # CSS color token
    rationale: str                     # Detailed institutional setup narrative
    catalyst_tags: list[str] = field(default_factory=list)
    position_size: PositionSizeInfo | None = None
    is_ws_live: bool = False           # True when raw exchange trade ticks arrive via WebSocket
    rec_id: str = ""                   # Unique persistent ID e.g. "NIFTY_CE_22600_MOMENTUM"
    triggered_at: str = ""             # Formatted IST datetime string
    conviction_tier: str = "HIGH"      # "HIGH" (Score > 85), "MEDIUM" (75-85), "LOW" (<75)
    is_bookmarked: bool = False        # User pinned state
    score_delta: int = 0               # Real-time score delta
    score_reason: str = ""             # Explanation for why score is increasing or fading
    score_history: list[dict] = field(default_factory=list)  # Historical data points
    p_touch_target_1: float = 0.0      # Reflection principle probability of touching Target 1
    p_touch_sl: float = 0.0            # Reflection principle probability of touching Stop Loss
    net_ev_pts: float = 0.0            # Probability-weighted Net EV in option points (minus costs)
    greek_attribution: dict = field(default_factory=dict) # Greek decomposition {delta, gamma, vega, theta}
    spread_alternative: dict | None = None   # Defined-risk vertical spread alternative
    latch_state: str = "QUALIFIED"           # "QUALIFIED" | "ACTIVE" | "TARGET_HIT" | "INVALIDATED" | "DEGRADED"
    adx_5m: float = 20.0                     # 5m ADX reading
    adx_regime: str = "NEUTRAL"              # "TRENDING" | "CHOP" | "NEUTRAL"
    cvd_divergence: str = "NONE"             # "BULLISH" | "BEARISH" | "NONE"
    initial_ltp: float = 0.0                 # Entry option premium reference anchor
    initial_spot: float = 0.0                # Spot price at recommendation entry
    live_pnl_pts: float = 0.0                # Live unrealized PnL in points
    live_pnl_pct: float = 0.0                # Live unrealized PnL in percentage
    target_progress_pct: float = 0.0         # 0-100% progress towards Target 1
    inval_buffer: float = 0.0                # Live buffer points away from Spot Invalidation
    inval_breached: bool = False             # True if Spot Invalidation is breached
    first_active_ts: float = 0.0             # Epoch timestamp when setup first entered ACTIVE state
    last_active_ts: float = 0.0              # Epoch timestamp when setup was last in ACTIVE state
    time_since_active_sec: float = 0.0       # Elapsed seconds since last active (or active duration)
    time_since_active_label: str = ""        # Human-readable time since active counter e.g. "🟢 Active: 2m 15s"


@dataclass
class RecommendationBatch:
    symbol: str
    spot: float
    session_vwap: float
    call_wall: float
    put_wall: float
    gamma_flip: float
    directional_bias_score: float
    bias_regime: str
    ce_recommendations: list[TradeIdea] = field(default_factory=list)
    pe_recommendations: list[TradeIdea] = field(default_factory=list)
    top_recommended_side: str = "NEUTRAL"   # "CE" | "PE" | "BOTH" | "NEUTRAL"
    executive_summary: str = ""
    all_ce_candidates: list[TradeIdea] = field(default_factory=list)
    all_pe_candidates: list[TradeIdea] = field(default_factory=list)
    min_score_filter: int = 75
    higher_ce_recommendations: list[TradeIdea] = field(default_factory=list)
    higher_pe_recommendations: list[TradeIdea] = field(default_factory=list)
    lower_ce_recommendations: list[TradeIdea] = field(default_factory=list)
    lower_pe_recommendations: list[TradeIdea] = field(default_factory=list)
    tactical_ce_recommendations: list[TradeIdea] = field(default_factory=list)
    tactical_pe_recommendations: list[TradeIdea] = field(default_factory=list)
    ce_fallback_active: bool = False
    pe_fallback_active: bool = False
    ce_tactical_active: bool = False
    pe_tactical_active: bool = False
    bookmarked_recommendations: list[TradeIdea] = field(default_factory=list)

    @property
    def all_recommendations(self) -> list[TradeIdea]:
        """Combined list of active CE and PE recommendations."""
        return self.ce_recommendations + self.pe_recommendations


def _round_strike(price: float, step: int) -> float:
    """Rounds price to nearest strike step."""
    if step <= 0:
        step = 50
    return float(round(price / step) * step)


def _approx_bs_price_and_delta(
    spot: float,
    strike: float,
    is_call: bool,
    dte_days: float = 3.0,
    iv: float = 0.135,
    r: float = 0.065,
) -> tuple[float, float]:
    """Fast Black-Scholes approximation for delta and option premium if live chain quotes are missing."""
    T = max(dte_days / 365.0, 0.001)
    sigma = max(iv, 0.05)
    sqrt_T = math.sqrt(T)

    try:
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T

        # Standard normal CDF approximation (Abramowitz & Stegun)
        def norm_cdf(x: float) -> float:
            return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

        nd1 = norm_cdf(d1)
        nd2 = norm_cdf(d2)

        if is_call:
            price = spot * nd1 - strike * math.exp(-r * T) * nd2
            delta = nd1
        else:
            price = strike * math.exp(-r * T) * (1.0 - nd2) - spot * (1.0 - nd1)
            delta = nd1 - 1.0

        # Guard against degenerate values
        price = max(price, 1.0)
        return round(price, 1), round(delta, 2)
    except Exception:
        intrinsic = max(0.0, spot - strike if is_call else strike - spot)
        return max(intrinsic + 15.0, 25.0), 0.50 if is_call else -0.50


def _reprice_trade_idea_targets(
    side: str,
    strike: float,
    current_ltp: float,
    spot: float,
    inval_spot: float,
    tgt1_spot: float,
    tgt2_spot: float,
    dte_days: float = 3.0,
    atm_iv: float = 0.14,
    horizon_minutes: int = 45,
    step: int = 50,
) -> tuple[float, float, float, float, float, float, str, dict]:
    """Calculate Target 1, Target 2, Stop Loss, touch probabilities, and EV using Black-76 and Target Engine.

    Returns:
        (sl_opt, tgt1_opt, tgt2_opt, p_touch_tgt1, p_touch_sl, net_ev_pts, rr_str, greek_attr_dict)
    """
    is_call = side.upper() == "CE"
    opt_type = "CE" if is_call else "PE"
    safe_ltp = max(float(current_ltp or 0.0), 1.0)
    safe_spot = max(float(spot or 0.0), 100.0)
    safe_k = max(float(strike or 0.0), 100.0)

    # 1. Base time to expiry in annual fraction
    safe_dte = float(dte_days if dte_days is not None else 3.0)
    T_entry = max(safe_dte / 365.0, 1.0 / (365.0 * 6.25 * 60.0))

    # 2. Estimate or solve entry IV
    iv_now = None
    if safe_ltp > 0.0:
        try:
            from target_engine.pricer import implied_volatility
            iv_now = implied_volatility(safe_spot, safe_k, T_entry, safe_ltp, r=0.065, option_type=opt_type)
        except Exception:
            iv_now = None

    if iv_now is None or iv_now <= 0.02 or math.isnan(iv_now):
        iv_now = float(atm_iv if (atm_iv is not None and atm_iv > 0.02) else 0.14)

    # 3. Horizon time decrements
    dt_annual = horizon_minutes / (365.0 * 6.25 * 60.0)
    T_tgt1 = max(1e-4, T_entry - dt_annual)
    T_tgt2 = max(1e-4, T_entry - (dt_annual * 1.5))
    T_sl = max(1e-4, T_entry - (dt_annual * 0.5))

    # 4. Spot Target Sanity Clamping: Prevent extreme spot projections from data mismatch
    max_spot_reach = max(step * 5.0, 300.0)  # max ~300 pts on NIFTY, 600 pts on BANKNIFTY
    if is_call:
        tgt1_spot = min(tgt1_spot, safe_spot + max_spot_reach)
        tgt2_spot = min(tgt2_spot, safe_spot + (max_spot_reach * 1.5))
        inval_spot = max(inval_spot, safe_spot - max_spot_reach)
    else:
        tgt1_spot = max(tgt1_spot, safe_spot - max_spot_reach)
        tgt2_spot = max(tgt2_spot, safe_spot - (max_spot_reach * 1.5))
        inval_spot = min(inval_spot, safe_spot + max_spot_reach)

    # 5. Forecasted IVs for scenarios
    try:
        from target_engine.iv_forecast import forecast_iv
        scen_fwd = "bull" if is_call else "bear"
        scen_adv = "bear" if is_call else "bull"

        move1_pct = (tgt1_spot - safe_spot) / safe_spot if safe_spot > 0 else 0.0
        move2_pct = (tgt2_spot - safe_spot) / safe_spot if safe_spot > 0 else 0.0
        move_sl_pct = (inval_spot - safe_spot) / safe_spot if safe_spot > 0 else 0.0

        iv_f_tgt1 = forecast_iv(scen_fwd, horizon_minutes, iv_now, spot_move_pct=move1_pct).sigma_target
        iv_f_tgt2 = forecast_iv(scen_fwd, horizon_minutes * 2, iv_now, spot_move_pct=move2_pct).sigma_target
        iv_f_sl = forecast_iv(scen_adv, int(horizon_minutes * 0.5), iv_now, spot_move_pct=move_sl_pct).sigma_target
    except Exception:
        iv_f_tgt1 = iv_now
        iv_f_tgt2 = iv_now
        iv_f_sl = iv_now

    # 6. Black-76 Repricing
    try:
        from target_engine.pricer import reprice
        tgt1_calc = reprice(tgt1_spot, safe_k, T_tgt1, iv_f_tgt1, r=0.065, option_type=opt_type)
        tgt2_calc = reprice(tgt2_spot, safe_k, T_tgt2, iv_f_tgt2, r=0.065, option_type=opt_type)
        sl_calc = reprice(inval_spot, safe_k, T_sl, iv_f_sl, r=0.065, option_type=opt_type)
    except Exception:
        tgt1_calc = safe_ltp * 1.35
        tgt2_calc = safe_ltp * 1.70
        sl_calc = safe_ltp * 0.72

    # Enforce strict monotonicity and minimum buffers against actual entry LTP:
    actual_ltp = max(0.05, float(current_ltp))
    tgt1_opt = max(round(tgt1_calc, 1), round(safe_ltp * 1.15, 1), round(actual_ltp * 1.15, 2))
    tgt2_opt = max(round(tgt2_calc, 1), round(tgt1_opt * 1.15, 1), round(safe_ltp * 1.35, 1), round(actual_ltp * 1.35, 2))
    
    # Cap target prices to realistic intraday multiples (max 4.0x entry for T1, 6.5x for T2)
    max_t1_mult = round(actual_ltp * 4.0, 1)
    max_t2_mult = round(actual_ltp * 6.5, 1)
    tgt1_opt = min(tgt1_opt, max_t1_mult)
    tgt2_opt = min(tgt2_opt, max_t2_mult)

    sl_opt = min(round(sl_calc, 1), round(safe_ltp * 0.85, 1), round(actual_ltp * 0.85, 2))
    if sl_opt >= actual_ltp:
        sl_opt = round(actual_ltp * 0.72, 2)
    sl_opt = max(0.05, min(sl_opt, round(actual_ltp - 0.05, 2) if actual_ltp > 0.15 else round(actual_ltp * 0.70, 2)))
    if tgt1_opt <= actual_ltp:
        tgt1_opt = round(actual_ltp * 1.25, 2)
    if tgt2_opt <= tgt1_opt:
        tgt2_opt = round(tgt1_opt * 1.25, 2)

    # 6. Touch Probabilities via Reflection Principle
    try:
        from target_engine.levels import compute_touch_probability
        sigma_h = max(1.0, safe_spot * iv_now * math.sqrt(horizon_minutes / (365.0 * 6.25 * 60.0)))
        p_touch_tgt1 = compute_touch_probability(distance=abs(tgt1_spot - safe_spot), sigma_h=sigma_h)
        p_touch_sl = compute_touch_probability(distance=abs(inval_spot - safe_spot), sigma_h=sigma_h)
    except Exception:
        p_touch_tgt1 = 0.52
        p_touch_sl = 0.38

    # 7. Probability-weighted Net EV and Risk:Reward
    gain_t1 = max(0.1, tgt1_opt - safe_ltp)
    loss_sl = max(0.1, safe_ltp - sl_opt)
    cost_pts = 0.8
    net_ev = round((p_touch_tgt1 * gain_t1) - (p_touch_sl * loss_sl) - cost_pts, 1)

    rr_ratio = round(gain_t1 / loss_sl, 1)
    rr_str = f"1 : {rr_ratio}"

    # 8. Greek Attribution Decomposition
    attr_dict = {}
    try:
        from target_engine.pricer import greek_attribution
        ga = greek_attribution(
            F_entry=safe_spot, F_target=tgt1_spot,
            sigma_entry=iv_now, sigma_target=iv_f_tgt1,
            T_entry=T_entry, T_target=T_tgt1,
            K=safe_k, r=0.065, option_type=opt_type
        )
        attr_dict = {
            "delta_pnl": round(ga.delta_pnl, 2),
            "gamma_pnl": round(ga.gamma_pnl, 2),
            "vega_pnl": round(ga.vega_pnl, 2),
            "theta_pnl": round(ga.theta_pnl, 2),
            "total_pnl": round(ga.total_pnl, 2),
        }
    except Exception:
        attr_dict = {"delta_pnl": round(gain_t1 * 0.7, 2), "theta_pnl": -round(gain_t1 * 0.1, 2)}

    return (sl_opt, tgt1_opt, tgt2_opt, p_touch_tgt1, p_touch_sl, net_ev, rr_str, attr_dict)


def _lookup_chain_data(
    strike: float,
    is_call: bool,
    oi_strikes: list[dict] | None,
    contracts_df: Any | None,
    spot: float,
    step: int,
    oi_map: dict[float, dict] | None = None,
    contracts_map: dict[tuple[str, float], tuple[float, float, float, float]] | None = None,
    chain_spot: float = 0.0,
    symbol: str = "NIFTY",
) -> tuple[float, float, float, float, bool]:
    """Look up LTP, Delta, OI, Delta OI, and is_ws_live for a given strike and option leg.

    Checks raw exchange WebSocket tick feed first (<1ms push latency). If not available,
    falls back to O(1) indexed chain snapshot with real-time delta MTM adjustment.
    """
    ltp = 0.0
    delta = 0.0
    oi = 0.0
    doi = 0.0
    tol = step * 0.4
    is_ws_live = False

    # 0. Primary path: Check live WebSocket option tick feed (<1ms exchange push)
    try:
        from app.spot_ticker import get_option_ws_quote
        type_flag = "CE" if is_call else "PE"
        ws_q = get_option_ws_quote(symbol, strike, type_flag, max_age_s=120.0)
        if ws_q and ws_q.get("ltp") and ws_q["ltp"] > 0:
            ltp = float(ws_q["ltp"])
            is_ws_live = True
            if ws_q.get("oi"):
                oi = float(ws_q["oi"])
    except Exception:
        pass

    # 1. Search in oi_map / oi_strikes (fastest and cleanest) if LTP not from WebSocket
    if not is_ws_live:
        if oi_map is not None:
            s = oi_map.get(strike)
            if s is None:
                for sk, s_val in oi_map.items():
                    if abs(sk - strike) < tol:
                        s = s_val
                        break
            if s:
                if is_call:
                    ltp = float(s.get("call_ltp", 0.0) or 0.0)
                    oi = float(s.get("call_oi", 0.0) or 0.0)
                    doi = float(s.get("call_doi", 0.0) or 0.0)
                else:
                    ltp = float(s.get("put_ltp", 0.0) or 0.0)
                    oi = float(s.get("put_oi", 0.0) or 0.0)
                    doi = float(s.get("put_doi", 0.0) or 0.0)
        elif oi_strikes:
            for s in oi_strikes:
                if abs(float(s.get("strike", 0.0)) - strike) < tol:
                    if is_call:
                        ltp = float(s.get("call_ltp", 0.0) or 0.0)
                        oi = float(s.get("call_oi", 0.0) or 0.0)
                        doi = float(s.get("call_doi", 0.0) or 0.0)
                    else:
                        ltp = float(s.get("put_ltp", 0.0) or 0.0)
                        oi = float(s.get("put_oi", 0.0) or 0.0)
                        doi = float(s.get("put_doi", 0.0) or 0.0)
                    break

    # 2. Check contracts_map / contracts_df if LTP or delta not found
    if ltp <= 0.0 or delta == 0.0:
        c_flag = "C" if is_call else "P"
        if contracts_map is not None:
            c_entry = contracts_map.get((c_flag, strike))
            if c_entry is None:
                for (t_f, sk), val in contracts_map.items():
                    if t_f == c_flag and abs(sk - strike) < tol:
                        c_entry = val
                        break
            if c_entry:
                c_ltp, c_delta, c_oi, c_doi = c_entry
                if ltp <= 0.0 and c_ltp > 0.0:
                    ltp = c_ltp
                if delta == 0.0 and c_delta != 0.0:
                    delta = round(c_delta, 2)
                if oi <= 0.0 and c_oi > 0.0:
                    oi = c_oi
                if doi == 0.0 and c_doi != 0.0:
                    doi = c_doi
        elif contracts_df is not None:
            try:
                if hasattr(contracts_df, "itertuples"):
                    for row in contracts_df.itertuples(index=False):
                        r_type = "C" if str(getattr(row, "type", "")).upper().startswith("C") else "P"
                        r_strike = float(getattr(row, "strike", 0.0))
                        if r_type == c_flag and abs(r_strike - strike) < tol:
                            if ltp <= 0.0:
                                ltp = float(getattr(row, "ltp", 0.0) or getattr(row, "mid", 0.0) or 0.0)
                            if delta == 0.0 and hasattr(row, "delta"):
                                d_val = float(getattr(row, "delta", 0.0) or 0.0)
                                if not math.isnan(d_val):
                                    delta = round(d_val, 2)
                            break
            except Exception:
                pass

    # 3. Fallback to Black-Scholes estimate if LTP or Delta is zero
    if ltp <= 0.0 or delta == 0.0:
        bs_px, bs_delta = _approx_bs_price_and_delta(spot, strike, is_call)
        if ltp <= 0.0:
            ltp = bs_px
        if delta == 0.0:
            delta = bs_delta
    elif not is_ws_live and chain_spot > 0.0 and spot > 0.0 and abs(spot - chain_spot) > 0.05:
        # Mark-to-market real-time option LTP adjustment using delta and live spot move (0ms latency)
        d_spot = spot - chain_spot
        eff_delta = delta if delta != 0.0 else (0.50 if is_call else -0.50)
        ltp = round(ltp + (eff_delta * d_spot), 1)
        if is_call:
            intrinsic = max(0.0, spot - strike)
            ltp = max(ltp, round(intrinsic + 0.5, 1), 0.5)
        else:
            intrinsic = max(0.0, strike - spot)
            ltp = max(ltp, round(intrinsic + 0.5, 1), 0.5)

    return round(ltp, 1), round(delta, 2), oi, doi, is_ws_live


def _select_strike_by_delta_window(
    target_delta: float,
    is_call: bool,
    fallback_strike: float,
    contracts_map: dict[tuple[str, float], tuple[float, float, float, float]] | None,
    step: int,
) -> float:
    """Select the strike in the option chain whose delta is closest to target_delta within +/- 2 steps."""
    if not contracts_map:
        return float(fallback_strike)

    c_type = "C" if is_call else "P"
    min_k = fallback_strike - (2.5 * step)
    max_k = fallback_strike + (2.5 * step)

    best_strike = float(fallback_strike)
    min_diff = 999.0

    for (r_type, r_strike), (_, r_delta, _, _) in contracts_map.items():
        if r_type == c_type and min_k <= r_strike <= max_k:
            abs_delta = abs(float(r_delta))
            if 0.05 <= abs_delta <= 0.95:
                diff = abs(abs_delta - target_delta)
                if diff < min_diff:
                    min_diff = diff
                    best_strike = float(r_strike)

    return best_strike


def _get_bar_num(b: Any, key: str, default: float = 0.0) -> float:
    if isinstance(b, dict):
        v = b.get(key)
        return float(v) if v is not None else default
    v = getattr(b, key, None)
    return float(v) if v is not None else default


def compute_adx(bars: Sequence[Any] | None, period: int = 14) -> tuple[float, str]:
    """Compute Average Directional Index (ADX) over bars (Wilder's standard).
    Returns (adx_value, regime) where regime is 'CHOP' (ADX < 20), 'TRENDING' (ADX > 25), or 'NEUTRAL'.
    """
    if not bars or len(bars) < max(period + 2, 8):
        return 20.0, "NEUTRAL"

    tr_list = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(bars)):
        try:
            h = _get_bar_num(bars[i], "high")
            l = _get_bar_num(bars[i], "low")
            prev_h = _get_bar_num(bars[i - 1], "high")
            prev_l = _get_bar_num(bars[i - 1], "low")
            prev_c = _get_bar_num(bars[i - 1], "close")

            if h <= 0 or l <= 0 or prev_h <= 0 or prev_l <= 0 or prev_c <= 0:
                continue

            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            up_move = h - prev_h
            down_move = prev_l - l

            p_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
            m_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

            tr_list.append(tr)
            plus_dm.append(p_dm)
            minus_dm.append(m_dm)
        except Exception:
            continue

    eff_period = min(period, max(5, len(tr_list)))
    if len(tr_list) < eff_period:
        return 20.0, "NEUTRAL"

    smooth_tr = sum(tr_list[:eff_period])
    smooth_p_dm = sum(plus_dm[:eff_period])
    smooth_m_dm = sum(minus_dm[:eff_period])

    dx_list = []
    for i in range(eff_period, len(tr_list)):
        smooth_tr = smooth_tr - (smooth_tr / eff_period) + tr_list[i]
        smooth_p_dm = smooth_p_dm - (smooth_p_dm / eff_period) + plus_dm[i]
        smooth_m_dm = smooth_m_dm - (smooth_m_dm / eff_period) + minus_dm[i]

        if smooth_tr > 0:
            plus_di = 100.0 * (smooth_p_dm / smooth_tr)
            minus_di = 100.0 * (smooth_m_dm / smooth_tr)
            di_sum = plus_di + minus_di
            dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
            dx_list.append(dx)

    if not dx_list:
        if smooth_tr > 0:
            plus_di = 100.0 * (smooth_p_dm / smooth_tr)
            minus_di = 100.0 * (smooth_m_dm / smooth_tr)
            di_sum = plus_di + minus_di
            dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 20.0
            dx_list.append(dx)
        else:
            return 20.0, "NEUTRAL"

    adx = sum(dx_list[-min(len(dx_list), eff_period):]) / min(len(dx_list), eff_period)
    adx = round(adx, 1)

    if adx < 20.0:
        regime = "CHOP"
    elif adx > 25.0:
        regime = "TRENDING"
    else:
        regime = "NEUTRAL"

    return adx, regime


def compute_cvd_divergence(bars: Sequence[Any] | None, atr: float = 35.0) -> str:
    """Detects price vs CVD slope divergence over recent 10-15 bars.
    Returns: 'BEARISH' (Price up / CVD down), 'BULLISH' (Price down / CVD up), or 'NONE'.
    """
    if not bars or len(bars) < 4:
        return "NONE"

    recent = bars[-min(len(bars), 14):]
    closes = []
    cvds = []
    cum_delta = 0.0
    for b in recent:
        try:
            c = _get_bar_num(b, "close")
            if isinstance(b, dict) and "cvd" in b:
                cvd = float(b["cvd"])
            elif not isinstance(b, dict) and getattr(b, "cvd", None) is not None:
                cvd = float(getattr(b, "cvd"))
            else:
                d = _get_bar_num(b, "delta", 0.0)
                cum_delta += d
                cvd = cum_delta

            if c > 0:
                closes.append(c)
                cvds.append(cvd)
        except Exception:
            continue

    if len(closes) < 4 or len(cvds) < 4:
        return "NONE"

    price_delta = closes[-1] - closes[0]
    cvd_delta = cvds[-1] - cvds[0]

    min_p_move = max(5.0, atr * 0.20)
    # Bearish divergence: price up but CVD down (institutional absorption)
    if price_delta > min_p_move and cvd_delta < 0:
        return "BEARISH"
    # Bullish divergence: price down but CVD up (institutional accumulation)
    elif price_delta < -min_p_move and cvd_delta > 0:
        return "BULLISH"

    return "NONE"


def is_lunch_hour_chop(latest_bar: Any | None = None) -> bool:
    """Checks if current time is within 11:30 - 13:30 IST and volume is dry."""
    from datetime import datetime, timezone, timedelta
    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    mins = now_ist.hour * 60 + now_ist.minute
    # 11:30 IST is 690 mins from midnight, 13:30 IST is 810 mins
    if 690 <= mins <= 810:
        rvol = float(getattr(latest_bar, "rvol_tod", 0.5) or 0.5) if latest_bar else 0.5
        return rvol < 0.70
    return False


def compute_trend_context(
    bars: Sequence[Any] | None,
    default_step: float = 50.0,
    latest_bar: Any | None = None,
) -> dict[str, Any]:
    """Extract EMA-9, EMA-21 trend alignment, ATR, ADX, and CVD divergence from Sentinel bars."""
    fallback_atr = max(30.0, default_step * 0.7)
    if not bars or len(bars) < 2:
        return {
            "ema9": None,
            "ema21": None,
            "ema_trend": "NEUTRAL",
            "atr": fallback_atr,
            "adx": 20.0,
            "adx_regime": "NEUTRAL",
            "cvd_divergence": "NONE",
            "is_lunch_chop": is_lunch_hour_chop(latest_bar),
        }

    # 1. Compute ATR over available bars (up to 14 periods)
    tr_list = []
    for i in range(1, len(bars)):
        try:
            h = float(getattr(bars[i], "high", 0.0) or 0.0)
            l = float(getattr(bars[i], "low", 0.0) or 0.0)
            prev_c = float(getattr(bars[i - 1], "close", 0.0) or 0.0)
            if h > 0 and l > 0 and prev_c > 0:
                tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                tr_list.append(tr)
        except Exception:
            continue
    atr_val = (sum(tr_list[-14:]) / min(len(tr_list), 14)) if tr_list else fallback_atr

    # 2. Compute EMA-9 and EMA-21 on closes
    closes = []
    for b in bars:
        try:
            c = float(getattr(b, "close", 0.0) or 0.0)
            if c > 0:
                closes.append(c)
        except Exception:
            continue

    # 3. Compute ADX and regime
    adx_val, adx_regime = compute_adx(bars, period=14)

    # 4. Compute CVD divergence
    cvd_div = compute_cvd_divergence(bars, atr=atr_val)

    # 5. Check lunch hour
    lunch_chop = is_lunch_hour_chop(latest_bar or bars[-1])

    if len(closes) < 9:
        return {
            "ema9": None,
            "ema21": None,
            "ema_trend": "NEUTRAL",
            "atr": round(atr_val, 2),
            "adx": adx_val,
            "adx_regime": adx_regime,
            "cvd_divergence": cvd_div,
            "is_lunch_chop": lunch_chop,
        }

    def _calc_ema(series: list[float], span: int) -> float:
        alpha = 2.0 / (span + 1.0)
        curr_ema = series[0]
        for val in series[1:]:
            curr_ema = alpha * val + (1.0 - alpha) * curr_ema
        return curr_ema

    ema9 = _calc_ema(closes, 9)
    span21 = min(len(closes), 21)
    ema21 = _calc_ema(closes, span21)

    diff = ema9 - ema21
    # Threshold buffer of 1.5 pts to prevent micro-oscillation at cross boundary
    if diff > 1.5:
        trend = "BULLISH"
    elif diff < -1.5:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    return {
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "ema_trend": trend,
        "atr": round(atr_val, 2),
        "adx": adx_val,
        "adx_regime": adx_regime,
        "cvd_divergence": cvd_div,
        "is_lunch_chop": lunch_chop,
    }


def compute_sector_breadth_context(sector_rows: list[dict] | None = None) -> dict[str, Any]:
    """Evaluates Nifty sector breadth alignment across key heavyweights (Bank, IT, Energy/Oil)."""
    rows = sector_rows
    if isinstance(rows, dict):
        rows = rows.get("rows")
    if not rows or not isinstance(rows, list):
        return {"breadth": "NEUTRAL", "bull_count": 0, "bear_count": 0, "summary": ""}

    bank_pct = None
    it_pct = None
    energy_pct = None

    for r in rows:
        lbl = str(r.get("label") or r.get("sector") or r.get("symbol") or "").upper()
        pct = r.get("pct") if r.get("pct") is not None else r.get("change_pct")
        if pct is None:
            continue
        try:
            f_pct = float(pct)
        except (ValueError, TypeError):
            continue

        if "BANK" in lbl and bank_pct is None:
            bank_pct = f_pct
        elif "IT" in lbl and it_pct is None:
            it_pct = f_pct
        elif ("ENERGY" in lbl or "OIL" in lbl) and energy_pct is None:
            energy_pct = f_pct

    bull_count = 0
    bear_count = 0
    checked = []

    for name, val in [("Bank", bank_pct), ("IT", it_pct), ("Energy", energy_pct)]:
        if val is not None:
            checked.append(f"{name} {val:+.2f}%")
            if val > 0.05:
                bull_count += 1
            elif val < -0.05:
                bear_count += 1

    summary_str = ", ".join(checked)
    if bull_count >= 2:
        breadth = "BULLISH"
    elif bear_count >= 2:
        breadth = "BEARISH"
    else:
        breadth = "NEUTRAL"

    return {
        "breadth": breadth,
        "bull_count": bull_count,
        "bear_count": bear_count,
        "summary": summary_str,
    }


def compute_fii_positioning_context() -> dict[str, Any]:
    """Reads latest captured FII index futures positioning from participant OI journal."""
    try:
        from app.participant_oi import latest_available
        latest = latest_available()
        if not latest:
            return {"fii_bias": "NEUTRAL", "net_fut": 0, "date": ""}
        date_ist, parsed = latest
        fii_data = (parsed or {}).get("FII", {})
        fl = fii_data.get("fut_long") or 0
        fs = fii_data.get("fut_short") or 0
        net_fut = int(fl) - int(fs)

        if net_fut > 15_000:
            bias = "BULLISH"
        elif net_fut < -15_000:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        return {
            "fii_bias": bias,
            "net_fut": net_fut,
            "date": date_ist,
        }
    except Exception:
        return {"fii_bias": "NEUTRAL", "net_fut": 0, "date": ""}


def _build_spread_alternative(
    idea: TradeIdea,
    contracts_map: dict[tuple[str, float], tuple[float, float, float, float]] | None,
    step: int,
    eff_iv: float = 0.14,
) -> dict[str, Any] | None:
    """Build a defined-risk vertical spread alternative to reduce theta decay and delta risk."""
    try:
        is_call = (idea.side == "CE")
        buy_strike = float(idea.strike)

        if is_call:
            sell_strike = buy_strike + step
            spread_name = "Bull Call Spread"
        else:
            sell_strike = buy_strike - step
            spread_name = "Bear Put Spread"

        buy_ltp = float(idea.current_ltp)
        if buy_ltp <= 0.0:
            return None

        c_flag = "C" if is_call else "P"
        sell_ltp = 0.0
        if contracts_map:
            entry = contracts_map.get((c_flag, sell_strike))
            if entry and entry[0] > 0:
                sell_ltp = float(entry[0])

        if sell_ltp <= 0.0:
            px, _ = _approx_bs_price_and_delta(spot=buy_strike, strike=sell_strike, is_call=is_call, iv=eff_iv)
            sell_ltp = max(0.5, round(px, 1))

        if sell_ltp >= buy_ltp:
            sell_ltp = round(buy_ltp * 0.55, 1)

        net_debit = max(1.0, round(buy_ltp - sell_ltp, 1))
        spread_width = float(step)
        max_profit = max(0.5, round(spread_width - net_debit, 1))
        max_loss = net_debit

        if is_call:
            breakeven = round(buy_strike + net_debit, 1)
        else:
            breakeven = round(buy_strike - net_debit, 1)

        rr_ratio = f"1 : {max_profit / max_loss:.2f}" if max_loss > 0 else "1 : 1.5"

        return {
            "spread_name": spread_name,
            "buy_leg": f"{idea.symbol} {int(buy_strike)} {c_flag}E @ ₹{buy_ltp:,.1f}",
            "sell_leg": f"{idea.symbol} {int(sell_strike)} {c_flag}E @ ₹{sell_ltp:,.1f}",
            "long_leg": f"{idea.symbol} {int(buy_strike)} {c_flag}E @ ₹{buy_ltp:,.1f}",
            "short_leg": f"{idea.symbol} {int(sell_strike)} {c_flag}E @ ₹{sell_ltp:,.1f}",
            "buy_strike": buy_strike,
            "sell_strike": sell_strike,
            "long_strike": buy_strike,
            "short_strike": sell_strike,
            "net_cost": net_debit,
            "net_debit": net_debit,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "breakeven_spot": breakeven,
            "risk_reward": rr_ratio,
            "rr_ratio": rr_ratio,
            "spread_type": "VERTICAL_DEBIT",
        }
    except Exception:
        return None


def format_time_since_active(
    latch_state: str,
    last_active_ts: float,
    first_active_ts: float = 0.0,
    now_ts: float | None = None,
) -> tuple[str, str, str]:
    """Calculate and format the 'time since last Active' counter for a recommendation card.

    Returns:
        tuple of (display_label, color_token, tooltip_description)
    """
    now = now_ts or time.time()
    st = str(latch_state or "QUALIFIED").upper()

    if st == "ACTIVE":
        base_ts = first_active_ts if first_active_ts > 0 else (last_active_ts if last_active_ts > 0 else now)
        dur = max(0, int(now - base_ts))
        if dur < 60:
            dur_str = f"{dur}s"
        elif dur < 3600:
            dur_str = f"{dur // 60}m {dur % 60:02d}s"
        else:
            dur_str = f"{dur // 3600}h {(dur % 3600) // 60:02d}m"
        return f"🟢 Active: {dur_str}", "var(--go)", f"Setup active for {dur_str} (dwell lease guaranteed)"

    elif st == "TARGET_HIT":
        base_ts = last_active_ts if last_active_ts > 0 else now
        elapsed = max(0, int(now - base_ts))
        if elapsed < 60:
            el_str = f"{elapsed}s ago"
        elif elapsed < 3600:
            el_str = f"{elapsed // 60}m ago"
        else:
            el_str = f"{elapsed // 3600}h {(elapsed % 3600) // 60}m ago"
        return f"🎯 Target Hit ({el_str})", "var(--go)", f"Target 1 achieved {el_str}"

    elif st == "INVALIDATED":
        base_ts = last_active_ts if last_active_ts > 0 else now
        elapsed = max(0, int(now - base_ts))
        if elapsed < 60:
            el_str = f"{elapsed}s ago"
        elif elapsed < 3600:
            el_str = f"{elapsed // 60}m ago"
        else:
            el_str = f"{elapsed // 3600}h {(elapsed % 3600) // 60}m ago"
        return f"🛑 Inval ({el_str})", "var(--stop)", f"Spot invalidation breached {el_str}"

    else:
        # DEGRADED, QUALIFIED, DORMANT
        if last_active_ts > 0:
            elapsed = max(0, int(now - last_active_ts))
            if elapsed < 60:
                el_str = f"{elapsed}s ago"
            elif elapsed < 3600:
                el_str = f"{elapsed // 60}m {elapsed % 60:02d}s ago"
            else:
                el_str = f"{elapsed // 3600}h {(elapsed % 3600) // 60:02d}m ago"
            return f"⏱️ Last Active: {el_str}", "#f59e0b", f"Setup conviction decayed below threshold; last active {el_str}"
        else:
            return "⏱️ Pending Active", "var(--ink-mute)", "Setup evaluating; has not reached active threshold today"


class RecommendationLatchManager:
    """Latch active recommendations across evaluation cycles.
    Prevents recommendations from appearing and vanishing rapidly due to minor micro-tick fluctuations.
    """
    def __init__(self, dwell_lease_s: float = 180.0):
        self._latched: dict[str, dict[str, Any]] = {}
        self.dwell_lease_s = dwell_lease_s
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._latched.clear()

    def process_and_latch(
        self,
        symbol: str,
        current_spot: float,
        candidates: list[TradeIdea],
        min_score: int = 75,
    ) -> list[TradeIdea]:
        now = time.time()
        with self._lock:
            # Clean up stale latched records (> 1 hour old or from another symbol)
            to_remove = [
                k for k, v in self._latched.items()
                if v.get("symbol") == symbol and (now - v.get("last_seen_ts", now) > 3600.0)
            ]
            for k in to_remove:
                self._latched.pop(k, None)

            for idea in candidates:
                rec_id = idea.rec_id or f"{symbol}_{idea.side}_{int(idea.strike)}_{idea.play_badge}"
                entry = self._latched.get(rec_id)

                # Check spot boundary
                inval_breached = False
                target_hit = False
                if current_spot > 0:
                    if idea.side == "CE":
                        inval_breached = (current_spot < idea.spot_invalidation)
                        target_hit = (current_spot >= idea.target_1_spot)
                    else:
                        inval_breached = (current_spot > idea.spot_invalidation)
                        target_hit = (current_spot <= idea.target_1_spot)

                if inval_breached:
                    idea.latch_state = "INVALIDATED"
                    if entry:
                        entry["latch_state"] = "INVALIDATED"
                        idea.first_active_ts = entry.get("first_active_ts", 0.0)
                        idea.last_active_ts = entry.get("last_active_ts", 0.0)
                    lbl, _, _ = format_time_since_active("INVALIDATED", idea.last_active_ts, idea.first_active_ts, now)
                    idea.time_since_active_label = lbl
                    idea.time_since_active_sec = max(0.0, now - idea.last_active_ts) if idea.last_active_ts > 0 else 0.0
                    continue

                if target_hit:
                    idea.latch_state = "TARGET_HIT"
                    if entry:
                        entry["latch_state"] = "TARGET_HIT"
                        idea.first_active_ts = entry.get("first_active_ts", now)
                        idea.last_active_ts = entry.get("last_active_ts", now)
                    lbl, _, _ = format_time_since_active("TARGET_HIT", idea.last_active_ts, idea.first_active_ts, now)
                    idea.time_since_active_label = lbl
                    idea.time_since_active_sec = max(0.0, now - idea.last_active_ts) if idea.last_active_ts > 0 else 0.0
                    continue

                if entry is not None:
                    first_ts = entry.get("first_qualified_ts", now)
                    initial_score = entry.get("initial_score", 75)
                    # Preserve original entry anchors so cards do not reset or jump
                    if entry.get("triggered_at"):
                        idea.triggered_at = entry["triggered_at"]
                    if entry.get("initial_ltp", 0) > 0:
                        idea.initial_ltp = float(entry["initial_ltp"])
                    if entry.get("initial_spot", 0) > 0:
                        idea.initial_spot = float(entry["initial_spot"])

                    if idea.quality_score >= min_score:
                        entry["last_seen_ts"] = now
                        entry["last_active_ts"] = now
                        if not entry.get("first_active_ts"):
                            entry["first_active_ts"] = now
                        entry["last_score"] = idea.quality_score
                        entry["latch_state"] = "ACTIVE"
                        idea.latch_state = "ACTIVE"
                        idea.first_active_ts = entry["first_active_ts"]
                        idea.last_active_ts = now
                    elif (now - first_ts < self.dwell_lease_s) and idea.quality_score >= 65 and min_score <= initial_score:
                        entry["last_seen_ts"] = now
                        entry["last_active_ts"] = now
                        if not entry.get("first_active_ts"):
                            entry["first_active_ts"] = now
                        idea.latch_state = "ACTIVE"
                        idea.first_active_ts = entry["first_active_ts"]
                        idea.last_active_ts = now
                    else:
                        entry["latch_state"] = "DEGRADED"
                        idea.latch_state = "DEGRADED"
                        idea.first_active_ts = entry.get("first_active_ts", 0.0)
                        idea.last_active_ts = entry.get("last_active_ts", 0.0)
                else:
                    if idea.quality_score >= min_score:
                        idea.latch_state = "ACTIVE"
                        init_ltp = float(idea.current_ltp or 0.0)
                        init_spot = float(current_spot or 0.0)
                        idea.initial_ltp = init_ltp
                        idea.initial_spot = init_spot
                        idea.first_active_ts = now
                        idea.last_active_ts = now
                        self._latched[rec_id] = {
                            "symbol": symbol,
                            "first_qualified_ts": now,
                            "first_active_ts": now,
                            "last_seen_ts": now,
                            "last_active_ts": now,
                            "initial_score": idea.quality_score,
                            "last_score": idea.quality_score,
                            "latch_state": "ACTIVE",
                            "triggered_at": idea.triggered_at,
                            "initial_ltp": init_ltp,
                            "initial_spot": init_spot,
                        }
                    else:
                        idea.latch_state = "QUALIFIED" if idea.quality_score >= 65 else "DEGRADED"
                        idea.first_active_ts = 0.0
                        idea.last_active_ts = 0.0

                lbl, _, _ = format_time_since_active(idea.latch_state, idea.last_active_ts, idea.first_active_ts, now)
                idea.time_since_active_label = lbl
                if idea.latch_state == "ACTIVE":
                    idea.time_since_active_sec = max(0.0, now - idea.first_active_ts)
                else:
                    idea.time_since_active_sec = max(0.0, now - idea.last_active_ts) if idea.last_active_ts > 0 else 0.0

        return candidates


_LATCH_MANAGER = RecommendationLatchManager()


def clear_latch_manager() -> None:
    """Reset the latch manager cache for testing and session isolation."""
    _LATCH_MANAGER.clear()


def update_trade_idea_live_telemetry(
    idea: TradeIdea,
    symbol: str,
    current_spot: float,
) -> TradeIdea:
    """Sub-millisecond (< 0.05ms) in-memory MTM pricing, live option tick resolution, and lifecycle updates.

    1. Direct WebSocket Tick from _STORE_OPTIONS if available and consistent with current_spot.
    2. Preserves authentic Option Chain mid/traded price if already resolved.
    3. Real-time Spot Delta Mark-to-Market Model as secondary fallback if price is missing.
    4. Unbroken Entry Price and Trigger Anchor tracking.
    5. Real-time Unrealized PnL, Target Progress %, and Invalidation Buffer.
    6. Dynamic Time Since Last Active Counter.
    """
    if idea.initial_ltp <= 0.0:
        idea.initial_ltp = float(idea.current_ltp if idea.current_ltp > 0 else 1.0)
    if idea.initial_spot <= 0.0 and current_spot > 0:
        idea.initial_spot = float(current_spot)

    # 1. Check direct WebSocket quote (guarding against mock/replay spot mismatch)
    ws_q = None
    try:
        from app.spot_ticker import _STORE, get_option_ws_quote
        feed_spot = float(((_STORE.get(symbol) or {}).get("ohlc") or {}).get("ltp") or 0.0)
        # Only accept WS option quote if live feed spot is consistent with current_spot (within 100 pts)
        if not (feed_spot > 0 and current_spot > 0 and abs(feed_spot - current_spot) > 100.0):
            ws_q = get_option_ws_quote(symbol, idea.strike, idea.side, max_age_s=120.0)
    except Exception:
        ws_q = None

    if ws_q and float(ws_q.get("ltp") or 0.0) > 0:
        idea.current_ltp = float(ws_q["ltp"])
        idea.is_ws_live = True
    else:
        idea.is_ws_live = False
        # Preserve authentic chain price if already populated; fallback to spot-delta only if missing
        if idea.current_ltp <= 0.0:
            if current_spot > 0 and idea.initial_spot > 0:
                spot_diff = current_spot - idea.initial_spot
                if idea.side == "PE":
                    spot_diff = -spot_diff
                eff_delta = max(0.15, min(0.95, abs(float(idea.delta_approx or 0.50))))
                idea.current_ltp = max(0.5, round(idea.initial_ltp + (spot_diff * eff_delta), 1))
            else:
                idea.current_ltp = max(0.5, float(idea.initial_ltp or 1.0))

    # Maintain strict SL < LTP invariant for live rendering
    if idea.current_ltp > 0.0 and idea.stop_loss >= idea.current_ltp:
        idea.stop_loss = max(0.05, round(idea.current_ltp * 0.72, 2))

    _refresh_trade_idea_pnl(idea, current_spot)
    return idea


def _refresh_trade_idea_pnl(idea: TradeIdea, current_spot: float) -> None:
    """Recompute MTM, T1 progress, and invalidation buffer after LTP/target updates."""
    if idea.initial_ltp > 0:
        idea.live_pnl_pts = round(idea.current_ltp - idea.initial_ltp, 1)
        idea.live_pnl_pct = round((idea.live_pnl_pts / idea.initial_ltp) * 100.0, 1)
    else:
        idea.live_pnl_pts = 0.0
        idea.live_pnl_pct = 0.0

    tgt1_pts = idea.target_1 - idea.initial_ltp
    if tgt1_pts > 0:
        prog = ((idea.current_ltp - idea.initial_ltp) / tgt1_pts) * 100.0
        idea.target_progress_pct = max(0.0, min(100.0, round(prog, 1)))
    else:
        idea.target_progress_pct = 0.0

    if current_spot > 0:
        if idea.side == "CE":
            idea.inval_buffer = current_spot - idea.spot_invalidation
            idea.inval_breached = (current_spot < idea.spot_invalidation)
        else:
            idea.inval_buffer = idea.spot_invalidation - current_spot
            idea.inval_breached = (current_spot > idea.spot_invalidation)

    if getattr(idea, "last_active_ts", 0.0) > 0 or getattr(idea, "first_active_ts", 0.0) > 0:
        now_sec = time.time()
        lbl, _, _ = format_time_since_active(
            latch_state=getattr(idea, "latch_state", "QUALIFIED"),
            last_active_ts=getattr(idea, "last_active_ts", 0.0),
            first_active_ts=getattr(idea, "first_active_ts", 0.0),
            now_ts=now_sec,
        )
        idea.time_since_active_label = lbl
        if getattr(idea, "latch_state", "") == "ACTIVE":
            idea.time_since_active_sec = max(0.0, now_sec - (idea.first_active_ts or now_sec))
        else:
            idea.time_since_active_sec = max(0.0, now_sec - idea.last_active_ts) if idea.last_active_ts > 0 else 0.0


def refresh_trade_idea_live_mtm(
    idea: TradeIdea,
    symbol: str,
    current_spot: float,
    *,
    reprice_targets: bool = True,
    step: int = 50,
) -> TradeIdea:
    """Overlay live option LTP and (optionally) recompute T1/T2/SL from current premium + spot."""
    update_trade_idea_live_telemetry(idea, symbol, current_spot)
    if not reprice_targets:
        return idea
    ltp = float(idea.current_ltp or 0.0)
    if ltp <= 0 or current_spot <= 0:
        return idea
    greeks = getattr(idea, "greek_attribution", None) or {}
    atm_iv = float(greeks.get("iv") or greeks.get("sigma") or 0.14)
    dte_days = float(getattr(idea, "dte_now", None) or 3.0)
    try:
        sl, t1, t2, p_t1, p_sl, ev, rr, _g = _reprice_trade_idea_targets(
            side=idea.side,
            strike=float(idea.strike),
            current_ltp=ltp,
            spot=float(current_spot),
            inval_spot=float(idea.spot_invalidation or current_spot),
            tgt1_spot=float(idea.target_1_spot or current_spot),
            tgt2_spot=float(idea.target_2_spot or current_spot),
            dte_days=dte_days,
            atm_iv=atm_iv,
            step=step,
        )
        idea.stop_loss = float(sl)
        idea.target_1 = float(t1)
        idea.target_2 = float(t2)
        idea.p_touch_target_1 = float(p_t1)
        idea.p_touch_sl = float(p_sl)
        idea.net_ev_pts = float(ev)
        idea.risk_reward_ratio = str(rr)
    except Exception:
        if idea.stop_loss >= ltp:
            idea.stop_loss = max(0.05, round(ltp * 0.72, 2))
        if idea.target_1 <= ltp:
            idea.target_1 = round(ltp * 1.25, 1)
        if idea.target_2 <= idea.target_1:
            idea.target_2 = round(idea.target_1 * 1.25, 1)
    _refresh_trade_idea_pnl(idea, current_spot)
    return idea


def _enrich_trade_idea(
    idea: TradeIdea,
    contracts_map: dict | None,
    step: int,
    eff_iv: float,
    adx_5m: float,
    adx_regime: str,
    cvd_divergence: str,
    sector_breadth: str,
    fii_macro_bias: str,
    is_lunch_chop: bool,
) -> None:
    """Enrich TradeIdea with regime indicators, contextual catalyst tags, and defined-risk spread alternative."""
    idea.adx_5m = adx_5m
    idea.adx_regime = adx_regime
    idea.cvd_divergence = cvd_divergence

    if adx_regime == "TRENDING" and idea.play_badge in ("MOMENTUM", "STRUCTURE"):
        idea.catalyst_tags.append(f"ADX Trending ({adx_5m:.0f})")
    elif adx_regime == "CHOP" and idea.play_badge in ("ICEBERG FADE", "VANNA-CHARM"):
        idea.catalyst_tags.append("ADX chop")

    if cvd_divergence == "BULLISH" and idea.side == "CE":
        idea.catalyst_tags.append("Bullish CVD Div")
    elif cvd_divergence == "BEARISH" and idea.side == "PE":
        idea.catalyst_tags.append("Bearish CVD Div")

    if sector_breadth == "BULLISH" and idea.side == "CE":
        idea.catalyst_tags.append("Sector Breadth (+)")
    elif sector_breadth == "BEARISH" and idea.side == "PE":
        idea.catalyst_tags.append("Sector Breadth (-)")

    if fii_macro_bias == "BULLISH" and idea.side == "CE":
        idea.catalyst_tags.append("FII Net Long")
    elif fii_macro_bias == "BEARISH" and idea.side == "PE":
        idea.catalyst_tags.append("FII Net Short")

    if is_lunch_chop and idea.play_badge in ("MOMENTUM", "STRIKE BREAK"):
        idea.catalyst_tags.append("Lunch Chop Caution")

    idea.spread_alternative = _build_spread_alternative(idea, contracts_map, step, eff_iv)


def _compute_trade_score(
    side: str,
    play_type: str,
    bias_score: float,
    spot: float,
    vwap: float,
    call_wall: float,
    put_wall: float,
    sentinel_signals: Sequence[Any] | None,
    leg_doi: float,
    vwap_upper: float = 0.0,
    vwap_lower: float = 0.0,
    flip: float = 0.0,
    scenario_set: Any | None = None,
    ema_trend: str | None = None,
    atr: float | None = None,
    adx_regime: str | None = None,
    adx_val: float | None = None,
    cvd_divergence: str | None = None,
    is_lunch_chop: bool = False,
    sector_breadth: str | None = None,
    fii_macro_bias: str | None = None,
    atm_iv: float | None = None,
    pre_market_regime: str | None = None,
    gap_fill_status: str | None = None,
) -> tuple[int, str, str]:
    """Calculates Trade Quality Score (0-100), Conviction Label, and Badge Color."""
    score = 55
    pt_upper = play_type.upper()

    # 0. Short-term Trend & EMA-9/21 Alignment Gate (Weight: ±12 pts)
    if ema_trend:
        t_upper = str(ema_trend).upper()
        if t_upper == "BULLISH":
            if side == "CE":
                if "MOMENTUM" in pt_upper or "STRUCTURE" in pt_upper:
                    score += 8   # Trend confluence: 9-EMA > 21-EMA confirms upward momentum
                else:
                    score += 4
            else:  # PE
                if "MOMENTUM" in pt_upper or "STRUCTURE" in pt_upper:
                    score -= 12  # Counter-trend penalty: shorting into a rising 9/21 EMA ribbon
                elif "ICEBERG" in pt_upper or "ABSORPTION" in pt_upper:
                    score += 4   # Fading extreme upper exhaustion is allowed with absorption
        elif t_upper == "BEARISH":
            if side == "PE":
                if "MOMENTUM" in pt_upper or "STRUCTURE" in pt_upper:
                    score += 8   # Trend confluence: 9-EMA < 21-EMA confirms downward momentum
                else:
                    score += 4
            else:  # CE
                if "MOMENTUM" in pt_upper or "STRUCTURE" in pt_upper:
                    score -= 12  # Counter-trend penalty: buying calls into a falling 9/21 EMA ribbon
                elif "ICEBERG" in pt_upper or "ABSORPTION" in pt_upper:
                    score += 4   # Fading extreme lower absorption is allowed

    # 1. Macro & Directional Bias alignment (Weight: 25 pts)
    if side == "CE":
        if bias_score >= 4.0:
            score += 25
        elif bias_score >= 1.5:
            score += 18
        elif bias_score <= -4.0:
            score -= 22
        elif bias_score <= -1.5:
            score -= 12
    else:  # PE
        if bias_score <= -4.0:
            score += 25
        elif bias_score <= -1.5:
            score += 18
        elif bias_score >= 4.0:
            score -= 22
        elif bias_score >= 1.5:
            score -= 12

    # 2. VWAP & Equilibrium Location (Continuous Sigmoid Scoring: eliminates cliff oscillation)
    eff_atr = float(atr if (atr and atr > 5.0) else 35.0)
    norm_dist = (spot - vwap) / max(eff_atr * 0.35, 10.0)
    sig_arg = max(-12.0, min(12.0, 2.0 * norm_dist))
    sig_val = 1.0 / (1.0 + math.exp(-sig_arg))  # 0.5 at spot == vwap

    if side == "CE":
        if "MOMENTUM" in pt_upper:
            # Smooth transition from -8 (far below VWAP) to +12 (firmly above VWAP)
            vwap_pts = -8.0 + 20.0 * sig_val
            score += round(vwap_pts)
        elif "ICEBERG" in pt_upper or "ABSORPTION" in pt_upper:
            # Buying absorption near or below VWAP / lower band is setup objective
            vwap_pts = 12.0 - 7.0 * max(0.0, min(1.0, (sig_val - 0.5) * 2.0))
            score += round(vwap_pts)
        else:
            # Structure / support plays smoothly reward trading in upper distribution
            vwap_pts = 3.0 + 9.0 * sig_val
            score += round(vwap_pts)
    else:  # PE
        if "MOMENTUM" in pt_upper:
            # For PE: spot below VWAP is favorable (1.0 - sig_val)
            vwap_pts = -8.0 + 20.0 * (1.0 - sig_val)
            score += round(vwap_pts)
        elif "ICEBERG" in pt_upper or "ABSORPTION" in pt_upper:
            # Shorting exhaustion above VWAP / upper band
            vwap_pts = 12.0 - 7.0 * max(0.0, min(1.0, ((1.0 - sig_val) - 0.5) * 2.0))
            score += round(vwap_pts)
        else:
            vwap_pts = 3.0 + 9.0 * (1.0 - sig_val)
            score += round(vwap_pts)

    # 3. Sentinel OrderFlow signals (Weight: 20 pts)
    has_absorption_sig = False
    has_exhaustion_sig = False
    has_breakout_sig = False
    if sentinel_signals:
        top_sigs = [s for s in sentinel_signals if getattr(s, "score", 0) >= 55][:4]
        for sig in top_sigs:
            cat = str(getattr(sig, "category", "")).upper()
            sig_type = str(getattr(sig, "type", "")).upper()
            if "ABSORPTION" in sig_type or "ABSORPTION" in cat:
                has_absorption_sig = True
            if "EXHAUSTION" in sig_type or "EXHAUSTION" in cat:
                has_exhaustion_sig = True
            if "BREAKOUT" in sig_type or "MOMENTUM" in sig_type:
                has_breakout_sig = True

            if side == "CE":
                if cat == "BULLISH" or "BUY" in sig_type or "ABSORPTION" in sig_type:
                    score += 10
                    break
                elif cat == "BEARISH":
                    score -= 8
            else:  # PE
                if cat == "BEARISH" or "SELL" in sig_type or "EXHAUSTION" in sig_type:
                    score += 10
                    break
                elif cat == "BULLISH":
                    score -= 8

    # 4. Buildup & Velocity confirmation (Weight: 10 pts)
    if leg_doi > 0:
        score += 8  # Open interest expanding in direction of trade

    # 5. Play type specific bonuses & institutional condition tests
    if "STRIKE BREAK" in pt_upper:
        dist_to_wall = abs(spot - (call_wall if side == "CE" else put_wall))
        if dist_to_wall <= (spot * 0.006):  # within 0.6% of wall
            score += 12
        else:
            score -= 6

    elif "ICEBERG" in pt_upper or "ABSORPTION" in pt_upper:
        # Edge: Absorption at VWAP envelopes
        if side == "CE":
            at_lower_band = (vwap_lower > 0 and spot <= (vwap_lower + (vwap - vwap_lower) * 0.35)) or (spot < vwap)
            if at_lower_band:
                score += 12
            if has_absorption_sig or has_exhaustion_sig:
                score += 10
        else:
            at_upper_band = (vwap_upper > 0 and spot >= (vwap_upper - (vwap_upper - vwap) * 0.35)) or (spot > vwap)
            if at_upper_band:
                score += 12
            if has_absorption_sig or has_exhaustion_sig:
                score += 10

    elif "VANNA" in pt_upper or "CHARM" in pt_upper or "THETA" in pt_upper:
        # Charm while spot sits between the gamma strikes. Near the balance is not a pin bonus.
        inside_corridor = (put_wall <= spot <= call_wall) or (abs(call_wall - put_wall) > 0 and put_wall * 0.998 <= spot <= call_wall * 1.002)
        if inside_corridor:
            score += 10
        if abs(bias_score) <= 2.5:
            score += 8

    elif "VOL" in pt_upper or "DISPERSION" in pt_upper:
        # Edge: Range compression ready for explosive breakout
        if abs(spot - vwap) <= max(25.0, spot * 0.0015):
            score += 10
        if has_breakout_sig or abs(bias_score) >= 2.5:
            score += 8

    elif "RATIO" in pt_upper or "REVERSAL" in pt_upper:
        # Edge: Moderate directional drift with high R:R to Wall
        if side == "CE":
            if 1.0 <= bias_score <= 4.0:
                score += 14
            elif bias_score > 4.0:
                score += 6
        else:
            if -4.0 <= bias_score <= -1.0:
                score += 14
            elif bias_score < -4.0:
                score += 6

    # 6. Target Engine Scenario Confluence (Weight: ±12 pts)
    if scenario_set is not None:
        try:
            p_bull = float(scenario_set.get_prob("bull")) if hasattr(scenario_set, "get_prob") else 0.33
            p_base = float(scenario_set.get_prob("base")) if hasattr(scenario_set, "get_prob") else 0.34
            p_bear = float(scenario_set.get_prob("bear")) if hasattr(scenario_set, "get_prob") else 0.33
            dom_scen = getattr(scenario_set, "dominant_scenario", "base")
            no_edge = getattr(scenario_set, "no_edge", False)

            if no_edge or p_base >= 0.45:
                # A base path is a range on the scenario dial. It does not add a pin point.
                pass
            else:
                # Confirmed directional edge
                if side == "CE":
                    if dom_scen == "bull" or p_bull >= 0.45:
                        score += 10
                    elif dom_scen == "bear" or p_bear >= 0.45:
                        score -= 10
                else:  # PE
                    if dom_scen == "bear" or p_bear >= 0.45:
                        score += 10
                    elif dom_scen == "bull" or p_bull >= 0.45:
                        score -= 10
        except Exception:
            pass

    # 7. ADX Regime Gate (Weight: ±10 pts)
    # ADX < 20 indicates sideways chop where momentum false breakouts thrive.
    # ADX > 25 confirms sustained directional trend follow-through.
    if adx_regime:
        a_reg = str(adx_regime).upper()
        if a_reg == "CHOP":
            if "MOMENTUM" in pt_upper or "STRUCTURE" in pt_upper or "STRIKE BREAK" in pt_upper:
                score -= 10  # A chase is a poor fit when ADX says the tape is choppy
        elif a_reg == "TRENDING":
            if "MOMENTUM" in pt_upper or "STRUCTURE" in pt_upper:
                score += 8   # Trend follow-through confirmed by ADX > 25
            elif "ICEBERG" in pt_upper or "VANNA" in pt_upper:
                score -= 8   # A chop setup is a poor fit when ADX says the tape is trending

    # 8. Time-of-Day / RVOL Lunch Hour Filter (Weight: -10 pts)
    if is_lunch_chop:
        if "MOMENTUM" in pt_upper or "STRUCTURE" in pt_upper or "STRIKE BREAK" in pt_upper:
            score -= 10  # Suppress naked momentum during 11:30-13:30 low-volume theta bleed

    # 9. OrderFlow CVD Slope Divergence Filter (Weight: ±8 pts)
    if cvd_divergence:
        c_div = str(cvd_divergence).upper()
        if c_div == "BEARISH":
            if side == "CE":
                score -= 8
            else:
                score += 8
        elif c_div == "BULLISH":
            if side == "CE":
                score += 8
            else:
                score -= 8

    # 10. Sector Breadth Alignment (Weight: ±6 pts)
    if sector_breadth:
        b_reg = str(sector_breadth).upper()
        if b_reg == "BULLISH":
            if side == "CE":
                score += 6
            else:
                score -= 6
        elif b_reg == "BEARISH":
            if side == "PE":
                score += 6
            else:
                score -= 6

    # 11. FII Net Futures Position (T-1) Macro Filter (Weight: ±5 pts)
    if fii_macro_bias:
        f_reg = str(fii_macro_bias).upper()
        if f_reg == "BULLISH":
            if side == "CE":
                score += 5
            else:
                score -= 5
        elif f_reg == "BEARISH":
            if side == "PE":
                score += 5
            else:
                score -= 5

    # 12. Intraday IV Percentile / Crush Gate (Weight: ±6 pts)
    if atm_iv is not None and atm_iv > 0:
        if atm_iv < 0.115:
            if "MOMENTUM" in pt_upper or "VOL DISPERSION" in pt_upper or "STRIKE BREAK" in pt_upper:
                score += 4
        elif atm_iv > 0.165:
            if "MOMENTUM" in pt_upper:
                score -= 6
            elif "VANNA" in pt_upper or "CHARM" in pt_upper or "ICEBERG" in pt_upper:
                score += 5

    # 13. Pre-Market Opening Regime & Gap Confluence (Weight: ±6 pts)
    if pre_market_regime:
        pm_reg = str(pre_market_regime).upper()
        is_gap_unfilled = (gap_fill_status != "FILLED")
        if pm_reg in ("GAP_UP_RUNAWAY", "CALL_WALL_BREACH_SQUEEZE"):
            if side == "CE" and ("MOMENTUM" in pt_upper or "STRUCTURE" in pt_upper):
                score += 5   # Confirmed strong open-drive gap up
            elif side == "PE" and "MOMENTUM" in pt_upper and is_gap_unfilled:
                score -= 6   # Penalize short momentum fighting an unfilled bullish gap
        elif pm_reg in ("GAP_DOWN_TRAP", "PUT_WALL_BREAKDOWN_CASCADE"):
            if side == "PE" and ("MOMENTUM" in pt_upper or "STRUCTURE" in pt_upper):
                score += 5   # Confirmed strong open-drive gap down
            elif side == "CE" and "MOMENTUM" in pt_upper and is_gap_unfilled:
                score -= 6   # Penalize long momentum fighting an unfilled bearish gap
        elif pm_reg == "SHORT_GAMMA_WALL_TEST":
            if side == "CE" and "STRIKE BREAK" in pt_upper:
                score += 6   # Open is at the call-gamma strike. Location only.
        elif pm_reg == "PUT_WALL_BREAKDOWN_TEST":
            if side == "PE" and ("STRIKE BREAK" in pt_upper or "STRUCTURE" in pt_upper):
                score += 6   # Open is at the put-gamma strike. Location only.
        elif pm_reg == "AUCTION_BALANCED":
            if "ICEBERG" in pt_upper or "VANNA" in pt_upper or "CHARM" in pt_upper:
                score += 4   # Flat open. Not a call-heavy fade bonus.
            elif "MOMENTUM" in pt_upper:
                score -= 3   # Flat open. Momentum follow-through is not assumed.

    # Clamp to [15, 95]
    score = max(15, min(95, score))

    if score >= 80:
        label = "HIGH CONVICTION (Grade A+)"
        color = "var(--go)"
    elif score >= 75:
        label = "HIGH CONVICTION (Grade A)"
        color = "var(--go)"
    elif score >= 65:
        label = "TACTICAL OPPORTUNITY (Grade B+)"
        color = "var(--spark)"
    elif score >= 50:
        label = "COUNTER-TREND / SPECULATIVE (Grade C)"
        color = "#f59e0b"
    else:
        label = "LOW PROBABILITY / AVOID"
        color = "var(--stop)"

    return score, label, color


def compute_position_size(
    idea: TradeIdea,
    account_capital: float = 500_000.0,
    risk_pct: float = 1.5,
    lot_size: int = 65,
    max_outlay_pct: float = 0.15,
) -> PositionSizeInfo:
    """Computes risk-budgeted and conviction-adjusted position sizing for an option trade idea.

    Formula:
    1. Max Risk Allowance (INR) = account_capital * (risk_pct / 100.0)
    2. Per-share Risk (INR) = max(idea.current_ltp - idea.stop_loss, 1.0)
    3. Per-lot Risk (INR) = Per-share Risk * lot_size
    4. Raw Lots = floor(Max Risk / Per-lot Risk)
    5. Conviction Scaling:
       - Score >= 80: 100% of raw lots
       - Score >= 65: 75% of raw lots
       - Score < 65: 50% of raw lots (capped)
    6. Capital Outlay Cap = account_capital * max_outlay_pct (default 15%)
    """
    account_capital = max(float(account_capital), 10_000.0)
    lot_size = max(int(lot_size), 1)
    current_ltp = max(float(idea.current_ltp), 1.0)
    stop_loss = float(idea.stop_loss)
    t1 = float(idea.target_1)
    t2 = float(idea.target_2)

    max_risk_allowed = account_capital * (risk_pct / 100.0)
    per_share_risk = max(current_ltp - stop_loss, 0.5)
    per_lot_risk = per_share_risk * lot_size

    # Raw lots from stop loss distance
    raw_lots = int(max_risk_allowed // per_lot_risk) if per_lot_risk > 0 else 1

    # Conviction multiplier based on quality score & play type
    score = getattr(idea, "quality_score", 60)
    play_badge = getattr(idea, "play_badge", "")

    if score >= 80:
        conviction_mult = 1.0
        rationale_tier = "Grade A+ (Full Risk Allocation)"
    elif score >= 65:
        conviction_mult = 0.75
        rationale_tier = "Grade B+ (Tactical 75% Allocation)"
    else:
        conviction_mult = 0.50
        rationale_tier = "Grade C (Conservative 50% Allocation)"

    lots = int(math.ceil(raw_lots * conviction_mult)) if raw_lots > 0 else 0

    # Gamma Squeeze Lotto plays have fast time-decay / high convexity: cap to 2 lots for accounts < 10L
    if play_badge == "STRIKE BREAK" and lots > 2 and account_capital < 1_000_000.0:
        lots = min(lots, 2)
        rationale_tier += " · Lotto Cap (Max 2 Lots)"

    # Base minimum: 1 lot if capital permits
    one_lot_outlay = lot_size * current_ltp
    if lots <= 0 and account_capital >= one_lot_outlay:
        lots = 1
        rationale_tier = "Base 1 Lot Allocation"

    if lots <= 0:
        lots = 1

    # Outlay cap: No single trade can exceed max_outlay_pct (default 15%)
    max_outlay = account_capital * max_outlay_pct
    is_outlay_capped = False
    while lots > 1 and (lots * lot_size * current_ltp) > max_outlay:
        lots -= 1
        is_outlay_capped = True

    total_qty = lots * lot_size
    capital_outlay = round(total_qty * current_ltp, 2)
    portfolio_pct = round((capital_outlay / account_capital) * 100.0, 1)

    actual_max_risk = round(total_qty * per_share_risk, 2)
    risk_pct_portfolio = round((actual_max_risk / account_capital) * 100.0, 2)

    tgt1_pnl = round(total_qty * (t1 - current_ltp), 2)
    tgt2_pnl = round(total_qty * (t2 - current_ltp), 2)

    if is_outlay_capped:
        rationale_tier += f" · Capped by {int(max_outlay_pct * 100)}% Portfolio Outlay Limit"

    return PositionSizeInfo(
        suggested_lots=lots,
        total_quantity=total_qty,
        capital_outlay_inr=capital_outlay,
        portfolio_pct=portfolio_pct,
        max_risk_inr=actual_max_risk,
        risk_pct_portfolio=risk_pct_portfolio,
        target_1_pnl_inr=tgt1_pnl,
        target_2_pnl_inr=tgt2_pnl,
        is_outlay_capped=is_outlay_capped,
        sizing_rationale=rationale_tier,
    )


def _format_option_symbol(symbol: str, expiry_tag: str, strike: float, side: str) -> str:
    """Format institutional option symbol e.g. 'NIFTY 13 OCT 22700 CE'."""
    if expiry_tag:
        return f"{symbol} {expiry_tag} {int(strike)} {side}"
    return f"{symbol} {int(strike)} {side}"


def _generate_strategy_score_reason(
    idea: TradeIdea,
    spot: float,
    session_vwap: float,
    vwap_upper: float,
    vwap_lower: float,
    call_wall: float,
    put_wall: float,
    flip: float,
    bias_score: float,
    latest_bar: Any | None = None,
    sentinel_signals: Sequence[Any] | None = None,
) -> str:
    """Generate strategy-specific microstructure reasoning with precise data points."""
    badge = (idea.play_badge or "").upper()
    side = idea.side
    reasons: list[str] = []

    # 1. Strategy-specific edge & exact level data points
    if "ICEBERG" in badge:
        if side == "CE":
            band_dist = spot - vwap_lower
            reasons.append(f"VWAP lower band absorption (Spot ₹{spot:,.0f} vs -1σ band ₹{vwap_lower:,.0f}, {band_dist:+.0f} pts buffer)")
            if latest_bar and getattr(latest_bar, "delta_ratio", 0) > 0.05:
                reasons.append(f"Buyer tape absorption (Δ ratio {latest_bar.delta_ratio:+.2f})")
            else:
                reasons.append("Passive limit bids absorbing retail selling")
        else:
            band_dist = vwap_upper - spot
            reasons.append(f"VWAP upper band absorption (Spot ₹{spot:,.0f} vs +1σ band ₹{vwap_upper:,.0f}, {band_dist:+.0f} pts buffer)")
            if latest_bar and getattr(latest_bar, "delta_ratio", 0) < -0.05:
                reasons.append(f"Seller tape absorption (Δ ratio {latest_bar.delta_ratio:+.2f})")
            else:
                reasons.append("Passive limit asks absorbing retail buying")

    elif "STRIKE BREAK" in badge:
        wall = call_wall if side == "CE" else put_wall
        dist = abs(spot - wall)
        if side == "CE":
            reasons.append(f"Near the call-gamma strike ₹{call_wall:,.0f} ({dist:,.0f} pts). Not a measured hedge.")
            reasons.append("A print through the strike is a location on the book, not a squeeze.")
        else:
            reasons.append(f"Near the put-gamma strike ₹{put_wall:,.0f} ({dist:,.0f} pts). Not a floor.")
            reasons.append("A print through the strike is a location on the book, not a cascade.")

    elif "MOMENTUM" in badge:
        vwap_diff = spot - session_vwap
        if side == "CE":
            reasons.append(f"Holding above Session VWAP ₹{session_vwap:,.0f} ({vwap_diff:+.0f} pts)")
            reasons.append(f"Delta velocity ({idea.delta_approx:+.2f}) monetizing upward momentum")
        else:
            reasons.append(f"Holding below Session VWAP ₹{session_vwap:,.0f} ({vwap_diff:+.0f} pts)")
            reasons.append(f"Delta velocity ({idea.delta_approx:+.2f}) monetizing downward momentum")

    elif "VANNA" in badge or "CHARM" in badge:
        range_pts = abs(call_wall - put_wall)
        flip_dist = abs(spot - flip)
        reasons.append(f"Spot between gamma strikes, {range_pts:,.0f} pts apart (₹{put_wall:,.0f}–₹{call_wall:,.0f})")
        reasons.append(f"Call−put balance ₹{flip:,.0f} is {flip_dist:,.0f} pts away. Charm decay is not a pin.")

    elif "VOL DISPERSION" in badge or "VOL" in badge:
        comp_dist = abs(spot - session_vwap)
        reasons.append(f"Volatility compression within {comp_dist:,.0f} pts of Session VWAP (₹{session_vwap:,.0f})")
        reasons.append("Compression coiling for high-convexity long gamma expansion")

    elif "RATIO" in badge:
        target_wall = call_wall if side == "CE" else put_wall
        wall_dist = abs(spot - target_wall)
        reasons.append(f"Path runs toward the {'call-gamma' if side == 'CE' else 'put-gamma'} strike ₹{target_wall:,.0f} ({wall_dist:,.0f} pts away). That strike is a concentration, not a target the price must reach.")
        reasons.append(f"Structured R:R ({idea.risk_reward_ratio}) eliminating sideways theta decay")

    elif "STRUCTURE" in badge:
        wall = call_wall if side == "CE" else put_wall
        reasons.append(f"Structure near the {'call-gamma' if side == 'CE' else 'put-gamma'} strike ₹{wall:,.0f}. The strike is not a floor or a ceiling.")
        reasons.append(f"Convex risk profile ({idea.risk_reward_ratio}) riding institutional order flow")

    else:
        reasons.append(f"Aligned with {idea.play_type}")

    # 2. Directional Bias Score with exact numeric reading
    if side == "CE":
        if bias_score >= 4.0:
            reasons.append(f"Aggressive Bullish Bias ({bias_score:+.1f}/5.0)")
        elif bias_score >= 1.5:
            reasons.append(f"Directional Bias ({bias_score:+.1f}/5.0) confirming upside")
        elif bias_score <= -1.5:
            reasons.append(f"Counter-trend headwind (Bias {bias_score:+.1f}/5.0)")
        else:
            reasons.append(f"Neutral Bias ({bias_score:+.1f}/5.0)")
    else:
        if bias_score <= -4.0:
            reasons.append(f"Aggressive Bearish Bias ({bias_score:+.1f}/5.0)")
        elif bias_score <= -1.5:
            reasons.append(f"Directional Bias ({bias_score:+.1f}/5.0) confirming downside")
        elif bias_score >= 1.5:
            reasons.append(f"Counter-trend headwind (Bias {bias_score:+.1f}/5.0)")
        else:
            reasons.append(f"Neutral Bias ({bias_score:+.1f}/5.0)")

    # 3. Gamma Regime relative to Flip
    if flip > 0:
        side_word = "above" if spot >= flip else "below"
        reasons.append(
            f"Spot is {abs(spot - flip):,.0f} pts {side_word} the call−put balance. That is book balance, not a fade or a chase."
        )

    # 4. Regime Confluence Context
    adx_reg = getattr(idea, "adx_regime", "NEUTRAL")
    adx_v = getattr(idea, "adx_5m", 20.0)
    if adx_reg == "CHOP":
        reasons.append(f"Range-bound regime (5m ADX {adx_v:.0f} < 20)")
    elif adx_reg == "TRENDING":
        reasons.append(f"Strong trend regime (5m ADX {adx_v:.0f} > 25)")

    cvd_div = getattr(idea, "cvd_divergence", "NONE")
    if cvd_div == "BULLISH":
        reasons.append("Bullish CVD divergence (buyer delta absorption)")
    elif cvd_div == "BEARISH":
        reasons.append("Bearish CVD divergence (seller delta absorption)")

    return " · ".join(reasons)


def _calc_horizon_sigma(spot: float, eff_iv: float, horizon_minutes: float, step: int) -> float:
    """Computes intraday expected move sigma_h = max(step * 0.5, spot * eff_iv * sqrt(h / 94500)).

    Calibrated to Indian market trading hours (375 intraday minutes per day, 252 trading days/year = 94,500 mins).
    """
    ann_factor = 1.0 / math.sqrt(252.0 * 375.0)
    raw_sigma = spot * eff_iv * ann_factor * math.sqrt(horizon_minutes)
    return max(step * 0.5, raw_sigma)


def _apply_ev_quality_gate(
    score: int,
    ev: float,
    pt1: float,
) -> tuple[int, str, str]:
    """Enforces Net EV quality gating:
    - If EV <= 0.0: negative edge setup. Clamp score to max 74 (Grade B+ max), preventing Grade A/A+ promotion.
    - If EV >= 5.0 and pt1 >= 0.45 and initial score >= 75: strong statistical edge. Award +4 conviction bonus (capped at 98).
    Returns (adjusted_score, conviction_label, conviction_color).
    """
    adj_score = score
    if ev <= 0.0:
        adj_score = min(score, 74)
    elif ev >= 5.0 and pt1 >= 0.45 and score >= 75:
        adj_score = min(98, score + 4)

    if adj_score >= 80:
        label = "HIGH CONVICTION (Grade A+)"
        color = "var(--go)"
    elif adj_score >= 75:
        label = "HIGH CONVICTION (Grade A)"
        color = "var(--go)"
    elif adj_score >= 65:
        label = "TACTICAL OPPORTUNITY (Grade B+)"
        color = "var(--spark)"
    elif adj_score >= 50:
        label = "COUNTER-TREND / SPECULATIVE (Grade C)"
        color = "#f59e0b"
    else:
        label = "LOW PROBABILITY / AVOID"
        color = "var(--stop)"

    return adj_score, label, color


def _evaluate_ce_strategies(
    symbol: str,
    step: int,
    spot: float,
    chain_spot: float,
    call_wall: float,
    put_wall: float,
    flip: float,
    session_vwap: float,
    vwap_upper: float,
    vwap_lower: float,
    bias_score: float,
    sentinel_signals: Sequence[Any] | None,
    ce_atm_k: float,
    ce_struct_k: float,
    ce_squeeze_k: float,
    ce_vanna_k: float,
    oi_strikes: Any,
    contracts_df: Any,
    oi_map: dict[float, dict],
    contracts_map: dict[tuple[str, float], tuple[float, float, float, float]],
    expiry_tag: str = "",
    dte_days: float = 3.0,
    atm_iv: float = 0.14,
    scenario_set: Any | None = None,
    ema_trend: str = "NEUTRAL",
    eff_atr: float = 35.0,
    adx_regime: str = "NEUTRAL",
    adx_val: float = 20.0,
    cvd_divergence: str = "NONE",
    is_lunch_chop: bool = False,
    sector_breadth: str = "NEUTRAL",
    fii_macro_bias: str = "NEUTRAL",
    pre_market_regime: str = "",
    gap_fill_status: str = "",
) -> list[TradeIdea]:
    """Evaluates 7 institutional Call candidate strategies in parallel."""
    # =======================================================================
    # CALL CANDIDATE STRATEGIES (7 Plays)
    # =======================================================================
    ce_recs: list[TradeIdea] = []
    eff_iv = max(0.08, min(0.60, atm_iv if atm_iv > 0 else 0.14))
    trend_tag = f" · 5m Trend: {ema_trend.title()} (EMA 9 vs 21)" if ema_trend != "NEUTRAL" else ""

    def _score(play_type: str, leg_doi: float) -> tuple[int, str, str]:
        return _compute_trade_score(
            side="CE",
            play_type=play_type,
            bias_score=bias_score,
            spot=spot,
            vwap=session_vwap,
            call_wall=call_wall,
            put_wall=put_wall,
            sentinel_signals=sentinel_signals,
            leg_doi=leg_doi,
            vwap_upper=vwap_upper,
            vwap_lower=vwap_lower,
            flip=flip,
            scenario_set=scenario_set,
            ema_trend=ema_trend,
            atr=eff_atr,
            adx_regime=adx_regime,
            adx_val=adx_val,
            cvd_divergence=cvd_divergence,
            is_lunch_chop=is_lunch_chop,
            sector_breadth=sector_breadth,
            fii_macro_bias=fii_macro_bias,
            atm_iv=atm_iv,
            pre_market_regime=pre_market_regime,
            gap_fill_status=gap_fill_status,
        )

    # Common chain quote lookups for Calls
    ce_atm_ltp, ce_atm_delta, ce_atm_oi, ce_atm_doi, ce_atm_ws = _lookup_chain_data(
        ce_atm_k, True, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    ce_struct_ltp, ce_struct_delta, ce_struct_oi, ce_struct_doi, ce_struct_ws = _lookup_chain_data(
        ce_struct_k, True, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    ce_sq_ltp, ce_sq_delta, ce_sq_oi, ce_sq_doi, ce_sq_ws = _lookup_chain_data(
        ce_squeeze_k, True, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    ce_vc_ltp, ce_vc_delta, ce_vc_oi, ce_vc_doi, ce_vc_ws = _lookup_chain_data(
        ce_vanna_k, True, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )

    # 1. CE MOMENTUM (h = 45 min)
    sigma_45 = _calc_horizon_sigma(spot, eff_iv, 45.0, step)
    ce_atm_score, _, _ = _score("ATM Momentum", ce_atm_doi)
    ce_atm_inval_spot = round(min(session_vwap - (eff_atr * 0.9), spot - max(eff_atr * 1.3, step * 0.8)), 0)
    ce_atm_tgt1_spot = round(max(vwap_upper, spot + sigma_45), 0)
    ce_atm_tgt2_spot = round(max(call_wall, ce_atm_tgt1_spot + sigma_45), 0)
    ce_atm_sl, ce_atm_tgt1, ce_atm_tgt2, ce_atm_pt1, ce_atm_psl, ce_atm_ev, ce_atm_rr, ce_atm_attr = _reprice_trade_idea_targets(
        side="CE", strike=ce_atm_k, current_ltp=ce_atm_ltp, spot=spot,
        inval_spot=ce_atm_inval_spot, tgt1_spot=ce_atm_tgt1_spot, tgt2_spot=ce_atm_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=45, step=step,
    )
    ce_atm_score, ce_atm_label, ce_atm_color = _apply_ev_quality_gate(ce_atm_score, ce_atm_ev, ce_atm_pt1)
    ce_recs.append(
        TradeIdea(
            symbol=symbol,
            side="CE",
            play_type="ATM Momentum Play",
            play_badge="MOMENTUM",
            strike=ce_atm_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, ce_atm_k, "CE"),
            current_ltp=ce_atm_ltp,
            delta_approx=ce_atm_delta if ce_atm_delta > 0 else 0.50,
            entry_trigger=f"Enter on intraday retest & bounce of Session VWAP (₹{session_vwap:,.0f}) or 5m close above Opening Range High.",
            spot_invalidation=ce_atm_inval_spot,
            spot_invalidation_text=f"Spot prints below ₹{ce_atm_inval_spot:,.0f} (through the VWAP level)",
            target_1=ce_atm_tgt1,
            target_1_spot=ce_atm_tgt1_spot,
            target_2=ce_atm_tgt2,
            target_2_spot=ce_atm_tgt2_spot,
            stop_loss=ce_atm_sl,
            risk_reward_ratio=ce_atm_rr,
            quality_score=ce_atm_score,
            conviction_label=ce_atm_label,
            conviction_color=ce_atm_color,
            rationale=(
                f"Capitalizes on pure delta momentum with {symbol} holding equilibrium. "
                f"Delta ~{ce_atm_delta:.2f}. The call-gamma strike is {call_wall:,.0f}. That is a concentration on the path, not a ceiling.{trend_tag}"
            ),
            catalyst_tags=["Delta ~0.50", "VWAP Pullback", "High Liquidity"],
            is_ws_live=ce_atm_ws,
            p_touch_target_1=ce_atm_pt1,
            p_touch_sl=ce_atm_psl,
            net_ev_pts=ce_atm_ev,
            greek_attribution=ce_atm_attr,
        )
    )

    # 2. CE STRUCTURE (h = 75 min)
    sigma_75 = _calc_horizon_sigma(spot, eff_iv, 75.0, step)
    ce_struct_score, _, _ = _score("Institutional Structure", ce_struct_doi)
    ce_struct_inval_spot = round(min(put_wall - max(eff_atr * 0.6, step * 0.5), spot - max(sigma_75 * 0.8, eff_atr * 1.4)), 0)
    ce_struct_tgt1_spot = round(max(spot + (step * 0.5), flip if flip > spot else spot + sigma_75), 0)
    ce_struct_tgt2_spot = round(max(call_wall, ce_struct_tgt1_spot + sigma_75), 0)
    ce_struct_sl, ce_struct_tgt1, ce_struct_tgt2, ce_struct_pt1, ce_struct_psl, ce_struct_ev, ce_struct_rr, ce_struct_attr = _reprice_trade_idea_targets(
        side="CE", strike=ce_struct_k, current_ltp=ce_struct_ltp, spot=spot,
        inval_spot=ce_struct_inval_spot, tgt1_spot=ce_struct_tgt1_spot, tgt2_spot=ce_struct_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=75, step=step,
    )
    ce_struct_score, ce_struct_label, ce_struct_color = _apply_ev_quality_gate(ce_struct_score, ce_struct_ev, ce_struct_pt1)
    ce_recs.append(
        TradeIdea(
            symbol=symbol,
            side="CE",
            play_type="Institutional Structure Play",
            play_badge="STRUCTURE",
            strike=ce_struct_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, ce_struct_k, "CE"),
            current_ltp=ce_struct_ltp,
            delta_approx=ce_struct_delta if ce_struct_delta > 0 else 0.40,
            entry_trigger=f"Tape absorption near the put-gamma strike (₹{put_wall:,.0f}), or a print through the call−put balance (₹{flip:,.0f}). The strike is not a floor.",
            spot_invalidation=ce_struct_inval_spot,
            spot_invalidation_text=f"Spot prints below ₹{ce_struct_inval_spot:,.0f}",
            target_1=ce_struct_tgt1,
            target_1_spot=ce_struct_tgt1_spot,
            target_2=ce_struct_tgt2,
            target_2_spot=ce_struct_tgt2_spot,
            stop_loss=ce_struct_sl,
            risk_reward_ratio=ce_struct_rr,
            quality_score=ce_struct_score,
            conviction_label=ce_struct_label,
            conviction_color=ce_struct_color,
            rationale=(
                f"Spot is near the put-gamma strike ({put_wall:,.0f}). "
                f"That location is not writer defense and not a floor.{trend_tag}"
            ),
            catalyst_tags=["Put-gamma strike", "Tape", "Defined risk"],
            is_ws_live=ce_struct_ws,
            p_touch_target_1=ce_struct_pt1,
            p_touch_sl=ce_struct_psl,
            net_ev_pts=ce_struct_ev,
            greek_attribution=ce_struct_attr,
        )
    )

    # 3. CE STRIKE BREAK (h = 30 min)
    sigma_30 = _calc_horizon_sigma(spot, eff_iv, 30.0, step)
    ce_sq_score, _, _ = _score("Strike break", ce_sq_doi)
    eff_cw = call_wall if (spot < call_wall <= spot + 6 * step) else (spot + 2 * step)
    ce_sq_inval_spot = round(min(eff_cw - max(eff_atr * 0.6, step * 0.5), spot - max(sigma_30 * 0.8, eff_atr * 1.1)), 0)
    ce_sq_tgt1_spot = round(max(eff_cw + sigma_30, spot + max(sigma_30, step * 1.5)), 0)
    ce_sq_tgt2_spot = round(ce_sq_tgt1_spot + sigma_30, 0)
    ce_sq_sl, ce_sq_tgt1, ce_sq_tgt2, ce_sq_pt1, ce_sq_psl, ce_sq_ev, ce_sq_rr, ce_sq_attr = _reprice_trade_idea_targets(
        side="CE", strike=ce_squeeze_k, current_ltp=ce_sq_ltp, spot=spot,
        inval_spot=ce_sq_inval_spot, tgt1_spot=ce_sq_tgt1_spot, tgt2_spot=ce_sq_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=30, step=step,
    )
    ce_sq_score, ce_sq_label, ce_sq_color = _apply_ev_quality_gate(ce_sq_score, ce_sq_ev, ce_sq_pt1)
    ce_recs.append(
        TradeIdea(
            symbol=symbol,
            side="CE",
            play_type="Strike break",
            play_badge="STRIKE BREAK",
            strike=ce_squeeze_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, ce_squeeze_k, "CE"),
            current_ltp=ce_sq_ltp,
            delta_approx=ce_sq_delta if ce_sq_delta > 0 else 0.28,
            entry_trigger=f"Buyers print through the call-gamma strike (₹{call_wall:,.0f}) with positive CVD. A print through the strike is a location, not a squeeze.",
            spot_invalidation=ce_sq_inval_spot,
            spot_invalidation_text=f"Failed breakout: Spot drops back below ₹{ce_sq_inval_spot:,.0f}",
            target_1=ce_sq_tgt1,
            target_1_spot=ce_sq_tgt1_spot,
            target_2=ce_sq_tgt2,
            target_2_spot=ce_sq_tgt2_spot,
            stop_loss=ce_sq_sl,
            risk_reward_ratio=ce_sq_rr,
            quality_score=ce_sq_score,
            conviction_label=ce_sq_label,
            conviction_color=ce_sq_color,
            rationale=(
                f"Convex idea only if price holds beyond the call-gamma strike ({call_wall:,.0f}). "
                f"A breach is a location on the book, not a measured dealer hedge.{trend_tag}"
            ),
            catalyst_tags=["Call-gamma strike", "Convex idea", "User confirms"],
            is_ws_live=ce_sq_ws,
            p_touch_target_1=ce_sq_pt1,
            p_touch_sl=ce_sq_psl,
            net_ev_pts=ce_sq_ev,
            greek_attribution=ce_sq_attr,
        )
    )

    # 4. CE ICEBERG ABSORPTION FADE (h = 30 min)
    ce_ice_score, _, _ = _score("OrderFlow Iceberg Absorption Fade", ce_atm_doi)
    ce_ice_inval_spot = round(min(vwap_lower - max(eff_atr * 0.6, step * 0.5), spot - max(eff_atr * 0.9, step * 0.6)), 0)
    ce_ice_tgt1_spot = round(max(session_vwap, spot + (sigma_30 * 0.75)), 0)
    ce_ice_tgt2_spot = round(max(vwap_upper, ce_ice_tgt1_spot + (sigma_30 * 0.75)), 0)
    ce_ice_sl, ce_ice_tgt1, ce_ice_tgt2, ce_ice_pt1, ce_ice_psl, ce_ice_ev, ce_ice_rr, ce_ice_attr = _reprice_trade_idea_targets(
        side="CE", strike=ce_atm_k, current_ltp=ce_atm_ltp, spot=spot,
        inval_spot=ce_ice_inval_spot, tgt1_spot=ce_ice_tgt1_spot, tgt2_spot=ce_ice_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=30, step=step,
    )
    ce_ice_score, ce_ice_label, ce_ice_color = _apply_ev_quality_gate(ce_ice_score, ce_ice_ev, ce_ice_pt1)
    ce_recs.append(
        TradeIdea(
            symbol=symbol,
            side="CE",
            play_type="OrderFlow Iceberg Absorption Fade",
            play_badge="ICEBERG FADE",
            strike=ce_atm_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, ce_atm_k, "CE"),
            current_ltp=ce_atm_ltp,
            delta_approx=ce_atm_delta if ce_atm_delta > 0 else 0.50,
            entry_trigger=f"Tape buyer absorption confirmed at VWAP lower band (₹{vwap_lower:,.0f}) with positive CVD divergence.",
            spot_invalidation=ce_ice_inval_spot,
            spot_invalidation_text=f"Spot breakdown below ₹{ce_ice_inval_spot:,.0f} (OrderFlow absorption risk buffer)",
            target_1=ce_ice_tgt1,
            target_1_spot=ce_ice_tgt1_spot,
            target_2=ce_ice_tgt2,
            target_2_spot=ce_ice_tgt2_spot,
            stop_loss=ce_ice_sl,
            risk_reward_ratio=ce_ice_rr,
            quality_score=ce_ice_score,
            conviction_label=ce_ice_label,
            conviction_color=ce_ice_color,
            rationale=(
                f"The tape absorbed selling at {vwap_lower:,.0f}. Session VWAP is {session_vwap:,.0f}. "
                f"That is order flow, not a gamma floor.{trend_tag}"
            ),
            catalyst_tags=["Iceberg Absorption", "Tape Divergence", "Ultra-Tight Stop"],
            is_ws_live=ce_atm_ws,
            p_touch_target_1=ce_ice_pt1,
            p_touch_sl=ce_ice_psl,
            net_ev_pts=ce_ice_ev,
            greek_attribution=ce_ice_attr,
        )
    )

    # 5. CE 0DTE VANNA-CHARM THETA COMPRESSION (h = 90 min)
    sigma_90 = _calc_horizon_sigma(spot, eff_iv, 90.0, step)
    ce_vc_score, _, _ = _score("0DTE Vanna-Charm Theta Compression", ce_vc_doi)
    ce_vc_inval_spot = round(spot - max(sigma_90 * 0.6, eff_atr * 1.4), 0)
    ce_vc_tgt1_spot = round(max(spot + (step * 0.5), flip if flip > spot else spot + (sigma_90 * 0.6)), 0)
    ce_vc_tgt2_spot = round(max(call_wall, ce_vc_tgt1_spot + (sigma_90 * 0.8)), 0)
    ce_vc_sl, ce_vc_tgt1, ce_vc_tgt2, ce_vc_pt1, ce_vc_psl, ce_vc_ev, ce_vc_rr, ce_vc_attr = _reprice_trade_idea_targets(
        side="CE", strike=ce_vanna_k, current_ltp=ce_vc_ltp, spot=spot,
        inval_spot=ce_vc_inval_spot, tgt1_spot=ce_vc_tgt1_spot, tgt2_spot=ce_vc_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=90, step=step,
    )
    ce_vc_score, ce_vc_label, ce_vc_color = _apply_ev_quality_gate(ce_vc_score, ce_vc_ev, ce_vc_pt1)
    ce_recs.append(
        TradeIdea(
            symbol=symbol,
            side="CE",
            play_type="0DTE Vanna-Charm Theta Compression",
            play_badge="VANNA-CHARM",
            strike=ce_vanna_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, ce_vanna_k, "CE"),
            current_ltp=ce_vc_ltp,
            delta_approx=ce_vc_delta if ce_vc_delta > 0 else 0.20,
            entry_trigger=f"Spot between the gamma strikes (₹{put_wall:,.0f}–₹{call_wall:,.0f}), balance at ₹{flip:,.0f}. Charm decay is not a pin.",
            spot_invalidation=ce_vc_inval_spot,
            spot_invalidation_text=f"Spot volatility breakdown below ₹{ce_vc_inval_spot:,.0f}",
            target_1=ce_vc_tgt1,
            target_1_spot=ce_vc_tgt1_spot,
            target_2=ce_vc_tgt2,
            target_2_spot=ce_vc_tgt2_spot,
            stop_loss=ce_vc_sl,
            risk_reward_ratio=ce_vc_rr,
            quality_score=ce_vc_score,
            conviction_label=ce_vc_label,
            conviction_color=ce_vc_color,
            rationale=(
                f"Charm decay while spot sits between the gamma strikes. "
                f"The corridor is a map, not a dealer pin.{trend_tag}"
            ),
            catalyst_tags=["0DTE Charm Decay", "Between gamma strikes"],
            is_ws_live=ce_vc_ws,
            p_touch_target_1=ce_vc_pt1,
            p_touch_sl=ce_vc_psl,
            net_ev_pts=ce_vc_ev,
            greek_attribution=ce_vc_attr,
        )
    )

    # 6. CE VOLATILITY DISPERSION / LONG GAMMA (h = 60 min)
    sigma_60 = _calc_horizon_sigma(spot, eff_iv, 60.0, step)
    ce_vd_score, _, _ = _score("Volatility Dispersion / Long Gamma", ce_atm_doi)
    ce_vd_inval_spot = round(min(session_vwap - max(eff_atr * 0.8, step * 0.6), spot - max(sigma_60 * 0.8, eff_atr * 1.3)), 0)
    ce_vd_tgt1_spot = round(max(call_wall, spot + (sigma_60 * 1.2)), 0)
    ce_vd_tgt2_spot = round(max(ce_vd_tgt1_spot + step, spot + (sigma_60 * 2.2)), 0)
    ce_vd_sl, ce_vd_tgt1, ce_vd_tgt2, ce_vd_pt1, ce_vd_psl, ce_vd_ev, ce_vd_rr, ce_vd_attr = _reprice_trade_idea_targets(
        side="CE", strike=ce_atm_k, current_ltp=ce_atm_ltp, spot=spot,
        inval_spot=ce_vd_inval_spot, tgt1_spot=ce_vd_tgt1_spot, tgt2_spot=ce_vd_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=60, step=step,
    )
    ce_vd_score, ce_vd_label, ce_vd_color = _apply_ev_quality_gate(ce_vd_score, ce_vd_ev, ce_vd_pt1)
    ce_recs.append(
        TradeIdea(
            symbol=symbol,
            side="CE",
            play_type="Volatility Dispersion / Long Gamma",
            play_badge="VOL DISPERSION",
            strike=ce_atm_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, ce_atm_k, "CE"),
            current_ltp=ce_atm_ltp,
            delta_approx=ce_atm_delta if ce_atm_delta > 0 else 0.50,
            entry_trigger=f"Volatility compression breakout above ₹{vwap_upper:,.0f} with surging market volume and expanding ATR.",
            spot_invalidation=ce_vd_inval_spot,
            spot_invalidation_text=f"Spot falls back into consolidation band below ₹{ce_vd_inval_spot:,.0f}",
            target_1=ce_vd_tgt1,
            target_1_spot=ce_vd_tgt1_spot,
            target_2=ce_vd_tgt2,
            target_2_spot=ce_vd_tgt2_spot,
            stop_loss=ce_vd_sl,
            risk_reward_ratio=ce_vd_rr,
            quality_score=ce_vd_score,
            conviction_label=ce_vd_label,
            conviction_color=ce_vd_color,
            rationale=(
                f"Pre-breakout long gamma positioning. Low implied volatility provides cheap option convexity; "
                f"sudden volatility expansion creates non-linear premium surges.{trend_tag}"
            ),
            catalyst_tags=["Long Gamma", "IV Expansion", "Convex Volatility"],
            is_ws_live=ce_atm_ws,
            p_touch_target_1=ce_vd_pt1,
            p_touch_sl=ce_vd_psl,
            net_ev_pts=ce_vd_ev,
            greek_attribution=ce_vd_attr,
        )
    )

    # 7. CE ZERO-COST RATIO RISK REVERSAL (h = 90 min)
    ce_rr_score, _, _ = _score("Zero-Cost Ratio Risk Reversal", ce_struct_doi)
    ce_rr_inval_spot = round(min(session_vwap - max(eff_atr * 0.8, step * 0.6), spot - max(sigma_90 * 0.8, eff_atr * 1.3)), 0)
    ce_rr_tgt1_spot = round(max(call_wall, spot + sigma_90), 0)
    ce_rr_tgt2_spot = round(max(ce_rr_tgt1_spot + step, spot + (sigma_90 * 1.8)), 0)
    ce_rr_sl, ce_rr_tgt1, ce_rr_tgt2, ce_rr_pt1, ce_rr_psl, ce_rr_ev, ce_rr_rr, ce_rr_attr = _reprice_trade_idea_targets(
        side="CE", strike=ce_struct_k, current_ltp=ce_struct_ltp, spot=spot,
        inval_spot=ce_rr_inval_spot, tgt1_spot=ce_rr_tgt1_spot, tgt2_spot=ce_rr_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=90, step=step,
    )
    ce_rr_score, ce_rr_label, ce_rr_color = _apply_ev_quality_gate(ce_rr_score, ce_rr_ev, ce_rr_pt1)
    ce_recs.append(
        TradeIdea(
            symbol=symbol,
            side="CE",
            play_type="Zero-Cost Ratio Risk Reversal",
            play_badge="RATIO REVERSAL",
            strike=ce_struct_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, ce_struct_k, "CE"),
            current_ltp=ce_struct_ltp,
            delta_approx=ce_struct_delta if ce_struct_delta > 0 else 0.40,
            entry_trigger=f"Upward drift with the call-gamma strike at ₹{call_wall:,.0f}. The strike is a concentration, not a magnet.",
            spot_invalidation=ce_rr_inval_spot,
            spot_invalidation_text=f"Spot invalidation below VWAP @ ₹{ce_rr_inval_spot:,.0f}",
            target_1=ce_rr_tgt1,
            target_1_spot=ce_rr_tgt1_spot,
            target_2=ce_rr_tgt2,
            target_2_spot=ce_rr_tgt2_spot,
            stop_loss=ce_rr_sl,
            risk_reward_ratio=ce_rr_rr,
            quality_score=ce_rr_score,
            conviction_label=ce_rr_label,
            conviction_color=ce_rr_color,
            rationale=(
                f"Ratio structure with the call-gamma strike at {call_wall:,.0f}. The strike is not a destination. "
                f"Eliminates sideways theta drag while capturing directional upside.{trend_tag}"
            ),
            catalyst_tags=["Ratio asymmetry", "Call-gamma strike", "Defined risk"],
            is_ws_live=ce_struct_ws,
            p_touch_target_1=ce_rr_pt1,
            p_touch_sl=ce_rr_psl,
            net_ev_pts=ce_rr_ev,
            greek_attribution=ce_rr_attr,
        )
    )

    for idea in ce_recs:
        _enrich_trade_idea(
            idea,
            contracts_map=contracts_map,
            step=step,
            eff_iv=eff_iv,
            adx_5m=adx_val,
            adx_regime=adx_regime,
            cvd_divergence=cvd_divergence,
            sector_breadth=sector_breadth,
            fii_macro_bias=fii_macro_bias,
            is_lunch_chop=is_lunch_chop,
        )

    return ce_recs


def _evaluate_pe_strategies(
    symbol: str,
    step: int,
    spot: float,
    chain_spot: float,
    call_wall: float,
    put_wall: float,
    flip: float,
    session_vwap: float,
    vwap_upper: float,
    vwap_lower: float,
    bias_score: float,
    sentinel_signals: Sequence[Any] | None,
    pe_atm_k: float,
    pe_struct_k: float,
    pe_squeeze_k: float,
    pe_vanna_k: float,
    oi_strikes: Any,
    contracts_df: Any,
    oi_map: dict[float, dict],
    contracts_map: dict[tuple[str, float], tuple[float, float, float, float]],
    expiry_tag: str = "",
    dte_days: float = 3.0,
    atm_iv: float = 0.14,
    scenario_set: Any | None = None,
    ema_trend: str = "NEUTRAL",
    eff_atr: float = 35.0,
    adx_regime: str = "NEUTRAL",
    adx_val: float = 20.0,
    cvd_divergence: str = "NONE",
    is_lunch_chop: bool = False,
    sector_breadth: str = "NEUTRAL",
    fii_macro_bias: str = "NEUTRAL",
    pre_market_regime: str = "",
    gap_fill_status: str = "",
) -> list[TradeIdea]:
    """Evaluates 7 institutional Put candidate strategies in parallel."""
    # =======================================================================
    # PUT CANDIDATE STRATEGIES (7 Plays)
    # =======================================================================
    pe_recs: list[TradeIdea] = []
    eff_iv = max(0.08, min(0.60, atm_iv if atm_iv > 0 else 0.14))
    trend_tag = f" · 5m Trend: {ema_trend.title()} (EMA 9 vs 21)" if ema_trend != "NEUTRAL" else ""

    def _score(play_type: str, leg_doi: float) -> tuple[int, str, str]:
        return _compute_trade_score(
            side="PE",
            play_type=play_type,
            bias_score=bias_score,
            spot=spot,
            vwap=session_vwap,
            call_wall=call_wall,
            put_wall=put_wall,
            sentinel_signals=sentinel_signals,
            leg_doi=leg_doi,
            vwap_upper=vwap_upper,
            vwap_lower=vwap_lower,
            flip=flip,
            scenario_set=scenario_set,
            ema_trend=ema_trend,
            atr=eff_atr,
            adx_regime=adx_regime,
            adx_val=adx_val,
            cvd_divergence=cvd_divergence,
            is_lunch_chop=is_lunch_chop,
            sector_breadth=sector_breadth,
            fii_macro_bias=fii_macro_bias,
            atm_iv=atm_iv,
            pre_market_regime=pre_market_regime,
            gap_fill_status=gap_fill_status,
        )

    # Common chain quote lookups for Puts
    pe_atm_ltp, pe_atm_delta, pe_atm_oi, pe_atm_doi, pe_atm_ws = _lookup_chain_data(
        pe_atm_k, False, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    pe_struct_ltp, pe_struct_delta, pe_struct_oi, pe_struct_doi, pe_struct_ws = _lookup_chain_data(
        pe_struct_k, False, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    pe_sq_ltp, pe_sq_delta, pe_sq_oi, pe_sq_doi, pe_sq_ws = _lookup_chain_data(
        pe_squeeze_k, False, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    pe_vc_ltp, pe_vc_delta, pe_vc_oi, pe_vc_doi, pe_vc_ws = _lookup_chain_data(
        pe_vanna_k, False, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )

    # 1. PE MOMENTUM (h = 45 min)
    sigma_45 = _calc_horizon_sigma(spot, eff_iv, 45.0, step)
    pe_atm_score, _, _ = _score("ATM Momentum", pe_atm_doi)
    pe_atm_inval_spot = round(max(session_vwap + (eff_atr * 0.9), spot + max(eff_atr * 1.3, step * 0.8)), 0)
    pe_atm_tgt1_spot = round(min(vwap_lower, spot - sigma_45), 0)
    pe_atm_tgt2_spot = round(min(put_wall, pe_atm_tgt1_spot - sigma_45), 0)
    pe_atm_sl, pe_atm_tgt1, pe_atm_tgt2, pe_atm_pt1, pe_atm_psl, pe_atm_ev, pe_atm_rr, pe_atm_attr = _reprice_trade_idea_targets(
        side="PE", strike=pe_atm_k, current_ltp=pe_atm_ltp, spot=spot,
        inval_spot=pe_atm_inval_spot, tgt1_spot=pe_atm_tgt1_spot, tgt2_spot=pe_atm_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=45, step=step,
    )
    pe_atm_score, pe_atm_label, pe_atm_color = _apply_ev_quality_gate(pe_atm_score, pe_atm_ev, pe_atm_pt1)
    pe_recs.append(
        TradeIdea(
            symbol=symbol,
            side="PE",
            play_type="ATM Momentum Play",
            play_badge="MOMENTUM",
            strike=pe_atm_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, pe_atm_k, "PE"),
            current_ltp=pe_atm_ltp,
            delta_approx=pe_atm_delta if pe_atm_delta < 0 else -0.50,
            entry_trigger=f"Rejection at Session VWAP (₹{session_vwap:,.0f}) or 5m close below Opening Range Low with negative CVD.",
            spot_invalidation=pe_atm_inval_spot,
            spot_invalidation_text=f"Spot prints above ₹{pe_atm_inval_spot:,.0f} (through the VWAP level)",
            target_1=pe_atm_tgt1,
            target_1_spot=pe_atm_tgt1_spot,
            target_2=pe_atm_tgt2,
            target_2_spot=pe_atm_tgt2_spot,
            stop_loss=pe_atm_sl,
            risk_reward_ratio=pe_atm_rr,
            quality_score=pe_atm_score,
            conviction_label=pe_atm_label,
            conviction_color=pe_atm_color,
            rationale=(
                f"Downside acceleration vehicle when spot trades below Session Equilibrium. "
                f"Delta ~{pe_atm_delta:.2f}. The put-gamma strike is {put_wall:,.0f}. That is a concentration on the path, not a floor.{trend_tag}"
            ),
            catalyst_tags=["Delta ~-0.50", "VWAP Rejection", "High Liquidity"],
            is_ws_live=pe_atm_ws,
            p_touch_target_1=pe_atm_pt1,
            p_touch_sl=pe_atm_psl,
            net_ev_pts=pe_atm_ev,
            greek_attribution=pe_atm_attr,
        )
    )

    # 2. PE STRUCTURE (h = 75 min)
    sigma_75 = _calc_horizon_sigma(spot, eff_iv, 75.0, step)
    pe_struct_score, _, _ = _score("Institutional Structure", pe_struct_doi)
    pe_struct_inval_spot = round(max(call_wall + max(eff_atr * 0.6, step * 0.5), spot + max(sigma_75 * 0.8, eff_atr * 1.4)), 0)
    pe_struct_tgt1_spot = round(min(spot - (step * 0.5), flip if flip < spot else spot - sigma_75), 0)
    pe_struct_tgt2_spot = round(min(put_wall, pe_struct_tgt1_spot - sigma_75), 0)
    pe_struct_sl, pe_struct_tgt1, pe_struct_tgt2, pe_struct_pt1, pe_struct_psl, pe_struct_ev, pe_struct_rr, pe_struct_attr = _reprice_trade_idea_targets(
        side="PE", strike=pe_struct_k, current_ltp=pe_struct_ltp, spot=spot,
        inval_spot=pe_struct_inval_spot, tgt1_spot=pe_struct_tgt1_spot, tgt2_spot=pe_struct_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=75, step=step,
    )
    pe_struct_score, pe_struct_label, pe_struct_color = _apply_ev_quality_gate(pe_struct_score, pe_struct_ev, pe_struct_pt1)
    pe_recs.append(
        TradeIdea(
            symbol=symbol,
            side="PE",
            play_type="Institutional Structure Play",
            play_badge="STRUCTURE",
            strike=pe_struct_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, pe_struct_k, "PE"),
            current_ltp=pe_struct_ltp,
            delta_approx=pe_struct_delta if pe_struct_delta < 0 else -0.40,
            entry_trigger=f"Seller delta on the tape while spot is near the call-gamma strike (₹{call_wall:,.0f}). The strike is not a ceiling.",
            spot_invalidation=pe_struct_inval_spot,
            spot_invalidation_text=f"Print back through ₹{pe_struct_inval_spot:,.0f}",
            target_1=pe_struct_tgt1,
            target_1_spot=pe_struct_tgt1_spot,
            target_2=pe_struct_tgt2,
            target_2_spot=pe_struct_tgt2_spot,
            stop_loss=pe_struct_sl,
            risk_reward_ratio=pe_struct_rr,
            quality_score=pe_struct_score,
            conviction_label=pe_struct_label,
            conviction_color=pe_struct_color,
            rationale=(
                f"Spot is near the call-gamma strike ({call_wall:,.0f}). "
                f"That location is not a rejection and not a ceiling.{trend_tag}"
            ),
            catalyst_tags=["Call-gamma strike", "Tape", "Defined risk"],
            is_ws_live=pe_struct_ws,
            p_touch_target_1=pe_struct_pt1,
            p_touch_sl=pe_struct_psl,
            net_ev_pts=pe_struct_ev,
            greek_attribution=pe_struct_attr,
        )
    )

    # 3. PE STRIKE BREAK (h = 30 min)
    sigma_30 = _calc_horizon_sigma(spot, eff_iv, 30.0, step)
    pe_sq_score, _, _ = _score("Strike break", pe_sq_doi)
    eff_pw = put_wall if (spot - 6 * step <= put_wall < spot) else (spot - 2 * step)
    pe_sq_inval_spot = round(max(eff_pw + max(eff_atr * 0.6, step * 0.5), spot + max(sigma_30 * 0.8, eff_atr * 1.1)), 0)
    pe_sq_tgt1_spot = round(min(eff_pw - sigma_30, spot - max(sigma_30, step * 1.5)), 0)
    pe_sq_tgt2_spot = round(pe_sq_tgt1_spot - sigma_30, 0)
    pe_sq_sl, pe_sq_tgt1, pe_sq_tgt2, pe_sq_pt1, pe_sq_psl, pe_sq_ev, pe_sq_rr, pe_sq_attr = _reprice_trade_idea_targets(
        side="PE", strike=pe_squeeze_k, current_ltp=pe_sq_ltp, spot=spot,
        inval_spot=pe_sq_inval_spot, tgt1_spot=pe_sq_tgt1_spot, tgt2_spot=pe_sq_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=30, step=step,
    )
    pe_sq_score, pe_sq_label, pe_sq_color = _apply_ev_quality_gate(pe_sq_score, pe_sq_ev, pe_sq_pt1)
    pe_recs.append(
        TradeIdea(
            symbol=symbol,
            side="PE",
            play_type="Strike break",
            play_badge="STRIKE BREAK",
            strike=pe_squeeze_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, pe_squeeze_k, "PE"),
            current_ltp=pe_sq_ltp,
            delta_approx=pe_sq_delta if pe_sq_delta < 0 else -0.28,
            entry_trigger=f"High-volume print through the put-gamma strike (₹{put_wall:,.0f}). A break is a location, not a cascade.",
            spot_invalidation=pe_sq_inval_spot,
            spot_invalidation_text=f"Trapdoor reversal: Spot recovers back above ₹{pe_sq_inval_spot:,.0f}",
            target_1=pe_sq_tgt1,
            target_1_spot=pe_sq_tgt1_spot,
            target_2=pe_sq_tgt2,
            target_2_spot=pe_sq_tgt2_spot,
            stop_loss=pe_sq_sl,
            risk_reward_ratio=pe_sq_rr,
            quality_score=pe_sq_score,
            conviction_label=pe_sq_label,
            conviction_color=pe_sq_color,
            rationale=(
                f"Convex idea only if price holds beyond the put-gamma strike ({put_wall:,.0f}). "
                f"A break is a location on the book, not a measured dealer hedge.{trend_tag}"
            ),
            catalyst_tags=["Put-gamma strike", "Convex idea", "User confirms"],
            is_ws_live=pe_sq_ws,
            p_touch_target_1=pe_sq_pt1,
            p_touch_sl=pe_sq_psl,
            net_ev_pts=pe_sq_ev,
            greek_attribution=pe_sq_attr,
        )
    )

    # 4. PE ICEBERG ABSORPTION FADE (h = 30 min)
    pe_ice_score, _, _ = _score("OrderFlow Iceberg Absorption Fade", pe_atm_doi)
    pe_ice_inval_spot = round(max(vwap_upper + max(eff_atr * 0.6, step * 0.5), spot + max(eff_atr * 0.9, step * 0.6)), 0)
    pe_ice_tgt1_spot = round(min(session_vwap, spot - (sigma_30 * 0.75)), 0)
    pe_ice_tgt2_spot = round(min(vwap_lower, pe_ice_tgt1_spot - (sigma_30 * 0.75)), 0)
    pe_ice_sl, pe_ice_tgt1, pe_ice_tgt2, pe_ice_pt1, pe_ice_psl, pe_ice_ev, pe_ice_rr, pe_ice_attr = _reprice_trade_idea_targets(
        side="PE", strike=pe_atm_k, current_ltp=pe_atm_ltp, spot=spot,
        inval_spot=pe_ice_inval_spot, tgt1_spot=pe_ice_tgt1_spot, tgt2_spot=pe_ice_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=30, step=step,
    )
    pe_ice_score, pe_ice_label, pe_ice_color = _apply_ev_quality_gate(pe_ice_score, pe_ice_ev, pe_ice_pt1)
    pe_recs.append(
        TradeIdea(
            symbol=symbol,
            side="PE",
            play_type="OrderFlow Iceberg Absorption Fade",
            play_badge="ICEBERG FADE",
            strike=pe_atm_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, pe_atm_k, "PE"),
            current_ltp=pe_atm_ltp,
            delta_approx=pe_atm_delta if pe_atm_delta < 0 else -0.50,
            entry_trigger=f"Tape seller absorption confirmed at VWAP upper band (₹{vwap_upper:,.0f}) with negative CVD divergence.",
            spot_invalidation=pe_ice_inval_spot,
            spot_invalidation_text=f"Spot breakout above ₹{pe_ice_inval_spot:,.0f} (OrderFlow absorption risk buffer)",
            target_1=pe_ice_tgt1,
            target_1_spot=pe_ice_tgt1_spot,
            target_2=pe_ice_tgt2,
            target_2_spot=pe_ice_tgt2_spot,
            stop_loss=pe_ice_sl,
            risk_reward_ratio=pe_ice_rr,
            quality_score=pe_ice_score,
            conviction_label=pe_ice_label,
            conviction_color=pe_ice_color,
            rationale=(
                f"Passive limit sellers absorbing aggressive retail buying at {vwap_upper:,.0f}. "
                f"High R:R rotation back toward Session VWAP ({session_vwap:,.0f}).{trend_tag}"
            ),
            catalyst_tags=["Seller Absorption", "Tape Exhaustion", "Ultra-Tight Stop"],
            is_ws_live=pe_atm_ws,
            p_touch_target_1=pe_ice_pt1,
            p_touch_sl=pe_ice_psl,
            net_ev_pts=pe_ice_ev,
            greek_attribution=pe_ice_attr,
        )
    )

    # 5. PE 0DTE VANNA-CHARM THETA COMPRESSION (h = 90 min)
    sigma_90 = _calc_horizon_sigma(spot, eff_iv, 90.0, step)
    pe_vc_score, _, _ = _score("0DTE Vanna-Charm Theta Compression", pe_vc_doi)
    pe_vc_inval_spot = round(spot + max(sigma_90 * 0.6, eff_atr * 1.4), 0)
    pe_vc_tgt1_spot = round(min(spot - (step * 0.5), flip if flip < spot else spot - (sigma_90 * 0.6)), 0)
    pe_vc_tgt2_spot = round(min(put_wall, pe_vc_tgt1_spot - (sigma_90 * 0.8)), 0)
    pe_vc_sl, pe_vc_tgt1, pe_vc_tgt2, pe_vc_pt1, pe_vc_psl, pe_vc_ev, pe_vc_rr, pe_vc_attr = _reprice_trade_idea_targets(
        side="PE", strike=pe_vanna_k, current_ltp=pe_vc_ltp, spot=spot,
        inval_spot=pe_vc_inval_spot, tgt1_spot=pe_vc_tgt1_spot, tgt2_spot=pe_vc_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=90, step=step,
    )
    pe_vc_score, pe_vc_label, pe_vc_color = _apply_ev_quality_gate(pe_vc_score, pe_vc_ev, pe_vc_pt1)
    pe_recs.append(
        TradeIdea(
            symbol=symbol,
            side="PE",
            play_type="0DTE Vanna-Charm Theta Compression",
            play_badge="VANNA-CHARM",
            strike=pe_vanna_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, pe_vanna_k, "PE"),
            current_ltp=pe_vc_ltp,
            delta_approx=pe_vc_delta if pe_vc_delta < 0 else -0.20,
            entry_trigger=f"Spot between the gamma strikes (₹{put_wall:,.0f}–₹{call_wall:,.0f}), balance at ₹{flip:,.0f}. Charm decay is not a pin.",
            spot_invalidation=pe_vc_inval_spot,
            spot_invalidation_text=f"Spot volatility breakdown below ₹{pe_vc_inval_spot:,.0f}",
            target_1=pe_vc_tgt1,
            target_1_spot=pe_vc_tgt1_spot,
            target_2=pe_vc_tgt2,
            target_2_spot=pe_vc_tgt2_spot,
            stop_loss=pe_vc_sl,
            risk_reward_ratio=pe_vc_rr,
            quality_score=pe_vc_score,
            conviction_label=pe_vc_label,
            conviction_color=pe_vc_color,
            rationale=(
                f"Charm decay while spot sits between the gamma strikes. "
                f"The corridor is a map, not a dealer pin.{trend_tag}"
            ),
            catalyst_tags=["0DTE Charm Decay", "Between gamma strikes"],
            is_ws_live=pe_vc_ws,
            p_touch_target_1=pe_vc_pt1,
            p_touch_sl=pe_vc_psl,
            net_ev_pts=pe_vc_ev,
            greek_attribution=pe_vc_attr,
        )
    )

    # 6. PE VOLATILITY DISPERSION / LONG GAMMA (h = 60 min)
    sigma_60 = _calc_horizon_sigma(spot, eff_iv, 60.0, step)
    pe_vd_score, _, _ = _score("Volatility Dispersion / Long Gamma", pe_atm_doi)
    pe_vd_inval_spot = round(max(session_vwap + max(eff_atr * 0.8, step * 0.6), spot + max(sigma_60 * 0.8, eff_atr * 1.3)), 0)
    pe_vd_tgt1_spot = round(min(put_wall, spot - (sigma_60 * 1.2)), 0)
    pe_vd_tgt2_spot = round(min(pe_vd_tgt1_spot - step, spot - (sigma_60 * 2.2)), 0)
    pe_vd_sl, pe_vd_tgt1, pe_vd_tgt2, pe_vd_pt1, pe_vd_psl, pe_vd_ev, pe_vd_rr, pe_vd_attr = _reprice_trade_idea_targets(
        side="PE", strike=pe_atm_k, current_ltp=pe_atm_ltp, spot=spot,
        inval_spot=pe_vd_inval_spot, tgt1_spot=pe_vd_tgt1_spot, tgt2_spot=pe_vd_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=60, step=step,
    )
    pe_vd_score, pe_vd_label, pe_vd_color = _apply_ev_quality_gate(pe_vd_score, pe_vd_ev, pe_vd_pt1)
    pe_recs.append(
        TradeIdea(
            symbol=symbol,
            side="PE",
            play_type="Volatility Dispersion / Long Gamma",
            play_badge="VOL DISPERSION",
            strike=pe_atm_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, pe_atm_k, "PE"),
            current_ltp=pe_atm_ltp,
            delta_approx=pe_atm_delta if pe_atm_delta < 0 else -0.50,
            entry_trigger=f"Volatility compression breakdown below ₹{vwap_lower:,.0f} with surging volume and downside momentum.",
            spot_invalidation=pe_vd_inval_spot,
            spot_invalidation_text=f"Spot recovers back into consolidation band above ₹{pe_vd_inval_spot:,.0f}",
            target_1=pe_vd_tgt1,
            target_1_spot=pe_vd_tgt1_spot,
            target_2=pe_vd_tgt2,
            target_2_spot=pe_vd_tgt2_spot,
            stop_loss=pe_vd_sl,
            risk_reward_ratio=pe_vd_rr,
            quality_score=pe_vd_score,
            conviction_label=pe_vd_label,
            conviction_color=pe_vd_color,
            rationale=(
                f"Long gamma downside vehicle capitalizing on volatility regime shifts. In compressed markets, "
                f"sudden put buying triggers rapid implied vol spikes.{trend_tag}"
            ),
            catalyst_tags=["Long Gamma", "IV Expansion", "Convex Volatility"],
            is_ws_live=pe_atm_ws,
            p_touch_target_1=pe_vd_pt1,
            p_touch_sl=pe_vd_psl,
            net_ev_pts=pe_vd_ev,
            greek_attribution=pe_vd_attr,
        )
    )

    # 7. PE ZERO-COST RATIO RISK REVERSAL (h = 90 min)
    pe_rr_score, _, _ = _score("Zero-Cost Ratio Risk Reversal", pe_struct_doi)
    pe_rr_inval_spot = round(max(session_vwap + max(eff_atr * 0.8, step * 0.6), spot + max(sigma_90 * 0.8, eff_atr * 1.3)), 0)
    pe_rr_tgt1_spot = round(min(put_wall, spot - sigma_90), 0)
    pe_rr_tgt2_spot = round(min(pe_rr_tgt1_spot - step, spot - (sigma_90 * 1.8)), 0)
    pe_rr_sl, pe_rr_tgt1, pe_rr_tgt2, pe_rr_pt1, pe_rr_psl, pe_rr_ev, pe_rr_rr, pe_rr_attr = _reprice_trade_idea_targets(
        side="PE", strike=pe_struct_k, current_ltp=pe_struct_ltp, spot=spot,
        inval_spot=pe_rr_inval_spot, tgt1_spot=pe_rr_tgt1_spot, tgt2_spot=pe_rr_tgt2_spot,
        dte_days=dte_days, atm_iv=atm_iv, horizon_minutes=90, step=step,
    )
    pe_rr_score, pe_rr_label, pe_rr_color = _apply_ev_quality_gate(pe_rr_score, pe_rr_ev, pe_rr_pt1)
    pe_recs.append(
        TradeIdea(
            symbol=symbol,
            side="PE",
            play_type="Zero-Cost Ratio Risk Reversal",
            play_badge="RATIO REVERSAL",
            strike=pe_struct_k,
            option_symbol=_format_option_symbol(symbol, expiry_tag, pe_struct_k, "PE"),
            current_ltp=pe_struct_ltp,
            delta_approx=pe_struct_delta if pe_struct_delta < 0 else -0.40,
            entry_trigger=f"Downward drift with the put-gamma strike at ₹{put_wall:,.0f}. The strike is a concentration, not a magnet.",
            spot_invalidation=pe_rr_inval_spot,
            spot_invalidation_text=f"Spot invalidation above VWAP @ ₹{pe_rr_inval_spot:,.0f}",
            target_1=pe_rr_tgt1,
            target_1_spot=pe_rr_tgt1_spot,
            target_2=pe_rr_tgt2,
            target_2_spot=pe_rr_tgt2_spot,
            stop_loss=pe_rr_sl,
            risk_reward_ratio=pe_rr_rr,
            quality_score=pe_rr_score,
            conviction_label=pe_rr_label,
            conviction_color=pe_rr_color,
            rationale=(
                f"Ratio structure with the put-gamma strike at {put_wall:,.0f}. The strike is not a destination. "
                f"Delivers high risk-adjusted return without severe chop decay.{trend_tag}"
            ),
            catalyst_tags=["Ratio asymmetry", "Put-gamma strike", "Defined risk"],
            is_ws_live=pe_struct_ws,
            p_touch_target_1=pe_rr_pt1,
            p_touch_sl=pe_rr_psl,
            net_ev_pts=pe_rr_ev,
            greek_attribution=pe_rr_attr,
        )
    )

    for idea in pe_recs:
        _enrich_trade_idea(
            idea,
            contracts_map=contracts_map,
            step=step,
            eff_iv=eff_iv,
            adx_5m=adx_val,
            adx_regime=adx_regime,
            cvd_divergence=cvd_divergence,
            sector_breadth=sector_breadth,
            fii_macro_bias=fii_macro_bias,
            is_lunch_chop=is_lunch_chop,
        )

    return pe_recs


def _empty_recommendation_batch(symbol: str, reason: str) -> RecommendationBatch:
    """No ideas. Used when the chain spot is missing or the book is the demo fixture."""
    return RecommendationBatch(
        symbol=symbol,
        spot=0.0,
        session_vwap=0.0,
        call_wall=0.0,
        put_wall=0.0,
        gamma_flip=0.0,
        directional_bias_score=0.0,
        bias_regime="UNAVAILABLE",
        executive_summary=reason,
    )


def _best_tactical(candidates: Sequence[Any], floor: int = 65) -> Any | None:
    """Highest live read at or above the tactical floor. Invalidated ideas stay off the card."""
    alive = [
        t for t in candidates
        if getattr(t, "latch_state", "") != "INVALIDATED" and int(getattr(t, "quality_score", 0) or 0) >= floor
    ]
    if not alive:
        return None
    return max(alive, key=lambda t: (int(t.quality_score), float(getattr(t, "net_ev_pts", 0) or 0)))


def apply_tactical_watch(
    qualified_ce: list,
    qualified_pe: list,
    all_ce: Sequence[Any],
    all_pe: Sequence[Any],
    min_score: int,
) -> tuple[list, list, bool, bool]:
    """When nothing clears 75, keep the best read at or above 65 on the card.

    A side that already cleared the high-conviction line is left as it is.
    The 75 line stays the high-conviction bar. This only fills an empty card.
    """
    if min_score not in (75, 85) or qualified_ce or qualified_pe:
        return qualified_ce, qualified_pe, False, False
    best_ce = _best_tactical(all_ce)
    best_pe = _best_tactical(all_pe)
    ce_watch = best_ce is not None
    pe_watch = best_pe is not None
    return (
        [best_ce] if ce_watch else qualified_ce,
        [best_pe] if pe_watch else qualified_pe,
        ce_watch,
        pe_watch,
    )


def _rehydrate_bookmarked_trade_idea(r: dict, current_spot: float) -> TradeIdea:
    """Instantiate a full TradeIdea from an existing SQLite/cached recommendation record."""
    payload = {}
    if r.get("payload_json"):
        try:
            payload = json.loads(r["payload_json"])
        except Exception:
            payload = {}

    side = str(r.get("side") or payload.get("side", "CE")).upper()
    strike = float(r.get("strike") or payload.get("strike", 0.0))
    initial_ltp = float(r.get("initial_ltp") or payload.get("initial_ltp") or payload.get("entry_ltp", 1.0))
    score = int(r.get("current_score") or r.get("initial_score") or payload.get("quality_score", 70))
    conviction_tier = r.get("conviction_tier") or payload.get("conviction_tier", "LOW")
    if conviction_tier == "HIGH":
        conv_label = "HIGH CONVICTION (Grade A+)"
        conv_col = "var(--go)"
    elif conviction_tier == "MEDIUM":
        conv_label = "SECONDARY PLAY (Grade B+)"
        conv_col = "var(--spark)"
    else:
        conv_label = "TACTICAL OPPORTUNITY (Grade B)"
        conv_col = "var(--ink-mute)"

    sym = r.get("symbol") or payload.get("symbol", "")
    badge = r.get("play_badge") or payload.get("play_badge", "STRATEGY")
    rid = r.get("rec_id") or payload.get("rec_id", f"{sym}_{side}_{int(strike)}_{badge}")

    idea = TradeIdea(
        symbol=sym,
        side=side,
        play_type=r.get("play_type") or payload.get("play_type", "Strategy Play"),
        play_badge=badge,
        strike=strike,
        option_symbol=r.get("option_symbol") or payload.get("option_symbol", f"{sym} {int(strike)} {side}"),
        current_ltp=initial_ltp,
        delta_approx=float(payload.get("delta_approx", 0.50 if side == "CE" else -0.50)),
        entry_trigger=r.get("entry_trigger") or payload.get("entry_trigger", "Bookmarked Active Thesis"),
        spot_invalidation=float(r.get("spot_invalidation") or payload.get("spot_invalidation", 0.0)),
        spot_invalidation_text=r.get("spot_invalidation_text") or payload.get("spot_invalidation_text", ""),
        target_1=float(r.get("target_1") or payload.get("target_1", 0.0)),
        target_1_spot=float(r.get("target_1_spot") or payload.get("target_1_spot", 0.0)),
        target_2=float(r.get("target_2") or payload.get("target_2", 0.0)),
        target_2_spot=float(r.get("target_2_spot") or payload.get("target_2_spot", 0.0)),
        stop_loss=float(r.get("stop_loss") or payload.get("stop_loss", 0.0)),
        risk_reward_ratio=r.get("risk_reward_ratio") or payload.get("risk_reward_ratio", "1 : 2.5"),
        quality_score=score,
        conviction_label=conv_label,
        conviction_color=conv_col,
        rationale=r.get("rationale") or payload.get("rationale", "User-pinned setup preserved across market scans."),
        catalyst_tags=payload.get("catalyst_tags") or (json.loads(r.get("catalyst_tags_json") or "[]") if r.get("catalyst_tags_json") else []),
        rec_id=rid,
        triggered_at=r.get("triggered_at") or payload.get("triggered_at", ""),
        conviction_tier=conviction_tier,
        is_bookmarked=True,
        p_touch_target_1=float(payload.get("p_touch_target_1", 0.65)),
        p_touch_sl=float(payload.get("p_touch_sl", 0.35)),
        net_ev_pts=float(payload.get("net_ev_pts", 15.0)),
        initial_ltp=initial_ltp,
        initial_spot=float(r.get("initial_spot") or payload.get("initial_spot", current_spot)),
    )
    return idea


def generate_trade_recommendations(
    data: Any,
    bias_result: Any | None = None,
    market_intel: Any | None = None,
    sentinel_signals: Sequence[Any] | None = None,
    latest_bar: Any | None = None,
    sentinel_bars: Sequence[Any] | None = None,
    spot_override: float | None = None,
    min_score: int = 75,
    **kwargs: Any,
) -> RecommendationBatch:
    """Generates institutional options recommendations for CE and PE, filtering for Score >= min_score (default 75).

    Evaluates 7 institutional strategy playbooks per side:
    1. MOMENTUM: ATM Momentum Play (Delta ~0.50)
    2. STRUCTURE: Institutional Structure Play (Delta ~0.40)
    3. STRIKE BREAK: Convex break at a gamma strike (Delta ~0.28)
    4. ICEBERG FADE: OrderFlow Iceberg Absorption Fade (Delta ~0.50)
    5. VANNA-CHARM: 0DTE Vanna-Charm Theta Compression (Delta ~0.20)
    6. VOL DISPERSION: Volatility Dispersion / Long Gamma Explosion (Delta ~0.50)
    7. RATIO REVERSAL: Zero-Cost Ratio Risk Reversal (Delta ~0.40)

    Args:
        data: DashboardData instance containing levels, oi_strikes, contracts, etc.
        bias_result: Optional BiasResult from compute_directional_bias.
        market_intel: Optional MarketIntelligence from analyze_market_intelligence.
        sentinel_signals: Recent OrderFlow Sentinel signals.
        latest_bar: Latest Sentinel bar (for VWAP and sigma bands).
        spot_override: Optional live spot quote override.
        min_score: Minimum Quality Score filter threshold (default 75).

    Returns:
        RecommendationBatch with filtered recommendations (Score >= min_score),
        full candidate lists, and macro executive summary.
    """
    symbol = getattr(data, "symbol", "NIFTY") if data else "NIFTY"
    idx = config.get_index(symbol)
    step = int(getattr(idx, "strike_step", 50 if symbol == "NIFTY" else 100))

    # 1. Resolve core levels. A missing chain is not a 23,000 Nifty, a 55,000
    # Bank Nifty, or a 74,000 Sensex, and the synthetic demo book is not a live read.
    levels = getattr(data, "levels", None) if data else None
    raw_spot = getattr(levels, "spot", None) if levels else None
    try:
        chain_spot = float(raw_spot) if raw_spot is not None else 0.0
    except (TypeError, ValueError):
        chain_spot = 0.0
    source = str(getattr(data, "source", "") or "")
    if source.upper().startswith("DEMO") or (not spot_override and chain_spot <= 0.0):
        return _empty_recommendation_batch(
            symbol,
            "No live chain spot. Ideas are not built on a placeholder index.",
        )
    spot = float(spot_override) if spot_override and float(spot_override) > 0 else chain_spot
    if chain_spot <= 0.0:
        chain_spot = spot
    raw_cw = getattr(levels, "call_wall", None) if levels else None
    call_wall = float(raw_cw if raw_cw is not None else _round_strike(spot + 2 * step, step))
    raw_pw = getattr(levels, "put_wall", None) if levels else None
    put_wall = float(raw_pw if raw_pw is not None else _round_strike(spot - 2 * step, step))
    raw_flip = getattr(levels, "flip", None) if levels else None
    flip = float(raw_flip if raw_flip is not None else spot)

    # 2. Resolve VWAP & Sigma Envelopes
    if latest_bar and getattr(latest_bar, "vwap", None) and latest_bar.vwap > 0:
        session_vwap = float(latest_bar.vwap)
        raw_vu = getattr(latest_bar, "vwap_sigma_1", None)
        vwap_upper = float(raw_vu if raw_vu is not None else (session_vwap + step * 0.7))
        raw_vl = getattr(latest_bar, "vwap_lower_1", None)
        vwap_lower = float(raw_vl if raw_vl is not None else (session_vwap - step * 0.7))
    else:
        session_vwap = spot
        vwap_upper = spot + step * 0.75
        vwap_lower = spot - step * 0.75

    # 3. Resolve Directional Bias
    raw_score = getattr(bias_result, "score", None) if bias_result else None
    bias_score = float(raw_score if raw_score is not None else 0.0)
    bias_regime = str(getattr(bias_result, "regime", "Neutral")) if bias_result else "Neutral"

    # Extract strike data structures & pre-index for sub-millisecond O(1) lookups
    oi_strikes = getattr(data, "oi_strikes", None) if data else None
    contracts_df = getattr(data, "contracts", None) if data else None

    oi_map: dict[float, dict] = {}
    if oi_strikes:
        for s in oi_strikes:
            try:
                stk_raw = s.get("strike", 0.0)
                if stk_raw is not None:
                    oi_map[float(stk_raw)] = s
            except Exception:
                continue

    contracts_map: dict[tuple[str, float], tuple[float, float, float, float]] = {}
    if contracts_df is not None:
        try:
            if hasattr(contracts_df, "itertuples"):
                for row in contracts_df.itertuples(index=False):
                    r_type = "C" if str(getattr(row, "type", "")).upper().startswith("C") else "P"
                    r_strike = float(getattr(row, "strike", 0.0) or 0.0)
                    r_ltp = float(getattr(row, "ltp", 0.0) or getattr(row, "mid", 0.0) or 0.0)
                    r_delta = float(getattr(row, "delta", 0.0) or 0.0)
                    r_oi = float(getattr(row, "oi", 0.0) or 0.0)
                    r_doi = float(getattr(row, "doi", 0.0) or 0.0)
                    contracts_map[(r_type, r_strike)] = (r_ltp, r_delta, r_oi, r_doi)
        except Exception:
            pass

    # Base ATM strike
    atm_strike = _round_strike(spot, step)

    # Pre-calculate candidate strikes across all 7 strategies via Delta Window selection
    ce_atm_k = _select_strike_by_delta_window(0.50, True, atm_strike, contracts_map, step)
    ce_struct_k = _select_strike_by_delta_window(0.40, True, atm_strike + step, contracts_map, step)
    # Clamp gamma squeeze strike to avoid deep OTM outlier noise (> 6 steps away)
    ce_squeeze_k = call_wall if (call_wall > spot and call_wall <= spot + (6 * step)) else _select_strike_by_delta_window(0.28, True, atm_strike + (2 * step), contracts_map, step)
    ce_vanna_k = _select_strike_by_delta_window(0.20, True, atm_strike + (2 * step), contracts_map, step)

    pe_atm_k = _select_strike_by_delta_window(0.50, False, atm_strike, contracts_map, step)
    pe_struct_k = _select_strike_by_delta_window(0.40, False, atm_strike - step, contracts_map, step)
    pe_squeeze_k = put_wall if (put_wall < spot and put_wall >= spot - (6 * step)) else _select_strike_by_delta_window(0.28, False, atm_strike - (2 * step), contracts_map, step)
    pe_vanna_k = _select_strike_by_delta_window(0.20, False, atm_strike - (2 * step), contracts_map, step)

    # Deduplicated subscription registration into live Dhan WebSocket feed
    try:
        from app.ws_spot_feed import subscribe_options
        all_rec_strikes = [
            ce_atm_k, ce_struct_k, ce_squeeze_k, ce_vanna_k,
            pe_atm_k, pe_struct_k, pe_squeeze_k, pe_vanna_k,
        ]
        novel_strikes = [float(k) for k in set(all_rec_strikes) if (symbol, float(k)) not in _SUBSCRIBED_REC_STRIKES]
        if novel_strikes:
            subscribe_options(symbol, novel_strikes)
            for k in novel_strikes:
                _SUBSCRIBED_REC_STRIKES.add((symbol, float(k)))
    except Exception:
        pass

    # Resolve active expiry tag (e.g. '13 OCT')
    expiry_tag = ""
    try:
        if contracts_df is not None and hasattr(contracts_df, "columns") and "expiry" in contracts_df.columns:
            valid_exp = contracts_df["expiry"].dropna()
            if not valid_exp.empty:
                raw_exp = str(valid_exp.iloc[0]).strip()[:10]
                dt = datetime.strptime(raw_exp, "%Y-%m-%d")
                expiry_tag = dt.strftime("%d %b").upper()
        if not expiry_tag and getattr(data, "expiries", None) and len(data.expiries) > 0:
            exp_obj = data.expiries[0]
            if hasattr(exp_obj, "strftime"):
                expiry_tag = exp_obj.strftime("%d %b").upper()
            else:
                raw_exp = str(exp_obj)[:10]
                dt = datetime.strptime(raw_exp, "%Y-%m-%d")
                expiry_tag = dt.strftime("%d %b").upper()
        if not expiry_tag:
            from data.expiry import next_weekly_expiry, next_monthly_expiry
            today_ist = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
            exch = getattr(idx, "exchange", "NSE")
            exp_date = next_weekly_expiry(today_ist, exch) if getattr(idx, "has_weekly", True) else next_monthly_expiry(today_ist, exch)
            expiry_tag = exp_date.strftime("%d %b").upper()
    except Exception:
        pass

    raw_dte = getattr(data, "dte_now", None) if data else None
    dte_days = float(raw_dte if raw_dte is not None else 3.0)
    raw_iv = getattr(levels, "atm_iv", None) if levels else None
    atm_iv = float(raw_iv if raw_iv is not None else 0.14)

    # Extract Target Engine Scenario Set (Dirichlet distribution over Bull, Base, Bear)
    scenario_set = None
    try:
        from target_engine.features import extract_features
        from target_engine.scenarios import score_scenarios
        raw_fut = getattr(data, "fut_price", None) if data else None
        fut_val = float(raw_fut if raw_fut is not None else spot)
        raw_mp = getattr(levels, "max_pain", None) if levels else None
        max_pain_val = float(raw_mp if raw_mp is not None else spot)
        fv = extract_features(
            spot=spot,
            fut=fut_val,
            atm_iv=atm_iv,
            expiry=expiry_tag or "2026-10-15",
            contracts_df=contracts_df,
            vwap=session_vwap,
            max_pain=max_pain_val,
        )
        scenario_set = score_scenarios(fv)
    except Exception:
        scenario_set = None

    # Resolve Trend Context (EMA-9, EMA-21 cross, 5m ATR, ADX, CVD divergence, Lunch chop)
    bars_for_trend = sentinel_bars if sentinel_bars else ([latest_bar] if latest_bar else None)
    trend_ctx = compute_trend_context(bars_for_trend, default_step=float(step), latest_bar=latest_bar)
    ema_trend = trend_ctx.get("ema_trend", "NEUTRAL")
    eff_atr = float(trend_ctx.get("atr", step * 0.7))
    adx_val = float(trend_ctx.get("adx", 20.0))
    adx_regime = str(trend_ctx.get("adx_regime", "NEUTRAL"))
    cvd_div = str(trend_ctx.get("cvd_divergence", "NONE"))
    if cvd_div == "NONE" and sentinel_signals:
        for sig in sentinel_signals[:4]:
            stype = str(getattr(sig, "type", "")).upper()
            if "CVD_DIVERGENCE_BULL" in stype:
                cvd_div = "BULLISH"
                break
            elif "CVD_DIVERGENCE_BEAR" in stype:
                cvd_div = "BEARISH"
                break
    is_lunch = bool(trend_ctx.get("is_lunch_chop", False))

    sector_rows = kwargs.get("sector_rows")
    breadth_ctx = compute_sector_breadth_context(sector_rows)
    sec_breadth = breadth_ctx.get("breadth", "NEUTRAL")

    fii_ctx = compute_fii_positioning_context()
    fii_bias = fii_ctx.get("fii_bias", "NEUTRAL")

    # Extract pre-market regime and intraday gap fill confluence
    pm_state = kwargs.get("pre_market_state")
    if pm_state is None:
        try:
            from data.pre_post_market import get_pre_market_state
            pm_state = get_pre_market_state(symbol)
        except Exception:
            pm_state = None

    pm_regime = ""
    pm_fill = ""
    if pm_state is not None:
        if isinstance(pm_state, dict):
            pm_regime = str(pm_state.get("gap_regime") or "").upper()
            pm_fill = str(pm_state.get("gap_fill_status") or "UNFILLED").upper()
        else:
            pm_regime = str(getattr(pm_state, "gap_regime", "") or "").upper()
            pm_fill = str(getattr(pm_state, "gap_fill_status", "") or "UNFILLED").upper()

    # Concurrent strategy evaluation: evaluate CE and PE pipelines simultaneously in parallel threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fut_ce = executor.submit(
            _evaluate_ce_strategies,
            symbol=symbol,
            step=step,
            spot=spot,
            chain_spot=chain_spot,
            call_wall=call_wall,
            put_wall=put_wall,
            flip=flip,
            session_vwap=session_vwap,
            vwap_upper=vwap_upper,
            vwap_lower=vwap_lower,
            bias_score=bias_score,
            sentinel_signals=sentinel_signals,
            ce_atm_k=ce_atm_k,
            ce_struct_k=ce_struct_k,
            ce_squeeze_k=ce_squeeze_k,
            ce_vanna_k=ce_vanna_k,
            oi_strikes=oi_strikes,
            contracts_df=contracts_df,
            oi_map=oi_map,
            contracts_map=contracts_map,
            expiry_tag=expiry_tag,
            dte_days=dte_days,
            atm_iv=atm_iv,
            scenario_set=scenario_set,
            ema_trend=ema_trend,
            eff_atr=eff_atr,
            adx_regime=adx_regime,
            adx_val=adx_val,
            cvd_divergence=cvd_div,
            is_lunch_chop=is_lunch,
            sector_breadth=sec_breadth,
            fii_macro_bias=fii_bias,
            pre_market_regime=pm_regime,
            gap_fill_status=pm_fill,
        )
        fut_pe = executor.submit(
            _evaluate_pe_strategies,
            symbol=symbol,
            step=step,
            spot=spot,
            chain_spot=chain_spot,
            call_wall=call_wall,
            put_wall=put_wall,
            flip=flip,
            session_vwap=session_vwap,
            vwap_upper=vwap_upper,
            vwap_lower=vwap_lower,
            bias_score=bias_score,
            sentinel_signals=sentinel_signals,
            pe_atm_k=pe_atm_k,
            pe_struct_k=pe_struct_k,
            pe_squeeze_k=pe_squeeze_k,
            pe_vanna_k=pe_vanna_k,
            oi_strikes=oi_strikes,
            contracts_df=contracts_df,
            oi_map=oi_map,
            contracts_map=contracts_map,
            expiry_tag=expiry_tag,
            dte_days=dte_days,
            atm_iv=atm_iv,
            scenario_set=scenario_set,
            ema_trend=ema_trend,
            eff_atr=eff_atr,
            adx_regime=adx_regime,
            adx_val=adx_val,
            cvd_divergence=cvd_div,
            is_lunch_chop=is_lunch,
            sector_breadth=sec_breadth,
            fii_macro_bias=fii_bias,
            pre_market_regime=pm_regime,
            gap_fill_status=pm_fill,
        )
        ce_recs = fut_ce.result()
        pe_recs = fut_pe.result()

    # 4. Attach position sizing and persistent telemetry
    lot_size = int(getattr(idx, "lot_size", 65))
    try:
        import data.journal.recommendations_store as rec_store
    except Exception:
        rec_store = None

    # Re-hydrate bookmarked/pinned recommendations so they remain visible across strike drift & score fades
    if rec_store:
        try:
            bm_records = rec_store.list_bookmarked_records(symbol)
            existing_ce_ids = {getattr(t, "rec_id", None) for t in ce_recs}
            existing_pe_ids = {getattr(t, "rec_id", None) for t in pe_recs}
            for b_rec in bm_records:
                rid = b_rec.get("rec_id")
                if not rid:
                    continue
                b_side = str(b_rec.get("side", "CE")).upper()
                if b_side == "CE" and rid not in existing_ce_ids:
                    rehydrated = _rehydrate_bookmarked_trade_idea(b_rec, spot)
                    ce_recs.append(rehydrated)
                    existing_ce_ids.add(rid)
                elif b_side == "PE" and rid not in existing_pe_ids:
                    rehydrated = _rehydrate_bookmarked_trade_idea(b_rec, spot)
                    pe_recs.append(rehydrated)
                    existing_pe_ids.add(rid)
        except Exception as exc:
            log.warning("Error rehydrating bookmarked records for %s: %s", symbol, exc)

    from datetime import datetime, timezone, timedelta
    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S")

    persist_items: list[tuple[dict, dict, dict]] = []
    for t in ce_recs + pe_recs:
        t.position_size = compute_position_size(t, account_capital=500_000.0, risk_pct=1.5, lot_size=lot_size)
        t.rec_id = f"{symbol}_{t.side}_{int(t.strike)}_{t.play_badge}"
        t.triggered_at = now_ist
        t.conviction_tier = "HIGH" if t.quality_score > 85 else ("MEDIUM" if t.quality_score >= 75 else "LOW")

        # Generate strategy-specific microstructure reasoning with precise data points
        t.score_reason = _generate_strategy_score_reason(
            idea=t,
            spot=spot,
            session_vwap=session_vwap,
            vwap_upper=vwap_upper,
            vwap_lower=vwap_lower,
            call_wall=call_wall,
            put_wall=put_wall,
            flip=flip,
            bias_score=bias_score,
            latest_bar=latest_bar,
            sentinel_signals=sentinel_signals,
        )

        factors = {
            "bias_score": round(bias_score, 2),
            "spot": round(spot, 1),
            "session_vwap": round(session_vwap, 1),
            "flip": round(flip, 1),
            "call_wall": round(call_wall, 1),
            "put_wall": round(put_wall, 1),
            "delta_ratio": round(float(getattr(latest_bar, "delta_ratio", 0.0) or 0.0), 3) if latest_bar else 0.0,
            "play_badge": t.play_badge,
            "strike": t.strike,
        }

        if rec_store:
            try:
                t.is_bookmarked = rec_store.is_bookmarked(t.rec_id)

                # In-memory score evolution (instant 0ms without blocking SQLite queries)
                hist = _MEM_SCORE_HISTORY.get(t.rec_id)
                if not hist:
                    try:
                        db_hist = rec_store.get_score_history(t.rec_id, limit=5)
                        hist = list(db_hist) if db_hist else []
                    except Exception:
                        hist = []
                    _MEM_SCORE_HISTORY[t.rec_id] = hist

                prev_score = hist[0].get("score", t.quality_score) if hist else t.quality_score
                delta = t.quality_score - prev_score
                t.score_delta = delta

                if not hist or hist[0].get("score") != t.quality_score or abs(float(hist[0].get("current_spot") or 0) - spot) > 1.0:
                    entry = {
                        "score": t.quality_score,
                        "score_delta": delta,
                        "current_spot": spot,
                        "current_ltp": t.current_ltp,
                        "reason": t.score_reason,
                        "factors_json": json.dumps(factors),
                    }
                    hist.insert(0, entry)
                    _MEM_SCORE_HISTORY[t.rec_id] = hist[:10]

                t.score_history = _MEM_SCORE_HISTORY[t.rec_id]

                # Prepare persistence payloads for background execution
                idea_payload = {
                    "rec_id": t.rec_id,
                    "symbol": t.symbol,
                    "side": t.side,
                    "strike": t.strike,
                    "option_symbol": t.option_symbol,
                    "play_type": t.play_type,
                    "play_badge": t.play_badge,
                    "triggered_at": t.triggered_at,
                    "initial_spot": spot,
                    "initial_ltp": t.current_ltp,
                    "quality_score": t.quality_score,
                    "conviction_tier": t.conviction_tier,
                    "target_1": t.target_1,
                    "target_1_spot": t.target_1_spot,
                    "target_2": t.target_2,
                    "target_2_spot": t.target_2_spot,
                    "stop_loss": t.stop_loss,
                    "spot_invalidation": t.spot_invalidation,
                    "spot_invalidation_text": t.spot_invalidation_text,
                    "risk_reward_ratio": t.risk_reward_ratio,
                    "entry_trigger": t.entry_trigger,
                    "rationale": t.rationale,
                    "catalyst_tags": t.catalyst_tags,
                    "is_bookmarked": 1 if t.is_bookmarked else 0,
                }

                # Log point-in-time ML telemetry features
                vanna_val = getattr(data, "vanna_now", None) if data else None
                charm_val = getattr(data, "charm_now", None) if data else None
                raw_dte_val = getattr(data, "dte_now", None) if data else None
                dte_val = float(raw_dte_val if raw_dte_val is not None else 3.0)
                vix_obj = getattr(data, "india_vix", {}) if data else {}
                vix_val = float(vix_obj.get("last")) if vix_obj and vix_obj.get("last") is not None else 13.5
                raw_agg = getattr(data, "raw_agg", {}) if data else {}
                pcr_val = float(raw_agg.get("pcr")) if raw_agg and raw_agg.get("pcr") is not None else 1.0

                date_tag = str(t.triggered_at)[:10].replace("-", "")
                ml_feat = {
                    "sample_id": f"{t.rec_id}_{date_tag}",
                    "symbol": symbol,
                    "triggered_at": t.triggered_at,
                    "strategy_name": t.play_badge,
                    "side": t.side,
                    "strike": t.strike,
                    "moneyness": round(t.strike / spot, 4),
                    "delta": t.delta_approx,
                    "entry_spot": spot,
                    "entry_ltp": t.current_ltp,
                    "quality_score": t.quality_score,
                    "call_wall": call_wall,
                    "put_wall": put_wall,
                    "gamma_flip": flip,
                    "dist_call_wall": abs(call_wall - spot),
                    "dist_put_wall": abs(spot - put_wall),
                    "dist_gamma_flip": abs(spot - flip),
                    "vanna": vanna_val,
                    "charm": charm_val,
                    "dte": dte_val,
                    "india_vix": vix_val,
                    "pcr_oi": pcr_val,
                    "session_vwap": session_vwap,
                    "vwap_distance": spot - session_vwap,
                    "bar_delta": getattr(latest_bar, "delta", 0.0) if latest_bar else 0.0,
                    "cvd": getattr(latest_bar, "cvd", 0.0) if latest_bar else 0.0,
                    "delta_ratio": getattr(latest_bar, "delta_ratio", 0.0) if latest_bar else 0.0,
                    "bias_score": bias_score,
                    "bias_regime": bias_regime,
                    "risk_reward_num": float(t.risk_reward_ratio.split(":")[-1].strip()) if ":" in t.risk_reward_ratio else 2.5,
                    "target_distance_pts": abs(t.target_1_spot - spot),
                    "stop_distance_pts": abs(t.spot_invalidation - spot),
                }

                hist_item = {
                    "rec_id": t.rec_id,
                    "score": t.quality_score,
                    "current_spot": spot,
                    "current_ltp": t.current_ltp,
                    "reason": t.score_reason,
                    "factors": factors,
                }

                persist_items.append((idea_payload, ml_feat, hist_item))
            except Exception:
                pass

    # Asynchronous non-blocking background flush of SQLite persistence and ML telemetry
    if persist_items and rec_store:
        def _persist_worker(items=persist_items, store=rec_store):
            for payload, mlf, h_item in items:
                try:
                    store.save_or_update_recommendation(payload)
                    if mlf:
                        store.log_ml_features(mlf)
                    if h_item:
                        store.append_score_history(**h_item)
                except Exception:
                    pass

        try:
            fut = _BG_PERSIST_EXECUTOR.submit(_persist_worker)
            _PENDING_PERSIST_FUTURES.append(fut)
        except Exception:
            pass

    # 5. Process signal latching to eliminate card flickering/vanishing
    _LATCH_MANAGER.process_and_latch(symbol, spot, ce_recs + pe_recs, min_score=min_score)
    for t in ce_recs + pe_recs:
        update_trade_idea_live_telemetry(t, symbol, spot)

    all_ce_candidates = sorted(ce_recs, key=lambda x: (x.quality_score, x.net_ev_pts), reverse=True)
    all_pe_candidates = sorted(pe_recs, key=lambda x: (x.quality_score, x.net_ev_pts), reverse=True)

    # 6. Quality-Gate Partitioning (Score > 85 Higher Conviction, 75 <= Score <= 85 Lower Fallback, 65 <= Score < 75 Tactical, or Bookmarked/Active Latch)
    higher_ce = [
        t for t in all_ce_candidates
        if (t.quality_score > 85)
        or (t.is_bookmarked and t.conviction_tier == "HIGH" and t.latch_state != "INVALIDATED")
    ]
    higher_pe = [
        t for t in all_pe_candidates
        if (t.quality_score > 85)
        or (t.is_bookmarked and t.conviction_tier == "HIGH" and t.latch_state != "INVALIDATED")
    ]

    lower_ce = [
        t for t in all_ce_candidates
        if (
            (75 <= t.quality_score <= 85)
            or (t.latch_state == "ACTIVE" and 75 <= t.quality_score <= 85)
            or (t.is_bookmarked and t.conviction_tier == "MEDIUM" and t.latch_state != "INVALIDATED")
        ) and t not in higher_ce
    ]
    lower_pe = [
        t for t in all_pe_candidates
        if (
            (75 <= t.quality_score <= 85)
            or (t.latch_state == "ACTIVE" and 75 <= t.quality_score <= 85)
            or (t.is_bookmarked and t.conviction_tier == "MEDIUM" and t.latch_state != "INVALIDATED")
        ) and t not in higher_pe
    ]

    tactical_ce = [
        t for t in all_ce_candidates
        if (
            (65 <= t.quality_score < 75)
            or (t.latch_state == "ACTIVE" and 65 <= t.quality_score < 75)
            or (t.is_bookmarked and (t.conviction_tier == "LOW" or t.quality_score < 75) and t.latch_state != "INVALIDATED")
        ) and t not in higher_ce and t not in lower_ce
    ]
    tactical_pe = [
        t for t in all_pe_candidates
        if (
            (65 <= t.quality_score < 75)
            or (t.latch_state == "ACTIVE" and 65 <= t.quality_score < 75)
            or (t.is_bookmarked and (t.conviction_tier == "LOW" or t.quality_score < 75) and t.latch_state != "INVALIDATED")
        ) and t not in higher_pe and t not in lower_pe
    ]
    tactical_ce = [t for t in tactical_ce if t.latch_state != "INVALIDATED"]
    tactical_pe = [t for t in tactical_pe if t.latch_state != "INVALIDATED"]

    # Display rule: Higher conviction if available; fallback to lower ONLY if zero higher conviction
    if higher_ce:
        display_ce = higher_ce
        ce_fallback = False
    elif lower_ce:
        display_ce = lower_ce
        ce_fallback = True
    else:
        display_ce = []
        ce_fallback = False

    if higher_pe:
        display_pe = higher_pe
        pe_fallback = False
    elif lower_pe:
        display_pe = lower_pe
        pe_fallback = True
    else:
        display_pe = []
        pe_fallback = False

    # Respect explicit custom min_score if specified (e.g. min_score=99 or min_score=0 in tests)
    if min_score not in (75, 85):
        qualified_ce = [t for t in all_ce_candidates if t.quality_score >= min_score or t.latch_state == "ACTIVE"]
        qualified_pe = [t for t in all_pe_candidates if t.quality_score >= min_score or t.latch_state == "ACTIVE"]
    else:
        qualified_ce = display_ce
        qualified_pe = display_pe

    # Filter out invalidated setups from active display
    qualified_ce = [t for t in qualified_ce if t.latch_state != "INVALIDATED"]
    qualified_pe = [t for t in qualified_pe if t.latch_state != "INVALIDATED"]
    qualified_ce, qualified_pe, ce_watch, pe_watch = apply_tactical_watch(
        qualified_ce, qualified_pe, all_ce_candidates, all_pe_candidates, min_score,
    )

    # Collect bookmarked recommendations
    bookmarked = [t for t in all_ce_candidates + all_pe_candidates if t.is_bookmarked]

    # 7. Synthesize top recommended side and executive summary
    avg_ce_score = sum(t.quality_score for t in all_ce_candidates) / max(1, len(all_ce_candidates))
    avg_pe_score = sum(t.quality_score for t in all_pe_candidates) / max(1, len(all_pe_candidates))

    regime_notes = []
    if adx_regime == "CHOP":
        regime_notes.append("ADX is below 20. The tape is choppy.")
    elif adx_regime == "TRENDING":
        regime_notes.append("Directional trend (ADX > 25) — momentum favored")
    if sec_breadth != "NEUTRAL":
        regime_notes.append(f"Sector breadth: {sec_breadth.title()}")
    if fii_bias != "NEUTRAL":
        regime_notes.append(f"FII macro: {fii_bias.title()}")
    if is_lunch:
        regime_notes.append("Lunch-hour low RVOL window")
    reg_suffix = f" [{' · '.join(regime_notes)}]" if regime_notes else ""

    if ce_watch or pe_watch:
        ce_txt = str(qualified_ce[0].quality_score) if qualified_ce else "none at 65"
        pe_txt = str(qualified_pe[0].quality_score) if qualified_pe else "none at 65"
        if qualified_ce and qualified_pe:
            top_side = "BOTH"
        elif qualified_ce:
            top_side = "CE"
        elif qualified_pe:
            top_side = "PE"
        else:
            top_side = "NEUTRAL"
        exec_summary = (
            f"Best live read is under the 75 high-conviction line (call {ce_txt}, put {pe_txt}). "
            f"The card shows that read. High conviction still starts at 75. "
            f"Directional bias ({bias_score:+.1f}).{reg_suffix}"
        )
    elif qualified_ce and not qualified_pe:
        top_side = "CE"
        mode_txt = "Secondary fallback mode" if ce_fallback else "High-conviction (Score > 85)"
        exec_summary = (
            f"Bullish conviction dominates ({len(qualified_ce)} Call strategies · {mode_txt}). "
            f"Directional bias ({bias_score:+.1f}). Call-gamma strike {call_wall:,.0f} is a concentration, not a ceiling.{reg_suffix}"
        )
    elif qualified_pe and not qualified_ce:
        top_side = "PE"
        mode_txt = "Secondary fallback mode" if pe_fallback else "High-conviction (Score > 85)"
        exec_summary = (
            f"Bearish conviction dominates ({len(qualified_pe)} Put strategies · {mode_txt}). "
            f"Directional bias ({bias_score:+.1f}). Put-gamma strike {put_wall:,.0f} is a concentration, not a floor.{reg_suffix}"
        )
    elif qualified_ce and qualified_pe:
        top_side = "BOTH"
        exec_summary = (
            f"Active two-way setups ({len(qualified_ce)} Calls, {len(qualified_pe)} Puts). "
            f"Both sides are on the card. Put-gamma strike {put_wall:,.0f} and call-gamma strike {call_wall:,.0f} are concentrations, not boundaries that hold.{reg_suffix}"
        )
    else:
        top_side = "NEUTRAL"
        exec_summary = (
            f"Capital preservation mode: No option setups currently achieve the minimum Score ≥ 75 threshold. "
            f"Market regime is choppy or directionally ambiguous (Bias: {bias_score:+.1f}, CE Avg: {avg_ce_score:.0f} | PE Avg: {avg_pe_score:.0f}). "
            f"Stand aside or monitor watchlists below.{reg_suffix}"
        )

    return RecommendationBatch(
        symbol=symbol,
        spot=spot,
        session_vwap=session_vwap,
        call_wall=call_wall,
        put_wall=put_wall,
        gamma_flip=flip,
        directional_bias_score=bias_score,
        bias_regime=bias_regime,
        ce_recommendations=qualified_ce,
        pe_recommendations=qualified_pe,
        top_recommended_side=top_side,
        executive_summary=exec_summary,
        all_ce_candidates=all_ce_candidates,
        all_pe_candidates=all_pe_candidates,
        min_score_filter=min_score,
        higher_ce_recommendations=higher_ce,
        higher_pe_recommendations=higher_pe,
        lower_ce_recommendations=lower_ce,
        lower_pe_recommendations=lower_pe,
        tactical_ce_recommendations=tactical_ce,
        tactical_pe_recommendations=tactical_pe,
        ce_fallback_active=ce_fallback,
        pe_fallback_active=pe_fallback,
        ce_tactical_active=bool(ce_watch or (not higher_ce and not lower_ce and tactical_ce) or any(t.is_bookmarked for t in tactical_ce)),
        pe_tactical_active=bool(pe_watch or (not higher_pe and not lower_pe and tactical_pe) or any(t.is_bookmarked for t in tactical_pe)),
        bookmarked_recommendations=bookmarked,
    )

