"""analytics/trade_ideas.py — Top 3 CE/PE Options Recommendation Engine.

Synthesizes:
1. Real-time Spot & GEX Structure (Spot, Call Wall, Put Wall, Gamma Flip, Max Pain)
2. OrderFlow Sentinel Microstructure (Session VWAP, Sigma Envelopes, Aggressive vs Passive Absorption)
3. Institutional Buildup & Strike Velocity (OI changes, Short Covering, Long Buildup)
4. Macro Context & Directional Bias (-10.0 to +10.0 conviction score)

Produces 3 actionable trade ideas for Calls (CE) and 3 for Puts (PE):
- Play 1: ATM Momentum Play (Delta ~0.45-0.55, closest to Spot)
- Play 2: Institutional Liquidity / Structure Play (Key buildup strike / wall defense)
- Play 3: Gamma Squeeze / Lotto Play (OTM convexity near major GEX acceleration walls)

Separation guarantee:
- Pure analytical and recommendation logic, unit-testable offline without Streamlit or network calls.
- Fail-soft defaults for missing or degraded data streams.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import config


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
    play_type: str                     # "ATM Momentum", "Institutional Structure", "Gamma Squeeze Lotto"
    play_badge: str                    # "MOMENTUM" | "STRUCTURE" | "GAMMA SQUEEZE"
    strike: float
    option_symbol: str                 # e.g. "NIFTY 24500 CE"
    current_ltp: float                 # Estimated or live Option Premium
    delta_approx: float                # Delta estimate (e.g. 0.50 or -0.50)
    entry_trigger: str                 # Microstructure trigger (e.g., "Pullback to VWAP @ 24,480")
    spot_invalidation: float           # Spot price level that invalidates the trade
    spot_invalidation_text: str        # Plain-English spot invalidation condition
    target_1: float                    # Option price target 1 (1R / ~30-40%)
    target_1_spot: float               # Spot level corresponding to Target 1
    target_2: float                    # Option price target 2 (2R+ / ~60-120%)
    target_2_spot: float               # Spot level corresponding to Target 2
    stop_loss: float                   # Option premium stop loss (e.g. -25% to -35%)
    risk_reward_ratio: str             # e.g. "1 : 2.4"
    quality_score: int                 # 0 to 100
    conviction_label: str              # "HIGH CONVICTION", "TACTICAL OPPORTUNITY", "COUNTER-TREND / SPECULATIVE"
    conviction_color: str              # CSS color token
    rationale: str                     # Detailed institutional setup narrative
    catalyst_tags: list[str] = field(default_factory=list)
    position_size: PositionSizeInfo | None = None
    is_ws_live: bool = False           # True when raw exchange trade ticks arrive via WebSocket


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
) -> tuple[int, str, str]:
    """Calculates Trade Quality Score (0-100), Conviction Label, and Badge Color."""
    score = 55

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

    # 2. VWAP & Equilibrium Location (Weight: 15 pts)
    if side == "CE":
        if spot >= vwap:
            score += 12  # Trading in upper distribution
        else:
            if play_type == "ATM Momentum":
                score -= 8   # Buying ATM call when spot is below VWAP is risky
            else:
                score += 5   # Buying support structure below VWAP
    else:  # PE
        if spot <= vwap:
            score += 12  # Trading in lower distribution
        else:
            if play_type == "ATM Momentum":
                score -= 8   # Buying ATM put when spot is above VWAP is risky
            else:
                score += 5   # Shorting resistance above VWAP

    # 3. Sentinel OrderFlow signals (Weight: 20 pts)
    if sentinel_signals:
        top_sigs = [s for s in sentinel_signals if getattr(s, "score", 0) >= 60][:3]
        for sig in top_sigs:
            cat = str(getattr(sig, "category", "")).upper()
            sig_type = str(getattr(sig, "type", "")).upper()
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
    if side == "CE":
        if leg_doi > 0:
            score += 8  # Open interest expanding in Call
    else:
        if leg_doi > 0:
            score += 8  # Open interest expanding in Put

    # 5. Play type specific bonuses
    if play_type == "Gamma Squeeze Lotto":
        # Squeeze requires spot near wall
        dist_to_wall = abs(spot - (call_wall if side == "CE" else put_wall))
        if dist_to_wall <= (spot * 0.006):  # within 0.6% of wall
            score += 12
        else:
            score -= 6

    # Clamp to [15, 95]
    score = max(15, min(95, score))

    if score >= 80:
        label = "HIGH CONVICTION (Grade A+)"
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
    if play_badge == "GAMMA SQUEEZE" and lots > 2 and account_capital < 1_000_000.0:
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


def generate_trade_recommendations(
    data: Any,
    bias_result: Any | None = None,
    market_intel: Any | None = None,
    sentinel_signals: Sequence[Any] | None = None,
    latest_bar: Any | None = None,
    spot_override: float | None = None,
) -> RecommendationBatch:
    """Generates the Top 3 CE and Top 3 PE trade ideas using live market data.

    Args:
        data: DashboardData instance containing levels, oi_strikes, contracts, etc.
        bias_result: Optional BiasResult from compute_directional_bias.
        market_intel: Optional MarketIntelligence from analyze_market_intelligence.
        sentinel_signals: Recent OrderFlow Sentinel signals.
        latest_bar: Latest Sentinel bar (for VWAP and sigma bands).
        spot_override: Optional live spot quote override.

    Returns:
        RecommendationBatch with 3 CE and 3 PE recommendations plus macro summary.
    """
    symbol = getattr(data, "symbol", "NIFTY") if data else "NIFTY"
    idx = config.get_index(symbol)
    step = int(getattr(idx, "strike_step", 50 if symbol == "NIFTY" else 100))

    # 1. Resolve core levels
    levels = getattr(data, "levels", None) if data else None
    chain_spot = float(getattr(levels, "spot", 0.0)) if levels else 0.0
    spot = spot_override or (chain_spot if chain_spot > 0 else 24500.0)
    if chain_spot <= 0.0:
        chain_spot = spot
    call_wall = float(getattr(levels, "call_wall", _round_strike(spot + 2 * step, step))) if levels else _round_strike(spot + 2 * step, step)
    put_wall = float(getattr(levels, "put_wall", _round_strike(spot - 2 * step, step))) if levels else _round_strike(spot - 2 * step, step)
    flip = float(getattr(levels, "flip", spot)) if levels and getattr(levels, "flip", None) else spot

    # 2. Resolve VWAP & Sigma Envelopes
    if latest_bar and getattr(latest_bar, "vwap", None) and latest_bar.vwap > 0:
        session_vwap = float(latest_bar.vwap)
        vwap_upper = float(getattr(latest_bar, "vwap_sigma_1", session_vwap + step * 0.7))
        vwap_lower = float(getattr(latest_bar, "vwap_lower_1", session_vwap - step * 0.7))
    else:
        session_vwap = spot
        vwap_upper = spot + step * 0.75
        vwap_lower = spot - step * 0.75

    # 3. Resolve Directional Bias
    bias_score = float(getattr(bias_result, "score", 0.0)) if bias_result else 0.0
    bias_regime = str(getattr(bias_result, "regime", "Neutral")) if bias_result else "Neutral"

    # Extract strike data structures & pre-index for sub-millisecond O(1) lookups
    oi_strikes = getattr(data, "oi_strikes", None) if data else None
    contracts_df = getattr(data, "contracts", None) if data else None

    oi_map: dict[float, dict] = {}
    if oi_strikes:
        for s in oi_strikes:
            try:
                oi_map[float(s.get("strike", 0.0))] = s
            except Exception:
                continue

    contracts_map: dict[tuple[str, float], tuple[float, float, float, float]] = {}
    if contracts_df is not None:
        try:
            if hasattr(contracts_df, "itertuples"):
                for row in contracts_df.itertuples(index=False):
                    r_type = "C" if str(getattr(row, "type", "")).upper().startswith("C") else "P"
                    r_strike = float(getattr(row, "strike", 0.0))
                    r_ltp = float(getattr(row, "ltp", 0.0) or getattr(row, "mid", 0.0) or 0.0)
                    r_delta = float(getattr(row, "delta", 0.0) or 0.0)
                    r_oi = float(getattr(row, "oi", 0.0) or 0.0)
                    r_doi = float(getattr(row, "doi", 0.0) or 0.0)
                    contracts_map[(r_type, r_strike)] = (r_ltp, r_delta, r_oi, r_doi)
        except Exception:
            pass

    # Base ATM strike
    atm_strike = _round_strike(spot, step)

    # Pre-calculate all candidate recommendation strikes
    ce_atm_k = atm_strike
    ce_struct_k = atm_strike + step
    ce_squeeze_k = call_wall if call_wall > spot else atm_strike + (2 * step)

    pe_atm_k = atm_strike
    pe_struct_k = atm_strike - step
    pe_squeeze_k = put_wall if put_wall < spot else atm_strike - (2 * step)

    # Dynamically register all recommended option contracts into live Dhan WebSocket feed (0ms tick push)
    try:
        from app.ws_spot_feed import subscribe_options
        all_rec_strikes = list({ce_atm_k, ce_struct_k, ce_squeeze_k, pe_atm_k, pe_struct_k, pe_squeeze_k})
        subscribe_options(symbol, all_rec_strikes)
    except Exception:
        pass

    # -----------------------------------------------------------------------
    # CALL RECOMMENDATIONS (Top 3 CE)
    # -----------------------------------------------------------------------
    ce_recs: list[TradeIdea] = []

    # CE Play 1: ATM Momentum Play
    ce_atm_ltp, ce_atm_delta, ce_atm_oi, ce_atm_doi, ce_atm_ws = _lookup_chain_data(
        ce_atm_k, True, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    ce_atm_score, ce_atm_label, ce_atm_color = _compute_trade_score(
        "CE", "ATM Momentum", bias_score, spot, session_vwap, call_wall, put_wall, sentinel_signals, ce_atm_doi
    )
    # Invalidation: Spot breakdown below VWAP -1σ or session VWAP
    ce_atm_inval_spot = round(min(session_vwap - (step * 0.35), spot - (step * 0.6)), 0)
    ce_atm_tgt1_spot = round(max(vwap_upper, spot + (step * 0.75)), 0)
    ce_atm_tgt2_spot = round(call_wall, 0)
    # Option pricing targets
    ce_atm_sl = max(1.0, round(ce_atm_ltp * 0.72, 1))  # -28% risk
    ce_atm_tgt1 = round(ce_atm_ltp * 1.35, 1)          # +35% (1.25R)
    ce_atm_tgt2 = round(ce_atm_ltp * 1.70, 1)          # +70% (2.5R)

    ce_recs.append(
        TradeIdea(
            symbol=symbol,
            side="CE",
            play_type="ATM Momentum Play",
            play_badge="MOMENTUM",
            strike=ce_atm_k,
            option_symbol=f"{symbol} {int(ce_atm_k)} CE",
            current_ltp=ce_atm_ltp,
            delta_approx=ce_atm_delta if ce_atm_delta > 0 else 0.50,
            entry_trigger=f"Enter on intraday retest & bounce of Session VWAP (₹{session_vwap:,.0f}) or 5m close above Opening Range High.",
            spot_invalidation=ce_atm_inval_spot,
            spot_invalidation_text=f"Spot breakdown below ₹{ce_atm_inval_spot:,.0f} (invalidates VWAP support)",
            target_1=ce_atm_tgt1,
            target_1_spot=ce_atm_tgt1_spot,
            target_2=ce_atm_tgt2,
            target_2_spot=ce_atm_tgt2_spot,
            stop_loss=ce_atm_sl,
            risk_reward_ratio="1 : 2.5",
            quality_score=ce_atm_score,
            conviction_label=ce_atm_label,
            conviction_color=ce_atm_color,
            rationale=(
                f"Capitalizes on pure delta momentum with {symbol} holding equilibrium. "
                f"Delta ~{ce_atm_delta:.2f} provides rapid premium expansion with target towards Call Wall ({call_wall:,.0f})."
            ),
            catalyst_tags=["Delta ~0.50", "VWAP Pullback", "High Liquidity"],
            is_ws_live=ce_atm_ws,
        )
    )

    # CE Play 2: Institutional Structure / Liquidity Play
    ce_struct_ltp, ce_struct_delta, ce_struct_oi, ce_struct_doi, ce_struct_ws = _lookup_chain_data(
        ce_struct_k, True, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    ce_struct_score, ce_struct_label, ce_struct_color = _compute_trade_score(
        "CE", "Institutional Structure", bias_score, spot, session_vwap, call_wall, put_wall, sentinel_signals, ce_struct_doi
    )
    ce_struct_inval_spot = round(put_wall - (step * 0.3), 0)
    ce_struct_tgt1_spot = round(flip if flip > spot else spot + step, 0)
    ce_struct_tgt2_spot = round(call_wall, 0)
    ce_struct_sl = max(1.0, round(ce_struct_ltp * 0.68, 1))  # -32% risk
    ce_struct_tgt1 = round(ce_struct_ltp * 1.45, 1)          # +45% (1.4R)
    ce_struct_tgt2 = round(ce_struct_ltp * 1.95, 1)          # +95% (3.0R)

    ce_recs.append(
        TradeIdea(
            symbol=symbol,
            side="CE",
            play_type="Institutional Structure Play",
            play_badge="STRUCTURE",
            strike=ce_struct_k,
            option_symbol=f"{symbol} {int(ce_struct_k)} CE",
            current_ltp=ce_struct_ltp,
            delta_approx=ce_struct_delta if ce_struct_delta > 0 else 0.40,
            entry_trigger=f"Passive buyer absorption confirmed at Put Wall (₹{put_wall:,.0f}) or breakout through Gamma Flip (₹{flip:,.0f}).",
            spot_invalidation=ce_struct_inval_spot,
            spot_invalidation_text=f"Structural breakdown below Put Wall @ ₹{ce_struct_inval_spot:,.0f}",
            target_1=ce_struct_tgt1,
            target_1_spot=ce_struct_tgt1_spot,
            target_2=ce_struct_tgt2,
            target_2_spot=ce_struct_tgt2_spot,
            stop_loss=ce_struct_sl,
            risk_reward_ratio="1 : 3.0",
            quality_score=ce_struct_score,
            conviction_label=ce_struct_label,
            conviction_color=ce_struct_color,
            rationale=(
                f"Tied to institutional Put-writer defense at {put_wall:,.0f}. "
                f"Offers high asymmetry as spot rotates back towards upper distribution."
            ),
            catalyst_tags=["OI Buildup Support", "Wall Reversal", "High Asymmetry"],
            is_ws_live=ce_struct_ws,
        )
    )

    # CE Play 3: Gamma Squeeze / Lotto Play
    ce_sq_ltp, ce_sq_delta, ce_sq_oi, ce_sq_doi, ce_sq_ws = _lookup_chain_data(
        ce_squeeze_k, True, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    ce_sq_score, ce_sq_label, ce_sq_color = _compute_trade_score(
        "CE", "Gamma Squeeze Lotto", bias_score, spot, session_vwap, call_wall, put_wall, sentinel_signals, ce_sq_doi
    )
    ce_sq_inval_spot = round(call_wall - (step * 0.4), 0)
    ce_sq_tgt1_spot = round(call_wall + step, 0)
    ce_sq_tgt2_spot = round(call_wall + (2 * step), 0)
    ce_sq_sl = max(1.0, round(ce_sq_ltp * 0.60, 1))          # -40% risk
    ce_sq_tgt1 = round(ce_sq_ltp * 1.60, 1)                  # +60% (1.5R)
    ce_sq_tgt2 = round(ce_sq_ltp * 2.40, 1)                  # +140% (3.5R)

    ce_recs.append(
        TradeIdea(
            symbol=symbol,
            side="CE",
            play_type="Gamma Squeeze Lotto Play",
            play_badge="GAMMA SQUEEZE",
            strike=ce_squeeze_k,
            option_symbol=f"{symbol} {int(ce_squeeze_k)} CE",
            current_ltp=ce_sq_ltp,
            delta_approx=ce_sq_delta if ce_sq_delta > 0 else 0.28,
            entry_trigger=f"Aggressive market buy orders breach Call Wall (₹{call_wall:,.0f}) with positive CVD volume spike.",
            spot_invalidation=ce_sq_inval_spot,
            spot_invalidation_text=f"Failed breakout: Spot drops back below ₹{ce_sq_inval_spot:,.0f}",
            target_1=ce_sq_tgt1,
            target_1_spot=ce_sq_tgt1_spot,
            target_2=ce_sq_tgt2,
            target_2_spot=ce_sq_tgt2_spot,
            stop_loss=ce_sq_sl,
            risk_reward_ratio="1 : 3.5",
            quality_score=ce_sq_score,
            conviction_label=ce_sq_label,
            conviction_color=ce_sq_color,
            rationale=(
                f"Positioned for convex gamma explosion. A high-volume breach of Call Wall ({call_wall:,.0f}) "
                f"forces short call-dealers to aggressively buy futures to delta-hedge."
            ),
            catalyst_tags=["Dealer Short Covering", "Gamma Blast", "100%+ Multiplier"],
            is_ws_live=ce_sq_ws,
        )
    )

    # -----------------------------------------------------------------------
    # PUT RECOMMENDATIONS (Top 3 PE)
    # -----------------------------------------------------------------------
    pe_recs: list[TradeIdea] = []

    # PE Play 1: ATM Momentum Play
    pe_atm_ltp, pe_atm_delta, pe_atm_oi, pe_atm_doi, pe_atm_ws = _lookup_chain_data(
        pe_atm_k, False, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    pe_atm_score, pe_atm_label, pe_atm_color = _compute_trade_score(
        "PE", "ATM Momentum", bias_score, spot, session_vwap, call_wall, put_wall, sentinel_signals, pe_atm_doi
    )
    pe_atm_inval_spot = round(max(session_vwap + (step * 0.35), spot + (step * 0.6)), 0)
    pe_atm_tgt1_spot = round(min(vwap_lower, spot - (step * 0.75)), 0)
    pe_atm_tgt2_spot = round(put_wall, 0)
    pe_atm_sl = max(1.0, round(pe_atm_ltp * 0.72, 1))        # -28% risk
    pe_atm_tgt1 = round(pe_atm_ltp * 1.35, 1)                # +35% (1.25R)
    pe_atm_tgt2 = round(pe_atm_ltp * 1.70, 1)                # +70% (2.5R)

    pe_recs.append(
        TradeIdea(
            symbol=symbol,
            side="PE",
            play_type="ATM Momentum Play",
            play_badge="MOMENTUM",
            strike=pe_atm_k,
            option_symbol=f"{symbol} {int(pe_atm_k)} PE",
            current_ltp=pe_atm_ltp,
            delta_approx=pe_atm_delta if pe_atm_delta < 0 else -0.50,
            entry_trigger=f"Rejection at Session VWAP (₹{session_vwap:,.0f}) or 5m close below Opening Range Low with negative CVD.",
            spot_invalidation=pe_atm_inval_spot,
            spot_invalidation_text=f"Spot breakout above ₹{pe_atm_inval_spot:,.0f} (invalidates VWAP resistance)",
            target_1=pe_atm_tgt1,
            target_1_spot=pe_atm_tgt1_spot,
            target_2=pe_atm_tgt2,
            target_2_spot=pe_atm_tgt2_spot,
            stop_loss=pe_atm_sl,
            risk_reward_ratio="1 : 2.5",
            quality_score=pe_atm_score,
            conviction_label=pe_atm_label,
            conviction_color=pe_atm_color,
            rationale=(
                f"Downside acceleration vehicle when spot trades below Session Equilibrium. "
                f"Delta ~{pe_atm_delta:.2f} rapidly monetizes drops towards Put Wall ({put_wall:,.0f})."
            ),
            catalyst_tags=["Delta ~-0.50", "VWAP Rejection", "High Liquidity"],
            is_ws_live=pe_atm_ws,
        )
    )

    # PE Play 2: Institutional Structure / Liquidity Play
    pe_struct_ltp, pe_struct_delta, pe_struct_oi, pe_struct_doi, pe_struct_ws = _lookup_chain_data(
        pe_struct_k, False, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    pe_struct_score, pe_struct_label, pe_struct_color = _compute_trade_score(
        "PE", "Institutional Structure", bias_score, spot, session_vwap, call_wall, put_wall, sentinel_signals, pe_struct_doi
    )
    pe_struct_inval_spot = round(call_wall + (step * 0.3), 0)
    pe_struct_tgt1_spot = round(flip if flip < spot else spot - step, 0)
    pe_struct_tgt2_spot = round(put_wall, 0)
    pe_struct_sl = max(1.0, round(pe_struct_ltp * 0.68, 1))  # -32% risk
    pe_struct_tgt1 = round(pe_struct_ltp * 1.45, 1)          # +45% (1.4R)
    pe_struct_tgt2 = round(pe_struct_ltp * 1.95, 1)          # +95% (3.0R)

    pe_recs.append(
        TradeIdea(
            symbol=symbol,
            side="PE",
            play_type="Institutional Structure Play",
            play_badge="STRUCTURE",
            strike=pe_struct_k,
            option_symbol=f"{symbol} {int(pe_struct_k)} PE",
            current_ltp=pe_struct_ltp,
            delta_approx=pe_struct_delta if pe_struct_delta < 0 else -0.40,
            entry_trigger=f"Aggressive seller rejection at Call Wall (₹{call_wall:,.0f}) with seller delta absorption on the tape.",
            spot_invalidation=pe_struct_inval_spot,
            spot_invalidation_text=f"Structural breach above Call Wall @ ₹{pe_struct_inval_spot:,.0f}",
            target_1=pe_struct_tgt1,
            target_1_spot=pe_struct_tgt1_spot,
            target_2=pe_struct_tgt2,
            target_2_spot=pe_struct_tgt2_spot,
            stop_loss=pe_struct_sl,
            risk_reward_ratio="1 : 3.0",
            quality_score=pe_struct_score,
            conviction_label=pe_struct_label,
            conviction_color=pe_struct_color,
            rationale=(
                f"Captures institutional rejection off Call Wall ({call_wall:,.0f}). "
                f"Allows riding the rotation down towards equilibrium with strong risk containment."
            ),
            catalyst_tags=["Call Wall Rejection", "Short Buildup", "High Asymmetry"],
            is_ws_live=pe_struct_ws,
        )
    )

    # PE Play 3: Gamma Squeeze / Breakdown Lotto
    pe_sq_ltp, pe_sq_delta, pe_sq_oi, pe_sq_doi, pe_sq_ws = _lookup_chain_data(
        pe_squeeze_k, False, oi_strikes, contracts_df, spot, step,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=chain_spot, symbol=symbol
    )
    pe_sq_score, pe_sq_label, pe_sq_color = _compute_trade_score(
        "PE", "Gamma Squeeze Lotto", bias_score, spot, session_vwap, call_wall, put_wall, sentinel_signals, pe_sq_doi
    )
    pe_sq_inval_spot = round(put_wall + (step * 0.4), 0)
    pe_sq_tgt1_spot = round(put_wall - step, 0)
    pe_sq_tgt2_spot = round(put_wall - (2 * step), 0)
    pe_sq_sl = max(1.0, round(pe_sq_ltp * 0.60, 1))          # -40% risk
    pe_sq_tgt1 = round(pe_sq_ltp * 1.60, 1)                  # +60% (1.5R)
    pe_sq_tgt2 = round(pe_sq_ltp * 2.40, 1)                  # +140% (3.5R)

    pe_recs.append(
        TradeIdea(
            symbol=symbol,
            side="PE",
            play_type="Gamma Squeeze Lotto Play",
            play_badge="GAMMA SQUEEZE",
            strike=pe_squeeze_k,
            option_symbol=f"{symbol} {int(pe_squeeze_k)} PE",
            current_ltp=pe_sq_ltp,
            delta_approx=pe_sq_delta if pe_sq_delta < 0 else -0.28,
            entry_trigger=f"High-volume breakdown below Put Wall (₹{put_wall:,.0f}) with cascading negative market delta.",
            spot_invalidation=pe_sq_inval_spot,
            spot_invalidation_text=f"Trapdoor reversal: Spot recovers back above ₹{pe_sq_inval_spot:,.0f}",
            target_1=pe_sq_tgt1,
            target_1_spot=pe_sq_tgt1_spot,
            target_2=pe_sq_tgt2,
            target_2_spot=pe_sq_tgt2_spot,
            stop_loss=pe_sq_sl,
            risk_reward_ratio="1 : 3.5",
            quality_score=pe_sq_score,
            conviction_label=pe_sq_label,
            conviction_color=pe_sq_color,
            rationale=(
                f"Trapdoor breakdown setup. If Put Wall ({put_wall:,.0f}) collapses, "
                f"dealer gamma flips deeply negative, forcing panic short-hedging into the abyss."
            ),
            catalyst_tags=["Trapdoor Breakdown", "Dealer Panic Selling", "100%+ Multiplier"],
        )
    )

    # 4. Attach default position sizing (₹5 Lakh capital, 1.5% risk)
    lot_size = int(getattr(idx, "lot_size", 65))
    for t in ce_recs + pe_recs:
        t.position_size = compute_position_size(t, account_capital=500_000.0, risk_pct=1.5, lot_size=lot_size)

    # 5. Synthesize top side and executive summary
    avg_ce_score = sum(t.quality_score for t in ce_recs) / len(ce_recs)
    avg_pe_score = sum(t.quality_score for t in pe_recs) / len(pe_recs)

    if avg_ce_score >= avg_pe_score + 10:
        top_side = "CE"
        exec_summary = (
            f"Bullish conviction dominates ({avg_ce_score:.0f} vs {avg_pe_score:.0f} PE Score). "
            f"Directional bias ({bias_score:+.1f}) and institutional flow favor Call upside towards {call_wall:,.0f}."
        )
    elif avg_pe_score >= avg_ce_score + 10:
        top_side = "PE"
        exec_summary = (
            f"Bearish conviction dominates ({avg_pe_score:.0f} vs {avg_ce_score:.0f} CE Score). "
            f"Directional bias ({bias_score:+.1f}) and seller pressure favor Put downside towards {put_wall:,.0f}."
        )
    else:
        top_side = "BOTH"
        exec_summary = (
            f"Balanced regime (CE: {avg_ce_score:.0f} | PE: {avg_pe_score:.0f}). "
            f"Market is range-bound between Put Wall ({put_wall:,.0f}) and Call Wall ({call_wall:,.0f}); "
            f"trade reversals at boundaries or await momentum breakout above VWAP ({session_vwap:,.0f})."
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
        ce_recommendations=ce_recs,
        pe_recommendations=pe_recs,
        top_recommended_side=top_side,
        executive_summary=exec_summary,
    )
