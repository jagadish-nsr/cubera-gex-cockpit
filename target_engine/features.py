"""T1 Point-in-Time Feature Extraction Engine.

Extracts normalized features F1 to F18 from market snapshots and history.
Strict convention: +1.0 = Bullish pressure, -1.0 = Bearish pressure.
Non-directional features provide volatility and regime inputs for T4 and conviction C.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from target_engine.config import DEFAULT_CONFIG, TargetEngineConfig
from target_engine.contracts import FeatureVector
from target_engine.quality import validate_snapshot_quality
from target_engine.realized_vol import compute_close_to_close_rv
from target_engine.time_utils import calculate_fractional_t, to_ist
from target_engine.walls import OptionWall, detect_walls


def extract_features(
    spot: float,
    fut: float,
    atm_iv: float,
    expiry: str,
    contracts_df: Optional[pd.DataFrame] = None,
    history_df: Optional[pd.DataFrame] = None,
    oi_volume_agg: Optional[Dict[str, Any]] = None,
    vwap: Optional[float] = None,
    day_high: Optional[float] = None,
    day_low: Optional[float] = None,
    max_pain: Optional[float] = None,
    skew_25d: Optional[float] = None,
    config: TargetEngineConfig = DEFAULT_CONFIG,
    now: Optional[datetime] = None,
) -> FeatureVector:
    """Extract full normalized FeatureVector (F1–F18) at a single point in time.

    Guarantees no lookahead bias: uses only current snapshot and past history.
    """
    ts = to_ist(now)
    dte_years = calculate_fractional_t(ts, expiry, day_count=config.day_count)
    dte_days = dte_years * config.day_count

    # 1-day expected sigma move for normalization
    # sigma_1d = spot * atm_iv * sqrt(1/365)
    effective_iv = max(atm_iv, 0.05) if atm_iv > 0 else 0.15
    sigma_1d = max(spot * effective_iv * math.sqrt(1.0 / config.day_count), 20.0)

    # 1. Detect walls from contracts
    call_walls, put_walls = detect_walls(
        contracts_df=contracts_df,
        spot=spot,
        strike_window=config.wall_strike_window,
        min_oi_share=config.wall_min_oi_share,
    )

    nearest_call_wall = call_walls[0] if call_walls else None
    nearest_put_wall = put_walls[0] if put_walls else None

    # F1 / F2: strike proximity is a map, not a direction.
    # A nearer call-gamma strike is not bearish resistance.
    # A nearer put-gamma strike is not bullish support.
    _ = nearest_call_wall
    f1 = 0.0
    f2 = 0.0

    # F3: Wall momentum: OI change at walls
    # Call wall OI falling = bullish (+), Put wall OI falling = bearish (-)
    f3 = 0.0
    if nearest_call_wall:
        # If call OI change is negative (unwinding), it's bullish (+)
        call_oi_chg = nearest_call_wall.oi_change
        f3 += float(np.tanh(-call_oi_chg / 500000.0)) * 0.5
    if nearest_put_wall:
        # If put OI change is negative (unwinding), it's bearish (-)
        put_oi_chg = nearest_put_wall.oi_change
        f3 += float(np.tanh(put_oi_chg / 500000.0)) * 0.5
    f3 = float(np.clip(f3, -1.0, 1.0))

    # F4: Aggregate buildup from options OI
    # Long buildup / short covering on calls (+), Put additions (+)
    f4 = 0.0
    if oi_volume_agg:
        call_chg = float(oi_volume_agg.get("call_oi_change", 0.0))
        put_chg = float(oi_volume_agg.get("put_oi_change", 0.0))
        # More puts added than calls is bullish support
        diff = put_chg - call_chg
        f4 = float(np.tanh(diff / 2000000.0))
    elif contracts_df is not None and not contracts_df.empty:
        c_chg = contracts_df[contracts_df["type"].str.upper().str.startswith("C")]["change_oi"].sum() if "change_oi" in contracts_df.columns else 0.0
        p_chg = contracts_df[contracts_df["type"].str.upper().str.startswith("P")]["change_oi"].sum() if "change_oi" in contracts_df.columns else 0.0
        f4 = float(np.tanh((p_chg - c_chg) / 2000000.0))

    # F5: PCR level vs history (z-score)
    pcr_now = 1.0
    if oi_volume_agg and "pcr_oi" in oi_volume_agg:
        pcr_now = float(oi_volume_agg["pcr_oi"])
    elif contracts_df is not None and not contracts_df.empty:
        c_oi = contracts_df[contracts_df["type"].str.upper().str.startswith("C")]["oi"].sum()
        p_oi = contracts_df[contracts_df["type"].str.upper().str.startswith("P")]["oi"].sum()
        pcr_now = p_oi / max(c_oi, 1.0)

    f5 = float(np.tanh((pcr_now - 1.0) / 0.4))
    if config.scoring.pcr_mode == "contrarian":
        f5 = -f5
    elif config.scoring.pcr_mode == "neutral":
        f5 *= 0.5

    # F6: PCR trend slope
    f6 = 0.0
    if history_df is not None and "pcr_oi" in history_df.columns and len(history_df) >= 3:
        pcr_series = history_df["pcr_oi"].tail(10).dropna()
        if len(pcr_series) >= 2:
            slope = float(np.polyfit(np.arange(len(pcr_series)), pcr_series.values, 1)[0])
            f6 = float(np.tanh(slope * 50.0))

    # F7: PCR of OI change
    f7 = 0.0
    if oi_volume_agg:
        call_chg = max(0.1, float(oi_volume_agg.get("call_oi_change", 0.0)))
        put_chg = max(0.1, float(oi_volume_agg.get("put_oi_change", 0.0)))
        ratio = put_chg / call_chg
        f7 = float(np.tanh(math.log(max(ratio, 0.01)) * 0.8))

    # F8: Futures buildup
    fut_basis = fut - spot
    f8 = float(np.tanh(fut_basis / (0.5 * sigma_1d)))

    # F9: Futures basis change
    f9 = float(np.tanh(fut_basis / 25.0))

    # F10: Market depth imbalance (stubbed to 0.0 if unavailable)
    f10 = 0.0

    # F11: Volume burst
    f11 = 0.0
    if day_high and day_low and day_high > day_low:
        pos_in_range = (spot - day_low) / (day_high - day_low)
        # Centered around 0.5 (-1 to +1)
        f11 = float(np.tanh((pos_in_range - 0.5) * 3.0))

    # F12: ATM straddle change (non-directional)
    f12 = 0.0

    # F13: ATM IV change (non-directional)
    f13 = float(effective_iv)

    # F14: Skew change (put skew rising = bearish demand)
    f14 = 0.0
    if skew_25d is not None:
        # 25-delta put IV minus call IV. Higher put skew = bearish (-)
        f14 = -float(np.tanh(skew_25d / 3.0))

    # F15: max pain is the settlement strike. Distance to it is not a directional pull.
    _ = (max_pain, dte_days)
    f15 = 0.0

    # F16: Intraday trend (spot vs VWAP)
    f16 = 0.0
    if vwap and vwap > 0:
        vwap_diff = spot - vwap
        f16 = float(np.tanh(vwap_diff / (0.5 * sigma_1d)))

    # F17: Realized vs Implied Volatility (non-directional)
    f17 = 1.0
    if history_df is not None and "spot" in history_df.columns and len(history_df) >= 10:
        rv = compute_close_to_close_rv(history_df["spot"].values)
        f17 = rv / max(effective_iv, 0.01)

    # F18: Event proximity (non-directional hours to event)
    f18 = 48.0  # Default 48 hours

    features = {
        "F1_call_wall_prox": f1,
        "F2_put_wall_prox": f2,
        "F3_wall_momentum": f3,
        "F4_aggregate_buildup": f4,
        "F5_pcr_level": f5,
        "F6_pcr_trend": f6,
        "F7_pcr_oi_change": f7,
        "F8_futures_buildup": f8,
        "F9_futures_basis": f9,
        "F10_depth_imbalance": f10,
        "F11_volume_burst": f11,
        "F12_straddle_change": f12,
        "F13_atm_iv": f13,
        "F14_skew_change": f14,
        "F15_max_pain_pull": f15,
        "F16_intraday_trend": f16,
        "F17_rv_vs_iv": f17,
        "F18_event_proximity": f18,
    }

    # Quality flags inspection
    n_contracts = len(contracts_df) if contracts_df is not None else 0
    flags = validate_snapshot_quality(
        n_contracts=n_contracts,
        staleness_seconds=0.0,
        dte_calendar_days=dte_days,
        has_depth=False,
    )

    return FeatureVector(
        ts=ts,
        expiry=expiry,
        dte=dte_days,
        spot=spot,
        fut=fut,
        features=features,
        quality_flags=flags,
    )
