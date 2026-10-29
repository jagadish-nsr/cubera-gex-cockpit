"""sentinel/models.py — OrderFlow Sentinel Data Contracts.

Defines:
  - Bar: Normalized 1m/5m candle with order flow delta, CVD, and session VWAP bands
  - SentinelSignal: Real-time microstructure signals with confidence score (0-100) and context
  - SignalOutcome: Forward return evaluations (+1m, +3m, +5m, +15m MFE/MAE)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Bar:
    """Intraday bar enriched with order flow metrics."""
    instrument_id: str             # "NIFTY-FUT" | "SENSEX-FUT" | "NIFTY"
    timeframe: str                 # "1m" | "5m"
    start_ts: datetime             # UTC or IST timestamp
    open: float
    high: float
    low: float
    close: float
    volume_units: int
    volume_lots: float
    buy_vol_est: float             # Estimated buyer-initiated volume
    sell_vol_est: float            # Estimated seller-initiated volume
    delta: float                   # buy_vol_est - sell_vol_est
    delta_ratio: float             # delta / volume_units (-1.0 to +1.0)
    delta_quality: float           # % classified cleanly (0.0 to 1.0)
    cvd: float                     # Cumulative Volume Delta for session
    vwap: float                    # Session Volume Weighted Average Price
    vwap_sigma_1: float            # Upper +1σ band
    vwap_sigma_2: float            # Upper +2σ band
    vwap_lower_1: float            # Lower -1σ band
    vwap_lower_2: float            # Lower -2σ band
    oi: int = 0
    delta_oi: int = 0
    oi_fresh: bool = True
    incomplete: bool = False       # True if bar closed prematurely or dropped ticks
    depth_imbalance: float = 0.0   # (bids - asks) / (bids + asks) from depth book


@dataclass
class SentinelSignal:
    """Microstructure order flow signal triggered on bar completion or intra-bar."""
    event_id: str                  # e.g. "sig_20261002_094500_NIFTY_1m_CONT_BULL"
    instrument_id: str
    timeframe: str                 # "1m" | "5m"
    bar_start_ts: datetime
    type: str                      # "CONTINUATION_BULL", "ABSORPTION_BEAR", "EXHAUSTION_UP", "CVD_DIVERGENCE_BEAR", etc.
    category: str                  # "BULLISH" | "BEARISH" | "NEUTRAL" | "CAUTION"
    bias: str                      # "LONG" | "SHORT" | "NEUTRAL"
    score: int                     # 0 to 100 confidence score
    confidence: str                # "HIGH" (>=70) | "MEDIUM" (50-69) | "LOW" (<50)
    rvol_tod: float                # Time-of-day normalized relative volume (e.g. 2.4x)
    delta: float                   # Bar delta
    delta_ratio: float             # Bar delta ratio
    oi_change: int                 # OI change over bar
    context_tags: list[str]        # ["NEAR_CALL_WALL", "ABOVE_VWAP", "ORB_HIGH"]
    insight_text: str              # Explainable plain English description
    invalidation_level: float      # Concrete price invalidation cutoff
    entry_price: float = 0.0       # Exact benchmark entry price at signal trigger
    target_1_pts: float = 0.0      # Target 1 (e.g. 1.5R or nearest GEX wall)
    target_2_pts: float = 0.0      # Target 2 (e.g. 2.5R or major GEX wall)
    risk_reward_ratio: float = 0.0 # Expected R:R ratio
    actionable_play: str = ""      # Specific derivative trade structure (e.g. "Bull Put Spread 24800/24700")
    trigger_speed: str = "CONFIRMED_BAR"  # "CONFIRMED_BAR" | "MICRO_BURST"
    rule_set_version: str = "v2.1"
    status: str = "ACTIVE"            # "ACTIVE" | "INVALIDATED" | "TARGET_HIT"
    gex_alignment: str = "NEUTRAL" # "CONFIRMED_AT_WALL" | "COUNTER_TREND" | "NEUTRAL"


@dataclass
class SignalOutcome:
    """Forward-return evaluation of a SentinelSignal."""
    event_id: str
    instrument_id: str
    signal_type: str
    entry_price: float
    invalidation_level: float
    ret_1m_pts: float | None = None
    ret_3m_pts: float | None = None
    ret_5m_pts: float | None = None
    ret_15m_pts: float | None = None
    ret_30m_pts: float | None = None
    mfe_pts: float = 0.0           # Maximum Favorable Excursion
    mae_pts: float = 0.0           # Maximum Adverse Excursion
    stopped_out: bool = False
    target_hit: bool = False
    status: str = "ACTIVE"         # "ACTIVE" | "INVALIDATED" | "TARGET_HIT"
    evaluated_at: datetime | None = None
