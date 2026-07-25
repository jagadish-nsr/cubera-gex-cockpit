import pytest
from analytics.stock_futures_gauge import rolling_percentile_rank, compute_gauge_metrics

def test_rolling_percentile_rank_extremes():
    # Create a synthetic 60-day history with values from 0.01 to 0.60
    history = [i/100.0 for i in range(1, 61)]
    
    # A value lower than anything in history should be 0th percentile
    assert rolling_percentile_rank(0.00, history) == 0
    
    # A value higher than anything in history should be 100th percentile
    assert rolling_percentile_rank(1.00, history) == 100
    
    # A value in the exact middle
    assert rolling_percentile_rank(0.30, history) == 50

def test_rolling_percentile_rank_cold_start():
    # Only 19 days of history
    history = [0.1] * 19
    assert rolling_percentile_rank(0.2, history) == "insufficient_history"
    
    # 20 days of history works
    history = [0.1] * 20
    assert isinstance(rolling_percentile_rank(0.2, history), int)

def test_gauge_metrics_scale_invariance():
    # If all participants double their stock futures OI, the pct_of_total_oi should remain identical.
    base_today = {
        "Client": {"fut_stk_long": 100, "fut_stk_short": 50},  # net 50
        "DII":    {"fut_stk_long": 200, "fut_stk_short": 50},  # net 150
        "FII":    {"fut_stk_long": 300, "fut_stk_short": 50},  # net 250
        "Pro":    {"fut_stk_long": 400, "fut_stk_short": 50},  # net 350
    } # Total long = 1000
    
    scaled_today = {
        "Client": {"fut_stk_long": 200, "fut_stk_short": 100}, # net 100
        "DII":    {"fut_stk_long": 400, "fut_stk_short": 100}, # net 300
        "FII":    {"fut_stk_long": 600, "fut_stk_short": 100}, # net 500
        "Pro":    {"fut_stk_long": 800, "fut_stk_short": 100}, # net 700
    } # Total long = 2000
    
    # We pass empty history so percentiles return insufficient_history
    base_res = compute_gauge_metrics(base_today, None, {})
    scaled_res = compute_gauge_metrics(scaled_today, None, {})
    
    base_pcts = {r["category"]: r["pct_of_total_oi"] for r in base_res}
    scaled_pcts = {r["category"]: r["pct_of_total_oi"] for r in scaled_res}
    
    assert base_pcts["Client"] == 50 / 1000 == 0.05
    assert base_pcts["Client"] == scaled_pcts["Client"]
    assert base_pcts["DII"] == 150 / 1000 == 0.15
    assert base_pcts["DII"] == scaled_pcts["DII"]
    assert base_pcts["FII"] == 250 / 1000 == 0.25
    assert base_pcts["FII"] == scaled_pcts["FII"]
    assert base_pcts["Pro"] == 350 / 1000 == 0.35
    assert base_pcts["Pro"] == scaled_pcts["Pro"]
