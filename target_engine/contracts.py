"""Core data contracts for Target Engine (Section 2 & T0 spec).

All stages (T1 to T9) pass these strongly-typed dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class OptionGreeks:
    """Option Greek sensitivities calculated via Black-76."""
    delta: float
    gamma: float
    vega: float      # Per 1% (1 IV point) change in implied volatility
    theta: float     # Per calendar day (negative for long options)
    vanna: float = 0.0  # d(Vega)/dF or d(Delta)/d(sigma)
    volga: float = 0.0  # d(Vega)/d(sigma) (vomma)


@dataclass
class GreekAttribution:
    """Decomposition of full option reprice PnL into Greek components."""
    delta_pnl: float
    gamma_pnl: float
    vega_pnl: float
    theta_pnl: float
    residual: float
    total_modeled_pnl: float
    full_reprice_pnl: float


@dataclass
class OptionLeg:
    """Single leg in a multi-leg or single-leg options strategy."""
    strike: float
    option_type: str  # "CE" or "PE"
    action: str       # "BUY" or "SELL"
    quantity: int = 1
    entry_premium: float = 0.0
    iv: float = 0.0


@dataclass
class FeatureVector:
    """T1: Extracted point-in-time features from market snapshot + history."""
    ts: datetime
    expiry: str
    dte: float
    spot: float
    fut: float
    features: Dict[str, float] = field(default_factory=dict)
    quality_flags: List[str] = field(default_factory=list)


@dataclass
class ScenarioProb:
    """Individual scenario probability definition."""
    name: str        # "bull", "base", "bear"
    prob: float      # Probability in [0, 1]
    calibrated: bool = False


@dataclass
class ScenarioSet:
    """T2: Output of scenario scoring engine."""
    ts: datetime
    scenarios: List[ScenarioProb]
    dominant_scenario: str
    conviction: float
    calibrated: bool = False
    model_version: str = "v1.0-heuristic"
    n_train_samples: int = 0
    brier_oos: Optional[float] = None
    contributions: Dict[str, float] = field(default_factory=dict)
    no_edge: bool = False

    def get_prob(self, name: str) -> float:
        for s in self.scenarios:
            if s.name == name:
                return s.prob
        return 0.0


@dataclass
class UnderlyingTarget:
    """T3: Scenario-based underlying target and stop levels."""
    scenario: str
    horizon_minutes: int
    target: float
    stop: float
    p_touch_target: float
    p_touch_stop: float
    p_close_beyond: float
    reason: str


@dataclass
class IVForecast:
    """T4: Implied volatility forecast for a scenario at a horizon."""
    scenario: str
    horizon_minutes: int
    iv_now: float
    iv_at_horizon: float
    method: str
    components: Dict[str, float] = field(default_factory=dict)
    sensitivity_table: Dict[str, float] = field(default_factory=dict)
    unreliable: bool = False


@dataclass
class OptionTarget:
    """T5: Repriced option premium and Greek attribution for a scenario."""
    strike: float
    option_type: str  # "CE" or "PE"
    scenario: str
    horizon_minutes: int
    entry_premium: float
    target_premium: float
    pnl_pts: float
    pnl_pct: float
    greek_attribution: GreekAttribution
    entry_greeks: OptionGreeks
    target_greeks: OptionGreeks


@dataclass
class TradeCandidate:
    """T6 & T7: Ranked trade candidate with payoffs, risk/reward, and sizing."""
    structure_type: str  # "LONG_CALL", "LONG_PUT", "BULL_CALL_SPREAD", "BEAR_PUT_SPREAD", etc.
    legs: List[OptionLeg]
    scenario_targets: Dict[str, OptionTarget] = field(default_factory=dict)
    expected_value: float = 0.0
    risk_reward: float = 0.0
    breakevens: List[float] = field(default_factory=list)
    max_loss_pts: float = 0.0
    max_loss_inr: float = 0.0
    max_profit_pts: Optional[float] = None
    max_profit_inr: Optional[float] = None
    margin_est_inr: float = 0.0
    rank_score: float = 0.0
    passes_filters: bool = True
    rejection_reasons: List[str] = field(default_factory=list)
    explain: List[str] = field(default_factory=list)
