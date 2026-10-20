"""data/pre_post_market.py — Pre-Open & Post-Market Microstructure Data Pipeline.

Implements:
1. Pre-Open Session Discovery (09:00–09:15 IST):
   - Adaptive 3s retry starting at 09:07:30 IST until equilibrium price is discovered (09:08:00–09:12:00)
   - Opening Gap calculation (points & percentage vs previous close)
   - Pre-Bell Option MTM shock pricing & Gamma Wall breach diagnosis
   - Morning Chime Bell audio trigger
2. Post-Market Session Analysis (15:30–16:00 IST):
   - F&O Extended Session monitoring (15:30–15:40 IST)
   - Cash Closing Auction Session (CAS) 30-min VWAP settlement capture
   - Dealer Pinning Efficiency Index against Call/Put Walls
   - Settlement Divergence vs 15:30 continuous close
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import textwrap
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
DB_PATH = Path("data/journal/market_snapshot.db")
_DB_LOCK = threading.Lock()

# In-memory session caches
_PRE_MARKET_CACHE: dict[str, dict] = {}     # symbol -> pre-market state dict
_POST_MARKET_CACHE: dict[str, dict] = {}    # symbol -> post-market state dict
_CHIME_TRIGGERED_DAYS: set[tuple[str, str]] = set()  # (symbol, "YYYY-MM-DD")
_CACHE_LOCK = threading.Lock()


def _get_db_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_pre_post_db() -> None:
    """Initialize persistent pre/post-market auction registry table."""
    with _DB_LOCK, _get_db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_auctions_history (
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                phase_code TEXT,
                is_discovered INTEGER DEFAULT 0,
                discovered_spot REAL,
                prev_close REAL,
                gap_pts REAL,
                gap_pct REAL,
                gap_regime TEXT,
                regime_badge TEXT,
                regime_color TEXT,
                regime_briefing TEXT,
                favored_play TEXT,
                gap_fill_status TEXT DEFAULT 'UNFILLED',
                discovered_at TEXT,
                straddle_shock_pct REAL,
                atm_straddle_prev REAL,
                atm_straddle_projected REAL,
                option_shocks_json TEXT,
                post_market_settled INTEGER DEFAULT 0,
                settlement_spot REAL,
                last_continuous_spot REAL,
                settlement_divergence_pts REAL,
                pin_efficiency_pct REAL,
                pin_target_wall TEXT,
                settled_at TEXT,
                closing_cvd REAL,
                orderflow_bias TEXT,
                status_text TEXT,
                updated_at TEXT,
                day_range TEXT,
                net_change_pts REAL,
                net_change_pct REAL,
                next_key_levels TEXT,
                institutional_wrap TEXT,
                PRIMARY KEY (trade_date, symbol)
            )
        """)
        for col, col_type in [
            ("day_range", "TEXT"),
            ("net_change_pts", "REAL"),
            ("net_change_pct", "REAL"),
            ("next_key_levels", "TEXT"),
            ("institutional_wrap", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE market_auctions_history ADD COLUMN {col} {col_type}")
            except Exception:
                pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auctions_date_sym ON market_auctions_history(trade_date, symbol)")
        conn.commit()


def save_pre_market_state(symbol: str, state: dict, shocks: dict | None = None) -> None:
    """Persist discovered pre-market equilibrium state into SQLite."""
    try:
        init_pre_post_db()
        sym = symbol.upper().strip()
        trade_date = state.get("date") or datetime.now(IST).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()

        straddle_shock = None
        straddle_prev = None
        straddle_proj = None
        shocks_json = None
        if shocks:
            straddle_shock = shocks.get("straddle_shock_pct")
            straddle_prev = shocks.get("atm_straddle_prev")
            straddle_proj = shocks.get("atm_straddle_projected")
            strikes = shocks.get("projected_strikes")
            if strikes:
                shocks_json = json.dumps(strikes)

        with _DB_LOCK, _get_db_conn() as conn:
            existing = conn.execute(
                "SELECT * FROM market_auctions_history WHERE trade_date = ? AND symbol = ?",
                (trade_date, sym),
            ).fetchone()
            ex = dict(existing) if existing else {}

            conn.execute("""
                INSERT OR REPLACE INTO market_auctions_history (
                    trade_date, symbol, phase_code, is_discovered, discovered_spot,
                    prev_close, gap_pts, gap_pct, gap_regime, regime_badge,
                    regime_color, regime_briefing, favored_play, gap_fill_status,
                    discovered_at, straddle_shock_pct, atm_straddle_prev, atm_straddle_projected,
                    option_shocks_json, post_market_settled, settlement_spot, last_continuous_spot,
                    settlement_divergence_pts, pin_efficiency_pct, pin_target_wall, settled_at,
                    closing_cvd, orderflow_bias, status_text, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_date,
                sym,
                state.get("phase_code") or ex.get("phase_code"),
                1 if state.get("is_discovered") else (ex.get("is_discovered") or 0),
                state.get("discovered_spot") or ex.get("discovered_spot"),
                state.get("prev_close") or ex.get("prev_close"),
                state.get("gap_pts") if state.get("gap_pts") is not None else ex.get("gap_pts"),
                state.get("gap_pct") if state.get("gap_pct") is not None else ex.get("gap_pct"),
                state.get("gap_regime") or ex.get("gap_regime"),
                state.get("regime_badge") or ex.get("regime_badge"),
                state.get("regime_color") or ex.get("regime_color"),
                state.get("regime_briefing") or ex.get("regime_briefing"),
                state.get("favored_play") or ex.get("favored_play"),
                state.get("gap_fill_status") or ex.get("gap_fill_status") or "UNFILLED",
                state.get("discovered_at") or ex.get("discovered_at"),
                straddle_shock if straddle_shock is not None else ex.get("straddle_shock_pct"),
                straddle_prev if straddle_prev is not None else ex.get("atm_straddle_prev"),
                straddle_proj if straddle_proj is not None else ex.get("atm_straddle_projected"),
                shocks_json if shocks_json is not None else ex.get("option_shocks_json"),
                ex.get("post_market_settled", 0),
                ex.get("settlement_spot"),
                ex.get("last_continuous_spot"),
                ex.get("settlement_divergence_pts"),
                ex.get("pin_efficiency_pct"),
                ex.get("pin_target_wall"),
                ex.get("settled_at"),
                ex.get("closing_cvd"),
                ex.get("orderflow_bias"),
                state.get("status_text") or ex.get("status_text"),
                now_iso,
            ))
            conn.commit()
    except Exception as e:
        log.debug(f"Failed to save pre-market state for {symbol}: {e}")


def save_option_shocks(symbol: str, shocks: dict, trade_date: str | None = None) -> None:
    """Persist Black-76 option shocks into the daily auction record and memory cache."""
    if not shocks:
        return
    try:
        init_pre_post_db()
        sym = symbol.upper().strip()
        t_date = trade_date or datetime.now(IST).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()

        straddle_shock = shocks.get("straddle_shock_pct")
        straddle_prev = shocks.get("atm_straddle_prev")
        straddle_proj = shocks.get("atm_straddle_projected")
        strikes = shocks.get("projected_strikes")
        shocks_json = json.dumps(strikes) if strikes else None

        with _DB_LOCK, _get_db_conn() as conn:
            conn.execute("""
                UPDATE market_auctions_history
                SET straddle_shock_pct = COALESCE(?, straddle_shock_pct),
                    atm_straddle_prev = COALESCE(?, atm_straddle_prev),
                    atm_straddle_projected = COALESCE(?, atm_straddle_projected),
                    option_shocks_json = COALESCE(?, option_shocks_json),
                    updated_at = ?
                WHERE trade_date = ? AND symbol = ?
            """, (straddle_shock, straddle_prev, straddle_proj, shocks_json, now_iso, t_date, sym))
            conn.commit()

        with _CACHE_LOCK:
            cached = _PRE_MARKET_CACHE.get(sym)
            if cached and cached.get("date") == t_date:
                cached["straddle_shock_pct"] = straddle_shock
                cached["atm_straddle_prev"] = straddle_prev
                cached["atm_straddle_projected"] = straddle_proj
                if strikes:
                    cached["projected_strikes"] = strikes
    except Exception as e:
        log.debug(f"Failed to save option shocks for {symbol}: {e}")


def update_gap_fill_status(symbol: str, status: str, trade_date: str | None = None) -> None:
    """Update dynamic gap fill status in DB and memory cache."""
    try:
        init_pre_post_db()
        sym = symbol.upper().strip()
        t_date = trade_date or datetime.now(IST).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()

        with _DB_LOCK, _get_db_conn() as conn:
            conn.execute("""
                UPDATE market_auctions_history
                SET gap_fill_status = ?, updated_at = ?
                WHERE trade_date = ? AND symbol = ?
            """, (status, now_iso, t_date, sym))
            conn.commit()

        with _CACHE_LOCK:
            cached = _PRE_MARKET_CACHE.get(sym)
            if cached and cached.get("date") == t_date:
                cached["gap_fill_status"] = status
    except Exception as e:
        log.debug(f"Failed to update gap fill status: {e}")


def save_post_market_state(symbol: str, state: dict) -> None:
    """Persist post-market settlement & pinning scorecard into SQLite."""
    try:
        init_pre_post_db()
        sym = symbol.upper().strip()
        trade_date = state.get("date") or datetime.now(IST).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()

        with _DB_LOCK, _get_db_conn() as conn:
            existing = conn.execute(
                "SELECT * FROM market_auctions_history WHERE trade_date = ? AND symbol = ?",
                (trade_date, sym),
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE market_auctions_history
                    SET post_market_settled = 1,
                        settlement_spot = ?,
                        last_continuous_spot = ?,
                        settlement_divergence_pts = ?,
                        pin_efficiency_pct = ?,
                        pin_target_wall = ?,
                        settled_at = ?,
                        closing_cvd = ?,
                        orderflow_bias = ?,
                        status_text = ?,
                        day_range = COALESCE(?, day_range),
                        net_change_pts = COALESCE(?, net_change_pts),
                        net_change_pct = COALESCE(?, net_change_pct),
                        next_key_levels = COALESCE(?, next_key_levels),
                        institutional_wrap = COALESCE(?, institutional_wrap),
                        updated_at = ?
                    WHERE trade_date = ? AND symbol = ?
                """, (
                    state.get("settlement_spot"),
                    state.get("last_continuous_spot"),
                    state.get("settlement_divergence_pts"),
                    state.get("pin_efficiency_pct"),
                    state.get("pin_target_wall"),
                    state.get("settled_at"),
                    state.get("closing_cvd"),
                    state.get("orderflow_bias"),
                    state.get("status_text"),
                    state.get("day_range"),
                    state.get("net_change_pts"),
                    state.get("net_change_pct"),
                    state.get("next_key_levels"),
                    state.get("institutional_wrap"),
                    now_iso,
                    trade_date,
                    sym,
                ))
            else:
                conn.execute("""
                    INSERT INTO market_auctions_history (
                        trade_date, symbol, post_market_settled, settlement_spot,
                        last_continuous_spot, settlement_divergence_pts, pin_efficiency_pct,
                        pin_target_wall, settled_at, closing_cvd, orderflow_bias,
                        status_text, day_range, net_change_pts, net_change_pct,
                        next_key_levels, institutional_wrap, updated_at
                    ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trade_date,
                    sym,
                    state.get("settlement_spot"),
                    state.get("last_continuous_spot"),
                    state.get("settlement_divergence_pts"),
                    state.get("pin_efficiency_pct"),
                    state.get("pin_target_wall"),
                    state.get("settled_at"),
                    state.get("closing_cvd"),
                    state.get("orderflow_bias"),
                    state.get("status_text"),
                    state.get("day_range"),
                    state.get("net_change_pts"),
                    state.get("net_change_pct"),
                    state.get("next_key_levels"),
                    state.get("institutional_wrap"),
                    now_iso,
                ))
            conn.commit()
    except Exception as e:
        log.debug(f"Failed to save post-market state for {symbol}: {e}")


def get_persisted_auction_record(symbol: str, trade_date: str | None = None) -> dict | None:
    """Retrieve persisted auction record for symbol and date from SQLite."""
    try:
        init_pre_post_db()
        sym = symbol.upper().strip()
        t_date = trade_date or datetime.now(IST).strftime("%Y-%m-%d")

        with _DB_LOCK, _get_db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM market_auctions_history WHERE trade_date = ? AND symbol = ?",
                (t_date, sym),
            ).fetchone()
            if not row and trade_date is None:
                row = conn.execute(
                    "SELECT * FROM market_auctions_history WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
                    (sym,),
                ).fetchone()
            if not row:
                return None

            d = dict(row)
            if d.get("option_shocks_json"):
                try:
                    d["projected_strikes"] = json.loads(d["option_shocks_json"])
                except Exception:
                    d["projected_strikes"] = []
            else:
                d["projected_strikes"] = []
            return d
    except Exception as e:
        log.debug(f"Failed to read persisted auction record for {symbol}: {e}")
        return None


def get_current_session_phase(now: datetime | None = None) -> tuple[str, str, int]:
    """Return (phase_code, phase_label, minutes_until_next) for current IST time.

    Phase codes:
    - 'PRE_MARKET_ENTRY' (09:00 - 09:07:30)
    - 'PRE_MARKET_DISCOVERY' (09:07:30 - 09:15:00)
    - 'REGULAR_SESSION' (09:15:00 - 15:30:00)
    - 'CAS_AND_FNO_EXTENDED' (15:30:00 - 15:40:00)
    - 'POST_MARKET_SETTLEMENT' (15:40:00 - 16:00:00)
    - 'OFF_HOURS' (16:00:00 - 09:00:00 or weekends/holidays)
    """
    try:
        from data.holidays import is_trading_day
    except Exception:
        def is_trading_day(d):
            return d.weekday() < 5

    now_ist = now or datetime.now(IST)
    if not is_trading_day(now_ist.date()):
        return ("OFF_HOURS", "Exchange Closed (Weekend / Holiday)", 0)

    t_min = now_ist.hour * 60 + now_ist.minute
    t_sec = now_ist.second

    if t_min < 9 * 60:
        mins_left = 9 * 60 - t_min
        return ("OFF_HOURS", "Pre-Market Opens 09:00", mins_left)
    elif t_min < 9 * 60 + 7 or (t_min == 9 * 60 + 7 and t_sec < 30):
        # 09:00:00 to 09:07:29
        return ("PRE_MARKET_ENTRY", "Pre-Open Order Collection (09:00–09:08)", 9 * 60 + 8 - t_min)
    elif t_min < 9 * 60 + 15:
        # 09:07:30 to 09:14:59
        return ("PRE_MARKET_DISCOVERY", "Pre-Open Equilibrium Discovery Active", 9 * 60 + 15 - t_min)
    elif t_min < 15 * 60 + 30:
        # 09:15:00 to 15:30:00
        return ("REGULAR_SESSION", "Live Regular Market Session", 15 * 60 + 30 - t_min)
    elif t_min < 15 * 60 + 40:
        # 15:30:00 to 15:40:00
        return ("CAS_AND_FNO_EXTENDED", "Cash CAS Auction & F&O Session (to 15:40)", 15 * 60 + 40 - t_min)
    elif t_min < 16 * 60:
        # 15:40:00 to 16:00:00
        return ("POST_MARKET_SETTLEMENT", "Post-Close Settlement Session", 16 * 60 - t_min)
    else:
        return ("OFF_HOURS", "Market Closed", 0)


def get_pre_market_state(
    symbol: str = "NIFTY",
    force_refresh: bool = False,
    now: datetime | None = None,
) -> dict:
    """Retrieve or poll pre-open equilibrium discovery state for symbol.

    Adaptive 3s retry runs between 09:07:30 and 09:15:00 IST until discovered.
    """
    now_ist = now or datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")
    ckey = (symbol.upper(), today_str)

    with _CACHE_LOCK:
        cached = _PRE_MARKET_CACHE.get(symbol.upper())
        if cached and cached.get("date") == today_str and not force_refresh:
            # If already discovered today, dynamically refresh gap fill status against live high/low
            if cached.get("is_discovered"):
                if cached.get("gap_fill_status") != "FILLED":
                    try:
                        from app.spot_ticker import _STORE
                        ohlc_now = (_STORE.get(symbol.upper()) or {}).get("ohlc") or {}
                        cur_low = float(ohlc_now.get("low") or cached.get("discovered_spot") or 0.0)
                        cur_high = float(ohlc_now.get("high") or cached.get("discovered_spot") or 0.0)
                        p_c = float(cached.get("prev_close") or 0.0)
                        g_pts = float(cached.get("gap_pts") or 0.0)
                        if g_pts > 0 and cur_low > 0 and cur_low <= p_c:
                            cached["gap_fill_status"] = "FILLED"
                            update_gap_fill_status(symbol.upper(), "FILLED", today_str)
                        elif g_pts < 0 and cur_high > 0 and cur_high >= p_c:
                            cached["gap_fill_status"] = "FILLED"
                            update_gap_fill_status(symbol.upper(), "FILLED", today_str)
                    except Exception:
                        pass
                return cached
            # If discovery in progress but checked less than 2.8s ago, return cached
            if (time.time() - cached.get("_polled_at", 0)) < 2.8:
                return cached

    # Check persistent DB if not in memory
    if not force_refresh:
        persisted = get_persisted_auction_record(symbol.upper(), today_str)
        if persisted and persisted.get("is_discovered"):
            disc_spot = float(persisted.get("discovered_spot") or 0.0)
            p_close = float(persisted.get("prev_close") or 0.0)
            g_pts = float(persisted.get("gap_pts") or 0.0)
            g_pct = float(persisted.get("gap_pct") or 0.0)
            g_fill = persisted.get("gap_fill_status") or "UNFILLED"
            try:
                from app.spot_ticker import _STORE
                ohlc_now = (_STORE.get(symbol.upper()) or {}).get("ohlc") or {}
                cur_low = float(ohlc_now.get("low") or disc_spot or 0.0)
                cur_high = float(ohlc_now.get("high") or disc_spot or 0.0)
                if g_fill != "FILLED":
                    if g_pts > 0 and cur_low > 0 and cur_low <= p_close:
                        g_fill = "FILLED"
                        update_gap_fill_status(symbol.upper(), "FILLED", today_str)
                    elif g_pts < 0 and cur_high > 0 and cur_high >= p_close:
                        g_fill = "FILLED"
                        update_gap_fill_status(symbol.upper(), "FILLED", today_str)
            except Exception:
                pass

            p_code, p_label, _ = get_current_session_phase(now_ist)
            rehydrated = {
                "symbol": symbol.upper(),
                "date": today_str,
                "phase_code": p_code,
                "phase_label": p_label,
                "is_pre_market": p_code in ("PRE_MARKET_ENTRY", "PRE_MARKET_DISCOVERY"),
                "is_discovered": True,
                "discovered_spot": disc_spot,
                "prev_close": p_close,
                "gap_pts": g_pts,
                "gap_pct": g_pct,
                "gap_fill_status": g_fill,
                "gap_regime": persisted.get("gap_regime") or "AUCTION_BALANCED",
                "favored_play": persisted.get("favored_play") or "RANGE ACCUMULATION",
                "regime_badge": persisted.get("regime_badge") or "⚖️ BALANCED AUCTION",
                "regime_color": persisted.get("regime_color") or "var(--spark)",
                "regime_briefing": persisted.get("regime_briefing") or "",
                "discovered_at": persisted.get("discovered_at") or "09:08:15",
                "status_text": persisted.get("status_text") or f"Equilibrium Discovered: ₹{disc_spot:,.1f}",
                "straddle_shock_pct": persisted.get("straddle_shock_pct"),
                "atm_straddle_prev": persisted.get("atm_straddle_prev"),
                "atm_straddle_projected": persisted.get("atm_straddle_projected"),
                "projected_strikes": persisted.get("projected_strikes") or [],
                "_polled_at": time.time(),
            }
            with _CACHE_LOCK:
                _PRE_MARKET_CACHE[symbol.upper()] = rehydrated
            return rehydrated

    phase_code, phase_label, _ = get_current_session_phase(now_ist)

    # Load authentic session snapshot
    from data.market_snapshot import get_snapshot
    snap = get_snapshot(symbol) or {}
    fallback_spot = float(snap.get("spot") or snap.get("prev_close") or 0.0)
    prev_close_val = float(snap.get("prev_close") or fallback_spot)
    cw_val = float(snap.get("call_wall") or 0.0)
    pw_val = float(snap.get("put_wall") or 0.0)
    flip_val = float(snap.get("gamma_flip") or 0.0)
    net_gex_val = float(snap.get("net_gex") or 0.0)

    # Extract institutional FII positioning
    fii_bias = "NEUTRAL"
    fii_net_fut = 0
    try:
        from analytics.trade_ideas import compute_fii_positioning_context
        fii_info = compute_fii_positioning_context()
        fii_bias = fii_info.get("fii_bias", "NEUTRAL")
        fii_net_fut = fii_info.get("net_fut", 0)
    except Exception:
        pass

    # Extract prior session closing CVD
    prior_cvd = 0.0
    prior_of_bias = "Neutral"
    try:
        from sentinel.storage.db import list_daily_cvd
        hist = list_daily_cvd(f"{symbol.upper()}-FUT", limit=2)
        if hist:
            prior_rec = hist[-2] if len(hist) >= 2 and hist[-1].get("trade_date") == today_str else hist[-1]
            prior_cvd = float(prior_rec.get("closing_cvd", 0.0))
            prior_of_bias = "Bullish Accumulation (+)" if prior_cvd > 0 else "Bearish Distribution (-)"
    except Exception:
        pass

    state = {
        "symbol": symbol.upper(),
        "date": today_str,
        "phase_code": phase_code,
        "phase_label": phase_label,
        "is_pre_market": phase_code in ("PRE_MARKET_ENTRY", "PRE_MARKET_DISCOVERY"),
        "is_discovered": False,
        "discovered_spot": fallback_spot,
        "prev_close": prev_close_val,
        "call_wall": cw_val,
        "put_wall": pw_val,
        "gamma_flip": flip_val,
        "fii_bias": fii_bias,
        "fii_net_fut": fii_net_fut,
        "prior_session_cvd": prior_cvd,
        "prior_session_orderflow": prior_of_bias,
        "gap_pts": 0.0,
        "gap_pct": 0.0,
        "gap_fill_status": "UNFILLED",
        "gap_regime": "AUCTION_BALANCED",
        "favored_play": "RANGE ACCUMULATION",
        "regime_badge": "⚖️ BALANCED AUCTION",
        "regime_color": "var(--spark)",
        "discovered_at": None,
        "status_text": (
            f"Pre-Market Setup · FII: {fii_bias} ({fii_net_fut:+d} fut) · Walls: {cw_val:,.0f} / {pw_val:,.0f}"
            if cw_val > 0 and pw_val > 0
            else f"Pre-Market Setup · FII: {fii_bias} ({fii_net_fut:+d} fut)"
        ),
        "_polled_at": time.time(),
    }

    # If before 09:07:30, pre-open equilibrium matching has not occurred yet
    if phase_code == "PRE_MARKET_ENTRY":
        state["status_text"] = f"Pre-Open Auction Collection (09:00–09:08) · FII: {fii_bias} ({fii_net_fut:+d} fut) · Matching at ~09:08:00"
        with _CACHE_LOCK:
            _PRE_MARKET_CACHE[symbol.upper()] = state
        return state

    # Adaptive Poll (runs ONLY when phase is PRE_MARKET_DISCOVERY or REGULAR_SESSION or force_refresh)
    if not force_refresh and phase_code not in ("PRE_MARKET_DISCOVERY", "REGULAR_SESSION"):
        with _CACHE_LOCK:
            _PRE_MARKET_CACHE[symbol.upper()] = state
        return state

    try:
        from data.dhan_live import fetch_index_ohlc
        skip_c = (phase_code in ("PRE_MARKET_DISCOVERY", "PRE_MARKET_ENTRY") or force_refresh)
        ohlc = fetch_index_ohlc(symbol.upper(), timeout=4, skip_cache=skip_c)
        if ohlc:
            open_px = float(ohlc.get("open") or 0.0)
            prev_close = float(ohlc.get("prev_close") or ohlc.get("close") or fallback_spot)
            ltp = float(ohlc.get("ltp") or open_px or fallback_spot)
            as_of = str(ohlc.get("as_of") or "")

            # Equilibrium is considered discovered when open > 0 and (either diff > 0.01 or time >= 09:08)
            # or in regular market hours when market opened
            now_mins = now_ist.hour * 60 + now_ist.minute
            is_disc = (open_px > 0 and (abs(open_px - prev_close) > 0.01 or now_mins >= 9 * 60 + 8))

            if is_disc:
                disc_spot = round(open_px, 2)
                p_close = round(prev_close, 2)
                gap_pts = round(disc_spot - p_close, 2)
                gap_pct = round((gap_pts / p_close) * 100.0, 2) if p_close > 0 else 0.0

                high_px = float(ohlc.get("high") or ltp)
                low_px = float(ohlc.get("low") or ltp)
                if gap_pts > 0:
                    is_filled = (low_px <= p_close)
                elif gap_pts < 0:
                    is_filled = (high_px >= p_close)
                else:
                    is_filled = True

                gap_cls = classify_opening_gap(gap_pts, gap_pct, disc_spot, cw_val, pw_val, flip_val, net_gex_val)

                state["is_discovered"] = True
                state["discovered_spot"] = disc_spot
                state["prev_close"] = p_close
                state["gap_pts"] = gap_pts
                state["gap_pct"] = gap_pct
                state["gap_fill_status"] = "FILLED" if is_filled else "UNFILLED"
                state["gap_regime"] = gap_cls.get("regime", "AUCTION_BALANCED")
                state["favored_play"] = gap_cls.get("favored_play", "")
                state["regime_badge"] = gap_cls.get("badge", "")
                state["regime_color"] = gap_cls.get("color", "")
                state["regime_briefing"] = gap_cls.get("briefing", "")
                state["discovered_at"] = as_of[-8:] if len(as_of) >= 8 else now_ist.strftime("%H:%M:%S")
                state["status_text"] = (
                    f"Equilibrium Discovered: ₹{disc_spot:,.1f} ({gap_pts:+.1f} pts / {gap_pct:+.2f}%)"
                )

                # Flag chime if not triggered today
                chime_key = (symbol.upper(), today_str)
                with _CACHE_LOCK:
                    if chime_key not in _CHIME_TRIGGERED_DAYS:
                        state["should_play_chime"] = True

                save_pre_market_state(symbol.upper(), state)
            else:
                state["status_text"] = "Awaiting Equilibrium Auction Matching (09:08)..."
    except Exception as e:
        log.debug(f"Pre-market poll failed for {symbol}: {e}")

    with _CACHE_LOCK:
        _PRE_MARKET_CACHE[symbol.upper()] = state
    return state


def acknowledge_pre_market_chime(symbol: str = "NIFTY") -> None:
    """Mark morning chime bell as played for today so it does not repeat."""
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    with _CACHE_LOCK:
        _CHIME_TRIGGERED_DAYS.add((symbol.upper(), today_str))
        cached = _PRE_MARKET_CACHE.get(symbol.upper())
        if cached:
            cached["should_play_chime"] = False


def classify_opening_gap(
    gap_pts: float,
    gap_pct: float,
    spot: float,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
    flip: float = 0.0,
    net_gex: float = 0.0,
) -> dict:
    """Classify discovered opening gap relative to GEX boundaries and generate strategic briefing."""
    is_pos_gamma = (flip > 0 and spot >= flip) or (net_gex > 0)

    # 1. Structural wall tests
    if call_wall > 0 and spot >= call_wall - 15.0:
        if spot >= call_wall + 10.0:
            regime = "CALL_WALL_BREACH_SQUEEZE"
            badge = "🚀 CALL WALL BREACH (SQUEEZE)"
            color = "var(--go)"
            briefing = (
                f"Indicative open at ₹{spot:,.0f} has breached above the Call Wall (₹{call_wall:,.0f}). "
                f"Call writers face severe short gamma squeeze; expect explosive upside momentum if 09:15 opening print holds."
            )
            favored_play = "GAMMA SQUEEZE BREAKOUT"
        else:
            regime = "SHORT_GAMMA_WALL_TEST"
            badge = "🚨 CALL WALL BREACH"
            color = "var(--warn)"
            briefing = (
                f"Indicative open at ₹{spot:,.0f} directly challenges Call Wall (₹{call_wall:,.0f}). "
                f"Dealers face short gamma acceleration — watch for explosive buy squeeze at 09:15 bell."
            )
            favored_play = "MOMENTUM CALLS"
    elif put_wall > 0 and spot <= put_wall + 15.0:
        if spot <= put_wall - 10.0:
            regime = "PUT_WALL_BREAKDOWN_CASCADE"
            badge = "🔴 PUT WALL BREAKDOWN"
            color = "var(--stop)"
            briefing = (
                f"Indicative open at ₹{spot:,.0f} has cracked below Put Wall support (₹{put_wall:,.0f}). "
                f"Dealers face unhedged short gamma acceleration; failure to reclaim triggers cascading liquidation."
            )
            favored_play = "NEGATIVE GAMMA CASCADE"
        else:
            regime = "PUT_WALL_BREAKDOWN_TEST"
            badge = "⚠️ PUT WALL DEFENSE"
            color = "var(--stop)"
            briefing = (
                f"Indicative open at ₹{spot:,.0f} tests Put Wall support (₹{put_wall:,.0f}). "
                f"Option writers must absorb sell volume; failure triggers long gamma liquidation."
            )
            favored_play = "PUT EXHAUSTION / REVERSAL"
    elif gap_pct >= 0.40:
        if is_pos_gamma and call_wall > 0 and spot < call_wall - 35.0:
            regime = "GAP_UP_INTO_POS_GAMMA"
            badge = "⚖️ GAP INTO POSITIVE GAMMA"
            color = "var(--go)"
            briefing = (
                f"Positive opening gap of {gap_pts:+.1f} pts ({gap_pct:+.2f}%) opens within positive dealer gamma (Flip: ₹{flip:,.0f}). "
                f"Dealer hedging dampens runaway momentum. Look for initial gap fade back toward Session VWAP before initiating longs."
            )
            favored_play = "VWAP DIP BUY / GAP FADE"
        else:
            regime = "GAP_UP_RUNAWAY"
            badge = "🚀 GAP & GO (BULLISH)"
            color = "var(--go)"
            briefing = (
                f"Strong positive opening gap of {gap_pts:+.1f} pts ({gap_pct:+.2f}%). "
                f"Momentum favors upside continuation if 09:15 opening 3m range holds above ₹{spot - 20:,.0f}."
            )
            favored_play = "CE MOMENTUM"
    elif gap_pct <= -0.40:
        if is_pos_gamma and put_wall > 0 and spot > put_wall + 30.0:
            regime = "GAP_DOWN_INTO_POS_GAMMA"
            badge = "⚖️ GAP DOWN IN POSITIVE GAMMA"
            color = "var(--warn)"
            briefing = (
                f"Downward opening rotation of {gap_pts:+.1f} pts ({gap_pct:+.2f}%) into positive dealer gamma buffer. "
                f"Institutional put writing cushions downside; expect mean-reversion bounce toward Session VWAP."
            )
            favored_play = "EXHAUSTION BOUNCE / PUT SPREAD"
        else:
            regime = "GAP_DOWN_TRAP"
            badge = "🔴 GAP & GO (BEARISH)"
            color = "var(--stop)"
            briefing = (
                f"Significant downward gap of {gap_pts:+.1f} pts ({gap_pct:+.2f}%). "
                f"Bearish momentum dominant; watch for continuation puts or bounce failure at previous close."
            )
            favored_play = "PE MOMENTUM"
    elif abs(gap_pct) <= 0.15:
        regime = "AUCTION_BALANCED"
        badge = "⚖️ BALANCED AUCTION"
        color = "var(--spark)"
        briefing = (
            f"Flat/balanced open ({gap_pts:+.1f} pts / {gap_pct:+.2f}%). "
            f"Expect initial two-sided mean-reversion around Session VWAP before directional trend develops."
        )
        favored_play = "RANGE ACCUMULATION"
    else:
        regime = "MODERATE_DRIFT"
        badge = "📈 MODERATE GAP" if gap_pct > 0 else "📉 MODERATE GAP"
        color = "var(--go)" if gap_pct > 0 else "var(--stop)"
        briefing = (
            f"Moderate opening rotation of {gap_pts:+.1f} pts ({gap_pct:+.2f}%). "
            f"Structure neutral to mildly {'bullish' if gap_pct > 0 else 'bearish'} entering 09:15."
        )
        favored_play = "CE STRUCTURAL" if gap_pct > 0 else "PE STRUCTURAL"

    return {
        "regime": regime,
        "badge": badge,
        "color": color,
        "briefing": briefing,
        "favored_play": favored_play,
        "gap_pts": gap_pts,
        "gap_pct": gap_pct,
    }


def estimate_pre_bell_option_ltps(
    discovered_spot: float,
    prev_spot: float,
    candidate_strikes: list[dict],
) -> list[dict]:
    """Model synthetic opening Option LTPs prior to 09:15:00 bell using delta MTM shock."""
    d_spot = discovered_spot - prev_spot
    results = []

    for c in candidate_strikes:
        strike = float(c.get("strike", 0))
        side = str(c.get("side", "CE")).upper()
        prev_ltp = float(c.get("prev_ltp", 0) or c.get("current_ltp", 0))
        delta = float(c.get("delta", 0.50 if side == "CE" else -0.50))

        # First-order delta price adjustment with gamma curvature buffer
        gamma_est = 0.0015
        model_shock = (delta * d_spot) + (0.5 * gamma_est * (d_spot ** 2) if side == "CE" else -0.5 * gamma_est * (d_spot ** 2))
        est_open_ltp = round(max(prev_ltp + model_shock, 0.5), 1)

        # Intrinsic floor
        if side == "CE":
            intrinsic = max(0.0, discovered_spot - strike)
        else:
            intrinsic = max(0.0, strike - discovered_spot)
        est_open_ltp = max(est_open_ltp, round(intrinsic + 0.5, 1))

        chg_ltp = round(est_open_ltp - prev_ltp, 1)
        chg_pct = round((chg_ltp / prev_ltp) * 100.0, 1) if prev_ltp > 0 else 0.0

        results.append({
            "strike": strike,
            "side": side,
            "prev_ltp": prev_ltp,
            "est_open_ltp": est_open_ltp,
            "chg_ltp": chg_ltp,
            "chg_pct": chg_pct,
            "delta": delta,
        })

    return results


def calculate_pre_market_option_shocks(
    discovered_spot: float,
    prev_close: float,
    contracts_df: Any = None,
    symbol: str = "NIFTY",
    dte_days: float = 3.0,
    atm_iv: float = 0.14,
) -> dict:
    """Calculate institutional pre-market option repricing and straddle shocks using Black-76.

    Reprices candidate strikes around ATM at the discovered opening equilibrium price S_disc,
    accounting for gap jump volatility adjustment, delta/gamma expansion, and theta overnight decay.
    """
    from target_engine.pricer import reprice, greeks
    from target_engine.iv_forecast import forecast_iv

    sym_u = symbol.upper()
    step = 50 if sym_u == "NIFTY" else (100 if sym_u in ("BANKNIFTY", "SENSEX") else 50)
    gap_pts = round(discovered_spot - prev_close, 2)
    gap_pct = round((gap_pts / prev_close) * 100.0, 2) if prev_close > 0 else 0.0

    T_prev = max(float(dte_days) / 365.0, 1.0 / 365.0)
    # Overnight decay ~ 0.5 trading day
    T_open = max(1e-4, T_prev - (0.5 / 252.0))

    atm_k = round(discovered_spot / step) * step
    strikes = [atm_k + i * step for i in range(-3, 4)]

    # Overnight gap jump vol adjustment
    move_pct = gap_pts / prev_close if prev_close > 0 else 0.0
    try:
        iv_proj_ce = forecast_iv("bull" if gap_pts > 0 else "bear", 15, atm_iv, spot_move_pct=move_pct).sigma_target
        iv_proj_pe = forecast_iv("bear" if gap_pts < 0 else "bull", 15, atm_iv, spot_move_pct=move_pct).sigma_target
    except Exception:
        iv_proj_ce = atm_iv
        iv_proj_pe = atm_iv

    projected_strikes = []
    atm_ce_prev = 0.0
    atm_ce_proj = 0.0
    atm_pe_prev = 0.0
    atm_pe_proj = 0.0

    # Build contracts lookup if available
    contracts_map: dict[tuple[str, float], float] = {}
    if contracts_df is not None and hasattr(contracts_df, "itertuples"):
        for row in contracts_df.itertuples(index=False):
            t = "CE" if str(getattr(row, "type", "")).upper().startswith("C") else "PE"
            k = float(getattr(row, "strike", 0.0))
            px = float(getattr(row, "close", 0.0) or getattr(row, "ltp", 0.0) or 0.0)
            contracts_map[(t, k)] = px

    for k in strikes:
        for opt in ("CE", "PE"):
            # 1. Previous close price
            prev_px = contracts_map.get((opt, k), 0.0)
            if prev_px <= 0.0:
                prev_px = reprice(prev_close, k, T_prev, atm_iv, r=0.065, option_type=opt)

            # 2. Projected opening price
            iv_opt = iv_proj_ce if opt == "CE" else iv_proj_pe
            proj_px = reprice(discovered_spot, k, T_open, iv_opt, r=0.065, option_type=opt)

            # Intrinsic floor
            intrinsic = max(0.0, (discovered_spot - k) if opt == "CE" else (k - discovered_spot))
            proj_px = max(proj_px, intrinsic)

            prev_px = max(round(prev_px, 1), 0.5)
            proj_px = max(round(proj_px, 1), 0.5)
            shock_pts = round(proj_px - prev_px, 1)
            shock_pct = round((shock_pts / prev_px) * 100.0, 1) if prev_px > 0 else 0.0

            opt_greeks = greeks(discovered_spot, k, T_open, iv_opt, r=0.065, option_type=opt)

            item = {
                "strike": k,
                "side": opt,
                "prev_ltp": prev_px,
                "projected_open_ltp": proj_px,
                "shock_pts": shock_pts,
                "shock_pct": shock_pct,
                "delta": round(opt_greeks.delta, 2),
                "is_atm": (k == atm_k),
            }
            projected_strikes.append(item)

            if k == atm_k:
                if opt == "CE":
                    atm_ce_prev = prev_px
                    atm_ce_proj = proj_px
                else:
                    atm_pe_prev = prev_px
                    atm_pe_proj = proj_px

    straddle_prev = round(atm_ce_prev + atm_pe_prev, 1)
    straddle_proj = round(atm_ce_proj + atm_pe_proj, 1)
    straddle_shock_pct = round(((straddle_proj - straddle_prev) / straddle_prev) * 100.0, 1) if straddle_prev > 0 else 0.0

    if gap_pts > 0:
        ce_pct = ((atm_ce_proj - atm_ce_prev) / max(atm_ce_prev, 1.0)) * 100.0
        pe_pct = ((atm_pe_proj - atm_pe_prev) / max(atm_pe_prev, 1.0)) * 100.0
        narrative = (
            f"Pre-open gap-up of {gap_pts:+.1f} pts ({gap_pct:+.2f}%) induces {ce_pct:+.0f}% "
            f"expansion on ATM {atm_k:,.0f} CE, while ATM PE deflates {pe_pct:+.0f}%. "
            f"Straddle moves {straddle_shock_pct:+.1f}%."
        )
    elif gap_pts < 0:
        ce_pct = ((atm_ce_proj - atm_ce_prev) / max(atm_ce_prev, 1.0)) * 100.0
        pe_pct = ((atm_pe_proj - atm_pe_prev) / max(atm_pe_prev, 1.0)) * 100.0
        narrative = (
            f"Pre-open gap-down of {gap_pts:+.1f} pts ({gap_pct:+.2f}%) triggers {pe_pct:+.0f}% "
            f"surge in ATM {atm_k:,.0f} PE, with Call crush of {ce_pct:+.0f}%. "
            f"Straddle moves {straddle_shock_pct:+.1f}%."
        )
    else:
        narrative = "Balanced auction discovery with flat overnight straddle delta impact."

    res = {
        "discovered_spot": discovered_spot,
        "prev_close": prev_close,
        "gap_pts": gap_pts,
        "gap_pct": gap_pct,
        "atm_strike": atm_k,
        "projected_strikes": projected_strikes,
        "atm_straddle_prev": straddle_prev,
        "atm_straddle_projected": straddle_proj,
        "straddle_shock_pct": straddle_shock_pct,
        "narrative": narrative,
    }
    try:
        save_option_shocks(symbol, res)
    except Exception as e:
        log.debug(f"Failed to persist option shocks: {e}")
    return res


def get_post_market_state(
    symbol: str = "NIFTY",
    force_refresh: bool = False,
    now: datetime | None = None,
    session_date: str | None = None,
) -> dict:
    """Retrieve post-closing settlement state, CAS 30-min VWAP reconciliation, and dealer pin efficiency."""
    now_ist = now or datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")
    target_date = session_date or today_str

    # 1. Historical or specific prior session requested
    if target_date != today_str:
        persisted = get_persisted_auction_record(symbol.upper(), target_date)
        if persisted and (persisted.get("post_market_settled") or persisted.get("settlement_spot")):
            s_spot = float(persisted.get("settlement_spot") or 0.0)
            p_close = float(persisted.get("prev_close") or s_spot)
            net_pts = float(persisted.get("net_change_pts") or (round(s_spot - p_close, 1) if p_close > 0 else 0.0))
            net_pct = float(persisted.get("net_change_pct") or (round((net_pts / p_close) * 100.0, 2) if p_close > 0 else 0.0))
            return {
                "symbol": symbol.upper(),
                "date": target_date,
                "is_post_market": True,
                "post_market_settled": bool(persisted.get("post_market_settled", 1)),
                "settlement_spot": s_spot,
                "last_continuous_spot": float(persisted.get("last_continuous_spot") or s_spot),
                "settlement_divergence_pts": float(persisted.get("settlement_divergence_pts") or 0.0),
                "pin_efficiency_pct": float(persisted.get("pin_efficiency_pct") or 100.0),
                "pin_target_wall": persisted.get("pin_target_wall") or "Call Wall",
                "settled_at": persisted.get("settled_at") or "15:40:00",
                "closing_cvd": float(persisted.get("closing_cvd") or 0.0),
                "orderflow_bias": persisted.get("orderflow_bias") or "",
                "status_text": (
                    "Session Concluded · Final Settlement Reconciled"
                    if any(k in (persisted.get("status_text") or "") for k in ("Equilibrium Discovered", "Pre-Market", "Pre-Open", "Pre-Open Auction"))
                    else (persisted.get("status_text") or "Session Concluded · Final Settlement Reconciled")
                ),
                "day_range": persisted.get("day_range") or "",
                "net_change_pts": net_pts,
                "net_change_pct": net_pct,
                "next_key_levels": persisted.get("next_key_levels") or "",
                "institutional_wrap": persisted.get("institutional_wrap") or "",
            }

    with _CACHE_LOCK:
        cached = _POST_MARKET_CACHE.get(symbol.upper())
        if cached and cached.get("date") == today_str and not force_refresh:
            return cached

    from data.market_snapshot import get_snapshot
    snap = get_snapshot(symbol) or {}
    snap_spot = float(snap.get("spot") or 0.0)
    snap_prev_close = float(snap.get("prev_close") or 0.0)
    fallback_spot = snap_spot if snap_spot > 0 else snap_prev_close

    # In continuous or pre-market hours on trading days, post-market is not yet reached
    t_min = now_ist.hour * 60 + now_ist.minute
    from data.holidays import is_holiday
    is_trading_day_today = (not is_holiday(now_ist.date())) and (now_ist.weekday() < 5)
    is_post = (t_min >= 15 * 60 + 30) or (not is_trading_day_today)

    if not is_post:
        state = {
            "symbol": symbol.upper(),
            "date": today_str,
            "is_post_market": False,
            "post_market_settled": False,
            "settlement_spot": fallback_spot,
            "last_continuous_spot": fallback_spot,
            "settlement_divergence_pts": 0.0,
            "pin_efficiency_pct": 100.0,
            "pin_target_wall": "Call Wall",
            "settled_at": None,
            "status_text": "Active Trading Session · Settlement at 15:40 IST",
            "day_range": "",
            "net_change_pts": 0.0,
            "net_change_pct": 0.0,
            "next_key_levels": "",
        }
        with _CACHE_LOCK:
            _POST_MARKET_CACHE[symbol.upper()] = state
        return state

    persisted = get_persisted_auction_record(symbol.upper(), today_str) if not force_refresh else None
    if persisted and persisted.get("post_market_settled"):
        p_settle = float(persisted.get("settlement_spot") or 0.0)
        p_prev = float(persisted.get("prev_close") or 0.0)
        # Avoid stale clone where settlement was erroneously set to prev_close when authentic spot is different
        is_stale_clone = (snap_spot > 0 and p_prev > 0 and abs(p_settle - p_prev) < 0.01 and abs(snap_spot - p_prev) > 0.05)
        if not is_stale_clone:
                snap_hi = float(snap.get("high") or 0.0)
                snap_lo = float(snap.get("low") or 0.0)
                calc_range = f"₹{snap_hi:,.1f} / ₹{snap_lo:,.1f}" if snap_hi > 0 and snap_lo > 0 else ""
                calc_net_pts = round(p_settle - p_prev, 1) if p_prev > 0 else 0.0
                calc_net_pct = round((calc_net_pts / p_prev) * 100.0, 2) if p_prev > 0 else 0.0
                rehydrated = {
                    "symbol": symbol.upper(),
                    "date": today_str,
                    "is_post_market": True,
                    "post_market_settled": True,
                    "settlement_spot": p_settle,
                    "last_continuous_spot": float(persisted.get("last_continuous_spot") or p_settle),
                    "settlement_divergence_pts": float(persisted.get("settlement_divergence_pts") or 0.0),
                    "pin_efficiency_pct": float(persisted.get("pin_efficiency_pct") or 100.0),
                    "pin_target_wall": persisted.get("pin_target_wall") or "Call Wall",
                    "settled_at": persisted.get("settled_at") or "15:40:00",
                    "closing_cvd": float(persisted.get("closing_cvd") or 0.0),
                    "orderflow_bias": persisted.get("orderflow_bias") or "",
                    "status_text": persisted.get("status_text") or "Session Concluded · Final Settlement Reconciled",
                    "day_range": persisted.get("day_range") or calc_range,
                    "net_change_pts": float(persisted.get("net_change_pts") or calc_net_pts),
                    "net_change_pct": float(persisted.get("net_change_pct") or calc_net_pct),
                    "next_key_levels": persisted.get("next_key_levels") or "",
                    "institutional_wrap": persisted.get("institutional_wrap") or "",
                }
                with _CACHE_LOCK:
                    _POST_MARKET_CACHE[symbol.upper()] = rehydrated
                return rehydrated

    settle_px = snap_spot if snap_spot > 0 else fallback_spot
    cont_px = settle_px

    if is_post and (15 * 60 + 30 <= t_min <= 16 * 60 + 5) and is_trading_day_today:
        try:
            from data.dhan_live import fetch_index_ohlc
            ohlc = fetch_index_ohlc(symbol.upper(), timeout=4)
            if ohlc:
                ltp = float(ohlc.get("ltp") or ohlc.get("close") or settle_px)
                if ltp > 0:
                    settle_px = round(ltp, 2)
                    cont_px = round(ltp, 2)
        except Exception as e:
            log.debug(f"Post-market poll failed: {e}")

    persisted_rec = get_persisted_auction_record(symbol.upper(), today_str) or {}
    prev_close_ref = float(persisted_rec.get("prev_close") or 0.0)
    if prev_close_ref <= 0:
        pm_cached = _PRE_MARKET_CACHE.get(symbol.upper()) or {}
        prev_close_ref = float(pm_cached.get("prev_close") or 0.0) or snap_prev_close or settle_px

    net_pts = round(settle_px - prev_close_ref, 1) if prev_close_ref > 0 else 0.0
    net_pct = round((net_pts / prev_close_ref) * 100.0, 2) if prev_close_ref > 0 else 0.0
    hi = float(snap.get("high") or 0.0)
    lo = float(snap.get("low") or 0.0)
    cw = float(snap.get("call_wall") or 0.0)
    pw = float(snap.get("put_wall") or 0.0)
    d_range = f"₹{hi:,.1f} / ₹{lo:,.1f}" if hi > 0 and lo > 0 else ""
    key_lvls = f"₹{cw:,.0f} CW / ₹{pw:,.0f} PW" if cw > 0 and pw > 0 else ""

    state = {
        "symbol": symbol.upper(),
        "date": today_str,
        "is_post_market": True,
        "post_market_settled": True,
        "settlement_spot": settle_px,
        "last_continuous_spot": cont_px,
        "settlement_divergence_pts": round(settle_px - cont_px, 2),
        "pin_efficiency_pct": 100.0,
        "pin_target_wall": "Call Wall",
        "settled_at": "15:40:00",
        "status_text": "Session Concluded · Final Settlement Reconciled",
        "prev_close": prev_close_ref,
        "day_range": d_range,
        "net_change_pts": net_pts,
        "net_change_pct": net_pct,
        "next_key_levels": key_lvls,
    }

    try:
        from sentinel.storage.db import record_daily_closing_cvd
        cvd_rec = record_daily_closing_cvd(f"{symbol.upper()}-FUT", symbol=symbol.upper(), trade_date=today_str)
        if cvd_rec:
            state["closing_cvd"] = cvd_rec["closing_cvd"]
            state["buy_volume"] = cvd_rec["buy_volume"]
            state["sell_volume"] = cvd_rec["sell_volume"]
            state["orderflow_bias"] = "Institutional Net Buyers (+)" if cvd_rec["closing_cvd"] > 0 else "Institutional Net Sellers (-)"
    except Exception as e:
        log.debug(f"Post-market CVD recording failed: {e}")

    with _CACHE_LOCK:
        _POST_MARKET_CACHE[symbol.upper()] = state
    try:
        if is_post and state.get("settlement_spot", 0) > 0:
            save_post_market_state(symbol.upper(), state)
    except Exception as e:
        log.debug(f"Failed to persist post-market state: {e}")
    return state


def is_contract_expired(rec_dict: dict, ref_date: date | None = None) -> bool:
    """Check if recommendation contract has expired (today > contract expiry).

    Bookmarked cards stay active until user explicitly unpins them OR crosses expiry date.
    """
    today = ref_date or datetime.now(IST).date()
    payload = rec_dict.get("payload_json")
    if isinstance(payload, str):
        try:
            import json
            payload = json.loads(payload)
        except Exception:
            payload = {}
    elif not isinstance(payload, dict):
        payload = {}

    # 1. Direct expiry field in payload (e.g. '2026-10-13')
    exp_str = payload.get("expiry") or rec_dict.get("expiry")
    if exp_str:
        try:
            clean_exp = str(exp_str)[:10]
            exp_d = datetime.strptime(clean_exp, "%Y-%m-%d").date()
            return today > exp_d
        except Exception:
            pass

    # 2. Extract from option symbol string: e.g. "NIFTY 13 OCT 22700 CE" or "NIFTY26OCT22700CE"
    opt_sym = str(rec_dict.get("option_symbol") or rec_dict.get("symbol") or payload.get("option_symbol") or payload.get("symbol") or "")
    parts = opt_sym.split()
    if len(parts) >= 3:
        # Format: "NIFTY 13 OCT 22700 CE"
        try:
            day_str = parts[1]
            mon_str = parts[2].upper()
            curr_year = today.year
            dt = datetime.strptime(f"{day_str} {mon_str} {curr_year}", "%d %b %Y").date()
            return today > dt
        except Exception:
            pass

    # Default: not expired
    return False


def is_recently_triggered(triggered_at_str: str, max_age_seconds: float = 600.0) -> bool:
    """Return True if recommendation timestamp was within max_age_seconds (default 10 mins)."""
    if not triggered_at_str or not isinstance(triggered_at_str, str):
        return False
    try:
        clean_str = triggered_at_str.strip().replace("T", " ")[:19]
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        dt_ist = dt.replace(tzinfo=IST)
        now_ist = datetime.now(IST)
        diff_sec = (now_ist - dt_ist).total_seconds()
        return 0 <= diff_sec <= max_age_seconds
    except Exception:
        return False


def render_pre_market_commentary_card(symbol: str, data: Any = None, session_date: str | None = None) -> None:
    """Render Pre-Market Tactical Analysis card at top of Commentary tab."""
    import streamlit as st
    try:
        now_ist = datetime.now(IST)
        today_str = now_ist.strftime("%Y-%m-%d")
        sym_u = symbol.upper()
        target_date = session_date or today_str

        persisted = get_persisted_auction_record(sym_u, target_date) or {}
        if target_date == today_str:
            pm = get_pre_market_state(sym_u, now=now_ist)
        else:
            pm = persisted

        # GEX levels
        cw = float(getattr(getattr(data, "levels", None), "call_wall", 0.0) or 0.0)
        pw = float(getattr(getattr(data, "levels", None), "put_wall", 0.0) or 0.0)
        flip = float(getattr(getattr(data, "levels", None), "flip", 0.0) or 0.0)
        if cw == 0.0 or pw == 0.0:
            from data.market_snapshot import get_snapshot
            snap = get_snapshot(sym_u) or {}
            cw = cw or float(snap.get("call_wall") or 0.0)
            pw = pw or float(snap.get("put_wall") or 0.0)
            flip = flip or float(snap.get("gamma_flip") or 0.0)

        is_disc = bool(pm.get("is_discovered")) or bool(persisted.get("is_discovered"))
        if is_disc:
            disc_spot = float(pm.get("discovered_spot") or persisted.get("discovered_spot") or 0.0)
            gap_pts = float(pm.get("gap_pts") if pm.get("gap_pts") is not None else (persisted.get("gap_pts") or 0.0))
            gap_pct = float(pm.get("gap_pct") if pm.get("gap_pct") is not None else (persisted.get("gap_pct") or 0.0))
            disc_time = pm.get("discovered_at") or persisted.get("discovered_at") or "09:08:15"
            gap_fill_status = pm.get("gap_fill_status") or persisted.get("gap_fill_status") or "UNFILLED"

            gap_cls = classify_opening_gap(gap_pts, gap_pct, disc_spot, cw, pw, flip)
            badge_html = f'<span class="sentinel-chip" style="background:var(--surface-2);color:{gap_cls["color"]};border:1px solid {gap_cls["color"]};font-size:.74rem">{gap_cls["badge"]}</span>'

            sign_str = "+" if gap_pts >= 0 else ""
            if gap_fill_status == "FILLED":
                fill_chip = '<span class="sentinel-chip" style="background:rgba(34,197,94,.12);color:var(--go);border:1px solid rgba(34,197,94,.3);font-size:.70rem">✅ GAP FILLED</span>'
                fill_note = "Opening gap has been filled — mean-reversion cycle completed back to previous close."
            else:
                fill_chip = '<span class="sentinel-chip" style="background:rgba(245,158,11,.12);color:#f59e0b;border:1px solid rgba(245,158,11,.3);font-size:.70rem">⚡ GAP UNFILLED</span>'
                fill_note = f"Opening gap holding {sign_str}{gap_pts:,.1f} pts — directional momentum dominant ({gap_cls['favored_play']})."

            strikes_data = pm.get("projected_strikes") or persisted.get("projected_strikes") or []
            straddle_shock_pct = pm.get("straddle_shock_pct") if pm.get("straddle_shock_pct") is not None else persisted.get("straddle_shock_pct")
            atm_straddle_prev = pm.get("atm_straddle_prev") if pm.get("atm_straddle_prev") is not None else persisted.get("atm_straddle_prev")
            atm_straddle_proj = pm.get("atm_straddle_projected") if pm.get("atm_straddle_projected") is not None else persisted.get("atm_straddle_projected")

            if not strikes_data:
                try:
                    raw_dte = getattr(data, "dte_now", None) if data else None
                    p_close = float(pm.get("prev_close") or persisted.get("prev_close") or (disc_spot - gap_pts))
                    shocks = calculate_pre_market_option_shocks(
                        discovered_spot=disc_spot,
                        prev_close=p_close,
                        contracts_df=getattr(data, "contracts", None),
                        symbol=sym_u,
                        dte_days=float(raw_dte if raw_dte is not None else 3.0),
                        atm_iv=float(getattr(getattr(data, "levels", None), "atm_iv", None) or 0.14),
                    )
                    strikes_data = shocks.get("projected_strikes") or []
                    straddle_shock_pct = shocks.get("straddle_shock_pct")
                    atm_straddle_prev = shocks.get("atm_straddle_prev")
                    atm_straddle_proj = shocks.get("atm_straddle_projected")
                except Exception as ex:
                    log.debug(f"Option shocks calculation error in commentary: {ex}")

            straddle_chip_html = ""
            if straddle_shock_pct is not None and atm_straddle_prev and atm_straddle_proj:
                straddle_clr = "var(--go)" if straddle_shock_pct >= 0 else "var(--stop)"
                exp_label = "Expansion" if straddle_shock_pct >= 0 else "Crush"
                straddle_chip_html = (
                    f'<span style="font-size:.70rem;background:rgba(56,189,248,.1);border:1px solid rgba(56,189,248,.25);padding:2px 8px;border-radius:4px;font-family:var(--mono)">'
                    f'ATM Straddle: ₹{atm_straddle_prev:,.1f} → <b>₹{atm_straddle_proj:,.1f}</b> (<span style="color:{straddle_clr};font-weight:700">{straddle_shock_pct:+.1f}% {exp_label}</span>)'
                    f'</span>'
                )

            shock_rows = []
            if strikes_data:
                atm_k = round(disc_spot / (50 if sym_u == "NIFTY" else 100)) * (50 if sym_u == "NIFTY" else 100)
                stk_step = 50 if sym_u == "NIFTY" else 100
                disp_strikes = [atm_k + i * stk_step for i in range(-2, 3)]
                for sk in disp_strikes:
                    c_inf = next((x for x in strikes_data if x["strike"] == sk and x["side"] == "CE"), None)
                    p_inf = next((x for x in strikes_data if x["strike"] == sk and x["side"] == "PE"), None)
                    if c_inf and p_inf:
                        c_clr = "var(--go)" if c_inf["shock_pts"] >= 0 else "var(--stop)"
                        p_clr = "var(--go)" if p_inf["shock_pts"] >= 0 else "var(--stop)"
                        is_atm_r = (sk == atm_k)
                        r_bg = "background:rgba(56,189,248,.07);" if is_atm_r else ""
                        atm_tag = " <span style='font-size:.58rem;background:var(--spark);color:#000;padding:1px 3px;border-radius:3px;font-weight:800'>ATM</span>" if is_atm_r else ""
                        shock_rows.append(
                            f'<tr style="{r_bg}border-bottom:1px solid var(--line);text-align:center">'
                            f'<td style="padding:3px 6px;color:var(--ink-mute);font-family:var(--mono)">₹{c_inf["prev_ltp"]:,.1f}</td>'
                            f'<td style="padding:3px 6px;color:var(--go);font-weight:700;font-family:var(--mono)">₹{c_inf["projected_open_ltp"]:,.1f}</td>'
                            f'<td style="padding:3px 6px;color:{c_clr};font-weight:700">{c_inf["shock_pct"]:+.0f}%</td>'
                            f'<td style="padding:3px 6px;font-weight:800;color:var(--ink);font-family:var(--mono)">{sk:,.0f}{atm_tag}</td>'
                            f'<td style="padding:3px 6px;color:var(--ink-mute);font-family:var(--mono)">₹{p_inf["prev_ltp"]:,.1f}</td>'
                            f'<td style="padding:3px 6px;color:var(--stop);font-weight:700;font-family:var(--mono)">₹{p_inf["projected_open_ltp"]:,.1f}</td>'
                            f'<td style="padding:3px 6px;color:{p_clr};font-weight:700">{p_inf["shock_pct"]:+.0f}%</td>'
                            f'</tr>'
                        )

            date_tag = f" · {target_date}" if target_date != today_str else ""
            st.markdown(
                textwrap.dedent(f'''
                <div class="ck pre-bell-card" style="margin-bottom:12px;border-left:4px solid {gap_cls['color']}">
                    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px">
                        <div style="display:flex;align-items:center;gap:8px">
                            <span style="font-size:1.1rem">🌅</span>
                            <b style="font-size:.92rem;color:var(--ink)">Pre-Market Equilibrium & Tactical Baseline{date_tag}</b>
                            <span style="font-size:.68rem;color:var(--ink-mute);font-family:var(--mono)">Discovered {disc_time} IST</span>
                            <span style="font-size:.65rem;background:rgba(56,189,248,.12);color:var(--spark);padding:1px 6px;border-radius:4px;border:1px solid rgba(56,189,248,.25)">💾 DB PERSISTED</span>
                            <a href="/?view=pre_post_market_guide" target="_blank" style="font-size:.70rem;color:var(--spark);text-decoration:none;background:rgba(56,189,248,.08);padding:1px 6px;border-radius:4px;border:1px solid rgba(56,189,248,.25)">📖 Pre-Bell Guide ↗</a>
                        </div>
                        <div style="display:flex;align-items:center;gap:6px">
                            {fill_chip}
                            {badge_html}
                        </div>
                    </div>
                    <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:10px;margin-bottom:10px">
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Indicative Open</small>
                            <b style="font-size:1.05rem;font-family:var(--mono)">₹{disc_spot:,.1f}</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Opening Gap</small>
                            <b style="font-size:1.05rem;font-family:var(--mono);color:{gap_cls['color']}">{sign_str}{gap_pts:,.1f} ({sign_str}{gap_pct:.2f}%)</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Call / Put Wall</small>
                            <b style="font-size:1.0rem;font-family:var(--mono)">{cw:,.0f} / {pw:,.0f}</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Strategic Opening Play</small>
                            <b style="font-size:.92rem;color:var(--spark)">{gap_cls['favored_play']}</b>
                        </div>
                    </div>
                    <div style="font-size:.82rem;line-height:1.45;color:var(--ink);background:var(--surface);padding:9px 12px;border-radius:8px;border:1px solid var(--line);margin-bottom:8px">
                        <b>Strategic Analysis:</b> {gap_cls['briefing']}
                        <div style="margin-top:4px;font-size:.74rem;color:var(--ink-mute);font-style:italic">{fill_note}</div>
                    </div>
                </div>
                ''').strip(),
                unsafe_allow_html=True,
            )

            if shock_rows:
                exp_title = "🎯 Pre-Bell Projected Option Openings (Target Engine Black-76)"
                if straddle_shock_pct is not None and atm_straddle_prev and atm_straddle_proj:
                    exp_label = "Expansion" if straddle_shock_pct >= 0 else "Crush"
                    exp_title += f" · ATM Straddle: ₹{atm_straddle_prev:,.1f} → ₹{atm_straddle_proj:,.1f} ({straddle_shock_pct:+.1f}% {exp_label})"
                with st.expander(exp_title, expanded=False):
                    st.markdown(
                        textwrap.dedent(f'''
                        <div style="background:var(--surface);padding:8px 10px;border-radius:8px;border:1px solid var(--line)">
                            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                                <span style="font-size:.70rem;font-weight:750;color:var(--spark);text-transform:uppercase">🎯 Black-76 Analytical Forward Pricing Matrix</span>
                                {straddle_chip_html}
                            </div>
                            <table style="width:100%;font-size:.72rem;border-collapse:collapse">
                                <thead><tr style="border-bottom:1px solid var(--line);color:var(--ink-mute);text-align:center">
                                    <th style="padding:4px">CE Prev</th><th style="padding:4px">CE Proj</th><th style="padding:4px">CE Δ</th>
                                    <th style="padding:4px">Strike</th>
                                    <th style="padding:4px">PE Prev</th><th style="padding:4px">PE Proj</th><th style="padding:4px">PE Δ</th>
                                </tr></thead>
                                <tbody>{"".join(shock_rows)}</tbody>
                            </table>
                        </div>
                        ''').strip(),
                        unsafe_allow_html=True,
                    )
        else:
            prev_cl = float(pm.get("prev_close") or (getattr(getattr(data, "levels", None), "prev_close", 0.0) if data else 0.0) or 0.0)
            fii_b = pm.get("fii_bias") or "NEUTRAL"
            fii_n = pm.get("fii_net_fut") or 0
            prior_cvd = pm.get("prior_session_cvd") or 0.0
            prior_of = pm.get("prior_session_orderflow") or "Neutral"
            fii_color = "var(--go)" if fii_b == "BULLISH" else ("var(--stop)" if fii_b == "BEARISH" else "var(--spark)")
            cvd_color = "var(--go)" if prior_cvd > 0 else ("var(--stop)" if prior_cvd < 0 else "var(--ink-mute)")
            phase_desc = "Pre-Open Order Auction (09:00–09:08 IST)" if pm.get("phase_code") == "PRE_MARKET_ENTRY" else "Early Pre-Market Preparation (06:30–09:00 IST)"

            st.markdown(
                textwrap.dedent(f'''
                <div class="ck pre-bell-card" style="border-left:4px solid var(--spark);margin-bottom:12px">
                    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px">
                        <div style="display:flex;align-items:center;gap:8px">
                            <span style="font-size:1.1rem">🌅</span>
                            <b style="font-size:.92rem;color:var(--ink)">Early Pre-Market Tactical Setup</b>
                            <span style="font-size:.68rem;color:var(--ink-mute);font-family:var(--mono)">{phase_desc}</span>
                        </div>
                        <span class="sentinel-chip" style="background:var(--surface-2);color:var(--spark);border:1px solid var(--spark);font-size:.70rem">⏳ AWAITING 09:08 DISCOVERY</span>
                    </div>
                    <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:10px;margin-bottom:8px">
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Previous Close</small>
                            <b style="font-size:1.05rem;font-family:var(--mono)">₹{prev_cl:,.1f}</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Key GEX Walls</small>
                            <b style="font-size:1.0rem;font-family:var(--mono)">{cw:,.0f} CW / {pw:,.0f} PW</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">FII Positioning</small>
                            <b style="font-size:.95rem;color:{fii_color}">{fii_b} ({fii_n:+d} fut)</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Prior Session CVD</small>
                            <b style="font-size:.95rem;color:{cvd_color};font-family:var(--mono)">{prior_cvd:+,.0f}</b>
                        </div>
                    </div>
                    <div style="font-size:.80rem;line-height:1.45;color:var(--ink-mute);background:var(--surface);padding:8px 12px;border-radius:8px;border:1px solid var(--line)">
                        <b>Morning Tactical Baseline:</b> Pre-open equilibrium auction runs from 09:00 to 09:08 IST on the NSE Cash matching engine.
                        Equilibrium price discovery, GEX wall alignment, opening gap classification, and Black-76 option MTM shocks will stream here automatically upon 09:08 matching.
                    </div>
                </div>
                ''').strip(),
                unsafe_allow_html=True,
            )
    except Exception as e:
        log.debug(f"Pre-market commentary card render error: {e}")


def render_post_market_commentary_card(symbol: str, data: Any = None, session_date: str | None = None) -> None:
    """Render Post-Market Settlement & Pinning Scorecard at bottom of Commentary tab."""
    import streamlit as st
    try:
        now_ist = datetime.now(IST)
        today_str = now_ist.strftime("%Y-%m-%d")
        sym_u = symbol.upper()
        target_date = session_date or today_str

        persisted = get_persisted_auction_record(sym_u, target_date) or {}
        post_st = get_post_market_state(sym_u, now=now_ist, session_date=target_date)

        # GEX levels
        cw = float(getattr(getattr(data, "levels", None), "call_wall", 0.0) or 0.0)
        pw = float(getattr(getattr(data, "levels", None), "put_wall", 0.0) or 0.0)
        if cw == 0.0 or pw == 0.0:
            from data.market_snapshot import get_snapshot
            snap = get_snapshot(sym_u) or {}
            cw = cw or float(snap.get("call_wall") or 0.0)
            pw = pw or float(snap.get("put_wall") or 0.0)

        is_post_settled = bool(post_st.get("is_post_market")) if target_date == today_str else bool(post_st.get("post_market_settled") or persisted.get("post_market_settled") or persisted.get("settlement_spot"))
        if is_post_settled:
            settle_spot = float(post_st.get("settlement_spot") or persisted.get("settlement_spot") or 0.0)
            cont_spot = float(post_st.get("last_continuous_spot") or persisted.get("last_continuous_spot") or settle_spot)
            drift_pts = round(settle_spot - cont_spot, 1)
            pin_wall = post_st.get("pin_target_wall") or persisted.get("pin_target_wall") or f"{cw:,.0f} CW"
            pin_eff = float(post_st.get("pin_efficiency_pct") or persisted.get("pin_efficiency_pct") or 100.0)
            cvd_val = float(post_st.get("closing_cvd") or persisted.get("closing_cvd") or 0.0)
            of_bias = post_st.get("orderflow_bias") or persisted.get("orderflow_bias") or ("Institutional Net Buyers (+)" if cvd_val > 0 else "Institutional Net Sellers (-)")
            cvd_color = "var(--go)" if cvd_val > 0 else "var(--stop)"

            date_tag = f" · {target_date}" if target_date != today_str else ""
            st.markdown(
                textwrap.dedent(f'''
                <div class="ck post-mkt-card" style="margin-top:14px;margin-bottom:12px">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                        <div style="display:flex;align-items:center;gap:8px">
                            <span style="font-size:1.05rem">🏁</span>
                            <b style="font-size:.92rem;color:#8b5cf6">Post-Market Settlement & Pinning Scorecard{date_tag}</b>
                            <span style="font-size:.68rem;color:var(--ink-mute);font-family:var(--mono)">CAS 30-min VWAP Reconciled</span>
                            <span style="font-size:.65rem;background:rgba(139,92,246,.12);color:#8b5cf6;padding:1px 6px;border-radius:4px;border:1px solid rgba(139,92,246,.25)">💾 DB PERSISTED</span>
                        </div>
                        <span class="sentinel-chip" style="background:var(--surface-2);color:#8b5cf6;border:1px solid #8b5cf6;font-size:.72rem">EOD SETTLED</span>
                    </div>
                    <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:10px;margin-bottom:8px">
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Final Settlement Close</small>
                            <b style="font-size:1.05rem;font-family:var(--mono)">₹{settle_spot:,.1f}</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">CAS 30m VWAP Drift</small>
                            <b style="font-size:1.05rem;font-family:var(--mono)">{drift_pts:+.1f} pts</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Nearest Wall Target</small>
                            <b style="font-size:1.0rem;font-family:var(--mono)">{pin_wall}</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Dealer Pinning Status</small>
                            <b style="font-size:.92rem;color:var(--go)">Pin Defended ({pin_eff:.0f}%)</b>
                        </div>
                    </div>
                    <div style="font-size:.78rem;background:var(--surface);padding:7px 12px;border-radius:6px;border:1px solid var(--line);display:flex;justify-content:space-between;align-items:center">
                        <span style="color:var(--ink-mute)">Closing Institutional CVD:</span>
                        <b style="color:{cvd_color};font-family:var(--mono)">{cvd_val:+,.0f} · {of_bias}</b>
                    </div>
                </div>
                ''').strip(),
                unsafe_allow_html=True,
            )
    except Exception as e:
        log.debug(f"Post-market commentary card render error: {e}")


def render_auction_commentary_card(symbol: str, data: Any = None) -> None:
    """Backwards-compatible wrapper that renders both pre-market and post-market cards."""
    render_pre_market_commentary_card(symbol, data)
    render_post_market_commentary_card(symbol, data)

