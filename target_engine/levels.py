"""T3 Underlying Target and Level Engine.

Computes scenario-based target levels, stops, and theoretical touch and close
probabilities using the reflection principle and structural resistance/support walls.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.stats import norm

from target_engine.config import DEFAULT_CONFIG, TargetEngineConfig
from target_engine.contracts import UnderlyingTarget
from target_engine.time_utils import get_horizon_annual_duration
from target_engine.walls import OptionWall


def compute_touch_probability(
    distance: float,
    sigma_h: float,
    drift: float = 0.0,
) -> float:
    """Calculate probability of touching a barrier at distance d within horizon h.

    Uses the reflection principle: P(touch) = 2 * (1 - N(d / sigma_h)),
    adjusted for scenario drift.
    """
    if sigma_h <= 1e-6:
        return 0.0 if distance > 0 else 1.0

    d = max(0.1, abs(distance) - drift)
    z = d / sigma_h
    # Reflection principle gives 2 * (1 - N(z))
    p_touch = 2.0 * (1.0 - norm.cdf(z))
    return float(np.clip(p_touch, 0.01, 0.99))


def compute_close_probability(
    distance: float,
    sigma_h: float,
    drift: float = 0.0,
) -> float:
    """Calculate probability of price closing beyond distance d at horizon end."""
    if sigma_h <= 1e-6:
        return 0.0 if distance > 0 else 1.0

    d = max(0.1, abs(distance) - drift)
    z = d / sigma_h
    p_close = 1.0 - norm.cdf(z)
    return float(np.clip(p_close, 0.005, 0.95))


def generate_underlying_targets(
    spot: float,
    fut: float,
    atm_iv: float,
    horizon_minutes: int,
    call_walls: List[OptionWall],
    put_walls: List[OptionWall],
    day_high: Optional[float] = None,
    day_low: Optional[float] = None,
    max_pain: Optional[float] = None,
    dte_days: float = 5.0,
    config: TargetEngineConfig = DEFAULT_CONFIG,
) -> Dict[str, UnderlyingTarget]:
    """Generate UnderlyingTarget for Bull, Base, and Bear scenarios for given horizon.

    Parameters
    ----------
    spot, fut : float
        Current spot and futures price.
    atm_iv : float
        Current ATM implied volatility as decimal.
    horizon_minutes : int
        Horizon duration (30, 120, 375 minutes).
    call_walls, put_walls : List[OptionWall]
        Identified call resistance and put support walls.
    """
    h_years = get_horizon_annual_duration(horizon_minutes, day_count=config.day_count)
    effective_iv = max(atm_iv, 0.05) if atm_iv > 0 else 0.15

    # 1-SD horizon expected move
    sigma_h = max(fut * effective_iv * math.sqrt(h_years), 15.0)

    # Wall references
    nearest_call = call_walls[0].strike if call_walls else None
    nearest_put = put_walls[0].strike if put_walls else None

    # Drift assumptions per scenario in units of sigma_h
    bull_drift = 0.3 * sigma_h
    bear_drift = 0.3 * sigma_h

    # -------------------------------------------------------------
    # 1. BULL SCENARIO TARGET & STOP
    # -------------------------------------------------------------
    bull_candidates: List[Tuple[float, str]] = []
    if nearest_call and nearest_call > spot:
        bull_candidates.append((nearest_call, f"Call Wall at {int(nearest_call)}"))
    bull_candidates.append((fut + 0.6 * sigma_h, f"0.6-Sigma Move ({fut + 0.6 * sigma_h:.0f})"))
    if day_high and day_high > spot:
        bull_candidates.append((day_high, f"Day High ({int(day_high)})"))
    if max_pain and max_pain > spot and dte_days <= 1.0:
        bull_candidates.append((max_pain, f"Max Pain Pin ({int(max_pain)})"))

    # Pick nearest credible target above spot, capped at fut + 2*sigma_h
    bull_target_val, bull_reason = min(bull_candidates, key=lambda c: c[0])
    bull_target_val = min(bull_target_val, fut + 2.0 * sigma_h)

    # Bull stop: below put wall or fut - 0.5*sigma_h
    if nearest_put and nearest_put < spot:
        bull_stop_val = min(nearest_put - 10.0, fut - 0.4 * sigma_h)
    else:
        bull_stop_val = fut - 0.5 * sigma_h
    bull_stop_val = max(bull_stop_val, fut - 1.5 * sigma_h)

    bull_target = UnderlyingTarget(
        scenario="bull",
        horizon_minutes=horizon_minutes,
        target=bull_target_val,
        stop=bull_stop_val,
        p_touch_target=compute_touch_probability(bull_target_val - fut, sigma_h, bull_drift),
        p_touch_stop=compute_touch_probability(fut - bull_stop_val, sigma_h, -0.2 * sigma_h),
        p_close_beyond=compute_close_probability(bull_target_val - fut, sigma_h, bull_drift),
        reason=bull_reason,
    )

    # -------------------------------------------------------------
    # 2. BEAR SCENARIO TARGET & STOP
    # -------------------------------------------------------------
    bear_candidates: List[Tuple[float, str]] = []
    if nearest_put and nearest_put < spot:
        bear_candidates.append((nearest_put, f"Put Wall at {int(nearest_put)}"))
    bear_candidates.append((fut - 0.6 * sigma_h, f"0.6-Sigma Move ({fut - 0.6 * sigma_h:.0f})"))
    if day_low and day_low < spot:
        bear_candidates.append((day_low, f"Day Low ({int(day_low)})"))
    if max_pain and max_pain < spot and dte_days <= 1.0:
        bear_candidates.append((max_pain, f"Max Pain Pin ({int(max_pain)})"))

    # Pick nearest credible target below spot, capped at fut - 2*sigma_h
    bear_target_val, bear_reason = max(bear_candidates, key=lambda c: c[0])
    bear_target_val = max(bear_target_val, fut - 2.0 * sigma_h)

    # Bear stop: above call wall or fut + 0.5*sigma_h
    if nearest_call and nearest_call > spot:
        bear_stop_val = max(nearest_call + 10.0, fut + 0.4 * sigma_h)
    else:
        bear_stop_val = fut + 0.5 * sigma_h
    bear_stop_val = min(bear_stop_val, fut + 1.5 * sigma_h)

    bear_target = UnderlyingTarget(
        scenario="bear",
        horizon_minutes=horizon_minutes,
        target=bear_target_val,
        stop=bear_stop_val,
        p_touch_target=compute_touch_probability(fut - bear_target_val, sigma_h, bear_drift),
        p_touch_stop=compute_touch_probability(bear_stop_val - fut, sigma_h, -0.2 * sigma_h),
        p_close_beyond=compute_close_probability(fut - bear_target_val, sigma_h, bear_drift),
        reason=bear_reason,
    )

    # -------------------------------------------------------------
    # 3. BASE (RANGE) SCENARIO TARGET & STOP
    # -------------------------------------------------------------
    if nearest_call and nearest_put and nearest_call > nearest_put:
        base_target_val = 0.5 * (nearest_call + nearest_put)
        base_reason = f"Midpoint of {int(nearest_put)} Put & {int(nearest_call)} Call Walls"
        base_stop_val = nearest_call + 0.3 * sigma_h
    else:
        base_target_val = fut
        base_reason = "Current Forward Equilibrium"
        base_stop_val = fut + 0.8 * sigma_h

    base_target = UnderlyingTarget(
        scenario="base",
        horizon_minutes=horizon_minutes,
        target=base_target_val,
        stop=base_stop_val,
        p_touch_target=compute_touch_probability(abs(base_target_val - fut), sigma_h, 0.0),
        p_touch_stop=compute_touch_probability(abs(base_stop_val - fut), sigma_h, 0.0),
        p_close_beyond=0.45,
        reason=base_reason,
    )

    return {
        "bull": bull_target,
        "base": base_target,
        "bear": bear_target,
    }
