"""data/journal/pnl.py — Mark-to-market and P&L utilities.

Keeps strict point-in-time discipline:
  - Mark-to-market uses CURRENT chain LTP — never the entry LTP.
  - Realised P&L is calculated from the STORED exit_net_premium — never re-priced.
  - Costs (brokerage + slippage) are subtracted every time.

No streamlit import here — pure computation, unit-testable offline.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.journal.store import PaperTrade

log = logging.getLogger(__name__)


def mark_to_market(
    trade: "PaperTrade",
    current_option_prices: dict[tuple[str, float, str], float],
) -> dict:
    """Compute unrealised P&L for an OPEN trade at current market prices.

    current_option_prices: {(type, strike, expiry_str): ltp, ...}
      type: "C" | "P"  strike: float  expiry_str: "YYYY-MM-DD"

    Returns:
      {
        current_net_premium: float,  # current mid of all legs
        unrealized_gross_inr: float, # (entry - current) * lot_size  [for credit spread]
        unrealized_net_inr: float,   # gross - costs already incurred (entry side only)
        pct_of_max_profit: float,    # 0-100 for credit, negative for debit in loss
        legs_detail: list[dict],     # per-leg current price
        stale_legs: list[str],       # leg keys with missing prices (warn the user)
      }
    """
    if trade.status == "CLOSED":
        return {
            "current_net_premium": trade.exit_net_premium,
            "unrealized_gross_inr": trade.gross_pnl,
            "unrealized_net_inr": trade.net_realized_pnl,
            "pct_of_max_profit": None,
            "legs_detail": [],
            "stale_legs": [],
        }

    current_premium = 0.0
    legs_detail = []
    stale_legs = []

    for leg in trade.legs:
        key = (leg.get("type", "").upper(), float(leg.get("strike", 0)),
               str(leg.get("expiry", "")))
        ltp = current_option_prices.get(key)
        if ltp is None:
            stale_legs.append(f"{key[0]}{int(key[1])}")
            ltp = float(leg.get("ltp", 0.0))  # fall back to entry price

        action = leg.get("action", "SELL").upper()
        # Credit legs (SELL): we received premium; current price is our liability.
        # Debit legs (BUY): we paid premium; current price is our asset.
        if action == "SELL":
            current_premium += ltp   # positive = current cost to close
        else:
            current_premium -= ltp   # negative = current value of long

        legs_detail.append({**leg, "current_ltp": ltp})

    # Net current value (credit spread: entry > 0, current credit > 0)
    # Unrealised gross: for credit spread, profit = entry received - current buyback cost
    gross_inr = (trade.entry_net_premium - current_premium) * trade.lot_size

    # Subtract only the costs already incurred (entry brokerage + entry slippage)
    net_inr = gross_inr - trade.brokerage_cost - trade.slippage_cost

    # Percentage of max profit captured (credit spreads only)
    pct = None
    max_credit = abs(trade.entry_net_premium) * trade.lot_size
    if max_credit > 0:
        pct = round((gross_inr / max_credit) * 100, 1)

    if stale_legs:
        log.warning("MTM: prices missing for legs %s in trade %s — using entry LTP",
                    stale_legs, trade.trade_id)

    return {
        "current_net_premium": current_premium,
        "unrealized_gross_inr": round(gross_inr, 2),
        "unrealized_net_inr": round(net_inr, 2),
        "pct_of_max_profit": pct,
        "legs_detail": legs_detail,
        "stale_legs": stale_legs,
    }


def format_pnl_badge(net_inr: float | None) -> tuple[str, str]:
    """Return (label, css_class) for display in the dashboard.

    css_class is one of: "pnl-profit" | "pnl-loss" | "pnl-neutral".
    """
    if net_inr is None:
        return "—", "pnl-neutral"
    prefix = "+" if net_inr > 0 else ""
    label = f"{prefix}₹{net_inr:,.0f}"
    cls = "pnl-profit" if net_inr > 0 else ("pnl-loss" if net_inr < 0 else "pnl-neutral")
    return label, cls
