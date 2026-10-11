"""app/market_simulator.py — Session Replay & Market Simulation Engine.

Enables point-in-time intraday market replay for:
  1. Curated Historical Market Scenarios:
     - Expiry Day Gamma-Blast (Pin at Max Pain → 15:00 Blast)
     - Range-Bound High-Gamma Iron Condor Pinning
     - Trend Day (Call Wall Absorption Breakdown)
  2. Captured Historical Trading Days:
     - Real per-minute recorded GEX levels and option chain ladders from data/journal/timeseries/
  3. Interactive Scrubbing & Playback:
     - Minute-by-minute timeline slider (09:15 to 15:30 IST)
     - Play/Pause auto-advance, step buttons (+5m, -15m), and key phase jump presets
  4. End-to-End Cockpit & Sentinel Synchronization:
     - Reconstructs point-in-time DashboardData (spot, walls, flip, max pain, net gex, PCR, day OHLC)
     - Synchronizes Sentinel candles, CVD, and microstructure signals
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import config
from app.data_access import DashboardData
from app.timeseries import TS_DIR, load_day, load_strikes_day
from gex.levels import Levels
from regime.classify import classify
from sentinel.engine.aggregator import SessionVWAPTracker, aggregate_1m_to_5m
from sentinel.models import Bar, SentinelSignal
from sentinel.storage.db import (
    clear_sentinel_data,
    ensure_sentinel_schema,
    list_recent_bars,
    save_bar,
    save_signal,
)

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Timeline Constants (09:15 to 15:30 IST in 5-minute steps)
# ---------------------------------------------------------------------------

def generate_intraday_timestamps(step_minutes: int = 5) -> list[str]:
    """Generate HH:MM time strings from 09:15 to 15:30 IST."""
    times = []
    curr = datetime(2026, 1, 1, 9, 15)
    end = datetime(2026, 1, 1, 15, 30)
    while curr <= end:
        times.append(curr.strftime("%H:%M"))
        curr += timedelta(minutes=step_minutes)
    return times


TIMESTAMPS_5M = generate_intraday_timestamps(5)

KEY_PHASES = {
    "09:15": ("🌅 09:15 Market Open", "Opening bell volatility & initial auction discovery"),
    "10:00": ("📐 10:00 Opening Range Established", "First 45m range defined; dealer positioning visible"),
    "11:30": ("☕ 11:30 Midday Liquidity Stall", "Volume thins, range compression around dealer equilibrium"),
    "13:45": ("⚡ 13:45 Afternoon Expiry Anchor", "Gamma ramp begins; hedging sensitivity increases"),
    "14:45": ("🚀 14:45 0-DTE Gamma Blast Window", "Critical 30-min window for wall breakouts or terminal pin"),
    "15:20": ("🏁 15:20 Final Dealer Squaring", "Intraday squaring and market-on-close rebalancing"),
    "15:30": ("🛑 15:30 Cash Market Close", "Settlement at closing price"),
}

# ---------------------------------------------------------------------------
# Curated Preset Scenarios Definitions
# ---------------------------------------------------------------------------

SCENARIOS = {
    "expiry_gamma_blast": {
        "id": "expiry_gamma_blast",
        "title": "Expiry Day Gamma-Blast (Pin at Max Pain → 15:00 Blast)",
        "badge": "0-DTE EXPIRY · GAMMA SQUEEZE",
        "description": (
            "Spot stays pinned around Max Pain (22,650) through midday. "
            "At 13:45, spot creeps above the Gamma Flip (22,680), dealer gamma turns negative, "
            "and a violent short-squeeze explosion detonates through Call Wall (22,750) into 15:00."
        ),
        "base_spot": 22650.0,
        "call_wall": 22750.0,
        "put_wall": 22550.0,
        "flip": 22680.0,
        "max_pain": 22650.0,
        "bias": "BULLISH BREAKOUT",
        "key_setup": "Look for Call Wall absorption early, then buy ATM Calls or Bull Call Spreads on the 13:45 breakout.",
    },
    "iron_condor_pin": {
        "id": "iron_condor_pin",
        "title": "Range-Bound High-Gamma Iron Condor Pinning",
        "badge": "HIGH POSITIVE GAMMA · MEAN REVERTING",
        "description": (
            "Dealers hold heavy positive gamma (+₹340 Cr). Any rally toward 22,700 is met with aggressive dealer selling, "
            "and any dip toward 22,600 is met with dealer buying. Spot oscillates between walls in a textbook pin."
        ),
        "base_spot": 22650.0,
        "call_wall": 22800.0,
        "put_wall": 22500.0,
        "flip": 22450.0,
        "max_pain": 22650.0,
        "bias": "NEUTRAL / RANGE PIN",
        "key_setup": "Fade extremes near walls; harvest rapid theta decay via Iron Condor or Short Strangle.",
    },
    "trend_breakdown": {
        "id": "trend_breakdown",
        "title": "Trend Day (Call Wall Absorption Breakdown)",
        "badge": "NEGATIVE GAMMA · VOLATILE TREND",
        "description": (
            "Opens with an early bull trap at Call Wall (22,800). Fails with heavy absorption delta, "
            "breaks down through Gamma Flip (22,720), and triggers an accelerating negative-gamma cascade through Put Wall (22,550)."
        ),
        "base_spot": 22780.0,
        "call_wall": 22850.0,
        "put_wall": 22550.0,
        "flip": 22720.0,
        "max_pain": 22700.0,
        "bias": "BEARISH CASCADE",
        "key_setup": "Sell rallies below VWAP; enter Bear Put Spreads once spot breaks below the Gamma Flip.",
    },
}

# ---------------------------------------------------------------------------
# Historical Days Discovery
# ---------------------------------------------------------------------------

def get_available_historical_days(symbol: str = "NIFTY") -> list[str]:
    """Return sorted list of available captured days (YYYY-MM-DD) from data/journal/timeseries/."""
    sym_dir = TS_DIR / symbol.upper()
    if not sym_dir.exists():
        return []
    csvs = sorted(sym_dir.glob("*.csv"), reverse=True)
    return [p.stem for p in csvs if len(p.stem) == 10 and p.stem.startswith("20")]


# ---------------------------------------------------------------------------
# Price Path & GEX Synthesizers for Preset Scenarios
# ---------------------------------------------------------------------------

def _mins_from_open(time_str: str) -> int:
    """Minutes elapsed since 09:15."""
    try:
        h, m = map(int, time_str.split(":"))
        return max(0, min(375, (h * 60 + m) - (9 * 60 + 15)))
    except Exception:
        return 0


def _scenario_spot_and_gex(scenario_id: str, upto_time: str) -> dict[str, Any]:
    """Compute point-in-time spot, walls, GEX levels, and Greeks for a preset scenario at upto_time."""
    scen = SCENARIOS.get(scenario_id, SCENARIOS["expiry_gamma_blast"])
    elapsed = _mins_from_open(upto_time)  # 0 to 375 minutes (09:15 to 15:30)
    frac = elapsed / 375.0

    call_wall = scen["call_wall"]
    put_wall = scen["put_wall"]
    flip = scen["flip"]
    max_pain = scen["max_pain"]
    base_spot = scen["base_spot"]

    if scenario_id == "expiry_gamma_blast":
        # 09:15 - 13:45 (0 to 270 min): Pin oscillation around base_spot (±14 pts)
        if elapsed <= 270:
            osc = math.sin(elapsed * 0.12) * 14.0 + math.cos(elapsed * 0.04) * 6.0
            spot = base_spot + osc
            net_gex = 45_000_000.0 - (elapsed / 270.0) * 20_000_000.0
            regime = "POSITIVE"
            mode_label = "RANGE-BOUND (pinning)"
            pcr = 0.98 + (elapsed / 270.0) * 0.08
        # 13:45 - 14:45 (270 to 330 min): Squeeze build-up toward Call Wall
        elif elapsed <= 330:
            progress = (elapsed - 270) / 60.0
            spot = base_spot + progress * 95.0 + math.sin(elapsed * 0.3) * 5.0
            net_gex = -80_000_000.0 - progress * 140_000_000.0
            regime = "NEGATIVE"
            mode_label = "CAUTION (gamma ramp)"
            pcr = 1.15 + progress * 0.15
        # 14:45 - 15:30 (330 to 375 min): Gamma Blast detonation!
        else:
            progress = (elapsed - 330) / 45.0
            surge = 1.0 / (1.0 + math.exp(-6.0 * (progress - 0.4)))
            spot = base_spot + 95.0 + surge * 125.0
            net_gex = -320_000_000.0 - progress * 180_000_000.0
            call_wall = base_spot + 200.0 if progress > 0.5 else base_spot + 100.0
            regime = "NEGATIVE"
            mode_label = "TRENDING (explosive blast)"
            pcr = 1.42

    elif scenario_id == "iron_condor_pin":
        # Mean-reverting cycle
        wave1 = math.sin(elapsed * 0.05) * 35.0
        wave2 = math.cos(elapsed * 0.11) * 12.0
        spot = base_spot + wave1 + wave2
        net_gex = 340_000_000.0 + math.sin(elapsed * 0.02) * 20_000_000.0
        regime = "POSITIVE"
        mode_label = "RANGE-BOUND (iron pin)"
        pcr = 1.05 + math.sin(elapsed * 0.03) * 0.04

    else:  # trend_breakdown
        # 09:15 - 10:30 (0 to 75 min): Early test of Call Wall
        if elapsed <= 75:
            spot = base_spot + math.sin(elapsed * 0.08) * 35.0
            net_gex = 60_000_000.0
            regime = "POSITIVE"
            mode_label = "CAUTION (wall rejection)"
            pcr = 0.95
        # 10:30 - 13:00 (75 to 225 min): Slide through Flip
        elif elapsed <= 225:
            progress = (elapsed - 75) / 150.0
            spot = base_spot - progress * 110.0 + math.sin(elapsed * 0.1) * 8.0
            net_gex = 20_000_000.0 - progress * 150_000_000.0
            regime = "NEGATIVE" if spot < flip else "POSITIVE"
            mode_label = "TRENDING (bearish slide)"
            pcr = 0.90 - progress * 0.15
        # 13:00 - 15:30 (225 to 375 min): Cascade through Put Wall
        else:
            progress = (elapsed - 225) / 150.0
            spot = (base_spot - 110.0) - progress * 250.0 + math.sin(elapsed * 0.15) * 10.0
            net_gex = -220_000_000.0 - progress * 140_000_000.0
            put_wall = base_spot - 350.0 if progress > 0.6 else base_spot - 250.0
            regime = "NEGATIVE"
            mode_label = "TRENDING (cascade breakdown)"
            pcr = 0.68

    spot = round(spot, 2)
    return {
        "spot": spot,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "flip": flip,
        "max_pain": max_pain,
        "magnet": max_pain,
        "net_gex": net_gex,
        "regime": regime,
        "mode_label": mode_label,
        "pcr": round(pcr, 3),
        "vix": round(13.8 + (1.8 * frac if regime == "NEGATIVE" else -0.8 * frac), 2),
    }


# ---------------------------------------------------------------------------
# Snapshot Builders: Scenario & Historical
# ---------------------------------------------------------------------------

def _build_synthetic_strikes(spot: float, call_wall: float, put_wall: float, net_gex: float) -> list[dict[str, Any]]:
    """Build a realistic 21-strike options chain grid centered on spot."""
    atm = round(spot / 50.0) * 50.0
    strikes = []
    for step in range(-10, 11):
        k = atm + step * 50.0
        dist = abs(k - spot)
        # Heuristic OI distribution
        call_oi = 4_500_000.0 * math.exp(-((k - call_wall) ** 2) / (2 * (150 ** 2))) + 400_000.0
        put_oi = 4_500_000.0 * math.exp(-((k - put_wall) ** 2) / (2 * (150 ** 2))) + 400_000.0
        k_net_gex = (put_oi - call_oi) * 45.0
        strikes.append({
            "strike": k,
            "call_oi": round(call_oi),
            "call_doi": round(call_oi * 0.08),
            "put_oi": round(put_oi),
            "put_doi": round(put_oi * 0.08),
            "call_vol": round(call_oi * 0.4),
            "put_vol": round(put_oi * 0.4),
            "call_ltp": round(max(2.0, spot - k + 45.0), 2) if k < spot else round(max(1.5, 55.0 - dist * 0.2), 2),
            "put_ltp": round(max(2.0, k - spot + 45.0), 2) if k > spot else round(max(1.5, 55.0 - dist * 0.2), 2),
            "net_gex": k_net_gex,
            "call_doi_near": round(call_oi * 0.05),
            "put_doi_near": round(put_oi * 0.05),
            "call_oi_near": round(call_oi),
            "put_oi_near": round(put_oi),
        })
    return strikes


def generate_scenario_snapshot(
    scenario_id: str,
    upto_time: str,
    symbol: str = "NIFTY",
    window: str = "weekly",
) -> DashboardData:
    """Generate a fully populated point-in-time DashboardData for a preset scenario."""
    meta = SCENARIOS.get(scenario_id, SCENARIOS["expiry_gamma_blast"])
    data_dict = _scenario_spot_and_gex(scenario_id, upto_time)

    spot = data_dict["spot"]
    call_wall = data_dict["call_wall"]
    put_wall = data_dict["put_wall"]
    flip = data_dict["flip"]
    max_pain = data_dict["max_pain"]
    magnet = data_dict["magnet"]
    total_net_gex = data_dict["net_gex"]
    regime = data_dict["regime"]
    vix = data_dict["vix"]

    # Synthesize Day OHLC up to current minute
    elapsed = _mins_from_open(upto_time)
    all_spots = [_scenario_spot_and_gex(scenario_id, t)["spot"] for t in TIMESTAMPS_5M if _mins_from_open(t) <= elapsed]
    if not all_spots:
        all_spots = [spot]

    day_ohlc = {
        "open": float(all_spots[0]),
        "high": float(max(all_spots)),
        "low": float(min(all_spots)),
        "close": float(spot),
        "ltp": float(spot),
    }

    # Synthesize OI strikes
    oi_strikes = _build_synthetic_strikes(spot, call_wall, put_wall, total_net_gex)
    per_strike_df = pd.DataFrame([{"strike": s["strike"], "net_gex": s["net_gex"]} for s in oi_strikes])

    # Construct Levels object
    levels = Levels(
        spot=spot,
        call_wall=call_wall,
        put_wall=put_wall,
        magnet=magnet,
        flip=flip,
        max_pain=max_pain,
        total_net_gex=total_net_gex,
        regime=regime,
        dealer_sign_model="short_call_short_put",
        expiry_window=window,
        oi_timestamp=datetime.now(IST),
        oi_stale=False,
        source=f"SIMULATED: {meta['title']}",
        per_strike=per_strike_df,
    )

    # Classify RegimeView
    view = classify(
        spot=spot,
        call_wall=call_wall,
        put_wall=put_wall,
        flip=flip,
        regime=regime,
        total_net_gex=total_net_gex,
        magnet=magnet,
        max_pain=max_pain,
    )
    view.mode_label = data_dict["mode_label"]
    view.mode_plain = meta["description"]

    # Target synthetic expiry date
    exp_date = (datetime.now(IST) + timedelta(days=2)).date()

    return DashboardData(
        levels=levels,
        view=view,
        fetched_at=datetime.now(IST),
        n_contracts=250,
        expiries=[exp_date],
        window=window,
        source=f"SIMULATOR ({meta['badge']})",
        symbol=symbol,
        index_label=f"{symbol} 50",
        oi_volume={
            "call_oi": 85_400_000.0,
            "put_oi": round(85_400_000.0 * data_dict["pcr"]),
            "pcr_oi": data_dict["pcr"],
            "call_volume": 12_400_000.0,
            "put_volume": 11_200_000.0,
        },
        oi_strikes=oi_strikes,
        india_vix={"last": vix, "change": 0.25, "pct_change": 1.8},
        usdinr={"last": 83.92, "change": 0.04, "pct_change": 0.05, "as_of": "15:30"},
        news={
            "count": 3,
            "impact": 1 if regime == "NEGATIVE" else 0,
            "top": f"Replay Event: {meta['title']} active at {upto_time} IST",
        },
        vanna_now=45.2 if regime == "POSITIVE" else -82.4,
        charm_now=12.1 if regime == "POSITIVE" else -34.8,
        dte_now=0.3 if "0-DTE" in meta["badge"] else 2.1,
        day_ohlc=day_ohlc,
        source_note=f"🎬 MARKET SIMULATOR ACTIVE · Clock: {upto_time} IST",
    )


def load_historical_snapshot(
    date_ist: str,
    upto_time: str,
    symbol: str = "NIFTY",
    window: str = "weekly",
) -> DashboardData | None:
    """Reconstruct a point-in-time DashboardData from a captured historical day in data/journal/."""
    df_ts = load_day(date_ist, symbol)
    if df_ts.empty:
        log.warning("No timeseries data found for %s on %s", symbol, date_ist)
        return None

    # Slice rows <= upto_time
    df_sliced = df_ts[df_ts["time_ist"].astype(str) <= upto_time]
    if df_sliced.empty:
        df_sliced = df_ts.head(1)

    latest = df_sliced.iloc[-1].to_dict()

    spot = float(latest.get("spot", 24500.0))
    call_wall = float(latest.get("call_wall", spot + 100))
    put_wall = float(latest.get("put_wall", spot - 100))
    flip_val = latest.get("flip")
    flip = float(flip_val) if flip_val is not None and not math.isnan(float(flip_val)) else None
    max_pain = float(latest.get("max_pain", spot))
    magnet = float(latest.get("magnet", spot))
    net_gex = float(latest.get("net_gex", 0.0))
    regime = str(latest.get("regime", "POSITIVE")).upper()
    mode_label = str(latest.get("mode_label", "CAUTION"))

    # Day OHLC from sliced spots
    spots = df_sliced["spot"].dropna().tolist()
    day_ohlc = {
        "open": float(spots[0]) if spots else spot,
        "high": float(max(spots)) if spots else spot,
        "low": float(min(spots)) if spots else spot,
        "close": float(spot),
        "ltp": float(spot),
    }

    # Strikes data
    df_st = load_strikes_day(date_ist, symbol)
    if not df_st.empty:
        st_sliced = df_st[df_st["time_ist"].astype(str) <= upto_time]
        if not st_sliced.empty:
            # Last available row per strike
            st_latest = st_sliced.groupby("strike").last().reset_index()
            oi_strikes = []
            for _, r in st_latest.iterrows():
                oi_strikes.append({
                    "strike": float(r["strike"]),
                    "call_oi": float(r.get("call_oi", 0.0)),
                    "call_doi": float(r.get("call_doi", 0.0)),
                    "put_oi": float(r.get("put_oi", 0.0)),
                    "put_doi": float(r.get("put_doi", 0.0)),
                    "call_vol": float(r.get("call_vol", 0.0)),
                    "put_vol": float(r.get("put_vol", 0.0)),
                    "call_ltp": float(r.get("call_ltp", 0.0)) if r.get("call_ltp") is not None else None,
                    "put_ltp": float(r.get("put_ltp", 0.0)) if r.get("put_ltp") is not None else None,
                    "net_gex": float(r.get("net_gex", 0.0)) if r.get("net_gex") is not None else None,
                    "call_doi_near": float(r.get("call_doi_near", 0.0)),
                    "put_doi_near": float(r.get("put_doi_near", 0.0)),
                    "call_oi_near": float(r.get("call_oi_near", 0.0)),
                    "put_oi_near": float(r.get("put_oi_near", 0.0)),
                })
        else:
            oi_strikes = _build_synthetic_strikes(spot, call_wall, put_wall, net_gex)
    else:
        oi_strikes = _build_synthetic_strikes(spot, call_wall, put_wall, net_gex)

    per_strike_df = pd.DataFrame([{"strike": s["strike"], "net_gex": s.get("net_gex", 0.0)} for s in oi_strikes])

    levels = Levels(
        spot=spot,
        call_wall=call_wall,
        put_wall=put_wall,
        magnet=magnet,
        flip=flip,
        max_pain=max_pain,
        total_net_gex=net_gex,
        regime=regime,
        dealer_sign_model=str(latest.get("dealer_sign_model", "short_call_short_put")),
        expiry_window=window,
        oi_timestamp=datetime.now(IST),
        oi_stale=False,
        source=f"HISTORICAL REPLAY ({date_ist} {upto_time} IST)",
        per_strike=per_strike_df,
    )

    view = classify(
        spot=spot,
        call_wall=call_wall,
        put_wall=put_wall,
        flip=flip,
        regime=regime,
        total_net_gex=net_gex,
        magnet=magnet,
        max_pain=max_pain,
    )
    view.mode_label = mode_label

    try:
        exp_d = datetime.strptime(date_ist, "%Y-%m-%d").date()
    except Exception:
        exp_d = date.today()

    vix_val = latest.get("india_vix")
    vix = float(vix_val) if vix_val is not None and not math.isnan(float(vix_val)) else 14.5

    return DashboardData(
        levels=levels,
        view=view,
        fetched_at=datetime.now(IST),
        n_contracts=int(latest.get("n_contracts", 250)),
        expiries=[exp_d],
        window=window,
        source=f"HISTORICAL REPLAY ({date_ist})",
        symbol=symbol,
        index_label=f"{symbol} 50",
        oi_volume={
            "call_oi": float(latest.get("call_oi", 50000000)),
            "put_oi": float(latest.get("put_oi", 50000000)),
            "pcr_oi": float(latest.get("pcr_oi", 1.0)),
            "call_volume": float(latest.get("call_vol", 10000000)),
            "put_volume": float(latest.get("put_vol", 10000000)),
        },
        oi_strikes=oi_strikes,
        india_vix={"last": vix, "change": float(latest.get("vix_pct_chg", 0.0)), "pct_change": float(latest.get("vix_pct_chg", 0.0))},
        usdinr={"last": 83.90, "change": 0.0, "pct_change": 0.0, "as_of": upto_time},
        news={"count": 2, "impact": 0, "top": str(latest.get("news_top", f"Session replay {date_ist}"))},
        vanna_now=40.0,
        charm_now=10.0,
        dte_now=0.5,
        day_ohlc=day_ohlc,
        source_note=f"🎬 HISTORICAL REPLAY · {date_ist} · Clock: {upto_time} IST",
    )


# ---------------------------------------------------------------------------
# Sentinel Candle & OrderFlow Synchronization
# ---------------------------------------------------------------------------

def sync_sentinel_simulation(
    symbol: str,
    source_type: str,
    target_id: str,
    upto_time: str,
) -> None:
    """Populate Sentinel database with 1m bars and CVD synchronized to the replayed timeline."""
    ensure_sentinel_schema()
    instrument_id = f"{symbol}-FUT"
    clear_sentinel_data(instrument_id)

    elapsed = _mins_from_open(upto_time)
    if elapsed <= 0:
        elapsed = 5

    # Determine spot curve
    if source_type == "scenario":
        spots_at_steps = [_scenario_spot_and_gex(target_id, t)["spot"] for t in TIMESTAMPS_5M if _mins_from_open(t) <= elapsed]
    else:
        df_ts = load_day(target_id, symbol)
        if not df_ts.empty:
            df_s = df_ts[df_ts["time_ist"].astype(str) <= upto_time]
            spots_at_steps = df_s["spot"].dropna().tolist() if not df_s.empty else [24500.0]
        else:
            spots_at_steps = [24500.0]

    if not spots_at_steps:
        spots_at_steps = [24500.0]

    # Generate 1m bars from 09:15 up to upto_time
    now_ist = datetime.now(IST)
    try:
        from data.holidays import is_trading_day, roll_back_to_trading_day
    except Exception:
        def is_trading_day(d):
            return d.weekday() < 5
        def roll_back_to_trading_day(d):
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            return d

    if source_type == "day" and target_id:
        try:
            d_part = datetime.strptime(str(target_id)[:10], "%Y-%m-%d").date()
            ref_day = d_part
        except Exception:
            ref_day = now_ist.date()
    else:
        ref_day = now_ist.date()
        if not is_trading_day(ref_day):  # Weekend or market holiday on weekday
            ref_day = roll_back_to_trading_day(ref_day - timedelta(days=1))

    start_dt = datetime(ref_day.year, ref_day.month, ref_day.day, 9, 15, tzinfo=IST)
    bars_1m = []
    tracker = SessionVWAPTracker()
    running_cvd = 0.0

    num_bars = min(elapsed, 375)
    for m in range(num_bars):
        bar_ts = start_dt + timedelta(minutes=m)
        # Interpolate spot
        spot_idx = min(int((m / max(1, num_bars - 1)) * (len(spots_at_steps) - 1)), len(spots_at_steps) - 1)
        base_p = spots_at_steps[spot_idx]

        noise = math.sin(m * 0.4) * 4.0
        c_open = base_p + noise
        c_close = base_p + math.cos(m * 0.4) * 4.0
        c_high = max(c_open, c_close) + abs(math.sin(m * 0.7)) * 3.5
        c_low = min(c_open, c_close) - abs(math.cos(m * 0.7)) * 3.5

        # Volume & delta
        vol = int(450 + abs(math.sin(m * 0.2)) * 1200)
        # Higher volume at open and close
        if m < 30 or m > 330:
            vol = int(vol * 1.8)

        # Delta biased by price direction
        bar_move = c_close - c_open
        delta_ratio = max(-0.8, min(0.8, bar_move / 5.0))
        delta = round(vol * delta_ratio)
        running_cvd += delta

        vwap, v_s1, v_s2, v_l1, v_l2 = tracker.update(c_close, vol)

        bar = Bar(
            instrument_id=instrument_id,
            timeframe="1m",
            start_ts=bar_ts,
            open=round(c_open, 2),
            high=round(c_high, 2),
            low=round(c_low, 2),
            close=round(c_close, 2),
            volume_units=vol,
            volume_lots=round(vol / 25.0, 1),
            buy_vol_est=round((vol + delta) / 2.0, 1),
            sell_vol_est=round((vol - delta) / 2.0, 1),
            delta=delta,
            delta_ratio=round(delta_ratio, 3),
            delta_quality=1.0,
            cvd=round(running_cvd),
            vwap=vwap,
            vwap_sigma_1=v_s1,
            vwap_sigma_2=v_s2,
            vwap_lower_1=v_l1,
            vwap_lower_2=v_l2,
            oi=int(12_500_000 + m * 5000),
            delta_oi=int(delta * 0.4),
        )
        bars_1m.append(bar)
        save_bar(bar)

    # Synthesize 5m bars
    if bars_1m:
        bars_5m = aggregate_1m_to_5m(bars_1m)
        for b5 in bars_5m:
            save_bar(b5)


# ---------------------------------------------------------------------------
# Global Accessor for Cockpit Dashboard
# ---------------------------------------------------------------------------

def get_active_replay_snapshot(symbol: str = "NIFTY", window: str = "weekly") -> DashboardData | None:
    """Retrieve the active replay snapshot according to st.session_state configuration."""
    try:
        import streamlit as st
    except ImportError:
        return None

    if not st.session_state.get("replay_mode_active", False):
        return None

    source_type = st.session_state.get("replay_source_type", "scenario")
    upto_time = st.session_state.get("replay_clock", "13:45")

    if source_type == "scenario":
        scen_id = st.session_state.get("replay_scenario_id", "expiry_gamma_blast")
        return generate_scenario_snapshot(scen_id, upto_time, symbol=symbol, window=window)
    else:
        hist_day = st.session_state.get("replay_historical_day")
        if not hist_day:
            avail = get_available_historical_days(symbol)
            hist_day = avail[0] if avail else "2026-10-06"
        return load_historical_snapshot(hist_day, upto_time, symbol=symbol, window=window)
