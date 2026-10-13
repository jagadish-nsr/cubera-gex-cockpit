"""T6 Strike & Structure Candidate Selector.

Generates directional, range, and volatility option structures,
reprices legs across all scenarios via Black-76, applies hard screening filters,
and ranks candidates by probability-weighted EV and Risk/Reward.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

import config as app_config
from target_engine.config import DEFAULT_CONFIG, TargetEngineConfig
from target_engine.contracts import (
    IVForecast,
    OptionLeg,
    OptionTarget,
    ScenarioSet,
    TradeCandidate,
    UnderlyingTarget,
)
from target_engine.pricer import greek_attribution, greeks, price, reprice
from target_engine.risk import (
    calculate_breakevens_and_payoff,
    calculate_expected_value,
    calculate_transaction_costs,
    compute_recommended_sizing,
)
from target_engine.time_utils import (
    calculate_fractional_t,
    calculate_horizon_t,
    to_ist,
)


def select_trade_candidates(
    scenario_set: ScenarioSet,
    underlying_targets: Dict[str, UnderlyingTarget],
    iv_forecasts: Dict[str, IVForecast],
    contracts_df: pd.DataFrame,
    spot: float,
    fut: float,
    expiry: str,
    horizon_minutes: int = 120,
    symbol: str = "NIFTY",
    config: TargetEngineConfig = DEFAULT_CONFIG,
) -> List[TradeCandidate]:
    """Generate and rank multi-scenario trade candidates for dominant view."""
    if contracts_df is None or contracts_df.empty:
        return []

    idx_info = app_config.get_index(symbol)
    lot_size = idx_info.lot_size if idx_info else 65

    now_dt = to_ist(scenario_set.ts)
    T_entry = calculate_fractional_t(now_dt, expiry, day_count=config.day_count)
    _, T_target = calculate_horizon_t(now_dt, horizon_minutes, expiry, day_count=config.day_count)

    # Filter available chain contracts
    df = contracts_df.copy()
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df["oi"] = pd.to_numeric(df["oi"], errors="coerce").fillna(0.0) if "oi" in df.columns else 0.0
    
    price_col = next((c for c in ["ltp", "mid", "price", "last_price"] if c in df.columns), None)
    if price_col:
        df["ltp"] = pd.to_numeric(df[price_col], errors="coerce").fillna(0.0)
    else:
        df["ltp"] = 0.0

    if "iv" in df.columns:
        df["iv"] = pd.to_numeric(df["iv"], errors="coerce").fillna(0.15)
        df.loc[df["iv"] > 1.0, "iv"] = df.loc[df["iv"] > 1.0, "iv"] / 100.0
    else:
        df["iv"] = 0.15

    df["type"] = df["type"].astype(str).str.upper()

    strikes = sorted(df["strike"].unique())
    if not strikes:
        return []

    atm_strike = min(strikes, key=lambda k: abs(k - spot))
    strike_step = 50.0 if "NIFTY" in symbol.upper() else 100.0

    dominant = scenario_set.dominant_scenario
    candidates: List[TradeCandidate] = []

    # 1. Structure prototypes based on dominant scenario
    raw_prototypes = []

    if dominant == "bull":
        # Long ATM Call
        raw_prototypes.append(("LONG_CALL", [("BUY", atm_strike, "CE")]))
        # Long OTM Call
        otm_call = atm_strike + strike_step
        if otm_call in strikes:
            raw_prototypes.append(("LONG_CALL_OTM", [("BUY", otm_call, "CE")]))
        # Bull Call Spread
        spread_sell = atm_strike + 2 * strike_step
        if spread_sell in strikes:
            raw_prototypes.append(("BULL_CALL_SPREAD", [
                ("BUY", atm_strike, "CE"),
                ("SELL", spread_sell, "CE")
            ]))

    elif dominant == "bear":
        # Long ATM Put
        raw_prototypes.append(("LONG_PUT", [("BUY", atm_strike, "PE")]))
        # Long OTM Put
        otm_put = atm_strike - strike_step
        if otm_put in strikes:
            raw_prototypes.append(("LONG_PUT_OTM", [("BUY", otm_put, "PE")]))
        # Bear Put Spread
        spread_sell = atm_strike - 2 * strike_step
        if spread_sell in strikes:
            raw_prototypes.append(("BEAR_PUT_SPREAD", [
                ("BUY", atm_strike, "PE"),
                ("SELL", spread_sell, "PE")
            ]))

    else:
        # Base (Range): generate defined-risk debit spreads on both sides
        up_sell = atm_strike + strike_step
        dn_sell = atm_strike - strike_step
        if up_sell in strikes and dn_sell in strikes:
            raw_prototypes.append(("BULL_CALL_SPREAD", [
                ("BUY", atm_strike, "CE"),
                ("SELL", up_sell, "CE")
            ]))
            raw_prototypes.append(("BEAR_PUT_SPREAD", [
                ("BUY", atm_strike, "PE"),
                ("SELL", dn_sell, "PE")
            ]))

    # 2. Build full TradeCandidate objects with multi-scenario repricing
    for st_name, leg_specs in raw_prototypes:
        legs: List[OptionLeg] = []
        valid_legs = True

        for action, k, opt_type in leg_specs:
            match = df[(df["strike"] == k) & (df["type"].str.startswith(opt_type[0]))]
            if match.empty:
                valid_legs = False
                break
            row = match.iloc[0]
            entry_p = float(row["ltp"])
            leg_iv = float(row["iv"])
            if entry_p <= 0.5:
                # Theoretical fallback if LTP missing
                entry_p = price(fut, k, T_entry, leg_iv, config.risk_free_rate, opt_type)

            legs.append(OptionLeg(
                strike=k,
                option_type=opt_type,
                action=action,
                quantity=1,
                entry_premium=entry_p,
                iv=leg_iv,
            ))

        if not valid_legs or not legs:
            continue

        # Reprice across Bull, Base, Bear
        scenario_targets: Dict[str, OptionTarget] = {}
        for sc in ["bull", "base", "bear"]:
            target_obj = underlying_targets.get(sc)
            iv_obj = iv_forecasts.get(sc)
            if not target_obj or not iv_obj:
                continue

            F_target = target_obj.target
            iv_target = iv_obj.iv_at_horizon

            # Composite repriced premium across legs
            total_entry = 0.0
            total_target = 0.0

            # Use primary leg for Greek attribution
            primary_leg = legs[0]
            primary_p_entry = primary_leg.entry_premium
            primary_p_target = reprice(
                F_new=F_target,
                K=primary_leg.strike,
                T_new=T_target,
                sigma_new=iv_target,
                r=config.risk_free_rate,
                option_type=primary_leg.option_type,
            )
            attr = greek_attribution(
                F_entry=fut,
                F_target=F_target,
                sigma_entry=primary_leg.iv,
                sigma_target=iv_target,
                T_entry=T_entry,
                T_target=T_target,
                K=primary_leg.strike,
                r=config.risk_free_rate,
                option_type=primary_leg.option_type,
            )
            g_entry = greeks(fut, primary_leg.strike, T_entry, primary_leg.iv, config.risk_free_rate, primary_leg.option_type)
            g_target = greeks(F_target, primary_leg.strike, T_target, iv_target, config.risk_free_rate, primary_leg.option_type)

            # Net structure PnL across all legs
            net_pnl = 0.0
            for leg in legs:
                leg_p_entry = leg.entry_premium
                leg_p_target = reprice(
                    F_new=F_target,
                    K=leg.strike,
                    T_new=T_target,
                    sigma_new=iv_target,
                    r=config.risk_free_rate,
                    option_type=leg.option_type,
                )
                leg_mult = 1.0 if leg.action.upper() == "BUY" else -1.0
                total_entry += leg_p_entry * leg_mult
                total_target += leg_p_target * leg_mult
                net_pnl += (leg_p_target - leg_p_entry) * leg_mult

            pnl_pct = (net_pnl / max(abs(total_entry), 1.0)) * 100.0

            scenario_targets[sc] = OptionTarget(
                strike=primary_leg.strike,
                option_type=primary_leg.option_type,
                scenario=sc,
                horizon_minutes=horizon_minutes,
                entry_premium=total_entry,
                target_premium=total_target,
                pnl_pts=net_pnl,
                pnl_pct=pnl_pct,
                greek_attribution=attr,
                entry_greeks=g_entry,
                target_greeks=g_target,
            )

        # Cost and payoffs
        breakevens, max_loss_pts, max_profit_pts = calculate_breakevens_and_payoff(st_name, legs)
        cost_inr = calculate_transaction_costs(legs, lot_size=lot_size, num_lots=1, cost_cfg=config.costs)
        ev_net_inr, rr = calculate_expected_value(scenario_set, scenario_targets, lot_size=lot_size, num_lots=1, cost_inr=cost_inr)

        max_loss_inr = max_loss_pts * lot_size + cost_inr
        max_profit_inr = (max_profit_pts * lot_size - cost_inr) if max_profit_pts is not None else None

        # Hard screening filters
        passes = True
        rejections: List[str] = []
        explain: List[str] = []

        # Filter: Risk-Reward
        if rr < config.min_rr:
            passes = False
            rejections.append(f"R:R ({rr:.2f}) below threshold ({config.min_rr:.2f})")

        # Filter: Breakeven reachability
        sigma_move = max(spot * 0.15 * math.sqrt(T_entry), 10.0)
        for be in breakevens:
            if abs(be - spot) > config.max_reachability_sigma * sigma_move:
                passes = False
                rejections.append(f"Breakeven {be:.0f} beyond reachability limit ({config.max_reachability_sigma:.1f}σ)")

        # Ranking score calculation
        # rank = 0.40 * EV_norm + 0.30 * RR_norm - 0.15 * max_loss_norm
        ev_norm = float(np.tanh(ev_net_inr / 2500.0))
        rr_norm = float(np.tanh(rr / 2.0))
        loss_norm = float(np.tanh(max_loss_pts / 100.0))
        rank_score = 0.45 * ev_norm + 0.35 * rr_norm - 0.20 * loss_norm

        explain.append(f"Structure: {st_name} on {dominant.upper()} view (Conviction: {scenario_set.conviction:.0%})")
        explain.append(f"Net EV: ₹{ev_net_inr:+,.0f} | R:R: {rr:.2f}:1 | Max Loss: ₹{max_loss_inr:,.0f}")

        candidates.append(TradeCandidate(
            structure_type=st_name,
            legs=legs,
            scenario_targets=scenario_targets,
            expected_value=ev_net_inr,
            risk_reward=rr,
            breakevens=breakevens,
            max_loss_pts=max_loss_pts,
            max_loss_inr=max_loss_inr,
            max_profit_pts=max_profit_pts,
            max_profit_inr=max_profit_inr,
            margin_est_inr=max_loss_inr,
            rank_score=rank_score,
            passes_filters=passes,
            rejection_reasons=rejections,
            explain=explain,
        ))

    # Sort candidates by rank_score descending
    candidates.sort(key=lambda c: c.rank_score, reverse=True)
    return candidates
