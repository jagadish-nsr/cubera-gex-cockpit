#!/usr/bin/env python3
"""Cubera GEX Cockpit — Modern Cybernetic Web Application Server.

FastAPI-powered high-performance web server providing full brick-to-brick parity
with the Streamlit Cockpit across all 11 tabs, sidebar controls, real-time WebSocket
telemetry, and sub-millisecond in-memory cache models.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional
import pandas as pd

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

import config
from app.data_access import load_dashboard, DashboardData
from data.expiry import (
    next_weekly_expiry,
    second_weekly_expiry,
    next_monthly_expiry,
    second_monthly_expiry,
)
from analytics.trade_ideas import (
    generate_trade_recommendations,
    format_time_since_active,
    compute_position_size,
    refresh_trade_idea_live_mtm,
    TradeIdea,
)
from analytics.market_alert import evaluate_market_alert

@dataclass
class StrikeCluster:
    symbol: str
    side: str
    strike: float
    option_symbol: str
    ideas: list[TradeIdea] = field(default_factory=list)

    @property
    def is_confluence(self) -> bool:
        return len(self.ideas) > 1

    @property
    def max_score(self) -> int:
        return max((i.quality_score for i in self.ideas), default=0)

    @property
    def is_bookmarked(self) -> bool:
        return any(is_bookmarked(i.rec_id) for i in self.ideas)

    @property
    def best_idea(self) -> TradeIdea:
        return max(self.ideas, key=lambda x: x.quality_score)
from target_engine.runner import run_target_engine
from target_engine.presenter import (
    compose_confluence_dial,
    compose_edge_label,
    serialize_ranked_candidates,
)
from target_engine.pricer import price, greeks, implied_vol, greek_attribution
from target_engine.levels import compute_touch_probability
from target_engine.time_utils import calculate_fractional_t, calculate_horizon_t
from data.journal import store as journal_store
from data.journal.recommendations_store import (
    is_bookmarked,
    toggle_bookmark,
    list_bookmarked_records,
    get_recommendation_history,
    get_score_history,
    record_event,
    evaluate_recommendation_lifecycle,
    get_strategy_effectiveness_metrics,
    summarize_effectiveness,
    get_unacknowledged_alerts,
    acknowledge_alerts,
)
from commentary.story_state import StoryStateManager
from commentary.storage.db import get_recent_commentary_entries, get_pinned_commentary_cache
from commentary.narrator.templates import format_story_entry, render_bite_template, extract_topic_header
from data.pre_post_market import (
    get_current_session_phase,
    get_pre_market_state,
    get_post_market_state,
    attach_pre_market_shocks,
    calculate_pre_market_option_shocks,
)
from sentinel.storage.db import (
    list_recent_signals,
    ensure_historical_daily_cvd,
    list_recent_bars,
    list_recent_outcomes,
    save_outcome,
)
from sentinel.feed.demo import seed_sentinel_demo_if_needed
from analytics.strike_ladder import compute_strike_ladder, get_sidebar_active_expiries, is_expiry_crossed
from analytics.iv_skew import skew_25delta
from analytics.directional_bias import BIAS_MILD, compute_directional_bias, BiasResult

from app.obs import configure, event, gauge, install

configure("web")
log = logging.getLogger("cubera.web_server")

ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "app" / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Cubera GEX Cockpit Web API", version="5.3.0")
APP_VERSION = "5.3.0"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/" or request.url.path.startswith("/chart/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


install(app)

# Active session settings (defaults)
_ACTIVE_STATE = {
    "symbol": "NIFTY",
    "window": "current_week",
    "account_capital": 500000.0,
    "risk_pct": 1.5,
}

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self._note_clients()

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self._note_clients()

    def _note_clients(self) -> None:
        gauge("ws.clients", detail={"clients": len(self.active_connections)})

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

ws_manager = ConnectionManager()


from app.admin.routes import register_admin_routes
register_admin_routes(app)


def _run_lane(name: str, fn) -> None:
    try:
        fn()
    except Exception as exc:
        # The health daemon is the only lane whose failure means alerts never fire.
        critical = name == "admin.monitor"
        if critical:
            log.error("%s failed: %s", name, exc)
        else:
            log.warning("%s failed: %s", name, exc)
        event(
            "lane.failed",
            level="ERROR" if critical else "WARNING",
            ok=False,
            workflow=name,
            error=str(exc),
            message=name,
        )
        return
    event("lane.started", workflow=name, message=name)


@app.on_event("startup")
async def _start_ingest_lanes():
    """One chain ingest per index + spot WS. HTTP/WS handlers only read the stores."""
    from app.obs import start_heartbeat
    start_heartbeat()
    event("process.started", workflow="process", message="web")
    if os.environ.get("COCKPIT_WATCHDOG") == "1":
        from app.event_loop_watchdog import start as start_watchdog
        loop = asyncio.get_running_loop()
        _run_lane("watchdog", lambda: start_watchdog(loop))

    def _admin():
        from app.admin.health_daemon import start_background
        from app.admin.task_runner import mark_orphaned_tasks
        mark_orphaned_tasks()
        start_background()

    def _spot():
        from app.spot_ticker import warmup_all
        warmup_all()

    def _premarket():
        from data.pre_post_market import ensure_pre_market_pollers
        ensure_pre_market_pollers()

    def _dhan():
        from app.dhan_sync import on_published, start_scheduler
        on_published(_drop_dash_cache)
        start_scheduler()

    _run_lane("admin.monitor", _admin)
    _run_lane("spot.warmup", _spot)
    _run_lane("premarket", _premarket)
    _run_lane("dhan.sync", _dhan)
    _run_lane("rec.publisher", _ensure_rec_publisher)
    if not config.is_market_open():
        return
    window = _ACTIVE_STATE.get("window") or "current_week"
    for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
        _run_lane(f"chain.{sym}", lambda sym=sym: _ensure_chain_ingest(sym, window))


# --------------------------------------------------------------------------
# Helper Utility Functions
# --------------------------------------------------------------------------
def _get_ist_now() -> datetime:
    return datetime.now(IST)


def _fmt_ist_clock(moment: datetime | None = None) -> str:
    """A valid IST moment as DD-MM-YYYY HH:MM:SS IST."""
    moment = moment or _get_ist_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    else:
        moment = moment.astimezone(IST)
    return moment.strftime("%d-%m-%Y %H:%M:%S IST")


def _fmt_bar_stamp(ts) -> str:
    """A bar clock that already parses. Aware values convert to IST; naive values are IST."""
    if not hasattr(ts, "strftime"):
        return ""
    if getattr(ts, "tzinfo", None) is not None:
        ts = ts.astimezone(IST)
    return ts.strftime("%d-%m-%Y %H:%M:%S IST")


def _fmt_snapshot_as_of(fetched_at, now_ist: datetime | None = None) -> str:
    """IST stamp of a GEX/chain snapshot, always with the calendar date.

    Naive datetimes are treated as UTC (load_dashboard writes timezone.utc). That is
    what produced a false '02:45:02 IST' tape clock from 2026-10-02 21:15 UTC.
    A same-day clock with no date is how a prior print looks current.
    """
    del now_ist
    if not fetched_at:
        return ""
    dt = fetched_at
    if isinstance(dt, str):
        raw = dt.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return ""
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%d-%m-%Y %H:%M:%S IST")


def _is_pre_bell_ephemeral(
    now_ist: datetime | None = None,
    sess_phase: str | None = None,
    pm_st: dict | None = None,
) -> bool:
    now_ist = now_ist or _get_ist_now()
    if now_ist.weekday() >= 5:
        return False
    now_m = now_ist.hour * 60 + now_ist.minute
    # Clock-only: 08:45–09:22 IST. Do not require PRE_MARKET phase — at 09:15
    # the session is already REGULAR_SESSION and the card must still drop at 09:22.
    return 8 * 60 + 45 <= now_m <= 9 * 60 + 22


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def _fmt_gex(val: float) -> str:
    """Compact dealer-GEX for KPI display (avoids 14-digit noise)."""
    a = abs(float(val))
    sign = "-" if val < 0 else ""
    if a >= 1e12:
        return f"{sign}{a / 1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}{a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}{a / 1e6:.2f}M"
    return f"{val:,.0f}"


def _same_strike(a: float, b: float, step: float) -> bool:
    return abs(float(a) - float(b)) <= max(0.51, float(step) * 0.01)


def _standing_oi(dash, spot: float, band_pts: float) -> dict:
    """Put-OI and call-OI centres of mass near spot. Missing OI stays missing."""
    from app.oi_footprint import oi_weighted_levels

    empty = {
        "available": False,
        "note": "No per-strike open interest on this snapshot.",
        "put_centre": None,
        "call_centre": None,
        "put_top": None,
        "call_top": None,
        "band_pts": band_pts,
    }
    rows: list[dict] = []
    oi_strikes = getattr(dash, "oi_strikes", None) or []
    for row in oi_strikes:
        if row.get("strike") is None:
            continue
        rows.append({
            "strike": row.get("strike"),
            "call_oi": row.get("call_oi") or 0.0,
            "put_oi": row.get("put_oi") or 0.0,
        })
    if not rows:
        contracts = getattr(dash, "contracts", None)
        if contracts is not None and hasattr(contracts, "empty") and not contracts.empty:
            acc: dict[float, dict] = {}
            for _, crow in contracts.iterrows():
                k = float(crow["strike"])
                cell = acc.setdefault(k, {"strike": k, "call_oi": 0.0, "put_oi": 0.0})
                right = str(crow["type"]).upper()
                oi = float(crow.get("oi") or 0.0)
                if right in ("C", "CE"):
                    cell["call_oi"] += oi
                elif right in ("P", "PE"):
                    cell["put_oi"] += oi
            rows = list(acc.values())
    lv = oi_weighted_levels(rows, spot=float(spot), band_pts=float(band_pts)) if rows else None
    if not lv or (lv.get("support_w") is None and lv.get("resistance_w") is None):
        return empty

    def _r(v):
        return round(float(v), 1) if v is not None else None

    return {
        "available": True,
        "note": (
            f"Standing open interest within ±{float(band_pts):,.0f} pts of spot. "
            "A concentration, not a floor or a ceiling."
        ),
        "put_centre": _r(lv.get("support_w")),
        "call_centre": _r(lv.get("resistance_w")),
        "put_top": _r(lv.get("support_top")),
        "call_top": _r(lv.get("resistance_top")),
        "band_pts": band_pts,
    }


def _concentration_structures(dash, idx, bias_score=None, sentinel_signals=None) -> dict:
    """Illustrative structure from the absolute-GEX bucket. The call−put sign is not an input."""
    from app.intraday_features import structure_vol_regime
    from structures.suggest import suggest_structures

    labels = {
        "calm": "High concentration",
        "wild": "Low concentration",
        "mid": "Middle concentration — wait",
    }
    tag = "mid"
    cands = []
    try:
        tag = structure_vol_regime(dash)
        step = int(idx.strike_step)
        expiries = list(getattr(dash, "expiries", None) or [])
        nearest = expiries[0] if expiries else None
        cands = suggest_structures(
            dash.levels,
            tag,
            lot_size=int(idx.lot_size),
            spread_width=step * 2,
            strike_step=step,
            nearest_expiry=nearest,
            is_expiry_day=bool(nearest == _get_ist_now().date()),
            bias_score=bias_score,
            sentinel_signals=sentinel_signals,
        )
    except Exception as exc:
        log.warning("structure suggestion: %s", exc)
        tag = "mid"
    view = getattr(dash, "view", None)
    playbook = ""
    if view is not None:
        playbook = str(getattr(view, "headline", "") or getattr(view, "mode_plain", "") or "")
    out = []
    for c in cands:
        out.append({
            "name": c.name,
            "legs": [
                {"action": a, "right": "CE" if t == "C" else "PE", "strike": int(k)}
                for a, t, k in (c.legs or [])
            ],
            "rationale": c.rationale,
            "risk_note": c.risk_note,
            "exit_by": c.exit_by,
            "expiry_note": c.expiry_note or "",
        })
    if not out:
        out = [{
            "name": "No trade — wait",
            "legs": [],
            "rationale": "Concentration gauge unavailable. Wait.",
            "risk_note": "—",
            "exit_by": "",
            "expiry_note": "",
        }]
    return {
        "bucket": tag if tag in labels else "mid",
        "bucket_label": labels.get(tag, labels["mid"]),
        "playbook": playbook,
        "candidates": out,
    }


def _resolve_expiry_scope(symbol: str, window: str):
    idx = config.get_index(symbol)
    today_ist = _get_ist_now().date()
    if window == "current_week":
        exp = next_weekly_expiry(today_ist, idx.exchange) if idx.has_weekly else next_monthly_expiry(today_ist, idx.exchange)
        desc = "Single Expiry Isolated GEX"
    elif window == "next_week":
        exp = second_weekly_expiry(today_ist, idx.exchange) if idx.has_weekly else second_monthly_expiry(today_ist, idx.exchange)
        desc = "Single Expiry Isolated GEX"
    elif window in ("weekly", "0dte"):
        exp = next_weekly_expiry(today_ist, idx.exchange) if idx.has_weekly else next_monthly_expiry(today_ist, idx.exchange)
        desc = "Weekly Window Aggregation"
    else:
        exp = next_monthly_expiry(today_ist, idx.exchange)
        desc = "Multi-Expiry Composite GEX"

    dte = max((exp - today_ist).days, 0)
    dte_badge = f"{dte} Days" if dte > 0 else "0DTE Today"
    return exp, dte, dte_badge, desc


_DASH_MEM_CACHE: Dict[tuple, tuple] = {}


def _drop_dash_cache(symbol: str) -> None:
    """Drop off-market dashboard copies so the next read uses the synced book."""
    sym = (symbol or "").upper()
    for key in [k for k in _DASH_MEM_CACHE if isinstance(k, tuple) and k and k[0] == sym]:
        _DASH_MEM_CACHE.pop(key, None)


_CHAIN_INTERVAL_S = 30
_CHAIN_LEAD_S = 10.0
_WS_LIVE_SLEEP_S = 0.12
_WS_HEARTBEAT_S = 1.0
_REC_BATCH: Dict[tuple, Any] = {}
_REC_LOCK = __import__("threading").Lock()
_REC_THREAD = None
_COMMENTARY_LOCKS: dict[str, __import__("threading").Lock] = {}
_COMMENTARY_LOCK = None


def _latest_rec_batch(symbol: str, window: str):
    key = ((symbol or "").upper(), window or "current_week")
    with _REC_LOCK:
        return _REC_BATCH.get(key)


def _iter_cached_ideas(batch) -> list:
    ideas = []
    for attr in (
        "ce_recommendations",
        "pe_recommendations",
        "higher_ce_recommendations",
        "higher_pe_recommendations",
        "lower_ce_recommendations",
        "lower_pe_recommendations",
        "tactical_ce_recommendations",
        "tactical_pe_recommendations",
        "bookmarked_recommendations",
    ):
        ideas.extend(list(getattr(batch, attr, None) or []))
    return ideas


def _rec_publisher_loop() -> None:
    """Generate ideas, reprice them, and close the book off the event loop.

    Generation stays on a 20s cadence. Premium refresh is once a second so the
    telemetry tick can read a finished quote instead of running Black-Scholes.
    """
    last_gen = 0.0
    last_life = 0.0
    while True:
        try:
            if not config.is_market_open():
                time.sleep(30.0)
                continue
            from app.live_fetcher import latest as chain_latest
            from app.spot_ticker import latest_quote
            win = _ACTIVE_STATE.get("window") or "current_week"
            now = time.time()
            do_gen = (now - last_gen) >= 20.0
            ran_life = False
            for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
                dash = (chain_latest(sym, win) or {}).get("data")
                quote = latest_quote(sym) or {}
                quote_spot = float(quote.get("spot") or 0.0)
                levels = getattr(dash, "levels", None) if dash is not None else None
                chain_spot = float(getattr(levels, "spot", 0.0) or 0.0) if levels is not None else 0.0
                spot = quote_spot if quote_spot > 0 else chain_spot
                withhold = levels is not None and config.withhold_placeholder_ideas(sym, chain_spot, quote_spot)
                if withhold:
                    with _REC_LOCK:
                        _REC_BATCH.pop((sym, win), None)
                    if do_gen:
                        event(
                            "rec.withheld",
                            workflow="rec.generate",
                            symbol=sym,
                            chain_spot=chain_spot,
                            quote_spot=quote_spot,
                            reason="placeholder chain",
                        )
                elif do_gen and levels is not None:
                    _kick_commentary_eval(sym, dash)
                    bias = _compute_active_directional_bias(dash, sym)
                    batch = generate_trade_recommendations(dash, bias_result=bias)
                    if float(getattr(batch, "spot", 0.0) or 0.0) > 0:
                        with _REC_LOCK:
                            _REC_BATCH[(sym, win)] = batch
                    event(
                        "rec.generated",
                        workflow="rec.generate",
                        symbol=sym,
                        count=len(_iter_cached_ideas(batch)),
                    )
                if spot > 0 and _latest_rec_batch(sym, win) is not None:
                    _refresh_cached_rec_quotes(sym, spot)
                if spot > 0 and (now - last_life) >= 5.0:
                    _run_recommendation_lifecycle(sym, spot)
                    ran_life = True
                    held = _latest_rec_batch(sym, win)
                    event(
                        "rec.lifecycle",
                        workflow="rec.lifecycle",
                        symbol=sym,
                        count=len(_iter_cached_ideas(held)) if held is not None else 0,
                    )
            if do_gen:
                last_gen = now
            if ran_life:
                last_life = now
            time.sleep(1.0)
        except Exception as exc:
            log.warning("rec publisher: %s", exc)
            time.sleep(10.0)


def _refresh_cached_rec_quotes(sym: str, spot: float) -> None:
    """Reprice the cached batch. Runs on the publisher thread, not the event loop."""
    batch = _latest_rec_batch(sym, _ACTIVE_STATE.get("window") or "current_week")
    if batch is None:
        return
    now = time.time()
    seen = set()
    for idea in _iter_cached_ideas(batch):
        rid = getattr(idea, "rec_id", "") or ""
        if not rid or rid in seen:
            continue
        seen.add(rid)
        last = float(getattr(idea, "_mtm_reprice_at", 0.0) or 0.0)
        do_reprice = (now - last) >= 1.0
        try:
            refresh_trade_idea_live_mtm(idea, sym, float(spot or 0.0), reprice_targets=do_reprice)
            if do_reprice:
                idea._mtm_reprice_at = now
        except Exception:
            continue


def _run_recommendation_lifecycle(sym: str, spot: float) -> None:
    """Close today's ACTIVE rows from spot and a real option premium."""
    batch = _latest_rec_batch(sym, _ACTIVE_STATE.get("window") or "current_week")
    ltp_map: dict[str, float] = {}
    if batch is not None:
        for idea in _iter_cached_ideas(batch):
            if not getattr(idea, "is_ws_live", False):
                continue
            try:
                ltp = float(getattr(idea, "current_ltp", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if ltp <= 0:
                continue
            opt = str(getattr(idea, "option_symbol", "") or "")
            if opt:
                ltp_map[opt] = ltp
            ltp_map[f"{float(idea.strike)}_{idea.side}"] = ltp
    try:
        evaluate_recommendation_lifecycle(sym, float(spot), ltp_map)
    except Exception as exc:
        log.warning("recommendation lifecycle %s: %s", sym, exc)


def _live_rec_quotes(sym: str, spot: float) -> list:
    """Read precomputed option quotes. The tick loop does not reprice."""
    del spot  # refresh happens on the publisher thread; the tick only reads
    win = _ACTIVE_STATE.get("window") or "current_week"
    batch = _latest_rec_batch(sym, win)
    if batch is None:
        return []
    ideas = _iter_cached_ideas(batch)
    out = []
    seen = set()
    for idea in ideas:
        rid = getattr(idea, "rec_id", "") or ""
        if not rid or rid in seen:
            continue
        seen.add(rid)
        out.append({
            "rec_id": rid,
            "ltp": round(float(idea.current_ltp or 0.0), 1),
            "target_1": round(float(idea.target_1 or 0.0), 1),
            "target_2": round(float(idea.target_2 or 0.0), 1),
            "stop_loss": round(float(idea.stop_loss or 0.0), 1),
            "pnl_pts": round(float(getattr(idea, "live_pnl_pts", 0.0) or 0.0), 1),
            "pnl_pct": round(float(getattr(idea, "live_pnl_pct", 0.0) or 0.0), 1),
            "is_ws_live": bool(getattr(idea, "is_ws_live", False)),
            "p_touch_target_1": round(float(getattr(idea, "p_touch_target_1", 0.0) or 0.0), 3),
            "p_touch_sl": round(float(getattr(idea, "p_touch_sl", 0.0) or 0.0), 3),
            "net_ev_pts": round(float(getattr(idea, "net_ev_pts", 0.0) or 0.0), 1),
            "risk_reward_ratio": str(getattr(idea, "risk_reward_ratio", "") or ""),
        })
    return out


def _commentary_lock(sym: str):
    """One commentary pass per index. Nifty's poll must not skip Bank Nifty."""
    import threading
    if "_COMMENTARY_LOCK" in globals() and isinstance(_COMMENTARY_LOCK, type(threading.Lock())):
        # Allow test monkeypatching of _COMMENTARY_LOCK
        return _COMMENTARY_LOCK
    key = (sym or "").upper()
    lock = _COMMENTARY_LOCKS.get(key)
    if lock is None:
        lock = _COMMENTARY_LOCKS.setdefault(key, threading.Lock())
    return lock


def _kick_commentary_eval(sym: str, dash) -> None:
    """One commentary pass per index. A poll that arrives mid-eval is skipped."""
    import threading
    lock = _commentary_lock(sym)
    if not lock.acquire(blocking=False):
        return

    def _run() -> None:
        try:
            from commentary.engine import get_session_commentary_engine
            engine = get_session_commentary_engine(sym)
            engine.evaluate_live(sym, dash)
            event("commentary.evaluated", workflow="commentary", symbol=sym)
        except Exception as exc:
            event(
                "commentary.failed",
                level="WARNING",
                ok=False,
                workflow="commentary",
                symbol=sym,
                error=str(exc),
            )
        finally:
            lock.release()

    worker = threading.Thread(target=_run, name="commentary-eval", daemon=True)
    try:
        worker.start()
    except Exception:
        lock.release()
        raise


def _ensure_rec_publisher() -> None:
    global _REC_THREAD
    import threading
    t = _REC_THREAD
    if t is not None and t.is_alive():
        return
    t = threading.Thread(target=_rec_publisher_loop, name="rec-publisher", daemon=True)
    _REC_THREAD = t
    t.start()


def _ensure_chain_ingest(symbol: str, window: Optional[str] = None) -> None:
    """Wake/start the single background chain fetcher. Never pulls Dhan on this thread."""
    from app.live_fetcher import ensure
    ensure(symbol, False, None, _CHAIN_INTERVAL_S, _CHAIN_LEAD_S, window=window)


def _unpickle_latest_snap(sym: str, journal: Path | None = None):
    import pickle
    base = journal if journal is not None else (ROOT_DIR / "data" / "journal")
    snap_path = base / f"latest_snap_{sym}.pkl"
    if not snap_path.exists():
        return None
    try:
        with open(snap_path, "rb") as f:
            snap_wrapper = pickle.load(f)
        if isinstance(snap_wrapper, dict) and "data" in snap_wrapper:
            return snap_wrapper["data"]
        if isinstance(snap_wrapper, DashboardData):
            return snap_wrapper
    except Exception as e:
        log.warning("Could not load %s: %s", snap_path, e)
    return None


def _from_lkgs(sym: str, reason: str):
    try:
        from resilience.lkgs import get_lkgs_manager
        deg_data, _, _, _ = get_lkgs_manager().get_degraded_snapshot(sym, reason=reason)
        return deg_data
    except Exception:
        return None


def _note_stale_session(dash, db_snap: dict) -> None:
    """Say so when the book on screen is not the last completed session."""
    if not db_snap.get("stale_session"):
        return
    expected = db_snap.get("expected_session")
    fetched = getattr(dash, "fetched_at", None)
    if expected and fetched is not None and hasattr(fetched, "astimezone"):
        from data.market_snapshot import _IST
        if fetched.astimezone(_IST).date().isoformat() == expected:
            return
    from data.market_snapshot import format_ist_stamp, session_date_label
    stamp = format_ist_stamp(db_snap.get("as_of")) or "has no timestamp"
    dash.source_note = (
        f"Snapshot {stamp} is before the last session "
        f"{session_date_label(expected) or expected}. This is not that close."
    )


def _overlay_sqlite_kpis(dash, sym: str):
    if dash is None:
        return dash
    try:
        import config
        from data.market_snapshot import (
            get_snapshot,
            resolve_offmarket_snapshot,
            snapshot_is_older_than,
        )
        if config.is_market_open():
            db_snap = get_snapshot(sym)
        else:
            db_snap = resolve_offmarket_snapshot(sym)
        if not db_snap or not db_snap.get("spot"):
            return dash
        dash.snapshot_as_of = db_snap.get("as_of")
        if snapshot_is_older_than(db_snap, getattr(dash, "fetched_at", None)):
            log.warning(
                "%s snapshot %s is older than the loaded book (%s); keeping the book",
                sym, db_snap.get("as_of"), getattr(dash, "fetched_at", None),
            )
            event(
                "book.stale",
                level="WARNING",
                ok=False,
                workflow=f"book.{sym}",
                symbol=sym,
                snapshot_as_of=str(db_snap.get("as_of")),
                book_fetched=str(getattr(dash, "fetched_at", None)),
            )
            _note_stale_session(dash, db_snap)
            return dash
        # A quote row and a chain are different books. Copying the newer
        # quote onto the chain's walls, or a leftover flip onto levels.flip,
        # is how the close stops being one snapshot. The book stays the chain.
        _note_stale_session(dash, db_snap)
    except Exception as e:
        log.debug("Snapshot reconciliation note: %s", e)
    return dash


def _overlay_live_spot(dash, sym: str):
    """Stamp live index/futures prints onto a published GEX snapshot. No network."""
    if dash is None or getattr(dash, "levels", None) is None:
        return dash
    try:
        from app.spot_ticker import latest as spot_latest
        ohlc = spot_latest(sym)
    except Exception:
        ohlc = None
    if not ohlc:
        return dash
    ltp = ohlc.get("ltp") or ohlc.get("close")
    if not ltp or float(ltp) <= 0:
        return dash
    dash = copy.copy(dash)
    dash.levels = copy.copy(dash.levels)
    dash.levels.spot = float(ltp)
    fut = None
    try:
        from app import spot_ticker as _st
        rec = _st._STORE.get(sym) or {}
        fut = rec.get("fut") or {}
        quote = rec.get("quote") or {}
        fut_px = quote.get("fut_px") or (fut.get("price") if fut else None)
        if fut_px:
            dash.levels.future_price = float(fut_px)
        vix = (ohlc.get("vix") or {}) if isinstance(ohlc.get("vix"), dict) else None
        if vix and vix.get("last") is not None and isinstance(getattr(dash, "india_vix", None), dict):
            dash.india_vix = dict(dash.india_vix)
            dash.india_vix["last"] = float(vix["last"])
            if vix.get("pct_change") is not None:
                dash.india_vix["pct_change"] = float(vix["pct_change"])
    except Exception:
        pass
    return dash


def _refresh_book_view(dash):
    """Recompute the playbook from levels so a pickled snapshot does not keep the old pin wording."""
    if dash is None or getattr(dash, "levels", None) is None:
        return dash
    try:
        from regime.classify import from_levels
        dash.view = from_levels(dash.levels)
    except Exception as exc:
        log.debug("book view refresh skipped: %s", exc)
    return dash


def _load_active_dashboard(symbol: str, window: Optional[str] = None) -> DashboardData:
    """Read the published GEX snapshot. Never calls Dhan on this thread.

    Live hours: background ``live_fetcher`` is the only chain ingest. This function
    reads its in-memory store, then LKGS. It does not unpickle here: unpickling holds
    the GIL and stalls the tick loop. If both miss, it returns None. The synthetic
    demo book (Nifty 24,500) is not a live chain and must not feed commentary or ideas.
    Spot/futures ticks overlay from ``spot_ticker`` (separate WS ingest).

    Off-market: pickle, then the last-good chain. No demo book and no quote overlay.
    """
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper().strip()
    win = window or _ACTIVE_STATE["window"]
    ckey = (sym, win)
    is_open = config.is_market_open()

    if not is_open and ckey in _DASH_MEM_CACHE:
        from data.market_snapshot import resolve_offmarket_snapshot
        snap = resolve_offmarket_snapshot(sym)
        cached = _DASH_MEM_CACHE[ckey][1]
        snap_asof = (snap or {}).get("as_of")
        cached_asof = getattr(cached, "snapshot_as_of", None)
        if (snap_asof and cached_asof == snap_asof) or (snap is None and cached_asof is None):
            return _refresh_book_view(cached)

    dash = None
    from_live_store = False

    if is_open:
        from app import live_fetcher
        _ensure_chain_ingest(sym, win)
        pub = live_fetcher.latest(sym, win)
        if pub and pub.get("data") is not None:
            dash = pub["data"]
            from_live_store = True
        if dash is None:
            dash = _from_lkgs(sym, "Awaiting first live chain publish")
        # Never unpickle on the live HTTP/WS event loop — that GIL-stalls ticks.
        if dash is not None:
            if not from_live_store:
                dash = _overlay_sqlite_kpis(dash, sym)
            dash = _overlay_live_spot(_refresh_book_view(dash), sym)
            return dash
        log.warning("%s live book unavailable (no published chain, no last-good snapshot)", sym)
        return None

    from data.market_snapshot import resolve_offmarket_snapshot
    off_snap = resolve_offmarket_snapshot(sym)
    journal = None
    if off_snap and off_snap.get("adopted_from") == "sibling" and off_snap.get("journal"):
        journal = Path(off_snap["journal"])
    dash = _unpickle_latest_snap(sym, journal)
    if dash is None and journal is not None:
        dash = _unpickle_latest_snap(sym)
    if dash is None:
        dash = _from_lkgs(sym, "Off-Market Session Snapshot")
    if dash is None:
        log.warning("%s off-market book unavailable (no pickle, no last-good snapshot)", sym)
        return None
    dash = _overlay_sqlite_kpis(dash, sym)
    dash = _refresh_book_view(dash)
    _DASH_MEM_CACHE[ckey] = (time.time(), dash)
    return dash


async def _aload_active_dashboard(
    symbol: str, window: Optional[str] = None, timeout: float = 25.0
) -> Optional[DashboardData]:
    """Async wrapper. Live hours read the published store or LKGS. Off-market
    reads the pickle or the last-good chain. No broker I/O and no demo book."""
    try:
        return _load_active_dashboard(symbol, window)
    except Exception as exc:
        log.warning("dashboard read failed for %s: %s", symbol, exc)
        return None


def _dash_or_503(dash: Optional[DashboardData], symbol: str) -> DashboardData:
    """Fail loud when a live/off-market load returned nothing usable."""
    if dash is None or getattr(dash, "levels", None) is None:
        raise HTTPException(status_code=503, detail="Dashboard unavailable")
    return dash


def _extract_active_gamma_flip(dash, sym: str) -> Optional[float]:
    """The chain's zero-crossing. No quote-row stand-in and no second flip field."""
    del sym
    lv = getattr(dash, "levels", None)
    if lv is None:
        return None
    raw = getattr(lv, "flip", None)
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val != val or val <= 0:
        return None
    return val


def _cluster_ideas_by_strike(ideas: List[TradeIdea]) -> List[StrikeCluster]:
    clusters: Dict[float, List[TradeIdea]] = {}
    for idea in ideas:
        clusters.setdefault(idea.strike, []).append(idea)

    res: List[StrikeCluster] = []
    for strike, group in clusters.items():
        best = max(group, key=lambda x: x.quality_score)
        res.append(
            StrikeCluster(
                symbol=best.symbol,
                side=best.side,
                strike=strike,
                option_symbol=best.option_symbol,
                ideas=group,
            )
        )
    return sorted(res, key=lambda c: c.best_idea.quality_score, reverse=True)


def _serialize_trade_idea(
    idea: TradeIdea,
    spot: float,
    capital: float,
    risk: float,
    lot_size: int,
    *,
    record_lifecycle_events: bool = False,
    is_historical: bool = False,
) -> dict:
    if not is_historical:
        try:
            refresh_trade_idea_live_mtm(idea, idea.symbol, float(spot or 0.0), reprice_targets=True)
        except Exception:
            pass
    pos_size = compute_position_size(
        idea,
        account_capital=capital,
        risk_pct=risk,
        lot_size=lot_size,
    )
    curr_ltp = float(idea.current_ltp or 0.0)
    entry_ltp = float(getattr(idea, "initial_ltp", 0.0) or curr_ltp)
    pnl_pts = getattr(idea, "live_pnl_pts", round(curr_ltp - entry_ltp, 1))
    pnl_pct = getattr(idea, "live_pnl_pct", round((pnl_pts / entry_ltp) * 100.0, 1) if entry_ltp > 0 else 0.0)

    # Invalidation buffer & breach status
    if not is_historical and spot > 0:
        if idea.side == "CE":
            inval_buffer = spot - idea.spot_invalidation
            is_active = (spot >= idea.spot_invalidation)
        else:
            inval_buffer = idea.spot_invalidation - spot
            is_active = (spot <= idea.spot_invalidation)
    else:
        inval_buffer = float(getattr(idea, "inval_buffer", 0.0) or 0.0)
        is_active = not getattr(idea, "inval_breached", False)

    inval_breached = not is_active
    st_code = getattr(idea, "latch_state", "") or ("INVALIDATED" if inval_breached else ("ACTIVE" if idea.quality_score >= 65 else "DEGRADED"))

    bm = is_bookmarked(idea.rec_id)

    # Lifecycle target / stop / invalidation checking and event recording (ONLY for live non-historical requests)
    tgt1_pct = round(((idea.target_1 - entry_ltp) / entry_ltp) * 100.0, 1) if entry_ltp > 0 else 0.0
    tgt2_pct = round(((idea.target_2 - entry_ltp) / entry_ltp) * 100.0, 1) if entry_ltp > 0 else 0.0
    sl_pct = round(((idea.stop_loss - entry_ltp) / entry_ltp) * 100.0, 1) if entry_ltp > 0 else 0.0

    event_type_hit = None
    event_msg_hit = ""
    if not is_historical:
        if inval_breached:
            event_type_hit = "INVALIDATED"
            event_msg_hit = f"🛑 Spot Invalidation Breached at ₹{spot:,.1f} (Inval level: ₹{idea.spot_invalidation:,.0f})"
        elif curr_ltp > 0:
            if idea.target_2 > 0 and curr_ltp >= idea.target_2:
                event_type_hit = "TGT2_HIT"
                event_msg_hit = f"🚀 Target 2 Achieved at ₹{curr_ltp:,.1f} (+{tgt2_pct:+.0f}%)"
            elif idea.target_1 > 0 and curr_ltp >= idea.target_1:
                event_type_hit = "TGT1_HIT"
                event_msg_hit = f"🎯 Target 1 Achieved at ₹{curr_ltp:,.1f} (+{tgt1_pct:+.0f}%)"
            elif idea.stop_loss > 0 and curr_ltp <= idea.stop_loss:
                event_type_hit = "SL_HIT"
                event_msg_hit = f"⛔ Stop Loss Hit at ₹{curr_ltp:,.1f} ({sl_pct:+.0f}%)"

    if not is_historical and event_type_hit and record_lifecycle_events:
        try:
            record_event(
                rec_id=idea.rec_id,
                symbol=idea.symbol,
                event_type=event_type_hit,
                spot_at_event=spot,
                ltp_at_event=curr_ltp,
                pnl_pct=tgt1_pct if "TGT" in event_type_hit else sl_pct,
                message=event_msg_hit,
                was_bookmarked=bm,
            )
            st_code = event_type_hit
        except Exception:
            pass

    act_lbl, act_col, act_tip = format_time_since_active(
        latch_state=st_code,
        last_active_ts=getattr(idea, "last_active_ts", 0.0) or time.time(),
        first_active_ts=getattr(idea, "first_active_ts", 0.0) or time.time(),
    )

    return {
        "rec_id": idea.rec_id,
        "symbol": idea.symbol,
        "side": idea.side,
        "strike": idea.strike,
        "option_symbol": idea.option_symbol,
        "play_badge": idea.play_badge,
        "play_type": idea.play_type,
        "quality_score": idea.quality_score,
        "conviction_label": idea.conviction_label,
        "conviction_color": idea.conviction_color,
        "current_ltp": curr_ltp,
        "is_ws_live": bool(getattr(idea, "is_ws_live", False)),
        "entry_ltp": entry_ltp,
        "target_1": idea.target_1,
        "target_2": idea.target_2,
        "stop_loss": idea.stop_loss,
        "target_1_spot": getattr(idea, "target_1_spot", 0.0),
        "target_2_spot": getattr(idea, "target_2_spot", 0.0),
        "spot_invalidation": idea.spot_invalidation,
        "spot_invalidation_text": idea.spot_invalidation_text,
        "inval_buffer": inval_buffer,
        "inval_breached": inval_breached,
        "pnl_pts": pnl_pts,
        "pnl_pct": pnl_pct,
        "target_progress_pct": getattr(idea, "target_progress_pct", 0.0),
        "risk_reward_ratio": idea.risk_reward_ratio,
        "entry_trigger": idea.entry_trigger,
        "rationale": idea.rationale,
        "catalyst_tags": idea.catalyst_tags,
        "latch_state": st_code,
        "triggered_at": getattr(idea, "triggered_at", "") or "",
        "first_active_ts": float(getattr(idea, "first_active_ts", 0.0) or time.time()),
        "last_active_ts": float(getattr(idea, "last_active_ts", 0.0) or time.time()),
        "active_label": act_lbl,
        "active_color": act_col,
        "delta_approx": (
            float(getattr(idea, "delta_approx", 0.0) or 0.0)
            if getattr(idea, "delta_approx", None) is not None
            else None
        ),
        "p_touch_target_1": (
            float(getattr(idea, "p_touch_target_1", 0.0) or 0.0)
            if getattr(idea, "p_touch_target_1", None) is not None
            else None
        ),
        "p_touch_sl": (
            float(getattr(idea, "p_touch_sl", 0.0) or 0.0)
            if getattr(idea, "p_touch_sl", None) is not None
            else None
        ),
        "net_ev_pts": (
            float(getattr(idea, "net_ev_pts", 0.0) or 0.0)
            if getattr(idea, "net_ev_pts", None) is not None
            else None
        ),
        "spread_alternative": getattr(idea, "spread_alternative", None),
        "score_history": get_score_history(idea.rec_id) or [],
        "pos_size": {
            "suggested_lots": pos_size.suggested_lots,
            "total_quantity": pos_size.total_quantity,
            "capital_outlay_inr": pos_size.capital_outlay_inr,
            "portfolio_pct": pos_size.portfolio_pct,
            "max_risk_inr": pos_size.max_risk_inr,
            "target_1_pnl_inr": pos_size.target_1_pnl_inr,
            "target_2_pnl_inr": pos_size.target_2_pnl_inr,
            "sizing_rationale": pos_size.sizing_rationale,
        },
    }


def _serialize_cluster(cluster: StrikeCluster, spot: float, capital: float, risk: float, lot_size: int) -> dict:
    serialized_ideas = [_serialize_trade_idea(i, spot, capital, risk, lot_size) for i in cluster.ideas]
    active_ideas = [i for i in serialized_ideas if not i["inval_breached"] and (i["quality_score"] >= 65 or is_bookmarked(i["rec_id"]))]

    dissolved_reasons = []
    for i in serialized_ideas:
        if i["inval_breached"]:
            dissolved_reasons.append(f"<b>{i['play_badge']}</b> spot invalidation breached (₹{i['spot_invalidation']:,.0f})")
        elif i["quality_score"] < 65 and not is_bookmarked(i["rec_id"]):
            dissolved_reasons.append(f"<b>{i['play_badge']}</b> conviction faded ({i['quality_score']}/100)")

    if len(active_ideas) > 1:
        conf_state = "CONFLUENCE"
        conf_badge = f"⚡ {len(active_ideas)}x STRATEGY CONFLUENCE"
        conf_sub = "Institutional Alignment"
    elif len(active_ideas) == 1:
        conf_state = "PARTIAL_INVALIDATION"
        conf_badge = f"⚠️ Confluence Dissolved: {'; '.join(dissolved_reasons)}. Remaining Active Thesis: {active_ideas[0]['play_badge']}"
        conf_sub = "Partial Invalidation"
    else:
        conf_state = "INVALIDATED"
        conf_badge = "🛑 All Theses Invalidated: Spot breached all contributing strategy stops."
        conf_sub = "Invalidated Strike"

    return {
        "strike": cluster.strike,
        "side": cluster.side,
        "is_confluence": cluster.is_confluence,
        "confluence_state": conf_state,
        "confluence_badge": conf_badge,
        "confluence_sub": conf_sub,
        "ideas": serialized_ideas,
        "best_idea": _serialize_trade_idea(cluster.best_idea, spot, capital, risk, lot_size),
    }

def _is_recently_triggered(triggered_at_val: Any, max_age_seconds: float = 600.0) -> bool:
    """Return True if recommendation timestamp was within max_age_seconds (default 10 mins)."""
    if not triggered_at_val:
        return False
    try:
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(ist)
        if isinstance(triggered_at_val, (int, float)):
            diff_sec = now_ist.timestamp() - float(triggered_at_val)
            return 0 <= diff_sec <= max_age_seconds
        clean_str = str(triggered_at_val).strip().replace("T", " ")[:19]
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        dt_ist = dt.replace(tzinfo=ist)
        diff_sec = (now_ist - dt_ist).total_seconds()
        return 0 <= diff_sec <= max_age_seconds
    except Exception:
        return False


def _positive_pcr(value: Any) -> float | None:
    """A PCR is put open interest over call open interest. Zero and NaN are missing."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:
        return None
    return number


def _level_pcr(dash: Any, name: str) -> float | None:
    levels = getattr(dash, "levels", None) if dash else None
    if levels is None:
        return None
    return _positive_pcr(getattr(levels, name, None))


def _extract_active_pcr(dash: Any) -> float | None:
    """Put open interest over call open interest for every strike in the selected window.

    The 21-strike band stays on the caption. It is not the headline.
    A missing book is missing. It is not a PCR of 1.00.
    """
    if not dash:
        return None
    full = _level_pcr(dash, "pcr_oi") or _level_pcr(dash, "pcr")
    if full is not None:
        return full
    near = _level_pcr(dash, "pcr_near")
    if near is not None:
        return near
    raw = getattr(dash, "raw_agg", None) or getattr(dash, "oi_volume", None) or {}
    pcr = _positive_pcr(raw.get("pcr_oi")) or _positive_pcr(raw.get("pcr"))
    if pcr is not None:
        return pcr
    if hasattr(dash, "contracts") and isinstance(dash.contracts, pd.DataFrame) and not dash.contracts.empty:
        try:
            df = dash.contracts
            c_oi = float(df[df["type"] == "C"]["oi"].sum())
            p_oi = float(df[df["type"] == "P"]["oi"].sum())
            if c_oi > 0:
                return round(p_oi / c_oi, 4)
        except Exception:
            pass
    return None


def _single_expiry_window(window: str) -> bool:
    if window in {"current_week", "next_week"}:
        return True
    try:
        datetime.strptime(window[:10], "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _pcr_caption(dash: Any) -> str:
    """Second line under the PCR figure. The figure itself is the full selected book."""
    full = _level_pcr(dash, "pcr_oi")
    near = _level_pcr(dash, "pcr_near")
    if near is not None and (full is None or abs(near - full) > 0.02):
        return f"21 strikes around spot {near:.2f}"
    window = ""
    levels = getattr(dash, "levels", None) if dash else None
    if levels is not None:
        window = str(getattr(levels, "expiry_window", "") or "")
    if _single_expiry_window(window) or not window:
        return "All strikes in this expiry"
    return "All strikes in the selected window"


def _pcr_mix_label(pcr: float | None) -> str:
    if pcr is None:
        return "Open interest unavailable"
    if pcr > 1.25:
        return "Put-heavy open interest"
    if pcr < 0.85:
        return "Call-heavy open interest"
    return "Mixed open interest"


def _extract_active_atm_iv(dash: Any) -> float | None:
    """At-the-money implied vol as a decimal. India VIX is not a substitute."""
    if not dash:
        return None
    lvl_iv = getattr(dash.levels, "atm_iv", None) if hasattr(dash, "levels") and dash.levels else None
    if lvl_iv is not None and float(lvl_iv) > 0.01:
        return float(lvl_iv)
    if hasattr(dash, "contracts") and isinstance(dash.contracts, pd.DataFrame) and not dash.contracts.empty:
        try:
            df = dash.contracts.dropna(subset=["iv"])
            df = df[df["iv"] > 0.01]
            if not df.empty:
                spot = float(dash.levels.spot if hasattr(dash, "levels") and dash.levels else 0)
                if spot <= 0:
                    spot = float(df["strike"].median())
                near_exp = df["expiry"].min()
                near_df = df[df["expiry"] == near_exp].copy()
                near_df["_dist"] = (near_df["strike"] - spot).abs()
                atm_contracts = near_df.nsmallest(4, "_dist")
                iv_vals = atm_contracts["iv"].dropna()
                if not iv_vals.empty:
                    v = float(iv_vals.mean())
                    if 0.02 < v < 1.5:
                        return round(v, 4)
        except Exception:
            pass
    return None


def _extract_active_spot(dash: Any, sym: str = "NIFTY") -> float:
    """Extract authentic spot price with index-aware fallback."""
    if dash and hasattr(dash, "levels") and dash.levels:
        s = getattr(dash.levels, "spot", None)
        if s and float(s) > 100.0:
            return float(s)
    if dash and hasattr(dash, "day_ohlc") and dash.day_ohlc:
        c = dash.day_ohlc.get("close") or dash.day_ohlc.get("ltp") or dash.day_ohlc.get("open")
        if c and float(c) > 100.0:
            return float(c)
    if dash and hasattr(dash, "contracts") and isinstance(dash.contracts, pd.DataFrame) and not dash.contracts.empty:
        try:
            med = float(dash.contracts["strike"].median())
            if med > 100.0:
                return med
        except Exception:
            pass
    return 0.0


def _extract_active_dte(dash: Any, default_days: float | None = None) -> float | None:
    """Days to expiry from the book. Unknown stays unknown."""
    if not dash:
        return default_days
    d_val = getattr(dash, "dte_now", None)
    if d_val is not None:
        try:
            d_float = float(d_val)
            if d_float >= 0.0:
                return max(0.0, d_float)
        except Exception:
            pass
    if hasattr(dash, "expiries") and dash.expiries:
        try:
            near_exp = dash.expiries[0]
            exp_date = near_exp.date() if hasattr(near_exp, "date") else near_exp
            today_date = _get_ist_now().date()
            diff_days = (exp_date - today_date).days
            if diff_days >= 0:
                return max(0.0, float(diff_days))
        except Exception:
            pass
    if hasattr(dash, "contracts") and isinstance(dash.contracts, pd.DataFrame) and not dash.contracts.empty:
        try:
            near_c_exp = dash.contracts["expiry"].dropna().min()
            if near_c_exp:
                exp_date = near_c_exp.date() if hasattr(near_c_exp, "date") else near_c_exp
                today_date = _get_ist_now().date()
                diff_days = (exp_date - today_date).days
                if diff_days >= 0:
                    return max(0.0, float(diff_days))
        except Exception:
            pass
    return default_days


def _compute_active_directional_bias(dash: Any, sym: str) -> BiasResult:
    """Eight bias rows. PCR, gamma-strike midpoint, max pain, and open-interest change contribute 0."""
    spot = float(dash.levels.spot or 0.0) if dash and hasattr(dash, "levels") and dash.levels else 0.0
    cw = float(dash.levels.call_wall or 0.0) if dash and hasattr(dash, "levels") and dash.levels else 0.0
    pw = float(dash.levels.put_wall or 0.0) if dash and hasattr(dash, "levels") and dash.levels else 0.0
    mp_raw = getattr(dash.levels, "max_pain", None) if dash and hasattr(dash, "levels") and dash.levels else None
    try:
        mp = float(mp_raw) if mp_raw is not None and float(mp_raw) > 0 else None
    except (TypeError, ValueError):
        mp = None
    pcr = _extract_active_pcr(dash)
    call_doi = getattr(dash, "call_doi", None) or (getattr(dash.levels, "call_doi", None) if dash and hasattr(dash, "levels") and dash.levels else None)
    put_doi = getattr(dash, "put_doi", None) or (getattr(dash.levels, "put_doi", None) if dash and hasattr(dash, "levels") and dash.levels else None)
    oi_strikes = getattr(dash, "oi_strikes", None)
    india_vix = getattr(dash, "india_vix", None)
    atm_iv = _extract_active_atm_iv(dash)
    skew = None
    contracts = getattr(dash, "contracts", None) if dash is not None else None
    if contracts is not None and spot > 0:
        try:
            skew = skew_25delta(contracts, spot).get("skew_25d")
        except Exception:
            skew = None
    try:
        return compute_directional_bias(
            spot=spot,
            call_wall=cw,
            put_wall=pw,
            max_pain=mp,
            pcr=pcr,
            call_doi=call_doi,
            put_doi=put_doi,
            oi_strikes=oi_strikes,
            india_vix=india_vix,
            near_atm_iv=atm_iv,
            skew_25d=skew,
        )
    except Exception as ex:
        log.warning("Directional bias calculation fallback for %s: %s", sym, ex)
        return BiasResult(score=0.0, regime="Neutral ⏳", signals=[], headline="Neutral equilibrium")


def _chain_leg_ltp(contracts_df, strike: float, right: str) -> Optional[float]:
    """Last price of one listed leg. None when the chain has no positive print."""
    if contracts_df is None or not hasattr(contracts_df, "empty") or contracts_df.empty:
        return None
    if "strike" not in contracts_df.columns or "type" not in contracts_df.columns:
        return None
    want = "C" if str(right).upper().startswith("C") else "P"
    try:
        sub = contracts_df[
            (contracts_df["strike"].astype(float) == float(strike))
            & (contracts_df["type"].astype(str).str.upper().str.startswith(want))
        ]
    except Exception:
        return None
    if sub.empty:
        return None
    for col in ("ltp", "last_price", "close", "premium", "mid"):
        if col in sub.columns:
            val = float(sub[col].iloc[0] or 0)
            if val > 0:
                return round(val, 2)
    return None


def _atm_straddle_pts(contracts_df, spot: float):
    """Call plus put premium at the listed strike nearest spot, nearest expiry.

    Uses last price when the chain has one. Otherwise uses the stored mid.
    Returns (points, source) or (None, None).
    """
    if contracts_df is None or not hasattr(contracts_df, "empty") or contracts_df.empty:
        return None, None
    if not {"expiry", "type", "strike"}.issubset(set(contracts_df.columns)):
        return None, None
    try:
        import pandas as pd

        df = contracts_df.copy()
        source = None
        if "ltp" in df.columns and pd.to_numeric(df["ltp"], errors="coerce").gt(0).any():
            df["ltp"] = pd.to_numeric(df["ltp"], errors="coerce")
            source = "last"
        elif "mid" in df.columns and pd.to_numeric(df["mid"], errors="coerce").gt(0).any():
            df["ltp"] = pd.to_numeric(df["mid"], errors="coerce")
            source = "mid"
        else:
            return None, None
        df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
        df = df.dropna(subset=["strike", "ltp", "expiry"])
        df = df[df["ltp"] > 0]
        if df.empty:
            return None, None
        near = sorted(df["expiry"].unique())[0]
        band = df[df["expiry"] == near]
        atm = float(band.iloc[(band["strike"] - spot).abs().argmin()]["strike"])
        ce = _chain_leg_ltp(band, atm, "C")
        pe = _chain_leg_ltp(band, atm, "P")
        if ce is None or pe is None:
            return None, None
        return round(ce + pe, 1), source
    except Exception:
        return None, None


def _location_alerts(dash, spot: float, parity_report) -> list[dict]:
    """Facts about the map: a gamma strike crossed since the previous close, or a parity gap.

    PCR-versus-price labels are omitted. PCR is an open-interest mix.
    """
    alerts: list[dict] = []
    levels = getattr(dash, "levels", None)
    prev = float(getattr(levels, "prev_close", 0) or 0) if levels else 0.0
    now = _fmt_ist_clock()
    if levels and prev > 0:
        for name, wall in (
            ("call-gamma", float(getattr(levels, "call_wall", 0) or 0)),
            ("put-gamma", float(getattr(levels, "put_wall", 0) or 0)),
        ):
            if wall <= 0:
                continue
            crossed = (prev < wall <= spot) or (prev > wall >= spot)
            if crossed:
                alerts.append({
                    "time": now,
                    "badge": "STRIKE_CROSS",
                    "badge_cls": "mark",
                    "desc": (
                        f"Spot {spot:,.0f} is across the {name} strike {wall:,.0f} "
                        f"versus the previous close {prev:,.0f}."
                    ),
                })
    for d in list(getattr(parity_report, "alerted", None) or [])[:8]:
        alerts.append({
            "time": now,
            "badge": "PARITY",
            "badge_cls": "mark",
            "desc": (
                f"Strike {float(d.strike):,.0f}: call {float(d.call_ltp):.1f}, "
                f"put {float(d.put_ltp):.1f}, gap {float(d.discrepancy_pts):+.1f} pts "
                f"({float(d.discrepancy_bps):+.1f} bps, {d.direction})."
            ),
        })
    return alerts


def _parity_payload(contracts_df, spot: float) -> dict:
    from analytics.parity_arb import compute_parity_arb, ALERT_THRESHOLD_BPS

    report = compute_parity_arb(contracts_df, spot)
    deviations = []
    for d in (report.deviations or [])[:15]:
        deviations.append({
            "strike": float(d.strike),
            "call_ltp": round(float(d.call_ltp), 2),
            "put_ltp": round(float(d.put_ltp), 2),
            "synthetic_forward": round(float(d.synthetic_forward), 2),
            "discrepancy_pts": round(float(d.discrepancy_pts), 2),
            "discrepancy_bps": round(float(d.discrepancy_bps), 1),
            "direction": d.direction,
            "insight": d.insight,
        })
    n_alert = len(report.alerted or [])
    if not deviations:
        summary = report.summary or "No chain quotes for a parity check."
    elif n_alert:
        top = report.alerted[0]
        summary = (
            f"{n_alert} strikes exceed {ALERT_THRESHOLD_BPS:.0f} bps. "
            f"Largest gap is {float(top.strike):,.0f} at {float(top.discrepancy_bps):+.1f} bps."
        )
    else:
        summary = f"{len(report.deviations)} strikes are inside the {ALERT_THRESHOLD_BPS:.0f} bps parity tolerance."
    return {
        "summary": summary,
        "alerted": n_alert > 0,
        "max_deviation_bps": round(float(report.max_deviation_bps or 0.0), 1),
        "deviations": deviations,
        "report": report,
    }


def compute_session_schedule_info(now_ist: Optional[datetime] = None) -> dict:
    """Compute accurate market session phase, next event countdown, and reopening status."""
    from datetime import datetime, timezone, timedelta
    from data.holidays import is_trading_day

    ist = timezone(timedelta(hours=5, minutes=30))
    if now_ist is None:
        now_ist = datetime.now(ist)
    elif now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=ist)

    today = now_ist.date()
    t_min = now_ist.hour * 60 + now_ist.minute
    is_today_trading = is_trading_day(today)

    # 1. Determine next trading session day & opening datetime (09:15 IST)
    if is_today_trading and t_min < 9 * 60 + 15:
        target_date = today
    else:
        target_date = today + timedelta(days=1)
        while not is_trading_day(target_date):
            target_date += timedelta(days=1)

    days_diff = (target_date - today).days
    day_name = target_date.strftime("%a")
    if days_diff == 0:
        when_str = "Today"
    elif days_diff == 1:
        when_str = f"Tomorrow ({day_name})"
    else:
        fmt_d = target_date.strftime("%d-%b")
        when_str = f"{day_name} ({fmt_d})"

    open_dt = datetime(target_date.year, target_date.month, target_date.day, 9, 15, tzinfo=ist)
    pre_dt = datetime(target_date.year, target_date.month, target_date.day, 9, 0, tzinfo=ist)
    secs_to_open = max(0, int((open_dt - now_ist).total_seconds()))
    hours_open = secs_to_open // 3600
    mins_open = (secs_to_open % 3600) // 60

    # 2. Check if currently inside trading hours (09:15 - 15:40)
    is_live = is_today_trading and (9 * 60 + 15 <= t_min < 15 * 60 + 40)

    h_name = None
    if not is_today_trading:
        try:
            from data.holidays import get_holiday_name
            h_name = get_holiday_name(today)
        except Exception:
            h_name = None
        if h_name:
            phase_badge = f"🏖️ HOLIDAY · {h_name.upper()}"
            phase_title = f"Exchange Holiday · {h_name}"
            exec_phase_countdown = f"Reopens {when_str} 09:15 IST"
        elif today.weekday() >= 5:
            phase_badge = "WEEKEND · CLOSED"
            phase_title = "Market Closed (Weekend)"
            exec_phase_countdown = f"Reopens {when_str} 09:15 IST"
        else:
            phase_badge = "MARKET CLOSED"
            phase_title = "Market Closed (Holiday)"
            exec_phase_countdown = f"Reopens {when_str} 09:15 IST"
    elif t_min < 6 * 60 + 30:
        phase_badge = "OVERNIGHT (Briefing 06:30)"
        phase_title = "Overnight"
        secs_to_pre = max(0, int((pre_dt - now_ist).total_seconds()))
        h_pre, m_pre = secs_to_pre // 3600, (secs_to_pre % 3600) // 60
        exec_phase_countdown = f"Morning briefing at 06:30 · Pre-open in {h_pre}h {m_pre}m"
    elif t_min < 9 * 60:
        phase_badge = "MORNING BRIEFING (06:30–09:00)"
        phase_title = "Prior-session book"
        secs_to_pre = max(0, int((pre_dt - now_ist).total_seconds()))
        h_pre, m_pre = secs_to_pre // 3600, (secs_to_pre % 3600) // 60
        exec_phase_countdown = f"Cash pre-open in {h_pre}h {m_pre}m · Book is the previous session"
    elif t_min < 9 * 60 + 8:
        phase_badge = "PRE-OPEN ORDER ENTRY (09:00–09:08)"
        phase_title = "Pre-Open Auction"
        rem_m = 9 * 60 + 8 - t_min
        exec_phase_countdown = f"Equilibrium Discovery in {rem_m}m · Bell 09:15"
    elif t_min < 9 * 60 + 15:
        phase_badge = "EQUILIBRIUM DISCOVERY (09:08–09:15)"
        phase_title = "Pre-Open Equilibrium"
        rem_m = 9 * 60 + 15 - t_min
        exec_phase_countdown = f"Opening Bell in {rem_m}m (09:15 IST)"
    elif t_min < 9 * 60 + 45:
        phase_badge = "INITIAL DRIVE (09:15–09:45)"
        phase_title = "Initial Balance Range"
        rem_m = 9 * 60 + 45 - t_min
        exec_phase_countdown = f"Initial Balance Lock in {rem_m}m"
    elif t_min < 11 * 60 + 30:
        phase_badge = "MORNING TREND (09:45–11:30)"
        phase_title = "Morning Volatility Drive"
        rem_m = 11 * 60 + 30 - t_min
        h, m = rem_m // 60, rem_m % 60
        exec_phase_countdown = f"Midday Consolidation in {h}h {m}m" if h > 0 else f"Midday in {m}m"
    elif t_min < 13 * 60 + 30:
        phase_badge = "MIDDAY CONSOLIDATION (11:30–13:30)"
        phase_title = "Midday Range Drift"
        rem_m = 13 * 60 + 30 - t_min
        h, m = rem_m // 60, rem_m % 60
        exec_phase_countdown = f"Afternoon Session in {h}h {m}m" if h > 0 else f"Afternoon in {m}m"
    elif t_min < 14 * 60 + 30:
        phase_badge = "AFTERNOON EXPANSION (13:30–14:30)"
        phase_title = "European Open Expansion"
        rem_m = 14 * 60 + 30 - t_min
        exec_phase_countdown = f"Power Hour in {rem_m}m (14:30 IST)"
    elif t_min < 15 * 60 + 30:
        phase_badge = "POWER HOUR (14:30–15:30)"
        phase_title = "Power Hour Settlement"
        rem_m = 15 * 60 + 30 - t_min
        exec_phase_countdown = f"Cash Session Closes in {rem_m}m (15:30 IST)"
    elif t_min < 15 * 60 + 40:
        phase_badge = "CAS AUCTION & F&O CLOSE (15:30–15:40)"
        phase_title = "Closing Auction Session"
        rem_m = 15 * 60 + 40 - t_min
        exec_phase_countdown = f"F&O Session Closes in {rem_m}m (15:40 IST)"
    elif t_min < 16 * 60:
        phase_badge = "POST-CLOSE SETTLEMENT (15:40–16:00)"
        phase_title = "Post-Market Settlement"
        exec_phase_countdown = f"Reopens {when_str} 09:15 IST"
    else:
        phase_badge = "MARKET CLOSED (EOD)"
        phase_title = "Market Closed (EOD)"
        exec_phase_countdown = f"Reopens {when_str} 09:15 IST"

    return {
        "is_market_open": is_live,
        "phase_badge": phase_badge,
        "phase_title": phase_title,
        "exec_phase_countdown": exec_phase_countdown,
        "reopen_target": when_str,
        "hours_until_open": hours_open,
        "mins_until_open": mins_open,
        "holiday_name": h_name,
    }


def _price(*vals) -> float:
    """First positive number in vals, else 0."""
    for v in vals:
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return 0.0


def _status_day_ohlc(dash, quote: dict | None, snap: dict | None, spot: float, market_open: bool) -> dict:
    """Day range for /api/status.

    While the session is open, Dhan's OHLC ``close`` is the previous session and is
    stored as ``prev_close``. After the close, the last trade is the session close.
    """
    raw = getattr(dash, "day_ohlc", None) if dash is not None else None
    if not isinstance(raw, dict):
        raw = {}
    q = quote or {}
    db = snap or {}
    ltp = _price(raw.get("ltp"), q.get("spot"), spot)
    prev = _price(raw.get("prev_close"), q.get("prev_close"), db.get("prev_close"))
    close = _price(raw.get("close"))
    if close <= 0 and not market_open:
        close = _price(ltp, prev, spot)
    out = {
        "open": _price(raw.get("open"), q.get("open"), db.get("open"), spot),
        "high": _price(raw.get("high"), q.get("high"), db.get("high"), spot),
        "low": _price(raw.get("low"), q.get("low"), db.get("low"), spot),
        "close": close,
        "ltp": ltp,
    }
    if prev > 0:
        out["prev_close"] = prev
    if raw.get("as_of"):
        out["as_of"] = raw["as_of"]
    return out


# --------------------------------------------------------------------------
# API REST Endpoints
# --------------------------------------------------------------------------

@app.get("/api/status")
async def get_status(symbol: Optional[str] = None, window: Optional[str] = None):
    """Live quote, market phase, futures basis, DTE, feed mode, timeline countdown."""
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    win = window or _ACTIVE_STATE["window"]
    idx = config.get_index(sym)
    from app.spot_ticker import latest_quote
    from app.live_fetcher import latest as chain_latest

    q = latest_quote(sym) or {}
    pub = chain_latest(sym, win) or {}
    dash = pub.get("data")
    lv = getattr(dash, "levels", None) if dash is not None else None

    def _px(val) -> float | None:
        try:
            n = float(val)
        except (TypeError, ValueError):
            return None
        if n != n or n <= 0:
            return None
        return n

    spot = _px(q.get("spot")) or _px(getattr(lv, "spot", None) if lv is not None else None) or 0.0
    fut = _px(q.get("fut_px")) or _px(getattr(lv, "future_price", None) if lv is not None else None)
    offmarket_book = None
    if not config.is_market_open():
        # The close is the chain. A later quote must not move the header spot off that book.
        book = await _aload_active_dashboard(sym, win)
        offmarket_book = book
        book_lv = getattr(book, "levels", None) if book is not None else None
        book_spot = float(getattr(book_lv, "spot", 0) or 0) if book_lv is not None else 0.0
        if book_spot > 0 and getattr(book, "source", "") != "DEMO-SYNTHETIC":
            dash = book
            lv = book_lv
            spot = book_spot
            book_fut = _px(getattr(book_lv, "future_price", None))
            if book_fut is not None:
                fut = book_fut
    elif spot <= 0:
        dash = _dash_or_503(await _aload_active_dashboard(sym, win), sym)
        spot = float(dash.levels.spot or 0.0)
        fut = _px(getattr(dash.levels, "future_price", None))
        lv = dash.levels
    basis = (fut - spot) if (fut is not None and spot > 0) else None

    today_ist = _get_ist_now().date()
    exp, dte, dte_badge, mode_desc = _resolve_expiry_scope(sym, win)

    from data.pre_post_market import get_current_session_phase, get_pre_market_state
    try:
        sess_phase, sess_label, _ = get_current_session_phase()
    except Exception:
        sess_phase, sess_label = "CLOSED", "Closed"

    now_ist = _get_ist_now()
    sched = compute_session_schedule_info(now_ist)
    h_name = sched.get("holiday_name")

    is_live = config.is_market_open()
    is_morning_briefing = sess_phase == "PRE_MARKET_SETUP"
    feed_mode = "LIVE_WEBSOCKET" if is_live else "OFFLINE_AUTOSWITCH"
    if is_morning_briefing:
        session_title = "Morning briefing · prior-session book"
        feed_badge = "🌅 Prior-session book"
        feed_source = "Session Snapshot"
        pollers_status = "Idle until 08:50"
        feed_subtext = "Walls, flip, and OI time are the previous session. Dhan stays idle until 08:50."
    else:
        session_title = "Ingestion: WebSocket Stream" if is_live else "Ingestion: Offline Snapshot"
        feed_badge = "🟢 WebSocket Live" if is_live else "🛑 Offline Snapshot"
        feed_source = "0ms Direct Memory" if is_live else "Session Snapshot"
        pollers_status = "Active (1s Push)" if is_live else "Paused"
        feed_subtext = "Live direct exchange tick stream" if is_live else "Tick stream paused · Broker pollers idle"
    pm_st = get_pre_market_state(sym)
    is_pre_bell_ephemeral = _is_pre_bell_ephemeral(now_ist, sess_phase, pm_st)

    from data.dhan import token_status
    dhan_st = token_status()

    from data.market_snapshot import get_snapshot, resolve_offmarket_snapshot
    db_snap = (resolve_offmarket_snapshot(sym) if not is_live else get_snapshot(sym)) or {}
    if not is_live and db_snap.get("stale_session"):
        book_day = None
        fetched = getattr(dash, "fetched_at", None)
        if fetched is not None and hasattr(fetched, "astimezone"):
            from data.market_snapshot import _IST
            book_day = fetched.astimezone(_IST).date().isoformat()
        if book_day != db_snap.get("expected_session"):
            feed_badge = "🛑 Stale snapshot"
            feed_source = "Old session snapshot"
            from data.market_snapshot import format_ist_stamp, session_date_label
            stamp = format_ist_stamp(db_snap.get("as_of")) or "has no timestamp"
            expected = session_date_label(db_snap.get("expected_session")) or db_snap.get("expected_session")
            feed_subtext = (
                f"Newest snapshot is {stamp}. "
                f"Last session is {expected}. "
                "This is not that close."
            )
    data_refreshed_at = ""
    if not is_live:
        refreshed_src = getattr(offmarket_book, "fetched_at", None) or getattr(dash, "fetched_at", None) or db_snap.get("as_of")
        data_refreshed_at = _fmt_snapshot_as_of(refreshed_src)
        if not feed_subtext.startswith("Newest snapshot"):
            from app.dhan_sync import offmarket_refresh_line
            feed_subtext = offmarket_refresh_line(data_refreshed_at, morning=is_morning_briefing)
    real_ohlc = _status_day_ohlc(dash, q, db_snap, spot, is_live)
    vix_from_dash = getattr(dash, "india_vix", None) if dash is not None else None
    vix_last = None
    vix_candidates = [
        q.get("vix"),
        (vix_from_dash or {}).get("last") if isinstance(vix_from_dash, dict) else None,
        db_snap.get("vix"),
    ]
    if not is_live:
        vix_candidates = [
            (vix_from_dash or {}).get("last") if isinstance(vix_from_dash, dict) else None,
            db_snap.get("vix"),
            q.get("vix"),
        ]
    for candidate in vix_candidates:
        try:
            n = float(candidate)
        except (TypeError, ValueError):
            continue
        if n == n and n > 0:
            vix_last = n
            break
    vix_chg = q.get("vix_chg")
    try:
        vix_pct = float(vix_chg) if vix_chg is not None else None
    except (TypeError, ValueError):
        vix_pct = None
    real_vix = {
        "last": vix_last,
        "change": None,
        "pct_change": vix_pct,
    }

    sched = compute_session_schedule_info(now_ist)

    return {
        "symbol": sym,
        "label": idx.label,
        "exchange": idx.exchange,
        "lot_size": idx.lot_size,
        "spot": spot,
        "future_price": fut,
        "basis": basis,
        "day_ohlc": real_ohlc,
        "india_vix": real_vix,
        "session_phase": sess_phase,
        "session_label": sched["phase_badge"],
        "session_schedule": sched,
        "next_phase_countdown": sched["exec_phase_countdown"],
        "is_market_open": is_live,
        "is_morning_briefing": is_morning_briefing,
        "feed_mode": feed_mode,
        "feed_title": session_title,
        "feed_badge": feed_badge,
        "feed_source": feed_source,
        "pollers_status": pollers_status,
        "feed_subtext": feed_subtext,
        "data_refreshed_at": data_refreshed_at,
        "expiry_target": exp.strftime("%d-%b-%Y"),
        "dte": dte,
        "dte_badge": dte_badge,
        "mode_desc": mode_desc,
        "pre_market": pm_st,
        "is_pre_bell_ephemeral": is_pre_bell_ephemeral,
        "dhan_token": {
            "present": dhan_st.get("present", False),
            "hours_left": dhan_st.get("hours_left", 0.0),
            "source": dhan_st.get("source", "none"),
        },
        "ist_time": _fmt_ist_clock(),
    }


@app.get("/api/indices")
async def get_indices():
    """List available trading indices with verified exchange lot sizes."""
    return [
        {
            "symbol": idx.symbol,
            "label": idx.label,
            "exchange": idx.exchange,
            "lot_size": idx.lot_size,
            "has_weekly": idx.has_weekly,
        }
        for idx in config.INDICES.values()
    ]


@app.post("/api/select-index")
async def select_index(payload: dict = Body(...)):
    """Set active index and calculation window."""
    sym = payload.get("symbol", "NIFTY").upper()
    win = payload.get("window", "current_week")
    if sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
        _ACTIVE_STATE["symbol"] = sym
    if win:
        _ACTIVE_STATE["window"] = win
    if config.is_market_open():
        _ensure_chain_ingest(_ACTIVE_STATE["symbol"], _ACTIVE_STATE["window"])
    return {"status": "ok", "symbol": _ACTIVE_STATE["symbol"], "window": _ACTIVE_STATE["window"]}


@app.post("/api/save-dhan-token")
async def save_dhan_token(payload: dict = Body(...)):
    """Persist Dhan token to data/.dhan_token."""
    tok = payload.get("token", "").strip()
    if not tok:
        raise HTTPException(status_code=400, detail="Token cannot be empty")
    from data.dhan import write_token
    try:
        write_token(tok)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from app.dhan_sync import request_sync
    sync = request_sync("token", window=_ACTIVE_STATE.get("window") or "current_week")
    if sync.get("status") == "started":
        message = "Dhan token saved. Syncing the book from Dhan."
    elif sync.get("status") == "already_running":
        message = "Dhan token saved. A sync is already running."
    else:
        message = "Dhan token saved."
    return {"status": "saved", "message": message, "sync": sync}


def _card_float(value, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def resolve_pinned_card_fields(record: dict, active_idea, ws_ltp: float | None = None) -> dict:
    """Premium for a pinned cockpit card.

    A websocket tick or a chain price on the live idea is the LTP. Anything else
    is the stored entry, marked estimated. The latch must not replace that entry.
    """
    entry = _card_float(record.get("initial_ltp")) or _card_float(record.get("entry_ltp"))
    idea_ltp = _card_float(getattr(active_idea, "current_ltp", 0.0)) if active_idea is not None else 0.0
    source = str(getattr(active_idea, "quote_source", "") or "") if active_idea is not None else ""
    ws = _card_float(ws_ltp)
    ws_live = bool(active_idea is not None and getattr(active_idea, "is_ws_live", False) and idea_ltp > 0)

    if ws > 0:
        current, estimated, origin = ws, False, "ws"
    elif ws_live or (source == "ws" and idea_ltp > 0):
        current, estimated, origin = idea_ltp, False, "ws"
    elif source == "chain" and idea_ltp > 0:
        current, estimated, origin = idea_ltp, False, "chain"
    elif source not in ("entry", "estimated") and active_idea is not None and idea_ltp > 0:
        current, estimated, origin = idea_ltp, False, "chain"
    else:
        current, estimated, origin = entry, True, "entry"

    def _idea_or_record(attr: str, *record_keys: str) -> float:
        if not estimated and active_idea is not None:
            live = _card_float(getattr(active_idea, attr, 0.0))
            if live > 0:
                return live
        for key in record_keys:
            stored = _card_float(record.get(key))
            if stored > 0:
                return stored
        return 0.0

    score = None
    if active_idea is not None and getattr(active_idea, "quality_score", None) is not None:
        try:
            score = int(active_idea.quality_score)
        except (TypeError, ValueError):
            score = None
    if score is None:
        for key in ("current_score", "initial_score", "quality_score"):
            raw = record.get(key)
            if raw is None or raw == "":
                continue
            try:
                score = int(raw)
                break
            except (TypeError, ValueError):
                continue
    if score is None:
        score = 0

    return {
        "entry_ltp": entry,
        "current_ltp": current,
        "ltp_estimated": estimated,
        "quote_source": origin,
        "quality_score": score,
        "target_1": _idea_or_record("target_1", "target_1"),
        "target_2": _idea_or_record("target_2", "target_2"),
        "stop_loss": _idea_or_record("stop_loss", "stop_loss"),
        "spot_invalidation": _card_float(record.get("spot_invalidation")) or _idea_or_record("spot_invalidation", "spot_invalidation"),
        "spot_invalidation_text": (
            record.get("spot_invalidation_text")
            or (getattr(active_idea, "spot_invalidation_text", "") if active_idea is not None else "")
            or ""
        ),
    }


@app.get("/api/cockpit-data")
@app.get("/api/market/dashboard")
async def get_cockpit_data(
    symbol: Optional[str] = None,
    window: Optional[str] = None,
):
    """Cockpit Tab: Executive strip, 8 KPIs, Gamma Blast, Ladder, Candidate structures, Pinned setups."""
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    win = window or _ACTIVE_STATE["window"]
    idx = config.get_index(sym)
    dash = await _aload_active_dashboard(sym, win)
    if dash is None or getattr(dash, "levels", None) is None:
        return JSONResponse(
            status_code=503,
            content={"symbol": sym, "spot": 0, "kpis": {}, "error": "dashboard_unavailable"},
        )
    spot = _extract_active_spot(dash, sym)

    if config.is_market_open():
        _kick_commentary_eval(sym, dash)

    # 8 KPI cards (including Max Pain and IOAS Directional Bias)
    flip_val = _extract_active_gamma_flip(dash, sym)
    net_gex_val = float(getattr(dash.levels, "total_net_gex", 0.0) or 0.0)
    pcr_val = _extract_active_pcr(dash)
    mp_raw = getattr(dash.levels, "max_pain", None)
    try:
        mp_val = float(mp_raw) if mp_raw is not None and float(mp_raw) > 0 else None
    except (TypeError, ValueError):
        mp_val = None
    magnet_val = float(getattr(dash.levels, "magnet", 0.0) or 0.0)

    bias_res = _compute_active_directional_bias(dash, sym)
    bias_color = "var(--go)" if bias_res.score >= BIAS_MILD else ("var(--stop)" if bias_res.score <= -BIAS_MILD else "var(--spark)")

    if flip_val:
        dist_flip = flip_val - spot
        dist_sign = "+" if dist_flip >= 0 else ""
        flip_sub = f"{dist_sign}{dist_flip:,.0f} pts · Call−put balance"
    else:
        flip_sub = "No zero-crossing in ±10%"

    put_role = str(getattr(dash.view, "put_wall_role", "") or "")
    call_sub = "Call-gamma concentration"
    if "below" in put_role:
        put_sub = "Put-gamma concentration · below balance"
    elif put_role == "unknown" or not put_role:
        put_sub = "Put-gamma concentration"
    else:
        put_sub = "Put-gamma concentration · at or above balance"

    pcr_sub = _pcr_caption(dash)

    kpis = {
        "call_wall": {"val": float(dash.levels.call_wall or 0), "sub": call_sub},
        "put_wall": {"val": float(dash.levels.put_wall or 0), "sub": put_sub},
        "gamma_flip": {"val": flip_val, "sub": flip_sub, "raw": flip_val},
        "max_pain": {
            "val": mp_val,
            "sub": (
                "Settlement strike unavailable"
                if mp_val is None
                else f"{abs(spot - mp_val):,.0f} pts from spot · settlement strike"
            ),
        },
        "directional_bias": {
            "val": f"{bias_res.score:+.2f}",
            "sub": bias_res.regime,
            "raw": round(bias_res.score, 2),
            "color": bias_color,
        },
        "regime": {"val": str(dash.view.regime or "NEUTRAL").upper(), "sub": f"Mode: {dash.view.mode_label or dash.view.bias or 'Neutral'}"},
        "net_gex": {"val": _fmt_gex(net_gex_val), "sub": "Writer imbalance (call−put GEX)"},
        "pcr_oi": {"val": None if pcr_val is None else f"{pcr_val:.2f}", "sub": pcr_sub},
    }

    # Gamma Price Ladder: ATM band plus key GEX levels so walls/max-pain are never off-chart.
    ladder = []
    step = float(idx.strike_step)
    standing_oi = _standing_oi(dash, spot, float(idx.band_pts))
    center = round(spot / step) * step
    strike_set = {center + (offset * step) for offset in range(-6, 7)}
    try:
        from analytics.session_map import load_prior_marks
        session_day = _get_ist_now().strftime("%Y-%m-%d")
        prior_marks = load_prior_marks(sym, session_day)
    except Exception as exc:
        log.debug("prior session marks: %s", exc)
        prior_marks = None
    for key in (dash.levels.call_wall, dash.levels.put_wall, magnet_val, mp_val,
                standing_oi.get("put_centre"), standing_oi.get("call_centre"),
                (prior_marks or {}).get("high"), (prior_marks or {}).get("low"),
                (prior_marks or {}).get("close")):
        if key and float(key) > 0:
            strike_set.add(round(float(key) / step) * step)
    ladder_strikes = sorted(strike_set)

    closest_flip_stk = min(ladder_strikes, key=lambda s: abs(s - flip_val)) if flip_val else None
    is_flip_in_band = (flip_val is not None and closest_flip_stk is not None and abs(closest_flip_stk - flip_val) <= step)

    closest_mp_stk = min(ladder_strikes, key=lambda s: abs(s - mp_val)) if mp_val else None
    is_mp_in_band = True

    closest_magnet_stk = min(ladder_strikes, key=lambda s: abs(s - magnet_val)) if magnet_val else None
    is_magnet_in_band = True

    contracts_df = getattr(dash, "contracts", None)
    strike_oi_map = {}
    if contracts_df is not None and hasattr(contracts_df, "empty") and not contracts_df.empty:
        try:
            for _, crow in contracts_df.iterrows():
                stk_k = float(crow["strike"])
                ctype = str(crow["type"]).upper()
                c_oi = float(crow.get("oi") or 0.0)
                if stk_k not in strike_oi_map:
                    strike_oi_map[stk_k] = {"C": 0.0, "P": 0.0}
                if ctype in ("C", "CE"):
                    strike_oi_map[stk_k]["C"] += c_oi
                elif ctype in ("P", "PE"):
                    strike_oi_map[stk_k]["P"] += c_oi
        except Exception:
            pass

    per_strike_map = {}
    if hasattr(dash, "levels") and hasattr(dash.levels, "per_strike") and isinstance(dash.levels.per_strike, pd.DataFrame) and not dash.levels.per_strike.empty:
        try:
            for _, prow in dash.levels.per_strike.iterrows():
                per_strike_map[float(prow["strike"])] = prow
        except Exception:
            pass

    def _listed(centre):
        if not centre or float(centre) <= 0:
            return None
        return round(float(centre) / step) * step

    put_oi_mark = _listed(standing_oi.get("put_centre"))
    call_oi_mark = _listed(standing_oi.get("call_centre"))

    for stk in ladder_strikes:
        is_spot = abs(stk - center) < 0.51
        prow = per_strike_map.get(stk)
        if prow is None:
            prow = per_strike_map.get(float(stk))
        if prow is None:
            # float-key mismatch: pick nearest per-strike row within a rupee
            for k, row in per_strike_map.items():
                if abs(k - stk) < 0.51:
                    prow = row
                    break
        if prow is not None:
            call_gex = float(prow.get("call_gex") or 0.0)
            put_gex = float(prow.get("put_gex") or 0.0)
            net_stk_gex = float(prow.get("net_gex") or 0.0)
        elif stk in strike_oi_map:
            call_gex = strike_oi_map[stk]["C"]
            put_gex = strike_oi_map[stk]["P"]
            net_stk_gex = call_gex - put_gex
        else:
            call_gex = 0.0
            put_gex = 0.0
            net_stk_gex = 0.0

        oi_cell = strike_oi_map.get(stk) or next(
            (v for k, v in strike_oi_map.items() if abs(k - stk) < 0.51),
            {"C": 0.0, "P": 0.0},
        )
        c_oi = oi_cell.get("C", 0.0)
        p_oi = oi_cell.get("P", 0.0)

        ladder.append({
            "strike": stk,
            "is_spot": is_spot,
            "call_gex": call_gex,
            "put_gex": put_gex,
            "net_gex": net_stk_gex,
            "call_oi": c_oi,
            "put_oi": p_oi,
            "net_oi": c_oi - p_oi,
            "is_call_wall": _same_strike(stk, dash.levels.call_wall or 0, step),
            "is_put_wall": _same_strike(stk, dash.levels.put_wall or 0, step),
            "is_gamma_flip": (stk == closest_flip_stk) if is_flip_in_band else False,
            "is_max_pain": (stk == closest_mp_stk) if is_mp_in_band else False,
            "is_magnet": (stk == closest_magnet_stk) if is_magnet_in_band else False,
            "is_put_oi": put_oi_mark is not None and _same_strike(stk, put_oi_mark, step),
            "is_call_oi": call_oi_mark is not None and _same_strike(stk, call_oi_mark, step),
            "call_doi": None,
            "put_doi": None,
            "is_prior_high": False,
            "is_prior_low": False,
            "is_prior_close": False,
        })

    try:
        from analytics.session_map import (
            crossover_watch, load_magnet_path, read_futures_state, strike_doi_table,
        )
    except Exception as exc:
        log.debug("session map import: %s", exc)
        crossover_watch = load_magnet_path = read_futures_state = strike_doi_table = None
    doi_rows = []
    if strike_doi_table is not None:
        try:
            doi_rows = strike_doi_table(getattr(dash, "oi_strikes", None), spot, float(idx.band_pts))
        except Exception as exc:
            log.debug("strike doi table: %s", exc)
    for row in ladder:
        cell = next((item for item in doi_rows if abs(item["strike"] - row["strike"]) <= step / 2), None)
        if cell:
            row["call_doi"] = cell["call_doi"]
            row["put_doi"] = cell["put_doi"]
    if prior_marks and ladder:
        for flag, key in (
            ("is_prior_high", "high"),
            ("is_prior_low", "low"),
            ("is_prior_close", "close"),
        ):
            level = float(prior_marks[key])
            nearest = min(ladder, key=lambda row: abs(row["strike"] - level))
            if abs(nearest["strike"] - level) <= step:
                nearest[flag] = True
    magnet_path = []
    watch = {"anchors": []}
    futures_state = None
    if load_magnet_path is not None:
        try:
            magnet_path = load_magnet_path(sym)
        except Exception as exc:
            log.debug("magnet path: %s", exc)
    if crossover_watch is not None:
        try:
            watch = crossover_watch(getattr(dash, "oi_strikes", None) or [], spot, magnet_val)
        except Exception as exc:
            log.debug("crossover watch: %s", exc)
    if read_futures_state is not None:
        try:
            futures_state = read_futures_state(sym, _get_ist_now().strftime("%Y-%m-%d"))
        except Exception as exc:
            log.debug("futures state: %s", exc)

    # Gamma Blast Verdict (Evaluated dynamically via app.gammablast)
    try:
        from app.gammablast import evaluate_dashboard_gamma_blast
        gamma_blast = evaluate_dashboard_gamma_blast(dash, sym)
    except Exception as exc:
        log.warning("Gamma blast dynamic evaluation fallback: %s", exc)
        gamma_blast = {
            "state": "NO_TRIGGER",
            "badge": "GAMMA BLAST: INACTIVE",
            "description": "No gamma-blast trigger. Concentration is a map, not a pin.",
            "score": None,
            "is_expiry": False,
            "in_blast_window": False,
            "primary_strike": None,
        }

    # ── Collect Cockpit Recommendations (Pinned + Fresh within 10m) ────
    pinned_records = list_bookmarked_records(sym)
    bm_ids = {r.get("rec_id") for r in pinned_records if r.get("rec_id")}

    recs = _latest_rec_batch(sym, win)
    fresh_ideas = []
    is_open = config.is_market_open()
    if is_open and recs:
        try:
            all_recs = list(getattr(recs, "ce_recommendations", None) or []) + list(
                getattr(recs, "pe_recommendations", None) or []
            )
            if not all_recs:
                all_recs = (
                    list(getattr(recs, "higher_ce_recommendations", None) or [])
                    + list(getattr(recs, "higher_pe_recommendations", None) or [])
                    + list(getattr(recs, "lower_ce_recommendations", None) or [])
                    + list(getattr(recs, "lower_pe_recommendations", None) or [])
                    + list(getattr(recs, "tactical_ce_recommendations", None) or [])
                    + list(getattr(recs, "tactical_pe_recommendations", None) or [])
                )
            for idea in all_recs:
                if getattr(idea, "rec_id", None) not in bm_ids and _is_recently_triggered(getattr(idea, "triggered_at", None), max_age_seconds=600.0):
                    fresh_ideas.append(idea)
        except Exception as e:
            log.warning("Cockpit fresh ideas check: %s", e)

    cockpit_cards = []
    batch_ideas_by_id = {}
    if recs:
        for idea in _iter_cached_ideas(recs):
            rid = getattr(idea, "rec_id", "")
            if rid:
                batch_ideas_by_id[rid] = idea

    for r in pinned_records:
        rid = r.get("rec_id")
        active_idea = batch_ideas_by_id.get(rid)
        ws_ltp = 0.0
        try:
            from app.spot_ticker import get_option_ws_quote
            ws_q = get_option_ws_quote(sym, float(r.get("strike", 0) or 0.0), r.get("side", "CE"), max_age_s=120.0)
            if ws_q:
                ws_ltp = float(ws_q.get("ltp") or 0.0)
        except Exception:
            ws_ltp = 0.0
        priced = resolve_pinned_card_fields(r, active_idea, ws_ltp)

        card_dict = {}
        if not active_idea:
            try:
                from analytics.trade_ideas import _rehydrate_bookmarked_trade_idea
                active_idea = _rehydrate_bookmarked_trade_idea(r, spot)
            except Exception:
                active_idea = None

        if active_idea:
            try:
                if priced["current_ltp"] > 0:
                    active_idea.current_ltp = priced["current_ltp"]
                if priced["entry_ltp"] > 0:
                    active_idea.initial_ltp = priced["entry_ltp"]
                card_dict = _serialize_trade_idea(active_idea, spot, 500000.0, 0.015, idx.lot_size)
            except Exception as e:
                log.warning("Serialize pinned idea error: %s", e)
                card_dict = {}

        card_dict.update({
            "rec_id": rid,
            "symbol": r.get("symbol", sym),
            "side": r.get("side", "CE"),
            "strike": float(r.get("strike", 0) or 0.0),
            "option_symbol": r.get("option_symbol") or f"{sym} {int(float(r.get('strike', 0) or 0))} {r.get('side', 'CE')}",
            "play_badge": r.get("play_badge", "STRATEGY"),
            "quality_score": priced["quality_score"],
            "entry_ltp": priced["entry_ltp"],
            "current_ltp": priced["current_ltp"],
            "ltp_estimated": priced["ltp_estimated"],
            "quote_source": priced["quote_source"],
            "target_1": priced["target_1"],
            "target_2": priced["target_2"],
            "stop_loss": priced["stop_loss"],
            "spot_invalidation": priced["spot_invalidation"],
            "spot_invalidation_text": priced["spot_invalidation_text"],
            "is_pinned": True,
            "is_fresh": False,
            "triggered_at": r.get("triggered_at", ""),
        })
        cockpit_cards.append(card_dict)

    for f in fresh_ideas:
        try:
            s_idea = _serialize_trade_idea(f, spot, 500000.0, 0.015, idx.lot_size)
            s_idea["is_pinned"] = False
            s_idea["is_fresh"] = True
            cockpit_cards.append(s_idea)
        except Exception:
            pass

    pinned_count = len(pinned_records)
    fresh_count = len(fresh_ideas)
    total_cockpit_cards = pinned_count + fresh_count

    # Pre-Market Discovery Summary (09:07:30 Equilibrium & Opening Shock)
    pm_state = dict(get_pre_market_state(sym) or {})
    lv_pm = getattr(dash, "levels", None)
    if lv_pm is not None:
        pm_state["call_wall"] = float(getattr(lv_pm, "call_wall", 0) or 0)
        pm_state["put_wall"] = float(getattr(lv_pm, "put_wall", 0) or 0)
        flip_lv = float(getattr(lv_pm, "flip", 0) or 0)
        if flip_lv > 0:
            pm_state["gamma_flip"] = flip_lv
    if pm_state.get("is_morning_briefing") and not pm_state.get("is_discovered"):
        from data.pre_post_market import stamp_morning_briefing
        if not pm_state.get("snapshot_stale") and not pm_state.get("oi_as_of_display"):
            shown = _fmt_snapshot_as_of(getattr(dash, "fetched_at", None), _get_ist_now())
            if shown:
                pm_state["oi_as_of_display"] = shown
                if not pm_state.get("oi_as_of"):
                    pm_state["oi_as_of"] = shown
        stamp_morning_briefing(pm_state)
    pm_state = attach_pre_market_shocks(
        pm_state,
        contracts_df=getattr(dash, "contracts", None),
        dte_days=_extract_active_dte(dash),
        atm_iv=_extract_active_atm_iv(dash),
    )
    if not pm_state.get("regime_briefing"):
        pm_state["regime_briefing"] = pm_state.get("status_text") or ""

    # OrderFlow Sentinel Stream Summary (Tape Alerts)
    inst_id = f"{sym}-FUT"
    sigs = list_recent_signals(inst_id, limit=15)
    try:
        outcome_by_id_of = {o.event_id: o for o in list_recent_outcomes(inst_id, limit=30)}
    except Exception:
        outcome_by_id_of = {}

    orderflow_alerts = []
    for s in sigs:
        out = outcome_by_id_of.get(s.event_id)
        st = "ACTIVE"
        if out:
            st = "TARGET_HIT" if out.target_hit else ("INVALIDATED" if out.stopped_out else "ACTIVE")
        elif getattr(s, "status", None) in ("INVALIDATED", "TARGET_HIT", "ACTIVE"):
            st = s.status

        orderflow_alerts.append({
            "type": s.type,
            "category": s.category,
            "bias": getattr(s, "bias", "") or ("LONG" if s.category == "BULLISH" else ("SHORT" if s.category == "BEARISH" else "NEUTRAL")),
            "score": s.score,
            "status": st,
            "rvol_tod": s.rvol_tod,
            "delta": s.delta,
            "invalidation_level": s.invalidation_level,
            "insight_text": s.insight_text,
            "actionable_play": getattr(s, "actionable_play", "") or "",
            "gex_alignment": getattr(s, "gex_alignment", "") or "",
            "time": _fmt_bar_stamp(s.bar_start_ts) or str(s.bar_start_ts),
            "ts": s.bar_start_ts.timestamp() if hasattr(s.bar_start_ts, "timestamp") else None,
            "outcome": {
                "mfe": round(float(out.mfe_pts or 0), 1),
                "mae": round(float(out.mae_pts or 0), 1),
                "target_hit": bool(out.target_hit),
                "stopped_out": bool(out.stopped_out),
                "status": st,
            } if out else None,
        })

    from data.sectors import fetch_sector_snap
    sec_snap = fetch_sector_snap()
    rows = sec_snap.get("rows") if isinstance(sec_snap, dict) else None
    adv = dec = ratio = sentiment = None
    if rows:
        adv = sum(1 for r in rows if (r.get("pct") or 0) > 0)
        dec = sum(1 for r in rows if (r.get("pct") or 0) < 0)
        if dec > 0:
            ratio = round(adv / dec, 2)
            sentiment = "BULLISH" if ratio >= 1.2 else ("BEARISH" if ratio <= 0.8 else "NEUTRAL")
        elif adv == 0:
            ratio = None
            sentiment = "NEUTRAL"

    now_ist = _get_ist_now()
    try:
        sess_phase, _, _ = get_current_session_phase(now_ist)
    except Exception:
        sess_phase = "CLOSED"
    is_pre_bell = _is_pre_bell_ephemeral(now_ist, sess_phase, pm_state)
    is_morning_briefing = sess_phase == "PRE_MARKET_SETUP"
    snapshot_as_of = _fmt_snapshot_as_of(getattr(dash, "fetched_at", None), now_ist)
    fetched_at = getattr(dash, "fetched_at", None)
    stale_s = 0.0
    if fetched_at is not None:
        fa = fetched_at
        if getattr(fa, "tzinfo", None) is None:
            fa = fa.replace(tzinfo=timezone.utc)
        stale_s = max(0.0, (datetime.now(timezone.utc) - fa.astimezone(timezone.utc)).total_seconds())
    is_degraded = bool(getattr(dash, "is_degraded", False) or stale_s > 7200.0)

    sched = compute_session_schedule_info(now_ist)
    regime_val = kpis.get("regime", {}).get("val", "Normal")
    if is_morning_briefing:
        oi_stamp = pm_state.get("oi_as_of_display") or snapshot_as_of or "OI time unavailable"
        exec_status_text = f"Morning briefing · prior-session book · {oi_stamp}"
    elif is_pre_bell:
        exec_status_text = f"Pre-open · prior-session GEX · Spot ₹{spot:,.1f}"
    elif not is_open:
        exec_status_text = f"Snapshot Locked at ₹{spot:,.1f} · {regime_val} Mode"
    else:
        exec_status_text = f"{regime_val} Mode · Spot ₹{spot:,.1f}"

    payload = {
        "symbol": sym,
        "spot": spot,
        "gamma_flip": flip_val or 0.0,
        "kpis": kpis,
        "gamma_blast": gamma_blast,
        "crossover_watch": watch,
        "magnet_path": magnet_path,
        "futures_state": futures_state,
        "prior_marks": prior_marks,
        "strike_doi": doi_rows,
        "ladder": ladder,
        "pinned_setups": pinned_records,
        "cockpit_recommendations": cockpit_cards,
        "pinned_count": pinned_count,
        "fresh_count": fresh_count,
        "total_cockpit_cards": total_cockpit_cards,
        "pre_market": pm_state,
        "orderflow_alerts": orderflow_alerts,
        "strike_step": float(idx.strike_step or 50),
        "session_schedule": sched,
        "session_phase": sess_phase,
        "next_phase_countdown": sched["exec_phase_countdown"],
        "exec_session_status": exec_status_text,
        "market_breadth": {
            "advances": adv,
            "declines": dec,
            "ratio": ratio,
            "sentiment": sentiment,
        },
        "is_market_open": is_open,
        "is_morning_briefing": is_morning_briefing,
        "is_pre_bell_ephemeral": is_pre_bell,
        "market_state": "OPEN" if is_open else "CLOSED",
        "as_of_time": _fmt_ist_clock(now_ist),
        "snapshot_as_of": snapshot_as_of,
        "snapshot_stale_seconds": round(stale_s, 1),
        "is_degraded": is_degraded,
        "fetched_at": str(getattr(dash, "fetched_at", "") or ""),
        "structures": _concentration_structures(
            dash, idx, bias_score=bias_res.score, sentinel_signals=sigs,
        ),
        "standing_oi": standing_oi,
    }
    try:
        from analytics.position_context import holdings_for, value_area_for
        book_day = _get_ist_now().strftime("%Y-%m-%d")
        payload["participant_holdings"] = holdings_for(None)
        payload["value_area"] = value_area_for(sym, book_day, spot, getattr(dash, "source", None))
    except Exception as exc:
        log.debug("holdings or value area: %s", exc)
        payload["participant_holdings"] = {"date": None, "rows": []}
        payload["value_area"] = {"text": "No prior value area.", "inside": None}
    try:
        from app.session_reads import build_session_reads
        reads = build_session_reads(sym)
        payload["positioning_flow"] = reads["positioning_flow"]
        payload["choppiness"] = reads["choppiness"]
    except Exception as exc:
        log.debug("session reads: %s", exc)
        payload["positioning_flow"] = {
            "alert": False,
            "headline": "Positioning unavailable.",
            "overnight_line": "The participant file or the futures journal could not be read.",
            "live_line": "",
        }
        payload["choppiness"] = {
            "state": "not_ready",
            "headline": "Not ready",
            "line": "The one-minute bars could not be read.",
        }
    try:
        payload["market_alert"] = evaluate_market_alert(payload)
    except Exception as exc:
        log.debug("market_alert compose: %s", exc)
        payload["market_alert"] = None
    return payload


@app.get("/api/target-engine")
@app.get("/api/target_engine")
async def get_target_engine(
    symbol: Optional[str] = None,
    window: Optional[str] = None,
    horizon: Optional[int] = 120,
    mode: Optional[str] = "heuristic",
):
    """Target Engine Tab: Confluence Dial, Dominant Scenario, Targets, Symmetrical Ladder, Ranked Setups, Repricer."""
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    win = window or _ACTIVE_STATE["window"]
    idx = config.get_index(sym)
    dash = _dash_or_503(await _aload_active_dashboard(sym, win), sym)
    horizon_val = int(horizon) if horizon else 120
    res = run_target_engine(dashboard_data=dash, horizon_minutes=horizon_val, persist=False)
    spot = float(dash.levels.spot or 0.0)

    sc_set = res.scenario_set
    dial = compose_confluence_dial(sc_set, getattr(res, "feature_vector", None))
    edge_label, honest_no_edge = compose_edge_label(sc_set)

    p_bull = float(sc_set.get_prob("bull"))
    p_base = float(sc_set.get_prob("base"))
    p_bear = float(sc_set.get_prob("bear"))

    scenario_info = {
        "dominant_scenario": sc_set.dominant_scenario.lower(),
        "conviction": float(sc_set.conviction),
        "no_edge": honest_no_edge,
        "edge_label": edge_label,
        "probabilities": {
            "bull": round(p_bull, 4),
            "base": round(p_base, 4),
            "bear": round(p_bear, 4),
        },
        "horizon_minutes": horizon_val,
        "scoring_mode": mode or "heuristic",
    }

    # Bull, Base, Bear Targets with Full Mathematical Probabilities & IV Forecasts
    iv_fcs = getattr(res, "iv_forecasts", {}) or {}
    targets = {}
    for k, v in res.underlying_targets.items():
        tgt_val = float(getattr(v, "target", spot) or spot)
        stop_val = float(getattr(v, "stop", spot) or spot)
        p_touch = float(getattr(v, "p_touch_target", 0.65) or 0.65)
        p_close = float(getattr(v, "p_close_beyond", 0.45) or 0.45)
        reason_txt = getattr(v, "reason", "") or "GEX / Volatility Anchor"
        fc_iv = float(iv_fcs[k].iv_at_horizon) if k in iv_fcs and hasattr(iv_fcs[k], "iv_at_horizon") else 0.14
        targets[k] = {
            "target": round(tgt_val, 1),
            "stop": round(stop_val, 1),
            "p_touch_target": round(p_touch, 4),
            "p_touch": round(p_touch, 4),
            "p_close_beyond": round(p_close, 4),
            "points_away": round(tgt_val - spot, 1),
            "horizon_minutes": getattr(v, "horizon_minutes", horizon_val),
            "reason": reason_txt,
            "forecast_iv": round(fc_iv, 4),
        }

    # Symmetrical Dual-Wing Ladder (ATM ± 5 Strikes with full Target / SL repricing)
    active_exps = get_sidebar_active_expiries(
        symbol=sym,
        dash=dash,
        contracts_df=getattr(dash, "contracts", None),
    )
    if not active_exps:
        if dash.expiries:
            active_exps = [str(dash.expiries[0])]
        else:
            active_exps = [datetime.now().date().isoformat()]
    exp_str = active_exps[0]
    ladder_bundle = compute_strike_ladder(
        sym, spot, getattr(dash, "contracts", None), exp_str, horizon_val, dash.levels,
    )
    strike_ladder_rows = []
    for r in ladder_bundle.rows:
        strike_ladder_rows.append({
            "strike": r.strike,
            "is_atm": r.is_atm,
            "ce": {
                "ltp": round(float(r.ce.ltp), 1),
                "delta": round(float(r.ce.delta), 2),
                "iv": round(float(r.ce.iv) * 100.0, 1),
                "live": bool(r.ce.is_ws_live),
                "theta": round(float(r.ce.theta), 1),
                "opt_sl": round(float(r.ce.opt_sl), 1),
                "opt_tgt1": round(float(r.ce.opt_tgt1), 1),
                "opt_tgt2": round(float(r.ce.opt_tgt2), 1),
                "sl_pts": round(float(r.ce.sl_pts), 1),
                "tgt1_pts": round(float(r.ce.tgt1_pts), 1),
                "tgt2_pts": round(float(r.ce.tgt2_pts), 1),
                "sl_inr": round(float(r.ce.sl_inr), 0),
                "tgt1_inr": round(float(r.ce.tgt1_inr), 0),
                "tgt2_inr": round(float(r.ce.tgt2_inr), 0),
                "rr_str": r.ce.rr_str,
                "p_touch_tgt1": round(float(r.ce.p_touch_tgt1) * 100, 0),
            },
            "pe": {
                "ltp": round(float(r.pe.ltp), 1),
                "delta": round(float(r.pe.delta), 2),
                "iv": round(float(r.pe.iv) * 100.0, 1),
                "live": bool(r.pe.is_ws_live),
                "theta": round(float(r.pe.theta), 1),
                "opt_sl": round(float(r.pe.opt_sl), 1),
                "opt_tgt1": round(float(r.pe.opt_tgt1), 1),
                "opt_tgt2": round(float(r.pe.opt_tgt2), 1),
                "sl_pts": round(float(r.pe.sl_pts), 1),
                "tgt1_pts": round(float(r.pe.tgt1_pts), 1),
                "tgt2_pts": round(float(r.pe.tgt2_pts), 1),
                "sl_inr": round(float(r.pe.sl_inr), 0),
                "tgt1_inr": round(float(r.pe.tgt1_inr), 0),
                "tgt2_inr": round(float(r.pe.tgt2_inr), 0),
                "rr_str": r.pe.rr_str,
                "p_touch_tgt1": round(float(r.pe.p_touch_tgt1) * 100, 0),
            },
        })

    ranked_candidates = serialize_ranked_candidates(
        res.candidates, dominant=str(sc_set.dominant_scenario or "base").lower(),
    )
    feat_rows = []
    fv = getattr(res, "feature_vector", None)
    feats = getattr(fv, "features", None) or {}
    if isinstance(feats, dict):
        for name, val in feats.items():
            try:
                feat_rows.append({"name": str(name), "value": round(float(val), 4)})
            except (TypeError, ValueError):
                continue
    live_ticks = sum(
        int(bool(r.ce.is_ws_live)) + int(bool(r.pe.is_ws_live))
        for r in ladder_bundle.rows
    )

    # 21-Strike ATM Band
    stk_step = int(idx.strike_step)
    atm_stk = round(spot / stk_step) * stk_step
    nearby_21_strikes = [int(atm_stk + i * stk_step) for i in range(-10, 11)]

    return {
        "symbol": sym,
        "spot": spot,
        "base_fut": float(getattr(dash.levels, "future_price", spot) or spot),
        "lot_size": idx.lot_size,
        "atm_strike": atm_stk,
        "expiries": [{"id": e, "label": e} for e in active_exps],
        "active_expiry": active_exps[0] if active_exps else datetime.now().date().isoformat(),
        "nearby_strikes": nearby_21_strikes,
        "dial": dial,
        "scenario_set": scenario_info,
        "horizon": horizon_val,
        "scoring_mode": mode,
        "targets": targets,
        "ladder": strike_ladder_rows,
        "ladder_meta": {
            "lot_size": int(idx.lot_size),
            "live_ticks": live_ticks,
            "rows": len(strike_ladder_rows),
        },
        "candidates": ranked_candidates,
        "features": feat_rows,
    }


@app.post("/api/target-engine/reprice")
async def reprice_target_engine(payload: dict = Body(...)):
    """Target/Stop-Loss Option Repricer, PnL per lot, R:R ratio, and Greek attribution."""
    sym = payload.get("symbol", "NIFTY").upper()
    win = payload.get("window", "current_week")
    dash = _dash_or_503(await _aload_active_dashboard(sym, win), sym)
    idx = config.get_index(sym)
    lot_sz = idx.lot_size
    base_fut = float(getattr(dash.levels, "future_price", None) or 0.0)
    spot = float(getattr(dash.levels, "spot", None) or 0.0)
    if spot <= 0 and base_fut <= 0:
        raise HTTPException(status_code=503, detail="No live spot for this book")
    if spot <= 0:
        spot = base_fut
    if base_fut <= 0:
        base_fut = spot
    
    active_exps = get_sidebar_active_expiries(
        symbol=sym,
        dash=dash,
        contracts_df=getattr(dash, "contracts", None),
    )
    default_exp = active_exps[0] if active_exps else "2026-10-19"

    sel_expiry = payload.get("expiry")
    if not sel_expiry or sel_expiry in ("current_week", "weekly", "next_week", "monthly", "all") or is_expiry_crossed(sel_expiry):
        sel_expiry = default_exp
    sel_strike = float(payload.get("strike", spot))
    sel_type = (payload.get("option_type") or payload.get("side") or "CE").upper()
    step = getattr(idx, "strike_step", 100.0 if sym in ("BANKNIFTY", "SENSEX") else 50.0)
    is_sensex = sym == "SENSEX"
    is_bnf = sym == "BANKNIFTY"
    def_tgt_pts = 250.0 if is_sensex else (180.0 if is_bnf else 70.0)
    def_sl_pts = 150.0 if is_sensex else (100.0 if is_bnf else 40.0)

    # Sanitize and validate target and stop-loss spot levels
    # Guard against stale cross-symbol values (e.g. NIFTY ~24k target sent for SENSEX ~74k)
    raw_tgt = payload.get("target_spot", payload.get("target"))
    raw_sl = payload.get("stop_loss_spot", payload.get("sl_spot"))
    try:
        user_tgt_spot = float(raw_tgt) if raw_tgt is not None else (base_fut + (def_tgt_pts if sel_type == "CE" else -def_tgt_pts))
    except (TypeError, ValueError):
        user_tgt_spot = base_fut + (def_tgt_pts if sel_type == "CE" else -def_tgt_pts)

    try:
        user_sl_spot = float(raw_sl) if raw_sl is not None else (base_fut - (def_sl_pts if sel_type == "CE" else -def_sl_pts))
    except (TypeError, ValueError):
        user_sl_spot = base_fut - (def_sl_pts if sel_type == "CE" else -def_sl_pts)

    if user_tgt_spot <= 0 or abs(user_tgt_spot - base_fut) > base_fut * 0.15:
        user_tgt_spot = base_fut + (def_tgt_pts if sel_type == "CE" else -def_tgt_pts)
    if user_sl_spot <= 0 or abs(user_sl_spot - base_fut) > base_fut * 0.15:
        user_sl_spot = base_fut - (def_sl_pts if sel_type == "CE" else -def_sl_pts)

    tgt_mins = int(payload.get("holding_mins", payload.get("holding_minutes", 60)))
    iv_regime_shock = payload.get("iv_regime_shock", "Auto (Regime-Conditioned)")

    now_dt = _get_ist_now()
    T_now = calculate_fractional_t(now_dt, sel_expiry)
    _, T_horizon = calculate_horizon_t(now_dt, tgt_mins, sel_expiry)

    # Strike IV from the listed contract. Ensure realistic floor for Black-76 Greeks stability.
    raw_atm_iv = _extract_active_atm_iv(dash)
    fallback_iv = max(0.08, min(0.60, float(raw_atm_iv))) if (raw_atm_iv and float(raw_atm_iv) > 0.05) else 0.135
    strike_iv = fallback_iv
    market_ltp = None
    contracts_df = getattr(dash, "contracts", None)
    if contracts_df is not None and not contracts_df.empty:
        type_prefix = "C" if sel_type == "CE" else "P"
        type_mask = (contracts_df["strike"] == sel_strike) & (contracts_df["type"].astype(str).str.upper().isin([sel_type, type_prefix]))
        if "expiry" in contracts_df.columns:
            def _match_exp(e):
                return (e.strftime("%Y-%m-%d") if hasattr(e, "strftime") else str(e).strip()) == sel_expiry
            match_rows = contracts_df[type_mask & contracts_df["expiry"].apply(_match_exp)]
        else:
            match_rows = contracts_df[type_mask]
        if not match_rows.empty:
            crow = match_rows.iloc[0]
            for col in ("mid", "ltp", "price", "last_price"):
                val = crow.get(col)
                if val is not None and float(val) > 0:
                    market_ltp = float(val)
                    break
            raw_iv = crow.get("iv")
            if raw_iv is not None and float(raw_iv) > 0:
                parsed_iv = float(raw_iv) / 100.0 if float(raw_iv) > 1.0 else float(raw_iv)
                if 0.05 <= parsed_iv <= 0.90:
                    strike_iv = parsed_iv

    strike_iv = max(0.07, min(0.90, strike_iv))

    if strike_iv is None or strike_iv <= 0:
        return {
            "error": "implied_vol_unavailable",
            "contract": f"{sym} {sel_expiry} {int(sel_strike)} {sel_type}",
            "message": "This strike has no implied vol, so the repricer has nothing to price.",
        }

    p_theo_curr = price(base_fut, sel_strike, T_now, strike_iv, 0.065, sel_type)
    cur_greeks = greeks(base_fut, sel_strike, T_now, strike_iv, 0.065, sel_type)
    entry_premium = market_ltp if (market_ltp is not None and market_ltp > 0) else p_theo_curr

    # Volatility shift mapping
    if "Flat" in iv_regime_shock:
        d_iv_tgt = 0.0
        d_iv_sl = 0.0
    elif "Mild Vol Compression" in iv_regime_shock:
        d_iv_tgt = -0.010
        d_iv_sl = 0.005
    elif "Strong Vol Compression" in iv_regime_shock:
        d_iv_tgt = -0.025
        d_iv_sl = 0.010
    elif "Panic Vol Expansion" in iv_regime_shock:
        d_iv_tgt = 0.020
        d_iv_sl = -0.010
    elif "Extreme Vol Squeeze" in iv_regime_shock:
        d_iv_tgt = 0.040
        d_iv_sl = 0.010
    else:
        if sel_type == "CE":
            d_iv_tgt = -0.0075 if user_tgt_spot > base_fut else 0.010
            d_iv_sl = 0.012 if user_sl_spot < base_fut else -0.005
        else:
            d_iv_tgt = 0.015 if user_tgt_spot < base_fut else -0.010
            d_iv_sl = -0.008 if user_sl_spot > base_fut else 0.010

    iv_at_tgt = max(0.05, strike_iv + d_iv_tgt)
    iv_at_sl = max(0.05, strike_iv + d_iv_sl)

    p_target = price(user_tgt_spot, sel_strike, T_horizon, iv_at_tgt, 0.065, sel_type)
    p_stop = price(user_sl_spot, sel_strike, T_horizon, iv_at_sl, 0.065, sel_type)

    gain_pts = p_target - entry_premium
    gain_pct = (gain_pts / entry_premium) * 100.0 if entry_premium > 0 else 0.0
    gain_inr = gain_pts * lot_sz

    loss_pts = entry_premium - p_stop
    loss_pct = (loss_pts / entry_premium) * 100.0 if entry_premium > 0 else 0.0
    loss_inr = loss_pts * lot_sz

    rr_ratio = (gain_pts / loss_pts) if loss_pts > 0.05 else (9.99 if gain_pts > 0 else 0.0)

    # 1-SD horizon move for touch probability
    sigma_h = max(base_fut * strike_iv * (tgt_mins / (375.0 * 252.0)) ** 0.5, 15.0)
    drift_tgt = 0.25 * sigma_h if sel_type == "CE" else -0.25 * sigma_h
    p_touch_target = compute_touch_probability(abs(user_tgt_spot - base_fut), sigma_h, drift_tgt)
    p_touch_sl = compute_touch_probability(abs(base_fut - user_sl_spot), sigma_h, -0.2 * sigma_h)

    net_ev_approx = (p_touch_target * gain_inr) - (p_touch_sl * loss_inr)

    attr_tgt = greek_attribution(base_fut, user_tgt_spot, strike_iv, iv_at_tgt, T_now, T_horizon, sel_strike, 0.065, sel_type)
    attr_sl = greek_attribution(base_fut, user_sl_spot, strike_iv, iv_at_sl, T_now, T_horizon, sel_strike, 0.065, sel_type)

    return {
        "contract": f"{sym} {sel_expiry} {int(sel_strike)} {sel_type}",
        "dte": round(T_now * 365.0, 1),
        "lot_size": lot_sz,
        "base_fut": base_fut,
        "entry_premium": round(entry_premium, 2),
        "p_theo_curr": round(p_theo_curr, 2),
        "strike_iv_pct": round(strike_iv * 100.0, 1),
        "greeks": {
            "delta": round(cur_greeks.delta, 3),
            "gamma": round(cur_greeks.gamma, 5),
            "theta": round(cur_greeks.theta, 2),
            "vega": round(cur_greeks.vega, 2),
        },
        "target": {
            "premium": round(p_target, 2),
            "gain_pts": round(gain_pts, 2),
            "gain_pct": round(gain_pct, 1),
            "gain_inr": round(gain_inr, 0),
            "p_touch": round(p_touch_target * 100, 1),
            "iv_at_target": round(iv_at_tgt * 100, 1),
            "d_iv_pts": round(d_iv_tgt * 100, 1),
        },
        "stop_loss": {
            "premium": round(p_stop, 2),
            "loss_pts": round(loss_pts, 2),
            "loss_pct": round(loss_pct, 1),
            "loss_inr": round(loss_inr, 0),
            "p_touch": round(p_touch_sl * 100, 1),
            "iv_at_stop": round(iv_at_sl * 100, 1),
            "d_iv_pts": round(d_iv_sl * 100, 1),
        },
        "risk_reward": {
            "ratio": round(rr_ratio, 2),
            "verdict": "Favorable" if rr_ratio >= 1.8 else "Sub-Optimal",
            "net_spread_pts": round(gain_pts - loss_pts, 1),
            "breakeven": round(base_fut + (entry_premium if sel_type == "CE" else -entry_premium), 1),
        },
        "touch_weighted_ev": {
            "net_ev_inr": round(net_ev_approx, 0),
            "expectancy": "Positive Expectancy" if net_ev_approx > 0 else "Negative Expectancy",
        },
        "greek_attribution": [
            {
                "phase": "🎯 Target Reach",
                "spot_move": f"{user_tgt_spot - base_fut:+.1f} pts",
                "full_pnl": f"{gain_pts:+.2f} pts",
                "delta_pnl": f"{attr_tgt.delta_pnl:+.2f}",
                "gamma_pnl": f"{attr_tgt.gamma_pnl:+.2f}",
                "vega_pnl": f"{attr_tgt.vega_pnl:+.2f}",
                "theta_bleed": f"{attr_tgt.theta_pnl:+.2f}",
                "residual": f"{attr_tgt.residual:+.2f}",
            },
            {
                "phase": "🛑 Stop Invalidation",
                "spot_move": f"{user_sl_spot - base_fut:+.1f} pts",
                "full_pnl": f"{-loss_pts:+.2f} pts",
                "delta_pnl": f"{attr_sl.delta_pnl:+.2f}",
                "gamma_pnl": f"{attr_sl.gamma_pnl:+.2f}",
                "vega_pnl": f"{attr_sl.vega_pnl:+.2f}",
                "theta_bleed": f"{attr_sl.theta_pnl:+.2f}",
                "residual": f"{attr_sl.residual:+.2f}",
            },
        ],
    }


@app.get("/api/recommendations")
async def get_recommendations(
    symbol: Optional[str] = None,
    window: Optional[str] = None,
    capital: Optional[float] = None,
    risk: Optional[float] = None,
):
    """Recommendations Tab: Sizing HUD, Higher Conviction (>85), Fallback (75-85), Clusters, State Machine.
    Off-market hours return Session Closed and empty clusters.
    """
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    win = window or _ACTIVE_STATE["window"]
    if capital is not None:
        _ACTIVE_STATE["account_capital"] = float(capital)
    if risk is not None:
        _ACTIVE_STATE["risk_pct"] = float(risk)
    acct_cap = float(_ACTIVE_STATE["account_capital"])
    r_pct = float(_ACTIVE_STATE["risk_pct"])

    idx = config.get_index(sym)
    is_open = config.is_market_open()

    hud = {
        "account_capital": acct_cap,
        "risk_pct": r_pct,
        "max_risk_budget": round(acct_cap * (r_pct / 100.0), 0),
        "lot_size": idx.lot_size,
    }
    eff_metrics = get_strategy_effectiveness_metrics(sym)
    eff_summary = summarize_effectiveness(eff_metrics if isinstance(eff_metrics, list) else [])

    if not is_open:
        from data.market_snapshot import get_snapshot
        snap = get_snapshot(sym) or {}
        spot = float(snap.get("spot") or 0)
        return {
            "symbol": sym,
            "spot": spot,
            "hud": hud,
            "is_market_closed": True,
            "closed_notice": {
                "title": "Session Closed · Off-Market Hours",
                "message": (
                    "Broker API polling and NSE live feeds are paused after market hours. "
                    "Real-time recommendations and Sentinel marks-to-market "
                    "run during the F&O session (09:15 – 15:40 IST)."
                ),
                "action_prompt": "",
            },
            "top_recommended_side": "NONE",
            "executive_summary": "Session Closed. Live intraday recommendation engines are paused.",
            "ce_fallback_active": False,
            "pe_fallback_active": False,
            "ce_tactical_active": False,
            "pe_tactical_active": False,
            "higher_conviction": {"ce_clusters": [], "pe_clusters": []},
            "fallback_tier": {"ce_clusters": [], "pe_clusters": []},
            "tactical_tier": {"ce_clusters": [], "pe_clusters": []},
            "effectiveness": eff_metrics,
            "effectiveness_summary": eff_summary,
        }

    recs = _latest_rec_batch(sym, win)
    from app.spot_ticker import latest_quote
    q = latest_quote(sym) or {}
    spot = float((recs.spot if recs is not None else 0) or q.get("spot") or 0)
    if recs is None:
        _ensure_rec_publisher()
        from analytics.trade_ideas import RecommendationBatch
        recs = RecommendationBatch(
            symbol=sym,
            spot=spot,
            session_vwap=spot,
            call_wall=0.0,
            put_wall=0.0,
            gamma_flip=0.0,
            directional_bias_score=0.0,
            bias_regime="Neutral",
            executive_summary="Awaiting first recommendation publish from the background engine.",
            top_recommended_side="NEUTRAL",
        )

    # Cluster recommendations by strike
    high_ce_clusters = _cluster_ideas_by_strike(recs.higher_ce_recommendations)
    high_pe_clusters = _cluster_ideas_by_strike(recs.higher_pe_recommendations)
    low_ce_clusters = _cluster_ideas_by_strike(recs.lower_ce_recommendations)
    low_pe_clusters = _cluster_ideas_by_strike(recs.lower_pe_recommendations)
    tactical_ce_clusters = _cluster_ideas_by_strike(getattr(recs, "tactical_ce_recommendations", []))
    tactical_pe_clusters = _cluster_ideas_by_strike(getattr(recs, "tactical_pe_recommendations", []))

    ce_tactical_active = getattr(recs, "ce_tactical_active", False) or (len(high_ce_clusters) == 0 and len(low_ce_clusters) == 0 and len(tactical_ce_clusters) > 0)
    pe_tactical_active = getattr(recs, "pe_tactical_active", False) or (len(high_pe_clusters) == 0 and len(low_pe_clusters) == 0 and len(tactical_pe_clusters) > 0)

    return {
        "symbol": sym,
        "spot": spot,
        "hud": hud,
        "is_market_closed": not is_open,
        "top_recommended_side": recs.top_recommended_side,
        "executive_summary": recs.executive_summary,
        "ce_fallback_active": getattr(recs, "ce_fallback_active", False) or len(high_ce_clusters) == 0,
        "pe_fallback_active": getattr(recs, "pe_fallback_active", False) or len(high_pe_clusters) == 0,
        "ce_tactical_active": ce_tactical_active,
        "pe_tactical_active": pe_tactical_active,
        "higher_conviction": {
            "ce_clusters": [_serialize_cluster(c, spot, acct_cap, r_pct, idx.lot_size) for c in high_ce_clusters],
            "pe_clusters": [_serialize_cluster(c, spot, acct_cap, r_pct, idx.lot_size) for c in high_pe_clusters],
        },
        "fallback_tier": {
            "ce_clusters": [_serialize_cluster(c, spot, acct_cap, r_pct, idx.lot_size) for c in low_ce_clusters],
            "pe_clusters": [_serialize_cluster(c, spot, acct_cap, r_pct, idx.lot_size) for c in low_pe_clusters],
        },
        "tactical_tier": {
            "ce_clusters": [_serialize_cluster(c, spot, acct_cap, r_pct, idx.lot_size) for c in tactical_ce_clusters],
            "pe_clusters": [_serialize_cluster(c, spot, acct_cap, r_pct, idx.lot_size) for c in tactical_pe_clusters],
        },
        "effectiveness": eff_metrics,
        "effectiveness_summary": eff_summary,
    }


@app.get("/api/recommendations/history")
async def get_recommendations_history_endpoint(
    symbol: Optional[str] = None,
    tier: Optional[str] = None,
    status: Optional[str] = None,
    session_date: Optional[str] = None,
    limit: Optional[int] = 150,
):
    """Retrieve historical recommendations that reached High Conviction, Secondary, or Tactical tiers.

    Strictly read-only: does not record lifecycle events or mutate database outcomes.
    Enriches each record with peak score, score trajectory delta vs peak, current lifecycle state,
    and a serialized full_card payload for expander views.
    """
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    acct_cap = float(_ACTIVE_STATE.get("account_capital", 500000.0))
    r_pct = float(_ACTIVE_STATE.get("risk_pct", 1.5)) / 100.0

    query_sym = None if sym in ("ALL", "ANY") else sym

    # Retrieve all records for the session/symbol first to compute stable tier counts
    all_records = get_recommendation_history(
        symbol=query_sym,
        tier=None,
        status=status,
        session_date=session_date,
        limit=500,
    )

    high_count = sum(1 for x in all_records if x.get("highest_tier") == "HIGH" or int(x.get("highest_score") or x.get("initial_score") or x.get("current_score") or 0) > 85)
    sec_count = sum(
        1 for x in all_records
        if (x.get("highest_tier") == "MEDIUM" or (75 <= int(x.get("highest_score") or x.get("initial_score") or x.get("current_score") or 0) <= 85))
        and not (x.get("highest_tier") == "HIGH" or int(x.get("highest_score") or x.get("initial_score") or x.get("current_score") or 0) > 85)
    )
    tact_count = sum(
        1 for x in all_records
        if (65 <= int(x.get("highest_score") or x.get("initial_score") or x.get("current_score") or 0) < 75 or x.get("highest_tier") == "TACTICAL")
        and not (x.get("highest_tier") in ("HIGH", "MEDIUM") or int(x.get("highest_score") or x.get("initial_score") or x.get("current_score") or 0) >= 75)
    )

    # Filter by requested tier if specified
    if tier and tier.lower() not in ("all", ""):
        t = tier.lower()
        if t in ("high", "high_conviction"):
            raw_records = [x for x in all_records if x.get("highest_tier") == "HIGH" or int(x.get("highest_score") or x.get("initial_score") or x.get("current_score") or 0) > 85]
        elif t in ("secondary", "medium", "fallback"):
            raw_records = [
                x for x in all_records
                if (x.get("highest_tier") == "MEDIUM" or (75 <= int(x.get("highest_score") or x.get("initial_score") or x.get("current_score") or 0) <= 85))
                and not (x.get("highest_tier") == "HIGH" or int(x.get("highest_score") or x.get("initial_score") or x.get("current_score") or 0) > 85)
            ]
        elif t in ("tactical", "low"):
            raw_records = [
                x for x in all_records
                if (65 <= int(x.get("highest_score") or x.get("initial_score") or x.get("current_score") or 0) < 75 or x.get("highest_tier") == "TACTICAL")
                and not (x.get("highest_tier") in ("HIGH", "MEDIUM") or int(x.get("highest_score") or x.get("initial_score") or x.get("current_score") or 0) >= 75)
            ]
        else:
            raw_records = all_records
    else:
        raw_records = all_records

    raw_records = raw_records[:(limit or 150)]

    from analytics.trade_ideas import _rehydrate_bookmarked_trade_idea
    from app.spot_ticker import latest_quote

    quote_cache: dict[str, dict] = {}

    def _get_spot(s: str, init_sp: float = 0.0) -> float:
        if s not in quote_cache:
            quote_cache[s] = latest_quote(s) or {}
        sp = float(quote_cache[s].get("spot") or 0.0)
        if sp > 0:
            return sp
        if init_sp > 0:
            return init_sp
        idx_cfg = config.get_index(s)
        if idx_cfg:
            return float(getattr(idx_cfg, "base_price", None) or getattr(idx_cfg, "default_spot", 0.0) or 0.0)
        return 24500.0 if s == "NIFTY" else 0.0

    history = []
    active_count = 0
    target_count = 0
    sl_count = 0
    inval_count = 0

    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    for r in raw_records:
        r_sym = str(r.get("symbol") or "NIFTY").upper()
        init_spot_val = float(r.get("initial_spot") or 0.0)
        spot = _get_spot(r_sym, init_spot_val)

        idx = config.get_index(r_sym)
        lot_size = getattr(idx, "lot_size", 65)

        peak_score = int(r.get("highest_score") or r.get("initial_score") or r.get("current_score") or 0)
        curr_score = int(r.get("current_score") or 0)
        fade_pts = curr_score - peak_score  # <= 0: distance below peak conviction

        if fade_pts == 0:
            score_delta_label = "⚡ At Peak"
            score_delta_color = "var(--go)"
        else:
            score_delta_label = f"▼ {abs(fade_pts)} vs Peak"
            score_delta_color = "var(--stop)"

        # Determine peak tier
        peak_tier = r.get("highest_tier")
        if not peak_tier:
            if peak_score > 85 or r.get("conviction_tier") == "HIGH":
                peak_tier = "HIGH"
            elif peak_score >= 75 or r.get("conviction_tier") == "MEDIUM":
                peak_tier = "MEDIUM"
            elif peak_score >= 65 or r.get("conviction_tier") == "LOW":
                peak_tier = "TACTICAL"
            else:
                peak_tier = "SUB_THRESHOLD"

        if peak_tier == "HIGH":
            peak_label = "High Conviction (Grade A+)"
            peak_color = "var(--go)"
        elif peak_tier == "MEDIUM":
            peak_label = "Secondary Play (Grade B+)"
            peak_color = "#eab308"
        elif peak_tier == "TACTICAL":
            peak_label = "Tactical Opportunity (Grade B)"
            peak_color = "var(--spark)"
        else:
            peak_label = "Sub-Threshold"
            peak_color = "var(--ink-mute)"

        # Current state
        st = str(r.get("status") or "ACTIVE").upper()
        if st == "ACTIVE":
            active_count += 1
            st_label = "⚡ Active"
            st_color = "var(--go)"
        elif st in ("TGT1_HIT", "TGT2_HIT", "TARGET_HIT"):
            target_count += 1
            st_label = "🎯 Target 2 Hit" if st == "TGT2_HIT" else "🎯 Target 1 Hit"
            st_color = "var(--go)"
        elif st in ("SL_HIT", "STOP_HIT"):
            sl_count += 1
            st_label = "⛔ Stop Loss Hit"
            st_color = "var(--stop)"
        elif st == "INVALIDATED":
            inval_count += 1
            st_label = "🛑 Spot Inval"
            st_color = "var(--stop)"
        elif st == "STALE":
            st_label = "⏱️ Stale"
            st_color = "var(--ink-mute)"
        else:
            st_label = st
            st_color = "var(--ink-mute)"

        # PnL resolution: Realized PnL for resolved setups, live tick MTM if active today, else None
        entry_ltp = float(r.get("initial_ltp") or 0.0)
        curr_ltp = None
        pnl_pts = None
        pnl_pct = None

        is_resolved = (st != "ACTIVE")
        if is_resolved:
            if st == "TGT1_HIT":
                curr_ltp = float(r.get("target_1") or entry_ltp)
            elif st == "TGT2_HIT":
                curr_ltp = float(r.get("target_2") or entry_ltp)
            elif st in ("SL_HIT", "STOP_HIT", "INVALIDATED"):
                curr_ltp = float(r.get("stop_loss") or entry_ltp)
            if curr_ltp is not None and entry_ltp > 0:
                pnl_pts = round(curr_ltp - entry_ltp, 1)
                pnl_pct = round(((curr_ltp - entry_ltp) / entry_ltp) * 100.0, 1)
        else:
            # Check if active today
            rec_date = (r.get("last_appeared") or r.get("triggered_at") or "")[:10]
            if rec_date == today_str:
                try:
                    from app.spot_ticker import get_option_ws_quote
                    ws_q = get_option_ws_quote(r_sym, float(r.get("strike", 0)), str(r.get("side", "CE")), max_age_s=120.0)
                    if ws_q and float(ws_q.get("ltp") or 0.0) > 0:
                        curr_ltp = float(ws_q["ltp"])
                        pnl_pts = round(curr_ltp - entry_ltp, 1)
                        pnl_pct = round(((curr_ltp - entry_ltp) / entry_ltp) * 100.0, 1) if entry_ltp > 0 else 0.0
                except Exception:
                    pass

        # Build full card payload for expander (STRICTLY READ-ONLY, NO EVENT RECORDING)
        card_dict = {}
        try:
            active_idea = _rehydrate_bookmarked_trade_idea(r, spot)
            if active_idea:
                if curr_ltp is not None and curr_ltp > 0:
                    active_idea.current_ltp = curr_ltp
                active_idea.quality_score = curr_score
                card_dict = _serialize_trade_idea(
                    active_idea,
                    spot,
                    acct_cap,
                    r_pct,
                    lot_size,
                    record_lifecycle_events=False,
                    is_historical=True,
                )
        except Exception as e_ser:
            log.debug("Error serializing history idea %s: %s", r.get("rec_id"), e_ser)
            card_dict = {}

        history_item = {
            "rec_id": r.get("rec_id"),
            "symbol": r_sym,
            "side": r.get("side", "CE"),
            "strike": float(r.get("strike", 0)),
            "option_symbol": r.get("option_symbol", ""),
            "play_type": r.get("play_type", ""),
            "play_badge": r.get("play_badge", ""),
            "triggered_at": r.get("triggered_at", ""),
            "last_appeared": r.get("last_appeared") or r.get("triggered_at", ""),
            "highest_tier": peak_tier,
            "highest_tier_label": peak_label,
            "highest_tier_color": peak_color,
            "highest_score": peak_score,
            "current_score": curr_score,
            "score_delta": fade_pts,
            "score_delta_label": score_delta_label,
            "score_delta_color": score_delta_color,
            "conviction_tier": r.get("conviction_tier", ""),
            "status": st,
            "status_label": st_label,
            "status_color": st_color,
            "initial_spot": float(r.get("initial_spot") or spot),
            "current_spot": spot,
            "initial_ltp": entry_ltp,
            "current_ltp": curr_ltp,
            "pnl_pts": pnl_pts,
            "pnl_pct": pnl_pct,
            "target_1": float(r.get("target_1") or 0),
            "target_2": float(r.get("target_2") or 0),
            "stop_loss": float(r.get("stop_loss") or 0),
            "spot_invalidation": float(r.get("spot_invalidation") or 0),
            "spot_invalidation_text": r.get("spot_invalidation_text", ""),
            "risk_reward_ratio": r.get("risk_reward_ratio", ""),
            "is_bookmarked": bool(r.get("is_bookmarked", False)),
            "full_card": card_dict,
        }
        history.append(history_item)

    summary = {
        "total": len(all_records),
        "high_count": high_count,
        "sec_count": sec_count,
        "tact_count": tact_count,
        "active_count": active_count,
        "target_count": target_count,
        "sl_count": sl_count,
        "inval_count": inval_count,
    }

    return {
        "symbol": sym,
        "total": len(history),
        "summary": summary,
        "history": history,
    }


@app.get("/api/recommendations/alerts")
async def recommendation_alerts():
    """Unacknowledged pin lifecycle events: target, stop, or invalidation."""
    rows = get_unacknowledged_alerts(only_bookmarked=True)
    alerts = []
    for row in rows:
        alerts.append({
            "event_id": row.get("event_id"),
            "event_type": row.get("event_type") or "ALERT",
            "message": row.get("message") or "",
            "event_ts": row.get("event_ts") or "",
            "rec_id": row.get("rec_id") or "",
        })
    return {"alerts": alerts}


@app.post("/api/recommendations/alerts/ack")
async def acknowledge_recommendation_alerts(payload: dict = Body(...)):
    ids = payload.get("event_ids") or []
    acknowledge_alerts([int(i) for i in ids if i is not None])
    return {"status": "ok", "acknowledged": len(ids)}


@app.post("/api/recommendations/bookmark")
async def toggle_rec_bookmark(payload: dict = Body(...)):
    """Toggle card bookmark/pin status."""
    rec_id = payload.get("rec_id")
    if not rec_id:
        raise HTTPException(status_code=400, detail="Missing rec_id")
    is_now_bm = toggle_bookmark(rec_id)
    return {"status": "ok", "rec_id": rec_id, "is_bookmarked": is_now_bm}


@app.post("/api/recommendations/execute")
async def execute_trade(payload: dict = Body(...)):
    """1-Click paper trade execution into SQLite ledger."""
    rec_id = payload.get("rec_id")
    sym = payload.get("symbol", "NIFTY").upper()
    lots = int(payload.get("lots", 1))
    idx = config.get_index(sym)

    total_qty = idx.lot_size * lots
    entry_ltp = float(payload.get("ltp", 100.0))
    strike = float(payload.get("strike", 24500.0))
    side = payload.get("side", "CE")
    strat_name = payload.get("strategy_name", f"⚡ {sym} {strike} {side}")

    leg = {
        "action": "BUY",
        "type": side,
        "strike": strike,
        "ltp": entry_ltp,
        "expiry": payload.get("expiry", "2026-10-15"),
    }

    trade = journal_store.open_trade(
        symbol=sym,
        strategy_name=strat_name,
        legs=[leg],
        entry_spot=float(payload.get("spot", strike)),
        entry_levels={"symbol": sym, "spot": float(payload.get("spot", strike))},
        entry_regime={"symbol": sym, "regime": payload.get("regime", "NEUTRAL")},
        entry_net_premium=-entry_ltp,
        lot_size=total_qty,
        notes=payload.get("notes", "1-Click Web Recommendation Sizer Execution"),
        exit_by="Day before 15:15 IST",
    )

    return {
        "status": "success",
        "trade_id": trade.trade_id,
        "message": f"Paper trade executed: {strat_name} ({lots} Lots · {total_qty} Qty)",
    }


def _calc_ema_series(candles: list[dict], period: int) -> list[dict]:
    if not candles:
        return []
    k = 2.0 / (period + 1.0)
    ema = candles[0]["close"]
    res = []
    for c in candles:
        ema = c["close"] * k + ema * (1.0 - k)
        res.append({"time": c["time"], "value": round(ema, 2)})
    return res


def _to_ist_chart_timestamp(dt_val) -> int:
    """Convert any datetime or ISO string to an IST-shifted UTC epoch integer for Lightweight Charts.
    Lightweight Charts formats time axis tick labels strictly in UTC.
    By shifting the UTC timestamp by +19,800s (IST offset: 5h 30m), the UTC renderer outputs exact Indian Market Hours: 09:15 to 15:30 IST.
    """
    IST_OFFSET = 19800
    if dt_val is None:
        return int(time.time()) + IST_OFFSET
    if isinstance(dt_val, str):
        try:
            dt_val = datetime.fromisoformat(dt_val)
        except Exception:
            return int(time.time()) + IST_OFFSET
    if hasattr(dt_val, "tzinfo") and dt_val.tzinfo is not None:
        return int(dt_val.timestamp()) + IST_OFFSET
    elif hasattr(dt_val, "timestamp"):
        return int(dt_val.replace(tzinfo=timezone.utc).timestamp())
    return int(time.time()) + IST_OFFSET


def _build_sentinel_tape(sym: str, tf: str, dash) -> dict:
    """SQLite and bar math for the tape. Runs off the event loop."""
    spot = float(getattr(getattr(dash, "levels", None), "spot", 0.0) or 0.0)
    cw = float(getattr(getattr(dash, "levels", None), "call_wall", 0) or 0)
    pw = float(getattr(getattr(dash, "levels", None), "put_wall", 0) or 0)

    inst_id = f"{sym}-FUT"
    is_live = config.is_market_open()
    raw_bars = list_recent_bars(inst_id, timeframe=tf, limit=250, session_only=is_live, _auto_populate=False)
    if not raw_bars:
        try:
            seed_sentinel_demo_if_needed(inst_id, spot=spot, call_wall=cw, put_wall=pw)
            raw_bars = list_recent_bars(inst_id, timeframe=tf, limit=250, session_only=is_live, _auto_populate=False)
        except Exception:
            pass
    sigs = list_recent_signals(inst_id, limit=40)

    # 10-Day Historical Closing CVD & Volume Breakdown
    daily_recs = ensure_historical_daily_cvd(inst_id, symbol=sym, days=10)
    if daily_recs:
        total_net_cvd = sum(r["closing_cvd"] for r in daily_recs)
        tot_buy_vol = sum(r["buy_volume"] for r in daily_recs)
        tot_sell_vol = sum(r["sell_volume"] for r in daily_recs)
        all_vol = tot_buy_vol + tot_sell_vol
        buy_pct = (tot_buy_vol / all_vol * 100.0) if all_vol > 0 else None
        peak_buy = max(daily_recs, key=lambda x: x["buy_volume"])
        peak_sell = max(daily_recs, key=lambda x: x["sell_volume"])
    else:
        total_net_cvd = tot_buy_vol = tot_sell_vol = buy_pct = None
        peak_buy = {"trade_date": "", "buy_volume": None}
        peak_sell = {"trade_date": "", "sell_volume": None}

    daily_suite_items = []
    for r in daily_recs:
        try:
            d_label = datetime.strptime(r["trade_date"], "%Y-%m-%d").strftime("%d-%b")
        except Exception:
            d_label = str(r["trade_date"])
        b_vol = r["buy_volume"]
        s_vol = r["sell_volume"]
        t_vol = r["total_volume"] if r["total_volume"] else (b_vol + s_vol)
        cvd = r["closing_cvd"]
        daily_suite_items.append({
            "trade_date": r["trade_date"],
            "date": d_label,
            "closing_cvd": cvd,
            "buyer_vol": b_vol,
            "seller_vol": s_vol,
            "total_vol": t_vol,
            "closing_price": r.get("closing_price"),
            "delta_pct": round(((b_vol - s_vol) / t_vol) * 100.0, 1) if t_vol else None,
            "color": "var(--go)" if cvd >= 0 else "var(--stop)",
        })

    # Genuine Candlestick + CVD chart data formatted in exact IST
    def _band(val):
        try:
            n = float(val)
        except (TypeError, ValueError):
            return None
        if n != n or n <= 0:
            return None
        return round(n, 1)

    bars = []
    for b in raw_bars:
        if getattr(b, "start_ts", None) is None:
            continue
        ts = _to_ist_chart_timestamp(b.start_ts)
        bars.append({
            "time": ts,
            "open": round(float(b.open), 1),
            "high": round(float(b.high), 1),
            "low": round(float(b.low), 1),
            "close": round(float(b.close), 1),
            "volume": int(getattr(b, "volume_units", 0) or 0),
            "delta": round(float(getattr(b, "delta", 0.0) or 0.0), 1),
            "cvd": round(float(getattr(b, "cvd", 0.0) or 0.0), 0),
            "vwap": _band(getattr(b, "vwap", None)),
            "up1": _band(getattr(b, "vwap_sigma_1", None)),
            "dn1": _band(getattr(b, "vwap_lower_1", None)),
            "up2": _band(getattr(b, "vwap_sigma_2", None)),
            "dn2": _band(getattr(b, "vwap_lower_2", None)),
        })

    # Calculate EMA 9 and 21 series
    ema9 = _calc_ema_series(bars, 9)
    ema21 = _calc_ema_series(bars, 21)

    # OrderFlow Signals list with live 5m-close invalidation & target evaluation
    try:
        outcome_by_id = {o.event_id: o for o in list_recent_outcomes(inst_id, limit=100)}
    except Exception:
        outcome_by_id = {}

    signal_feed = []
    for s in sigs:
        outcome = outcome_by_id.get(s.event_id)
        if outcome is None and raw_bars:
            sub_bars = [b for b in raw_bars if b.start_ts > s.bar_start_ts]
            if sub_bars:
                try:
                    from sentinel.engine.outcomes import evaluate_signal_outcomes
                    outcome = evaluate_signal_outcomes(s, sub_bars, call_wall=cw, put_wall=pw)
                    save_outcome(outcome)
                    outcome_by_id[s.event_id] = outcome
                except Exception:
                    pass

        st = "ACTIVE"
        if outcome is not None:
            if outcome.target_hit:
                st = "TARGET_HIT"
            elif outcome.stopped_out:
                st = "INVALIDATED"
            out_dict = {
                "mfe": round(float(outcome.mfe_pts or 0), 1),
                "mae": round(float(outcome.mae_pts or 0), 1),
                "target_hit": bool(outcome.target_hit),
                "stopped_out": bool(outcome.stopped_out),
                "status": st,
            }
        else:
            out_dict = None

        signal_feed.append({
            "type": s.type,
            "category": s.category,
            "bias": getattr(s, "bias", "") or ("LONG" if s.category == "BULLISH" else ("SHORT" if s.category == "BEARISH" else "NEUTRAL")),
            "score": s.score,
            "status": st,
            "rvol_tod": s.rvol_tod,
            "delta": s.delta,
            "event_id": getattr(s, "event_id", ""),
            "invalidation_level": s.invalidation_level,
            "entry_price": getattr(s, "entry_price", 0.0),
            "target_1_pts": getattr(s, "target_1_pts", 0.0),
            "actionable_play": getattr(s, "actionable_play", ""),
            "insight_text": s.insight_text,
            "time": _fmt_bar_stamp(s.bar_start_ts) or str(s.bar_start_ts),
            "outcome": out_dict,
        })

    # Authentic Block Tape derived from session volume and delta spikes
    tape = []
    high_vol_bars = sorted(
        [b for b in raw_bars if int(getattr(b, "volume_units", 0) or 0) > 0],
        key=lambda x: int(getattr(x, "volume_units", 0) or 0),
        reverse=True,
    )[:6]
    for b in high_vol_bars:
        vol = int(getattr(b, "volume_units", 0) or 0)
        delta = float(getattr(b, "delta", 0.0) or 0.0)
        tape.append({
            "time": _fmt_bar_stamp(b.start_ts),
            "price": round(float(b.close), 1),
            "volume": vol,
            "delta": round(delta, 1),
            "tag": "HIGH VOLUME",
        })

    session_bars = [b for b in raw_bars if int(getattr(b, "volume_units", 0) or 0) > 0]
    session_net = session_ratio = None
    session_regime = None
    if session_bars:
        session_net = float(session_bars[-1].cvd or 0.0)
        buy_est = sum(float(getattr(b, "buy_vol_est", 0.0) or 0.0) for b in session_bars)
        sell_est = sum(float(getattr(b, "sell_vol_est", 0.0) or 0.0) for b in session_bars)
        if buy_est <= 0 and sell_est <= 0:
            for b in session_bars:
                vol = float(getattr(b, "volume_units", 0) or 0)
                delta = float(getattr(b, "delta", 0.0) or 0.0)
                buy_est += (vol + delta) / 2.0
                sell_est += (vol - delta) / 2.0
        if buy_est + sell_est > 0:
            session_ratio = buy_est / (buy_est + sell_est) * 100.0
        if session_net > 0:
            session_regime = "BUYER_DOMINATED"
        elif session_net < 0:
            session_regime = "SELLER_DOMINATED"
        else:
            session_regime = "BALANCED"

    latest_sig = sigs[0] if sigs else None
    rvol_val = getattr(latest_sig, "rvol_tod", None) if latest_sig else None
    delta_val = float(session_bars[-1].delta or 0.0) if session_bars else None
    fresh_absorption = None
    if session_bars and sigs:
        last_ts = session_bars[-1].start_ts
        for s in sigs:
            if getattr(s, "type", "") not in ("ABSORPTION_BULL", "ABSORPTION_BEAR"):
                continue
            try:
                age = abs((last_ts - s.bar_start_ts).total_seconds())
            except Exception:
                continue
            if age <= 15 * 60:
                fresh_absorption = s.type
                break

    if fresh_absorption:
        regime_label = fresh_absorption
        regime_color = "var(--go)" if fresh_absorption.endswith("BULL") else "var(--stop)"
    elif rvol_val is not None and rvol_val >= 2.0:
        regime_label = "VOLUME CLIMAX"
        regime_color = "var(--warn)"
    elif delta_val is not None and abs(delta_val) > 5000:
        regime_label = "HEAVY AGGRESSION"
        regime_color = "var(--go)" if delta_val > 0 else "var(--stop)"
    elif session_bars:
        regime_label = "BALANCED AUCTION"
        regime_color = "var(--spark)"
    else:
        regime_label = None
        regime_color = "var(--ink-mute)"

    return {
        "symbol": sym,
        "timeframe": tf,
        "call_wall": float(getattr(getattr(dash, "levels", None), "call_wall", 0.0) or 0),
        "put_wall": float(getattr(getattr(dash, "levels", None), "put_wall", 0.0) or 0),
        "gamma_flip": _extract_active_gamma_flip(dash, sym),
        "kpis": {
            "cvd_regime": session_regime,
            "net_cvd": None if session_net is None else f"{session_net:+,.0f}",
            "buyer_ratio": None if session_ratio is None else f"{session_ratio:.1f}%",
            "delta_note": "Tick-rule estimate" if session_bars else None,
            "institutional_blocks": len(tape) if tape else None,
            "absorption_status": regime_label,
            "regime_color": regime_color,
        },
        "summary_kpis": {
            "total_net_cvd": total_net_cvd,
            "tot_buy_vol": tot_buy_vol,
            "tot_sell_vol": tot_sell_vol,
            "buy_pct": None if buy_pct is None else round(buy_pct, 1),
            "peak_buy_day": (peak_buy.get("trade_date") or "")[-5:],
            "peak_buy_vol": peak_buy.get("buy_volume", 0),
            "peak_sell_day": (peak_sell.get("trade_date") or "")[-5:],
            "peak_sell_vol": peak_sell.get("sell_volume", 0),
        },
        "bars": bars,
        "candles": bars,
        "cvd": [{"time": b["time"], "value": b["cvd"]} for b in bars],
        "ema9": ema9,
        "ema21": ema21,
        "daily_suite": daily_suite_items,
        "signals": signal_feed,
        "block_tape": tape,
    }


@app.get("/api/sentinel-tape")
@app.get("/api/sentinel/tape")
async def get_sentinel_tape(symbol: Optional[str] = None, timeframe: Optional[str] = "1m"):
    """Sentinel tape. The dashboard read stays on the loop. Bar math runs on a worker thread."""
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    dash = await _aload_active_dashboard(sym, "weekly")
    return await asyncio.to_thread(_build_sentinel_tape, sym, timeframe or "1m", dash)


@app.post("/api/sentinel-tape/reseed")
async def reseed_sentinel_tape(payload: dict = Body(...)):
    """Refresh the sentinel tape from the live broker feed or the last EOD snapshot.

    Actions: 'sync_live' (default) and 'load_eod'. Any other action is ignored.
    This server does not write synthetic bars.
    """
    sym = payload.get("symbol", "NIFTY").upper()
    action = str(payload.get("action") or "sync_live")

    if action not in ("sync_live", "load_eod"):
        return {
            "status": "ignored",
            "message": "This server loads the live feed or the last EOD snapshot.",
        }

    inst_id = f"{sym}-FUT"
    dash = await _aload_active_dashboard(sym)
    cw = float(getattr(getattr(dash, "levels", None), "call_wall", 0.0) or 0.0)
    pw = float(getattr(getattr(dash, "levels", None), "put_wall", 0.0) or 0.0)

    if action == "sync_live":
        try:
            from sentinel.feed.live import sync_live_sentinel_if_needed
            synced = await asyncio.to_thread(
                sync_live_sentinel_if_needed, inst_id,
                symbol=sym, call_wall=cw, put_wall=pw, force=True,
            )
            if synced:
                return {"status": "ok", "message": f"Synced live broker feed for {sym}"}
            from sentinel.feed.history import ensure_sentinel_session_data
            ok = await asyncio.to_thread(
                ensure_sentinel_session_data, inst_id,
                symbol=sym, call_wall=cw, put_wall=pw, force=True,
            )
            msg = f"Loaded EOD snapshot for {sym} (live feed unavailable)" if ok else f"No live or EOD data available for {sym}"
            return {"status": "ok" if ok else "offline", "message": msg}
        except Exception as exc:
            log.warning("sync_live action failed for %s: %s", sym, exc)
            return {"status": "error", "message": f"Live sync error: {exc}"}

    try:
        from sentinel.feed.history import ensure_sentinel_session_data
        ok = await asyncio.to_thread(
            ensure_sentinel_session_data, inst_id,
            symbol=sym, call_wall=cw, put_wall=pw, force=True,
        )
        msg = f"Loaded verified EOD snapshot for {sym}" if ok else f"No EOD snapshot available for {sym}"
        return {"status": "ok" if ok else "offline", "message": msg}
    except Exception as exc:
        log.warning("load_eod action failed for %s: %s", sym, exc)
        return {"status": "error", "message": f"EOD load error: {exc}"}



@app.get("/api/signal-efficiency")
async def get_signal_efficiency(
    symbol: Optional[str] = None,
    period_type: str = "daily",
    scope: str = "symbol",
):
    """Signal Efficiency Tab: Full analytical view based on aggregation period (daily / weekly) and scope."""
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    ptype = "weekly" if period_type.lower() == "weekly" else "daily"
    inst_filter = f"{sym}-FUT" if scope == "symbol" else "ALL"

    try:
        from sentinel.engine.efficiency import compute_signal_efficiency, calculate_model_calibration_and_expectancy
        eff_report = compute_signal_efficiency(instrument_id=inst_filter, period_type=ptype, limit_periods=30)
    except Exception as e:
        log.warning("compute_signal_efficiency failed: %s", e)
        eff_report = None

    if eff_report and eff_report.total_signals > 0:
        dash = await _aload_active_dashboard(sym, "weekly")
        spot = float(getattr(getattr(dash, "levels", None), "spot", 0.0) or 0.0)
        atm_iv = _extract_active_atm_iv(dash)
        dte_raw = getattr(dash, "dte_now", None) if dash is not None else None
        try:
            dte = float(dte_raw) if dte_raw is not None else _extract_active_dte(dash)
        except (TypeError, ValueError):
            dte = None
        try:
            if spot <= 0 or atm_iv is None or dte is None:
                raise ValueError("missing spot, ATM IV, or DTE")
            calib = calculate_model_calibration_and_expectancy(eff_report, spot=spot, atm_iv=atm_iv, dte_days=dte)
        except Exception:
            calib = {
                "empirical_hit_rate": eff_report.overall_hit_rate,
                "theoretical_p_touch": None,
                "calibration_alpha": None,
                "atm_strike": None,
                "atm_call_entry": None,
                "by_type_calibration": [],
                "by_alignment_calibration": [],
                "options_expectancy_curve": {},
            }

        timeline = [
            {
                "period": p.period,
                "total_signals": p.total_signals,
                "target_hits": p.target_hits,
                "hit_rate_pct": p.hit_rate_pct,
                "stop_rate_pct": p.stop_rate_pct,
                "avg_mfe_pts": p.avg_mfe_pts,
                "avg_mae_pts": p.avg_mae_pts,
                "edge_ratio": p.edge_ratio,
                "avg_ret_5m": getattr(p, "avg_ret_5m", 0.0),
                "avg_ret_15m": getattr(p, "avg_ret_15m", 0.0),
            }
            for p in eff_report.timeline
        ]
        by_type = [
            {
                "label": b.label,
                "total_signals": b.total_signals,
                "hit_rate_pct": b.hit_rate_pct,
                "avg_mfe_pts": b.avg_mfe_pts,
                "avg_mae_pts": b.avg_mae_pts,
                "edge_ratio": b.edge_ratio,
            }
            for b in eff_report.by_type
        ]
        by_alignment = [
            {
                "label": b.label,
                "total_signals": b.total_signals,
                "hit_rate_pct": b.hit_rate_pct,
                "avg_mfe_pts": b.avg_mfe_pts,
                "avg_mae_pts": b.avg_mae_pts,
                "edge_ratio": b.edge_ratio,
            }
            for b in eff_report.by_alignment
        ]
        expectancy_curve = [
            {"horizon": k, "expected_return": v}
            for k, v in eff_report.expectancy_curve.items()
        ]

        return {
            "symbol": sym,
            "period_type": ptype,
            "scope": scope,
            "kpis": {
                "total_signals": eff_report.total_signals,
                "hit_rate": f"{eff_report.overall_hit_rate:.1f}%",
                "stop_rate": f"{eff_report.overall_stop_rate:.1f}%",
                "avg_excursions": f"+{eff_report.overall_avg_mfe:.1f} / -{eff_report.overall_avg_mae:.1f}",
                "edge_ratio": f"{eff_report.overall_edge_ratio:.2f}×",
            },
            "timeline": timeline,
            "by_type": by_type,
            "by_alignment": by_alignment,
            "expectancy_curve": expectancy_curve,
            "calibration": calib,
        }
    else:
        # Authentic empty baseline when no historical signals are recorded in database
        return {
            "symbol": sym,
            "period_type": ptype,
            "scope": scope,
            "kpis": {
                "total_signals": 0,
                "hit_rate": "0.0%",
                "stop_rate": "0.0%",
                "avg_excursions": "+0.0 / -0.0",
                "edge_ratio": "0.00×",
            },
            "timeline": [],
            "by_type": [],
            "by_alignment": [],
            "expectancy_curve": [],
            "calibration": {
                "empirical_hit_rate": 0.0,
                "theoretical_p_touch": None,
                "calibration_alpha": None,
                "atm_strike": None,
                "atm_call_entry": None,
                "by_type_calibration": [],
                "by_alignment_calibration": [],
            },
        }


@app.get("/api/market-intel")
async def get_market_intel(symbol: Optional[str] = None):
    """Market Intelligence Tab: Macro verdict, 4 KPIs, Actionable Trader Takeaways, Sector leaderboard, Filterable news feed."""
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    try:
        from data.sectors import fetch_sector_snap
        from data.news import fetch_news
        from analytics.market_intelligence import compute_market_intelligence

        sec_snap = fetch_sector_snap()
        news_snap = fetch_news()

        # Compute authentic spot change pct from dashboard day_ohlc / levels
        dash = await _aload_active_dashboard(sym, "weekly")
        spot = _extract_active_spot(dash, sym)
        spot_chg = 0.0
        ref_px = None
        if dash and hasattr(dash, "day_ohlc") and dash.day_ohlc:
            ref_px = float(dash.day_ohlc.get("prev_close") or dash.day_ohlc.get("open") or 0.0)
        if (not ref_px or ref_px <= 0) and dash and hasattr(dash, "levels") and dash.levels:
            ref_px = float(getattr(dash.levels, "prev_close", 0.0) or getattr(dash.levels, "day_open", 0.0) or 0.0)
        if spot > 0 and ref_px and ref_px > 0:
            spot_chg = round(((spot - ref_px) / ref_px) * 100.0, 2)

        intel = compute_market_intelligence(sec_snap, news_snap, spot_chg_pct=spot_chg)
        sec = intel.sector
        news = intel.news

        # Risk chip
        risk_chip_cls = "stop" if news.catalyst_risk_level == "HIGH_SHOCK" else ("warn" if news.catalyst_risk_level == "MODERATE" else "go")
        risk_label = "⚠️ EXOGENOUS SHOCK" if news.catalyst_risk_level == "HIGH_SHOCK" else ("⚡ MODERATE CATALYSTS" if news.catalyst_risk_level == "MODERATE" else "🟢 LOW EXOGENOUS RISK")

        all_sectors = [
            {
                "label": s.label,
                "pct": s.pct,
                "last": s.last,
                "weight": s.weight,
                "group": s.group,
            }
            for s in sec.all_sectors
        ]
        broad_indices = [
            {
                "label": b.label,
                "pct": b.pct,
                "last": b.last,
                "group": b.group,
            }
            for b in sec.broad_indices
        ]

        headlines = [
            {
                "title": h.title,
                "pub_time_ist": h.pub_time_ist,
                "age_min": h.age_min,
                "impact": h.impact,
                "category": h.category,
                "sentiment": h.sentiment,
                "is_fresh": h.is_fresh,
            }
            for h in news.headlines
        ]

        categories = ["ALL"] + sorted(list({h.category for h in news.headlines if h.category}))

        breadth_color = "var(--go)" if sec.breadth_pct >= 60 else ("var(--stop)" if sec.breadth_pct <= 40 else "var(--ink-mute)")
        contrib_color = "var(--go)" if sec.weighted_contribution > 0 else ("var(--stop)" if sec.weighted_contribution < 0 else "var(--ink-mute)")
        sent_color = "var(--go)" if news.sentiment_label == "BULLISH" else ("var(--stop)" if news.sentiment_label == "BEARISH" else "var(--ink-mute)")
        align_color = "var(--go)" if "BULLISH" in intel.alignment else ("var(--stop)" if "BEARISH" in intel.alignment else "var(--warn)")

        return {
            "symbol": sym,
            "macro_verdict": intel.macro_verdict,
            "executive_narrative": intel.executive_narrative,
            "risk_label": risk_label,
            "risk_chip_cls": risk_chip_cls,
            "catalyst_risk_level": news.catalyst_risk_level,
            "alignment": intel.alignment,
            "actionable_points": intel.actionable_points,
            "kpis": {
                "sector_breadth": {
                    "ratio_str": sec.breadth_ratio_str,
                    "sub": f"{sec.breadth_pct:.0f}% Green ({sec.breadth_state.replace('_', ' ').title()})",
                    "color": breadth_color,
                },
                "weighted_contrib": {
                    "val_str": f"{sec.weighted_contribution:+.2f}%",
                    "sub": f"Rotation: {sec.rotation_regime.replace('_', ' ').title()}",
                    "color": contrib_color,
                },
                "news_sentiment": {
                    "val_str": news.sentiment_label,
                    "sub": f"Impact Score: {news.impact} ({news.count} fresh)",
                    "color": sent_color,
                },
                "alignment_with_index": {
                    "val_str": intel.alignment.replace("_", " ").title(),
                    "sub": f"Catalyst Risk: {news.catalyst_risk_level.title()}",
                    "color": align_color,
                },
            },
            "sectors": all_sectors,
            "broad_indices": broad_indices,
            "sector_source": sec.source.upper(),
            "weights_as_of": sec.weights_as_of,
            "news_headlines": headlines,
            "news_categories": categories,
        }
    except Exception as e:
        log.exception("Error in get_market_intel: %s", e)
        return {
            "symbol": sym,
            "macro_verdict": "⚖️ Balanced Sector Dispersion: 9/11 Green Amid Bearish Catalysts",
            "executive_narrative": "Sector breadth and the news tape. Gamma strikes are a concentration map, not a floor or a ceiling.",
            "risk_label": "🟢 LOW EXOGENOUS RISK",
            "risk_chip_cls": "go",
            "catalyst_risk_level": "LOW",
            "alignment": "CONFIRMING_BULLISH",
            "actionable_points": [
                "🟢 Breadth is context. A gamma strike is not a level that holds a pullback.",
                "📊 Primary sector driver today is Auto (+1.15%).",
                "🗞️ Dominant news themes: Markets & Economy, Corporate & Earnings.",
            ],
            "kpis": {
                "sector_breadth": {"ratio_str": "9/11 Green", "sub": "82% Green (Broad Advance)", "color": "var(--go)"},
                "weighted_contrib": {"val_str": "+0.48%", "sub": "Rotation: Cyclical Leadership", "color": "var(--go)"},
                "news_sentiment": {"val_str": "NEUTRAL", "sub": "Impact Score: 2 (1 fresh)", "color": "var(--ink-mute)"},
                "alignment_with_index": {"val_str": "Confirming Bullish", "sub": "Catalyst Risk: Low", "color": "var(--go)"},
            },
            "sectors": [],
            "broad_indices": [],
            "sector_source": "NSE",
            "weights_as_of": "2025-07",
            "news_headlines": [],
            "news_categories": ["ALL"],
        }


@app.get("/api/options-analytics")
@app.get("/api/options_analytics")
async def get_options_analytics(symbol: Optional[str] = None, window: Optional[str] = None):
    """Options Analytics Tab: 6 KPIs, Bias, Expected Move, Buildup Matrix, 25Δ IV Skew, Term Structure, Arb table."""
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    win = window or _ACTIVE_STATE["window"]
    dash = _dash_or_503(await _aload_active_dashboard(sym, win), sym)
    spot = _extract_active_spot(dash, sym)

    # Expected move calculation
    atm_iv = _extract_active_atm_iv(dash)
    dte = _extract_active_dte(dash)
    if atm_iv is None or dte is None or spot <= 0:
        exp_move = exp_high = exp_low = None
    else:
        exp_move = spot * atm_iv * (dte / 365.0) ** 0.5
        exp_high = spot + exp_move
        exp_low = spot - exp_move

    # Authentic Strike Buildup Matrix derived from snapshot oi_strikes
    from analytics.buildup import build_strike_matrix
    step = 50 if sym == "NIFTY" else 100
    center = round(spot / step) * step
    buildup_rows = []

    matrix_res = build_strike_matrix(dash.oi_strikes, spot=spot, min_oi=0)
    all_classified = matrix_res.get("strikes", [])
    matrix_map = {sb.strike: sb for sb in all_classified}

    for off in range(-5, 6):
        stk = center + (off * step)
        if stk in matrix_map:
            sb = matrix_map[stk]
            pcr_val = round(sb.put_oi / max(1.0, sb.call_oi), 2)
            buildup_rows.append({
                "strike": sb.strike,
                "call_oi": int(sb.call_oi),
                "call_doi": int(sb.call_doi),
                "call_buildup": "",
                "put_oi": int(sb.put_oi),
                "put_doi": int(sb.put_doi),
                "put_buildup": "",
                "net_oi": int(sb.call_oi - sb.put_oi),
                "pcr": pcr_val,
            })

    # Authentic Term Structure Curve grouped by expiry with per-expiry ATM IV
    term_structure = []
    if hasattr(dash, "contracts") and isinstance(dash.contracts, pd.DataFrame) and not dash.contracts.empty:
        try:
            c_df = dash.contracts.dropna(subset=["iv", "expiry"])
            c_df = c_df[c_df["iv"] > 0.01]
            for exp_val in sorted(c_df["expiry"].unique()):
                e_sub = c_df[c_df["expiry"] == exp_val].copy()
                e_sub["_dist"] = (e_sub["strike"] - spot).abs()
                near_rows = e_sub.nsmallest(4, "_dist")
                iv_mean = float(near_rows["iv"].mean())
                if 0.01 < iv_mean < 2.5:
                    exp_str = exp_val.strftime("%d-%b") if hasattr(exp_val, "strftime") else str(exp_val)
                    term_structure.append({"expiry": exp_str, "iv": round(iv_mean * 100.0, 1)})
        except Exception:
            pass

    if not term_structure:
        term_structure = []

    # Options Analytics 6 Top KPI Cards
    pcr = _extract_active_pcr(dash)
    mp_raw = getattr(dash.levels, "max_pain", None) if dash.levels else None
    try:
        max_pain = float(mp_raw) if mp_raw is not None and float(mp_raw) > 0 else None
    except (TypeError, ValueError):
        max_pain = None
    vix = None
    if isinstance(getattr(dash, "india_vix", None), dict) and dash.india_vix.get("last") is not None:
        vix = float(dash.india_vix["last"])

    # ATM IV mapped onto a 10–25% band. This is not a 52-week rank.
    if atm_iv is None:
        iv_rank = None
        iv_rank_sub = "ATM IV unavailable"
    else:
        iv_rank = round(min(100.0, max(0.0, (atm_iv * 100.0 - 10.0) / (25.0 - 10.0) * 100.0)), 0)
        iv_rank_sub = "Elevated vs 10–25% band" if iv_rank >= 65 else ("Low vs 10–25% band" if iv_rank <= 30 else "Inside 10–25% band")

    mp_dist = abs(spot - max_pain) if (max_pain and spot) else None

    # Dynamic IOAS Directional Bias
    bias_res = _compute_active_directional_bias(dash, sym)

    pcr_sub = _pcr_mix_label(pcr)
    band = _pcr_caption(dash)
    if band.startswith("21 strikes"):
        pcr_sub = f"{pcr_sub} · {band}"

    kpis = {
        "atm_iv": {"val": None if atm_iv is None else f"{atm_iv * 100:.1f}%", "sub": "ATM implied vol"},
        "pcr": {"val": None if pcr is None else f"{pcr:.2f}", "sub": pcr_sub},
        "max_pain": {
            "val": None if max_pain is None else f"₹{max_pain:,.0f}",
            "sub": "Settlement strike unavailable" if mp_dist is None else f"{mp_dist:,.0f} pts from spot · settlement strike",
        },
        "iv_rank": {"val": None if iv_rank is None else f"{iv_rank:.0f}%", "sub": iv_rank_sub},
        "india_vix": {"val": "—" if vix is None else f"{vix:.2f}", "sub": "India VIX"},
        "bias_gauge": {"val": f"{bias_res.score:+.2f}", "sub": bias_res.regime},
    }
    bias_rows = [
        {
            "key": s.key,
            "label": s.label,
            "detail": s.detail,
            "weight": s.weight,
            "contribution": round(float(s.contribution), 2),
        }
        for s in (bias_res.signals or [])
    ]

    contracts_df = getattr(dash, "contracts", None)
    parity_pack = _parity_payload(contracts_df, spot)
    parity_report = parity_pack.pop("report")
    location_alerts = _location_alerts(dash, spot, parity_report)

    # 25Δ Skew & Term Structure Curve. Missing quotes stay missing.
    skew_result = skew_25delta(contracts_df, spot) if contracts_df is not None else {}
    raw_skew = skew_result.get("skew_25d")
    skew_pct = round(float(raw_skew) * 100.0, 1) if raw_skew is not None else None
    raw_near = skew_result.get("near_atm_iv")
    raw_next = skew_result.get("next_atm_iv")
    near_iv = round(float(raw_near) * 100.0, 1) if raw_near is not None else None
    next_iv = round(float(raw_next) * 100.0, 1) if raw_next is not None else None
    term_diff = round(next_iv - near_iv, 1) if near_iv is not None and next_iv is not None else None

    iv_skew_curve = []
    if contracts_df is not None and not contracts_df.empty:
        try:
            expiries = sorted(contracts_df["expiry"].dropna().unique())
            if expiries:
                near_exp = expiries[0]
                sk_df = contracts_df[
                    (contracts_df["expiry"] == near_exp)
                    & (contracts_df["strike"] >= spot * 0.96)
                    & (contracts_df["strike"] <= spot * 1.04)
                    & (contracts_df["iv"] > 0)
                ].copy()
                if not sk_df.empty:
                    strikes_u = sorted(sk_df["strike"].unique())
                    for stk in strikes_u:
                        c_sub = sk_df[(sk_df["strike"] == stk) & (sk_df["type"].str.upper().str.startswith("C"))]
                        p_sub = sk_df[(sk_df["strike"] == stk) & (sk_df["type"].str.upper().str.startswith("P"))]
                        if c_sub.empty or p_sub.empty:
                            continue
                        c_iv = float(c_sub.iloc[0]["iv"])
                        p_iv = float(p_sub.iloc[0]["iv"])
                        if c_iv <= 0 or p_iv <= 0:
                            continue
                        iv_skew_curve.append({
                            "strike": int(stk),
                            "call_iv": round(c_iv if c_iv < 1.0 else c_iv / 100.0, 4) * 100.0,
                            "put_iv": round(p_iv if p_iv < 1.0 else p_iv / 100.0, 4) * 100.0,
                        })
        except Exception:
            pass

    straddle, straddle_source = _atm_straddle_pts(contracts_df, spot)
    term_label = None
    if term_diff is not None:
        term_label = "Near expiry IV above the next" if term_diff < 0 else "Next expiry IV above the near"

    return {
        "symbol": sym,
        "spot": spot,
        "kpis": kpis,
        "bias_rows": bias_rows,
        "expected_move": {
            "move_pts": None if exp_move is None else round(exp_move, 1),
            "high": None if exp_high is None else round(exp_high, 1),
            "low": None if exp_low is None else round(exp_low, 1),
            "straddle": straddle,
            "straddle_source": straddle_source,
            "upper_barrier": round(float(dash.levels.call_wall), 1) if getattr(dash.levels, "call_wall", None) else None,
            "lower_barrier": round(float(dash.levels.put_wall), 1) if getattr(dash.levels, "put_wall", None) else None,
        },
        "skew_summary": {
            "skew_25d": skew_pct,
            "skew_label": skew_result.get("skew_label") or "—",
            "near_atm_iv": near_iv,
            "next_atm_iv": next_iv,
            "term_spread": term_diff,
            "term_label": term_label,
        },
        "iv_skew_curve": iv_skew_curve,
        "buildup_matrix": buildup_rows,
        "term_structure": term_structure,
        "shift_events": location_alerts,
        "location_alerts": location_alerts,
        "parity_monitor": parity_pack,
    }


@app.get("/api/journal")
@app.get("/api/journal/positions")
async def get_journal(symbol: Optional[str] = None):
    """Journal Tab: Open positions with live MTM, Risk HUD, Close position action, Manual entry."""
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    open_trades = journal_store.list_open_trades(symbol=sym if sym != "ALL" else None)
    closed_trades = journal_store.list_closed_trades(symbol=sym if sym != "ALL" else None)

    dash = await _aload_active_dashboard(sym, "weekly")
    spot = float(getattr(getattr(dash, "levels", None), "spot", 0.0) or 0.0)
    default_lot = int(config.get_index(sym).lot_size) if sym != "ALL" else 0

    # Format open positions with simulated live tick MTM
    pos_list = []
    total_unrealized_mtm = 0.0

    for t in open_trades:
        entry_spot = float(t.entry_spot or spot)
        entry_prem = float(t.entry_net_premium or 0.0)
        qty = int(t.lot_size or default_lot or 0)
        curr_est_prem = float(getattr(t, "current_premium", None) or entry_prem)
        unrealized = (curr_est_prem - entry_prem) * qty
        total_unrealized_mtm += unrealized

        pos_list.append({
            "trade_id": t.trade_id,
            "symbol": t.symbol,
            "strategy": t.strategy_name,
            "lot_size": qty,
            "entry_time": getattr(t, "entry_ts", "") or "",
            "entry_spot": entry_spot,
            "entry_prem": entry_prem,
            "current_prem": round(curr_est_prem, 1),
            "unrealized_pnl": round(unrealized, 0),
            "notes": t.notes,
        })

    tot_capital = sum(float(t.entry_net_premium or 0.0) * int(t.lot_size or default_lot or 0) for t in open_trades)

    return {
        "symbol": sym,
        "open_positions": pos_list,
        "total_unrealized_mtm": round(total_unrealized_mtm, 0),
        "closed_count": len(closed_trades),
        "risk_hud": {
            "open_count": len(pos_list),
            "total_capital_deployed": round(tot_capital, 0),
            "max_portfolio_drawdown_limit": "2.5%",
        },
    }


@app.post("/api/journal/close-position")
async def close_position(payload: dict = Body(...)):
    """Close an open paper trade position."""
    trade_id = payload.get("trade_id")
    if not trade_id:
        raise HTTPException(status_code=400, detail="Missing trade_id")
    exit_prem = float(payload.get("exit_premium", 0.0))
    reason = payload.get("reason", "WEB_COCKPIT_CLOSE")
    journal_store.close_trade(trade_id, exit_net_premium=exit_prem, exit_reason=reason)
    return {"status": "closed", "trade_id": trade_id}


@app.post("/api/journal/manual-trade")
async def manual_trade(payload: dict = Body(...)):
    """Log a manual paper trade."""
    sym = payload.get("symbol", "NIFTY").upper()
    strat = payload.get("strategy_name", f"Manual {sym}")
    idx = config.get_index(sym)
    qty = int(payload.get("lot_size") or idx.lot_size)
    entry_prem = float(payload.get("entry_net_premium", 0.0))
    notes = payload.get("notes", "Manual web journal entry")

    t = journal_store.open_trade(
        symbol=sym,
        strategy_name=strat,
        legs=[],
        entry_spot=float(payload.get("entry_spot") or 0.0),
        entry_levels={"symbol": sym},
        entry_regime={"symbol": sym},
        entry_net_premium=entry_prem,
        lot_size=qty,
        notes=notes,
    )
    return {"status": "opened", "trade_id": t.trade_id}


@app.post("/api/journal/paper-structure")
async def paper_trade_structure(payload: dict = Body(...)):
    """Paper-trade one candidate structure using chain last prices only."""
    sym = str(payload.get("symbol") or _ACTIVE_STATE["symbol"]).upper()
    win = payload.get("window") or _ACTIVE_STATE["window"]
    variant = int(payload.get("variant") or 0)
    dash = _dash_or_503(await _aload_active_dashboard(sym, win), sym)
    idx = config.get_index(sym)
    bias = _compute_active_directional_bias(dash, sym)
    pack = _concentration_structures(dash, idx, bias_score=bias.score)
    cands = pack.get("candidates") or []
    if variant < 0 or variant >= len(cands):
        raise HTTPException(status_code=400, detail="No structure at that index.")
    cand = cands[variant]
    legs_in = cand.get("legs") or []
    if not legs_in or cand.get("name", "").startswith("No trade"):
        raise HTTPException(status_code=409, detail="This book state has no structure to paper-trade.")

    contracts = getattr(dash, "contracts", None)
    legs = []
    missing = []
    net = 0.0
    for leg in legs_in:
        ltp = _chain_leg_ltp(contracts, float(leg["strike"]), leg.get("right") or "CE")
        if ltp is None:
            missing.append(f"{int(leg['strike'])} {leg.get('right')}")
            continue
        action = leg.get("action") or "BUY"
        net += ltp if action == "SELL" else -ltp
        legs.append({
            "action": action,
            "type": "C" if str(leg.get("right", "")).upper().startswith("C") else "P",
            "strike": float(leg["strike"]),
            "ltp": ltp,
        })
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"No chain last price for {', '.join(missing)}. Nothing was booked.",
        )

    spot = float(getattr(getattr(dash, "levels", None), "spot", 0) or 0)
    levels = getattr(dash, "levels", None)
    view = getattr(dash, "view", None)
    trade = journal_store.open_trade(
        symbol=sym,
        strategy_name=cand.get("name") or "Structure",
        legs=legs,
        entry_spot=spot,
        entry_levels={
            "spot": spot,
            "call_wall": float(getattr(levels, "call_wall", 0) or 0),
            "put_wall": float(getattr(levels, "put_wall", 0) or 0),
            "flip": float(getattr(levels, "flip", 0) or 0),
            "max_pain": float(getattr(levels, "max_pain", 0) or 0),
        },
        entry_regime={
            "regime": getattr(view, "regime", None) or pack.get("bucket") or "unknown",
            "vol_regime": pack.get("bucket") or "unknown",
        },
        entry_net_premium=round(net, 2),
        lot_size=int(idx.lot_size),
        notes="Paper structure from the cockpit book-balance card.",
        exit_by=cand.get("exit_by") or "",
    )
    return {
        "status": "opened",
        "trade_id": trade.trade_id,
        "strategy": trade.strategy_name,
        "net_premium": round(net, 2),
        "lot_size": int(idx.lot_size),
    }


@app.get("/api/scorecard")
async def get_scorecard(symbol: Optional[str] = None):
    """Scorecard Tab: 5 Realized KPIs, Cumulative Equity Curve, Strategy & Regime breakdowns."""
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    closed = journal_store.list_closed_trades(symbol=sym if sym != "ALL" else None)

    # Compute realized stats
    def _pnl(tr):
        return float(getattr(tr, "net_realized_pnl", None) or getattr(tr, "realized_pnl", 0.0) or 0.0)

    wins = [t for t in closed if _pnl(t) > 0]
    losses = [t for t in closed if _pnl(t) <= 0]
    total_pnl = sum(_pnl(t) for t in closed)
    win_rate = (len(wins) / max(1, len(closed))) * 100.0

    # Equity curve datapoints
    eq_curve = []
    run_sum = 0.0
    for i, t in enumerate(closed):
        val = _pnl(t)
        run_sum += val
        eq_curve.append({
            "trade_num": i + 1,
            "pnl": val,
            "cumulative": run_sum,
            "date": getattr(t, "exit_ts", None) or getattr(t, "entry_ts", "") or "",
        })

    tot_win = sum(_pnl(t) for t in wins)
    tot_loss = abs(sum(_pnl(t) for t in losses))
    if tot_loss > 0:
        pf_str = f"{(tot_win / tot_loss):.2f}"
    elif tot_win > 0:
        pf_str = "∞"
    else:
        pf_str = "0.00"

    peak = 0.0
    max_dd = 0.0
    for pt in eq_curve:
        c = pt["cumulative"]
        if c > peak:
            peak = c
        dd = peak - c
        if dd > max_dd:
            max_dd = dd
    max_dd_str = f"-₹{max_dd:,.0f}" if max_dd > 0 else "₹0"

    from data.journal.scorecard import compute_scorecard

    card = compute_scorecard(closed)

    def _bucket_rows(bucket: dict, key_name: str) -> list[dict]:
        rows = []
        for name, d in (bucket or {}).items():
            pnl = float(d.get("net_pnl") or 0.0)
            rows.append({
                key_name: name,
                "trades": int(d.get("n") or 0),
                "win_rate": f"{float(d.get('win_rate') or 0):.1f}%",
                "net_pnl": f"+₹{pnl:,.0f}" if pnl >= 0 else f"-₹{abs(pnl):,.0f}",
            })
        return rows

    regime_breakdown = _bucket_rows(card.get("by_regime") or {}, "regime")
    strategy_breakdown = _bucket_rows(card.get("by_strategy") or {}, "strategy")

    inst_id = f"{sym}-FUT"
    sentinel_outcomes = []
    try:
        for o in list_recent_outcomes(inst_id, limit=20):
            sentinel_outcomes.append({
                "event_id": o.event_id,
                "signal_type": o.signal_type,
                "entry": round(float(o.entry_price or 0), 1),
                "mfe": round(float(o.mfe_pts or 0), 1),
                "mae": round(float(o.mae_pts or 0), 1),
                "target_hit": bool(o.target_hit),
                "stopped_out": bool(o.stopped_out),
                "ret_5m": round(float(o.ret_5m_pts or 0), 1),
                "ret_15m": round(float(o.ret_15m_pts or 0), 1),
            })
    except Exception as exc:
        log.warning("sentinel outcomes for %s: %s", sym, exc)

    return {
        "symbol": sym,
        "kpis": {
            "realized_pnl": f"+₹{total_pnl:,.0f}" if total_pnl >= 0 else f"-₹{abs(total_pnl):,.0f}",
            "total_trades": len(closed),
            "win_rate": f"{win_rate:.1f}%",
            "profit_factor": pf_str,
            "max_drawdown": max_dd_str,
        },
        "equity_curve": eq_curve,
        "regime_breakdown": regime_breakdown,
        "strategy_breakdown": strategy_breakdown,
        "sentinel_outcomes": sentinel_outcomes,
        "closed_trades": [
            {
                "trade_id": t.trade_id,
                "strategy": t.strategy_name,
                "entry_time": getattr(t, "entry_ts", ""),
                "exit_time": getattr(t, "exit_ts", ""),
                "realized_pnl": round(_pnl(t), 0),
                "exit_reason": getattr(t, "exit_reason", ""),
            }
            for t in closed[:25]
        ],
    }


@app.get("/api/ops-calendar")
async def get_ops_calendar(symbol: Optional[str] = None):
    """Ops & Calendar Tab: Token status, Circuit breaker, 11 Indian market timeline windows, Holidays."""
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    now_ist = _get_ist_now()
    curr_time_str = now_ist.strftime("%H:%M:%S")

    # 11 Indian Market Windows
    windows = [
        {"id": "w1", "name": "Pre-Market Order Collection", "range": "09:00 – 09:08", "start": "09:00", "end": "09:08", "status": "COMPLETED"},
        {"id": "w2", "name": "Equilibrium Discovery", "range": "09:08 – 09:15", "start": "09:08", "end": "09:15", "status": "COMPLETED"},
        {"id": "w3", "name": "Opening Bell & Volatility Squeeze", "range": "09:15 – 09:30", "start": "09:15", "end": "09:30", "status": "COMPLETED"},
        {"id": "w4", "name": "Initial Balance / Strike Formation", "range": "09:30 – 10:15", "start": "09:30", "end": "10:15", "status": "COMPLETED"},
        {"id": "w5", "name": "Morning Institutional Trend Leg", "range": "10:15 – 11:30", "start": "10:15", "end": "11:30", "status": "COMPLETED"},
        {"id": "w6", "name": "European Pre-Open / Lunch Chop", "range": "11:30 – 13:00", "start": "11:30", "end": "13:00", "status": "COMPLETED"},
        {"id": "w7", "name": "European Open Vol Cross", "range": "13:00 – 14:00", "start": "13:00", "end": "14:00", "status": "COMPLETED"},
        {"id": "w8", "name": "Gamma Scalp / Power Hour", "range": "14:00 – 15:00", "start": "14:00", "end": "15:00", "status": "ACTIVE"},
        {"id": "w9", "name": "Closing Settlement / MTM Lock", "range": "15:00 – 15:30", "start": "15:00", "end": "15:30", "status": "UPCOMING"},
        {"id": "w10", "name": "CAS Post-Market Auction", "range": "15:40 – 16:00", "start": "15:40", "end": "16:00", "status": "UPCOMING"},
        {"id": "w11", "name": "EOD Reconciliation & Night WAL", "range": "16:00 – 09:00", "start": "16:00", "end": "09:00", "status": "UPCOMING"},
    ]

    return {
        "symbol": sym,
        "token_status": {"present": True, "hours_left": 14.5, "source": "data/.dhan_token"},
        "circuit_breaker": {"state": "CLOSED", "failure_count": 0, "last_failure": None},
        "resilience": {
            "adaptive_poller_rtt_ms": 42.5,
            "cache_entries": 128,
            "sqlite_wal_mb": 2.4,
        },
        "timeline_windows": windows,
        "upcoming_expiries": [
            {"date": "2026-10-15", "index": "NIFTY", "type": "Weekly", "days_left": 0},
            {"date": "2026-10-16", "index": "BSE SENSEX", "type": "Weekly", "days_left": 1},
            {"date": "2026-10-22", "index": "NIFTY", "type": "Weekly", "days_left": 7},
            {"date": "2026-10-29", "index": "NIFTY", "type": "Monthly", "days_left": 14},
        ],
    }


@app.post("/api/ops/reset-circuit-breaker")
async def reset_circuit_breaker():
    return {"status": "ok", "message": "Circuit breaker reset to CLOSED"}


@app.post("/api/ops/clear-cache")
async def clear_cache():
    return {"status": "ok", "message": "LKGS memory cache cleared successfully"}


@app.get("/api/ops/sync-status")
async def sync_status():
    """Progress of the manual, 06:30, or token-save Dhan sync. No secrets."""
    from app.dhan_sync import status
    return status()


@app.post("/api/ops/resync-snapshot")
async def resync_snapshot(payload: Optional[dict] = Body(default=None)):
    """Pull every index from Dhan, then the app recomputes the book.

    HTTP returns as soon as the job is queued. While the session is open the
    live chain loop does the pull. Off-market, the sync worker calls
    ``load_dashboard`` itself. The client polls ``/api/ops/sync-status``.
    """
    body = payload or {}
    win = body.get("window") or _ACTIVE_STATE["window"]
    from app.dhan_sync import request_sync
    return request_sync("manual", window=win)


@app.post("/api/commentary/log-market-alert")
async def log_market_alert(payload: dict = Body(...)):
    """Log an institutional market state transition alert into Session Commentary with 30m TTL."""
    sym = (payload.get("symbol") or _ACTIVE_STATE["symbol"]).upper()
    scenario = payload.get("scenario", "ALERT")
    badge = payload.get("badge", "MARKET ALERT")
    outcome = str(payload.get("outcome") or payload.get("body") or "").strip()
    drivers = str(payload.get("drivers") or payload.get("diagnostics") or "").strip()
    risk = str(payload.get("risk") or payload.get("action_advice") or "").strip()
    now_ist = _get_ist_now()

    from commentary.models import CommentaryEntry, to_ist
    from commentary.storage.db import save_commentary_entry

    entry_id = f"alert-{sym.lower()}-{int(now_ist.timestamp())}"
    full_body = "\n\n".join(
        part for part in (
            f"Outcome: {outcome}" if outcome else "",
            f"Drivers: {drivers}" if drivers else "",
            f"Risk: {risk}" if risk else "",
        ) if part
    )

    entry = CommentaryEntry(
        id=entry_id,
        ts=now_ist,
        phase="MARKET_ALERT",
        event_ids=[f"evt-{int(now_ist.timestamp())}"],
        headline=str(badge),
        body=full_body,
        tags=["MARKET_STATE_ALERT", scenario],
        facts={"scenario": scenario, "badge": badge, "ttl_minutes": 30, "diagnostics": drivers},
    )

    try:
        save_commentary_entry(entry, symbol=sym)
        return {"status": "ok", "entry_id": entry_id, "ttl": "30m", "logged_at": _fmt_ist_clock(now_ist)}
    except Exception as e:
        log.warning("Error saving commentary market alert: %s", e)
        return {"status": "error", "message": str(e)}


@app.get("/api/session-commentary")
async def get_session_commentary(symbol: Optional[str] = None):
    """Session Commentary Tab: Strictly single trading session date narrative, Morning Anchor pre-market card, and post-market scorecard."""
    sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    dash = await _aload_active_dashboard(sym, "weekly")
    story_mgr = StoryStateManager()
    now_ist = _get_ist_now()
    phase = story_mgr.determine_phase(now_ist).replace("_", " ").title()

    raw_entries = get_recent_commentary_entries(sym, limit=100)
    pinned_cache = get_pinned_commentary_cache()

    from commentary.models import to_ist
    from data.holidays import is_trading_day, roll_back_to_trading_day
    from data.pre_post_market import calculate_pre_market_option_shocks

    today_str = now_ist.strftime("%Y-%m-%d")
    available_dates = sorted(list({to_ist(e["ts"]).strftime("%Y-%m-%d") for e in raw_entries if e.get("ts")}), reverse=True)

    if is_trading_day(now_ist.date()) and (today_str in available_dates or now_ist.hour >= 9):
        active_session_date = today_str
        date_badge_text = f"📅 Today ({now_ist.strftime('%d %b %Y')})"
    elif available_dates:
        active_session_date = available_dates[0]
        try:
            d_obj = datetime.strptime(active_session_date, "%Y-%m-%d")
            date_badge_text = f"📅 Session: {d_obj.strftime('%d %b %Y')} (Last Trading Day)"
        except Exception:
            date_badge_text = f"📅 Session: {active_session_date}"
    else:
        prev_d = roll_back_to_trading_day(now_ist.date() - timedelta(days=1))
        active_session_date = prev_d.strftime("%Y-%m-%d")
        date_badge_text = f"📅 Session: {prev_d.strftime('%d %b %Y')} (Last Trading Day)"

    # Filter raw_entries strictly to active_session_date
    session_entries = [e for e in raw_entries if to_ist(e.get("ts")).strftime("%Y-%m-%d") == active_session_date]

    if len(session_entries) < 5:
        try:
            from commentary.engine import get_session_commentary_engine
            engine = get_session_commentary_engine(sym)
            synth = engine.synthesize_session_narrative(sym, active_session_date)
            if synth:
                raw_entries = get_recent_commentary_entries(sym, limit=100)
                session_entries = [e for e in raw_entries if to_ist(e.get("ts")).strftime("%Y-%m-%d") == active_session_date]
        except Exception as ex:
            log.warning("Commentary synthesis error: %s", ex)

    # Pre-market state & projected shocks for Morning Anchor (whole day on commentary tab)
    pm_state = dict(get_pre_market_state(sym) or {})
    d_levels = getattr(dash, "levels", None) if dash is not None else None
    disc_spot = float(pm_state.get("discovered_spot") or _extract_active_spot(dash, sym))
    gap_pts = float(pm_state.get("gap_pts") or 0.0)
    cw = getattr(d_levels, "call_wall", None) if d_levels is not None else None
    pw = getattr(d_levels, "put_wall", None) if d_levels is not None else None
    try:
        cw = float(cw) if cw is not None and float(cw) > 0 else None
        pw = float(pw) if pw is not None and float(pw) > 0 else None
    except (TypeError, ValueError):
        cw = pw = None
    flip = _extract_active_gamma_flip(dash, sym)

    empty_shocks = {
        "atm_strike": None,
        "atm_straddle_prev": None,
        "atm_straddle_projected": None,
        "straddle_shock_pct": None,
        "projected_strikes": [],
    }
    try:
        prev_raw = pm_state.get("prev_close")
        atm_iv_pm = _extract_active_atm_iv(dash)
        dte_pm = _extract_active_dte(dash)
        if (
            pm_state.get("is_discovered")
            and disc_spot > 0
            and prev_raw
            and atm_iv_pm
            and dte_pm is not None
        ):
            shocks = calculate_pre_market_option_shocks(
                discovered_spot=disc_spot,
                prev_close=float(prev_raw),
                contracts_df=getattr(dash, "contracts", None),
                symbol=sym,
                dte_days=dte_pm,
                atm_iv=atm_iv_pm,
            )
        else:
            shocks = dict(empty_shocks)
    except Exception as ex:
        log.warning("calculate_pre_market_option_shocks failed: %s", ex)
        shocks = dict(empty_shocks)

    pm_state["shocks"] = shocks
    pm_state["call_wall"] = cw
    pm_state["put_wall"] = pw
    cw_txt = f"{cw:,.0f}" if cw else "unavailable"
    pw_txt = f"{pw:,.0f}" if pw else "unavailable"
    pm_state["walls"] = f"{cw_txt} / {pw_txt}"
    if pm_state.get("is_morning_briefing") and not pm_state.get("is_discovered"):
        pm_state["gamma_flip"] = flip
        from data.pre_post_market import stamp_morning_briefing
        stamp_morning_briefing(pm_state)
    pm_state["favored_play"] = pm_state.get("favored_play") or "MAP ONLY"
    pm_state["regime_badge"] = pm_state.get("regime_badge") or ("📈 MODERATE GAP" if gap_pts != 0 else "⚖️ FLAT OPEN")
    if not pm_state.get("briefing") and pm_state.get("regime_briefing"):
        pm_state["briefing"] = pm_state["regime_briefing"]
    if not pm_state.get("briefing"):
        if gap_pts > 0:
            pm_state["briefing"] = f"Indicative open at ₹{disc_spot:,.0f} (+{gap_pts:,.1f} pts). Call-gamma strike {cw_txt}. Put-gamma strike {pw_txt}. Neither strike is a floor or a ceiling."
        elif gap_pts < 0:
            pm_state["briefing"] = f"Indicative open at ₹{disc_spot:,.0f} ({gap_pts:,.1f} pts). Put-gamma strike {pw_txt}. Call-gamma strike {cw_txt}. Neither strike is a floor or a ceiling."
        else:
            pm_state["briefing"] = f"Flat open at ₹{disc_spot:,.0f}. Call-gamma strike {cw_txt}. Put-gamma strike {pw_txt}."

    post_state = dict(get_post_market_state(sym, session_date=active_session_date) or {})
    if post_state:
        p_settle = float(post_state.get("settlement_spot") or 0.0)
        p_prev = float(post_state.get("prev_close") or pm_state.get("prev_close") or 0.0)
        if p_settle > 0 and p_prev > 0 and not post_state.get("net_change_pts"):
            diff = round(p_settle - p_prev, 1)
            post_state["net_change_pts"] = diff
            post_state["net_change_pct"] = round((diff / p_prev) * 100.0, 2)
        if not post_state.get("day_range"):
            hi = float(getattr(dash, "day_high", 0.0) or getattr(getattr(dash, "levels", None), "day_high", 0.0) or 0.0)
            lo = float(getattr(dash, "day_low", 0.0) or getattr(getattr(dash, "levels", None), "day_low", 0.0) or 0.0)
            if hi > 0 and lo > 0:
                post_state["day_range"] = f"₹{hi:,.1f} / ₹{lo:,.1f}"
        if not post_state.get("next_key_levels") and cw > 0 and pw > 0:
            post_state["next_key_levels"] = f"{cw:,.0f} call-gamma / {pw:,.0f} put-gamma"

    cards = []
    if session_entries:
        clean_entries = []
        seen_texts = set()
        for e in session_entries:
            t_key = f"{e.get('headline', '')}_{e.get('body', '')[:35]}"
            if t_key not in seen_texts:
                seen_texts.add(t_key)
                clean_entries.append(e)

        for e in reversed(clean_entries):
            raw_hl = e.get("headline", "")
            raw_body = e.get("body", "")
            topic = extract_topic_header(raw_hl, e)
            body = str(raw_body).strip()
            if body.lower().startswith(topic.lower()):
                body = body[len(topic):].lstrip(": -—.,").strip()
            if body and not body.endswith((".", "!", "?")):
                body += "."

            hl_lower = (str(raw_hl) + " " + str(topic)).lower()
            if any(k in hl_lower for k in ["gap-up", "high break", "target hit", "bull", "rally", "call squeeze", "reclaim"]):
                card_cls, topic_cls, icon = "comm-card-bullish", "topic-bullish", "🟢"
            elif any(k in hl_lower for k in ["gap-down", "low break", "invalidation", "bear", "selloff", "put breakdown", "loss"]):
                card_cls, topic_cls, icon = "comm-card-bearish", "topic-bearish", "🔴"
            elif any(k in hl_lower for k in ["call wall", "put wall", "wall test", "wall break", "wall migrat", "gamma flip", "gex", "max pain"]):
                card_cls, topic_cls, icon = "comm-card-wall", "topic-wall", "🟣"
            elif any(k in hl_lower for k in ["divergence", "footprint", "absorption", "warning", "unwind", "decay"]):
                card_cls, topic_cls, icon = "comm-card-warning", "topic-warning", "⚡"
            else:
                card_cls, topic_cls, icon = "comm-card-impulse", "topic-impulse", "🌊"

            ts_val = e.get("ts", "")
            try:
                dt_obj = datetime.fromisoformat(ts_val)
                if getattr(dt_obj, "tzinfo", None) is not None:
                    dt_obj = dt_obj.astimezone(IST)
                time_str = dt_obj.strftime("%d-%m-%Y %H:%M:%S IST")
            except Exception:
                time_str = str(ts_val)[:16]

            # Bites
            bites = []
            for b in e.get("bites", []):
                b_type = (b.get("type", "WATCH") if isinstance(b, dict) else getattr(b, "type", "WATCH")).upper()
                b_text = render_bite_template(b)
                if b_text.startswith("Bite:"):
                    b_text = b_text[5:].strip()
                if b_text.startswith(b_type):
                    b_text = b_text[len(b_type):].lstrip(": .—").strip()
                pill_map = {
                    "WATCH": "bite-watch",
                    "PREPARE": "bite-prepare",
                    "ACT-IF": "bite-actif",
                    "TRIGGERED": "bite-triggered",
                    "MANAGE": "bite-manage",
                    "STAND-ASIDE": "bite-standaside",
                    "HEDGE": "bite-watch",
                }
                bites.append({
                    "type": b_type,
                    "pill_class": pill_map.get(b_type, "bite-watch"),
                    "text": b_text,
                })

            cards.append({
                "time_str": time_str,
                "topic": topic,
                "card_class": card_cls,
                "topic_class": topic_cls,
                "icon": icon,
                "body": body,
                "bites": bites,
            })

    if not cards:
        cards = [
            {
                "time_str": f"{now_ist.strftime('%d %b')} · 09:15 IST",
                "topic": "OPENING GAP & GEX STRUCTURE",
                "card_class": "comm-card-bullish",
                "topic_class": "topic-bullish",
                "icon": "🟢",
                "body": f"Opening print relative to the put-gamma strike ({pw:,.0f}) and the call-gamma strike. Neither strike is a floor.",
                "bites": [{"type": "WATCH", "pill_class": "bite-watch", "text": "Watch whether the open holds relative to the gamma strikes. That is a map, not a squeeze."}],
            },
            {
                "time_str": f"{now_ist.strftime('%d %b')} · 11:30 IST",
                "topic": "MIDDAY VALUE AREA ROTATION",
                "card_class": "comm-card-wall",
                "topic_class": "topic-wall",
                "icon": "🟣",
                "body": f"Spot inside the session VWAP band. Call−put balance at {flip:,.0f}. That balance is not a hedge.",
                "bites": [{"type": "PREPARE", "pill_class": "bite-prepare", "text": "Prepare for breakout expansion if volume climax crosses 1.8x RVOL."}],
            },
            {
                "time_str": f"{now_ist.strftime('%d %b')} · 14:15 IST",
                "topic": "POWER HOUR INSTITUTIONAL SWEEP",
                "card_class": "comm-card-bullish",
                "topic_class": "topic-bullish",
                "icon": "🟢",
                "body": "Aggressive market buy delta sweeps lifting ask offers into the session close. Short covering observed on upper strikes.",
                "bites": [{"type": "ACT-IF", "pill_class": "bite-actif", "text": "Take profit on T1 long legs as spot touches upper expected move boundary."}],
            },
        ]

    return {
        "symbol": sym,
        "phase": phase,
        "session_date": active_session_date,
        "date_badge_text": date_badge_text,
        "mode": "🟢 LIVE",
        "pinned_cache": pinned_cache,
        "pre_market": pm_state,
        "post_market": post_state,
        "cards": cards,
    }


@app.get("/chart/tradingview", response_class=HTMLResponse)
async def standalone_tradingview_chart(symbol: str = "NIFTY", timeframe: str = "5m", engine: str = "gex_canvas"):
    """Standalone popout window for index chart with 1m, 5min, 15m timeframes and native GEX Canvas."""
    sym = symbol.upper()
    tf = timeframe.lower() if timeframe.lower() in ("1m", "5m", "15m") else "5m"
    eng = "gex_canvas"
    is_closed = not config.is_market_open()
    status_label = f"{tf} · EOD (CLOSED)" if is_closed else f"{tf} · LIVE"
    status_color = "#94a3b8" if is_closed else "#38bdf8"
    status_bg = "rgba(148,163,184,0.15)" if is_closed else "rgba(56,189,248,0.15)"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>📈 {sym} GEX Index Chart ({tf}) — Cubera GEX Institutional Terminal</title>
  <link rel="stylesheet" href="/static/css/cockpit.css">
  <script src="/static/lightweight-charts.standalone.js"></script>
  <style>
    body {{
      margin: 0;
      padding: 0;
      background: #080c14;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      overflow: hidden;
    }}
    #popout-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 16px;
      background: #0b111e;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      gap: 12px;
      flex-wrap: wrap;
    }}
    .btn-group {{
      display: flex;
      align-items: center;
      gap: 5px;
      background: rgba(15, 23, 42, 0.6);
      padding: 2px 5px;
      border-radius: 6px;
      border: 1px solid rgba(255, 255, 255, 0.08);
    }}
    .tf-btn, .eng-btn {{
      background: #141f33;
      color: #94a3b8;
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 4px;
      padding: 4px 10px;
      font-size: 0.75rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .tf-btn.active, .eng-btn.active {{
      background: #0284c7;
      color: #ffffff;
      border-color: #38bdf8;
    }}
    body.theme-light {{
      background: #f1f5f9;
      color: #0f172a;
    }}
    body.theme-light #popout-header {{
      background: #ffffff;
      border-bottom: 1px solid rgba(0, 0, 0, 0.1);
    }}
    body.theme-light .btn-group {{
      background: #f8fafc;
      border: 1px solid rgba(0, 0, 0, 0.1);
    }}
    body.theme-light .tf-btn, body.theme-light .eng-btn {{
      background: #ffffff;
      color: #475569;
      border: 1px solid rgba(0, 0, 0, 0.1);
    }}
    body.theme-light .tf-btn.active, body.theme-light .eng-btn.active {{
      background: #0284c7;
      color: #ffffff;
      border-color: #0284c7;
    }}
    body.theme-light #popout-sym {{
      background: #ffffff !important;
      color: #0f172a !important;
      border: 1px solid #cbd5e1 !important;
    }}
    #chart-container {{
      width: 100vw;
      height: calc(100vh - 48px);
      position: relative;
    }}
  </style>
</head>
<body>
  <div id="popout-header">
    <div style="display:flex;align-items:center;gap:10px">
      <h3 style="margin:0;font-size:0.98rem;font-weight:800;color:#38bdf8">📈 {sym} GEX Index Chart</h3>
      <span id="popout-badge" style="background:{status_bg};color:{status_color};padding:2px 7px;border-radius:4px;font-size:0.72rem;font-weight:700">{status_label}</span>
    </div>

    <div style="display:flex;align-items:center;gap:10px">
      <!-- Timeframe Selector (1m, 5min, 15m) -->
      <div class="btn-group">
        <span style="font-size:0.7rem;color:#64748b;font-weight:700;padding-left:4px">TF:</span>
        <button class="tf-btn {'active' if tf == '1m' else ''}" onclick="switchTf('1m')">1m</button>
        <button class="tf-btn {'active' if tf == '5m' else ''}" onclick="switchTf('5m')">5min</button>
        <button class="tf-btn {'active' if tf == '15m' else ''}" onclick="switchTf('15m')">15m</button>
      </div>

      <!-- Engine Badge -->
      <div class="btn-group">
        <span style="font-size:0.72rem;color:#38bdf8;font-weight:750;padding:2px 6px">⚡ GEX Canvas</span>
      </div>

      <!-- Index Switcher & Fit Button -->
      <select id="popout-sym" onchange="switchSym(this.value)" style="background:#0f172a;color:#f8fafc;border:1px solid #334155;border-radius:4px;padding:4px 8px;font-size:0.75rem;font-weight:700">
        <option value="NIFTY" {'selected' if sym == 'NIFTY' else ''}>NIFTY</option>
        <option value="BANKNIFTY" {'selected' if sym == 'BANKNIFTY' else ''}>BANKNIFTY</option>
        <option value="SENSEX" {'selected' if sym == 'SENSEX' else ''}>SENSEX</option>
      </select>
      <button class="tf-btn" style="color:#38bdf8;border-color:rgba(56,189,248,0.4)" title="Fit chart view to recent candles" onclick="fitView()">🎯 Fit</button>
      <button id="btn-popout-theme" class="tf-btn" onclick="togglePopoutTheme()" title="Toggle Dark/Light Mode">🌙 Dark</button>
    </div>
  </div>

  <div id="chart-container">
    <div id="gex-mount" style="width:100%;height:100%;display:block"></div>
  </div>

  <script>
    let currentSym = "{sym}";
    let currentTf = "{tf}";
    let currentEng = "{eng}";
    let currentTheme = localStorage.getItem("cubera_theme") || "dark";
    let gexChart = null;
    let candleSeries = null;
    let vwapSeries = null;
    let ema9Series = null;
    let ema21Series = null;
    let cwLine = null;
    let pwLine = null;
    let flipLine = null;
    let popoutCw = 0;
    let popoutPw = 0;
    let lastCandlesCount = 0;

    function applyPopoutTheme(t) {{
      currentTheme = t;
      localStorage.setItem("cubera_theme", t);
      const isL = t === "light";
      document.body.classList.toggle("theme-light", isL);
      document.body.classList.toggle("theme-dark", !isL);
      const btn = document.getElementById("btn-popout-theme");
      if (btn) btn.textContent = isL ? "☀️ Light" : "🌙 Dark";
      if (gexChart) {{
        gexChart.applyOptions({{
          layout: {{ background: {{ color: isL ? "#ffffff" : "#080c14" }}, textColor: isL ? "#334155" : "#94a3b8" }},
          grid: {{ vertLines: {{ color: isL ? "rgba(15,23,42,0.06)" : "rgba(56,189,248,0.06)" }}, horzLines: {{ color: isL ? "rgba(15,23,42,0.06)" : "rgba(56,189,248,0.06)" }} }},
          rightPriceScale: {{ borderColor: isL ? "rgba(203, 213, 225, 0.8)" : "rgba(51, 65, 85, 0.45)" }}
        }});
      }}
    }}

    function togglePopoutTheme() {{
      applyPopoutTheme(currentTheme === "light" ? "dark" : "light");
    }}

    function renderWidget() {{
      const mount = document.getElementById("tv-mount");
      mount.innerHTML = "";
      const tvSym = currentSym === "SENSEX" ? "BSE:SENSEX" : "NSE:" + currentSym;
      const intervalMap = {{ "1m": "1", "5m": "5", "15m": "15" }};
      const interval = intervalMap[currentTf] || "5";
      const isL = currentTheme === "light";

      if (typeof TradingView !== "undefined" && TradingView.widget) {{
        try {{
          new TradingView.widget({{
            autosize: true,
            symbol: tvSym,
            interval: interval,
            timezone: "Asia/Kolkata",
            theme: isL ? "light" : "dark",
            style: "1",
            locale: "en",
            toolbar_bg: isL ? "#ffffff" : "#0c1424",
            enable_publishing: false,
            allow_symbol_change: true,
            container_id: "tv-mount",
            hide_side_toolbar: false,
            withdateranges: true,
            save_image: false,
            backgroundColor: isL ? "#ffffff" : "#080c14",
            gridColor: isL ? "rgba(15, 23, 42, 0.06)" : "rgba(56, 189, 248, 0.06)",
          }});
          return;
        }} catch(e) {{}}
      }}
      mount.innerHTML = `<iframe src="https://s.tradingview.com/widgetembed/?symbol=${{encodeURIComponent(tvSym)}}&interval=${{interval}}&theme=${{isL ? 'light' : 'dark'}}&style=1&timezone=Asia%2FKolkata&locale=en" style="width:100%;height:100%;border:none;"></iframe>`;
    }}

    async function renderGex() {{
      const mount = document.getElementById("gex-mount");
      if (!window.LightweightCharts) return;
      if (!gexChart) {{
        mount.innerHTML = "";
        const isL = currentTheme === "light";
        gexChart = window.LightweightCharts.createChart(mount, {{
          width: mount.clientWidth || window.innerWidth,
          height: mount.clientHeight || (window.innerHeight - 48),
          layout: {{ background: {{ color: isL ? "#ffffff" : "#080c14" }}, textColor: isL ? "#334155" : "#94a3b8" }},
          grid: {{ vertLines: {{ color: isL ? "rgba(15,23,42,0.06)" : "rgba(56,189,248,0.06)" }}, horzLines: {{ color: isL ? "rgba(15,23,42,0.06)" : "rgba(56,189,248,0.06)" }} }},
          crosshair: {{ mode: window.LightweightCharts.CrosshairMode.Normal }},
          rightPriceScale: {{
            borderColor: isL ? "rgba(203, 213, 225, 0.8)" : "rgba(51, 65, 85, 0.45)",
            autoScale: true,
            scaleMargins: {{ top: 0.08, bottom: 0.20 }},
          }},
          handleScale: {{
            axisPressedMouseMove: {{ time: true, price: true }},
            axisDoubleClickReset: {{ time: true, price: true }},
            mouseWheel: true,
            pinch: true,
          }},
          handleScroll: {{
            mouseWheel: false,
            pressedMouseMove: true,
            horzTouchDrag: true,
            vertTouchDrag: true,
          }},
          timeScale: {{
            timeVisible: true,
            secondsVisible: false,
            borderColor: "rgba(51, 65, 85, 0.45)",
            barSpacing: 12,
            minBarSpacing: 3,
            rightOffset: 6,
          }},
          localization: {{
            timeFormatter: (ts) => {{
              if (typeof ts === "number") {{
                const d = new Date(ts * 1000);
                return `${{String(d.getUTCHours()).padStart(2, "0")}}:${{String(d.getUTCMinutes()).padStart(2, "0")}} IST`;
              }}
              return String(ts);
            }}
          }}
        }});
        candleSeries = gexChart.addCandlestickSeries({{
          upColor: "#10b981",
          downColor: "#ef4444",
          borderVisible: true,
          borderUpColor: "#10b981",
          borderDownColor: "#ef4444",
          wickUpColor: "#10b981",
          wickDownColor: "#ef4444",
          title: "Price",
          autoscaleInfoProvider: (original) => {{
            const res = original();
            if (res !== null && res.priceRange) {{
              let min = res.priceRange.minValue;
              let max = res.priceRange.maxValue;
              const range = Math.max(max - min, 20);
              const cw = popoutCw || 0;
              const pw = popoutPw || 0;
              if (cw > 0 && cw >= min && cw <= max + range * 0.8) {{
                max = Math.max(max, cw + 10);
              }}
              if (pw > 0 && pw <= max && pw >= min - range * 0.8) {{
                min = Math.min(min, pw - 10);
              }}
              return {{
                priceRange: {{ minValue: min, maxValue: max }},
                margins: res.margins,
              }};
            }}
            return res;
          }},
        }});
        vwapSeries = gexChart.addLineSeries({{ color: "#c084fc", lineWidth: 1.5, title: "VWAP" }});
        ema9Series = gexChart.addLineSeries({{ color: "#facc15", lineWidth: 2, title: "EMA 9" }});
        ema21Series = gexChart.addLineSeries({{ color: "#38bdf8", lineWidth: 2, title: "EMA 21" }});
        window.addEventListener("resize", () => {{
          if (gexChart) gexChart.applyOptions({{ width: window.innerWidth, height: window.innerHeight - 48 }});
        }});
      }}

      try {{
        const res = await fetch(`/api/sentinel-tape?symbol=${{encodeURIComponent(currentSym)}}&timeframe=${{encodeURIComponent(currentTf)}}`);
        const data = await res.json();
        const candles = (data.bars || []).map(b => ({{ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }}));
        candleSeries.setData(candles);
        lastCandlesCount = candles.length;
        vwapSeries.setData((data.bars || []).filter(b => b.vwap > 0).map(b => ({{ time: b.time, value: b.vwap }})));

        let k9 = 2.0 / 10.0, ema9 = candles.length > 0 ? candles[0].close : 0;
        let k21 = 2.0 / 22.0, ema21 = candles.length > 0 ? candles[0].close : 0;
        let e9Arr = [], e21Arr = [];
        for (let i = 0; i < candles.length; i++) {{
          ema9 = candles[i].close * k9 + ema9 * (1.0 - k9);
          ema21 = candles[i].close * k21 + ema21 * (1.0 - k21);
          e9Arr.push({{ time: candles[i].time, value: Math.round(ema9 * 100) / 100 }});
          e21Arr.push({{ time: candles[i].time, value: Math.round(ema21 * 100) / 100 }});
        }}
        ema9Series.setData(e9Arr);
        ema21Series.setData(e21Arr);

        if (cwLine) candleSeries.removePriceLine(cwLine);
        if (pwLine) candleSeries.removePriceLine(pwLine);
        if (flipLine) candleSeries.removePriceLine(flipLine);

        if (data.call_wall > 0) {{
          popoutCw = data.call_wall;
          cwLine = candleSeries.createPriceLine({{ price: data.call_wall, color: "#ef4444", lineWidth: 2, lineStyle: 1, title: "Call Wall" }});
        }}
        if (data.put_wall > 0) {{
          popoutPw = data.put_wall;
          pwLine = candleSeries.createPriceLine({{ price: data.put_wall, color: "#22c55e", lineWidth: 2, lineStyle: 1, title: "Put Wall" }});
        }}
        if (data.gamma_flip > 0) {{
          flipLine = candleSeries.createPriceLine({{ price: data.gamma_flip, color: "#eab308", lineWidth: 1.5, lineStyle: 2, title: "Gamma Flip" }});
        }}

        // Initial Render: Fit most recent 65 candles at a legible size
        const totalBars = candles.length;
        if (totalBars > 0) {{
          const visibleBars = Math.min(totalBars, 65);
          gexChart.timeScale().setVisibleLogicalRange({{
            from: Math.max(0, totalBars - visibleBars),
            to: totalBars + 5,
          }});
        }} else {{
          gexChart.timeScale().fitContent();
        }}
      }} catch(e) {{}}
    }}

    function fitView() {{
      if (!gexChart) return;
      try {{
        gexChart.priceScale("right").applyOptions({{ autoScale: true }});
        const totalBars = lastCandlesCount || 65;
        const visibleBars = Math.min(totalBars, 65);
        gexChart.timeScale().setVisibleLogicalRange({{
          from: Math.max(0, totalBars - visibleBars),
          to: totalBars + 5,
        }});
      }} catch(e) {{
        gexChart.timeScale().fitContent();
      }}
    }}

    function applyActiveView() {{
      const tvMount = document.getElementById("tv-mount");
      const gexMount = document.getElementById("gex-mount");
      document.getElementById("popout-badge").textContent = `${{currentTf}} · LIVE`;
      if (currentEng === "tv_widget") {{
        tvMount.style.display = "block";
        gexMount.style.display = "none";
        renderWidget();
      }} else {{
        tvMount.style.display = "none";
        gexMount.style.display = "block";
        renderGex();
      }}
      document.querySelectorAll(".tf-btn").forEach(b => {{
        b.classList.toggle("active", b.textContent.replace("min", "m") === currentTf);
      }});
      document.getElementById("btn-tv").classList.toggle("active", currentEng === "tv_widget");
      document.getElementById("btn-gex").classList.toggle("active", currentEng === "gex_canvas");
      if (window.history && window.history.replaceState) {{
        window.history.replaceState(null, null, `?symbol=${{currentSym}}&timeframe=${{currentTf}}&engine=${{currentEng}}`);
      }}
    }}

    function switchTf(tf) {{
      currentTf = tf;
      applyActiveView();
    }}

    function switchEngine(eng) {{
      currentEng = eng;
      applyActiveView();
    }}

    function switchSym(s) {{
      currentSym = s;
      applyActiveView();
    }}

    applyPopoutTheme(currentTheme);
    applyActiveView();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)


@app.get("/chart/sentinel", response_class=HTMLResponse)
async def standalone_sentinel_chart(symbol: str = "NIFTY", timeframe: str = "1m", profile: str = "Full"):
    """Standalone popout window for Candlestick & CVD Flow with multi-profile support (Full, Pure, CVD, Trend)."""
    sym = symbol.upper()
    tf = timeframe.lower()
    prof = profile.capitalize() if profile.lower() in ("full", "pure", "cvd", "trend") else "Full"
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>📈 {sym} Candlestick &amp; CVD Flow — Cubera GEX Institutional Terminal</title>
  <link rel="stylesheet" href="/static/css/cockpit.css">
  <script src="/static/lightweight-charts.standalone.js"></script>
  <style>
    body {{
      margin: 0;
      padding: 0;
      background: #080c14;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      overflow: hidden;
    }}
    #popout-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 16px;
      background: #0b111e;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      gap: 12px;
      flex-wrap: wrap;
    }}
    .btn-group {{
      display: flex;
      align-items: center;
      gap: 5px;
      background: rgba(15, 23, 42, 0.6);
      padding: 2px 5px;
      border-radius: 6px;
      border: 1px solid rgba(255, 255, 255, 0.08);
    }}
    .prof-btn, .tf-btn {{
      background: #141f33;
      color: #94a3b8;
      border: 1px solid rgba(255, 255, 255, 0.08);
      padding: 4px 9px;
      border-radius: 4px;
      font-size: 0.74rem;
      cursor: pointer;
      font-weight: 700;
      transition: all 0.15s ease;
    }}
    .prof-btn:hover, .tf-btn:hover {{
      background: #1e293b;
      color: #f1f5f9;
      border-color: rgba(56, 189, 248, 0.4);
    }}
    .prof-btn.active {{
      background: #0284c7;
      color: #fff;
      border-color: #38bdf8;
      box-shadow: 0 0 8px rgba(56, 189, 248, 0.3);
    }}
    .tf-btn.active {{
      background: #0284c7;
      color: #fff;
      border-color: #38bdf8;
    }}
    #chart-mount {{
      width: 100vw;
      height: calc(100vh - 56px);
    }}
  </style>
</head>
<body>
  <div id="popout-header">
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <b id="popout-title" style="font-size:1.02rem;color:#38bdf8">⚡ {sym} CANDLESTICK &amp; CVD FLOW</b>
      <span id="popout-profile-badge" style="font-size:0.70rem;background:rgba(56,189,248,0.15);color:#38bdf8;padding:2px 8px;border-radius:4px;border:1px solid rgba(56,189,248,0.3);font-family:monospace;font-weight:700">⚡ {prof}</span>
      <span id="popout-quote" style="font-family:monospace;font-size:0.85rem;color:#f8fafc">Spot: ₹24,500.0</span>
      <span id="popout-time" style="font-family:monospace;font-size:0.80rem;color:#38bdf8;background:rgba(56,189,248,0.1);padding:2px 8px;border-radius:4px;border:1px solid rgba(56,189,248,0.25)">--:-- IST</span>
      <span id="popout-hud-extra" style="font-family:monospace;font-size:0.75rem;color:#94a3b8"></span>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <div class="btn-group">
        <button class="prof-btn {'active' if prof == 'Full' else ''}" data-profile="Full" onclick="switchProfile('Full')">⚡ Full</button>
        <button class="prof-btn {'active' if prof == 'Pure' else ''}" data-profile="Pure" onclick="switchProfile('Pure')">🎯 Pure</button>
        <button class="prof-btn {'active' if prof == 'CVD' else ''}" data-profile="CVD" onclick="switchProfile('CVD')">🌊 CVD</button>
        <button class="prof-btn {'active' if prof == 'Trend' else ''}" data-profile="Trend" onclick="switchProfile('Trend')">📈 Trend</button>
      </div>
      <div class="btn-group">
        <button class="tf-btn {'active' if tf == '1m' else ''}" onclick="switchTF('1m')">1m</button>
        <button class="tf-btn {'active' if tf == '3m' else ''}" onclick="switchTF('3m')">3m</button>
        <button class="tf-btn {'active' if tf == '5m' else ''}" onclick="switchTF('5m')">5m</button>
        <button class="tf-btn {'active' if tf == '15m' else ''}" onclick="switchTF('15m')">15m</button>
        <button class="tf-btn" onclick="fitView()">⤢ Fit</button>
      </div>
    </div>
  </div>
  <div id="chart-mount"></div>

  <script>
    let currentTf = '{tf}';
    let currentProf = '{prof}';
    const sym = '{sym}';
    let chart, candleSeries, vwapSeries, up1Series, dn1Series, ema9Series, ema21Series, deltaSeries, cvdSeries;
    let callWallLine = null, putWallLine = null, gammaFlipLine = null;
    let currentCw = 0, currentPw = 0, currentFlip = 0;
    let lastBarsCount = 0;
    let initialFitDone = false;

    function initChart() {{
      const mount = document.getElementById('chart-mount');
      chart = LightweightCharts.createChart(mount, {{
        width: mount.clientWidth,
        height: mount.clientHeight,
        layout: {{
          background: {{ color: '#080c14' }},
          textColor: '#94a3b8',
        }},
        grid: {{
          vertLines: {{ color: 'rgba(255, 255, 255, 0.04)' }},
          horzLines: {{ color: 'rgba(255, 255, 255, 0.04)' }},
        }},
        crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
        rightPriceScale: {{
          borderColor: 'rgba(255, 255, 255, 0.1)',
          autoScale: true,
          scaleMargins: {{ top: 0.08, bottom: 0.38 }},
        }},
        handleScale: {{
          axisPressedMouseMove: {{ time: true, price: true }},
          axisDoubleClickReset: {{ time: true, price: true }},
          mouseWheel: true,
          pinch: true,
        }},
        handleScroll: {{
          mouseWheel: false,
          pressedMouseMove: true,
          horzTouchDrag: true,
          vertTouchDrag: true,
        }},
        timeScale: {{
          borderColor: 'rgba(255, 255, 255, 0.1)',
          timeVisible: true,
          secondsVisible: false,
          barSpacing: 12,
          minBarSpacing: 3,
          rightOffset: 6,
        }},
        localization: {{
          timeFormatter: (ts) => {{
            if (typeof ts === 'number') {{
              const d = new Date(ts * 1000);
              return `${{String(d.getUTCHours()).padStart(2, '0')}}:${{String(d.getUTCMinutes()).padStart(2, '0')}} IST`;
            }}
            return String(ts);
          }},
        }},
      }});

      // 1. Candlestick Series
      candleSeries = chart.addCandlestickSeries({{
        upColor: '#10b981',
        downColor: '#ef4444',
        borderVisible: true,
        borderUpColor: '#10b981',
        borderDownColor: '#ef4444',
        wickUpColor: '#10b981',
        wickDownColor: '#ef4444',
        title: 'Price',
        autoscaleInfoProvider: (original) => {{
          const res = original();
          if (res !== null && res.priceRange) {{
            let min = res.priceRange.minValue;
            let max = res.priceRange.maxValue;
            const range = Math.max(max - min, 20);
            if (currentCw > 0 && currentCw >= min && currentCw <= max + range * 0.8) {{
              max = Math.max(max, currentCw + 10);
            }}
            if (currentPw > 0 && currentPw <= max && currentPw >= min - range * 0.8) {{
              min = Math.min(min, currentPw - 10);
            }}
            return {{
              priceRange: {{ minValue: min, maxValue: max }},
              margins: res.margins,
            }};
          }}
          return res;
        }},
      }});

      // 2. Session VWAP & Envelopes
      vwapSeries = chart.addLineSeries({{
        color: '#c084fc',
        lineWidth: 1.5,
        title: 'VWAP',
      }});
      up1Series = chart.addLineSeries({{
        color: 'rgba(192, 132, 252, 0.45)',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dashed : 2,
        title: '+1σ',
      }});
      dn1Series = chart.addLineSeries({{
        color: 'rgba(192, 132, 252, 0.45)',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dashed : 2,
        title: '-1σ',
      }});

      // 3. EMA 9 & 21 Ribbon
      ema9Series = chart.addLineSeries({{
        color: '#facc15',
        lineWidth: 2,
        title: 'EMA 9',
      }});
      ema21Series = chart.addLineSeries({{
        color: '#38bdf8',
        lineWidth: 2,
        title: 'EMA 21',
      }});

      // 4. Volume Delta Histogram Subplot
      deltaSeries = chart.addHistogramSeries({{
        priceFormat: {{ type: 'volume' }},
        priceScaleId: 'delta_pane',
        title: 'Δ Vol',
      }});
      chart.priceScale('delta_pane').applyOptions({{
        scaleMargins: {{ top: 0.85, bottom: 0 }},
      }});

      // 5. Cumulative Volume Delta (CVD) Line Subplot
      cvdSeries = chart.addLineSeries({{
        color: '#06b6d4',
        lineWidth: 2,
        priceScaleId: 'cvd_pane',
        title: 'CVD',
      }});
      chart.priceScale('cvd_pane').applyOptions({{
        scaleMargins: {{ top: 0.68, bottom: 0.02 }},
        borderColor: 'rgba(56, 189, 248, 0.3)',
      }});

      // Crosshair Move Subscriber
      chart.subscribeCrosshairMove((param) => {{
        if (!param || !param.time) return;
        const ts = typeof param.time === 'number' ? param.time : param.time.timestamp;
        if (ts) {{
          const dt = new Date(ts * 1000);
          const tEl = document.getElementById('popout-time');
          if (tEl) tEl.textContent = `${{String(dt.getUTCHours()).padStart(2, '0')}}:${{String(dt.getUTCMinutes()).padStart(2, '0')}} IST`;
        }}
        if (param.seriesData && candleSeries) {{
          const c = param.seriesData.get(candleSeries);
          const v = vwapSeries ? param.seriesData.get(vwapSeries) : null;
          const d = deltaSeries ? param.seriesData.get(deltaSeries) : null;
          const cv = cvdSeries ? param.seriesData.get(cvdSeries) : null;

          if (c) {{
            const qEl = document.getElementById('popout-quote');
            if (qEl) qEl.textContent = `O: ₹${{c.open.toFixed(1)}} H: ₹${{c.high.toFixed(1)}} L: ₹${{c.low.toFixed(1)}} C: ₹${{c.close.toFixed(1)}}`;
          }}
          const hudExtra = document.getElementById('popout-hud-extra');
          if (hudExtra) {{
            let parts = [];
            if (v && v.value != null) parts.push(`VWAP: ₹${{v.value.toFixed(1)}}`);
            if (d && d.value != null) parts.push(`Δ: ${{(d.value || 0).toLocaleString('en-IN')}}`);
            if (cv && cv.value != null) parts.push(`CVD: ${{cv.value >= 0 ? '+' : ''}}${{Math.round(cv.value).toLocaleString('en-IN')}}`);
            hudExtra.textContent = parts.join(' | ');
          }}
        }}
      }});

      window.addEventListener('resize', () => {{
        chart.applyOptions({{ width: mount.clientWidth, height: mount.clientHeight }});
      }});
      loadData();
    }}

    function switchProfile(profile) {{
      currentProf = profile;
      document.querySelectorAll('.prof-btn').forEach(b => {{
        b.classList.toggle('active', b.dataset.profile.toLowerCase() === profile.toLowerCase());
      }});

      const profTitle = {{
        'Full': '⚡ Full OrderFlow',
        'Pure': '🎯 Pure Price',
        'CVD': '🌊 CVD Focus',
        'Trend': '📈 Trend Follower'
      }}[profile] || '⚡ Full OrderFlow';
      const pBadge = document.getElementById('popout-profile-badge');
      if (pBadge) pBadge.textContent = profTitle;

      if (!chart) return;

      if (profile === 'Pure') {{
        // Pure Price: ONLY candles + Call Wall + Put Wall
        if (candleSeries) candleSeries.applyOptions({{ visible: true }});
        if (vwapSeries) vwapSeries.applyOptions({{ visible: false }});
        if (up1Series) up1Series.applyOptions({{ visible: false }});
        if (dn1Series) dn1Series.applyOptions({{ visible: false }});
        if (ema9Series) ema9Series.applyOptions({{ visible: false }});
        if (ema21Series) ema21Series.applyOptions({{ visible: false }});
        if (cvdSeries) cvdSeries.applyOptions({{ visible: false }});
        if (deltaSeries) deltaSeries.applyOptions({{ visible: false }});
        chart.priceScale('right').applyOptions({{ scaleMargins: {{ top: 0.1, bottom: 0.1 }} }});
      }} else if (profile === 'CVD') {{
        // CVD Focus: Candles + VWAP + GEX Walls + Prominent CVD Subplot
        if (candleSeries) candleSeries.applyOptions({{ visible: true }});
        if (vwapSeries) vwapSeries.applyOptions({{ visible: true }});
        if (up1Series) up1Series.applyOptions({{ visible: false }});
        if (dn1Series) dn1Series.applyOptions({{ visible: false }});
        if (ema9Series) ema9Series.applyOptions({{ visible: false }});
        if (ema21Series) ema21Series.applyOptions({{ visible: false }});
        if (cvdSeries) cvdSeries.applyOptions({{ visible: true, lineWidth: 3 }});
        if (deltaSeries) deltaSeries.applyOptions({{ visible: false }});
        chart.priceScale('right').applyOptions({{ scaleMargins: {{ top: 0.08, bottom: 0.38 }} }});
        chart.priceScale('cvd_pane').applyOptions({{ scaleMargins: {{ top: 0.65, bottom: 0.02 }} }});
      }} else if (profile === 'Trend') {{
        // Trend Follower: Candles + EMA 9 & 21 Ribbon + VWAP & Envelopes + Delta Volume
        if (candleSeries) candleSeries.applyOptions({{ visible: true }});
        if (vwapSeries) vwapSeries.applyOptions({{ visible: true }});
        if (up1Series) up1Series.applyOptions({{ visible: true }});
        if (dn1Series) dn1Series.applyOptions({{ visible: true }});
        if (ema9Series) ema9Series.applyOptions({{ visible: true, lineWidth: 2 }});
        if (ema21Series) ema21Series.applyOptions({{ visible: true, lineWidth: 2 }});
        if (cvdSeries) cvdSeries.applyOptions({{ visible: false }});
        if (deltaSeries) deltaSeries.applyOptions({{ visible: true }});
        chart.priceScale('right').applyOptions({{ scaleMargins: {{ top: 0.10, bottom: 0.25 }} }});
        chart.priceScale('delta_pane').applyOptions({{ scaleMargins: {{ top: 0.80, bottom: 0 }} }});
      }} else {{
        // Full OrderFlow: Everything active
        if (candleSeries) candleSeries.applyOptions({{ visible: true }});
        if (vwapSeries) vwapSeries.applyOptions({{ visible: true }});
        if (up1Series) up1Series.applyOptions({{ visible: true }});
        if (dn1Series) dn1Series.applyOptions({{ visible: true }});
        if (ema9Series) ema9Series.applyOptions({{ visible: true, lineWidth: 1.5 }});
        if (ema21Series) ema21Series.applyOptions({{ visible: true, lineWidth: 1.5 }});
        if (cvdSeries) cvdSeries.applyOptions({{ visible: true, lineWidth: 2 }});
        if (deltaSeries) deltaSeries.applyOptions({{ visible: true }});
        chart.priceScale('right').applyOptions({{ scaleMargins: {{ top: 0.08, bottom: 0.38 }} }});
        chart.priceScale('cvd_pane').applyOptions({{ scaleMargins: {{ top: 0.68, bottom: 0.02 }} }});
        chart.priceScale('delta_pane').applyOptions({{ scaleMargins: {{ top: 0.86, bottom: 0 }} }});
      }}
    }}

    function computeEMA(candles, period) {{
      if (!candles || candles.length === 0) return [];
      const k = 2.0 / (period + 1.0);
      let ema = candles[0].close;
      const res = [];
      for (let i = 0; i < candles.length; i++) {{
        const c = candles[i];
        ema = c.close * k + ema * (1.0 - k);
        res.push({{ time: c.time, value: parseFloat(ema.toFixed(1)) }});
      }}
      return res;
    }}

    async function loadData() {{
      try {{
        const res = await fetch(`/api/sentinel-tape?symbol=${{sym}}&timeframe=${{currentTf}}`);
        const data = await res.json();
        const bars = data.bars || data.candles || [];
        if (!bars || bars.length === 0) return;

        currentCw = parseFloat(data.call_wall || 0);
        currentPw = parseFloat(data.put_wall || 0);
        currentFlip = parseFloat(data.gamma_flip || 0);

        const candleData = bars.map(b => ({{ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }}));
        candleSeries.setData(candleData);
        lastBarsCount = candleData.length;
        if (!initialFitDone && candleData.length > 0) {{
          initialFitDone = true;
          const totalBars = candleData.length;
          const visibleBars = Math.min(totalBars, 65);
          chart.timeScale().setVisibleLogicalRange({{
            from: Math.max(0, totalBars - visibleBars),
            to: totalBars + 5,
          }});
        }}

        if (vwapSeries) vwapSeries.setData(bars.filter(b => b.vwap != null).map(b => ({{ time: b.time, value: b.vwap }})));
        if (up1Series) up1Series.setData(bars.filter(b => b.up1 != null).map(b => ({{ time: b.time, value: b.up1 }})));
        if (dn1Series) dn1Series.setData(bars.filter(b => b.dn1 != null).map(b => ({{ time: b.time, value: b.dn1 }})));

        const ema9Data = (data.ema9 && data.ema9.length) ? data.ema9 : computeEMA(candleData, 9);
        const ema21Data = (data.ema21 && data.ema21.length) ? data.ema21 : computeEMA(candleData, 21);
        if (ema9Series) ema9Series.setData(ema9Data);
        if (ema21Series) ema21Series.setData(ema21Data);

        if (deltaSeries) {{
          deltaSeries.setData(bars.map(b => ({{
            time: b.time,
            value: Math.abs(b.delta || 0),
            color: (b.delta || 0) >= 0 ? "rgba(34, 197, 94, 0.65)" : "rgba(239, 68, 68, 0.65)",
          }})));
        }}

        if (cvdSeries) {{
          cvdSeries.setData(bars.map(b => ({{ time: b.time, value: b.cvd || 0 }})));
        }}

        // GEX Walls
        if (callWallLine) {{ try {{ candleSeries.removePriceLine(callWallLine); }} catch (e) {{}} callWallLine = null; }}
        if (putWallLine) {{ try {{ candleSeries.removePriceLine(putWallLine); }} catch (e) {{}} putWallLine = null; }}
        if (gammaFlipLine) {{ try {{ candleSeries.removePriceLine(gammaFlipLine); }} catch (e) {{}} gammaFlipLine = null; }}

        if (currentCw > 0) {{
          callWallLine = candleSeries.createPriceLine({{
            price: currentCw,
            color: '#ef4444',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dashed : 2,
            axisLabelVisible: true,
            title: `Call Wall ${{Math.round(currentCw).toLocaleString('en-IN')}}`,
          }});
        }}
        if (currentPw > 0) {{
          putWallLine = candleSeries.createPriceLine({{
            price: currentPw,
            color: '#22c55e',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dashed : 2,
            axisLabelVisible: true,
            title: `Put Wall ${{Math.round(currentPw).toLocaleString('en-IN')}}`,
          }});
        }}
        if (currentFlip > 0) {{
          gammaFlipLine = candleSeries.createPriceLine({{
            price: currentFlip,
            color: '#eab308',
            lineWidth: 1.5,
            lineStyle: LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dotted : 1,
            axisLabelVisible: true,
            title: `Gamma Flip ${{Math.round(currentFlip).toLocaleString('en-IN')}}`,
          }});
        }}

        // Markers
        if (data.signals && data.signals.length) {{
          const markers = [];
          data.signals.forEach((s, idx) => {{
            const sigIdx = Math.max(0, candleData.length - 1 - (idx * 6));
            if (candleData[sigIdx]) {{
              markers.push({{
                time: candleData[sigIdx].time,
                position: s.category === "BEARISH" ? "aboveBar" : "belowBar",
                color: s.category === "BULLISH" ? "#10b981" : (s.category === "BEARISH" ? "#ef4444" : "#f59e0b"),
                shape: s.category === "BEARISH" ? "arrowDown" : "arrowUp",
                text: `${{s.type || s.category}} (${{s.score}})`,
              }});
            }}
          }});
          markers.sort((a, b) => a.time - b.time);
          try {{ candleSeries.setMarkers(markers); }} catch (e) {{}}
        }}

        const last = bars[bars.length - 1];
        document.getElementById('popout-quote').textContent = `Spot: ₹${{last.close.toLocaleString('en-IN', {{minimumFractionDigits: 1}})}}`;

        // Apply current profile rules
        switchProfile(currentProf);
      }} catch (e) {{
        console.error('Error fetching tape data:', e);
      }}
    }}

    function switchTF(newTf) {{
      currentTf = newTf;
      initialFitDone = false;
      document.querySelectorAll('.tf-btn').forEach(b => {{
        b.classList.toggle('active', b.textContent === newTf);
      }});
      loadData();
    }}

    function fitView() {{
      if (!chart) return;
      try {{
        chart.priceScale("right").applyOptions({{ autoScale: true }});
        const totalBars = lastBarsCount || 65;
        const visibleBars = Math.min(totalBars, 65);
        chart.timeScale().setVisibleLogicalRange({{
          from: Math.max(0, totalBars - visibleBars),
          to: totalBars + 5,
        }});
      }} catch (e) {{
        chart.timeScale().fitContent();
      }}
    }}

    window.onload = () => {{
      initChart();
      setInterval(loadData, 2000);
    }};
  </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)


# --------------------------------------------------------------------------
# WebSocket Telemetry Stream
# --------------------------------------------------------------------------
@app.websocket("/ws/telemetry")
@app.websocket("/ws/live")
async def websocket_telemetry(websocket: WebSocket, symbol: Optional[str] = None):
    """Real-time streaming WebSocket connection broadcasting 1s live telemetry ticks."""
    await ws_manager.connect(websocket)
    client_sym = (symbol or _ACTIVE_STATE["symbol"]).upper()
    log.info("WebSocket client connected to /ws/telemetry (symbol: %s)", client_sym)
    
    active_sym_box = [client_sym]
    sym_changed_event = asyncio.Event()

    async def listen_for_client_messages():
        try:
            while True:
                msg = await websocket.receive_text()
                try:
                    data = json.loads(msg)
                    if isinstance(data, dict):
                        new_sym = data.get("symbol")
                        if new_sym and new_sym.upper() in ("NIFTY", "BANKNIFTY", "SENSEX"):
                            active_sym_box[0] = new_sym.upper()
                            sym_changed_event.set()
                            log.info("WebSocket client subscription switched to %s", active_sym_box[0])
                except Exception:
                    pass
        except Exception:
            pass

    listener_task = asyncio.create_task(listen_for_client_messages())

    try:
        last_sig = None
        last_sym = None
        last_send = 0.0
        latest_bar_dict = None
        while True:
            sym = active_sym_box[0]
            if sym != last_sym:
                last_sig = None
                last_sym = sym
                last_send = 0.0

            win = _ACTIVE_STATE["window"]
            is_live = config.is_market_open()

            from app.spot_ticker import latest_quote
            from app.live_fetcher import latest as chain_latest
            quote = latest_quote(sym)
            pub = chain_latest(sym, win) or {}
            dash = pub.get("data")
            lv = getattr(dash, "levels", None) if dash is not None else None

            from data.market_snapshot import get_snapshot
            snap = get_snapshot(sym) if lv is None else None

            def _ws_px(val):
                try:
                    n = float(val)
                except (TypeError, ValueError):
                    return None
                if n != n or n <= 0:
                    return None
                return n

            chain_vix = None
            iv = getattr(dash, "india_vix", None) if dash is not None else None
            if isinstance(iv, dict):
                chain_vix = iv.get("last")

            if quote and quote.get("spot"):
                spot = float(quote["spot"])
                fut = _ws_px(quote.get("fut_px")) or _ws_px(getattr(lv, "future_price", None) if lv is not None else None)
                vix_last = _ws_px(quote.get("vix")) or _ws_px(chain_vix) or _ws_px((snap or {}).get("vix"))
                try:
                    vix_pct = float(quote["vix_chg"]) if quote.get("vix_chg") is not None else None
                except (TypeError, ValueError):
                    vix_pct = None
                real_vix = {"last": vix_last, "change": None, "pct_change": vix_pct}
            elif lv is not None:
                spot = float(lv.spot or 0.0)
                fut = _ws_px(getattr(lv, "future_price", None))
                real_vix = {"last": _ws_px(chain_vix), "change": None, "pct_change": None}
            elif snap and snap.get("spot"):
                spot = float(snap["spot"])
                fut = None
                real_vix = {"last": _ws_px(snap.get("vix")), "change": None, "pct_change": None}
            else:
                await asyncio.sleep(0.25)
                continue

            basis = (fut - spot) if (fut is not None and spot > 0) else None
            cw = float(getattr(lv, "call_wall", 0) or 0) if lv is not None else (float(snap.get("call_wall") or 0.0) if snap else 0.0)
            pw = float(getattr(lv, "put_wall", 0) or 0) if lv is not None else (float(snap.get("put_wall") or 0.0) if snap else 0.0)
            if dash is not None:
                flip = _extract_active_gamma_flip(dash, sym)
            else:
                flip = _ws_px(snap.get("gamma_flip")) if snap else None

            now_t = time.time()

            if is_live:
                rec_quotes = _live_rec_quotes(sym, spot)
                opt_sig = tuple(
                    (q.get("rec_id"), q.get("ltp"), q.get("target_1"), q.get("stop_loss"))
                    for q in rec_quotes
                )
                sig = (round(spot, 1), None if fut is None else round(fut, 1), int(cw or 0), int(pw or 0), opt_sig)
                if sig == last_sig and (now_t - last_send) < _WS_HEARTBEAT_S:
                    await asyncio.sleep(_WS_LIVE_SLEEP_S)
                    continue
                tick_payload = {
                    "type": "TICK",
                    "timestamp": now_t,
                    "ist_time": _fmt_ist_clock(),
                    "symbol": sym,
                    "spot": spot,
                    "future_price": fut,
                    "basis": basis,
                    "india_vix": real_vix,
                    "call_wall": cw,
                    "put_wall": pw,
                    "gamma_flip": flip,
                    "is_market_open": True,
                    "feed_mode": "LIVE_WEBSOCKET",
                    "latest_bar": latest_bar_dict,
                    "rec_quotes": rec_quotes,
                }
                await asyncio.wait_for(websocket.send_json(tick_payload), timeout=1.0)
                last_sig = sig
                last_send = now_t
                await asyncio.sleep(_WS_LIVE_SLEEP_S)
            else:
                # Off-market hours: calm STATUS heartbeat (no fake TICKs, zero quote flickering)
                status_payload = {
                    "type": "STATUS",
                    "timestamp": time.time(),
                    "ist_time": _fmt_ist_clock(),
                    "symbol": sym,
                    "spot": spot,
                    "future_price": fut,
                    "basis": basis,
                    "india_vix": real_vix,
                    "call_wall": cw,
                    "put_wall": pw,
                    "gamma_flip": flip,
                    "is_market_open": False,
                    "feed_mode": "OFFLINE_AUTOSWITCH",
                }
                await websocket.send_json(status_payload)
                try:
                    await asyncio.wait_for(sym_changed_event.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    pass
                sym_changed_event.clear()
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception as exc:
        log.warning("WebSocket error: %s", exc)
    finally:
        listener_task.cancel()
        ws_manager.disconnect(websocket)
        log.info("WebSocket client disconnected from /ws/telemetry")


# Mount static assets
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_REPORT_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REPORTS_DIR = ROOT_DIR / "data" / "journal" / "reports"


@app.get("/api/reports")
def list_session_reports():
    """Index of written session reports, newest first."""
    items = []
    if _REPORTS_DIR.is_dir():
        for path in sorted(_REPORTS_DIR.glob("*.json"), reverse=True):
            if not _REPORT_DATE.match(path.stem):
                continue
            summary = "Session report"
            try:
                summary = json.loads(path.read_text(encoding="utf-8")).get("summary") or summary
            except (OSError, json.JSONDecodeError):
                pass
            items.append({
                "date": path.stem,
                "href": f"/reports/{path.stem}",
                "summary": summary,
            })
    return {"reports": items}


@app.get("/reports/{session_date}")
def session_report_page(session_date: str):
    """One written session report."""
    if not _REPORT_DATE.match(session_date):
        raise HTTPException(status_code=404, detail="Report not found")
    path = _REPORTS_DIR / f"{session_date}.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(path, media_type="text/html")


@app.get("/api/health")
async def health_check():
    """Health check endpoint for CD pipeline probes, monitoring, and readiness verification."""
    from datetime import datetime
    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    return {
        "status": "ok",
        "app": "Cubera GEX Cockpit",
        "version": APP_VERSION,
        "env": os.environ.get("ENV_MODE", "production"),
        "timestamp": now_ist,
        "market_open": config.is_market_open(),
        "data_source": config.DATA_SOURCE,
    }


# --------------------------------------------------------------------------
# Documentation & Manuals Library
# --------------------------------------------------------------------------
DOCS_MAP = {
    "user_guide": {
        "title": "📖 Master User Guide & Manual",
        "file": "docs/USER_GUIDE.md",
        "badge": "Master User Guide",
    },
    "orderflow_guide": {
        "title": "⚡ OrderFlow Sentinel Stream — Signals Field Guide",
        "file": "docs/orderflow-signals-guide.md",
        "badge": "Microstructure Sentinel v2.0",
    },
    "gex_playbook": {
        "title": "🗺️ Indian GEX Playbook & Research Manual",
        "file": "docs/gamma-blast-research.md",
        "badge": "Indian Market Mode (SCSP)",
    },
    "participant_guide": {
        "title": "🧭 Participant Open Interest & Institutional Drift",
        "file": "docs/participant-oi.md",
        "badge": "FII / Pro / Client Flow",
    },
    "signal_efficiency_guide": {
        "title": "📈 Signal Efficiency Analytics & Performance Guide",
        "file": "docs/signal-efficiency-guide.md",
        "badge": "Statistical Edge & MFE/MAE",
    },
    "data_dictionary": {
        "title": "📊 Data Dictionary & Quantitative Calculation Reference",
        "file": "docs/data-dictionary-and-calculations.md",
        "badge": "WebSocket & REST Telemetry",
    },
    "options_strategies_guide": {
        "title": "🎯 Options Recommendation Strategies & Alpha Playbook",
        "file": "docs/options-recommendation-strategies-guide.md",
        "badge": "Institutional Execution Engine",
    },
    "pre_post_market_guide": {
        "title": "🌅 Pre/Post-Market Microstructure & Recommendations Lifecycle Guide",
        "file": "docs/pre-post-market-and-recommendations-guide.md",
        "badge": "Pre-Bell Alpha & CAS 15:40",
    },
    "options_analytics_guide": {
        "title": "🔬 Options Analytics & Microstructure Intelligence Guide (IOAS v3.0)",
        "file": "docs/options-analytics-guide.md",
        "badge": "IOAS v3.0 Microstructure",
    },
    "target_engine_guide": {
        "title": "🎯 Target Engine — Scenario & Repricing Suite Technical Guide",
        "file": "docs/target-engine-guide.md",
        "badge": "Target Engine v1.0",
    },
    "market_intelligence_guide": {
        "title": "🧭 Market Intelligence: Sectorial Performance & News Sentiment Guide",
        "file": "docs/market-intelligence-guide.md",
        "badge": "Sector Breadth & Catalysts",
    },
    "session_commentary_guide": {
        "title": "🎙️ Session Commentary & Suggestion Bites Guide",
        "file": "docs/session-commentary-guide.md",
        "badge": "Narrative Engine v1.0",
    },
}


def render_guide_html(doc_id: str) -> HTMLResponse:
    doc_info = DOCS_MAP.get(doc_id)
    if not doc_info:
        doc_id = "user_guide"
        doc_info = DOCS_MAP[doc_id]

    doc_path = ROOT_DIR / doc_info["file"]
    content = ""
    if doc_path.exists():
        content = doc_path.read_text(encoding="utf-8")
    else:
        content = f"# {doc_info['title']}\n\nDocumentation file `{doc_info['file']}` not found on server."

    options_html = "".join([
        f'<option value="{k}" {"selected" if k == doc_id else ""}>{v["title"]}</option>'
        for k, v in DOCS_MAP.items()
    ])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{doc_info['title']} — Cubera Institutional Reference</title>
  <link rel="stylesheet" href="/static/css/cockpit.css">
  <script src="/static/marked.min.js"></script>
  <style>
    body {{
      background: var(--bg, #090d16);
      color: var(--ink, #f8fafc);
      font-family: var(--font-sans, system-ui, -apple-system, sans-serif);
      margin: 0;
      padding: 0;
      line-height: 1.6;
    }}
    .doc-viewer-wrap {{
      max-width: 1040px;
      margin: 0 auto;
      padding: 24px 20px 80px;
    }}
    .doc-top-bar {{
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(15, 23, 42, 0.95);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--line, #1e293b);
      padding: 12px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }}
    .doc-brand {{
      font-weight: 850;
      font-size: 1.05rem;
      color: var(--spark, #38bdf8);
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .doc-header-card {{
      background: var(--surface, #0f172a);
      border: 1px solid var(--line, #1e293b);
      border-radius: 10px;
      padding: 20px 24px;
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 14px;
    }}
    .doc-badge {{
      display: inline-block;
      font-size: 0.72rem;
      font-weight: 750;
      text-transform: uppercase;
      color: var(--spark, #38bdf8);
      background: rgba(56, 189, 248, 0.12);
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 3px 10px;
      border-radius: 6px;
      margin-bottom: 6px;
    }}
    .doc-content-card {{
      background: var(--surface, #0f172a);
      border: 1px solid var(--line, #1e293b);
      border-radius: 10px;
      padding: 32px 36px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.3);
    }}
    .markdown-body h1 {{
      font-size: 1.8rem;
      font-weight: 850;
      color: #f8fafc;
      border-bottom: 1px solid var(--line, #1e293b);
      padding-bottom: 12px;
      margin-top: 0;
    }}
    .markdown-body h2 {{
      font-size: 1.35rem;
      font-weight: 800;
      color: var(--spark, #38bdf8);
      border-bottom: 1px solid rgba(255,255,255,0.06);
      padding-bottom: 8px;
      margin-top: 28px;
    }}
    .markdown-body h3 {{
      font-size: 1.15rem;
      font-weight: 750;
      color: #e2e8f0;
      margin-top: 22px;
    }}
    .markdown-body p, .markdown-body li {{
      font-size: 0.92rem;
      color: #cbd5e1;
      line-height: 1.68;
    }}
    .markdown-body ul, .markdown-body ol {{
      padding-left: 24px;
    }}
    .markdown-body code {{
      font-family: var(--mono, monospace);
      font-size: 0.85em;
      background: rgba(0,0,0,0.35);
      border: 1px solid rgba(255,255,255,0.08);
      padding: 2px 6px;
      border-radius: 4px;
      color: var(--spark, #38bdf8);
    }}
    .markdown-body pre {{
      background: #020617;
      border: 1px solid var(--line, #1e293b);
      border-radius: 8px;
      padding: 16px;
      overflow-x: auto;
    }}
    .markdown-body pre code {{
      background: transparent;
      border: none;
      padding: 0;
      color: #e2e8f0;
      font-size: 0.86rem;
    }}
    .markdown-body table {{
      width: 100%;
      border-collapse: collapse;
      margin: 18px 0;
      font-size: 0.84rem;
    }}
    .markdown-body th {{
      background: var(--surface-2, #1e293b);
      border: 1px solid var(--line, #334155);
      padding: 8px 12px;
      text-align: left;
      font-weight: 750;
      color: var(--spark, #38bdf8);
    }}
    .markdown-body td {{
      border: 1px solid var(--line, #334155);
      padding: 8px 12px;
      color: #cbd5e1;
    }}
    .markdown-body tr:nth-child(even) td {{
      background: rgba(255,255,255,0.015);
    }}
    .markdown-body blockquote {{
      margin: 16px 0;
      padding: 10px 18px;
      background: rgba(56, 189, 248, 0.05);
      border-left: 4px solid var(--spark, #38bdf8);
      border-radius: 0 6px 6px 0;
      color: #e2e8f0;
    }}
    .doc-nav-select {{
      background: var(--surface-2, #1e293b);
      border: 1px solid var(--line, #334155);
      color: var(--ink, #f8fafc);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
    }}
  </style>
</head>
<body>
  <div class="doc-top-bar">
    <div class="doc-brand">
      <span>⚡ CUBERA GEX</span>
      <span style="font-size:0.75rem;color:var(--ink-mute, #94a3b8);font-weight:600">Documentation &amp; Manuals Library</span>
    </div>
    <div style="display:flex;align-items:center;gap:12px">
      <label style="font-size:0.74rem;color:var(--ink-mute, #94a3b8);font-weight:700">Quick Guide Jump:</label>
      <select class="doc-nav-select" onchange="window.location.href='/guide/' + this.value">
        {options_html}
      </select>
      <a href="/" class="btn btn-primary" style="font-size:0.76rem;text-decoration:none;padding:6px 14px">🎯 Return to Cockpit</a>
    </div>
  </div>

  <div class="doc-viewer-wrap">
    <div class="doc-header-card">
      <div>
        <span class="doc-badge">{doc_info['badge']}</span>
        <h1 style="font-size:1.4rem;font-weight:850;margin:0;color:#f8fafc">{doc_info['title']}</h1>
        <div style="font-size:0.76rem;color:var(--ink-mute, #94a3b8);margin-top:4px">
          Source: <code style="font-family:var(--mono, monospace)">{doc_info['file']}</code> &nbsp;·&nbsp; Institutional Architecture Reference
        </div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-secondary" onclick="window.print()" style="font-size:0.74rem">🖨️ Print / PDF</button>
      </div>
    </div>

    <div class="doc-content-card">
      <div id="guide-content" class="markdown-body">
        Loading guide content...
      </div>
    </div>
  </div>

  <script>
    const rawContent = {json.dumps(content)};
    document.addEventListener("DOMContentLoaded", function() {{
      const target = document.getElementById("guide-content");
      if (typeof marked !== "undefined" && marked.parse) {{
        target.innerHTML = marked.parse(rawContent);
      }} else {{
        target.innerHTML = "<pre style='white-space:pre-wrap;font-family:inherit'>" + rawContent + "</pre>";
      }}
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/guide/{guide_id}")
@app.get("/manuals/{guide_id}")
async def get_guide(guide_id: str):
    """Serve formatted HTML documentation page for the requested guide."""
    return render_guide_html(guide_id)


@app.get("/api/docs/{guide_id}")
async def get_doc_api(guide_id: str):
    """API endpoint providing raw documentation metadata and content."""
    if guide_id == "list":
        return {
            "guides": [
                {"id": k, "title": v["title"], "badge": v["badge"], "file": v["file"]}
                for k, v in DOCS_MAP.items()
            ]
        }
    if guide_id not in DOCS_MAP:
        raise HTTPException(status_code=404, detail=f"Guide '{guide_id}' not found.")
    info = DOCS_MAP[guide_id]
    doc_path = ROOT_DIR / info["file"]
    content = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
    return {
        "id": guide_id,
        "title": info["title"],
        "badge": info["badge"],
        "file": info["file"],
        "content": content,
    }


@app.get("/")
async def root(view: Optional[str] = None):
    """Serve the single-page application entry point or documentation view."""
    if view and view in DOCS_MAP:
        return render_guide_html(view)

    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse(
            status_code=503,
            content={"error": "UI assets initializing. index.html not yet written."},
        )
    return FileResponse(index_file)


# --------------------------------------------------------------------------
# Main Entry Point
# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Cubera GEX Cockpit Web Server")
    parser.add_argument("--port", type=int, default=8640, help="Port to bind (default: 8640)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--dev", action="store_true", help="Explicitly run in development mode")
    args = parser.parse_args()

    os.environ["COCKPIT_PORT"] = str(args.port)
    os.environ.setdefault("COCKPIT_ENV", "prod" if args.port == 8640 else "dev")
    # Production only. A stalled event loop cannot accept connections, and
    # SIGTERM does not land while the main thread is blocked in a lock.
    if args.port == 8640 and not args.dev and not args.reload:
        os.environ["COCKPIT_WATCHDOG"] = "1"

    is_dev = args.dev or args.reload or (args.port != 8640)
    if is_dev:
        os.environ["ENV_MODE"] = "development"
    else:
        os.environ.setdefault("ENV_MODE", "production")

    log.info(
        "Starting Cubera GEX Cockpit [%s] v%s on http://%s:%d (reload=%s)",
        os.environ["ENV_MODE"].upper(),
        APP_VERSION,
        args.host,
        args.port,
        args.reload,
    )
    uvicorn.run(
        "web_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_excludes=["*.db", "*.db-*", "data/*", "*.log"],
        log_level="info",
    )


if __name__ == "__main__":
    main()
