"""T4 Implied Volatility Forecasting Engine.

Estimates scenario-conditioned ATM implied volatility at the target horizon
by combining mean-reversion, spot-vol correlation (skew dynamics), and event premiums.
"""

from __future__ import annotations

import math
from typing import Dict, Optional
import numpy as np

from target_engine.config import DEFAULT_CONFIG, TargetEngineConfig
from target_engine.contracts import IVForecast
from target_engine.time_utils import get_horizon_annual_duration


def forecast_iv(
    scenario: str,
    horizon_minutes: int,
    iv_now: float,
    spot_move_pct: float = 0.0,
    longrun_iv: float = 0.15,
    event_ahead: bool = False,
    event_passed_inside_horizon: bool = False,
    config: TargetEngineConfig = DEFAULT_CONFIG,
) -> IVForecast:
    """Forecast ATM implied volatility at horizon for a given scenario.

    Parameters
    ----------
    scenario : str
        "bull", "base", or "bear".
    horizon_minutes : int
        Horizon duration in minutes.
    iv_now : float
        Current ATM implied volatility as decimal (e.g. 0.15 for 15%).
    spot_move_pct : float
        Expected underlying move in percentage (e.g. +0.01 for +1%).
    longrun_iv : float
        Trailing median baseline IV (default 0.15).
    """
    iv_cfg = config.iv_forecast
    h_years = get_horizon_annual_duration(horizon_minutes, day_count=config.day_count)
    h_days = h_years * config.day_count

    # 1. Mean-reversion component
    # kappa * (longrun - iv_now) * (h_days / 30)
    mr_diff = longrun_iv - iv_now
    delta_mr = iv_cfg.kappa * mr_diff * min(h_days / 7.0, 1.0)

    # 2. Spot-move component (Spot-Vol correlation)
    # Down moves increase vol (beta_down > 0), up moves compress vol (beta_up < 0)
    if spot_move_pct < 0.0:
        delta_spot = iv_cfg.beta_down * abs(spot_move_pct)
    else:
        delta_spot = iv_cfg.beta_up * spot_move_pct

    # 3. Event component
    delta_event = 0.0
    if event_ahead:
        delta_event += iv_cfg.event_pre_drift_pts / 100.0
    if event_passed_inside_horizon:
        delta_event -= iv_cfg.event_post_crush_pts / 100.0

    # Total forecasted IV
    total_delta = delta_mr + delta_spot + delta_event
    iv_at_horizon = float(np.clip(iv_now + total_delta, 0.04, 1.20))

    components = {
        "mean_reversion": float(delta_mr),
        "spot_move": float(delta_spot),
        "event": float(delta_event),
        "total_delta": float(total_delta),
    }

    # Sensitivity table: IV at +/- 1 pt and +/- 2 pts
    sensitivity_table = {
        "-2pts": max(0.04, iv_at_horizon - 0.02),
        "-1pt": max(0.04, iv_at_horizon - 0.01),
        "base": iv_at_horizon,
        "+1pt": min(1.20, iv_at_horizon + 0.01),
        "+2pts": min(1.20, iv_at_horizon + 0.02),
    }

    return IVForecast(
        scenario=scenario,
        horizon_minutes=horizon_minutes,
        iv_now=iv_now,
        iv_at_horizon=iv_at_horizon,
        method="rule_based",
        components=components,
        sensitivity_table=sensitivity_table,
        unreliable=False,
    )
