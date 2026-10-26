"""Live WebSocket Dynamic Strike Ladder (ATM ± 5 Strikes).

Computes real-time option repricing for the 11-strike band around ATM (ATM-5 to ATM+5)
for both Calls (CE) and Puts (PE), featuring:
1. Live exchange-push LTPs from Dhan v2 WebSocket with millisecond latency.
2. Underlying Spot Invalidation & Option Stop-Loss (V_sl).
3. Underlying Target 1 & Option Target 1 (V_tgt1).
4. Underlying Target 2 & Option Target 2 (V_tgt2).
5. Analytical Risk:Reward ratio and reflection-principle touch probabilities.
6. Per-lot rupee PnL for each target and stop level.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

import numpy as np
import pandas as pd

import config
from target_engine.pricer import price, reprice, greeks, implied_vol
from target_engine.time_utils import calculate_fractional_t, calculate_horizon_t, to_ist
from target_engine.levels import compute_touch_probability

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class StrikeRowData:
    """Repriced data for a single option contract leg (CE or PE)."""
    strike: float
    side: str                            # "CE" or "PE"
    ltp: float                           # Current entry premium
    is_ws_live: bool = False             # Whether tick was sourced live from WebSocket
    tick_age_s: Optional[float] = None   # Age of WS tick in seconds
    iv: float = 0.15                     # Implied volatility decimal
    
    # Underlying spot levels
    spot_inval: float = 0.0              # Underlying spot level where trade invalidates
    tgt1_spot: float = 0.0               # Primary underlying spot target
    tgt2_spot: float = 0.0               # Secondary momentum extension spot target

    # Repriced option premiums
    opt_sl: float = 0.0                  # Repriced option premium at stop loss
    opt_tgt1: float = 0.0                # Repriced option premium at Target 1
    opt_tgt2: float = 0.0                # Repriced option premium at Target 2

    # PnL in points
    sl_pts: float = 0.0                  # Points risk (ltp - opt_sl)
    tgt1_pts: float = 0.0                # Points reward at T1 (opt_tgt1 - ltp)
    tgt2_pts: float = 0.0                # Points reward at T2 (opt_tgt2 - ltp)

    # PnL in percentage
    sl_pct: float = 0.0                  # % risk
    tgt1_pct: float = 0.0                # % gain at T1
    tgt2_pct: float = 0.0                # % gain at T2

    # PnL in INR per lot
    sl_inr: float = 0.0                  # Rupee risk per lot
    tgt1_inr: float = 0.0                # Rupee reward at T1 per lot
    tgt2_inr: float = 0.0                # Rupee reward at T2 per lot

    # Risk Metrics & Probabilities
    rr_ratio: float = 0.0                # Reward : Risk ratio
    rr_str: str = "0.0:1"
    p_touch_tgt1: float = 0.50           # Reflection-principle touch probability
    p_touch_sl: float = 0.35

    # Live Greeks
    delta: float = 0.50
    theta: float = -10.0
    is_atm: bool = False


@dataclass
class StrikeLadderRow:
    """Pair of Call and Put at the same strike for Dual-Wing matrix view."""
    strike: float
    is_atm: bool
    ce: StrikeRowData
    pe: StrikeRowData


@dataclass
class StrikeLadderBundle:
    """Consolidated bundle for the 11-strike ATM ± 5 ladder."""
    symbol: str
    spot: float
    atm_strike: float
    expiry: str
    horizon_minutes: int
    lot_size: int
    
    # Underlying session anchors
    ce_spot_inval: float
    ce_tgt1_spot: float
    ce_tgt2_spot: float
    pe_spot_inval: float
    pe_tgt1_spot: float
    pe_tgt2_spot: float

    rows: List[StrikeLadderRow] = field(default_factory=list)
    ws_active_count: int = 0
    computed_at: str = ""


# In-memory last subscribed band cache to avoid redundant WebSocket subscribe calls
_LAST_SUBSCRIBED_BAND: dict[str, tuple[str, list[float]]] = {}
_EXPIRY_LIST_CACHE: dict[str, tuple[float, list[str]]] = {}


def is_expiry_crossed(exp_str: str, now_dt: Optional[datetime] = None) -> bool:
    """Check whether an option expiry date has already crossed (expired).

    In Indian derivatives markets (NSE/BSE), F&O contracts expire at 15:30:00 IST
    on the designated expiry day.
    An expiry has crossed if:
      - expiry_date < today_ist
      - expiry_date == today_ist and current_time_ist >= 15:30:00
    """
    if not exp_str:
        return True
    try:
        if now_dt is None:
            now_ist = datetime.now(IST)
        else:
            if now_dt.tzinfo is None:
                now_ist = now_dt.replace(tzinfo=IST)
            else:
                now_ist = now_dt.astimezone(IST)

        exp_clean = str(exp_str).strip()
        exp_date = None
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                exp_date = datetime.strptime(exp_clean, fmt).date()
                break
            except ValueError:
                continue

        if exp_date is None:
            return False

        today_ist = now_ist.date()
        if exp_date < today_ist:
            return True
        if exp_date == today_ist and now_ist.time() >= datetime.strptime("15:30:00", "%H:%M:%S").time():
            return True
        return False
    except Exception:
        return False


def get_sidebar_active_expiries(
    symbol: str = "NIFTY",
    dash: Any = None,
    contracts_df: Optional[pd.DataFrame] = None,
    extra_expiries: Optional[list[str]] = None,
    now_dt: Optional[datetime] = None,
) -> list[str]:
    """Upcoming active expiries matching the sidebar's calculation scopes (weekly/monthly near-cycle).

    Resolves:
      1. Sidebar calendar expiries (Current Week, Next Week, Near Monthly, Second Monthly).
      2. Any near-dated active expiries from dash / contracts_df within the ~35-day trading horizon.
      3. Deletes any crossed expiry (past dates or today after 15:30 IST).
    """
    sym = (symbol or "NIFTY").upper()
    idx = config.get_index(sym) if hasattr(config, "get_index") else None
    exchange = idx.exchange if idx else ("BSE" if sym == "SENSEX" else "NSE")
    has_weekly = idx.has_weekly if idx else (sym in ("NIFTY", "SENSEX"))

    if now_dt is None:
        now_ist = datetime.now(IST)
    elif now_dt.tzinfo is None:
        now_ist = now_dt.replace(tzinfo=IST)
    else:
        now_ist = now_dt.astimezone(IST)
    today = now_ist.date()
    max_horizon_date = today + timedelta(days=35)

    candidates: list[str] = []

    # 1. Sidebar exchange calendar expiries
    m2 = None
    try:
        from data.expiry import (
            next_weekly_expiry,
            second_weekly_expiry,
            next_monthly_expiry,
            second_monthly_expiry,
        )
        if has_weekly:
            w1 = next_weekly_expiry(today, exchange)
            w2 = second_weekly_expiry(today, exchange)
            if w1:
                candidates.append(w1.strftime("%Y-%m-%d"))
            if w2:
                candidates.append(w2.strftime("%Y-%m-%d"))
        m1 = next_monthly_expiry(today, exchange)
        m2 = second_monthly_expiry(today, exchange)
        if m1:
            candidates.append(m1.strftime("%Y-%m-%d"))
        if m2:
            candidates.append(m2.strftime("%Y-%m-%d"))
    except Exception:
        pass

    # 2. Add contracts_df / dash.expiries if near-dated (within 35 days)
    if contracts_df is not None and not contracts_df.empty and "expiry" in contracts_df.columns:
        for e in contracts_df["expiry"].dropna().unique():
            candidates.append(e.strftime("%Y-%m-%d") if hasattr(e, "strftime") else str(e).strip())

    if dash is not None and hasattr(dash, "expiries") and dash.expiries:
        for e in dash.expiries:
            candidates.append(e.strftime("%Y-%m-%d") if hasattr(e, "strftime") else str(e).strip())

    if extra_expiries:
        candidates.extend(extra_expiries)

    # Filter out crossed expiries and distant leaps (> 35 days unless it is second monthly)
    valid_expiries: list[str] = []
    for item in candidates:
        s = str(item).strip()
        if not s:
            continue
        d_obj = None
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                d_obj = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
        if d_obj is not None:
            norm_str = d_obj.strftime("%Y-%m-%d")
            # Only include if not crossed and within the near-dated horizon (max 35 days or m2)
            if not is_expiry_crossed(norm_str, now_dt=now_ist):
                if d_obj <= max_horizon_date or (m2 and d_obj == m2):
                    valid_expiries.append(norm_str)
        else:
            if not is_expiry_crossed(s, now_dt=now_ist):
                valid_expiries.append(s)

    def _sort_key(exp_str: str):
        try:
            return datetime.strptime(exp_str, "%Y-%m-%d").date()
        except Exception:
            return datetime.max.date()

    return sorted(list(dict.fromkeys(valid_expiries)), key=_sort_key)


def get_dynamic_active_expiries(
    symbol: str = "NIFTY",
    dash: Any = None,
    contracts_df: Optional[pd.DataFrame] = None,
    extra_expiries: Optional[list[str]] = None,
    now_dt: Optional[datetime] = None,
) -> list[str]:
    """Alias for get_sidebar_active_expiries for backwards compatibility."""
    return get_sidebar_active_expiries(
        symbol=symbol,
        dash=dash,
        contracts_df=contracts_df,
        extra_expiries=extra_expiries,
        now_dt=now_dt,
    )


def compute_strike_ladder(
    symbol: str,
    spot: float,
    contracts_df: Optional[pd.DataFrame],
    expiry: str,
    horizon_minutes: int = 45,
    levels: Any = None,
    auto_subscribe_ws: bool = True,
) -> StrikeLadderBundle:
    """Compute the 11-strike (ATM ± 5) live repriced target & stop ladder."""
    sym = symbol.upper()
    idx_info = config.get_index(sym)
    lot_size = int(idx_info.lot_size)
    step = float(idx_info.strike_step)

    safe_spot = max(float(spot or 0.0), 100.0)
    now_dt = to_ist()
    T_now = calculate_fractional_t(now_dt, expiry)

    # 1. Resolve available strikes for this expiry
    exp_df = None
    if contracts_df is not None and not contracts_df.empty:
        def _match_exp(exp_val, target_str):
            if hasattr(exp_val, "strftime"):
                return exp_val.strftime("%Y-%m-%d") == target_str
            return str(exp_val).strip() == target_str

        if "expiry" in contracts_df.columns:
            matched = contracts_df[contracts_df["expiry"].apply(lambda x: _match_exp(x, expiry))]
            if not matched.empty:
                exp_df = matched.copy()
            else:
                exp_df = None
        else:
            exp_df = None

    if exp_df is not None and not exp_df.empty and "strike" in exp_df.columns:
        raw_strikes = sorted(exp_df["strike"].dropna().unique())
        all_strikes = [int(k) if float(k).is_integer() else float(k) for k in raw_strikes]
    else:
        # Synthetic fallback grid around spot
        center_k = round(safe_spot / step) * step
        all_strikes = [center_k + (i * step) for i in range(-15, 16)]

    # 2. Identify ATM and slice exactly 5 strikes below, ATM, and 5 strikes above (11 total)
    atm_strike = min(all_strikes, key=lambda k: abs(float(k) - safe_spot))
    atm_idx = all_strikes.index(atm_strike)
    
    start_idx = max(0, atm_idx - 5)
    end_idx = min(len(all_strikes), atm_idx + 6)
    band_11 = all_strikes[start_idx:end_idx]

    # Ensure exactly 11 strikes if possible
    if len(band_11) < 11:
        needed = 11 - len(band_11)
        if start_idx == 0 and end_idx < len(all_strikes):
            band_11 = all_strikes[start_idx : min(len(all_strikes), end_idx + needed)]
        elif end_idx == len(all_strikes) and start_idx > 0:
            band_11 = all_strikes[max(0, start_idx - needed) : end_idx]

    # 3. Dynamic WebSocket auto-subscription
    if auto_subscribe_ws:
        cached_sub = _LAST_SUBSCRIBED_BAND.get(sym)
        if not cached_sub or cached_sub[0] != expiry or cached_sub[1] != band_11:
            try:
                from app.ws_spot_feed import subscribe_options
                subscribe_options(sym, band_11, expiry)
                _LAST_SUBSCRIBED_BAND[sym] = (expiry, list(band_11))
            except Exception:
                pass

    # 4. Underlying Spot Level Calibration (Targets & Invalidation)
    atm_iv_base = float(getattr(levels, "atm_iv", None) or 0.14) if levels else 0.14
    atm_iv_base = max(0.04, min(0.90, atm_iv_base))
    
    # 1-SD horizon expected move
    sigma_h = max(safe_spot * atm_iv_base * math.sqrt(horizon_minutes / (375.0 * 252.0)), 15.0)

    # Extract GEX structural walls if present
    call_wall = float(getattr(levels, "call_wall", None) or 0.0) if levels else 0.0
    put_wall = float(getattr(levels, "put_wall", None) or 0.0) if levels else 0.0
    vwap = float(getattr(levels, "vwap", None) or safe_spot) if levels else safe_spot

    # Call (CE) underlying levels: Bullish directional view
    max_reach = max(step * 6.0, 350.0)
    if call_wall > safe_spot:
        ce_tgt1_spot = min(call_wall, safe_spot + 0.6 * sigma_h)
    else:
        ce_tgt1_spot = safe_spot + 0.6 * sigma_h
    ce_tgt2_spot = safe_spot + 1.2 * sigma_h
    ce_spot_inval = max(put_wall if 0 < put_wall < safe_spot else safe_spot - 0.4 * sigma_h, safe_spot - max_reach)
    ce_spot_inval = min(ce_spot_inval, safe_spot - 15.0)  # At least 15 pts buffer

    # Put (PE) underlying levels: Bearish directional view
    if 0 < put_wall < safe_spot:
        pe_tgt1_spot = max(put_wall, safe_spot - 0.6 * sigma_h)
    else:
        pe_tgt1_spot = safe_spot - 0.6 * sigma_h
    pe_tgt2_spot = safe_spot - 1.2 * sigma_h
    pe_spot_inval = min(call_wall if call_wall > safe_spot else safe_spot + 0.4 * sigma_h, safe_spot + max_reach)
    pe_spot_inval = max(pe_spot_inval, safe_spot + 15.0)  # At least 15 pts buffer

    # Time decrements for targets and stop loss
    _, T_tgt1 = calculate_horizon_t(now_dt, horizon_minutes, expiry)
    _, T_tgt2 = calculate_horizon_t(now_dt, int(horizon_minutes * 1.5), expiry)
    _, T_sl = calculate_horizon_t(now_dt, int(horizon_minutes * 0.5), expiry)

    # 5. Evaluate each strike in the 11-strike band
    rows: List[StrikeLadderRow] = []
    ws_active_count = 0

    from app.spot_ticker import get_option_ws_quote

    for k in band_11:
        is_atm = (k == atm_strike)

        # Reprice CE leg and PE leg
        ce_data = _reprice_ladder_leg(
            symbol=sym,
            strike=float(k),
            side="CE",
            spot=safe_spot,
            T_now=T_now,
            T_tgt1=T_tgt1,
            T_tgt2=T_tgt2,
            T_sl=T_sl,
            tgt1_spot=ce_tgt1_spot,
            tgt2_spot=ce_tgt2_spot,
            inval_spot=ce_spot_inval,
            sigma_h=sigma_h,
            atm_iv=atm_iv_base,
            exp_df=exp_df,
            lot_size=lot_size,
            is_atm=is_atm,
            get_ws_quote_fn=get_option_ws_quote,
        )

        pe_data = _reprice_ladder_leg(
            symbol=sym,
            strike=float(k),
            side="PE",
            spot=safe_spot,
            T_now=T_now,
            T_tgt1=T_tgt1,
            T_tgt2=T_tgt2,
            T_sl=T_sl,
            tgt1_spot=pe_tgt1_spot,
            tgt2_spot=pe_tgt2_spot,
            inval_spot=pe_spot_inval,
            sigma_h=sigma_h,
            atm_iv=atm_iv_base,
            exp_df=exp_df,
            lot_size=lot_size,
            is_atm=is_atm,
            get_ws_quote_fn=get_option_ws_quote,
        )

        if ce_data.is_ws_live:
            ws_active_count += 1
        if pe_data.is_ws_live:
            ws_active_count += 1

        rows.append(StrikeLadderRow(strike=float(k), is_atm=is_atm, ce=ce_data, pe=pe_data))

    return StrikeLadderBundle(
        symbol=sym,
        spot=safe_spot,
        atm_strike=float(atm_strike),
        expiry=expiry,
        horizon_minutes=horizon_minutes,
        lot_size=lot_size,
        ce_spot_inval=round(ce_spot_inval, 1),
        ce_tgt1_spot=round(ce_tgt1_spot, 1),
        ce_tgt2_spot=round(ce_tgt2_spot, 1),
        pe_spot_inval=round(pe_spot_inval, 1),
        pe_tgt1_spot=round(pe_tgt1_spot, 1),
        pe_tgt2_spot=round(pe_tgt2_spot, 1),
        rows=rows,
        ws_active_count=ws_active_count,
        computed_at=now_dt.strftime("%H:%M:%S"),
    )


def _reprice_ladder_leg(
    symbol: str,
    strike: float,
    side: str,
    spot: float,
    T_now: float,
    T_tgt1: float,
    T_tgt2: float,
    T_sl: float,
    tgt1_spot: float,
    tgt2_spot: float,
    inval_spot: float,
    sigma_h: float,
    atm_iv: float,
    exp_df: Optional[pd.DataFrame],
    lot_size: int,
    is_atm: bool,
    get_ws_quote_fn: Any,
) -> StrikeRowData:
    """Reprice a single CE or PE leg against live WebSocket tick or snapshot quote."""
    is_call = (side.upper() == "CE")
    type_prefix = "C" if is_call else "P"
    opt_type = "CE" if is_call else "PE"

    # Step A: Live WebSocket Quote lookup
    ws_quote = None
    try:
        ws_quote = get_ws_quote_fn(symbol, strike, side)
    except Exception:
        ws_quote = None

    is_ws_live = False
    tick_age_s = None
    entry_ltp = 0.0

    if ws_quote and ws_quote.get("ltp") and ws_quote["ltp"] > 0:
        entry_ltp = float(ws_quote["ltp"])
        is_ws_live = True
        tick_age_s = max(0.0, time.time() - float(ws_quote.get("at", time.time())))

    # Step B: Fallback to Option Chain DataFrame snapshot if WS quote is absent
    strike_iv = atm_iv
    if exp_df is not None and not exp_df.empty:
        match = exp_df[
            (pd.to_numeric(exp_df["strike"], errors="coerce") == float(strike)) &
            (exp_df["type"].astype(str).str.upper().isin([opt_type, type_prefix]))
        ]
        if not match.empty:
            row = match.iloc[0]
            if not entry_ltp or entry_ltp <= 0:
                for col in ("mid", "ltp", "price", "last_price"):
                    v = row.get(col)
                    if v is not None and not (isinstance(v, float) and np.isnan(v)) and float(v) > 0:
                        entry_ltp = float(v)
                        break
            raw_iv = row.get("iv")
            if raw_iv is not None and not (isinstance(raw_iv, float) and np.isnan(raw_iv)) and float(raw_iv) > 0:
                strike_iv = float(raw_iv) / 100.0 if float(raw_iv) > 1.0 else float(raw_iv)

    # Step C: Model fallback if entry_ltp is still zero
    if not entry_ltp or entry_ltp <= 0:
        entry_ltp = price(spot, strike, T_now, strike_iv, 0.065, opt_type)
        entry_ltp = max(0.05, round(entry_ltp, 2))

    # Step D: Extract strike IV using inversion if not present
    if not strike_iv or strike_iv <= 0.02 or math.isnan(strike_iv):
        solved = implied_vol(entry_ltp, spot, strike, T_now, 0.065, opt_type)
        strike_iv = solved if (solved and solved > 0.02) else atm_iv

    strike_iv = max(0.04, min(0.90, strike_iv))

    # Step E: Greeks
    cur_greeks = greeks(spot, strike, T_now, strike_iv, 0.065, opt_type)

    # Step F: Scenario IV adjustments (vol compression on CE targets, vol expansion on PE targets)
    if is_call:
        iv_tgt1 = max(0.04, strike_iv - 0.0075)
        iv_tgt2 = max(0.04, strike_iv - 0.0120)
        iv_sl = max(0.04, strike_iv + 0.0120)
    else:
        iv_tgt1 = max(0.04, strike_iv + 0.0150)
        iv_tgt2 = max(0.04, strike_iv + 0.0250)
        iv_sl = max(0.04, strike_iv - 0.0080)

    # Step G: Black-76 forward repricing
    p_tgt1_calc = reprice(tgt1_spot, strike, T_tgt1, iv_tgt1, 0.065, opt_type)
    p_tgt2_calc = reprice(tgt2_spot, strike, T_tgt2, iv_tgt2, 0.065, opt_type)
    p_sl_calc = reprice(inval_spot, strike, T_sl, iv_sl, 0.065, opt_type)

    # Step H: Strict monotonicity and buffer enforcement
    # T2 > T1 > Entry > SL
    tgt1_opt = max(round(p_tgt1_calc, 1), round(entry_ltp * 1.15, 1))
    tgt2_opt = max(round(p_tgt2_calc, 1), round(tgt1_opt * 1.15, 1))
    
    # Cap target price multiples to realistic intraday limits (max 4x for T1, 6.5x for T2)
    tgt1_opt = min(tgt1_opt, round(entry_ltp * 4.0, 1))
    tgt2_opt = min(tgt2_opt, round(entry_ltp * 6.5, 1))

    sl_opt = min(round(p_sl_calc, 1), round(entry_ltp * 0.85, 1))
    if sl_opt >= entry_ltp:
        sl_opt = round(entry_ltp * 0.72, 2)
    sl_opt = max(0.05, min(sl_opt, round(entry_ltp - 0.05, 2) if entry_ltp > 0.15 else round(entry_ltp * 0.70, 2)))

    # Step I: Calculate PnL (pts, %, INR)
    tgt1_pts = round(tgt1_opt - entry_ltp, 2)
    tgt2_pts = round(tgt2_opt - entry_ltp, 2)
    sl_pts = round(entry_ltp - sl_opt, 2)

    tgt1_pct = round((tgt1_pts / entry_ltp) * 100.0, 1) if entry_ltp > 0 else 0.0
    tgt2_pct = round((tgt2_pts / entry_ltp) * 100.0, 1) if entry_ltp > 0 else 0.0
    sl_pct = round((sl_pts / entry_ltp) * 100.0, 1) if entry_ltp > 0 else 0.0

    tgt1_inr = round(tgt1_pts * lot_size, 0)
    tgt2_inr = round(tgt2_pts * lot_size, 0)
    sl_inr = round(sl_pts * lot_size, 0)

    # Step J: Risk-to-Reward and Reflection Touch Probabilities
    rr_val = round(tgt1_pts / sl_pts, 2) if sl_pts > 0.05 else 9.9
    rr_val = max(0.1, min(9.9, rr_val))
    rr_str = f"{rr_val:.1f}:1"

    drift_fwd = 0.25 * sigma_h if is_call else -0.25 * sigma_h
    p_touch_t1 = compute_touch_probability(distance=abs(tgt1_spot - spot), sigma_h=sigma_h, drift=drift_fwd)
    p_touch_sl = compute_touch_probability(distance=abs(inval_spot - spot), sigma_h=sigma_h, drift=-0.2 * sigma_h)

    return StrikeRowData(
        strike=strike,
        side=side,
        ltp=round(entry_ltp, 2),
        is_ws_live=is_ws_live,
        tick_age_s=round(tick_age_s, 1) if tick_age_s is not None else None,
        iv=round(strike_iv, 4),
        spot_inval=round(inval_spot, 1),
        tgt1_spot=round(tgt1_spot, 1),
        tgt2_spot=round(tgt2_spot, 1),
        opt_sl=round(sl_opt, 2),
        opt_tgt1=round(tgt1_opt, 2),
        opt_tgt2=round(tgt2_opt, 2),
        sl_pts=sl_pts,
        tgt1_pts=tgt1_pts,
        tgt2_pts=tgt2_pts,
        sl_pct=sl_pct,
        tgt1_pct=tgt1_pct,
        tgt2_pct=tgt2_pct,
        sl_inr=sl_inr,
        tgt1_inr=tgt1_inr,
        tgt2_inr=tgt2_inr,
        rr_ratio=rr_val,
        rr_str=rr_str,
        p_touch_tgt1=round(p_touch_t1, 3),
        p_touch_sl=round(p_touch_sl, 3),
        delta=round(cur_greeks.delta, 3),
        theta=round(cur_greeks.theta, 2),
        is_atm=is_atm,
    )
