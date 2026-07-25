"""Stock Futures Risk-Appetite Gauge.

A secondary confidence filter built from the aggregate Future Stock Long/Short columns.
"""

from __future__ import annotations
import scipy.stats as stats

def _parse_int(val) -> int:
    try:
        return int(float(val)) if val is not None else 0
    except (ValueError, TypeError):
        return 0

def rolling_percentile_rank(current_val: float, history: list[float], window: int = 60, min_history: int = 20) -> int | str:
    """Compute the percentile rank (0-100) of current_val against the historical window.
    If the available history length is < min_history, returns 'insufficient_history'.
    """
    recent = history[-window:] if history else []
    if len(recent) < min_history:
        return "insufficient_history"
    
    return int(round(stats.percentileofscore(recent, current_val, kind='rank')))

def compute_gauge_metrics(today_parsed: dict, yesterday_parsed: dict | None, historical_pcts: dict[str, list[float]]) -> list[dict]:
    """Compute the risk-appetite gauge metrics for all categories.
    
    today_parsed: dict of {category: {fields}}
    yesterday_parsed: dict of {category: {fields}} (can be None)
    historical_pcts: dict mapping category -> list of historical pct_of_total_oi values (excluding today)
    """
    if not today_parsed:
        return []
        
    categories = ["Client", "DII", "FII", "Pro"]
    
    # Calculate Total Stock Futures OI for today (sum of Future Stock Long across all valid categories)
    total_oi_today = sum(_parse_int(today_parsed.get(c, {}).get("fut_stk_long")) for c in categories if c in today_parsed)
    
    out = []
    for c in categories:
        if c not in today_parsed:
            continue
            
        t_vals = today_parsed[c]
        long_tdy = _parse_int(t_vals.get("fut_stk_long"))
        short_tdy = _parse_int(t_vals.get("fut_stk_short"))
        net_tdy = long_tdy - short_tdy
        pct_tdy = (net_tdy / total_oi_today) if total_oi_today > 0 else 0.0
        
        # Calculate Delta
        delta = 0
        if yesterday_parsed and c in yesterday_parsed:
            y_vals = yesterday_parsed[c]
            long_ydy = _parse_int(y_vals.get("fut_stk_long"))
            short_ydy = _parse_int(y_vals.get("fut_stk_short"))
            net_ydy = long_ydy - short_ydy
            delta = net_tdy - net_ydy
            
        rank = rolling_percentile_rank(pct_tdy, historical_pcts.get(c, []))
        
        out.append({
            "category": c,
            "net_fut_stock": net_tdy,
            "pct_of_total_oi": pct_tdy,
            "percentile_rank_60d": rank,
            "day_over_day_delta": delta
        })
        
    return out
