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
   - Distance of the official settle to the nearer gamma strike. That distance is a location, not a hedge score.
   - Settlement divergence versus the 15:30 continuous close
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
_PM_THREADS: dict[str, threading.Thread] = {}


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


_PM_POLL_START_MIN = 8 * 60 + 50
_PM_POLL_END_MIN = 9 * 60 + 22
_MORNING_BRIEFING_MIN = 6 * 60 + 30


def pre_market_poller_asleep(now: datetime | None = None) -> bool:
    """True outside 08:50–09:22 IST on a weekday. The morning briefing does not wake this poller."""
    now_ist = now or datetime.now(IST)
    if now_ist.weekday() >= 5:
        return True
    t_min = now_ist.hour * 60 + now_ist.minute
    return t_min < _PM_POLL_START_MIN or t_min >= _PM_POLL_END_MIN


def prior_close_book_line(
    prev_close: float,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
    flip: float = 0.0,
) -> str:
    """Where the previous NSE close sits on the frozen book. A location, not a forecast."""
    if prev_close <= 0:
        return "Previous NSE close is unavailable on the snapshot."
    parts = [f"Previous NSE close ₹{prev_close:,.0f}"]
    if flip > 0:
        side = "above" if prev_close >= flip else "below"
        parts.append(f"sits {abs(prev_close - flip):,.0f} pts {side} the flip ₹{flip:,.0f}")
    if call_wall > 0:
        parts.append(f"{prev_close - call_wall:+,.0f} pts versus the call-gamma strike ₹{call_wall:,.0f}")
    if put_wall > 0:
        parts.append(f"{prev_close - put_wall:+,.0f} pts versus the put-gamma strike ₹{put_wall:,.0f}")
    if len(parts) == 1:
        return parts[0] + "."
    return parts[0] + " " + ", ".join(parts[1:]) + "."


def stamp_morning_briefing(state: dict) -> dict:
    """Prior-session book line. Does not set a discovered open and does not call Dhan."""
    if not state:
        return state
    prev = float(state.get("prev_close") or 0.0)
    cw = float(state.get("call_wall") or 0.0)
    pw = float(state.get("put_wall") or 0.0)
    flip = float(state.get("gamma_flip") or 0.0)
    line = prior_close_book_line(prev, cw, pw, flip)
    fii_bias = state.get("fii_bias") or "NEUTRAL"
    try:
        fii_net = int(state.get("fii_net_fut") or 0)
    except (TypeError, ValueError):
        fii_net = 0
    try:
        cvd = float(state.get("prior_session_cvd") or 0.0)
    except (TypeError, ValueError):
        cvd = 0.0
    of_bias = state.get("prior_session_orderflow") or "Neutral"
    oi = str(state.get("oi_as_of") or "").strip()
    oi_bit = f" OI as of {oi}." if oi else " OI timestamp is missing on the snapshot."
    text = (
        f"{line} FII {fii_bias} ({fii_net:+d} fut). "
        f"Prior-session CVD {cvd:+,.0f} ({of_bias})."
        f"{oi_bit}"
    )
    state["is_morning_briefing"] = True
    state["is_discovered"] = False
    state["discovered_spot"] = None
    state["status_text"] = text
    state["regime_briefing"] = text
    state["regime_badge"] = "PRIOR-SESSION BOOK"
    state["favored_play"] = "MAP ONLY"
    return state


def get_current_session_phase(now: datetime | None = None) -> tuple[str, str, int]:
    """Return (phase_code, phase_label, minutes_until_next) for current IST time.

    Phase codes:
    - 'PRE_MARKET_SETUP' (06:30 - 09:00) local prior-session book, no broker poll
    - 'PRE_MARKET_ENTRY' (09:00 - 09:07:30)
    - 'PRE_MARKET_DISCOVERY' (09:07:30 - 09:15:00)
    - 'REGULAR_SESSION' (09:15:00 - 15:30:00)
    - 'CAS_AND_FNO_EXTENDED' (15:30:00 - 15:40:00)
    - 'POST_MARKET_SETTLEMENT' (15:40:00 - 16:00:00)
    - 'OFF_HOURS' (before 06:30, after 16:00, or weekends/holidays)
    """
    try:
        from data.holidays import is_trading_day
    except Exception:
        def is_trading_day(d):
            return d.weekday() < 5

    now_ist = now or datetime.now(IST)
    if not is_trading_day(now_ist.date()):
        try:
            from data.holidays import get_holiday_name
            h_name = get_holiday_name(now_ist.date())
        except Exception:
            h_name = None
        if h_name:
            label = f"Exchange Closed ({h_name})"
        elif now_ist.weekday() >= 5:
            label = "Exchange Closed (Weekend)"
        else:
            label = "Exchange Closed (Holiday)"
        return ("OFF_HOURS", label, 0)

    t_min = now_ist.hour * 60 + now_ist.minute
    t_sec = now_ist.second

    if t_min < _MORNING_BRIEFING_MIN:
        mins_left = _MORNING_BRIEFING_MIN - t_min
        return ("OFF_HOURS", "Morning briefing at 06:30", mins_left)
    elif t_min < 9 * 60:
        mins_left = 9 * 60 - t_min
        return ("PRE_MARKET_SETUP", "Prior-session book (06:30–09:00)", mins_left)
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
            # If discovery in progress but checked less than 2.8s ago, return cached.
            # A morning-briefing stub must not outlive 09:00, when the cash auction starts.
            if (time.time() - cached.get("_polled_at", 0)) < 2.8:
                phase_now, _, _ = get_current_session_phase(now_ist)
                if not (cached.get("is_morning_briefing") and phase_now != "PRE_MARKET_SETUP"):
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
                "favored_play": persisted.get("favored_play") or "MAP ONLY",
                "regime_badge": persisted.get("regime_badge") or "⚖️ FLAT OPEN",
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
            if prior_cvd > 0:
                prior_of_bias = "Bullish Accumulation (+)"
            elif prior_cvd < 0:
                prior_of_bias = "Bearish Distribution (-)"
            else:
                prior_of_bias = "Neutral"
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
        "favored_play": "MAP ONLY",
        "regime_badge": "⚖️ AWAITING OPEN",
        "regime_color": "var(--spark)",
        "discovered_at": None,
        "oi_as_of": str(snap.get("as_of") or ""),
        "is_morning_briefing": phase_code == "PRE_MARKET_SETUP",
        "status_text": (
            f"Pre-Market Setup · FII: {fii_bias} ({fii_net_fut:+d} fut) · Walls: {cw_val:,.0f} / {pw_val:,.0f}"
            if cw_val > 0 and pw_val > 0
            else f"Pre-Market Setup · FII: {fii_bias} ({fii_net_fut:+d} fut)"
        ),
        "_polled_at": time.time(),
    }
    if phase_code == "PRE_MARKET_SETUP":
        stamp_morning_briefing(state)

    # HTTP/WS readers never hit Dhan. A background poller (force_refresh=True) is the ingest.
    # PRE_MARKET_SETUP is local snapshot only, even if a caller asks for a refresh.
    if phase_code == "PRE_MARKET_SETUP":
        with _CACHE_LOCK:
            _PRE_MARKET_CACHE[symbol.upper()] = state
        return state

    if not force_refresh:
        if phase_code in ("PRE_MARKET_ENTRY", "PRE_MARKET_DISCOVERY"):
            try:
                ensure_pre_market_poller(symbol)
            except Exception:
                pass
        with _CACHE_LOCK:
            existing = _PRE_MARKET_CACHE.get(symbol.upper())
            if existing and existing.get("date") == today_str:
                existing["phase_code"] = phase_code
                existing["phase_label"] = phase_label
                existing["is_pre_market"] = phase_code in ("PRE_MARKET_ENTRY", "PRE_MARKET_DISCOVERY")
                return existing
            _PRE_MARKET_CACHE[symbol.upper()] = state
        return state

    if phase_code == "PRE_MARKET_ENTRY":
        state["status_text"] = (
            f"Pre-Open Auction Collection (09:00–09:08) · FII: {fii_bias} ({fii_net_fut:+d} fut) · Matching at ~09:08:00"
        )

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

    if state.get("is_discovered"):
        state = attach_pre_market_shocks(state)
    with _CACHE_LOCK:
        _PRE_MARKET_CACHE[symbol.upper()] = state
    return state


def ensure_pre_market_poller(symbol: str = "NIFTY") -> None:
    """One daemon per index: Dhan OHLC poll 08:50–09:20 IST. Never called on the HTTP path's clock."""
    sym = symbol.upper().strip()
    with _CACHE_LOCK:
        t = _PM_THREADS.get(sym)
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=_pre_market_loop, args=(sym,), daemon=True, name=f"pm-{sym}")
        _PM_THREADS[sym] = t
        t.start()


def ensure_pre_market_pollers() -> None:
    for s in ("NIFTY", "BANKNIFTY", "SENSEX"):
        ensure_pre_market_poller(s)


def _pre_market_loop(symbol: str) -> None:
    while True:
        now = datetime.now(IST)
        if pre_market_poller_asleep(now):
            time.sleep(20.0)
            continue
        try:
            get_pre_market_state(symbol, force_refresh=True, now=now)
        except Exception as exc:
            log.debug("pre-market poller %s: %s", symbol, exc)
        time.sleep(3.0)


def attach_pre_market_shocks(state: dict, contracts_df: Any = None, dte_days: float = 3.0, atm_iv: float = 0.14) -> dict:
    """Black-76 ATM-band shocks onto a discovered pre-open state. No network."""
    if not state or not state.get("is_discovered"):
        return state
    if state.get("projected_strikes"):
        return state
    disc = float(state.get("discovered_spot") or 0.0)
    prev = float(state.get("prev_close") or 0.0)
    if disc <= 0 or prev <= 0:
        return state
    if contracts_df is None:
        contracts_df = _contracts_from_published(state.get("symbol") or "NIFTY")
    try:
        shocks = calculate_pre_market_option_shocks(
            discovered_spot=disc,
            prev_close=prev,
            contracts_df=contracts_df,
            symbol=str(state.get("symbol") or "NIFTY"),
            dte_days=dte_days,
            atm_iv=atm_iv,
        )
        state["atm_straddle_prev"] = shocks.get("atm_straddle_prev")
        state["atm_straddle_projected"] = shocks.get("atm_straddle_projected")
        state["straddle_shock_pct"] = shocks.get("straddle_shock_pct")
        state["projected_strikes"] = shocks.get("projected_strikes") or []
        save_option_shocks(str(state.get("symbol") or "NIFTY"), shocks, trade_date=state.get("date"))
    except Exception as exc:
        log.debug("pre-market shocks failed: %s", exc)
    return state


def _contracts_from_published(symbol: str):
    try:
        from app.live_fetcher import latest as chain_latest
        pub = chain_latest(symbol, "current_week") or chain_latest(symbol)
        dash = pub.get("data") if pub else None
        if dash is not None:
            return getattr(dash, "contracts", None)
    except Exception:
        pass
    try:
        import pickle
        p = Path("data/journal") / f"latest_snap_{symbol.upper()}.pkl"
        if p.exists():
            with open(p, "rb") as f:
                wrap = pickle.load(f)
            dash = wrap.get("data") if isinstance(wrap, dict) else wrap
            return getattr(dash, "contracts", None)
    except Exception:
        pass
    return None


def acknowledge_pre_market_chime(symbol: str = "NIFTY") -> None:
    """Mark morning chime bell as played for today so it does not repeat."""
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    with _CACHE_LOCK:
        _CHIME_TRIGGERED_DAYS.add((symbol.upper(), today_str))
        cached = _PRE_MARKET_CACHE.get(symbol.upper())
        if cached:
            cached["should_play_chime"] = False


def _book_label(net_gex: float) -> str:
    """Call-heavy only when call-gamma magnitude exceeds put-gamma magnitude."""
    if net_gex > 0:
        return "call-heavy"
    if net_gex < 0:
        return "put-heavy"
    return "book sign unavailable"


def _book_sentence(net_gex: float) -> str:
    if net_gex > 0:
        return "Call gamma is larger than put gamma. "
    if net_gex < 0:
        return "Put gamma is larger than call gamma. "
    return ""


def settle_vs_gamma_strikes(
    settle: float,
    call_wall: float,
    put_wall: float,
    step: float,
) -> dict:
    """How close the official settle printed to the nearer gamma strike.

    The stored column names stay pin_efficiency_pct and pin_target_wall so older
    rows still load. The number is closeness, not a claim that a strike was defended.
    """
    candidates: list[tuple[float, str]] = []
    if call_wall > 0:
        candidates.append((abs(settle - call_wall), f"{call_wall:,.0f} call-gamma"))
    if put_wall > 0:
        candidates.append((abs(settle - put_wall), f"{put_wall:,.0f} put-gamma"))
    if settle <= 0 or not candidates or step <= 0:
        return {
            "pin_efficiency_pct": None,
            "pin_target_wall": "",
            "settle_distance_pts": None,
            "settle_note": "No gamma strike on the book for this close.",
        }
    dist = min(item[0] for item in candidates)
    names = [name for gap, name in candidates if gap == dist]
    name = " and ".join(names)
    closeness = max(0.0, 100.0 - (dist / step) * 100.0)
    return {
        "pin_efficiency_pct": round(closeness, 1),
        "pin_target_wall": name,
        "settle_distance_pts": round(dist, 1),
        "settle_note": (
            f"Official settle is {dist:,.1f} pts from the {name} {'strikes' if len(names) > 1 else 'strike'}. "
            "That distance is a location, not a pin."
        ),
    }


def classify_opening_gap(
    gap_pts: float,
    gap_pct: float,
    spot: float,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
    flip: float = 0.0,
    net_gex: float = 0.0,
) -> dict:
    """Locate the opening print on the book.

    The flip argument is a price. Spot above that price is a location.
    Call-heavy is net_gex > 0 only. A strike is not a floor.
    """
    above_balance = flip > 0 and spot >= flip
    book = _book_label(net_gex)
    book_line = _book_sentence(net_gex)
    flip_txt = f"₹{flip:,.0f}" if flip else "unavailable"

    if call_wall > 0 and spot >= call_wall - 15.0:
        if spot >= call_wall + 10.0:
            regime = "CALL_WALL_BREACH_SQUEEZE"
            badge = "🚀 OPEN ABOVE CALL-GAMMA STRIKE"
            color = "var(--go)"
            briefing = (
                f"Indicative open at ₹{spot:,.0f} is above the call-gamma strike (₹{call_wall:,.0f}). "
                f"That is a location on the book ({book}; balance {flip_txt}). It is not a dealer squeeze."
            )
            favored_play = "WATCH HOLD ABOVE CALL STRIKE"
        else:
            regime = "SHORT_GAMMA_WALL_TEST"
            badge = "🚨 AT CALL-GAMMA STRIKE"
            color = "var(--warn)"
            briefing = (
                f"Indicative open at ₹{spot:,.0f} is at the call-gamma strike (₹{call_wall:,.0f}). "
                f"Book is {book} (balance {flip_txt}). A touch is not a squeeze."
            )
            favored_play = "WATCH THE CALL STRIKE"
    elif put_wall > 0 and spot <= put_wall + 15.0:
        if spot <= put_wall - 10.0:
            regime = "PUT_WALL_BREAKDOWN_CASCADE"
            badge = "🔴 OPEN BELOW PUT-GAMMA STRIKE"
            color = "var(--stop)"
            briefing = (
                f"Indicative open at ₹{spot:,.0f} is below the put-gamma strike (₹{put_wall:,.0f}). "
                f"Book is {book} (balance {flip_txt}). It is not a cascade and not a floor that failed."
            )
            favored_play = "WATCH HOLD BELOW PUT STRIKE"
        else:
            regime = "PUT_WALL_BREAKDOWN_TEST"
            badge = "⚠️ AT PUT-GAMMA STRIKE"
            color = "var(--stop)"
            briefing = (
                f"Indicative open at ₹{spot:,.0f} is at the put-gamma strike (₹{put_wall:,.0f}). "
                f"Book is {book} (balance {flip_txt}). A touch is not a defense."
            )
            favored_play = "WATCH THE PUT STRIKE"
    elif gap_pct >= 0.40:
        if above_balance and call_wall > 0 and spot < call_wall - 35.0:
            regime = "GAP_UP_INTO_POS_GAMMA"
            badge = "⚖️ GAP UP, ABOVE THE BALANCE"
            color = "var(--go)"
            briefing = (
                f"Opening gap of {gap_pts:+.1f} pts ({gap_pct:+.2f}%) with spot above the call−put balance ({flip_txt}). "
                f"Book is {book}. {book_line}That is a location, not a gap fade."
            )
            favored_play = "MAP ONLY — NO FADE"
        else:
            regime = "GAP_UP_RUNAWAY"
            badge = "🚀 GAP UP"
            color = "var(--go)"
            briefing = (
                f"Opening gap of {gap_pts:+.1f} pts ({gap_pct:+.2f}%). "
                f"Book is {book} (balance {flip_txt}). The gap is a print, not a dealer chase."
            )
            favored_play = "WATCH THE GAP"
    elif gap_pct <= -0.40:
        if above_balance and put_wall > 0 and spot > put_wall + 30.0:
            regime = "GAP_DOWN_INTO_POS_GAMMA"
            badge = "⚖️ GAP DOWN, ABOVE THE BALANCE"
            color = "var(--warn)"
            briefing = (
                f"Opening gap of {gap_pts:+.1f} pts ({gap_pct:+.2f}%) with spot still above the call−put balance ({flip_txt}). "
                f"Book is {book}. {book_line}A print above the balance is not a bounce."
            )
            favored_play = "MAP ONLY — NO BOUNCE"
        else:
            regime = "GAP_DOWN_TRAP"
            badge = "🔴 GAP DOWN"
            color = "var(--stop)"
            briefing = (
                f"Opening gap of {gap_pts:+.1f} pts ({gap_pct:+.2f}%). "
                f"Book is {book} (balance {flip_txt}). The gap is a print, not a cascade."
            )
            favored_play = "WATCH THE GAP"
    elif abs(gap_pct) <= 0.15:
        regime = "AUCTION_BALANCED"
        badge = "⚖️ FLAT OPEN"
        color = "var(--spark)"
        briefing = (
            f"Flat open ({gap_pts:+.1f} pts / {gap_pct:+.2f}%). "
            f"Book is {book} (balance {flip_txt}). No book-balance trade."
        )
        favored_play = "NO BOOK TRADE"
    else:
        regime = "MODERATE_DRIFT"
        badge = "📈 MODERATE GAP" if gap_pct > 0 else "📉 MODERATE GAP"
        color = "var(--ink)"
        briefing = (
            f"Moderate opening gap of {gap_pts:+.1f} pts ({gap_pct:+.2f}%). "
            f"Book is {book} (balance {flip_txt})."
        )
        favored_play = "MAP ONLY"

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


def _with_measured_settle(state: dict, symbol: str) -> dict:
    """Replace the stamped 100% / Call Wall placeholder with the distance to a gamma strike."""
    pct = state.get("pin_efficiency_pct")
    wall = (state.get("pin_target_wall") or "").strip()
    try:
        pct_f = None if pct is None else float(pct)
    except (TypeError, ValueError):
        pct_f = None
    levels = state.get("next_key_levels") or ""
    stamped = pct_f == 100.0 and wall in ("", "Call Wall", "Put Wall")
    abbreviated = " CW" in levels or " PW" in levels
    if not stamped and not abbreviated:
        return state
    from data.market_snapshot import get_snapshot
    snap = get_snapshot(symbol) or {}
    call_wall = float(snap.get("call_wall") or 0.0)
    put_wall = float(snap.get("put_wall") or 0.0)
    if stamped:
        near = settle_vs_gamma_strikes(
            float(state.get("settlement_spot") or 0.0),
            call_wall,
            put_wall,
            float(config.strike_step_for(symbol)),
        )
        state["pin_efficiency_pct"] = near["pin_efficiency_pct"]
        state["pin_target_wall"] = near["pin_target_wall"]
        state["settle_distance_pts"] = near["settle_distance_pts"]
        state["settle_note"] = near["settle_note"]
        if not state.get("institutional_wrap"):
            state["institutional_wrap"] = near["settle_note"]
    if call_wall > 0 and put_wall > 0 and (not levels or abbreviated):
        state["next_key_levels"] = f"₹{call_wall:,.0f} call-gamma / ₹{put_wall:,.0f} put-gamma"
    return state


def get_post_market_state(
    symbol: str = "NIFTY",
    force_refresh: bool = False,
    now: datetime | None = None,
    session_date: str | None = None,
) -> dict:
    """Retrieve the official close, the cash-auction drift, and the distance to the nearer gamma strike."""
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
            return _with_measured_settle({
                "symbol": symbol.upper(),
                "date": target_date,
                "is_post_market": True,
                "post_market_settled": bool(persisted.get("post_market_settled", 1)),
                "settlement_spot": s_spot,
                "last_continuous_spot": float(persisted.get("last_continuous_spot") or s_spot),
                "settlement_divergence_pts": float(persisted.get("settlement_divergence_pts") or 0.0),
                "pin_efficiency_pct": persisted.get("pin_efficiency_pct"),
                "pin_target_wall": persisted.get("pin_target_wall") or "",
                "settle_note": persisted.get("settle_note") or "",
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
            }, symbol)

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
            "pin_efficiency_pct": None,
            "pin_target_wall": "",
            "settle_note": "Settlement prints after the close. No closeness is measured yet.",
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
                    "pin_efficiency_pct": persisted.get("pin_efficiency_pct"),
                    "pin_target_wall": persisted.get("pin_target_wall") or "",
                    "settle_note": persisted.get("settle_note") or "",
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
                rehydrated = _with_measured_settle(rehydrated, symbol)
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
    key_lvls = (
        f"₹{cw:,.0f} call-gamma / ₹{pw:,.0f} put-gamma" if cw > 0 and pw > 0 else ""
    )
    near = settle_vs_gamma_strikes(
        settle_px, cw, pw, float(config.strike_step_for(symbol)),
    )

    state = {
        "symbol": symbol.upper(),
        "date": today_str,
        "is_post_market": True,
        "post_market_settled": True,
        "settlement_spot": settle_px,
        "last_continuous_spot": cont_px,
        "settlement_divergence_pts": round(settle_px - cont_px, 2),
        "pin_efficiency_pct": near["pin_efficiency_pct"],
        "pin_target_wall": near["pin_target_wall"],
        "settle_distance_pts": near["settle_distance_pts"],
        "settle_note": near["settle_note"],
        "institutional_wrap": near["settle_note"],
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

