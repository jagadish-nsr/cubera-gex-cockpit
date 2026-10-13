"""T0 Configuration module for Target Engine.

Defines all tunable parameters, thresholds, cost models, and feature weights.
Follows the project's explicit dataclass configuration pattern with no hardcoding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class LiquidityConfig:
    """Thresholds for screening liquid options strikes."""
    min_oi: int = 5000
    max_spread_pct: float = 5.0


@dataclass
class CostConfig:
    """Indian equity derivatives cost model (NSE Index Options)."""
    brokerage_per_order: float = 20.0       # Standard discount broker flat fee (INR)
    stt_pct: float = 0.00125               # 0.125% on sell turnover (options premium)
    exchange_charges_pct: float = 0.00053  # NSE turnover fee (0.053%)
    gst_pct: float = 0.18                  # 18% GST on brokerage + exchange charges
    sebi_charges_per_crore: float = 10.0   # SEBI turnover fee (INR 10 / crore)
    stamp_duty_pct: float = 0.00003        # Stamp duty on buy turnover (0.003%)
    slippage_pts: float = 0.5              # Slippage in index points per leg round-trip


@dataclass
class MarketHoursConfig:
    """Trading session timings for Indian National Stock Exchange."""
    open_time: str = "09:15"
    close_time: str = "15:30"
    tz: str = "Asia/Kolkata"
    minutes_per_session: int = 375


@dataclass
class IVForecastConfig:
    """Parameters for T4 rule-based and empirical IV forecasting."""
    kappa: float = 0.05                    # Mean reversion speed toward trailing median
    trailing_days_longrun: int = 60        # Long-run trailing window for baseline IV
    beta_down: float = 0.35                # IV expansion coefficient on negative spot returns
    beta_up: float = -0.15                 # IV compression coefficient on positive spot returns
    surface_convention: str = "sticky_delta"  # "sticky_delta" or "sticky_strike"
    post_spike_crush_threshold_pct: float = 10.0  # Daily IV spike threshold triggering crush bias
    event_pre_drift_pts: float = 1.5       # Vol build-up before major event (IV points)
    event_post_crush_pts: float = 2.5      # Vol crush immediately following major event


@dataclass
class ScoringParams:
    """T2 Scenario scoring softmax parameters."""
    a: float = 1.5                         # Scale for directional score in bull/bear logits
    b: float = 0.0                         # Base intercept for base (range) logit
    c: float = 1.0                         # Uncertainty weight in base logit (1 - C)
    d: float = 1.2                         # Range regime weight in base logit
    min_conviction: float = 0.35           # Below this, no_edge is declared
    pcr_mode: str = "neutral"              # "trend", "contrarian", or "neutral"


@dataclass
class TargetEngineConfig:
    """Master configuration for the Target Engine."""
    horizons_minutes: List[int] = field(default_factory=lambda: [30, 120, 375])
    scenario_names: List[str] = field(default_factory=lambda: ["bull", "base", "bear"])
    risk_free_rate: float = 0.065          # 6.5% annual risk-free rate
    dividend_yield: float = 0.0            # 0.0 for Black-76 on index futures
    day_count: float = 365.0               # Calendar day count convention for T

    wall_strike_window: int = 10           # Strikes on either side of ATM evaluated for walls
    wall_min_oi_share: float = 0.15        # Minimum share of window OI to qualify as wall

    liquidity: LiquidityConfig = field(default_factory=LiquidityConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    market_hours: MarketHoursConfig = field(default_factory=MarketHoursConfig)
    iv_forecast: IVForecastConfig = field(default_factory=IVForecastConfig)
    scoring: ScoringParams = field(default_factory=ScoringParams)

    # T1 Directional feature baseline weights (sum of directional |w_i| = 1.0)
    feature_weights: Dict[str, float] = field(default_factory=lambda: {
        "F1_call_wall_prox": 0.12,
        "F2_put_wall_prox": 0.12,
        "F3_wall_momentum": 0.08,
        "F4_aggregate_buildup": 0.14,
        "F5_pcr_level": 0.06,
        "F6_pcr_trend": 0.08,
        "F7_pcr_oi_change": 0.06,
        "F8_futures_buildup": 0.12,
        "F9_futures_basis": 0.04,
        "F10_depth_imbalance": 0.00,       # Stubbed out if market depth unavailable
        "F11_volume_burst": 0.06,
        "F14_skew_change": 0.04,
        "F15_max_pain_pull": 0.04,
        "F16_intraday_trend": 0.04,
    })

    # Strike selection filters
    min_rr: float = 1.2                    # Minimum risk-to-reward ratio for candidate trades
    max_reachability_sigma: float = 1.5    # Maximum reachability distance in horizon sigma
    min_dte_trading_days: float = 0.01     # Minimum DTE to avoid extreme zero-time singularity
    reconciliation_tolerance_pts: float = 2.0  # Max acceptable price diff between model and LTP


# Default global configuration instance
DEFAULT_CONFIG = TargetEngineConfig()
