"""T8 Target Engine Orchestration Runner.

Coordinates the full pipeline:
Snapshot -> T1 Feature Extraction -> T2 Scenario Scoring -> T3 Target Engine
-> T4 IV Forecast -> T5 Option Repricing -> T6 Selector -> T7 Risk -> T8 Persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
import pandas as pd

from app.data_access import DashboardData
from target_engine.config import DEFAULT_CONFIG, TargetEngineConfig
from target_engine.contracts import (
    FeatureVector,
    IVForecast,
    ScenarioSet,
    TradeCandidate,
    UnderlyingTarget,
)
from target_engine.features import extract_features
from target_engine.iv_forecast import forecast_iv
from target_engine.levels import generate_underlying_targets
from target_engine.scenarios import score_scenarios
from target_engine.selector import select_trade_candidates
from target_engine.storage import persist_target_run
from target_engine.time_utils import to_ist
from target_engine.walls import OptionWall, detect_walls


@dataclass
class TargetEngineResult:
    """Consolidated bundle containing outputs of all Target Engine stages."""
    feature_vector: FeatureVector
    scenario_set: ScenarioSet
    underlying_targets: Dict[str, UnderlyingTarget]
    iv_forecasts: Dict[str, IVForecast]
    candidates: List[TradeCandidate]
    call_walls: List[OptionWall]
    put_walls: List[OptionWall]
    run_id: int = -1


def run_target_engine(
    dashboard_data: DashboardData,
    horizon_minutes: int = 120,
    config: TargetEngineConfig = DEFAULT_CONFIG,
    persist: bool = True,
) -> TargetEngineResult:
    """Execute complete end-to-end Target Engine pipeline on a live dashboard snapshot."""
    dash = dashboard_data
    spot = float(dash.levels.spot or 0.0) if dash.levels else 25000.0
    fut = float(getattr(dash.levels, "future_price", None) or spot) if dash.levels else spot
    atm_iv = float(getattr(dash.levels, "atm_iv", None) or 0.15) if dash.levels else 0.15
    skew = float(getattr(dash.levels, "skew_25d", None) or 0.0) if dash.levels else 0.0
    max_pain = float(getattr(dash.levels, "max_pain", 0.0) or 0.0) if dash.levels else None
    expiry = str(dash.expiries[0]) if dash.expiries else "2026-10-15"
    contracts_df = dash.contracts if hasattr(dash, "contracts") and isinstance(dash.contracts, pd.DataFrame) else None

    # Step 1: Detect structural walls, then prepend cockpit GEX walls so
    # bull/bear targets share the same call/put wall the rest of the app uses.
    call_walls, put_walls = detect_walls(
        contracts_df=contracts_df,
        spot=spot,
        strike_window=config.wall_strike_window,
        min_oi_share=config.wall_min_oi_share,
    )
    lv = dash.levels if dash.levels else None
    gex_call = float(getattr(lv, "call_wall", 0.0) or 0.0) if lv else 0.0
    gex_put = float(getattr(lv, "put_wall", 0.0) or 0.0) if lv else 0.0

    def _prepend_gex(existing, strike: float, side: str):
        if strike <= 0:
            return existing
        if any(abs(w.strike - strike) < 1.0 for w in existing):
            return existing
        wall = OptionWall(
            strike=strike, side=side, oi=0.0, oi_share=1.0, oi_change=0.0, strength=1.0,
        )
        return [wall] + list(existing)

    call_walls = _prepend_gex(call_walls, gex_call, "CALL")
    put_walls = _prepend_gex(put_walls, gex_put, "PUT")

    # Step 2: Feature Extraction
    fv = extract_features(
        spot=spot,
        fut=fut,
        atm_iv=atm_iv,
        expiry=expiry,
        contracts_df=contracts_df,
        oi_volume_agg=dash.oi_volume,
        day_high=float(dash.day_ohlc.get("high")) if dash.day_ohlc and "high" in dash.day_ohlc else None,
        day_low=float(dash.day_ohlc.get("low")) if dash.day_ohlc and "low" in dash.day_ohlc else None,
        max_pain=max_pain,
        skew_25d=skew,
        config=config,
    )

    # Step 3: Scenario Scoring
    sc_set = score_scenarios(fv, config=config)

    # Step 4: Underlying Target and Stop Engine
    underlying_targets = generate_underlying_targets(
        spot=spot,
        fut=fut,
        atm_iv=atm_iv,
        horizon_minutes=horizon_minutes,
        call_walls=call_walls,
        put_walls=put_walls,
        day_high=float(dash.day_ohlc.get("high")) if dash.day_ohlc and "high" in dash.day_ohlc else None,
        day_low=float(dash.day_ohlc.get("low")) if dash.day_ohlc and "low" in dash.day_ohlc else None,
        max_pain=max_pain,
        dte_days=fv.dte,
        config=config,
    )

    # Step 5: Scenario IV Forecasts
    iv_forecasts: Dict[str, IVForecast] = {}
    for sc in ["bull", "base", "bear"]:
        target_obj = underlying_targets.get(sc)
        denom = spot if spot > 0 else fut
        spot_move = ((target_obj.target - spot) / denom) if target_obj and denom else 0.0
        iv_forecasts[sc] = forecast_iv(
            scenario=sc,
            horizon_minutes=horizon_minutes,
            iv_now=atm_iv,
            spot_move_pct=spot_move,
            config=config,
        )

    # Step 6: Strike & Structure Candidate Selection
    candidates = select_trade_candidates(
        scenario_set=sc_set,
        underlying_targets=underlying_targets,
        iv_forecasts=iv_forecasts,
        contracts_df=contracts_df,
        spot=spot,
        fut=fut,
        expiry=expiry,
        horizon_minutes=horizon_minutes,
        symbol=dash.symbol,
        config=config,
    )

    # Step 7: Persistence
    run_id = -1
    if persist:
        run_id = persist_target_run(
            feature_vector=fv,
            scenario_set=sc_set,
            underlying_targets=underlying_targets,
            iv_forecasts=iv_forecasts,
            candidates=candidates,
            symbol=dash.symbol,
        )

    return TargetEngineResult(
        feature_vector=fv,
        scenario_set=sc_set,
        underlying_targets=underlying_targets,
        iv_forecasts=iv_forecasts,
        candidates=candidates,
        call_walls=call_walls,
        put_walls=put_walls,
        run_id=run_id,
    )
