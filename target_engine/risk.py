"""T7 Risk, Reward, Cost Modeling, and Sizing Engine.

Calculates realistic trading transaction costs (NSE derivatives schedule),
breakeven levels, maximum loss/profit, probability-weighted Expected Value (EV),
and lot sizing per risk budget.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import config as app_config
from target_engine.config import CostConfig, DEFAULT_CONFIG, TargetEngineConfig
from target_engine.contracts import OptionLeg, OptionTarget, ScenarioSet, TradeCandidate


def calculate_transaction_costs(
    legs: List[OptionLeg],
    lot_size: int,
    num_lots: int = 1,
    cost_cfg: CostConfig = DEFAULT_CONFIG.costs,
) -> float:
    """Calculate total round-trip transaction costs in INR for a multi-leg structure.

    Schedule:
    - Brokerage: flat per executed order
    - STT: 0.125% on sell option turnover (premium * quantity)
    - Exchange turnover charges: 0.053%
    - GST: 18% on (brokerage + exchange charges)
    - Stamp duty: 0.003% on buy turnover
    - Slippage: index points per leg * quantity
    """
    total_qty = lot_size * num_lots
    total_cost = 0.0

    num_orders = len(legs) * 2  # Entry and exit for each leg
    brokerage = num_orders * cost_cfg.brokerage_per_order

    turnover_sell = 0.0
    turnover_buy = 0.0

    for leg in legs:
        premium = max(leg.entry_premium, 1.0)
        leg_turnover = premium * total_qty
        if leg.action.upper() == "SELL":
            turnover_sell += leg_turnover
            turnover_buy += leg_turnover  # Buy back on exit
        else:
            turnover_buy += leg_turnover
            turnover_sell += leg_turnover  # Sell to close

    stt = turnover_sell * cost_cfg.stt_pct
    exchange_charges = (turnover_buy + turnover_sell) * cost_cfg.exchange_charges_pct
    gst = (brokerage + exchange_charges) * cost_cfg.gst_pct
    stamp_duty = turnover_buy * cost_cfg.stamp_duty_pct
    slippage = len(legs) * cost_cfg.slippage_pts * total_qty

    total_cost = brokerage + stt + exchange_charges + gst + stamp_duty + slippage
    return float(total_cost)


def calculate_breakevens_and_payoff(
    structure_type: str,
    legs: List[OptionLeg],
) -> Tuple[List[float], float, Optional[float]]:
    """Calculate breakeven strike levels, maximum loss in points, and maximum profit in points.

    Returns (breakevens_list, max_loss_pts, max_profit_pts).
    """
    st = structure_type.upper()

    if st in ("LONG_CALL", "BUY_CALL") and len(legs) == 1:
        leg = legs[0]
        be = leg.strike + leg.entry_premium
        max_loss = leg.entry_premium
        return [be], max_loss, None  # Unlimited upside

    elif st in ("LONG_PUT", "BUY_PUT") and len(legs) == 1:
        leg = legs[0]
        be = leg.strike - leg.entry_premium
        max_loss = leg.entry_premium
        max_profit = leg.strike - leg.entry_premium
        return [be], max_loss, max_profit

    elif st in ("BULL_CALL_SPREAD",) and len(legs) == 2:
        # Buy lower call, sell higher call
        buy_leg = [l for l in legs if l.action.upper() == "BUY"][0]
        sell_leg = [l for l in legs if l.action.upper() == "SELL"][0]
        net_debit = buy_leg.entry_premium - sell_leg.entry_premium
        width = abs(sell_leg.strike - buy_leg.strike)
        be = min(buy_leg.strike, sell_leg.strike) + net_debit
        max_loss = max(net_debit, 0.1)
        max_profit = max(width - net_debit, 0.0)
        return [be], max_loss, max_profit

    elif st in ("BEAR_PUT_SPREAD",) and len(legs) == 2:
        # Buy higher put, sell lower put
        buy_leg = [l for l in legs if l.action.upper() == "BUY"][0]
        sell_leg = [l for l in legs if l.action.upper() == "SELL"][0]
        net_debit = buy_leg.entry_premium - sell_leg.entry_premium
        width = abs(sell_leg.strike - buy_leg.strike)
        be = max(buy_leg.strike, sell_leg.strike) - net_debit
        max_loss = max(net_debit, 0.1)
        max_profit = max(width - net_debit, 0.0)
        return [be], max_loss, max_profit

    # Fallback generic multi-leg net premium
    net_premium = sum(l.entry_premium * (1 if l.action.upper() == "BUY" else -1) for l in legs)
    max_loss = max(abs(net_premium), 50.0)
    return [legs[0].strike], max_loss, max_loss * 2.0


def calculate_expected_value(
    scenario_set: ScenarioSet,
    scenario_targets: Dict[str, OptionTarget],
    lot_size: int = 65,
    num_lots: int = 1,
    cost_inr: float = 0.0,
) -> Tuple[float, float]:
    """Calculate probability-weighted Expected Value (EV) in INR and risk-reward ratio.

    EV = P(bull)*PnL_bull + P(base)*PnL_base + P(bear)*PnL_bear - Cost.
    """
    total_qty = lot_size * num_lots
    p_bull = scenario_set.get_prob("bull")
    p_base = scenario_set.get_prob("base")
    p_bear = scenario_set.get_prob("bear")

    pnl_bull_inr = (scenario_targets.get("bull").pnl_pts if "bull" in scenario_targets else 0.0) * total_qty
    pnl_base_inr = (scenario_targets.get("base").pnl_pts if "base" in scenario_targets else 0.0) * total_qty
    pnl_bear_inr = (scenario_targets.get("bear").pnl_pts if "bear" in scenario_targets else 0.0) * total_qty

    ev_gross_inr = (p_bull * pnl_bull_inr) + (p_base * pnl_base_inr) + (p_bear * pnl_bear_inr)
    ev_net_inr = ev_gross_inr - cost_inr

    # Risk-Reward ratio: max potential profit vs max potential loss
    gains = [pnl for pnl in [pnl_bull_inr, pnl_base_inr, pnl_bear_inr] if pnl > 0]
    losses = [abs(pnl) for pnl in [pnl_bull_inr, pnl_base_inr, pnl_bear_inr] if pnl < 0]

    max_gain = max(gains) if gains else 1.0
    max_loss = max(losses) if losses else 1.0
    rr_ratio = max_gain / max(max_loss, 1.0)

    return float(ev_net_inr), float(rr_ratio)


def compute_recommended_sizing(
    max_loss_per_lot_inr: float,
    risk_budget_inr: float = 15000.0,
    max_lots_cap: int = 10,
) -> int:
    """Calculate position size in lots conforming to fixed risk capital budget."""
    if max_loss_per_lot_inr <= 0.0:
        return 1
    lots = int(risk_budget_inr // max_loss_per_lot_inr)
    return max(1, min(lots, max_lots_cap))
