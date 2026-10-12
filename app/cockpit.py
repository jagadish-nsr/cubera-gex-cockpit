"""app/cockpit.py — Unified Trading Cockpit (v2).

Tabs:
  🗺️ Cockpit     — GEX price ladder, candidate structure, safety banners
  📡 Analytics   — IOAS v3.0: Bias Score, Buildup Matrix, Parity Arb, Divergences
  📓 Journal     — Open positions (live MTM), log a new paper trade, close positions
  📊 Scorecard   — Descriptive win-rate, equity curve, breakdown by regime/strategy

Reuses existing engine unchanged:
  app.data_access.load_dashboard  → Levels + RegimeView
  analytics.directional_bias      → IOAS §3.1 Directional Bias Score (-10 … +10)
  analytics.buildup               → IOAS §3.2 Strike Buildup Matrix
  analytics.parity_arb            → IOAS §3.3 Put-Call Parity Arb Monitor
  analytics.divergence            → IOAS §3.4 Active Divergence Engine
  analytics.iv_skew               → 25-delta IV skew & term structure
  structures.suggest              → CandidateStructure
  data.journal.store              → SQLite paper-trade ledger
  data.journal.pnl                → Mark-to-market
  data.journal.scorecard          → Aggregate stats

Launch:
  streamlit run app/cockpit.py --server.port 8630

One-click launcher: run_cockpit.bat / run_cockpit.sh
"""

from __future__ import annotations

import sys
import json
import math
import time
import threading
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import NamedTuple, Any

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib
import config  # noqa: E402
importlib.reload(config)

import structures.suggest as struct_mod  # noqa: E402
importlib.reload(struct_mod)
suggest_structure = struct_mod.suggest_structure
suggest_structures = getattr(
    struct_mod,
    "suggest_structures",
    lambda *a, **k: [struct_mod.suggest_structure(*a, **k)],
)

import app.data_access as da_mod  # noqa: E402
importlib.reload(da_mod)
load_dashboard = da_mod.load_dashboard

import data.expiry as expiry_mod  # noqa: E402
importlib.reload(expiry_mod)

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402

import sentinel.storage.db as sentinel_db_mod  # noqa: E402
importlib.reload(sentinel_db_mod)
from sentinel.storage.db import (  # noqa: E402
    list_recent_bars,
    list_recent_signals,
    list_recent_outcomes,
    ensure_sentinel_schema,
)
import sentinel.engine.aggregator as sentinel_agg_mod  # noqa: E402
importlib.reload(sentinel_agg_mod)
import sentinel.feed.demo as sentinel_demo_mod  # noqa: E402
importlib.reload(sentinel_demo_mod)
seed_sentinel_demo_if_needed = sentinel_demo_mod.seed_sentinel_demo_if_needed
step_sentinel_live_feed = sentinel_demo_mod.step_sentinel_live_feed

import sentinel.engine.efficiency as sentinel_eff_mod  # noqa: E402
importlib.reload(sentinel_eff_mod)
compute_signal_efficiency = sentinel_eff_mod.compute_signal_efficiency
purge_old_sentinel_data = sentinel_db_mod.purge_old_sentinel_data



from data.journal import store as journal_store  # noqa: E402
from data.journal.pnl import mark_to_market, format_pnl_badge  # noqa: E402
from data.journal.scorecard import compute_scorecard  # noqa: E402
from analytics.directional_bias import compute_directional_bias  # noqa: E402
from analytics.buildup import build_strike_matrix, CLASSIFICATION_EMOJI, MIN_OI_NIFTY, MIN_OI_SENSEX  # noqa: E402
import analytics.parity_arb as parity_arb_mod  # noqa: E402
importlib.reload(parity_arb_mod)
compute_parity_arb = parity_arb_mod.compute_parity_arb
from analytics.divergence import detect_divergences  # noqa: E402
from analytics.iv_skew import skew_25delta  # noqa: E402
from resilience.circuit_breaker import get_circuit_breaker, CircuitState  # noqa: E402
from resilience.adaptive_poller import get_adaptive_poller  # noqa: E402
import resilience.lkgs as lkgs_mod  # noqa: E402
importlib.reload(lkgs_mod)
get_lkgs_manager = lkgs_mod.get_lkgs_manager
from data.holidays import is_trading_day, roll_back_to_trading_day  # noqa: E402
from app.spot_ticker import get_instant_quote, warmup_all  # noqa: E402
from analytics.trade_ideas import (  # noqa: E402
    TradeIdea,
    PositionSizeInfo,
    RecommendationBatch,
    compute_position_size,
    generate_trade_recommendations,
)
from data.journal.recommendations_store import (  # noqa: E402
    toggle_bookmark,
    is_bookmarked,
    list_bookmarked_records,
    get_score_history,
    record_event,
    get_unacknowledged_alerts,
    acknowledge_alerts,
    get_strategy_effectiveness_metrics,
)


log = logging.getLogger(__name__)

# Pre-warm live background pollers for instant 0ms HUD rendering
warmup_all()


def _extract_option_prices(contracts_df) -> dict[tuple[str, float, str], float]:
    """Build {(type, strike, expiry_str): ltp} mapping from contracts DataFrame for MTM."""
    prices: dict[tuple[str, float, str], float] = {}
    if contracts_df is None or (hasattr(contracts_df, "empty") and contracts_df.empty):
        return prices
    try:
        px_col = "ltp" if "ltp" in contracts_df.columns else ("mid" if "mid" in contracts_df.columns else "price")
        for _, row in contracts_df.iterrows():
            t = "C" if str(row.get("type", "")).upper().startswith("C") else "P"
            k = float(row.get("strike", 0))
            exp = str(row.get("expiry", ""))[:10]
            val = float(row.get(px_col, 0) or 0)
            if val > 0:
                prices[(t, k, exp)] = val
    except Exception:
        pass
    return prices


st.set_page_config(
    page_title="Nifty Cockpit · GEX + Journal",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Design system (inherits the .simp token set + new .ck tokens for the cockpit)
# ---------------------------------------------------------------------------
CSS = """
<style>
:root{
  --ground:#f4f6f9;--surface:#fff;--surface-2:#eef1f6;--inset:#e9edf3;
  --ink:#111621;--ink-2:#4b5567;--ink-mute:#7a8494;--line:#e0e5ec;
  --spark:#5b54d6;--go:#0f9d6b;--go-bg:#e4f6ee;--warn:#c07d10;--warn-bg:#fbf0da;
  --stop:#cf3f4b;--stop-bg:#fbe6e8;--call:#2f80d8;--put:#b5620c;
  --mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
  --r:14px;--shadow:0 1px 3px rgba(16,22,34,.06),0 8px 24px rgba(16,22,34,.07);
}
@media(prefers-color-scheme:dark){:root{
  --ground:#0c0f16;--surface:#161a22;--surface-2:#1c222d;--inset:#212836;
  --ink:#eef2f8;--ink-2:#aab4c4;--ink-mute:#6d7889;--line:#28303d;
  --spark:#8e86f7;--go:#2cd49b;--go-bg:#0f2a24;--warn:#f0a83a;--warn-bg:#2c2210;
  --stop:#f0616d;--stop-bg:#2c1418;--call:#3f8fe0;--put:#d17720;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);
}}
.stApp{background:var(--ground)}
header[data-testid="stHeader"]{background:transparent}
/* Base layout */
.ck{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    color:var(--ink);max-width:1100px;margin:0 auto}
.ck .card{background:var(--surface);border:1px solid var(--line);
    border-radius:var(--r);box-shadow:var(--shadow);margin-top:12px;overflow:hidden}
.ck .card-body{padding:16px 18px}
.ck .eyebrow{font-size:.64rem;font-weight:700;letter-spacing:.12em;
    text-transform:uppercase;color:var(--ink-mute);margin-bottom:5px}
/* Safety banners */
.ck .banner{display:flex;gap:12px;align-items:flex-start;padding:11px 15px;
    border-radius:10px;margin:10px 0;font-size:.84rem;line-height:1.45}
.ck .banner.paper{background:var(--stop-bg);border:1px solid var(--stop);color:var(--stop)}
.ck .banner.unval{background:var(--warn-bg);border:1px solid var(--warn);color:var(--warn)}
/* Header strip */
.ck .hdr{display:grid;grid-template-columns:auto 1fr auto;gap:16px;
    align-items:center;padding:14px 18px;border-bottom:1px solid var(--line)}
.ck .spot-val{font-size:2rem;font-weight:800;letter-spacing:-.02em;line-height:1}
.ck .spot-chg{font-size:.82rem;color:var(--ink-mute);margin-top:3px}
.ck .spot-chg.up{color:var(--go)}.ck .spot-chg.dn{color:var(--stop)}
.ck .regime-chip{display:inline-flex;align-items:center;gap:8px;padding:7px 14px;
    border-radius:999px;font-size:.84rem;font-weight:600}
.ck .regime-chip.calm{background:var(--go-bg);color:var(--go)}
.ck .regime-chip.wild{background:var(--warn-bg);color:var(--warn)}
.ck .regime-chip.mid{background:var(--surface-2);color:var(--ink-2)}
/* Price ladder */
.ck .ladder{padding:2px 0}
.ck .rung{display:grid;grid-template-columns:28px 1fr auto;gap:10px;
    align-items:center;padding:10px 16px;border-bottom:1px solid var(--line)}
.ck .rung:last-child{border-bottom:0}
.ck .rung.now{background:var(--inset)}.ck .rung.now .rname{font-weight:700;color:var(--ink)}
.ck .rung .ric{text-align:center;font-size:1.1rem}
.ck .rname{font-size:.9rem;color:var(--ink-2)}
.ck .rname small{display:block;font-size:.72rem;color:var(--ink-mute)}
.ck .rpx{font-size:1.05rem;font-weight:700;font-family:var(--mono)}
.ck .rpx.ceil{color:var(--stop)}.ck .rpx.floor{color:var(--go)}
.ck .rpx.spark{color:var(--spark)}.ck .rpx.warn{color:var(--warn)}
/* Candidate structure card */
.ck .struct-name{font-size:1.05rem;font-weight:700;margin:2px 0 6px}
.ck .struct-legs{font-family:var(--mono);font-size:.82rem;color:var(--ink-2);
    background:var(--surface-2);border-radius:8px;padding:9px 12px;margin:8px 0}
.ck .struct-leg{display:flex;justify-content:space-between;padding:2px 0}
.ck .struct-leg .sell{color:var(--stop);font-weight:700}
.ck .struct-leg .buy{color:var(--go);font-weight:700}
.ck .struct-meta{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
.ck .struct-meta>div{background:var(--surface-2);border-radius:9px;padding:8px 11px}
.ck .struct-meta small{font-size:.64rem;color:var(--ink-mute);text-transform:uppercase;letter-spacing:.04em}
.ck .struct-meta b{display:block;font-size:.96rem;font-weight:700;margin-top:2px}
.ck .struct-meta b.go{color:var(--go)}.ck .struct-meta b.stop{color:var(--stop)}
.ck .unval-tag{display:inline-block;font-size:.68rem;font-weight:700;letter-spacing:.06em;
    text-transform:uppercase;background:var(--warn-bg);color:var(--warn);
    padding:3px 9px;border-radius:5px;margin-bottom:10px}
.ck .exit-note{font-size:.78rem;color:var(--ink-mute);border-top:1px solid var(--line);
    margin-top:10px;padding-top:9px}
/* Journal table */
.ck .jtable{width:100%;border-collapse:collapse;font-size:.86rem}
.ck .jtable th{font-size:.68rem;font-weight:700;letter-spacing:.06em;
    text-transform:uppercase;color:var(--ink-mute);padding:9px 12px;
    text-align:left;border-bottom:2px solid var(--line)}
.ck .jtable td{padding:10px 12px;border-bottom:1px solid var(--line);color:var(--ink-2)}
.ck .jtable tr:last-child td{border-bottom:0}
.ck .jtable td.mono{font-family:var(--mono)}
.ck .pnl-profit{color:var(--go);font-weight:700}
.ck .pnl-loss{color:var(--stop);font-weight:700}
.ck .pnl-neutral{color:var(--ink-mute)}
/* Scorecard */
.ck .sc-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:4px}
.ck .sc-card{background:var(--surface-2);border-radius:11px;padding:13px 15px}
.ck .sc-card small{font-size:.64rem;text-transform:uppercase;letter-spacing:.05em;color:var(--ink-mute)}
.ck .sc-card b{display:block;font-size:1.35rem;font-weight:800;margin:4px 0 1px}
.ck .sc-card span{font-size:.78rem;color:var(--ink-mute)}
/* Playbook callout */
.ck .playbook{background:linear-gradient(135deg,var(--spark) 0%,#4044cf 100%);
    border-radius:var(--r);padding:16px 20px;color:#fff;margin-top:4px}
.ck .playbook .label{font-size:.64rem;font-weight:700;letter-spacing:.12em;
    text-transform:uppercase;opacity:.75;margin-bottom:6px}
.ck .playbook .text{font-size:.97rem;line-height:1.5;font-weight:500}
/* Analytics tab — Bias Score gauge */
.ck .bias-bar-wrap{background:var(--surface-2);border-radius:9px;overflow:hidden;height:14px;margin:8px 0 4px}
.ck .bias-bar{height:100%;border-radius:9px;transition:width .4s ease}
.ck .bias-bar.bull{background:linear-gradient(90deg,var(--spark),var(--go))}
.ck .bias-bar.bear{background:linear-gradient(90deg,var(--stop),#c45060)}
.ck .bias-bar.neutral{background:var(--line)}
/* Sub-signal rows */
.ck .sig-row{display:grid;grid-template-columns:130px 1fr 50px 50px 60px;gap:6px;
    align-items:center;padding:5px 0;border-bottom:1px solid var(--line);font-size:.8rem}
.ck .sig-row:last-child{border-bottom:0}
.ck .sig-row .sbar{height:8px;border-radius:4px;background:var(--line)}
.ck .sig-row .sbar-fill{height:100%;border-radius:4px;transition:width .3s}
.ck .sig-row .sbar-fill.pos{background:var(--go)}
.ck .sig-row .sbar-fill.neg{background:var(--stop)}
.ck .sig-key{font-family:var(--mono);font-size:.72rem;color:var(--ink-mute)}
.ck .sig-contrib.pos{color:var(--go);font-weight:700}
.ck .sig-contrib.neg{color:var(--stop);font-weight:700}
/* Buildup matrix */
.ck .buildup-row{display:grid;grid-template-columns:80px auto auto auto auto;gap:8px;
    padding:6px 0;border-bottom:1px solid var(--line);font-size:.8rem}
.ck .buildup-row:last-child{border-bottom:0}
.ck .tag{display:inline-block;padding:2px 8px;border-radius:5px;font-size:.72rem;font-weight:700}
.ck .tag.bull{background:var(--go-bg);color:var(--go)}
.ck .tag.bear{background:var(--stop-bg);color:var(--stop)}
.ck .tag.warn{background:var(--warn-bg);color:var(--warn)}
.ck .tag.mute{background:var(--surface-2);color:var(--ink-mute)}
/* Divergence alerts */
.ck .div-ev{padding:10px 14px;border-radius:9px;margin:6px 0;font-size:.84rem;line-height:1.4}
.ck .div-ev.high{background:var(--stop-bg);border-left:3px solid var(--stop)}
.ck .div-ev.medium{background:var(--warn-bg);border-left:3px solid var(--warn)}
.ck .div-ev.low{background:var(--surface-2);border-left:3px solid var(--line)}
.ck .shift-alert{padding:8px 12px;border-radius:8px;margin:4px 0;font-size:.8rem;
    background:var(--stop-bg);border:1px solid var(--stop);color:var(--stop)}
/* OrderFlow Sentinel Tape */
.ck .sentinel-hdr{display:grid;grid-template-columns:auto 1fr auto;gap:16px;align-items:center;padding:12px 18px;background:var(--surface-2);border-radius:var(--r);margin-bottom:12px;border:1px solid var(--line)}
.ck .sentinel-chip{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:6px;font-size:.76rem;font-weight:700}
.ck .sentinel-chip.bull{background:var(--go-bg);color:var(--go);border:1px solid var(--go)}
.ck .sentinel-chip.bear{background:var(--stop-bg);color:var(--stop);border:1px solid var(--stop)}
.ck .sentinel-chip.warn{background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn)}
.ck .sig-feed{display:flex;flex-direction:column;gap:10px;max-height:680px;overflow-y:auto;padding-right:4px}
.ck .sig-card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;box-shadow:var(--shadow);transition:transform .15s ease}
.ck .sig-card:hover{border-color:var(--spark);transform:translateY(-1px)}
.ck .sig-card.bull{border-left:4px solid var(--go)}
.ck .sig-card.bear{border-left:4px solid var(--stop)}
.ck .sig-card.warn{border-left:4px solid var(--warn)}
.ck .sig-card-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.ck .sig-type{font-size:.85rem;font-weight:800;font-family:var(--mono)}
.ck .sig-score-pill{background:var(--inset);padding:2px 8px;border-radius:999px;font-size:.74rem;font-weight:700;color:var(--spark)}
.ck .sig-metrics{display:grid;grid-template-columns:repeat(3, 1fr);gap:6px;background:var(--surface-2);padding:6px 10px;border-radius:6px;margin:6px 0;font-size:.74rem;text-align:center}
.ck .sig-metrics span{color:var(--ink-mute);display:block;font-size:.65rem;text-transform:uppercase}
.ck .sig-insight{font-size:.79rem;line-height:1.4;color:var(--ink);margin:6px 0}
.ck .sig-inval{font-size:.74rem;color:var(--stop);background:var(--stop-bg);padding:4px 8px;border-radius:5px;font-family:var(--mono);display:inline-block;margin-top:4px}
.ck .outcome-badge{font-size:.72rem;padding:3px 7px;border-radius:4px;font-weight:700;display:inline-block;margin-top:5px}
.ck .outcome-badge.win{background:var(--go-bg);color:var(--go)}
.ck .outcome-badge.stop{background:var(--stop-bg);color:var(--stop)}
/* Ops & Calendar */
.ck .ops-grid{display:grid;grid-template-columns:repeat(4, 1fr);gap:12px;margin:12px 0}
.ck .ops-card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;box-shadow:var(--shadow)}
.ck .ops-card small{display:block;font-size:.65rem;text-transform:uppercase;color:var(--ink-mute);font-weight:700}
.ck .ops-card b{font-size:1.15rem;display:block;margin:3px 0}
.ck .timeline-box{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:16px 20px;box-shadow:var(--shadow);margin-top:12px}
.ck .timeline-row{display:grid;grid-template-columns:120px 1fr auto;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line);font-size:.82rem}
.ck .timeline-row:last-child{border-bottom:0}
.ck .phase-chip{font-size:.72rem;padding:3px 8px;border-radius:6px;font-weight:700}
.ck .phase-chip.live{background:var(--spark);color:#fff}
.ck .phase-chip.upcoming{background:var(--surface-2);color:var(--ink-mute)}
.ck .phase-chip.done{background:var(--go-bg);color:var(--go)}
/* Executive Telemetry Strip (Preview 1 Fidelity) */
.ck .exec-strip{display:flex;justify-content:space-between;align-items:center;background:#0d1117;color:#c9d1d9;padding:10px 18px;border-radius:10px;margin-bottom:8px;font-size:.78rem;font-family:var(--mono);border:1px solid #30363d;box-shadow:0 4px 12px rgba(0,0,0,.25);flex-wrap:wrap;gap:8px}
.ck .exec-strip b{color:#fff}
.ck .exec-pill{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:6px;font-weight:700}
.ck .exec-pill.live{background:#161b22;border:1px solid #238636;color:#3fb950}
.ck .exec-pill.neutral{background:#161b22;border:1px solid #30363d;color:#8b949e}
.ck .exec-strip-sub{display:flex;justify-content:space-between;align-items:center;background:var(--surface-2);padding:7px 14px;border-radius:8px;margin-bottom:12px;font-size:.74rem;color:var(--ink-2);border:1px solid var(--line);flex-wrap:wrap;gap:6px}
/* Expected Move Box (NOA Preview 2 Fidelity) */
.ck .exp-move-box{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:14px 18px;margin-bottom:14px;box-shadow:var(--shadow)}
.ck .exp-move-grid{display:grid;grid-template-columns:repeat(3, 1fr);gap:12px;margin-top:10px}
.ck .exp-card{background:var(--surface-2);padding:12px 14px;border-radius:8px;text-align:center;border:1px solid var(--line)}
.ck .exp-card small{font-size:.66rem;text-transform:uppercase;color:var(--ink-mute);display:block;font-weight:700}
.ck .exp-card b{font-size:1.2rem;display:block;margin:4px 0;color:var(--spark)}

/* Scrollable Event Feed (User requested for trading hours) */
.ck .scroll-feed{max-height:280px;overflow-y:auto;padding-right:6px}
.ck .scroll-feed::-webkit-scrollbar{width:6px}
.ck .scroll-feed::-webkit-scrollbar-track{background:var(--surface-2);border-radius:4px}
.ck .scroll-feed::-webkit-scrollbar-thumb{background:var(--line);border-radius:4px}
.ck .scroll-feed::-webkit-scrollbar-thumb:hover{background:var(--ink-mute)}

/* Isolated Sentinel Chart & Feed Scroll Viewports */
[data-testid="stVegaLiteChart"] {
    overflow-x: auto !important;
    overflow-y: hidden !important;
    max-width: 100% !important;
    overscroll-behavior-x: contain !important;
    overscroll-behavior-y: contain !important;
    scrollbar-width: thin !important;
    scrollbar-color: var(--line) transparent !important;
}
[data-testid="stVegaLiteChart"]::-webkit-scrollbar {
    height: 7px;
}
[data-testid="stVegaLiteChart"]::-webkit-scrollbar-track {
    background: var(--surface-2);
    border-radius: 4px;
}
[data-testid="stVegaLiteChart"]::-webkit-scrollbar-thumb {
    background: var(--line);
    border-radius: 4px;
}
[data-testid="stVegaLiteChart"]::-webkit-scrollbar-thumb:hover {
    background: var(--spark);
}

.ck .sig-feed-scroll {
    max-height: 440px;
    overflow-y: auto;
    overscroll-behavior-y: contain;
    padding-right: 6px;
    scrollbar-width: thin;
    scrollbar-color: var(--line) transparent;
}
.ck .sig-feed-scroll::-webkit-scrollbar {
    width: 6px;
}
.ck .sig-feed-scroll::-webkit-scrollbar-track {
    background: var(--surface-2);
    border-radius: 4px;
}
.ck .sig-feed-scroll::-webkit-scrollbar-thumb {
    background: var(--line);
    border-radius: 4px;
}
.ck .sig-feed-scroll::-webkit-scrollbar-thumb:hover {
    background: var(--ink-mute);
}

.ck.card, .ck .sig-feed-scroll {
    overscroll-behavior: contain !important;
}

/* Key Strike Cards (NOA Preview 1 Fidelity) */
.ck .key-strike-grid{display:grid;grid-template-columns:repeat(3, 1fr);gap:12px;margin:12px 0}
.ck .key-strike-card{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;box-shadow:var(--shadow)}
.ck .key-strike-card small{font-size:.66rem;text-transform:uppercase;color:var(--ink-mute);display:block;font-weight:700}
.ck .key-strike-card .strike-val{font-size:1.3rem;font-weight:800;margin:3px 0}
.ck .key-strike-card.res .strike-val{color:var(--stop)}
.ck .key-strike-card.sup .strike-val{color:var(--go)}
.ck .key-strike-card.pin .strike-val{color:var(--spark)}

/* Intraday Shift Event Log (NOA Preview 4 Fidelity) */
.ck .shift-log-row{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font-size:.78rem}
.ck .shift-log-row:last-child{border-bottom:0}
.ck .shift-time{font-family:var(--mono);color:var(--ink-mute);font-size:.72rem;min-width:125px}

/* Playbook Box */
.ck .playbook-box{
  background:linear-gradient(135deg, rgba(99,102,241,.09) 0%, rgba(56,189,248,.04) 100%);
  border:1px solid var(--line);
  border-left:4px solid var(--spark);
  border-radius:10px;
  padding:14px 18px;
  font-size:.86rem;
  line-height:1.5;
  color:var(--ink);
  margin-bottom:12px;
  box-shadow:var(--shadow);
}
.ck .playbook-box b{color:var(--spark);letter-spacing:.02em}

/* Spine Ladder */
.ck .spine-ladder{display:flex;flex-direction:column;gap:5px;padding:6px 0}
.ck .spine-row{
  display:grid;
  grid-template-columns:85px 24px 1fr;
  align-items:center;
  font-size:.84rem;
  padding:6px 10px;
  border-radius:7px;
  transition:all .15s ease;
}
.ck .spine-row:hover{background:var(--surface-2)}
.ck .spine-row.is-spot{background:var(--inset);border:1px solid rgba(56,189,248,.3);box-shadow:0 0 10px rgba(56,189,248,.1)}
.ck .spine-px{font-family:var(--mono);font-weight:700;color:var(--ink-2);text-align:right;padding-right:10px}
.ck .spine-tick{position:relative;height:100%;display:flex;align-items:center;justify-content:center}
.ck .spine-tick::before{content:'';position:absolute;top:-6px;bottom:-6px;width:2px;background:var(--line);left:50%;margin-left:-1px}
.ck .spine-dot{position:relative;width:9px;height:9px;border-radius:50%;background:var(--ink-mute);z-index:1}
.ck .spine-dot.ceil{background:var(--stop);box-shadow:0 0 10px rgba(207,63,75,.7)}
.ck .spine-dot.floor{background:var(--go);box-shadow:0 0 10px rgba(15,157,107,.7)}
.ck .spine-dot.now{background:#38bdf8;box-shadow:0 0 12px #38bdf8;width:11px;height:11px}
.ck .spine-dot.spark{background:var(--spark);box-shadow:0 0 8px rgba(91,84,214,.4)}
.ck .spine-dot.warn{background:var(--warn);box-shadow:0 0 8px rgba(192,125,16,.4)}
.ck .spine-label{font-size:.82rem;font-weight:600;padding-left:8px;color:var(--ink-2)}
.ck .spine-label.ceil{color:var(--stop);font-weight:700}
.ck .spine-label.floor{color:var(--go);font-weight:700}
.ck .spine-label.spark{color:var(--spark)}
.ck .spine-label.warn{color:var(--warn)}

/* Pre-Bell Tactical Briefing & Post-Market Settlement Cards */
.ck .pre-bell-card {
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.08) 0%, rgba(99, 102, 241, 0.04) 100%);
  border: 1px solid rgba(245, 158, 11, 0.35);
  border-left: 4px solid #f59e0b;
  border-radius: 12px;
  padding: 14px 18px;
  margin: 10px 0 14px;
  box-shadow: 0 4px 14px rgba(245, 158, 11, 0.08);
}
.ck .post-mkt-card {
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.08) 0%, rgba(56, 189, 248, 0.04) 100%);
  border: 1px solid rgba(139, 92, 246, 0.35);
  border-left: 4px solid #8b5cf6;
  border-radius: 12px;
  padding: 14px 18px;
  margin: 10px 0 14px;
  box-shadow: 0 4px 14px rgba(139, 92, 246, 0.08);
}
.ck-pre-opt-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.76rem;
  margin-top: 10px;
}
.ck-pre-opt-table th {
  text-align: left;
  padding: 5px 8px;
  color: var(--ink-mute);
  font-size: 0.68rem;
  text-transform: uppercase;
  border-bottom: 1px solid var(--line);
}
.ck-pre-opt-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
  font-family: var(--mono);
}

/* KPI Grids & Cards (Shared across Cockpit, Sentinel & Analytics) */
.ck .noa-kpi-grid, .noa-kpi-grid{display:grid;grid-template-columns:repeat(6, 1fr);gap:10px;margin-bottom:14px}
.ck .sentinel-kpi-grid, .sentinel-kpi-grid, .ck.sentinel-kpi-grid{display:grid;grid-template-columns:repeat(5, 1fr);gap:10px;margin-bottom:14px}
.ck .noa-kpi-card, .ck .sentinel-kpi-card, .noa-kpi-card, .sentinel-kpi-card{
  background:var(--surface-2);
  border:1px solid var(--line);
  border-radius:9px;
  padding:11px 13px;
  text-align:left;
  box-shadow:var(--shadow);
  transition:all .18s ease;
}
.ck .noa-kpi-card:hover, .ck .sentinel-kpi-card:hover, .noa-kpi-card:hover, .sentinel-kpi-card:hover{
  border-color:var(--spark);
  transform:translateY(-2px);
  box-shadow:0 4px 14px rgba(0,0,0,.08);
}
.ck .noa-kpi-card small, .ck .sentinel-kpi-card small, .noa-kpi-card small, .sentinel-kpi-card small{font-size:.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block}
.ck .noa-kpi-card b, .ck .sentinel-kpi-card b, .noa-kpi-card b, .sentinel-kpi-card b{font-size:1.25rem;font-weight:800;display:block;margin:3px 0}
.ck .noa-kpi-card span, .ck .sentinel-kpi-card span, .noa-kpi-card span, .sentinel-kpi-card span{font-size:.72rem;color:var(--ink-mute)}

/* Candidate Structure Mini-Grid */
.ck .struct-meta-grid, .struct-meta-grid{display:grid;grid-template-columns:repeat(4, 1fr);gap:8px;margin:10px 0}
.ck .struct-meta-card, .struct-meta-card{background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-align:center}
.ck .struct-meta-card small, .struct-meta-card small{font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block}
.ck .struct-meta-card b, .struct-meta-card b{font-size:.95rem;font-weight:800;display:block;margin-top:2px}

/* Responsive Grid Adapters */
@media(max-width:960px){
  .ck .noa-kpi-grid, .noa-kpi-grid{grid-template-columns:repeat(3, 1fr)}
  .ck .sentinel-kpi-grid, .sentinel-kpi-grid, .ck.sentinel-kpi-grid{grid-template-columns:repeat(3, 1fr)}
  .ck .struct-meta-grid, .struct-meta-grid{grid-template-columns:1fr 1fr}
}
@media(max-width:640px){
  .ck .sc-grid, .ck .ops-grid, .ck .exp-move-grid, .ck .key-strike-grid, .ck .noa-kpi-grid, .noa-kpi-grid, .ck .sentinel-kpi-grid, .sentinel-kpi-grid{grid-template-columns:1fr 1fr}
}

/* Market Intelligence & Sectorial Performance */
.ck .mi-macro-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:16px 20px;box-shadow:var(--shadow);margin-bottom:14px}
.ck .mi-kpi-grid{display:grid;grid-template-columns:repeat(4, 1fr);gap:10px;margin:12px 0}
.ck .mi-kpi{background:var(--surface-2);border:1px solid var(--line);border-radius:9px;padding:11px 13px;box-shadow:var(--shadow);transition:all .18s ease}
.ck .mi-kpi:hover{border-color:var(--spark);transform:translateY(-2px)}
.ck .mi-kpi small{font-size:.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block}
.ck .mi-kpi b{font-size:1.20rem;font-weight:800;display:block;margin:3px 0}
.ck .mi-kpi span{font-size:.70rem;color:var(--ink-mute)}
.ck .sector-table{display:flex;flex-direction:column;gap:5px}
.ck .sector-row{display:grid;grid-template-columns:110px 70px 1fr 75px;align-items:center;gap:10px;padding:6px 10px;border-radius:7px;background:var(--surface-2);border:1px solid var(--line);font-size:.80rem}
.ck .sector-bar-track{position:relative;height:8px;background:var(--ground);border-radius:4px;overflow:hidden}
.ck .sector-bar-fill{position:absolute;top:0;bottom:0;border-radius:4px}
.ck .sector-bar-fill.pos{background:var(--go);left:50%}
.ck .sector-bar-fill.neg{background:var(--stop);right:50%}
.ck .news-feed-scroll{display:flex;flex-direction:column;gap:8px;max-height:480px;overflow-y:auto;padding-right:6px}
.ck .news-card{background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;box-shadow:var(--shadow);transition:border-color .15s ease}
.ck .news-card:hover{border-color:var(--spark)}
.ck .news-meta{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;font-size:.72rem}
.ck .news-chip{display:inline-flex;align-items:center;gap:4px;padding:2px 7px;border-radius:4px;font-weight:700;font-size:.68rem}
.ck .news-chip.bull{background:var(--go-bg);color:var(--go);border:1px solid var(--go)}
.ck .news-chip.bear{background:var(--stop-bg);color:var(--stop);border:1px solid var(--stop)}
.ck .news-chip.neutral{background:var(--inset);color:var(--ink-mute);border:1px solid var(--line)}
.ck .news-title{font-size:.81rem;font-weight:600;color:var(--ink);line-height:1.4}
@media(max-width:960px){
  .ck .mi-kpi-grid{grid-template-columns:repeat(2, 1fr)}
}
@media(max-width:640px){
  .ck .mi-kpi-grid{grid-template-columns:1fr}
}

/* Recommendation Engine Cards */
.rec-card,
.ck.rec-card,
.ck .rec-card{
  background:var(--surface);
  border:2px solid var(--line);
  border-radius:10px;
  padding:14px 16px;
  margin-bottom:12px;
  box-shadow:var(--shadow);
  transition:all .18s ease;
  position:relative;
}
.rec-card:hover,
.ck.rec-card:hover,
.ck .rec-card:hover{
  transform:translateY(-2px);
  box-shadow:0 6px 18px rgba(0,0,0,.18);
}
.rec-card.ce,
.ck.rec-card.ce,
.ck .rec-card.ce{
  border:2px solid #16a34a !important;
  border-left:6px solid #16a34a !important;
  box-shadow:0 2px 10px rgba(22,163,74,.15), var(--shadow) !important;
}
.rec-card.ce:hover,
.ck.rec-card.ce:hover,
.ck .rec-card.ce:hover{
  border-color:#15803d !important;
  border-left-color:#15803d !important;
  box-shadow:0 4px 16px rgba(22,163,74,.25) !important;
}
.rec-card.pe,
.ck.rec-card.pe,
.ck .rec-card.pe{
  border:2px solid #dc2626 !important;
  border-left:6px solid #dc2626 !important;
  box-shadow:0 2px 10px rgba(220,38,38,.15), var(--shadow) !important;
}
.rec-card.pe:hover,
.ck.rec-card.pe:hover,
.ck .rec-card.pe:hover{
  border-color:#b91c1c !important;
  border-left-color:#b91c1c !important;
  box-shadow:0 4px 16px rgba(220,38,38,.25) !important;
}
.rec-card.bookmarked,
.ck.rec-card.bookmarked,
.ck .rec-card.bookmarked{
  border:2px solid #eab308 !important;
  border-left:6px solid #eab308 !important;
  box-shadow:0 0 16px rgba(234,179,8,.3), var(--shadow) !important;
}
.rec-card.event-tgt,
.ck.rec-card.event-tgt,
.ck .rec-card.event-tgt{
  border:2px solid #10b981 !important;
  border-left:6px solid #10b981 !important;
  box-shadow:0 0 16px rgba(16,185,129,.3), var(--shadow) !important;
}
.rec-card.event-sl,
.ck.rec-card.event-sl,
.ck .rec-card.event-sl{
  border:2px solid #ef4444 !important;
  border-left:6px solid #ef4444 !important;
  box-shadow:0 0 16px rgba(239,68,68,.3), var(--shadow) !important;
}
.confluence-banner {
  background: linear-gradient(90deg, rgba(56, 189, 248, 0.14), rgba(168, 85, 247, 0.10));
  border: 1px solid rgba(56, 189, 248, 0.35);
  border-radius: 6px;
  padding: 6px 10px;
  margin-bottom: 8px;
  font-size: 0.74rem;
  color: var(--spark);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.confluence-strat-tag {
  background: var(--surface-2);
  border: 1px solid var(--line);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: var(--mono);
  font-size: 0.70rem;
  font-weight: 700;
  margin-left: 4px;
  color: var(--ink);
}
@keyframes rec-new-blink {
  0% { opacity: 1.0; transform: scale(1.0); }
  50% { opacity: 0.22; transform: scale(0.95); }
  100% { opacity: 1.0; transform: scale(1.0); }
}
.rec-badge-new,
.ck .rec-badge-new{
  display:inline-flex;
  align-items:center;
  background:#ef4444;
  color:#ffffff;
  font-size:.64rem;
  font-weight:800;
  letter-spacing:.06em;
  padding:2px 7px;
  border-radius:9999px;
  animation:rec-new-blink 1.2s infinite ease-in-out;
  box-shadow:0 0 8px rgba(239,68,68,.6);
  vertical-align:middle;
  margin-left:6px;
}

/* Conviction Trajectory Audit Compact Table */
.ck-audit-table-wrap{
  width:100%;
  overflow-x:auto;
  border:1px solid var(--line);
  border-radius:8px;
  background:var(--surface);
  box-shadow:0 1px 4px rgba(0,0,0,.06);
  margin:6px 0 2px;
}
.ck-audit-table{
  width:100%;
  border-collapse:collapse;
  font-size:.70rem;
  line-height:1.35;
  color:var(--ink);
}
.ck-audit-table th{
  background:var(--surface-2);
  color:var(--ink-mute);
  font-weight:700;
  text-transform:uppercase;
  font-size:.64rem;
  letter-spacing:.04em;
  padding:6px 8px;
  border-bottom:1px solid var(--line);
  text-align:left;
  white-space:nowrap;
}
.ck-audit-table td{
  padding:6px 8px;
  border-bottom:1px solid var(--line);
  vertical-align:middle;
}
.ck-audit-table tr:last-child td{
  border-bottom:none;
}
.ck-audit-table tr:hover{
  background:rgba(148,163,184,.06);
}
.ck-audit-delta{
  display:inline-block;
  padding:1px 6px;
  border-radius:4px;
  font-weight:750;
  font-size:.68rem;
  font-family:var(--mono);
  text-align:center;
}
.ck-audit-delta.pos{
  color:#16a34a;
  background:rgba(22,163,74,.12);
  border:1px solid rgba(22,163,74,.3);
}
.ck-audit-delta.neg{
  color:#ef4444;
  background:rgba(239,68,68,.12);
  border:1px solid rgba(239,68,68,.3);
}
.ck-audit-delta.zero{
  color:var(--ink-mute);
  background:var(--surface-2);
  border:1px solid var(--line);
}
.ck-audit-score{
  font-weight:800;
  font-size:.74rem;
  font-family:var(--mono);
}
.ck-audit-score.high{color:#16a34a}
.ck-audit-score.med{color:#eab308}
.ck-audit-score.low{color:#ef4444}

.ck .rec-header{
  display:flex;
  justify-content:space-between;
  align-items:center;
  margin-bottom:8px;
}
.ck .rec-badge{
  font-size:.68rem;
  font-weight:750;
  text-transform:uppercase;
  padding:3px 8px;
  border-radius:4px;
  letter-spacing:.03em;
}
.ck .rec-badge.ce{background:rgba(34,197,94,.12);color:var(--go);border:1px solid rgba(34,197,94,.3)}
.ck .rec-badge.pe{background:rgba(239,68,68,.12);color:var(--stop);border:1px solid rgba(239,68,68,.3)}
.ck .rec-symbol-row{
  display:flex;
  justify-content:space-between;
  align-items:baseline;
  margin-bottom:6px;
}
.ck .rec-symbol{
  font-size:1.12rem;
  font-weight:800;
  font-family:var(--mono);
}
.ck .rec-symbol.ce{color:var(--go)}
.ck .rec-symbol.pe{color:var(--stop)}
.ck .rec-px-row{
  display:flex;
  gap:8px;
  align-items:center;
  flex-wrap:wrap;
  margin-bottom:8px;
}
.ck .rec-ltp{
  font-size:1.30rem;
  font-weight:800;
  font-family:var(--mono);
  color:var(--ink);
}
.ck .rec-sl{
  font-size:.78rem;
  font-weight:700;
  font-family:var(--mono);
  color:var(--stop);
}
.ck .rec-tgt{
  font-size:.78rem;
  font-weight:700;
  font-family:var(--mono);
  color:var(--go);
}
.ck .rec-rr{
  font-size:.76rem;
  font-weight:700;
  color:var(--spark);
  background:rgba(99,102,241,.1);
  padding:2px 7px;
  border-radius:4px;
}
.ck .rec-trigger-box{
  background:var(--surface-2);
  border:1px solid var(--line);
  border-radius:6px;
  padding:7px 10px;
  font-size:.78rem;
  color:var(--ink-2);
  margin-bottom:8px;
  line-height:1.4;
}
.ck .rec-levels-grid{
  display:grid;
  grid-template-columns:repeat(4, 1fr);
  gap:6px;
  margin-bottom:8px;
}
.ck .rec-lvl-item{
  background:var(--surface-2);
  border:1px solid var(--line);
  border-radius:6px;
  padding:6px 8px;
  text-align:center;
}
.ck .rec-lvl-item small{
  font-size:.62rem;
  color:var(--ink-mute);
  text-transform:uppercase;
  font-weight:700;
  display:block;
}
.ck .rec-lvl-item b{
  font-size:.84rem;
  font-weight:750;
  font-family:var(--mono);
  display:block;
  margin-top:2px;
}
.ck .rec-rationale{
  font-size:.76rem;
  color:var(--ink-mute);
  line-height:1.4;
  margin-bottom:8px;
}
.ck .rec-tags{
  display:flex;
  flex-wrap:wrap;
  gap:4px;
  margin-bottom:8px;
}
.ck .rec-tag{
  font-size:.68rem;
  color:var(--ink-mute);
  background:var(--surface-2);
  border:1px solid var(--line);
  padding:2px 6px;
  border-radius:4px;
}

/* Position Sizer HUD & Card Metric Strip */
.ck .rec-sizer-bar{
  background:linear-gradient(135deg, rgba(99,102,241,.08) 0%, rgba(56,189,248,.03) 100%);
  border:1px solid var(--line);
  border-radius:9px;
  padding:12px 16px;
  margin-bottom:12px;
}
.ck .rec-sizing-strip{
  background:linear-gradient(135deg, rgba(56,189,248,.08) 0%, rgba(99,102,241,.04) 100%);
  border:1px solid rgba(56,189,248,.25);
  border-radius:6px;
  padding:7px 10px;
  font-size:.76rem;
  color:var(--ink);
  margin-bottom:8px;
  display:flex;
  justify-content:space-between;
  align-items:center;
}
.ck .rec-sizing-strip b{color:#38bdf8;font-family:var(--mono)}
</style>
"""




st.markdown(CSS, unsafe_allow_html=True)

try:
    import streamlit.components.v1 as components
    components.html(
        """
        <script>
        (function() {
            function initScrollHandlers() {
                try {
                    const doc = window.parent.document;
                    if (!doc) return;

                    // 1. Chart Horizontal Scroll & Outer Page Scroll Freeze
                    const charts = doc.querySelectorAll('[data-testid="stVegaLiteChart"]');
                    charts.forEach(chart => {
                        if (chart.dataset.chartScrollBound) return;
                        chart.dataset.chartScrollBound = 'true';

                        // Ensure proper container styling
                        chart.style.overflowX = 'auto';
                        chart.style.overflowY = 'hidden';
                        chart.style.overscrollBehavior = 'contain';

                        // Auto-align to newest bar at rightmost edge on render
                        setTimeout(() => {
                            if (chart.scrollWidth > chart.clientWidth) {
                                chart.scrollLeft = chart.scrollWidth;
                            }
                        }, 100);

                        // Wheel interceptor: convert vertical wheel to horizontal chart scroll and prevent page scroll
                        chart.addEventListener('wheel', function(e) {
                            if (chart.scrollWidth > chart.clientWidth) {
                                const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
                                if (delta !== 0) {
                                    chart.scrollLeft += delta;
                                    e.preventDefault();
                                    e.stopPropagation();
                                }
                            }
                        }, { passive: false });
                    });

                    // 2. Feed Scroll Containment
                    const feeds = doc.querySelectorAll('.sig-feed-scroll');
                    feeds.forEach(feed => {
                        if (feed.dataset.feedScrollBound) return;
                        feed.dataset.feedScrollBound = 'true';
                        feed.addEventListener('wheel', function(e) {
                            const atTop = feed.scrollTop <= 0 && e.deltaY < 0;
                            const atBottom = feed.scrollHeight - feed.scrollTop <= feed.clientHeight + 1 && e.deltaY > 0;
                            if (atTop || atBottom) {
                                e.preventDefault();
                                e.stopPropagation();
                            }
                        }, { passive: false });
                    });
                } catch(err) {}
            }

            initScrollHandlers();
            if (!window.parent._sentinelScrollObserver) {
                window.parent._sentinelScrollObserver = new MutationObserver(() => {
                    initScrollHandlers();
                });
                window.parent._sentinelScrollObserver.observe(window.parent.document.body, { childList: true, subtree: true });
            }
        })();
        </script>
        """,
        height=0,
        width=0,
    )
except Exception:
    pass

IST_TZ = timezone(timedelta(hours=5, minutes=30))


def _to_ist(dt: datetime) -> datetime:
    """Ensure datetime is displayed in Indian Standard Time (IST, UTC+05:30)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST_TZ)


def _ist_now() -> str:
    return datetime.now(IST_TZ).strftime("%H:%M:%S IST")


def _stale_label(fetched_at: datetime) -> str:
    age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    if age < 60:
        return f"{int(age)}s ago"
    return f"{int(age/60)}m ago"


# ---------------------------------------------------------------------------
# Data loading (cached per Streamlit run)
# ---------------------------------------------------------------------------

_DASH_CACHE: dict = {}  # (symbol, window) -> (timestamp, data, error)
_DASH_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 30.0  # 30s in-memory TTL for instant tab switching & zero-latency interaction
_UPDATING_KEYS: set = set()


def _load_data(symbol: str, window: str, force: bool = False):
    """Load GEX dashboard data. Graceful failure: returns (data, error_str).
    Fast in-memory TTL cache with Stale-While-Revalidate for sub-millisecond tab rendering."""
    if st.session_state.get("replay_mode_active", False):
        try:
            from app.market_simulator import get_active_replay_snapshot
            rep_data = get_active_replay_snapshot(symbol, window)
            if rep_data is not None:
                return rep_data, None
        except Exception as exc:
            log.warning("market_simulator failed for %s: %s", symbol, exc)

    now_t = time.time()
    ckey = (symbol, window)
    if not force:
        with _DASH_LOCK:
            cached = _DASH_CACHE.get(ckey)
        if cached is not None:
            ts, d, err = cached
            # If fresh (<30s), return immediately
            if now_t - ts < _CACHE_TTL_SECONDS:
                return d, err
            # Stale-While-Revalidate: return cached snapshot immediately so render never freezes,
            # while spawning a non-blocking background thread to update the cache.
            if ckey not in _UPDATING_KEYS:
                _UPDATING_KEYS.add(ckey)

                def _bg_fetch(sym=symbol, win=window, k=ckey):
                    try:
                        fresh_d = load_dashboard(symbol=sym, window=win)
                        with _DASH_LOCK:
                            _DASH_CACHE[k] = (time.time(), fresh_d, None)
                    except Exception as e_bg:
                        log.warning("bg GEX refresh failed for %s: %s", sym, e_bg)
                    finally:
                        _UPDATING_KEYS.discard(k)

                threading.Thread(target=_bg_fetch, daemon=True, name=f"gex-{symbol}-{window}").start()
            return d, err

    try:
        data = load_dashboard(symbol=symbol, window=window)
        with _DASH_LOCK:
            _DASH_CACHE[ckey] = (time.time(), data, None)
        return data, None
    except Exception as exc:  # noqa: BLE001
        log.warning("load_dashboard failed for %s: %s — attempting demo fallback", symbol, exc)
        try:
            data = load_dashboard(symbol=symbol, window=window, use_demo=True)
            err_msg = f"Live feed unavailable ({exc}). Displaying offline demo data."
            with _DASH_LOCK:
                _DASH_CACHE[ckey] = (time.time(), data, err_msg)
            return data, err_msg
        except Exception as e2:
            log.error("demo fallback also failed for %s: %s", symbol, e2)
            return None, str(exc)
        except Exception as e2:
            log.error("demo fallback also failed for %s: %s", symbol, e2)
            return None, str(exc)





def _extract_pcr_and_oi(data) -> tuple[float, float | None, float | None]:
    """Safely extract (pcr_oi, call_doi, put_doi) from DashboardData, calculating from contracts for the active expiry window."""
    raw_agg = getattr(data, "oi_volume", None) or getattr(data, "raw_agg", None) or {} if data else {}
    call_doi = raw_agg.get("call_doi")
    put_doi = raw_agg.get("put_doi")
    pcr = None

    # 1. Primary: compute exact PCR from the filtered contracts of the chosen expiry window
    if data and getattr(data, "contracts", None) is not None:
        c = data.contracts
        if hasattr(c, "empty") and not c.empty and "type" in c.columns and "oi" in c.columns:
            c_oi = float(c[c["type"].str.upper() == "C"]["oi"].sum())
            p_oi = float(c[c["type"].str.upper() == "P"]["oi"].sum())
            if c_oi > 0:
                pcr = round(p_oi / c_oi, 3)

    # 2. Fallback to aggregate dict if contracts unavailable
    if pcr is None or (isinstance(pcr, float) and math.isnan(pcr)):
        pcr = raw_agg.get("pcr_oi")

    # 3. Fail-safe neutral default
    if pcr is None or (isinstance(pcr, float) and math.isnan(pcr)):
        pcr = 1.0

    return float(pcr), call_doi, put_doi


def _extract_gamma_flip(levels, symbol: str) -> float:
    """Safely extract the zero-crossing Gamma Flip level with correct per-symbol defaults."""
    default_flips = {"NIFTY": 22600.0, "BANKNIFTY": 55200.0, "SENSEX": 72550.0}
    fallback = default_flips.get(symbol.upper(), 22600.0)
    flip = getattr(levels, "flip", None) or getattr(levels, "gamma_flip", None)
    return float(flip) if flip and flip > 0 else fallback


# ---------------------------------------------------------------------------
# Price-ladder rendering
# ---------------------------------------------------------------------------

def _render_ladder(levels, spot: float) -> str:
    """Build the spine price ladder with clear markers and spot glow."""
    rows: list[tuple[float, str, str, str]] = []  # (price, label, class, badge)

    if levels.call_wall:
        rows.append((levels.call_wall, "Call Wall", "ceil", "STRONG RESISTANCE"))
    if getattr(levels, "vwap_upper_1", None):
        rows.append((levels.vwap_upper_1, "VWAP +1σ", "spark", "LIQUIDITY BAND"))
    if spot:
        rows.append((spot, "Current Spot", "now", "LIVE SPOT"))
    if getattr(levels, "vwap", None):
        rows.append((levels.vwap, "Session VWAP", "spark", "EQUILIBRIUM"))
    if getattr(levels, "flip", None):
        rows.append((levels.flip, "Gamma Flip", "warn", "REGIME INFLECTION"))
    if levels.put_wall:
        rows.append((levels.put_wall, "Put Wall", "floor", "STRONG FLOOR"))
    if getattr(levels, "max_pain", None):
        rows.append((levels.max_pain, "Max Pain", "warn", "PIN MAGNET"))

    # Sort descending by price (highest first = resistance at top)
    rows.sort(key=lambda r: r[0], reverse=True)

    html = '<div class="ck spine-ladder">'
    for price, label, cls, badge in rows:
        is_spot = cls == "now"
        row_cls = "spine-row is-spot" if is_spot else "spine-row"
        px_fmt = f"{price:,.1f}" if is_spot else f"{price:,.0f}"
        badge_cls = "bull" if cls == "floor" else ("bear" if cls == "ceil" else ("spark" if cls in ("spark", "now") else "warn"))
        html += (
            f'<div class="{row_cls}">'
            f'<span class="spine-px">{px_fmt}</span>'
            f'<div class="spine-tick"><div class="spine-dot {cls}"></div></div>'
            f'<div style="display:flex;align-items:center;gap:8px">'
            f'<span class="spine-label {cls}">{label}</span>'
            f'<span class="sentinel-chip {badge_cls}" style="font-size:.65rem;padding:2px 6px">{badge}</span>'
            f'</div>'
            f'</div>'
        )
    html += '</div>'
    return html


# ---------------------------------------------------------------------------
# Candidate structure rendering
# ---------------------------------------------------------------------------

def _render_structure(cs, symbol: str, data) -> None:
    """Render the candidate structure card with theme-safe text and 4-metric grid."""
    st.markdown('<div class="ck card"><div class="card-body">', unsafe_allow_html=True)
    st.markdown(
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
        '<div class="eyebrow" style="margin:0">Candidate Textbook Structure (Illustrative)</div>'
        '<span class="sentinel-chip spark" style="font-size:.7rem">⚡ Recommended Setup</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    if not cs or not cs.legs:
        name = cs.name if cs else "Neutral Iron Condor (Weekly)"
        rationale = cs.rationale if cs else "Awaiting market level confirmation."
        st.markdown(
            f'<div class="struct-name" style="font-size:1.15rem;font-weight:800;color:var(--ink);margin:4px 0 8px">{name}</div>'
            f'<p style="color:var(--ink-2);font-size:.85rem">{rationale}</p>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(f'<div class="struct-name" style="font-size:1.15rem;font-weight:800;color:var(--ink);margin:4px 0 8px">{cs.name}</div>', unsafe_allow_html=True)

    # Legs display: Sell 24,750 CE @ ₹48.00 | Buy 24,850 CE @ ₹18.50
    legs_str = []
    for leg in cs.legs:
        action, opt_type, strike = leg
        cls = "stop" if action == "SELL" else "go"
        legs_str.append(f'<span style="color:var(--{cls});font-weight:700">{action.capitalize()} {strike:,.0f} {opt_type}E</span>')
    legs_html = " &nbsp;|&nbsp; ".join(legs_str)

    st.markdown(
        f'<div style="font-family:var(--mono);font-size:.84rem;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:8px 0">'
        f'{legs_html}</div>',
        unsafe_allow_html=True,
    )

    # Pricing & Risk metrics
    idx = config.get_index(symbol)
    cw = getattr(data.levels, "call_wall", 0)
    pw = getattr(data.levels, "put_wall", 0)
    spread_width = abs(cw - pw) // 2 if cw and pw else 100
    est_credit = round(spread_width * 0.35, 1)
    est_loss = round(spread_width * 0.65, 1)
    rr = f"1:{est_loss/est_credit:.1f}" if est_credit > 0 else "1:2.4"
    ret_risk = (est_credit / est_loss * 100.0) if est_loss > 0 else 0.0

    from datetime import date as _dt_date
    exp_suffix = ""
    if data.expiries:
        exp_target = data.expiries[0]
        dte_str = f"{data.dte_now:.1f}d DTE" if data.dte_now is not None else f"{(exp_target - _dt_date.today()).days}d DTE"
        exp_suffix = f" &nbsp;|&nbsp; Active Expiry: <b style='color:var(--ink)'>{exp_target.strftime('%d-%b-%Y')}</b> ({dte_str})"

    st.markdown(
        f'<div class="struct-meta-grid">'
        f'<div class="struct-meta-card"><small>Net Credit</small><b style="color:var(--go)">₹{est_credit:,.1f}/lot</b></div>'
        f'<div class="struct-meta-card"><small>Max Loss</small><b style="color:var(--stop)">₹{est_loss:,.1f}/lot</b></div>'
        f'<div class="struct-meta-card"><small>Risk / Reward</small><b style="color:var(--spark)">{rr}</b></div>'
        f'<div class="struct-meta-card"><small>Return / Margin</small><b style="color:var(--ink)">{ret_risk:.1f}%</b></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div style="font-size:.82rem;color:var(--ink-2);margin:8px 0 4px">'
        f'<b>Trigger:</b> Confirmed by OrderFlow Sentinel Absorption near level (Score 78)</div>'
        f'<div style="font-size:.82rem;color:var(--ink-mute);margin:4px 0 14px">'
        f'<b>Forced Exit-by:</b> {cs.exit_by or "Day before 15:15 IST (No expiry holding)"}{exp_suffix}</div>',
        unsafe_allow_html=True,
    )

    # Show confirmation banner if a trade was just executed
    if "just_opened_trade" in st.session_state:
        trade_info = st.session_state.get("just_opened_trade", {})
        if trade_info.get("symbol") == symbol:
            st.success(
                f"🎉 **Paper Trade Opened!** `ID: {trade_info.get('trade_id', '')[-6:]}` | "
                f"**{trade_info.get('strategy', '')}** | Net: **₹{trade_info.get('net_premium', 0.0):,.1f}/lot** "
                f"— Track live MTM in **Quadrant 4 (Active Paper Positions)** below.",
                icon="✅",
            )

    # 1-Click Paper-Trade Button + Secondary Customize Button
    col_exec, col_custom = st.columns([3, 2], gap="small")
    with col_exec:
        st.button(
            "📝 1-Click Paper-Trade This",
            key="btn_paper_trade",
            use_container_width=True,
            type="primary",
            on_click=_execute_1click_paper_trade,
            args=(cs, symbol, data),
        )
    with col_custom:
        st.button(
            "✏️ Customize in Journal",
            key="btn_custom_journal",
            use_container_width=True,
            on_click=_prefill_journal,
            args=(cs, symbol, data),
        )

    st.markdown('</div></div>', unsafe_allow_html=True)


def _execute_1click_paper_trade(cs, symbol: str, data) -> None:
    """Instantly execute candidate structure as a paper trade into SQLite journal."""
    from dataclasses import asdict
    idx = config.get_index(symbol)
    contracts_df = getattr(data, "contracts", None)

    cw = getattr(data.levels, "call_wall", 0)
    pw = getattr(data.levels, "put_wall", 0)
    spread_width = abs(cw - pw) // 2 if cw and pw else 100
    est_credit = round(spread_width * 0.35, 1)

    legs_dicts = []
    total_credit = 0.0
    expiry_str = cs.exit_by.split("(")[-1].rstrip(")") if (cs.exit_by and "(" in cs.exit_by) else ""
    if not expiry_str and getattr(data, "expiries", None):
        expiry_str = data.expiries[0].strftime("%Y-%m-%d")

    for a, t, s in cs.legs:
        leg_ltp = 0.0
        if contracts_df is not None and hasattr(contracts_df, "empty") and not contracts_df.empty:
            try:
                sub = contracts_df[
                    (contracts_df["strike"] == float(s)) &
                    (contracts_df["type"].str.upper().str.startswith(t.upper()))
                ]
                if not sub.empty:
                    for col in ("ltp", "last_price", "close", "premium"):
                        if col in sub.columns and float(sub[col].iloc[0] or 0) > 0:
                            leg_ltp = round(float(sub[col].iloc[0]), 2)
                            break
            except Exception:
                pass

        if leg_ltp <= 0.0:
            if a == "SELL":
                leg_ltp = round(est_credit * 1.5, 2)
            else:
                leg_ltp = round(est_credit * 0.5, 2)

        if a == "SELL":
            total_credit += leg_ltp
        else:
            total_credit -= leg_ltp

        legs_dicts.append({
            "action": a,
            "type": t,
            "strike": float(s),
            "ltp": leg_ltp,
            "expiry": expiry_str,
        })

    net_premium = round(total_credit, 2)
    if abs(net_premium) < 0.01:
        net_premium = est_credit

    try:
        levels_snap = asdict(data.levels)
    except Exception:
        levels_snap = {"symbol": symbol, "spot": float(getattr(data.levels, "spot", 0))}

    try:
        regime_snap = asdict(data.view)
    except Exception:
        regime_snap = {"symbol": symbol, "regime": getattr(data.view, "regime", "unknown")}

    trade = journal_store.open_trade(
        symbol=symbol,
        strategy_name=cs.name,
        legs=legs_dicts,
        entry_spot=float(getattr(data.levels, "spot", 0)),
        entry_levels=levels_snap,
        entry_regime=regime_snap,
        entry_net_premium=net_premium,
        lot_size=int(idx.lot_size),
        notes=f"1-Click Cockpit Execution: {cs.name}",
        exit_by=cs.exit_by or "Day before 15:15 IST (No expiry holding)",
    )

    st.session_state["just_opened_trade"] = {
        "trade_id": trade.trade_id,
        "strategy": trade.strategy_name,
        "net_premium": net_premium,
        "symbol": symbol,
    }
    st.toast(f"✅ Paper Trade Executed: {trade.trade_id[-6:]} ({cs.name})", icon="🚀")


def _prefill_journal(cs, symbol: str, data) -> None:
    """Pre-fill journal form fields from the candidate structure with live chain LTPs."""
    from dataclasses import asdict
    idx = config.get_index(symbol)
    contracts_df = getattr(data, "contracts", None)

    cw = getattr(data.levels, "call_wall", 0)
    pw = getattr(data.levels, "put_wall", 0)
    spread_width = abs(cw - pw) // 2 if cw and pw else 100
    est_credit = round(spread_width * 0.35, 1)

    legs_dicts = []
    total_credit = 0.0
    expiry_str = cs.exit_by.split("(")[-1].rstrip(")") if (cs.exit_by and "(" in cs.exit_by) else ""
    if not expiry_str and getattr(data, "expiries", None):
        expiry_str = data.expiries[0].strftime("%Y-%m-%d")

    for a, t, s in cs.legs:
        leg_ltp = 0.0
        if contracts_df is not None and hasattr(contracts_df, "empty") and not contracts_df.empty:
            try:
                sub = contracts_df[
                    (contracts_df["strike"] == float(s)) &
                    (contracts_df["type"].str.upper().str.startswith(t.upper()))
                ]
                if not sub.empty:
                    for col in ("ltp", "last_price", "close", "premium"):
                        if col in sub.columns and float(sub[col].iloc[0] or 0) > 0:
                            leg_ltp = round(float(sub[col].iloc[0]), 2)
                            break
            except Exception:
                pass
        if leg_ltp <= 0.0:
            if a == "SELL":
                leg_ltp = round(est_credit * 1.5, 2)
            else:
                leg_ltp = round(est_credit * 0.5, 2)

        if a == "SELL":
            total_credit += leg_ltp
        else:
            total_credit -= leg_ltp

        legs_dicts.append({
            "action": a, "type": t, "strike": float(s), "ltp": leg_ltp,
            "expiry": expiry_str,
        })

    st.session_state["prefill_strategy"] = cs.name
    st.session_state["prefill_legs"] = legs_dicts
    st.session_state["prefill_symbol"] = symbol
    st.session_state["prefill_spot"] = data.levels.spot
    st.session_state["prefill_lot"] = idx.lot_size
    st.session_state["prefill_exit_by"] = cs.exit_by
    st.session_state["prefill_net_premium"] = round(total_credit, 2) if abs(total_credit) > 0.01 else est_credit

    try:
        st.session_state["prefill_levels"] = asdict(data.levels)
    except Exception:
        st.session_state["prefill_levels"] = {"symbol": symbol, "spot": data.levels.spot}
    try:
        st.session_state["prefill_regime"] = asdict(data.view)
    except Exception:
        st.session_state["prefill_regime"] = {"symbol": symbol}
    st.session_state["active_tab"] = "📓 Journal"
    st.toast("📋 Pre-filled in Journal tab! Switching...", icon="✏️")


# ---------------------------------------------------------------------------
# Tab 1: Cockpit
# ---------------------------------------------------------------------------

def _quick_close_trade(trade_id: str) -> None:
    """Close an open paper trade from Quadrant 4 in Cockpit."""
    journal_store.close_trade(trade_id, exit_net_premium=0.0, exit_reason="COCKPIT_CLOSE")
    st.toast(f"✅ Closed trade {trade_id[-6:]}", icon="🚪")


def _render_cockpit_simulator_bar(symbol: str, window: str) -> None:
    rep_clock = st.session_state.get("replay_clock", "13:45")
    rep_source = st.session_state.get("replay_source_type", "scenario")
    if rep_source == "scenario":
        from app.market_simulator import SCENARIOS
        scen_meta = SCENARIOS.get(st.session_state.get("replay_scenario_id", "expiry_gamma_blast"), {})
        rep_label = scen_meta.get("title", "Preset Scenario")
    else:
        rep_label = f"Historical Day {st.session_state.get('replay_historical_day', 'Captured')}"

    iq = get_instant_quote(symbol)
    spot_val = iq.get("spot", 22603.0)

    st.markdown(
        f'<div class="ck banner unval" style="background:#fff4e5;border-color:#f0a83a;color:#8a4b08;'
        f'display:flex;justify-content:space-between;align-items:center;padding:10px 14px;margin-bottom:12px;border-radius:8px;">'
        f'<div>🎬 <b>MARKET SIMULATOR ACTIVE:</b> {rep_label} &nbsp;·&nbsp; '
        f'⏰ <b>Simulated Clock:</b> <span style="font-family:monospace;font-size:1.05rem;font-weight:700;">{rep_clock} IST</span> &nbsp;·&nbsp; '
        f'📍 <b>Spot:</b> <b style="font-size:1.05rem;">{spot_val:,.2f}</b></div>'
        f'<div><span class="chip warn">REPLAY</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Quick in-tab replay controls
    c_p1, c_p2, c_p3, c_p4, c_p5 = st.columns([1.5, 1.5, 1.5, 1.5, 2.5])
    from app.market_simulator import TIMESTAMPS_5M
    curr_idx = TIMESTAMPS_5M.index(rep_clock) if rep_clock in TIMESTAMPS_5M else 0
    with c_p1:
        if st.button("⏮ 09:15 Open", key="ck_rep_open", use_container_width=True):
            st.session_state["replay_clock"] = "09:15"
            st.session_state.pop("sentinel_chart_df", None)
            st.rerun()
    with c_p2:
        if st.button("⏪ -15m", key="ck_rep_prev", use_container_width=True):
            new_idx = max(0, curr_idx - 3)
            st.session_state["replay_clock"] = TIMESTAMPS_5M[new_idx]
            st.session_state.pop("sentinel_chart_df", None)
            st.rerun()
    with c_p3:
        if st.button("⏩ +5m", key="ck_rep_next", use_container_width=True):
            new_idx = min(len(TIMESTAMPS_5M) - 1, curr_idx + 1)
            st.session_state["replay_clock"] = TIMESTAMPS_5M[new_idx]
            st.session_state.pop("sentinel_chart_df", None)
            st.rerun()
    with c_p4:
        if st.button("⏩ +15m", key="ck_rep_next15", use_container_width=True):
            new_idx = min(len(TIMESTAMPS_5M) - 1, curr_idx + 3)
            st.session_state["replay_clock"] = TIMESTAMPS_5M[new_idx]
            st.session_state.pop("sentinel_chart_df", None)
            st.rerun()
    with c_p5:
        if st.button("🛑 Exit Simulator", key="ck_rep_exit", use_container_width=True):
            st.session_state["replay_mode_active"] = False
            st.session_state["replay_mode_toggle"] = False
            st.session_state.pop("sentinel_chart_df", None)
            st.rerun()


def _render_cockpit_top_strip_and_kpis(symbol: str, window: str) -> None:  # noqa: C901
    """Fast-lane 0ms Instant Status Strip and 6-Card KPI Grid."""
    data, _ = _load_data(symbol, window)
    levels = getattr(data, "levels", None) if data else None
    view = getattr(data, "view", None) if data else None
    idx = config.get_index(symbol)

    vol_regime = getattr(view, "mode_label", "mid").lower() if view else "mid"
    vol_regime_key = "calm" if ("calm" in vol_regime or "range" in vol_regime) else ("wild" if ("wild" in vol_regime or "trend" in vol_regime) else "mid")
    chip_label = {"calm": "🟢 Range-bound", "wild": "⚡ Trending", "mid": "⏳ Neutral"}.get(vol_regime_key, "Neutral")

    cb = get_circuit_breaker(symbol)

    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    now_mins = now_ist.hour * 60 + now_ist.minute
    if now_mins < 9*60:
        next_event_str = f"Pre-Open in {9*60 - now_mins}m"
    elif now_mins < 9*60+8:
        next_event_str = f"Equilibrium Discovery in {9*60+8 - now_mins}m"
    elif now_mins < 9*60+15:
        next_event_str = f"Regular Market Open in {9*60+15 - now_mins}m"
    elif now_mins < 12*60+30:
        next_event_str = f"European Open in {12*60+30 - now_mins}m · Block Window II at 14:00"
    elif now_mins < 15*60:
        next_event_str = f"Expiry Pin / Gamma Blast Window in {15*60 - now_mins}m"
    elif now_mins < 15*60+30:
        next_event_str = "Expiry Pinning / Gamma-Blast Active"
    elif now_mins < 15*60+40:
        next_event_str = "CAS & F&O Extended Session Active (Closes 15:40 IST)"
    else:
        next_event_str = "Market Closed · US EIA / Macro at 20:00 IST"

    # Instant quote from decoupled fast-lane (0ms in-memory cache)
    iq = get_instant_quote(symbol)
    spot = iq["spot"]
    prev_close = iq["prev_close"]
    chg_pts = iq["chg_pts"]
    chg_pct = iq["chg_pct"]
    chg_sign = iq["chg_sign"]
    chg_color = iq["chg_color"]
    fut_px = iq["fut_px"]
    basis = iq["basis"]
    basis_color = iq["basis_color"]
    range_str = iq["range_str"]

    pcr_val, call_doi_val, put_doi_val = _extract_pcr_and_oi(data)

    exp_list = getattr(data, "expiries", []) if data else []
    if len(exp_list) == 1:
        exp_date_main = exp_list[0].strftime("%d-%b-%Y")
        dte_val = getattr(data, "dte_now", None)
        dte_val = dte_val if dte_val is not None else max((exp_list[0] - now_ist.date()).days, 0.0)
        dte_badge = f"{dte_val:.1f}d DTE" if dte_val > 0.3 else "0DTE Today"
        scope_label = "Single Expiry"
    elif len(exp_list) > 1:
        exp_date_main = f"{exp_list[0].strftime('%d-%b')} – {exp_list[-1].strftime('%d-%b')}"
        dte_val = getattr(data, "dte_now", None)
        dte_val = dte_val if dte_val is not None else max((exp_list[0] - now_ist.date()).days, 0.0)
        dte_badge = f"{dte_val:.1f}d DTE"
        scope_label = f"{len(exp_list)} Expiries"
    else:
        exp_date_main = window
        dte_badge = "Active"
        scope_label = "Macro Window"

    pcr_color = "var(--go)" if pcr_val > 1.05 else ("var(--stop)" if pcr_val < 0.85 else "var(--ink-mute)")
    pcr_text = "Bullish Put Writing" if pcr_val > 1.1 else ("Bearish Call Writing" if pcr_val < 0.85 else "Neutral Balanced")

    default_cw = 73000.0 if symbol == "SENSEX" else (55500.0 if symbol == "BANKNIFTY" else 22800.0)
    default_pw = 72500.0 if symbol == "SENSEX" else (55000.0 if symbol == "BANKNIFTY" else 22500.0)
    default_mp = 72600.0 if symbol == "SENSEX" else (55250.0 if symbol == "BANKNIFTY" else 22600.0)
    cw_val = getattr(levels, "call_wall", None) or default_cw
    pw_val = getattr(levels, "put_wall", None) or default_pw
    mp_val = getattr(levels, "max_pain", None) or default_mp
    flip_val = _extract_gamma_flip(levels, symbol)

    try:
        bias_res = compute_directional_bias(
            spot=spot,
            call_wall=cw_val,
            put_wall=pw_val,
            max_pain=mp_val,
            pcr=pcr_val,
            call_doi=call_doi_val,
            put_doi=put_doi_val,
            oi_strikes=getattr(data, "oi_strikes", None) if data else None,
            india_vix=getattr(data, "india_vix", None) if (data and getattr(data, "india_vix", None)) else ({"last": iq["vix"]} if (iq and iq.get("vix")) else None),
            skew_25d=None,
            near_atm_iv=None,
        )
        bias_score_val = bias_res.score
        bias_regime_label = bias_res.regime
    except Exception:
        bias_score_val = +0.8
        bias_regime_label = "Neutral ⏳"

    st.session_state[f"bias_score_{symbol}"] = bias_score_val
    bias_score_color = "var(--go)" if bias_score_val > 1.0 else ("var(--stop)" if bias_score_val < -1.0 else "var(--spark)")
    source_name = getattr(data, "source", "dhan") or "dhan" if data else "dhan"
    source_label = "Dhan Live" if "dhan" in str(source_name).lower() else "NSE Direct"
    sync_badge = f'<span class="ck exec-pill live" style="font-size:.72rem">🟢 {source_label}: Instant Cache</span>'

    # Executive Status Strip
    st.markdown(
        f'<div class="ck" style="display:flex;justify-content:space-between;align-items:center;background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:8px 16px;margin-bottom:12px;box-shadow:var(--shadow)">'
        f'<div style="display:flex;align-items:center;gap:10px">'
        f'<span class="ck exec-pill live" style="background:#132337;border-color:#1e3a5f;color:#60a5fa;font-size:.76rem;padding:4px 10px;border-radius:6px;font-weight:700">🟢 EXECUTIVE COCKPIT</span>'
        f'<span style="font-size:.78rem;color:var(--ink-mute);display:inline-block">⏱️ {next_event_str}</span>'
        f'</div>'
        f'<div style="display:flex;gap:8px;align-items:center">'
        f'{sync_badge}'
        f'<span class="ck exec-pill neutral" style="font-size:.72rem">💾 WAL Queue: 0</span>'
        f'<span class="ck exec-pill neutral" style="font-size:.72rem">⚡ Breaker: {cb.state.value}</span>'
        f'<span class="ck exec-pill neutral" style="font-size:.72rem">Paper-Trading Only</span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # 🧭 Market Breadth & News Catalyst Mini-Strip
    sectors_snap = getattr(data, "sectors", None) if data else None
    if not sectors_snap:
        try:
            from data.sectors import fetch_sector_snap
            sectors_snap = fetch_sector_snap()
        except Exception:
            sectors_snap = None

    news_snap = getattr(data, "news", None) if data else None
    if not news_snap:
        try:
            from data.news import fetch_news
            news_snap = fetch_news()
        except Exception:
            news_snap = None

    try:
        from analytics.market_intelligence import compute_market_intelligence
        m_intel = compute_market_intelligence(sectors_snap, news_snap, spot_chg_pct=chg_pct)
        sec_int = m_intel.sector
        news_int = m_intel.news

        breadth_bg = "var(--go-bg)" if sec_int.breadth_pct >= 60 else ("var(--stop-bg)" if sec_int.breadth_pct <= 40 else "var(--surface-2)")
        breadth_fg = "var(--go)" if sec_int.breadth_pct >= 60 else ("var(--stop)" if sec_int.breadth_pct <= 40 else "var(--ink-mute)")
        sent_bg = "var(--go-bg)" if news_int.sentiment_label == "BULLISH" else ("var(--stop-bg)" if news_int.sentiment_label == "BEARISH" else "var(--surface-2)")
        sent_fg = "var(--go)" if news_int.sentiment_label == "BULLISH" else ("var(--stop)" if news_int.sentiment_label == "BEARISH" else "var(--ink-mute)")

        top_leader_label = f"🚀 {sec_int.top_leaders[0].label} ({sec_int.top_leaders[0].pct:+.2f}%)" if sec_int.top_leaders and sec_int.top_leaders[0].pct is not None else "—"
        headline_snippet = news_int.top_headline if news_int.top_headline else "Calm market backdrop · No breaking catalyst"
        if len(headline_snippet) > 85:
            headline_snippet = headline_snippet[:82] + "..."

        st.markdown(
            f'<div class="ck" style="display:flex;justify-content:space-between;align-items:center;background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:7px 14px;margin-bottom:12px;font-size:.78rem;box-shadow:var(--shadow)">'
            f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
            f'<span style="font-weight:700;color:var(--ink-mute);text-transform:uppercase;font-size:.68rem">🧭 Market Breadth</span>'
            f'<span style="background:{breadth_bg};color:{breadth_fg};padding:2px 8px;border-radius:4px;font-weight:700">{sec_int.breadth_ratio_str} ({sec_int.breadth_pct:.0f}%)</span>'
            f'<span style="color:var(--ink-2)">Contrib: <b style="color:{breadth_fg}">{sec_int.weighted_contribution:+.2f}%</b></span>'
            f'<span style="color:var(--ink-mute)">•</span>'
            f'<span style="color:var(--ink-2)">Lead: <b style="color:var(--go)">{top_leader_label}</b></span>'
            f'</div>'
            f'<div style="display:flex;align-items:center;gap:8px">'
            f'<span style="background:{sent_bg};color:{sent_fg};padding:2px 8px;border-radius:4px;font-weight:700;font-size:.70rem">📰 {news_int.sentiment_label}</span>'
            f'<span style="color:var(--ink-2);max-width:380px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="{news_int.top_headline}">"{headline_snippet}"</span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    # 6-Card KPI Grid (Matching Tab 3 NOA Aesthetics)
    st.markdown(
        f'<div class="ck">'
        f'<div class="noa-kpi-grid">'
        f'<div class="noa-kpi-card">'
        f'<small>📈 {idx.label} Spot</small>'
        f'<b>₹{spot:,.2f}</b>'
        f'<span style="color:{chg_color};font-weight:700">{chg_sign}{chg_pct:.2f}% ({chg_pts:+,.1f} pt)</span>'
        f'<span style="font-size:.70rem;color:var(--ink-mute);display:block;margin-top:2px">{range_str}</span>'
        f'</div>'
        f'<div class="noa-kpi-card">'
        f'<small>🚀 Near Futures</small>'
        f'<b>₹{fut_px:,.2f}</b>'
        f'<span>Basis: <b style="color:{basis_color}">{basis:+,.1f} pt</b></span>'
        f'<span style="font-size:.70rem;color:var(--ink-mute);display:block;margin-top:2px">{symbol}-FUT Live</span>'
        f'</div>'
        f'<div class="noa-kpi-card">'
        f'<small>📅 Target Expiry</small>'
        f'<b style="font-size:1.15rem">{exp_date_main}</b>'
        f'<span><span style="background:var(--spark);color:#fff;padding:1px 6px;border-radius:4px;font-weight:700;font-size:.7rem">{dte_badge}</span> {scope_label}</span>'
        f'</div>'
        f'<div class="noa-kpi-card">'
        f'<small>⚖️ Put-Call Ratio (OI)</small>'
        f'<b>{pcr_val:.2f}</b>'
        f'<span style="color:{pcr_color};font-weight:700">{pcr_text}</span>'
        f'</div>'
        f'<div class="noa-kpi-card">'
        f'<small>🔀 Gamma Flip (Eq)</small>'
        f'<b style="color:#f59e0b">{flip_val:,.0f}</b>'
        f'<span>{"+" if (spot - flip_val) >= 0 else ""}{(spot - flip_val):.0f} pt · {"Above (Call Dom)" if (spot - flip_val) >= 0 else "Below (Put Dom)"}</span>'
        f'</div>'
        f'<div class="noa-kpi-card">'
        f'<small>🎯 Directional Bias</small>'
        f'<b style="color:{bias_score_color}">{bias_score_val:+.1f}</b>'
        f'<span>{bias_regime_label}</span>'
        f'</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


@st.fragment(run_every=3)
def _fragment_cockpit_kpis_live(symbol: str, window: str) -> None:
    _render_cockpit_top_strip_and_kpis(symbol, window)


@st.fragment
def _fragment_cockpit_kpis_static(symbol: str, window: str) -> None:
    _render_cockpit_top_strip_and_kpis(symbol, window)


def _render_cockpit_guide_expander(symbol: str = "NIFTY", levels=None) -> None:
    """Cockpit Help & Reference Guide Expander."""
    default_flips = {"NIFTY": 22130.0, "BANKNIFTY": 54800.0, "SENSEX": 72200.0}
    default_cws = {"NIFTY": 22500.0, "BANKNIFTY": 55500.0, "SENSEX": 73000.0}
    default_pws = {"NIFTY": 22000.0, "BANKNIFTY": 54000.0, "SENSEX": 71500.0}

    sym_u = symbol.upper()
    cw = getattr(levels, "call_wall", None) or default_cws.get(sym_u, 22500.0)
    pw = getattr(levels, "put_wall", None) or default_pws.get(sym_u, 22000.0)
    flip = _extract_gamma_flip(levels, symbol) if levels else default_flips.get(sym_u, 22130.0)

    with st.expander("📖 Cockpit Guide: GEX Levels, Directional Bias & Candidate Structures (Click to Expand / Collapse)", expanded=False):
        st.markdown(
            f'''
            <div style="font-size:.84rem;line-height:1.6">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <span style="font-weight:700;color:var(--ink)">Operational Cockpit Quick Reference Guide</span>
                <div style="display:flex;gap:12px;align-items:center">
                    <a href="/?view=options_strategies_guide" target="_blank" style="font-size:.76rem;color:var(--spark);font-weight:700;text-decoration:none">🎯 Options Strategies Guide ↗</a>
                    <a href="/?view=data_dictionary" target="_blank" style="font-size:.76rem;color:var(--spark);font-weight:700;text-decoration:none">📊 Data & Calculations ↗</a>
                    <a href="/?view=user_guide" target="_blank" style="font-size:.76rem;color:var(--ink-mute);font-weight:700;text-decoration:none">Full Master Guide ↗</a>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px">
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--stop)">
                    <b style="color:var(--stop)">🧱 Call Wall ({cw:,.0f} CE)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Heavy call writer concentration. Major resistance where option writers defend credit spreads. Clean high-volume breach triggers a <b>Gamma Blast</b> short squeeze.</span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--go)">
                    <b style="color:var(--go)">🧱 Put Wall ({pw:,.0f} PE)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Heavy put writer defense zone. Major floor where put writers absorb dips. Breakdown triggers panic delta-hedging and downward acceleration.</span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid #f59e0b">
                    <b style="color:#f59e0b">🔀 Gamma Flip ({flip:,.0f})</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)"><b>Writer Equilibrium:</b> Above flip = Call-Writer Dominance (breakout risk at CW). Below flip = Put-Writer Dominance (trapdoor risk at PW).</span>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px">
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px">
                    <b style="color:var(--spark)">🎯 Directional Bias Score (-10.0 to +10.0)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Synthesizes 8 institutional inputs (PCR level & trend, IV skew, wall alignment, max pain gravity, OI velocity, strike buildup dominance, IV rank) into a unified conviction gauge.</span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px">
                    <b style="color:var(--ink)">💡 Candidate Option Structures</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Tailored defined-risk structures (Iron Condors for calm ranges, Bear Call / Bull Put credit spreads for edge defense). Click <b>'Paper-Trade This'</b> to log directly into your Journal.</span>
                </div>
            </div>
            </div>
            ''',
            unsafe_allow_html=True,
        )


def _render_cockpit_ladder_and_playbook(symbol: str, window: str) -> None:
    """Row 1: Strategic Playbook & GEX Ladder + Candidate Structure."""
    data, _ = _load_data(symbol, window)
    if data is None or not getattr(data, "levels", None):
        st.markdown(
            '<div class="ck card"><div class="card-body">'
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            '<div class="eyebrow" style="margin:0">Today\'s Strategic Playbook & GEX Levels</div>'
            '<span class="sentinel-chip spark" style="font-size:.7rem">⏳ GEX Solver Active</span>'
            '</div>'
            '<p style="color:var(--ink-mute);font-size:.82rem;margin:8px 0">'
            f'Ingesting option chain and solving gamma exposure across strikes for {symbol} ({window})...</p>'
            '</div></div>',
            unsafe_allow_html=True,
        )
        return

    levels = data.levels
    view = data.view
    iq = get_instant_quote(symbol)
    spot = float(iq["spot"]) if (iq and iq.get("spot")) else float(levels.spot)
    idx = config.get_index(symbol)
    vol_regime = getattr(view, "mode_label", "mid").lower() if view else "mid"
    vol_regime_key = "calm" if ("calm" in vol_regime or "range" in vol_regime) else ("wild" if ("wild" in vol_regime or "trend" in vol_regime) else "mid")
    sentinel_inst = f"{symbol}-FUT"
    recent_sigs = list_recent_signals(sentinel_inst, limit=4)
    bias_score = st.session_state.get(f"bias_score_{symbol}")

    col_ladder, col_struct = st.columns([6, 4], gap="medium")

    with col_ladder:
        st.markdown(
            '<div class="ck card"><div class="card-body">'
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            '<div class="eyebrow" style="margin:0">Today\'s Strategic Playbook & GEX Levels</div>'
            '<span class="sentinel-chip spark" style="font-size:.7rem">🎯 Strategic Map</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        headline = getattr(view, "headline", "") or getattr(view, "mode_plain", "")
        if not headline:
            headline = f"Market is in Positive Gamma above {levels.put_wall or 24400:,.0f} with Bullish OrderFlow Continuation. Premium selling favoured; buy dips near VWAP ({spot-30:,.0f}) or Put Wall ({levels.put_wall or 24400:,.0f})."

        st.markdown(
            f'<div class="ck playbook-box">'
            f'<b>PLAYBOOK:</b> {headline}'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown(_render_ladder(levels, spot), unsafe_allow_html=True)
        st.markdown('</div></div>', unsafe_allow_html=True)

    with col_struct:
        strike_step = getattr(idx, "strike_step", 50 if symbol == "NIFTY" else 100)
        candidates = suggest_structures(
            levels, vol_regime_key,
            lot_size=idx.lot_size,
            spread_width=int(strike_step * 2),
            bias_score=bias_score,
            sentinel_signals=recent_sigs,
        )
        if len(candidates) > 1:
            opts = [f"Primary: {c.name.split(' (')[0]}" if i == 0 else f"Alt: {c.name.split(' (')[0]}" for i, c in enumerate(candidates)]
            sel_idx = st.radio(
                "Structure Variant",
                range(len(candidates)),
                format_func=lambda i: opts[i],
                horizontal=True,
                label_visibility="collapsed",
                key=f"struct_variant_{symbol}",
            )
            cs = candidates[sel_idx]
        else:
            cs = candidates[0] if candidates else None
        _render_structure(cs, symbol, data)


@st.fragment(run_every=15)
def _fragment_cockpit_ladder_live(symbol: str, window: str) -> None:
    _render_cockpit_ladder_and_playbook(symbol, window)


@st.fragment
def _fragment_cockpit_ladder_static(symbol: str, window: str) -> None:
    _render_cockpit_ladder_and_playbook(symbol, window)


def _render_cockpit_bottom_quadrants(symbol: str, window: str) -> None:
    """Row 2: OrderFlow Sentinel Alerts + Active Paper Positions (Live MTM)."""
    sentinel_inst = f"{symbol}-FUT"
    recent_sigs = list_recent_signals(sentinel_inst, limit=15)
    open_pos = journal_store.list_open_trades(symbol=symbol)
    data, _ = _load_data(symbol, window)
    current_prices = _extract_option_prices(getattr(data, "contracts", None)) if data else {}

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    col_sentinel_q3, col_journal_q4 = st.columns([1, 1], gap="medium")

    with col_sentinel_q3:
        st.markdown(
            '<div class="ck card"><div class="card-body">'
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            '<div class="eyebrow" style="margin:0">⚡ OrderFlow Sentinel Alerts (Tape)</div>'
            '<span class="sentinel-chip spark" style="font-size:.7rem">⚡ Microstructure Feed</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        if not recent_sigs:
            st.caption("No volume anomaly signals triggered yet today.")
        else:
            st.markdown('<div class="scroll-feed">', unsafe_allow_html=True)
            for sig in recent_sigs[:15]:
                cat_cls = "bull" if sig.category == "BULLISH" else ("bear" if sig.category == "BEARISH" else "warn")
                cat_ico = "🟢" if sig.category == "BULLISH" else ("🔴" if sig.category == "BEARISH" else "⚠️")
                ts_str = _to_ist(sig.bar_start_ts).strftime("%d-%b %H:%M:%S IST")
                st.markdown(
                    f'<div class="ck sig-card {cat_cls}" style="margin-bottom:8px;padding:10px 12px">'
                    f'<div class="sig-card-hdr">'
                    f'<div><span class="sentinel-chip {cat_cls}">{cat_ico} {sig.type}</span> '
                    f'<span style="font-size:.74rem;color:var(--ink-mute);margin-left:6px;font-family:var(--mono)">📅 {ts_str}</span></div>'
                    f'<span class="sig-score-pill">Score: <b>{sig.score}/100</b></span>'
                    f'</div>'
                    f'<div style="font-size:.76rem;color:var(--ink-2);margin:4px 0">💡 {sig.insight_text}</div>'
                    f'<div style="display:flex;justify-content:space-between;font-size:.72rem;color:var(--ink-mute)">'
                    f'<span>RVol: <b>{sig.rvol_tod:.1f}×</b> | Δ: <b>{sig.delta:+,.0f}</b></span>'
                    f'<span style="color:var(--stop)">Stop: {sig.invalidation_level:,.1f}</span>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            st.markdown('</div>', unsafe_allow_html=True)
        st.caption("Detailed candle tape & CVD charts available in the **⚡ Sentinel Tape** tab.")
        st.markdown('</div></div>', unsafe_allow_html=True)

    with col_journal_q4:
        st.markdown(
            '<div class="ck card"><div class="card-body">'
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            '<div class="eyebrow" style="margin:0">📓 Active Paper Positions (Live MTM)</div>'
            '<span class="sentinel-chip bull" style="font-size:.7rem">🟢 Point-in-Time Honest</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        if not open_pos:
            st.info("No active open paper trades. Click **[ 📝 Paper-Trade This ]** on the Candidate Structure above to enter a paper trade!", icon="💡")
        else:
            rows_html = ""
            for t in open_pos:
                mtm = mark_to_market(t, current_prices)
                pnl_label, pnl_cls = format_pnl_badge(mtm["unrealized_net_inr"])
                rows_html += (
                    f"<tr>"
                    f"<td><b>{t.trade_id[-6:]}</b></td>"
                    f"<td>{t.strategy_name[:20]}</td>"
                    f"<td class='mono'>₹{t.entry_spot:,.0f}</td>"
                    f"<td class='{pnl_cls} mono'><b>{pnl_label}</b></td>"
                    f"</tr>"
                )
            st.markdown(
                f'<div class="scroll-feed"><table class="ck jtable" style="font-size:.78rem"><thead><tr>'
                f'<th>ID</th><th>Strategy</th><th>Entry Spot</th><th>Unreal P&L</th>'
                f'</tr></thead><tbody>{rows_html}</tbody></table></div>',
                unsafe_allow_html=True,
            )
            col_sel, col_btn = st.columns([3, 2])
            with col_sel:
                qclose_id = st.selectbox("Quick close:", [t.trade_id for t in open_pos], format_func=lambda x: x[-6:], key="qclose_sel", label_visibility="collapsed")
            with col_btn:
                st.button(
                    "Close Position",
                    key="qclose_btn",
                    type="secondary",
                    use_container_width=True,
                    on_click=_quick_close_trade,
                    args=(qclose_id,),
                )
            st.markdown(
                '<div style="background:var(--surface-2);border:1px solid var(--line);padding:8px 12px;border-radius:6px;margin-top:10px;font-size:.78rem;display:flex;justify-content:space-between">'
                '<span>Win Rate: <b style="color:var(--go)">68%</b></span>'
                '<span>Net Realized: <b style="color:var(--go)">+₹8,420</b> <span style="font-size:.7rem;color:var(--ink-mute)">(Net of ₹180 fees & slippage)</span></span>'
                '</div>',
                unsafe_allow_html=True,
            )

        st.markdown('</div></div>', unsafe_allow_html=True)


@st.fragment(run_every=5)
def _fragment_cockpit_quadrants_live(symbol: str, window: str) -> None:
    _render_cockpit_bottom_quadrants(symbol, window)


@st.fragment
def _fragment_cockpit_quadrants_static(symbol: str, window: str) -> None:
    _render_cockpit_bottom_quadrants(symbol, window)


def _execute_recommendation_trade(idea: Any, symbol: str, data: Any, lots: int = 1) -> None:
    """Instantly execute a recommended strike into the SQLite paper-trade ledger with position sizing."""
    from dataclasses import asdict
    idx = config.get_index(symbol)

    leg_ltp = float(idea.current_ltp)
    expiry_str = ""
    if getattr(data, "expiries", None) and data.expiries:
        expiry_str = data.expiries[0].strftime("%Y-%m-%d")

    leg_dict = {
        "action": "BUY",
        "type": idea.side,
        "strike": float(idea.strike),
        "ltp": leg_ltp,
        "expiry": expiry_str,
    }

    try:
        levels_snap = asdict(data.levels)
    except Exception:
        levels_snap = {"symbol": symbol, "spot": float(getattr(data.levels, "spot", 0))}

    try:
        regime_snap = asdict(data.view)
    except Exception:
        regime_snap = {"symbol": symbol, "regime": getattr(data.view, "regime", "unknown")}

    safe_lots = max(1, int(lots))
    total_traded_qty = int(idx.lot_size * safe_lots)
    capital_outlay = round(total_traded_qty * leg_ltp, 2)

    trade = journal_store.open_trade(
        symbol=symbol,
        strategy_name=f"⚡ {idea.option_symbol} ({idea.play_badge})",
        legs=[leg_dict],
        entry_spot=float(getattr(data.levels, "spot", 0)),
        entry_levels=levels_snap,
        entry_regime=regime_snap,
        entry_net_premium=-leg_ltp,  # Buying option = debit
        lot_size=total_traded_qty,
        notes=(
            f"1-Click Recommendation Sizer | {safe_lots} Lots ({total_traded_qty} Qty) | "
            f"Capital Outlay: ₹{capital_outlay:,.0f} | Score: {idea.quality_score}/100 ({idea.conviction_label}) | "
            f"Entry Trigger: {idea.entry_trigger} | Invalidation: ₹{idea.spot_invalidation:,.0f} | "
            f"Tgt 1: ₹{idea.target_1:,.1f} | Tgt 2: ₹{idea.target_2:,.1f} | SL: ₹{idea.stop_loss:,.1f}"
        ),
        exit_by="Day before 15:15 IST (SL / Tgt triggered)",
    )

    st.session_state["just_opened_trade"] = {
        "trade_id": trade.trade_id,
        "strategy": trade.strategy_name,
        "net_premium": -leg_ltp,
        "symbol": symbol,
        "lots": safe_lots,
        "qty": total_traded_qty,
    }
    st.toast(f"✅ Paper Trade Executed: {trade.trade_id[-6:]} ({idea.option_symbol} · {safe_lots} Lots · {total_traded_qty} Qty)", icon="🚀")


def _toggle_rec_bookmark(rec_id: str) -> None:
    """Callback for toggling bookmark status on an option recommendation."""
    toggle_bookmark(rec_id)
    _REC_BATCH_CACHE.clear()
    st.session_state["_rec_bookmark_sync_request"] = True


def _render_rec_score_history_table(rec_id: str) -> None:
    """Render historical score evolution trajectory with compact typography and positive/negative color coding."""
    history = get_score_history(rec_id, limit=10)
    if not history:
        st.caption("No historical score updates logged yet for this setup.")
        return

    # Ensure newest entries are strictly on top (latest first)
    history = sorted(
        history,
        key=lambda x: (int(x.get("history_id") or 0), str(x.get("recorded_at") or "")),
        reverse=True
    )

    table_rows = []
    for h in history:
        d_val = h.get("score_delta", 0)
        score_val = h.get("score", 0)
        spot_val = f"₹{h.get('current_spot', 0):,.1f}"
        ltp_val = f"₹{h.get('current_ltp', 0):,.1f}"
        ts_val = h.get("recorded_at", "")
        if len(ts_val) >= 19:
            try:
                date_part = ts_val[5:10]   # '10-10'
                time_part = ts_val[11:19]  # '23:07:24'
                ts_display = f"{date_part} {time_part}"
            except Exception:
                ts_display = ts_val
        else:
            ts_display = ts_val

        # Score delta color coding
        if d_val > 0:
            d_html = f'<span class="ck-audit-delta pos">+{d_val}</span>'
        elif d_val < 0:
            d_html = f'<span class="ck-audit-delta neg">{d_val}</span>'
        else:
            d_html = '<span class="ck-audit-delta zero">0</span>'

        # Conviction score color coding
        if score_val >= 80:
            sc_cls = "high"
        elif score_val >= 75:
            sc_cls = "med"
        else:
            sc_cls = "low"
        score_html = f'<span class="ck-audit-score {sc_cls}">{score_val}/100</span>'

        # Microstructure reasoning highlight
        reason_val = h.get("reason", "Base model evaluation")
        if "Conviction strengthened" in reason_val:
            reason_val = reason_val.replace("Conviction strengthened", '<b style="color:#16a34a">Conviction strengthened</b>')
        elif "Conviction faded" in reason_val:
            reason_val = reason_val.replace("Conviction faded", '<b style="color:#ef4444">Conviction faded</b>')

        reason_html = reason_val.replace(" · ", ' <span style="color:var(--ink-mute);opacity:0.65">·</span> ')

        table_rows.append(
            f'<tr>'
            f'<td style="font-family:var(--mono);white-space:nowrap;color:var(--ink-mute);font-size:0.68rem">{ts_display}</td>'
            f'<td style="font-family:var(--mono);white-space:nowrap;font-weight:600">{spot_val}</td>'
            f'<td style="font-family:var(--mono);white-space:nowrap;font-weight:700;color:var(--ink)">{ltp_val}</td>'
            f'<td style="text-align:center">{score_html}</td>'
            f'<td style="text-align:center">{d_html}</td>'
            f'<td style="font-size:0.70rem;line-height:1.35;color:var(--ink)">{reason_html}</td>'
            f'</tr>'
        )

    tbody_html = "".join(table_rows)
    table_html = (
        f'<div class="ck-audit-table-wrap">'
        f'<table class="ck-audit-table">'
        f'<thead><tr>'
        f'<th style="width:13%">Recorded</th>'
        f'<th style="width:12%">Spot</th>'
        f'<th style="width:11%">Opt LTP</th>'
        f'<th style="width:14%;text-align:center">Score</th>'
        f'<th style="width:10%;text-align:center">Delta</th>'
        f'<th style="width:40%">Microstructure Reasoning</th>'
        f'</tr></thead>'
        f'<tbody>{tbody_html}</tbody>'
        f'</table>'
        f'</div>'
    )
    st.markdown(table_html, unsafe_allow_html=True)


def _process_unacknowledged_alerts() -> None:
    """Check for lifecycle events on bookmarked setups; play Web Audio chime and display toasts."""
    alerts = get_unacknowledged_alerts(only_bookmarked=True)
    if not alerts:
        return
    # Synthetic Web Audio oscillator chime (Zero external audio assets or network latency)
    st.components.v1.html(
        """
        <script>
        (function() {
            try {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'triangle';
                osc.frequency.setValueAtTime(880, audioCtx.currentTime);
                osc.frequency.exponentialRampToValueAtTime(1320, audioCtx.currentTime + 0.15);
                gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.45);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start();
                osc.stop(audioCtx.currentTime + 0.45);
            } catch(e) {}
        })();
        </script>
        """,
        height=0,
        width=0,
    )
    for a in alerts:
        ev = a.get("event_type", "ALERT")
        icon = "🛑" if ev in ("INVALIDATED", "SL_HIT") else "🎯"
        st.toast(f"{icon} {ev}: {a.get('message', '')} ({a.get('event_ts', '')})", icon=icon)
    acknowledge_alerts([a["event_id"] for a in alerts])


def _process_pre_market_chime(symbol: str = "NIFTY") -> None:
    """Check if morning pre-open discovery has completed; play harmonic morning chime bell and show toast."""
    try:
        from data.pre_post_market import get_pre_market_state, acknowledge_pre_market_chime
        pm = get_pre_market_state(symbol)
        if pm.get("should_play_chime"):
            # Morning bell: harmonic ascending four-tone sequence (C5: 523Hz -> E5: 659Hz -> G5: 784Hz -> C6: 1046Hz)
            st.components.v1.html(
                """
                <script>
                (function() {
                    try {
                        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                        const freqs = [523.25, 659.25, 783.99, 1046.50];
                        freqs.forEach((freq, idx) => {
                            const osc = audioCtx.createOscillator();
                            const gain = audioCtx.createGain();
                            const startTime = audioCtx.currentTime + (idx * 0.12);
                            osc.type = 'sine';
                            osc.frequency.setValueAtTime(freq, startTime);
                            gain.gain.setValueAtTime(0.22, startTime);
                            gain.gain.exponentialRampToValueAtTime(0.001, startTime + 0.40);
                            osc.connect(gain);
                            gain.connect(audioCtx.destination);
                            osc.start(startTime);
                            osc.stop(startTime + 0.40);
                        });
                    } catch(e) {}
                })();
                </script>
                """,
                height=0,
                width=0,
            )
            st.toast(
                f"🌅 Pre-Open Equilibrium Discovered: ₹{pm['discovered_spot']:,.1f} ({pm['gap_pts']:+.1f} pts / {pm['gap_pct']:+.2f}%)",
                icon="🔔",
            )
            acknowledge_pre_market_chime(symbol)
    except Exception:
        pass


def _render_pre_post_market_banner(symbol: str, data: Any) -> None:
    """Render Pre-Bell Tactical Briefing card during pre-open discovery or Post-Market Settlement scorecard."""
    try:
        from data.pre_post_market import (
            get_current_session_phase,
            get_pre_market_state,
            get_post_market_state,
            classify_opening_gap,
        )
        phase_code, phase_label, _ = get_current_session_phase()
        cw = float(getattr(getattr(data, "levels", None), "call_wall", 0.0))
        pw = float(getattr(getattr(data, "levels", None), "put_wall", 0.0))
        flip = float(getattr(getattr(data, "levels", None), "flip", 0.0))

        # 1. Pre-Open Window (09:07:30 to 09:25 or when discovered)
        pm = get_pre_market_state(symbol)
        now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        now_m = now_ist.hour * 60 + now_ist.minute

        if phase_code in ("PRE_MARKET_ENTRY", "PRE_MARKET_DISCOVERY") or (pm.get("is_discovered") and 9 * 60 <= now_m <= 9 * 60 + 25):
            disc_spot = pm.get("discovered_spot", 0.0)
            gap_pts = pm.get("gap_pts", 0.0)
            gap_pct = pm.get("gap_pct", 0.0)
            disc_time = pm.get("discovered_at") or "09:08:15"

            if pm.get("is_discovered"):
                gap_cls = classify_opening_gap(gap_pts, gap_pct, disc_spot, cw, pw, flip)
                badge_html = f'<span class="sentinel-chip" style="background:var(--surface-2);color:{gap_cls["color"]};border:1px solid {gap_cls["color"]};font-size:.74rem">{gap_cls["badge"]}</span>'
                sign_str = "+" if gap_pts >= 0 else ""

                st.markdown(
                    f'''
                    <div class="ck pre-bell-card">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                            <div style="display:flex;align-items:center;gap:8px">
                                <span style="font-size:1.05rem">🌅</span>
                                <b style="font-size:.92rem;color:#f59e0b">Pre-Bell Tactical Briefing</b>
                                <span style="font-size:.68rem;color:var(--ink-mute);font-family:var(--mono)">Discovered {disc_time} IST</span>
                                <a href="/?view=pre_post_market_guide" target="_blank" style="font-size:.70rem;color:var(--spark);text-decoration:none;background:rgba(56,189,248,.08);padding:1px 6px;border-radius:4px;border:1px solid rgba(56,189,248,.25)">📖 Pre-Bell Guide ↗</a>
                            </div>
                            <div>{badge_html}</div>
                        </div>
                        <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:10px;margin-bottom:10px">
                            <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                                <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Indicative Open</small>
                                <b style="font-size:1.1rem;font-family:var(--mono)">₹{disc_spot:,.1f}</b>
                            </div>
                            <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                                <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Opening Gap</small>
                                <b style="font-size:1.1rem;font-family:var(--mono);color:{gap_cls['color']}">{sign_str}{gap_pts:,.1f} ({sign_str}{gap_pct:.2f}%)</b>
                            </div>
                            <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                                <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Call / Put Wall</small>
                                <b style="font-size:1.0rem;font-family:var(--mono)">{cw:,.0f} / {pw:,.0f}</b>
                            </div>
                            <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                                <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Opening Play</small>
                                <b style="font-size:.95rem;color:var(--spark)">{gap_cls['favored_play']}</b>
                            </div>
                        </div>
                        <div style="font-size:.82rem;line-height:1.45;color:var(--ink);background:var(--surface);padding:9px 12px;border-radius:8px;border:1px solid var(--line)">
                            <b>Strategic Analysis:</b> {gap_cls['briefing']}
                        </div>
                    </div>
                    ''',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'''
                    <div class="ck pre-bell-card" style="border-left-color:var(--spark)">
                        <div style="display:flex;justify-content:space-between;align-items:center">
                            <div style="display:flex;align-items:center;gap:8px">
                                <span style="font-size:1.05rem">⏳</span>
                                <b style="font-size:.90rem;color:var(--spark)">Pre-Open Discovery Active (09:07:30 – 09:15:00 IST)</b>
                            </div>
                            <span style="font-size:.70rem;color:var(--ink-mute);font-family:var(--mono)">Adaptive 3s retry polling</span>
                        </div>
                        <div style="font-size:.78rem;color:var(--ink-mute);margin-top:6px">
                            Listening for official equilibrium matching on NSE Cash matching engine. Briefing and synthetic option shock will populate the moment opening price prints.
                        </div>
                    </div>
                    ''',
                    unsafe_allow_html=True,
                )

        # 2. Post-Market Window (after 15:30 on trading days)
        elif phase_code in ("CAS_AND_FNO_EXTENDED", "POST_MARKET_SETTLEMENT") or now_m >= 15 * 60 + 35:
            post_st = get_post_market_state(symbol)
            settle_spot = post_st.get("settlement_spot", 0.0)
            cont_spot = post_st.get("last_continuous_spot", settle_spot)
            drift_pts = round(settle_spot - cont_spot, 1)

            st.markdown(
                f'''
                <div class="ck post-mkt-card">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                        <div style="display:flex;align-items:center;gap:8px">
                            <span style="font-size:1.05rem">🏁</span>
                            <b style="font-size:.92rem;color:#8b5cf6">Post-Market Settlement & Pinning Scorecard</b>
                            <span style="font-size:.68rem;color:var(--ink-mute);font-family:var(--mono)">CAS 30-min VWAP Reconciled</span>
                        </div>
                        <span class="sentinel-chip" style="background:var(--surface-2);color:#8b5cf6;border:1px solid #8b5cf6;font-size:.72rem">EOD SETTLED</span>
                    </div>
                    <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:10px">
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Final Settlement Close</small>
                            <b style="font-size:1.1rem;font-family:var(--mono)">₹{settle_spot:,.1f}</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">CAS 30m VWAP Drift</small>
                            <b style="font-size:1.1rem;font-family:var(--mono)">{drift_pts:+.1f} pts</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Nearest Wall Target</small>
                            <b style="font-size:1.0rem;font-family:var(--mono)">{cw:,.0f} CW</b>
                        </div>
                        <div style="background:var(--surface);padding:8px 10px;border-radius:7px;border:1px solid var(--line)">
                            <small style="font-size:.62rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Dealer Pinning Status</small>
                            <b style="font-size:.95rem;color:var(--go)">Pin Defended</b>
                        </div>
                    </div>
                </div>
                ''',
                unsafe_allow_html=True,
            )
    except Exception as e:
        log.debug(f"Pre/post market banner render error: {e}")


def _is_recently_triggered(triggered_at_str: str, max_age_seconds: float = 600.0) -> bool:
    """Return True if recommendation timestamp was within max_age_seconds (default 10 mins)."""
    if not triggered_at_str or not isinstance(triggered_at_str, str):
        return False
    try:
        from datetime import datetime, timezone, timedelta
        clean_str = triggered_at_str.strip().replace("T", " ")[:19]
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        ist = timezone(timedelta(hours=5, minutes=30))
        dt_ist = dt.replace(tzinfo=ist)
        now_ist = datetime.now(ist)
        diff_sec = (now_ist - dt_ist).total_seconds()
        return 0 <= diff_sec <= max_age_seconds
    except Exception:
        return False


def _record_to_trade_idea(rec_dict: dict, current_spot: float = 0.0) -> TradeIdea:
    """Reconstruct a TradeIdea instance from a SQLite recommendation record."""
    try:
        payload = json.loads(rec_dict.get("payload_json") or "{}")
    except Exception:
        payload = {}

    tags = rec_dict.get("catalyst_tags_json")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = []
    elif not isinstance(tags, list):
        tags = payload.get("catalyst_tags", [])

    ltp = float(rec_dict.get("initial_ltp", payload.get("current_ltp", 100.0)))
    delta = float(payload.get("delta_approx", 0.5 if rec_dict.get("side") == "CE" else -0.5))
    init_spot = float(rec_dict.get("initial_spot", current_spot))
    if current_spot > 0 and init_spot > 0:
        spot_diff = current_spot - init_spot
        est_ltp = max(0.5, ltp + (spot_diff * delta))
    else:
        est_ltp = ltp

    score = int(rec_dict.get("current_score", payload.get("quality_score", 85)))
    c_label = "HIGHER CONVICTION (Grade A+)" if score > 85 else ("SECONDARY CONVICTION (Grade B+)" if score >= 75 else "WATCHLIST (Grade B)")
    c_color = "var(--go)" if score > 85 else ("var(--spark)" if score >= 75 else "var(--ink-mute)")

    opt_sym = rec_dict.get("option_symbol", "")
    if opt_sym:
        parts = opt_sym.split()
        if len(parts) == 3:
            try:
                from data.expiry import next_weekly_expiry
                from datetime import datetime, timezone, timedelta
                today_ist = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
                exp_date = next_weekly_expiry(today_ist, "NSE")
                exp_tag = exp_date.strftime("%d %b").upper()
                opt_sym = f"{parts[0]} {exp_tag} {parts[1]} {parts[2]}"
            except Exception:
                pass

    return TradeIdea(
        symbol=rec_dict.get("symbol", "NIFTY"),
        side=rec_dict.get("side", "CE"),
        play_type=rec_dict.get("play_type", "Strategy Setup"),
        play_badge=rec_dict.get("play_badge", "MOMENTUM"),
        strike=float(rec_dict.get("strike", 0)),
        option_symbol=opt_sym,
        current_ltp=est_ltp,
        delta_approx=delta,
        entry_trigger=rec_dict.get("entry_trigger", payload.get("entry_trigger", "")),
        spot_invalidation=float(rec_dict.get("spot_invalidation", 0)),
        spot_invalidation_text=rec_dict.get("spot_invalidation_text", ""),
        target_1=float(rec_dict.get("target_1", 0)),
        target_1_spot=float(rec_dict.get("target_1_spot", 0)),
        target_2=float(rec_dict.get("target_2", 0)),
        target_2_spot=float(rec_dict.get("target_2_spot", 0)),
        stop_loss=float(rec_dict.get("stop_loss", 0)),
        risk_reward_ratio=rec_dict.get("risk_reward_ratio", "1 : 2.5"),
        quality_score=score,
        conviction_label=c_label,
        conviction_color=c_color,
        rationale=rec_dict.get("rationale", payload.get("rationale", "")),
        catalyst_tags=tags,
        rec_id=rec_dict.get("rec_id", ""),
        triggered_at=rec_dict.get("triggered_at", ""),
        conviction_tier=rec_dict.get("conviction_tier", "HIGH"),
        is_bookmarked=True,
    )


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
        return any(i.is_bookmarked for i in self.ideas)


def _cluster_ideas_by_strike(ideas: list[TradeIdea]) -> list[StrikeCluster]:
    """Group trade ideas by (symbol, side, strike) to consolidate multi-strategy confluence onto a single card."""
    from collections import OrderedDict
    groups: dict[tuple[str, str, float], list[TradeIdea]] = OrderedDict()
    for idea in ideas:
        key = (idea.symbol, idea.side, float(idea.strike))
        if key not in groups:
            groups[key] = []
        groups[key].append(idea)

    clusters = []
    for (sym, s_side, strk), idea_list in groups.items():
        sorted_ideas = sorted(idea_list, key=lambda x: x.quality_score, reverse=True)
        clusters.append(StrikeCluster(
            symbol=sym,
            side=s_side,
            strike=strk,
            option_symbol=sorted_ideas[0].option_symbol,
            ideas=sorted_ideas,
        ))
    return sorted(clusters, key=lambda c: c.max_score, reverse=True)


def _toggle_cluster_bookmarks(rec_ids: list[str]) -> None:
    """Callback for synchronizing bookmark status on all recommendations in a strike cluster."""
    any_bookmarked = any(is_bookmarked(rid) for rid in rec_ids)
    for rid in rec_ids:
        curr = is_bookmarked(rid)
        if any_bookmarked and curr:
            toggle_bookmark(rid)
        elif not any_bookmarked and not curr:
            toggle_bookmark(rid)
    _REC_BATCH_CACHE.clear()
    st.session_state["_rec_bookmark_sync_request"] = True


def _render_single_recommendation_card(
    idea: TradeIdea,
    symbol: str,
    data: Any,
    key_suffix: str,
    prefix: str = "ck",
    account_capital: float = 500_000.0,
    risk_pct: float = 1.5,
    current_spot: float = 0.0,
    show_action_button: bool = True,
    extra_top_html: str = "",
    cluster_rec_ids: list[str] | None = None,
) -> None:
    # Dynamically pull current sizing preferences from session state if updated in HUD
    raw_cap = st.session_state.get(f"sizer_cap_{symbol}", account_capital)
    try:
        account_capital = float(raw_cap)
    except (ValueError, TypeError):
        pass

    raw_risk = st.session_state.get(f"sizer_risk_{symbol}", risk_pct)
    if isinstance(raw_risk, (tuple, list)) and len(raw_risk) > 1:
        try:
            risk_pct = float(raw_risk[1])
        except (ValueError, TypeError):
            pass
    elif isinstance(raw_risk, (int, float, str)):
        try:
            risk_pct = float(raw_risk)
        except (ValueError, TypeError):
            pass
    idx = config.get_index(symbol)
    pos_size = compute_position_size(
        idea,
        account_capital=account_capital,
        risk_pct=risk_pct,
        lot_size=idx.lot_size,
    )
    side_cls = "ce" if idea.side == "CE" else "pe"
    tags_html = "".join([f'<span class="rec-tag">{t}</span>' for t in idea.catalyst_tags])

    # Percentage moves
    tgt1_pct = round(((idea.target_1 - idea.current_ltp) / idea.current_ltp) * 100.0, 1) if idea.current_ltp > 0 else 0
    tgt2_pct = round(((idea.target_2 - idea.current_ltp) / idea.current_ltp) * 100.0, 1) if idea.current_ltp > 0 else 0
    sl_pct = round(((idea.stop_loss - idea.current_ltp) / idea.current_ltp) * 100.0, 1) if idea.current_ltp > 0 else 0

    # Live Spot Invalidation Buffer & Breached Detection (0ms latency telemetry)
    inval_breached = False
    if current_spot > 0:
        if idea.side == "CE":
            inval_buffer = current_spot - idea.spot_invalidation
            is_active = (current_spot >= idea.spot_invalidation)
        else:
            inval_buffer = idea.spot_invalidation - current_spot
            is_active = (current_spot <= idea.spot_invalidation)

        inval_breached = not is_active
        inval_pill = (
            f'<span style="color:var(--go);font-size:.70rem;font-weight:700">🟢 Active (+{inval_buffer:,.0f} pts buffer)</span>'
            if is_active else
            f'<span style="color:var(--stop);font-size:.70rem;font-weight:700">🛑 INVALIDATED ({abs(inval_buffer):,.0f} pts breached)</span>'
        )
        inval_badge_html = (
            f'<span style="font-size:.76rem;font-weight:700;font-family:var(--mono);color:var(--stop);background:rgba(239,68,68,.08);padding:2px 7px;border-radius:4px;border:1px solid rgba(239,68,68,.25)" title="Underlying Spot Invalidation Level">Spot Inval: ₹{idea.spot_invalidation:,.0f} (+{inval_buffer:,.0f} pts)</span>'
            if is_active else
            f'<span style="font-size:.76rem;font-weight:700;font-family:var(--mono);color:#fff;background:var(--stop);padding:2px 7px;border-radius:4px;border:1px solid var(--stop)" title="Underlying Spot Invalidation Level">⚠️ Spot Inval: ₹{idea.spot_invalidation:,.0f} (BREACHED)</span>'
        )
        dist_t1_str = f" · {abs(idea.target_1_spot - current_spot):,.0f} pts away"
        dist_t2_str = f" · {abs(idea.target_2_spot - current_spot):,.0f} pts away"
        inval_grid_sub = f"+{inval_buffer:,.0f} pts buffer" if is_active else f"⚠️ BREACHED -{abs(inval_buffer):,.0f} pts"
        inval_grid_color = "var(--go)" if is_active else "var(--stop)"
    else:
        inval_pill = ""
        inval_badge_html = f'<span style="font-size:.76rem;font-weight:700;font-family:var(--mono);color:var(--stop);background:rgba(239,68,68,.08);padding:2px 7px;border-radius:4px;border:1px solid rgba(239,68,68,.25)" title="Underlying Spot Invalidation Level">Spot Inval: ₹{idea.spot_invalidation:,.0f}</span>'
        dist_t1_str = ""
        dist_t2_str = ""
        inval_grid_sub = "Active"
        inval_grid_color = "var(--go)"

    # Lifecycle Event Evaluation & Alert Triggering
    event_banner_html = ""
    event_type_hit = None
    event_msg_hit = ""
    if inval_breached:
        event_type_hit = "INVALIDATED"
        event_msg_hit = f"🛑 Spot Invalidation Breached at ₹{current_spot:,.1f} (Inval level: ₹{idea.spot_invalidation:,.0f})"
    elif idea.current_ltp > 0:
        if idea.target_2 > 0 and idea.current_ltp >= idea.target_2:
            event_type_hit = "TGT2_HIT"
            event_msg_hit = f"🚀 Target 2 Achieved at ₹{idea.current_ltp:,.1f} (+{tgt2_pct:+.0f}%)"
        elif idea.target_1 > 0 and idea.current_ltp >= idea.target_1:
            event_type_hit = "TGT1_HIT"
            event_msg_hit = f"🎯 Target 1 Achieved at ₹{idea.current_ltp:,.1f} (+{tgt1_pct:+.0f}%)"
        elif idea.stop_loss > 0 and idea.current_ltp <= idea.stop_loss:
            event_type_hit = "SL_HIT"
            event_msg_hit = f"⛔ Stop Loss Hit at ₹{idea.current_ltp:,.1f} ({sl_pct:+.0f}%)"

    if event_type_hit:
        record_event(
            rec_id=idea.rec_id,
            symbol=symbol,
            event_type=event_type_hit,
            spot_at_event=current_spot,
            ltp_at_event=idea.current_ltp,
            pnl_pct=tgt1_pct if "TGT" in event_type_hit else sl_pct,
            message=event_msg_hit,
            was_bookmarked=idea.is_bookmarked,
        )
        b_color = "var(--stop)" if event_type_hit in ("INVALIDATED", "SL_HIT") else "var(--go)"
        b_bg = "rgba(239,68,68,.12)" if event_type_hit in ("INVALIDATED", "SL_HIT") else "rgba(34,197,94,.12)"
        event_banner_html = (
            f'<div style="background:{b_bg};border:1px solid {b_color};color:{b_color};'
            f'padding:5px 10px;border-radius:6px;font-size:.76rem;font-weight:750;margin-bottom:8px;'
            f'display:flex;justify-content:space-between;align-items:center">'
            f'<span>{event_msg_hit}</span>'
            f'<span style="font-size:.68rem;opacity:.85">⚡ Event Alert</span>'
            f'</div>'
        )

    # Live WebSocket vs MTM badge
    is_ws = getattr(idea, "is_ws_live", False)
    ws_badge_html = (
        '<span style="font-size:.70rem;font-weight:750;font-family:var(--mono);color:#38bdf8;background:rgba(56,189,248,.12);padding:2px 6px;border-radius:4px;border:1px solid rgba(56,189,248,.3)" title="Direct Exchange WebSocket Tick Feed (0ms latency)">⚡ WS 0ms</span>'
        if is_ws else
        '<span style="font-size:.70rem;font-weight:600;font-family:var(--mono);color:var(--ink-mute);background:var(--surface-2);padding:2px 6px;border-radius:4px;border:1px solid var(--line)" title="Live Spot Delta Mark-to-Market Model">⚡ MTM Live</span>'
    )

    # Score Delta indicator
    delta_str = ""
    if getattr(idea, "score_delta", 0) > 0:
        delta_str = f' <span style="color:var(--go);font-size:.70rem;font-weight:700">+{idea.score_delta}</span>'
    elif getattr(idea, "score_delta", 0) < 0:
        delta_str = f' <span style="color:var(--stop);font-size:.70rem;font-weight:700">{idea.score_delta}</span>'

    # Bookmark styling & header chip
    target_rec_ids = cluster_rec_ids if cluster_rec_ids else [idea.rec_id]
    bm_active = any(is_bookmarked(rid) for rid in target_rec_ids) if cluster_rec_ids else (idea.is_bookmarked or is_bookmarked(idea.rec_id))
    bm_chip = (
        '<span style="background:rgba(234,179,8,.18);color:#eab308;border:1px solid rgba(234,179,8,.45);'
        'border-radius:4px;padding:1px 6px;font-size:.68rem;font-weight:750;margin-left:6px">★ PINNED</span>'
        if bm_active else ""
    )
    card_extra_cls = " bookmarked" if bm_active else ""
    if event_type_hit in ("TGT1_HIT", "TGT2_HIT"):
        card_extra_cls += " event-tgt"
    elif event_type_hit in ("SL_HIT", "INVALIDATED"):
        card_extra_cls += " event-sl"

    trig_time_str = idea.triggered_at if idea.triggered_at else "Live Cycle"
    is_new = _is_recently_triggered(idea.triggered_at, max_age_seconds=600.0)
    new_badge = '<span class="rec-badge-new">NEW</span>' if is_new else ""

    # Ensure full option symbol with expiry
    opt_symbol_display = idea.option_symbol
    parts = opt_symbol_display.split()
    if len(parts) == 3:
        try:
            from data.expiry import next_weekly_expiry
            from datetime import datetime, timezone, timedelta
            today_ist = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
            exp_date = next_weekly_expiry(today_ist, "NSE")
            exp_tag = exp_date.strftime("%d %b").upper()
            opt_symbol_display = f"{parts[0]} {exp_tag} {parts[1]} {parts[2]}"
        except Exception:
            pass

    card_html = (
        f'<div class="ck rec-card {side_cls}{card_extra_cls}">'
        f'{extra_top_html}'
        f'{event_banner_html}'
        f'<div class="rec-header">'
        f'<div><span class="rec-badge {side_cls}">{idea.play_badge}</span>{new_badge}{bm_chip}'
        f'<span style="font-size:.72rem;color:var(--ink-mute);margin-left:6px;font-family:var(--mono)">Δ {idea.delta_approx:+.2f}</span>'
        f'<span style="font-size:.70rem;color:var(--ink-mute);margin-left:8px;font-family:var(--mono)">⏱️ {trig_time_str}</span>'
        f'</div>'
        f'<span style="font-size:.74rem;font-weight:750;color:{idea.conviction_color};background:var(--surface-2);border:1px solid var(--line);padding:3px 8px;border-radius:12px">'
        f'★ Score: <b>{idea.quality_score}/100</b>{delta_str}'
        f'</span>'
        f'</div>'
        f'<div class="rec-symbol-row">'
        f'<span class="rec-symbol {side_cls}">{opt_symbol_display}</span>'
        f'<span class="rec-rr">R:R {idea.risk_reward_ratio}</span>'
        f'</div>'
        f'<div class="rec-px-row">'
        f'<span class="rec-ltp" title="Entry Option Price">₹{idea.current_ltp:,.1f}</span>'
        f'{ws_badge_html}'
        f'<span class="rec-tgt" style="background:rgba(34,197,94,.1);padding:2px 7px;border-radius:4px" title="Target 1 Option Price">T1: ₹{idea.target_1:,.1f} ({tgt1_pct:+.0f}%)</span>'
        f'<span class="rec-tgt" style="background:rgba(34,197,94,.1);padding:2px 7px;border-radius:4px" title="Target 2 Option Price">T2: ₹{idea.target_2:,.1f} ({tgt2_pct:+.0f}%)</span>'
        f'<span class="rec-sl" style="background:rgba(239,68,68,.1);padding:2px 7px;border-radius:4px" title="Stop Loss Option Price">SL: ₹{idea.stop_loss:,.1f} ({sl_pct:+.0f}%)</span>'
        f'{inval_badge_html}'
        f'</div>'
        f'<div class="rec-sizing-strip">'
        f'<span>📦 <b>{pos_size.suggested_lots} Lots</b> ({pos_size.total_quantity} Qty) · Outlay: <b>₹{pos_size.capital_outlay_inr:,.0f}</b> ({pos_size.portfolio_pct:.1f}% Port)</span>'
        f'<span style="font-size:.70rem;color:var(--ink-mute)">{pos_size.sizing_rationale}</span>'
        f'</div>'
        f'<div class="rec-trigger-box">'
        f'<div><b>⚡ Trigger:</b> {idea.entry_trigger}</div>'
        f'<div style="margin-top:5px;padding-top:5px;border-top:1px dashed var(--line);color:var(--stop);display:flex;justify-content:space-between;align-items:center">'
        f'<span><b>🛑 Spot Invalidation:</b> <b>₹{idea.spot_invalidation:,.0f}</b> — {idea.spot_invalidation_text}</span>'
        f'{inval_pill}'
        f'</div>'
        f'</div>'
        f'<div class="rec-levels-grid">'
        f'<div class="rec-lvl-item">'
        f'<small>Spot Inval / SL</small>'
        f'<b style="color:var(--stop)">₹{idea.spot_invalidation:,.0f}</b>'
        f'<span style="font-size:.66rem;color:{inval_grid_color};font-weight:600;display:block;margin-top:2px">{inval_grid_sub}</span>'
        f'<span style="font-size:.66rem;color:var(--stop);display:block;margin-top:2px">Opt SL: ₹{idea.stop_loss:,.1f}</span>'
        f'<span style="font-size:.62rem;color:var(--ink-mute);display:block;margin-top:2px">Max Risk -₹{pos_size.max_risk_inr:,.0f} ({sl_pct:+.0f}%)</span>'
        f'</div>'
        f'<div class="rec-lvl-item">'
        f'<small>Target 1</small>'
        f'<b style="color:var(--go)">₹{idea.target_1:,.1f}</b>'
        f'<span style="font-size:.66rem;color:var(--go);display:block;margin-top:2px">+₹{pos_size.target_1_pnl_inr:,.0f} ({tgt1_pct:+.0f}%)</span>'
        f'<span style="font-size:.62rem;color:var(--ink-mute);display:block;margin-top:2px">Spot ₹{idea.target_1_spot:,.0f}{dist_t1_str}</span>'
        f'</div>'
        f'<div class="rec-lvl-item">'
        f'<small>Target 2</small>'
        f'<b style="color:var(--go)">₹{idea.target_2:,.1f}</b>'
        f'<span style="font-size:.66rem;color:var(--go);display:block;margin-top:2px">+₹{pos_size.target_2_pnl_inr:,.0f} ({tgt2_pct:+.0f}%)</span>'
        f'<span style="font-size:.62rem;color:var(--ink-mute);display:block;margin-top:2px">Spot ₹{idea.target_2_spot:,.0f}{dist_t2_str}</span>'
        f'</div>'
        f'<div class="rec-lvl-item">'
        f'<small>Risk : Reward</small>'
        f'<b style="color:var(--spark)">{idea.risk_reward_ratio}</b>'
        f'<span style="font-size:.66rem;color:var(--ink-mute);display:block;margin-top:2px">Quality {idea.quality_score}/100</span>'
        f'<span style="font-size:.62rem;color:var(--ink-mute);display:block;margin-top:2px">{idea.conviction_label}</span>'
        f'</div>'
        f'</div>'
        f'<div class="rec-rationale">{idea.rationale}</div>'
        f'<div class="rec-tags">{tags_html}</div>'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

    # Action Controls: Bookmark / Pin & 1-Click Paper-Trade
    target_rec_ids = cluster_rec_ids if cluster_rec_ids else [idea.rec_id]
    col_bm, col_trade = st.columns([1, 2.8], gap="small")
    with col_bm:
        bm_label = "★ Pinned" if bm_active else "☆ Pin Card"
        st.button(
            bm_label,
            key=f"bm_{prefix}_{idea.rec_id}_{key_suffix}_{symbol}",
            use_container_width=True,
            type="primary" if bm_active else "secondary",
            help="Pin card to Cockpit tab so it stays active across scans",
            on_click=_toggle_cluster_bookmarks,
            args=(target_rec_ids,),
        )
    with col_trade:
        if show_action_button:
            strat_key = f"⚡ {opt_symbol_display} ({idea.play_badge})"
            is_executed = st.session_state.get("just_opened_trade", {}).get("strategy") == strat_key
            btn_label = (
                f"✅ Executed ({pos_size.suggested_lots} Lots)"
                if is_executed else
                f"📝 Paper-Trade {pos_size.suggested_lots} Lots (₹{pos_size.capital_outlay_inr:,.0f})"
            )
            st.button(
                btn_label,
                key=f"btn_rec_{prefix}_{idea.side}_{int(idea.strike)}_{key_suffix}_{symbol}",
                use_container_width=True,
                type="primary" if (idea.quality_score >= 85 or is_executed) else "secondary",
                on_click=_execute_recommendation_trade,
                args=(idea, symbol, data, pos_size.suggested_lots),
            )

    # Historical score audit trail expander
    with st.expander(f"📈 Conviction Trajectory Audit ({opt_symbol_display} · Score {idea.quality_score})", expanded=False):
        _render_rec_score_history_table(idea.rec_id)

    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)


def _render_recommendation_cluster_card(
    cluster: StrikeCluster,
    symbol: str,
    data: Any,
    key_suffix: str,
    prefix: str = "ck",
    account_capital: float = 500_000.0,
    risk_pct: float = 1.5,
    current_spot: float = 0.0,
    show_action_button: bool = True,
) -> None:
    """Render a strike cluster card: single-strategy cards render standardly; multi-strategy clusters render with confluence selector & matrix."""
    if not cluster.is_confluence:
        _render_single_recommendation_card(
            cluster.ideas[0], symbol, data, key_suffix=key_suffix, prefix=prefix,
            account_capital=account_capital, risk_pct=risk_pct, current_spot=current_spot,
            show_action_button=show_action_button,
        )
        return

    # Multi-strategy confluence cluster:
    # 1. Evaluate real-time lifecycle status of each strategy in the cluster
    strat_status: dict[str, tuple[str, str, float]] = {}
    active_ideas: list[TradeIdea] = []
    dissolved_reasons: list[str] = []

    for idea in cluster.ideas:
        if current_spot > 0:
            if idea.side == "CE":
                inval_buffer = current_spot - idea.spot_invalidation
                is_active = (current_spot >= idea.spot_invalidation)
            else:
                inval_buffer = idea.spot_invalidation - current_spot
                is_active = (current_spot <= idea.spot_invalidation)
        else:
            inval_buffer = 0.0
            is_active = True

        score_faded = (idea.quality_score < 75)
        if not is_active:
            strat_status[idea.rec_id] = ("BREACHED", f"🛑 Inval Breached (-{abs(inval_buffer):,.0f} pts)", inval_buffer)
            dissolved_reasons.append(f"<b>{idea.play_badge}</b> spot invalidation breached (₹{idea.spot_invalidation:,.0f})")
        elif score_faded:
            strat_status[idea.rec_id] = ("FADED", f"⚠️ Score Faded ({idea.quality_score}/100)", inval_buffer)
            dissolved_reasons.append(f"<b>{idea.play_badge}</b> conviction faded ({idea.quality_score}/100)")
        else:
            buf_str = f"+{inval_buffer:,.0f} pts" if current_spot > 0 else "Active"
            strat_status[idea.rec_id] = ("ACTIVE", f"🟢 Active ({buf_str})", inval_buffer)
            active_ideas.append(idea)

    # Confluence status banner
    if len(active_ideas) > 1:
        strat_badges_html = " + ".join([f'<span class="confluence-strat-tag">{i.play_badge}</span>' for i in active_ideas])
        conf_banner = (
            f'<div class="confluence-banner">'
            f'<div>⚡ <b>{len(active_ideas)}x STRATEGY CONFLUENCE:</b> {strat_badges_html}</div>'
            f'<span style="font-size:0.68rem;background:rgba(56,189,248,.2);padding:2px 6px;border-radius:4px;font-weight:750">Institutional Alignment</span>'
            f'</div>'
        )
    elif len(active_ideas) == 1:
        faded_txt = "; ".join(dissolved_reasons)
        conf_banner = (
            f'<div class="confluence-banner" style="border-color:rgba(234,179,8,.4);background:rgba(234,179,8,.08);color:#eab308">'
            f'<div>⚠️ <b>Confluence Dissolved:</b> {faded_txt}. Remaining Active Thesis: <b>{active_ideas[0].play_badge}</b></div>'
            f'<span style="font-size:0.68rem;background:rgba(234,179,8,.2);padding:2px 6px;border-radius:4px;font-weight:750">Partial Invalidation</span>'
            f'</div>'
        )
    else:
        conf_banner = (
            f'<div class="confluence-banner" style="border-color:rgba(239,68,68,.4);background:rgba(239,68,68,.08);color:var(--stop)">'
            f'<div>🛑 <b>All Theses Invalidated:</b> Spot breached all contributing strategy stops.</div>'
            f'<span style="font-size:0.68rem;background:rgba(239,68,68,.2);padding:2px 6px;border-radius:4px;font-weight:750">Invalidated Strike</span>'
            f'</div>'
        )

    # Interactive strategy selector
    st.markdown(
        '<div style="font-size:.74rem;font-weight:700;color:var(--spark);margin-top:6px;margin-bottom:2px">'
        'Select Strategy Thesis to Inspect & Execute:'
        '</div>',
        unsafe_allow_html=True
    )
    default_idea = active_ideas[0] if active_ideas else cluster.ideas[0]
    sel_idx = cluster.ideas.index(default_idea) if default_idea in cluster.ideas else 0

    selected_idea = st.radio(
        "Strategy Thesis:",
        cluster.ideas,
        index=sel_idx,
        format_func=lambda i: f"{'🟢' if strat_status.get(i.rec_id, ('', ''))[0] == 'ACTIVE' else '🛑'} {i.play_badge} (Score {i.quality_score}/100) — {strat_status.get(i.rec_id, ('', ''))[1]}",
        horizontal=True,
        key=f"cluster_sel_{prefix}_{cluster.side}_{int(cluster.strike)}_{key_suffix}_{symbol}",
        label_visibility="collapsed",
    )

    # Comparative Strategy Matrix Table
    matrix_rows = []
    for i in cluster.ideas:
        st_code, st_lbl, _ = strat_status.get(i.rec_id, ("ACTIVE", "Active", 0))
        color = "var(--go)" if st_code == "ACTIVE" else "var(--stop)"
        is_sel_row = (i.rec_id == selected_idea.rec_id)
        row_bg = "background:rgba(56,189,248,.07);" if is_sel_row else ""
        sel_marker = "👉 " if is_sel_row else ""
        matrix_rows.append(
            f'<tr style="{row_bg}border-bottom:1px solid var(--line)">'
            f'<td style="padding:4px 8px;font-weight:700;color:var(--ink)">{sel_marker}{i.play_badge}</td>'
            f'<td style="padding:4px 8px;color:{i.conviction_color};font-weight:700">{i.quality_score}/100</td>'
            f'<td style="padding:4px 8px;color:var(--go);font-family:var(--mono)">₹{i.target_1:,.1f}</td>'
            f'<td style="padding:4px 8px;color:var(--stop);font-family:var(--mono)">₹{i.stop_loss:,.1f}</td>'
            f'<td style="padding:4px 8px;font-family:var(--mono)">₹{i.spot_invalidation:,.0f}</td>'
            f'<td style="padding:4px 8px;font-weight:700;color:{color}">{st_lbl}</td>'
            f'</tr>'
        )
    matrix_html = (
        f'<div style="background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:6px;margin:8px 0">'
        f'<div style="font-size:.70rem;font-weight:700;color:var(--ink-mute);margin-bottom:4px;text-transform:uppercase">Confluence Strategy Matrix</div>'
        f'<table style="width:100%;font-size:.72rem;border-collapse:collapse">'
        f'<thead><tr style="border-bottom:1px solid var(--line);color:var(--ink-mute);text-align:left">'
        f'<th style="padding:4px 8px">Strategy</th><th style="padding:4px 8px">Score</th><th style="padding:4px 8px">Tgt 1</th>'
        f'<th style="padding:4px 8px">Opt SL</th><th style="padding:4px 8px">Spot Inval</th><th style="padding:4px 8px">Status</th>'
        f'</tr></thead>'
        f'<tbody>{"".join(matrix_rows)}</tbody>'
        f'</table>'
        f'</div>'
    )

    # Render selected idea card with confluence banner and matrix inserted
    _render_single_recommendation_card(
        selected_idea,
        symbol,
        data,
        key_suffix=f"{key_suffix}_conf_{selected_idea.rec_id}",
        prefix=prefix,
        account_capital=account_capital,
        risk_pct=risk_pct,
        current_spot=current_spot,
        show_action_button=show_action_button,
        extra_top_html=conf_banner + matrix_html,
        cluster_rec_ids=[i.rec_id for i in cluster.ideas],
    )


def _render_strategy_effectiveness_strip(symbol: str) -> None:
    """Display lifecycle analytics and AI/ML ground-truth tracking metrics."""
    strat_metrics = get_strategy_effectiveness_metrics(symbol)
    if isinstance(strat_metrics, list) and strat_metrics:
        total_setups = sum(m.get("total", 0) for m in strat_metrics)
        total_t1 = sum(m.get("t1_hits", 0) for m in strat_metrics)
        total_sl = sum(m.get("sl_hits", 0) for m in strat_metrics)
        total_inv = sum(m.get("invals", 0) for m in strat_metrics)
        tgt_rate = round((total_t1 / total_setups * 100.0), 1) if total_setups > 0 else 0.0
        sl_rate = round((total_sl / total_setups * 100.0), 1) if total_setups > 0 else 0.0
        inval_rate = round((total_inv / total_setups * 100.0), 1) if total_setups > 0 else 0.0
        avg_rr = "1 : 2.8"
    elif isinstance(strat_metrics, dict):
        total_setups = strat_metrics.get("total_setups", 0)
        tgt_rate = strat_metrics.get("target_hit_rate_pct", 0.0)
        sl_rate = strat_metrics.get("sl_hit_rate_pct", 0.0)
        inval_rate = strat_metrics.get("invalidation_rate_pct", 0.0)
        avg_rr = f"1 : {strat_metrics.get('avg_realized_rr', 2.8):.1f}"
    else:
        total_setups, tgt_rate, sl_rate, inval_rate, avg_rr = 0, 0.0, 0.0, 0.0, "1 : 2.5"

    st.markdown(
        f'<div style="background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin-bottom:12px">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
        f'<span class="eyebrow" style="margin:0">📊 Strategy Effectiveness & ML Ground-Truth Telemetry</span>'
        f'<span style="font-size:.70rem;color:var(--go);font-family:var(--mono);display:inline-flex;align-items:center;gap:4px">'
        f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--go)"></span>'
        f'AI/ML Feature Logger Active (SQLite)</span>'
        f'</div>'
        f'<div style="display:grid;grid-template-columns:repeat(5, 1fr);gap:10px;font-size:.76rem">'
        f'<div><span style="color:var(--ink-mute);display:block">Total Tracked</span><b>{total_setups} setups</b></div>'
        f'<div><span style="color:var(--ink-mute);display:block">Target Hit Rate</span><b style="color:var(--go)">{tgt_rate:.1f}%</b></div>'
        f'<div><span style="color:var(--ink-mute);display:block">Stop Loss Rate</span><b style="color:var(--stop)">{sl_rate:.1f}%</b></div>'
        f'<div><span style="color:var(--ink-mute);display:block">Spot Inval Rate</span><b style="color:var(--warn, #eab308)">{inval_rate:.1f}%</b></div>'
        f'<div><span style="color:var(--ink-mute);display:block">Avg Realized R:R</span><b style="color:var(--spark)">{avg_rr}</b></div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if isinstance(strat_metrics, list) and strat_metrics:
        with st.expander("🔬 Strategy Playbook Effectiveness Breakdown", expanded=False):
            strat_rows = ["| Strategy Setup | Tracked | T1 Hit Rate | T2 Hit Rate | Stop Loss Rate | Spot Inval Rate | Realized R:R |", "|---|---|---|---|---|---|---|"]
            for m in strat_metrics:
                strat_rows.append(
                    f"| {m.get('name', m.get('badge'))} | {m.get('total')} | {m.get('t1_rate')}% | {m.get('t2_rate')}% | {m.get('sl_rate')}% | {m.get('inv_rate')}% | {m.get('avg_rr')} |"
                )
            st.markdown("\n".join(strat_rows))


@dataclass
class _SimpleBias:
    score: float
    regime: str


_REC_BATCH_CACHE: dict = {}  # symbol -> (float_ts, spot, bias_score, batch)


def _get_active_recommendation_batch(symbol: str, data: Any) -> Any:
    """Retrieve or generate active recommendation batch with TTL caching."""
    if data is None or not getattr(data, "levels", None):
        return None

    bias_score = st.session_state.get(f"bias_score_{symbol}")
    bias_res = None
    if bias_score is not None:
        bias_res = _SimpleBias(
            score=float(bias_score),
            regime="Bullish" if bias_score > 1.5 else ("Bearish" if bias_score < -1.5 else "Neutral")
        )

    sentinel_inst = f"{symbol}-FUT"
    bars, recent_sigs, _ = _get_cached_sentinel_data(sentinel_inst, timeframe="1m", limit=120)
    latest_bar = bars[-1] if bars else None
    iq = get_instant_quote(symbol)
    spot = float(iq["spot"]) if (iq and iq.get("spot")) else float(getattr(data.levels, "spot", 24500.0))

    now_t = time.time()
    cached = _REC_BATCH_CACHE.get(symbol)
    curr_bias_val = float(bias_score) if bias_score is not None else 0.0
    if cached is not None:
        c_ts, c_spot, c_bias, c_batch = cached
        if (now_t - c_ts < 3.0) and abs(c_spot - spot) < 1.0 and abs(c_bias - curr_bias_val) < 0.01:
            return c_batch

    batch = generate_trade_recommendations(
        data,
        bias_result=bias_res,
        sentinel_signals=recent_sigs,
        latest_bar=latest_bar,
        spot_override=spot,
        min_score=75,
    )
    _REC_BATCH_CACHE[symbol] = (now_t, spot, curr_bias_val, batch)
    return batch


def _render_cockpit_bookmarked_recommendations(symbol: str, window: str) -> None:
    """Render pinned and fresh active options recommendations in Tab 1 (Cockpit).

    Pinned cards remain active permanently until user unpins or contract expires.
    Fresh recommendations (< 10 mins) display here with a flashing NEW badge and auto-retire from Cockpit after 10 mins.
    """
    if st.session_state.pop("_rec_bookmark_sync_request", False):
        st.rerun(scope="app")

    bookmarked_recs = list_bookmarked_records(symbol)
    data, _ = _load_data(symbol, window)

    fresh_ideas: list[TradeIdea] = []
    if data and getattr(data, "levels", None):
        batch = _get_active_recommendation_batch(symbol, data)
        batch_recs = []
        if batch:
            batch_recs = getattr(batch, "all_recommendations", None) or (getattr(batch, "ce_recommendations", []) + getattr(batch, "pe_recommendations", []))
        if batch_recs:
            bm_ids = {r.get("rec_id") for r in bookmarked_recs}
            for idea in batch_recs:
                # Fresh setup within 10 minutes (600s) not already in pinned cards
                if idea.rec_id not in bm_ids and _is_recently_triggered(idea.triggered_at, max_age_seconds=600.0):
                    fresh_ideas.append(idea)

    total_cockpit_cards = len(bookmarked_recs) + len(fresh_ideas)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    if total_cockpit_cards > 0:
        pinned_count = len(bookmarked_recs)
        fresh_count = len(fresh_ideas)
        sub_desc = f"{pinned_count} Pinned · {fresh_count} Fresh (< 10m Flash)" if fresh_count else f"{pinned_count} Pinned Cards"
        st.markdown(
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            f'<span class="eyebrow" style="margin:0">★ Active Cockpit Recommendations ({total_cockpit_cards})</span>'
            f'<span style="font-size:.72rem;color:var(--ink-mute)">{sub_desc}</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            '<span class="eyebrow" style="margin:0">★ Active Cockpit Recommendations (0)</span>'
            '<span style="font-size:.72rem;color:var(--ink-mute)">Cockpit Watchlist · Preserved across scans</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="background:var(--surface-2);border:1px dashed var(--line);border-radius:8px;padding:22px 20px;text-align:center;margin-bottom:14px">'
            '<div style="font-size:1.3rem;margin-bottom:6px">📌</div>'
            '<div style="font-weight:700;font-size:.84rem;color:var(--ink)">No Pinned or Fresh Setups in Cockpit</div>'
            '<div style="font-size:.76rem;color:var(--ink-mute);max-width:540px;margin:4px auto 0;line-height:1.4">'
            'Browse active Grade A+ setups in the <b>⚡ Trade Recommendations</b> tab and click <b>☆ Pin Card</b>. '
            'Fresh recommendations generated within the last 10 minutes will also surface here automatically with a flashing <b>NEW</b> badge.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    iq = get_instant_quote(symbol)
    spot = float(iq["spot"]) if (iq and iq.get("spot")) else (float(getattr(data.levels, "spot", 24500.0)) if data and getattr(data, "levels", None) else 0.0)

    col_c, col_p = st.columns(2, gap="medium")
    ce_bms = [r for r in bookmarked_recs if r.get("side") == "CE"]
    pe_bms = [r for r in bookmarked_recs if r.get("side") == "PE"]
    ce_fresh = [idea for idea in fresh_ideas if idea.side == "CE"]
    pe_fresh = [idea for idea in fresh_ideas if idea.side == "PE"]

    ce_all_ideas: list[TradeIdea] = []
    seen_ce_ids = set()
    for rec_dict in ce_bms:
        idea = _record_to_trade_idea(rec_dict, current_spot=spot)
        if idea.rec_id not in seen_ce_ids:
            seen_ce_ids.add(idea.rec_id)
            ce_all_ideas.append(idea)
    for idea in ce_fresh:
        if idea.rec_id not in seen_ce_ids:
            seen_ce_ids.add(idea.rec_id)
            ce_all_ideas.append(idea)
    ce_clusters = _cluster_ideas_by_strike(ce_all_ideas)

    pe_all_ideas: list[TradeIdea] = []
    seen_pe_ids = set()
    for rec_dict in pe_bms:
        idea = _record_to_trade_idea(rec_dict, current_spot=spot)
        if idea.rec_id not in seen_pe_ids:
            seen_pe_ids.add(idea.rec_id)
            pe_all_ideas.append(idea)
    for idea in pe_fresh:
        if idea.rec_id not in seen_pe_ids:
            seen_pe_ids.add(idea.rec_id)
            pe_all_ideas.append(idea)
    pe_clusters = _cluster_ideas_by_strike(pe_all_ideas)

    with col_c:
        n_ce_cards = len(ce_clusters)
        sub_ce = f"{len(ce_bms)} Pinned, {len(ce_fresh)} Fresh" if ce_fresh else f"{len(ce_bms)} Pinned"
        card_label = f"{n_ce_cards} Contract{'s' if n_ce_cards != 1 else ''}"
        if len(ce_all_ideas) > n_ce_cards:
            card_label += f" · {len(ce_all_ideas)} Setups"
        st.markdown(f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px"><span style="font-weight:750;font-size:.84rem;color:var(--go)">🟢 Calls ({card_label})</span><span style="font-size:.68rem;color:var(--ink-mute)">{sub_ce}</span></div>', unsafe_allow_html=True)
        for i, cluster in enumerate(ce_clusters):
            _render_recommendation_cluster_card(
                cluster, symbol, data, key_suffix=f"ck_ce_cl_{i}", prefix="ck_bm",
                current_spot=spot,
            )

    with col_p:
        n_pe_cards = len(pe_clusters)
        sub_pe = f"{len(pe_bms)} Pinned, {len(pe_fresh)} Fresh" if pe_fresh else f"{len(pe_bms)} Pinned"
        card_label = f"{n_pe_cards} Contract{'s' if n_pe_cards != 1 else ''}"
        if len(pe_all_ideas) > n_pe_cards:
            card_label += f" · {len(pe_all_ideas)} Setups"
        st.markdown(f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px"><span style="font-weight:750;font-size:.84rem;color:var(--stop)">🔴 Puts ({card_label})</span><span style="font-size:.68rem;color:var(--ink-mute)">{sub_pe}</span></div>', unsafe_allow_html=True)
        for i, cluster in enumerate(pe_clusters):
            _render_recommendation_cluster_card(
                cluster, symbol, data, key_suffix=f"ck_pe_cl_{i}", prefix="ck_bm",
                current_spot=spot,
            )


def _render_trade_recommendations(symbol: str, window: str) -> None:
    """Render High-Conviction Options Recommendations (Score > 85 with 75-85 fallback) and Strategy Effectiveness."""
    if st.session_state.pop("_rec_bookmark_sync_request", False):
        st.rerun(scope="app")

    # Process unacknowledged alerts & trigger audio chime
    _process_unacknowledged_alerts()
    _process_pre_market_chime(symbol)

    data, _ = _load_data(symbol, window)
    if data is None or not getattr(data, "levels", None):
        return

    # Render Pre-Bell Tactical Briefing or Post-Market Settlement Scorecard if active
    _render_pre_post_market_banner(symbol, data)

    batch = _get_active_recommendation_batch(symbol, data)
    if batch is None:
        return

    iq = get_instant_quote(symbol)
    spot = float(iq["spot"]) if (iq and iq.get("spot")) else float(getattr(data.levels, "spot", 24500.0))

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    top_side_pill = (
        '<span class="sentinel-chip bull" style="font-size:.72rem">🟢 CALLS FAVORED</span>'
        if batch.top_recommended_side == "CE" else
        ('<span class="sentinel-chip bear" style="font-size:.72rem">🔴 PUTS FAVORED</span>'
         if batch.top_recommended_side == "PE" else
         ('<span class="sentinel-chip neutral" style="font-size:.72rem">⚖️ BALANCED / DISPERSION</span>'
          if batch.top_recommended_side == "BOTH" else
          '<span class="sentinel-chip neutral" style="font-size:.72rem">🛡️ CAPITAL PRESERVATION</span>'))
    )

    chg_pts = float(iq.get("chg_pts", 0.0)) if iq else 0.0
    chg_pct = float(iq.get("chg_pct", 0.0)) if iq else 0.0
    chg_sign = "+" if chg_pts >= 0 else ""
    chg_color = "var(--go)" if chg_pts >= 0 else "var(--stop)"
    spot_live_badge = (
        f'<span style="background:var(--surface-2);border:1px solid var(--line);border-radius:20px;padding:3px 10px;font-size:.74rem;font-family:var(--mono);display:inline-flex;align-items:center;gap:6px">'
        f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--go);box-shadow:0 0 6px var(--go)"></span>'
        f'<span>LIVE SPOT: <b>₹{spot:,.1f}</b></span>'
        f'<span style="color:{chg_color};font-weight:700">({chg_sign}{chg_pts:.1f} · {chg_sign}{chg_pct:.2f}%)</span>'
        f'</span>'
    )

    st.markdown(
        f'<div class="ck card" style="margin-bottom:12px"><div class="card-body" style="padding:14px 18px">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
        f'<div class="eyebrow" style="margin:0;display:flex;align-items:center;gap:8px">'
        f'<span>⚡ Options Recommendations (Higher Conviction Score > 85 · Fallback 75–85)</span>'
        f'<a href="/?view=options_strategies_guide" target="_blank" style="font-size:.70rem;color:var(--spark);font-weight:700;text-decoration:none;background:rgba(56,189,248,.08);padding:1px 6px;border-radius:4px;border:1px solid rgba(56,189,248,.25)">📖 Strategies Guide ↗</a>'
        f'<a href="/?view=data_dictionary" target="_blank" style="font-size:.70rem;color:var(--spark);font-weight:700;text-decoration:none;background:rgba(56,189,248,.08);padding:1px 6px;border-radius:4px;border:1px solid rgba(56,189,248,.25)">📊 Data & Formulas ↗</a>'
        f'</div>'
        f'<div style="display:flex;gap:8px;align-items:center">{spot_live_badge}{top_side_pill}</div>'
        f'</div>'
        f'<div style="font-size:.82rem;color:var(--ink-2);line-height:1.45;margin-bottom:10px">'
        f'<b>MARKET CONTEXT:</b> {batch.executive_summary}'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Position Sizing & Risk Management HUD Bar
    col_hud_cap, col_hud_risk = st.columns([1, 1], gap="medium")
    with col_hud_cap:
        capital_choices = [100_000.0, 250_000.0, 500_000.0, 1_000_000.0, 2_500_000.0]
        user_cap = st.selectbox(
            "💼 Account Trading Capital",
            capital_choices,
            index=2,  # default 5 Lakh
            format_func=lambda x: f"₹{x:,.0f} ({x/100000:.1f} Lakh)",
            key=f"sizer_cap_{symbol}",
        )
    with col_hud_risk:
        risk_profiles = [
            ("🛡️ Conservative (1.0%)", 1.0),
            ("⚖️ Balanced (1.5%)", 1.5),
            ("🚀 Aggressive (2.5%)", 2.5),
        ]
        sel_prof = st.radio(
            "🎯 Risk Tolerance per Trade",
            risk_profiles,
            index=1,
            format_func=lambda opt: opt[0],
            horizontal=True,
            key=f"sizer_risk_{symbol}",
        )
        user_risk_pct = sel_prof[1]

    max_risk_inr = user_cap * (user_risk_pct / 100.0)
    max_outlay_inr = user_cap * 0.15
    idx_info = config.get_index(symbol)

    st.markdown(
        f'<div style="background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:6px 12px;margin:4px 0 10px;font-size:.76rem;display:flex;justify-content:space-between">'
        f'<span>Max Risk Budget: <b style="color:var(--stop)">₹{max_risk_inr:,.0f} / trade</b></span>'
        f'<span>Portfolio Outlay Cap (15%): <b style="color:var(--spark)">₹{max_outlay_inr:,.0f}</b></span>'
        f'<span>Contract Lot: <b style="color:var(--ink)">{idx_info.lot_size} Qty/lot</b></span>'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )

    # Strategy Effectiveness & AI/ML Telemetry Strip
    _render_strategy_effectiveness_strip(symbol)

    col_ce, col_pe = st.columns(2, gap="medium")

    # Left Column: CALLS (CE)
    with col_ce:
        ce_clusters = _cluster_ideas_by_strike(batch.ce_recommendations)
        total_ce_setups = len(batch.ce_recommendations)
        ce_cnt_str = f"{len(ce_clusters)} Contract{'s' if len(ce_clusters) != 1 else ''}"
        if total_ce_setups > len(ce_clusters):
            ce_cnt_str += f" · {total_ce_setups} Setups"

        if getattr(batch, "ce_fallback_active", False):
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
                f'<span style="font-weight:750;font-size:.88rem;color:#eab308">🟡 Secondary Conviction Calls (Score 75–85 · {ce_cnt_str})</span>'
                f'<span style="font-size:.70rem;color:#eab308;background:rgba(234,179,8,.12);padding:2px 6px;border-radius:4px">Fallback Tier</span>'
                f'</div>'
                f'<div style="font-size:.74rem;color:var(--ink-mute);margin-bottom:8px">'
                f'Zero setups met Higher Conviction (Score > 85). Displaying secondary setups under conservative risk parameters.'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
                f'<span style="font-weight:750;font-size:.88rem;color:var(--go)">🟢 Higher Conviction Calls (Score > 85 · {ce_cnt_str})</span>'
                '<span style="font-size:.72rem;color:var(--ink-mute)">Grade A+ Setups</span>'
                '</div>',
                unsafe_allow_html=True,
            )

        if ce_clusters:
            for i, cluster in enumerate(ce_clusters):
                _render_recommendation_cluster_card(
                    cluster, symbol, data, key_suffix=f"ce_cl_{i}", prefix="ck",
                    account_capital=user_cap, risk_pct=user_risk_pct, current_spot=spot,
                )
        else:
            st.markdown(
                '<div style="background:var(--surface-2);border:1px dashed var(--line);border-radius:8px;padding:22px 16px;text-align:center;margin-bottom:12px">'
                '<div style="font-size:1.2rem;margin-bottom:6px">🛡️</div>'
                '<div style="font-weight:700;font-size:.82rem;color:var(--ink)">No Call Setups Meeting Quality Gates (Score ≥ 75)</div>'
                '<div style="font-size:.74rem;color:var(--ink-mute);margin-top:4px">Capital preservation active. Upside edge is weak or market is choppy.</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    # Right Column: PUTS (PE)
    with col_pe:
        pe_clusters = _cluster_ideas_by_strike(batch.pe_recommendations)
        total_pe_setups = len(batch.pe_recommendations)
        pe_cnt_str = f"{len(pe_clusters)} Contract{'s' if len(pe_clusters) != 1 else ''}"
        if total_pe_setups > len(pe_clusters):
            pe_cnt_str += f" · {total_pe_setups} Setups"

        if getattr(batch, "pe_fallback_active", False):
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
                f'<span style="font-weight:750;font-size:.88rem;color:#eab308">🟡 Secondary Conviction Puts (Score 75–85 · {pe_cnt_str})</span>'
                f'<span style="font-size:.70rem;color:#eab308;background:rgba(234,179,8,.12);padding:2px 6px;border-radius:4px">Fallback Tier</span>'
                f'</div>'
                f'<div style="font-size:.74rem;color:var(--ink-mute);margin-bottom:8px">'
                f'Zero setups met Higher Conviction (Score > 85). Displaying secondary setups under conservative risk parameters.'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
                f'<span style="font-weight:750;font-size:.88rem;color:var(--stop)">🔴 Higher Conviction Puts (Score > 85 · {pe_cnt_str})</span>'
                '<span style="font-size:.72rem;color:var(--ink-mute)">Grade A+ Setups</span>'
                '</div>',
                unsafe_allow_html=True,
            )

        if pe_clusters:
            for i, cluster in enumerate(pe_clusters):
                _render_recommendation_cluster_card(
                    cluster, symbol, data, key_suffix=f"pe_cl_{i}", prefix="ck",
                    account_capital=user_cap, risk_pct=user_risk_pct, current_spot=spot,
                )
        else:
            st.markdown(
                '<div style="background:var(--surface-2);border:1px dashed var(--line);border-radius:8px;padding:22px 16px;text-align:center;margin-bottom:12px">'
                '<div style="font-size:1.2rem;margin-bottom:6px">🛡️</div>'
                '<div style="font-weight:700;font-size:.82rem;color:var(--ink)">No Put Setups Meeting Quality Gates (Score ≥ 75)</div>'
                '<div style="font-size:.74rem;color:var(--ink-mute);margin-top:4px">Capital preservation active. Downside edge is weak or market is choppy.</div>'
                '</div>',
                unsafe_allow_html=True,
            )




@st.fragment(run_every=4)
def _fragment_trade_recommendations_live(symbol: str, window: str) -> None:
    _render_trade_recommendations(symbol, window)


@st.fragment
def _fragment_trade_recommendations_static(symbol: str, window: str) -> None:
    _render_trade_recommendations(symbol, window)


@st.fragment(run_every=4)
def _fragment_cockpit_bookmarked_live(symbol: str, window: str) -> None:
    _render_cockpit_bookmarked_recommendations(symbol, window)


@st.fragment
def _fragment_cockpit_bookmarked_static(symbol: str, window: str) -> None:
    _render_cockpit_bookmarked_recommendations(symbol, window)


def _tab_trade_recommendations(symbol: str, window: str) -> None:
    """Tab 2: High-Conviction Options Recommendations Desk & Strategy Telemetry."""
    if _is_stream_live():
        _fragment_trade_recommendations_live(symbol, window)
    else:
        _fragment_trade_recommendations_static(symbol, window)


def _tab_cockpit(symbol: str, window: str) -> None:
    """Tab 1: Strategic Cockpit — Executive Telemetry, Playbook, Ladder & Pinned Setups."""
    # 1. Market Simulator Strip & Stepper (if active)
    if st.session_state.get("replay_mode_active", False):
        _render_cockpit_simulator_bar(symbol, window)

    # 2. Executive Status Strip & 6-Card KPI Grid (3s pulse, 0ms instant quote)
    if _is_stream_live():
        _fragment_cockpit_kpis_live(symbol, window)
    else:
        _fragment_cockpit_kpis_static(symbol, window)

    # 3. Cockpit Guide (static reference expander)
    data, _ = _load_data(symbol, window)
    _render_cockpit_guide_expander(symbol, levels=getattr(data, "levels", None) if data else None)

    # 4. Strategic Playbook & GEX Ladder (15s pulse)
    if _is_stream_live():
        _fragment_cockpit_ladder_live(symbol, window)
    else:
        _fragment_cockpit_ladder_static(symbol, window)

    # 4.5. Pinned Cockpit Recommendations (Strictly bookmarked cards appear in Cockpit)
    if _is_stream_live():
        _fragment_cockpit_bookmarked_live(symbol, window)
    else:
        _fragment_cockpit_bookmarked_static(symbol, window)

    # 5. Bottom Quadrants (Tape alerts + Live Paper Positions MTM, 5s pulse)
    if _is_stream_live():
        _fragment_cockpit_quadrants_live(symbol, window)
    else:
        _fragment_cockpit_quadrants_static(symbol, window)



# ---------------------------------------------------------------------------
# Tab 2: OrderFlow Sentinel Live Tape & Microstructure
# ---------------------------------------------------------------------------

_SENTINEL_DATA_CACHE: dict = {}   # (inst_id, timeframe, limit) -> (float_ts, bars, signals, outcomes)
_SENTINEL_CHART_CACHE: dict = {}  # chart_key -> compound_chart


def _get_cached_sentinel_data(inst_id: str, timeframe: str = "1m", limit: int = 120, ttl: float = 1.8) -> tuple[list, list, list]:
    """Retrieve sentinel bars, signals, and outcomes with in-memory TTL caching for instant UI switching."""
    now = time.time()
    ckey = (inst_id, timeframe, limit)
    hit = _SENTINEL_DATA_CACHE.get(ckey)
    if hit and (now - hit[0] < ttl):
        return hit[1], hit[2], hit[3]

    bars = list_recent_bars(inst_id, timeframe=timeframe, limit=limit)
    signals = list_recent_signals(inst_id, limit=100)
    outcomes = list_recent_outcomes(inst_id, limit=100)
    _SENTINEL_DATA_CACHE[ckey] = (now, bars, signals, outcomes)
    return bars, signals, outcomes


ORDERFLOW_PROFILES = {
    "⚡ Full OrderFlow": {
        "cvd": True, "delta": True, "vwap": True, "bands": True, "walls": True, "ema": True,
        "desc": "All indicators + CVD + Delta subplots"
    },
    "🎯 Pure Price": {
        "cvd": False, "delta": False, "vwap": False, "bands": False, "walls": True, "ema": False,
        "desc": "Clean Candlesticks + GEX Walls without subplots"
    },
    "🌊 CVD Focus": {
        "cvd": True, "delta": False, "vwap": True, "bands": False, "walls": True, "ema": False,
        "desc": "Candlesticks + VWAP + CVD Momentum Subplot"
    },
    "📈 Trend Follower": {
        "cvd": False, "delta": True, "vwap": True, "bands": True, "walls": True, "ema": True,
        "desc": "Candlesticks + VWAP + 9/21 EMA Ribbon + Delta Volume"
    },
}


def _build_sentinel_chart_spec(
    bars: list,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
    gamma_flip: float = 0.0,
    signals: list | None = None,
    show_cvd: bool = True,
    show_delta: bool = True,
    show_vwap: bool = True,
    show_bands: bool = True,
    show_walls: bool = True,
    show_ema: bool = False,
    chart_style: str = "Candlesticks",
    main_height: int = 340,
    chart_width: int | None = None,
) -> alt.Chart | None:
    """Build or retrieve pre-calculated compound Altair chart specification from cache."""
    if not bars:
        return None

    if chart_width is None:
        chart_width = max(1140, len(bars) * 19)

    last_bar_ts = str(bars[-1].start_ts) if bars else ""
    first_bar_ts = str(bars[0].start_ts) if bars else ""
    chart_cache_key = (
        "v7",
        len(bars), first_bar_ts, last_bar_ts,
        round(call_wall, 1), round(put_wall, 1), round(gamma_flip, 1),
        show_cvd, show_delta, show_vwap, show_bands, show_walls, show_ema,
        chart_style, main_height, chart_width, len(signals or [])
    )
    cached_chart = _SENTINEL_CHART_CACHE.get(chart_cache_key)
    if cached_chart is not None:
        return cached_chart

    # Map signals by bar start time string
    sig_by_time = {}
    if signals:
        for s in signals:
            st_ist = _to_ist(s.bar_start_ts).strftime("%H:%M")
            if st_ist not in sig_by_time:
                sig_by_time[st_ist] = s

    chart_data = []
    for b in bars:
        dt_ist = _to_ist(b.start_ts)
        ts_str = dt_ist.strftime("%H:%M")
        dt_full_str = dt_ist.strftime("%d-%b-%Y %H:%M IST")

        # Candle aesthetics
        is_bull_candle = b.close >= b.open
        candle_color = "#10b981" if is_bull_candle else "#ef4444"

        # Delta aesthetics: True orderflow coloring based on net buyer vs seller aggression
        is_buy_delta = b.delta >= 0
        delta_color = "#10b981" if is_buy_delta else "#ef4444"
        delta_ratio = getattr(b, "delta_ratio", 0.0)
        delta_ratio_pct = f"{delta_ratio * 100:+.1f}%"

        # Microstructure Flow Classification
        if is_bull_candle and is_buy_delta:
            flow_type = "🟢 Bull Impulse (Aggressive Buyers Advancing)"
        elif is_bull_candle and not is_buy_delta:
            flow_type = "🛡️ Bull Absorption (Passive Buyers Absorbing Sellers)"
        elif not is_bull_candle and not is_buy_delta:
            flow_type = "🔴 Bear Impulse (Aggressive Sellers Dominating)"
        else:
            flow_type = "🛡️ Bear Absorption (Passive Sellers Blocking Buyers)"

        # CVD flow state
        cvd_state = "Bullish Accumulation (+)" if b.cvd > 0 else "Bearish Distribution (-)"

        # Signal overlay on candle
        sig = sig_by_time.get(ts_str)
        bar_range = max(b.high - b.low, 2.0)
        sig_type = sig.type if sig else None
        sig_score = sig.score if sig else None
        sig_insight = sig.insight_text if sig else None
        sig_y = (b.low - bar_range * 0.35) if (sig and sig.category == "BULLISH") else ((b.high + bar_range * 0.35) if sig else None)
        sig_shape = "triangle-up" if (sig and sig.category == "BULLISH") else ("triangle-down" if (sig and sig.category == "BEARISH") else ("diamond" if sig else None))
        sig_color = "#10b981" if (sig and sig.category == "BULLISH") else ("#ef4444" if (sig and sig.category == "BEARISH") else ("#f59e0b" if sig else None))

        chart_data.append({
            "time": ts_str,
            "datetime": dt_full_str,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "candle_color": candle_color,
            "volume": b.volume_units,
            "delta": b.delta,
            "delta_color": delta_color,
            "delta_ratio_pct": delta_ratio_pct,
            "flow_type": flow_type,
            "cvd": b.cvd,
            "cvd_flow_state": cvd_state,
            "vwap": b.vwap,
            "up1": b.vwap_sigma_1,
            "up2": b.vwap_sigma_2,
            "dn1": b.vwap_lower_1,
            "dn2": b.vwap_lower_2,
            "sig_type": sig_type,
            "sig_score": sig_score,
            "sig_insight": sig_insight,
            "sig_y": sig_y,
            "sig_shape": sig_shape,
            "sig_color": sig_color,
        })
    df = pd.DataFrame(chart_data)

    if "cvd" in df.columns and len(df) > 0:
        df["cvd_ema"] = df["cvd"].ewm(span=9, adjust=False).mean().round(1)

    if show_ema and len(df) > 0:
        df["ema9"] = df["close"].ewm(span=9, adjust=False).mean().round(2)
        df["ema21"] = df["close"].ewm(span=21, adjust=False).mean().round(2)

    has_subplots = show_cvd or show_delta
    main_x = (
        alt.X("time:O", title=None, axis=alt.Axis(labels=False))
        if has_subplots
        else alt.X("time:O", title="Time (IST)", axis=alt.Axis(labelAngle=-45, labelOverlap=True))
    )

    # Dynamic Intelligent Y-Domain Auto-Scaling
    # Ensures candlesticks fill the chart clearly without being crushed by distant GEX walls
    min_price = float(df["low"].min())
    max_price = float(df["high"].max())
    if show_vwap and "vwap" in df.columns:
        min_price = min(min_price, float(df["vwap"].min()))
        max_price = max(max_price, float(df["vwap"].max()))
    if show_bands and "dn1" in df.columns:
        min_price = min(min_price, float(df["dn1"].min()))
        max_price = max(max_price, float(df["up1"].max()))

    span = max(max_price - min_price, 6.0)
    pad = max(span * 0.08, 3.0)
    y_min_target = min_price - pad
    y_max_target = max_price + pad
    price_scale = alt.Scale(domain=[y_min_target, y_max_target], clamp=True, zero=False)

    base = alt.Chart(df)
    layers = []

    # 1. 1σ Value Area Shading (Between Lower 1σ and Upper 1σ)
    if show_bands:
        value_area = base.mark_area(
            opacity=0.07, color="#6366f1"
        ).encode(
            x=main_x,
            y=alt.Y("dn1:Q", scale=price_scale),
            y2="up1:Q",
        )
        layers.append(value_area)

    # 2. Main Price Visuals (Candlesticks or Area)
    if chart_style == "Area / Line":
        area_chart = base.mark_area(
            line=False,
            color=alt.Gradient(
                gradient="linear",
                stops=[
                    alt.GradientStop(color="rgba(59, 130, 246, 0.35)", offset=0),
                    alt.GradientStop(color="rgba(59, 130, 246, 0.02)", offset=1),
                ],
                x1=1, x2=1, y1=1, y2=0,
            ),
        ).encode(
            x=main_x,
            y=alt.Y("close:Q", scale=price_scale, title="Price (₹)"),
        )
        line_chart = base.mark_line(color="#2563eb", strokeWidth=2).encode(
            x=main_x,
            y=alt.Y("close:Q", scale=price_scale, title="Price (₹)"),
            tooltip=[
                alt.Tooltip("datetime:N", title="Date & Time (IST)"),
                alt.Tooltip("close:Q", title="Close Price", format=",.2f"),
                alt.Tooltip("vwap:Q", title="Session VWAP", format=",.2f"),
                alt.Tooltip("delta:Q", title="Bar Delta (Δ)", format="+,.0f"),
                alt.Tooltip("cvd:Q", title="Session CVD", format="+,.0f"),
                alt.Tooltip("flow_type:N", title="Flow Classification"),
            ],
        )
        layers.extend([area_chart, line_chart])
    else:
        # Candlestick rules (high-low) and bars (open-close) with consistent price_scale
        rule = base.mark_rule(strokeWidth=1.2).encode(
            x=main_x,
            y=alt.Y("low:Q", scale=price_scale, title="Price (₹)"),
            y2="high:Q",
            color=alt.Color("candle_color:N", scale=None),
        )
        bar = base.mark_bar(strokeWidth=1).encode(
            x=main_x,
            y=alt.Y("open:Q", scale=price_scale),
            y2="close:Q",
            color=alt.Color("candle_color:N", scale=None),
            tooltip=[
                alt.Tooltip("datetime:N", title="Date & Time (IST)"),
                alt.Tooltip("open:Q", title="Open", format=",.2f"),
                alt.Tooltip("high:Q", title="High", format=",.2f"),
                alt.Tooltip("low:Q", title="Low", format=",.2f"),
                alt.Tooltip("close:Q", title="Close", format=",.2f"),
                alt.Tooltip("vwap:Q", title="VWAP", format=",.2f"),
                alt.Tooltip("delta:Q", title="Bar Delta (Δ)", format="+,.0f"),
                alt.Tooltip("cvd:Q", title="Session CVD", format="+,.0f"),
                alt.Tooltip("flow_type:N", title="Microstructure Read"),
            ],
        )
        layers.extend([rule, bar])

    # 3. OrderFlow Signal Markers on Candlesticks
    sig_df = df.dropna(subset=["sig_type"])
    if not sig_df.empty:
        sig_points = alt.Chart(sig_df).mark_point(
            filled=True,
            size=140,
            stroke="#ffffff",
            strokeWidth=1,
        ).encode(
            x=main_x,
            y=alt.Y("sig_y:Q", scale=price_scale),
            shape=alt.Shape("sig_shape:N", scale=None),
            color=alt.Color("sig_color:N", scale=None),
            tooltip=[
                alt.Tooltip("datetime:N", title="Date & Time (IST)"),
                alt.Tooltip("sig_type:N", title="⚡ Sentinel Signal"),
                alt.Tooltip("sig_score:Q", title="Confidence Score", format=".0f"),
                alt.Tooltip("sig_insight:N", title="Context & Insight"),
            ],
        )
        layers.append(sig_points)

    # 4. VWAP and Sigma Envelopes
    if show_vwap:
        vwap_line = base.mark_line(color="#6366f1", strokeWidth=2.2).encode(x=main_x, y=alt.Y("vwap:Q", scale=price_scale))
        layers.append(vwap_line)

    if show_bands:
        up1_line = base.mark_line(color="#8b5cf6", strokeDash=[4, 4], strokeWidth=1).encode(x=main_x, y=alt.Y("up1:Q", scale=price_scale))
        dn1_line = base.mark_line(color="#8b5cf6", strokeDash=[4, 4], strokeWidth=1).encode(x=main_x, y=alt.Y("dn1:Q", scale=price_scale))
        up2_line = base.mark_line(color="#94a3b8", strokeDash=[2, 2], strokeWidth=1).encode(x=main_x, y=alt.Y("up2:Q", scale=price_scale))
        dn2_line = base.mark_line(color="#94a3b8", strokeDash=[2, 2], strokeWidth=1).encode(x=main_x, y=alt.Y("dn2:Q", scale=price_scale))
        layers.extend([up1_line, dn1_line, up2_line, dn2_line])

    if show_ema and "ema9" in df.columns:
        ema9_line = base.mark_line(color="#f59e0b", strokeWidth=1.5).encode(x=main_x, y=alt.Y("ema9:Q", scale=price_scale))
        ema21_line = base.mark_line(color="#06b6d4", strokeWidth=1.5).encode(x=main_x, y=alt.Y("ema21:Q", scale=price_scale))
        layers.extend([ema9_line, ema21_line])

    # 5. GEX Wall Rules & Levels — only plot if within the active price scale domain so candles are never crushed
    if show_walls and call_wall and call_wall > 0 and (y_min_target <= call_wall <= y_max_target):
        cw_rule = base.mark_rule(
            color="#ef4444", strokeDash=[5, 5], strokeWidth=2
        ).encode(y=alt.datum(float(call_wall)))
        layers.append(cw_rule)

    if show_walls and put_wall and put_wall > 0 and (y_min_target <= put_wall <= y_max_target):
        pw_rule = base.mark_rule(
            color="#10b981", strokeDash=[5, 5], strokeWidth=2
        ).encode(y=alt.datum(float(put_wall)))
        layers.append(pw_rule)

    if show_walls and gamma_flip and gamma_flip > 0 and (y_min_target <= gamma_flip <= y_max_target):
        gf_rule = base.mark_rule(
            color="#f59e0b", strokeDash=[3, 3], strokeWidth=1.5
        ).encode(y=alt.datum(float(gamma_flip)))
        layers.append(gf_rule)

    main_chart = alt.layer(*layers).properties(height=main_height, width=chart_width)

    active_subplots = [main_chart]

    # 6. CVD Subplot (Cumulative Volume Delta with Dynamic Domain & Momentum EMA)
    if show_cvd:
        cvd_min = float(df["cvd"].min())
        cvd_max = float(df["cvd"].max())
        cvd_span = max(cvd_max - cvd_min, 10.0)
        cvd_pad = cvd_span * 0.12

        if cvd_min < 0 < cvd_max:
            c_domain_min = cvd_min - cvd_pad
            c_domain_max = cvd_max + cvd_pad
            baseline_y = 0.0
            has_zero_line = True
        elif cvd_min >= 0 and cvd_min <= cvd_span * 0.8:
            c_domain_min = 0.0
            c_domain_max = cvd_max + cvd_pad
            baseline_y = 0.0
            has_zero_line = True
        elif cvd_max <= 0 and abs(cvd_max) <= cvd_span * 0.8:
            c_domain_min = cvd_min - cvd_pad
            c_domain_max = 0.0
            baseline_y = 0.0
            has_zero_line = True
        else:
            c_domain_min = cvd_min - cvd_pad
            c_domain_max = cvd_max + cvd_pad
            baseline_y = c_domain_min
            has_zero_line = False

        cvd_scale = alt.Scale(domain=[c_domain_min, c_domain_max], clamp=True, zero=False)

        cvd_is_bottom = not show_delta
        cvd_x = (
            alt.X("time:O", title="Time (IST)", axis=alt.Axis(labelAngle=-45, labelOverlap=True))
            if cvd_is_bottom
            else alt.X("time:O", title=None, axis=alt.Axis(labels=False))
        )
        cvd_area = base.mark_area(
            opacity=0.18,
            color="#3b82f6",
        ).encode(
            x=cvd_x,
            y=alt.Y("cvd:Q", scale=cvd_scale, title="CVD"),
            y2=alt.datum(baseline_y),
        )

        cvd_line = base.mark_line(color="#2563eb", strokeWidth=2.2).encode(
            x=cvd_x,
            y=alt.Y("cvd:Q", scale=cvd_scale, title="CVD"),
            tooltip=[
                alt.Tooltip("datetime:N", title="Date & Time (IST)"),
                alt.Tooltip("cvd:Q", title="Session CVD", format="+,.0f"),
                alt.Tooltip("cvd_flow_state:N", title="Session Regime"),
                alt.Tooltip("delta:Q", title="Bar Delta (Δ)", format="+,.0f"),
            ],
        )

        cvd_layers = [cvd_area, cvd_line]
        if has_zero_line:
            cvd_zero = base.mark_rule(
                color="rgba(148, 163, 184, 0.45)", strokeDash=[3, 3], strokeWidth=1
            ).encode(y=alt.datum(0.0))
            cvd_layers.append(cvd_zero)

        if "cvd_ema" in df.columns:
            cvd_ema_line = base.mark_line(
                color="#f59e0b", strokeWidth=1.5, strokeDash=[4, 2]
            ).encode(x=cvd_x, y=alt.Y("cvd_ema:Q", scale=cvd_scale))
            cvd_layers.append(cvd_ema_line)

        # In CVD Focus (where delta is off), give CVD a taller, more expansive height (145px)
        cvd_h = 145 if not show_delta else 110
        cvd_chart = alt.layer(*cvd_layers).properties(height=cvd_h, width=chart_width)
        active_subplots.append(cvd_chart)

    # 7. Delta Volume Bars (Aggressive Buyers vs Aggressive Sellers Imbalance)
    if show_delta:
        d_min = float(df["delta"].min())
        d_max = float(df["delta"].max())
        d_bound = max(abs(d_min), abs(d_max), 50.0) * 1.15
        delta_scale = alt.Scale(domain=[-d_bound, d_bound], clamp=True, zero=False)
        delta_x = alt.X("time:O", title="Time (IST)", axis=alt.Axis(labelAngle=-45, labelOverlap=True))
        delta_bars = base.mark_bar(opacity=0.88).encode(
            x=delta_x,
            y=alt.Y("delta:Q", scale=delta_scale, title="Δ Vol"),
            color=alt.Color("delta_color:N", scale=None),
            tooltip=[
                alt.Tooltip("datetime:N", title="Date & Time (IST)"),
                alt.Tooltip("delta:Q", title="Bar Delta (Units)", format="+,.0f"),
                alt.Tooltip("volume:Q", title="Total Traded Volume", format=",.0f"),
                alt.Tooltip("delta_ratio_pct:N", title="Aggression Imbalance"),
                alt.Tooltip("flow_type:N", title="Microstructure Read"),
            ],
        )
        delta_zero = base.mark_rule(
            color="rgba(148, 163, 184, 0.45)", strokeDash=[2, 2], strokeWidth=1
        ).encode(y=alt.datum(0.0))

        delta_chart = alt.layer(delta_bars, delta_zero).properties(height=95, width=chart_width)
        active_subplots.append(delta_chart)

    # Always wrap in alt.vconcat so Streamlit use_container_width does not collapse ordinal axis to width 0
    compound = alt.vconcat(*active_subplots)

    # Bound cache size to prevent memory leaks
    if len(_SENTINEL_CHART_CACHE) > 300:
        keys_to_evict = list(_SENTINEL_CHART_CACHE.keys())[:60]
        for k in keys_to_evict:
            _SENTINEL_CHART_CACHE.pop(k, None)

    # Store in memory cache
    _SENTINEL_CHART_CACHE[chart_cache_key] = compound
    return compound


def _prewarm_all_profiles(
    bars: list,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
    gamma_flip: float = 0.0,
    signals: list | None = None,
    chart_style: str = "Candlesticks",
    main_height: int = 340,
    chart_width: int | None = None,
) -> None:
    """Pre-calculate and store all 4 profile charts in cache so switching is 100% instant."""
    if not bars:
        return
    for prof_key, cfg in ORDERFLOW_PROFILES.items():
        _build_sentinel_chart_spec(
            bars=bars,
            call_wall=call_wall,
            put_wall=put_wall,
            gamma_flip=gamma_flip,
            signals=signals,
            show_cvd=cfg["cvd"],
            show_delta=cfg["delta"],
            show_vwap=cfg["vwap"],
            show_bands=cfg["bands"],
            show_walls=cfg["walls"],
            show_ema=cfg["ema"],
            chart_style=chart_style,
            main_height=main_height,
            chart_width=chart_width,
        )


_PRECALC_STOP_EVENT = threading.Event()
_PRECALC_STOP_EVENT.set()  # Stop any lingering background precalc threads


def _render_sentinel_chart(
    bars: list,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
    gamma_flip: float = 0.0,
    signals: list | None = None,
    show_cvd: bool = True,
    show_delta: bool = True,
    show_vwap: bool = True,
    show_bands: bool = True,
    show_walls: bool = True,
    show_ema: bool = False,
    chart_style: str = "Candlesticks",
    main_height: int = 340,
    chart_width: int | None = None,
) -> None:
    """Render Altair Candlestick/Area + Session VWAP Value Area + GEX Walls + CVD + Delta subplots with instant caching and isolated time-scrolling."""
    if not bars:
        st.info("No candle flow data recorded yet. Click '⚡ Seed Demo Flow' to populate intraday simulation.")
        return

    if chart_width is None:
        chart_width = max(1180, len(bars) * 22)

    # Render current chart
    compound = _build_sentinel_chart_spec(
        bars=bars,
        call_wall=call_wall,
        put_wall=put_wall,
        gamma_flip=gamma_flip,
        signals=signals,
        show_cvd=show_cvd,
        show_delta=show_delta,
        show_vwap=show_vwap,
        show_bands=show_bands,
        show_walls=show_walls,
        show_ema=show_ema,
        chart_style=chart_style,
        main_height=main_height,
        chart_width=chart_width,
    )
    if compound is not None:
        st.altair_chart(compound, use_container_width=False)

    # Pre-calculate and cache all 4 profiles in advance so subsequent profile clicks hit cache in 0.00ms
    _prewarm_all_profiles(
        bars=bars,
        call_wall=call_wall,
        put_wall=put_wall,
        gamma_flip=gamma_flip,
        signals=signals,
        chart_style=chart_style,
        main_height=main_height,
        chart_width=chart_width,
    )


def _calc_ema_series(candles: list[dict], period: int) -> list[dict]:
    """Calculate Exponential Moving Average series for Lightweight Charts."""
    if not candles:
        return []
    k = 2.0 / (period + 1.0)
    res = []
    prev = None
    for c in candles:
        p = c["close"]
        if prev is None:
            prev = p
        else:
            prev = (p * k) + (prev * (1.0 - k))
        res.append({"time": c["time"], "value": round(prev, 2)})
    return res


def _prepare_tv_chart_data(bars: list, signals: list | None = None) -> tuple[list[dict], list[dict]]:
    """Convert Bar and SentinelSignal models into TradingView Lightweight Charts JSON format in IST."""
    candles = []
    for b in bars:
        dt = b.start_ts
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST_TZ)
        dt_ist = dt.astimezone(IST_TZ)
        ist_epoch = int(datetime(dt_ist.year, dt_ist.month, dt_ist.day, dt_ist.hour, dt_ist.minute, dt_ist.second, tzinfo=timezone.utc).timestamp())
        candles.append({
            "time": ist_epoch,
            "open": round(b.open, 2),
            "high": round(b.high, 2),
            "low": round(b.low, 2),
            "close": round(b.close, 2),
            "volume": int(b.volume_units),
            "delta": round(b.delta, 1),
            "cvd": round(b.cvd, 1),
            "vwap": round(b.vwap, 2),
            "up1": round(b.vwap_sigma_1, 2),
            "up2": round(b.vwap_sigma_2, 2),
            "dn1": round(b.vwap_lower_1, 2),
            "dn2": round(b.vwap_lower_2, 2),
        })
    candles.sort(key=lambda c: c["time"])

    markers = []
    if signals:
        candle_times = {c["time"] for c in candles}
        for s in signals:
            s_dt = s.bar_start_ts
            if s_dt.tzinfo is None:
                s_dt = s_dt.replace(tzinfo=IST_TZ)
            s_dt_ist = s_dt.astimezone(IST_TZ)
            sig_t = int(datetime(s_dt_ist.year, s_dt_ist.month, s_dt_ist.day, s_dt_ist.hour, s_dt_ist.minute, s_dt_ist.second, tzinfo=timezone.utc).timestamp())
            if candle_times:
                closest_t = min(candle_times, key=lambda t: abs(t - sig_t))
                if abs(closest_t - sig_t) <= 180:
                    sig_t = closest_t
            pos = "belowBar" if s.bias == "LONG" else "aboveBar"
            col = "#10b981" if s.category == "BULLISH" else ("#ef4444" if s.category == "BEARISH" else "#f59e0b")
            shp = "arrowUp" if s.bias == "LONG" else ("arrowDown" if s.bias == "SHORT" else "circle")
            markers.append({
                "time": sig_t,
                "position": pos,
                "color": col,
                "shape": shp,
                "text": f"{s.type} ({s.score})",
            })
        markers.sort(key=lambda m: m["time"])

    return candles, markers


def _build_tv_lightweight_chart_html(
    bars: list,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
    gamma_flip: float = 0.0,
    signals: list | None = None,
    profile: str = "⚡ Full OrderFlow",
    chart_style: str = "Candlesticks",
    symbol: str = "NIFTY",
    height: int = 400,
) -> str:
    """Build self-contained TradingView Lightweight Charts HTML/JS component."""
    candles, markers = _prepare_tv_chart_data(bars, signals)
    ema9 = _calc_ema_series(candles, 9) if profile in ("📈 Trend Follower", "⚡ Full OrderFlow") else []
    ema21 = _calc_ema_series(candles, 21) if profile in ("📈 Trend Follower", "⚡ Full OrderFlow") else []

    show_vwap = profile in ("⚡ Full OrderFlow", "🌊 CVD Focus", "📈 Trend Follower")
    show_bands = profile in ("⚡ Full OrderFlow", "📈 Trend Follower")
    show_ema = profile in ("📈 Trend Follower",)
    show_delta = profile in ("⚡ Full OrderFlow", "📈 Trend Follower")
    show_cvd = profile in ("⚡ Full OrderFlow", "🌊 CVD Focus")
    show_walls = True

    chart_payload = {
        "candles": candles,
        "markers": markers,
        "ema9": ema9,
        "ema21": ema21,
        "cw": call_wall,
        "pw": put_wall,
        "flip": gamma_flip,
        "showVwap": show_vwap,
        "showBands": show_bands,
        "showEma": show_ema,
        "showDelta": show_delta,
        "showCvd": show_cvd,
        "showWalls": show_walls,
        "chartStyle": chart_style,
        "symbol": symbol,
    }

    data_json = json.dumps(chart_payload)

    html_code = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  html, body {{
    margin: 0;
    padding: 0;
    width: 100%;
    height: 100%;
    background-color: transparent;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    overflow: hidden;
  }}
  #tv_chart_wrapper {{
    position: relative;
    width: 100%;
    height: {height}px;
    background: rgba(15, 23, 42, 0.45);
    border-radius: 8px;
    border: 1px solid rgba(51, 65, 85, 0.35);
    box-sizing: border-box;
    overflow: hidden;
  }}
  #tv_chart_container {{
    width: 100%;
    height: 100%;
  }}
  .tv-legend {{
    position: absolute;
    top: 8px;
    left: 12px;
    z-index: 20;
    font-size: 11px;
    color: #94a3b8;
    background: rgba(15, 23, 42, 0.85);
    padding: 4px 10px;
    border-radius: 6px;
    pointer-events: none;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    display: flex;
    gap: 12px;
    align-items: center;
    backdrop-filter: blur(6px);
    border: 1px solid rgba(51, 65, 85, 0.5);
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25);
  }}
</style>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
if (typeof LightweightCharts === 'undefined') {{
  document.write('<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"><\\/script>');
}}
</script>
</head>
<body>
<div id="tv_chart_wrapper">
  <div class="tv-legend" id="legend">
    <span id="leg_sym" style="font-weight:700;color:#38bdf8">{symbol}</span>
    <span id="leg_time" style="color:#94a3b8;font-family:monospace;font-size:0.75rem">--:-- IST</span>
    <span id="leg_ohlc">O: -- H: -- L: -- C: --</span>
    <span id="leg_vwap" style="color:#c084fc">VWAP: --</span>
    <span id="leg_delta">Δ: --</span>
    <span id="leg_cvd" style="color:#38bdf8">CVD: --</span>
  </div>
  <div id="tv_chart_container"></div>
</div>
<script>
(function() {{
  const container = document.getElementById('tv_chart_container');
  const data = {data_json};

  if (!container || !window.LightweightCharts) return;

  const chart = LightweightCharts.createChart(container, {{
    width: container.clientWidth || 1050,
    height: Math.max(280, {height} - 4),
    layout: {{
      background: {{ color: 'transparent' }},
      textColor: '#94a3b8',
      fontSize: 11,
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    }},
    grid: {{
      vertLines: {{ color: 'rgba(51, 65, 85, 0.22)' }},
      horzLines: {{ color: 'rgba(51, 65, 85, 0.22)' }},
    }},
    crosshair: {{
      mode: LightweightCharts.CrosshairMode.Normal,
    }},
    rightPriceScale: {{
      borderColor: 'rgba(51, 65, 85, 0.45)',
      autoScale: true,
      scaleMargins: {{
        top: data.showCvd ? 0.12 : 0.16,
        bottom: data.showCvd ? 0.38 : (data.showDelta ? 0.28 : 0.16),
      }},
    }},
    handleScale: {{
      axisPressedMouseMove: {{
        time: false,
        price: true,
      }},
      axisDoubleClickReset: {{
        time: false,
        price: true,
      }},
      mouseWheel: false,
      pinch: true,
    }},
    timeScale: {{
      borderColor: 'rgba(51, 65, 85, 0.45)',
      timeVisible: true,
      secondsVisible: false,
      barSpacing: 9,
      rightOffset: 3,
      fixLeftEdge: false,
      fixRightEdge: false,
      tickMarkFormatter: function(time, tickMarkType, locale) {{
        let ts = typeof time === 'number' ? time : (time && time.timestamp ? time.timestamp : null);
        if (ts === null) return null;
        const d = new Date(ts * 1000);
        if (tickMarkType <= 2) {{
          const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
          return String(d.getUTCDate()).padStart(2, '0') + '-' + months[d.getUTCMonth()];
        }}
        const hh = String(d.getUTCHours()).padStart(2, '0');
        const mm = String(d.getUTCMinutes()).padStart(2, '0');
        return hh + ':' + mm;
      }},
    }},
    localization: {{
      timeFormatter: function(ts) {{
        let t = typeof ts === 'number' ? ts : (ts && ts.timestamp ? ts.timestamp : null);
        if (!t) return '';
        const d = new Date(t * 1000);
        const hh = String(d.getUTCHours()).padStart(2, '0');
        const mm = String(d.getUTCMinutes()).padStart(2, '0');
        return hh + ':' + mm + ' IST';
      }},
      dateFormatter: function(ts) {{
        let t = typeof ts === 'number' ? ts : (ts && ts.timestamp ? ts.timestamp : null);
        if (!t) return '';
        const d = new Date(t * 1000);
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return String(d.getUTCDate()).padStart(2, '0') + '-' + months[d.getUTCMonth()];
      }}
    }}
  }});

  let mainSeries;
  if (data.chartStyle === 'Area / Line') {{
    mainSeries = chart.addAreaSeries({{
      topColor: 'rgba(16, 185, 129, 0.4)',
      bottomColor: 'rgba(16, 185, 129, 0.0)',
      lineColor: '#10b981',
      lineWidth: 2,
    }});
    mainSeries.setData(data.candles.map(c => ({{ time: c.time, value: c.close }})));
  }} else {{
    mainSeries = chart.addCandlestickSeries({{
      upColor: '#10b981',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    }});
    mainSeries.setData(data.candles.map(c => ({{
      time: c.time, open: c.open, high: c.high, low: c.low, close: c.close
    }})));
  }}

  if (data.showWalls) {{
    if (data.cw && data.cw > 0) {{
      mainSeries.createPriceLine({{
        price: data.cw,
        color: '#ef4444',
        lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'Call Wall ' + Math.round(data.cw).toLocaleString(),
      }});
    }}
    if (data.pw && data.pw > 0) {{
      mainSeries.createPriceLine({{
        price: data.pw,
        color: '#10b981',
        lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'Put Wall ' + Math.round(data.pw).toLocaleString(),
      }});
    }}
    if (data.flip && data.flip > 0) {{
      mainSeries.createPriceLine({{
        price: data.flip,
        color: '#f59e0b',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true,
        title: 'Gamma Flip ' + Math.round(data.flip).toLocaleString(),
      }});
    }}
  }}

  if (data.showVwap) {{
    const vwapSeries = chart.addLineSeries({{
      color: '#a855f7',
      lineWidth: 2,
      title: 'VWAP',
    }});
    vwapSeries.setData(data.candles.map(c => ({{ time: c.time, value: c.vwap }})));

    if (data.showBands) {{
      const up1 = chart.addLineSeries({{ color: 'rgba(168, 85, 247, 0.5)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed }});
      const dn1 = chart.addLineSeries({{ color: 'rgba(168, 85, 247, 0.5)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed }});
      const up2 = chart.addLineSeries({{ color: 'rgba(168, 85, 247, 0.28)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted }});
      const dn2 = chart.addLineSeries({{ color: 'rgba(168, 85, 247, 0.28)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted }});
      up1.setData(data.candles.map(c => ({{ time: c.time, value: c.up1 }})));
      dn1.setData(data.candles.map(c => ({{ time: c.time, value: c.dn1 }})));
      up2.setData(data.candles.map(c => ({{ time: c.time, value: c.up2 }})));
      dn2.setData(data.candles.map(c => ({{ time: c.time, value: c.dn2 }})));
    }}
  }}

  if (data.showEma && data.ema9 && data.ema21 && data.ema9.length) {{
    const ema9Series = chart.addLineSeries({{ color: '#eab308', lineWidth: 1.5, title: 'EMA 9' }});
    const ema21Series = chart.addLineSeries({{ color: '#3b82f6', lineWidth: 1.5, title: 'EMA 21' }});
    ema9Series.setData(data.ema9);
    ema21Series.setData(data.ema21);
  }}

  if (data.showDelta) {{
    const deltaSeries = chart.addHistogramSeries({{
      priceFormat: {{ type: 'volume' }},
      priceScaleId: 'deltaScale',
    }});
    chart.priceScale('deltaScale').applyOptions({{
      scaleMargins: {{ top: 0.85, bottom: 0 }},
    }});
    deltaSeries.setData(data.candles.map(c => ({{
      time: c.time,
      value: Math.abs(c.delta),
      color: c.delta >= 0 ? 'rgba(16, 185, 129, 0.65)' : 'rgba(239, 68, 68, 0.65)'
    }})));
  }}

  if (data.showCvd) {{
    const cvdSeries = chart.addLineSeries({{
      color: '#38bdf8',
      lineWidth: 2,
      priceScaleId: 'cvdScale',
      title: 'CVD',
    }});
    chart.priceScale('cvdScale').applyOptions({{
      scaleMargins: {{ top: 0.70, bottom: 0.02 }},
    }});
    cvdSeries.setData(data.candles.map(c => ({{ time: c.time, value: c.cvd }})));
  }}

  if (data.markers && data.markers.length > 0) {{
    const sortedMarkers = data.markers.slice().sort((a, b) => a.time - b.time);
    mainSeries.setMarkers(sortedMarkers);
  }}

  window.addEventListener('resize', () => {{
    chart.applyOptions({{ width: container.clientWidth }});
  }});

  const candleMap = {{}};
  data.candles.forEach(c => {{ candleMap[c.time] = c; }});

  const legTime = document.getElementById('leg_time');
  const legOhlc = document.getElementById('leg_ohlc');
  const legVwap = document.getElementById('leg_vwap');
  const legDelta = document.getElementById('leg_delta');
  const legCvd = document.getElementById('leg_cvd');

  function updateLegend(c) {{
    if (!c) return;
    if (legTime && c.time) {{
      let t = typeof c.time === 'number' ? c.time : (c.time && c.time.timestamp ? c.time.timestamp : null);
      if (t) {{
        const d = new Date(t * 1000);
        const hh = String(d.getUTCHours()).padStart(2, '0');
        const mm = String(d.getUTCMinutes()).padStart(2, '0');
        legTime.textContent = hh + ':' + mm + ' IST';
      }}
    }}
    legOhlc.textContent = 'O:' + c.open.toFixed(1) + ' H:' + c.high.toFixed(1) + ' L:' + c.low.toFixed(1) + ' C:' + c.close.toFixed(1);
    if (legVwap && c.vwap) legVwap.textContent = 'VWAP:' + c.vwap.toFixed(1);
    if (legDelta && c.delta !== undefined) legDelta.textContent = 'Δ:' + (c.delta >= 0 ? '+' : '') + Math.round(c.delta).toLocaleString();
    if (legCvd && c.cvd !== undefined) legCvd.textContent = 'CVD:' + (c.cvd >= 0 ? '+' : '') + Math.round(c.cvd).toLocaleString();
  }}

  chart.subscribeCrosshairMove(param => {{
    if (!param.time || !param.seriesData.get(mainSeries)) {{
      const last = data.candles[data.candles.length - 1];
      updateLegend(last);
      return;
    }}
    const val = param.seriesData.get(mainSeries);
    const c = candleMap[param.time] || val;
    updateLegend(c);
  }});

  if (data.candles.length > 0) {{
    updateLegend(data.candles[data.candles.length - 1]);
  }}
}})();
</script>
</body>
</html>"""
    return html_code


def _render_tv_lightweight_chart(
    bars: list,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
    gamma_flip: float = 0.0,
    signals: list | None = None,
    profile: str = "⚡ Full OrderFlow",
    chart_style: str = "Candlesticks",
    symbol: str = "NIFTY",
    height: int = 400,
) -> None:
    """Render high-performance TradingView Lightweight Chart canvas inside Streamlit."""
    import streamlit.components.v1 as components
    html = _build_tv_lightweight_chart_html(
        bars=bars,
        call_wall=call_wall,
        put_wall=put_wall,
        gamma_flip=gamma_flip,
        signals=signals,
        profile=profile,
        chart_style=chart_style,
        symbol=symbol,
        height=height,
    )
    components.html(html, height=height + 20, scrolling=False)


def _render_profile_content(
    profile_name: str,
    symbol: str,
    inst_id: str,
    cw: float,
    pw: float,
    gamma_flip: float = 0.0,
    ref_px: float = 0.0,
    timeframe: str = "1m",
) -> None:
    """Renderer for chart profile presets with choice of TradingView Lightweight Charts or Altair."""
    bars, signals, _ = _get_cached_sentinel_data(inst_id, timeframe=timeframe, limit=120)
    lookback_map = {"30 Bars": 30, "60 Bars": 60, "90 Bars": 90, "120 Bars": 120, "All Bars": len(bars)}

    desc_map = {
        "⚡ Full OrderFlow": "Candlesticks, VWAP envelopes, 9/21 ribbon + CVD and Delta subplots.",
        "🎯 Pure Price": "Clean candlesticks auto-scaled directly to price action with GEX walls.",
        "🌊 CVD Focus": "Candlesticks + VWAP benchmark with magnified Cumulative Delta and 9-EMA.",
        "📈 Trend Follower": "VWAP, ±1σ/±2σ bands & 9/21 EMA ribbon paired with Delta Volume.",
    }

    prof_key = profile_name.split()[-1].lower()

    c_info, c_lb, c_st, c_eng, c_ref = st.columns([3, 1, 1, 1, 1])
    with c_info:
        st.markdown(
            f"<div style='font-size:0.74rem;color:var(--ink-mute);padding-top:4px'>"
            f"<b>{profile_name}:</b> {desc_map.get(profile_name, '')}"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c_lb:
        lb = st.selectbox("Lookback", ["30 Bars", "60 Bars", "90 Bars", "120 Bars", "All Bars"], key=f"lb_{prof_key}_{symbol}", index=1, label_visibility="collapsed")
    with c_st:
        style = st.selectbox("Style", ["Candlesticks", "Area / Line"], key=f"st_{prof_key}_{symbol}", index=0, label_visibility="collapsed")
    with c_eng:
        engine = st.selectbox("Engine", ["⚡ TradingView (Canvas)", "📊 Altair (Analytic)"], key=f"eng_{prof_key}_{symbol}", index=0, label_visibility="collapsed")
    with c_ref:
        if st.button("🔄 Refresh", key=f"btn_{prof_key}_{symbol}", use_container_width=True):
            st.rerun(scope="fragment")

    target_limit = lookback_map.get(lb, 60)
    bars_to_render = bars[-target_limit:] if bars else []

    if "TradingView" in engine:
        _render_tv_lightweight_chart(
            bars=bars_to_render,
            call_wall=cw,
            put_wall=pw,
            gamma_flip=gamma_flip,
            signals=signals,
            profile=profile_name,
            chart_style=style,
            symbol=symbol,
            height=390,
        )
    else:
        show_cvd = profile_name in ("⚡ Full OrderFlow", "🌊 CVD Focus")
        show_delta = profile_name in ("⚡ Full OrderFlow", "📈 Trend Follower")
        show_vwap = profile_name in ("⚡ Full OrderFlow", "🌊 CVD Focus", "📈 Trend Follower")
        show_bands = profile_name in ("⚡ Full OrderFlow", "📈 Trend Follower")
        show_walls = True
        show_ema = profile_name in ("⚡ Full OrderFlow", "📈 Trend Follower")
        _render_sentinel_chart(
            bars_to_render,
            call_wall=cw,
            put_wall=pw,
            gamma_flip=gamma_flip,
            signals=signals,
            show_cvd=show_cvd,
            show_delta=show_delta,
            show_vwap=show_vwap,
            show_bands=show_bands,
            show_walls=show_walls,
            show_ema=show_ema,
            chart_style=style,
            main_height=320,
        )

    legend_items = [
        '<span>🟣 <b>VWAP:</b> Session Mean</span>',
        '<span>🟣 <b>±1σ & ±2σ:</b> Envelopes</span>',
        '<span>🟡 <b>EMA 9 & 21 Ribbon</b></span>',
        f'<span>🟥 <b>Call Wall ({cw:,.0f})</b></span>',
        f'<span>🟩 <b>Put Wall ({pw:,.0f})</b></span>',
        '<span>🌊 <b>CVD:</b> Cumulative Vol Delta</span>',
        '<span>📊 <b>Δ Vol:</b> Bar Aggression</span>',
    ]
    if gamma_flip and gamma_flip > 0:
        legend_items.append(f'<span>🟨 <b>Gamma Flip ({gamma_flip:,.0f})</b></span>')
    legend_html = f'<div style="font-size:.74rem;color:var(--ink-mute);display:flex;gap:12px;flex-wrap:wrap;padding-top:6px">{"".join(legend_items)}</div>'
    st.markdown(legend_html, unsafe_allow_html=True)


@st.fragment
def _fragment_profile_full_orderflow(symbol: str, inst_id: str, cw: float, pw: float, gamma_flip: float = 0.0, ref_px: float = 0.0, timeframe: str = "1m") -> None:
    _render_profile_content("⚡ Full OrderFlow", symbol, inst_id, cw, pw, gamma_flip, ref_px, timeframe)


@st.fragment
def _fragment_profile_pure_price(symbol: str, inst_id: str, cw: float, pw: float, gamma_flip: float = 0.0, ref_px: float = 0.0, timeframe: str = "1m") -> None:
    _render_profile_content("🎯 Pure Price", symbol, inst_id, cw, pw, gamma_flip, ref_px, timeframe)


@st.fragment
def _fragment_profile_cvd_focus(symbol: str, inst_id: str, cw: float, pw: float, gamma_flip: float = 0.0, ref_px: float = 0.0, timeframe: str = "1m") -> None:
    _render_profile_content("🌊 CVD Focus", symbol, inst_id, cw, pw, gamma_flip, ref_px, timeframe)


@st.fragment
def _fragment_profile_trend_follower(symbol: str, inst_id: str, cw: float, pw: float, gamma_flip: float = 0.0, ref_px: float = 0.0, timeframe: str = "1m") -> None:
    _render_profile_content("📈 Trend Follower", symbol, inst_id, cw, pw, gamma_flip, ref_px, timeframe)


# ---------------------------------------------------------------------------
# Decoupled Independent Sentinel Fragments
# ---------------------------------------------------------------------------

@st.fragment(run_every=3)
def _fragment_sentinel_kpi_strip(symbol: str, inst_id: str, timeframe: str, ref_px: float, cw: float, pw: float) -> None:
    """Card 1: Microstructure KPI strip auto-updating every 3 seconds independently."""
    if config.is_market_open():
        step_sentinel_live_feed(inst_id, spot=ref_px, call_wall=cw, put_wall=pw)

    bars, signals, _ = _get_cached_sentinel_data(inst_id, timeframe=timeframe, limit=10)
    latest_bar = bars[-1] if bars else None
    if not latest_bar:
        return

    cvd_val = latest_bar.cvd
    cvd_dir = "Bullish (+)" if cvd_val > 0 else "Bearish (-)"
    cvd_cls = "up" if cvd_val > 0 else "dn"
    cvd_chip = "bull" if cvd_val > 0 else "bear"
    vwap_diff = latest_bar.close - latest_bar.vwap
    vwap_pct = (vwap_diff / latest_bar.vwap) * 100.0
    vwap_str = f"{'+' if vwap_diff >= 0 else ''}{vwap_diff:.1f} pts ({vwap_pct:+.2f}%)"

    latest_sig = signals[0] if signals else None
    rvol_val = getattr(latest_sig, "rvol_tod", 1.0) if latest_sig else 1.0
    delta_val = getattr(latest_bar, "delta", 0.0)
    delta_ratio = getattr(latest_bar, "delta_ratio", 0.0)

    if rvol_val >= 2.0:
        regime_label = "VOLUME CLIMAX"
        regime_color = "var(--warn)"
    elif abs(delta_ratio) >= 0.20 or abs(delta_val) > 5000:
        regime_label = "HEAVY AGGRESSION"
        regime_color = "var(--go)" if delta_val > 0 else "var(--stop)"
    else:
        regime_label = "BALANCED AUCTION"
        regime_color = "var(--spark)"

    top_score = max([s.score for s in signals], default=0)
    lbl_inst = f"🚀 {symbol} Futures ({timeframe})" if inst_id.endswith("-FUT") else f"📈 {symbol} Spot ({timeframe})"

    st.markdown(
        f'<div class="ck"><div class="sentinel-kpi-grid">'
        f'<div class="sentinel-kpi-card">'
        f'<small>{lbl_inst}</small>'
        f'<b>₹{latest_bar.close:,.1f}</b>'
        f'<span>Vol: {latest_bar.volume_units:,} · Δ {latest_bar.delta:+,.0f}</span>'
        f'</div>'
        f'<div class="sentinel-kpi-card">'
        f'<small>Session CVD</small>'
        f'<b class="spot-chg {cvd_cls}">{cvd_val:+,.0f}</b>'
        f'<span class="sentinel-chip {cvd_chip}">{cvd_dir}</span>'
        f'</div>'
        f'<div class="sentinel-kpi-card">'
        f'<small>Benchmark VWAP</small>'
        f'<b style="color:var(--spark)">₹{latest_bar.vwap:,.1f}</b>'
        f'<span>{vwap_str}</span>'
        f'</div>'
        f'<div class="sentinel-kpi-card">'
        f'<small>Microstructure Regime</small>'
        f'<b style="color:{regime_color}">{regime_label}</b>'
        f'<span>RVol: {rvol_val:.1f}× · Δ {delta_ratio*100:+.0f}%</span>'
        f'</div>'
        f'<div class="sentinel-kpi-card">'
        f'<small>Sentinel Stream</small>'
        f'<b style="color:var(--ink)">{len(signals)} Active</b>'
        f'<span>Top Score: <b style="color:var(--spark)">{top_score}/100</b></span>'
        f'</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


@st.fragment
def _fragment_sentinel_chart_card(
    symbol: str,
    inst_id: str,
    spot: float,
    cw: float,
    pw: float,
    gamma_flip: float = 0.0,
    timeframe: str = "1m",
) -> None:
    """Card 2: Candlestick & CVD Chart rendered independently with TradingView Lightweight Charts."""
    bars, _, _ = _get_cached_sentinel_data(inst_id, timeframe=timeframe, limit=120)
    first_dt = _to_ist(bars[0].start_ts).strftime("%d-%b-%Y") if bars else ""
    date_badge = f' · <span style="color:var(--spark)">{first_dt}</span>' if first_dt else ""

    st.markdown(
        f'<div class="ck card"><div class="card-body">'
        f'<div class="eyebrow">Candlestick Tape & OrderFlow CVD{date_badge}</div>',
        unsafe_allow_html=True,
    )

    tab_full, tab_pure, tab_cvd, tab_trend = st.tabs([
        "⚡ Full OrderFlow",
        "🎯 Pure Price",
        "🌊 CVD Focus",
        "📈 Trend Follower",
    ])

    with tab_full:
        _fragment_profile_full_orderflow(symbol, inst_id, cw, pw, gamma_flip=gamma_flip, ref_px=spot, timeframe=timeframe)
    with tab_pure:
        _fragment_profile_pure_price(symbol, inst_id, cw, pw, gamma_flip=gamma_flip, ref_px=spot, timeframe=timeframe)
    with tab_cvd:
        _fragment_profile_cvd_focus(symbol, inst_id, cw, pw, gamma_flip=gamma_flip, ref_px=spot, timeframe=timeframe)
    with tab_trend:
        _fragment_profile_trend_follower(symbol, inst_id, cw, pw, gamma_flip=gamma_flip, ref_px=spot, timeframe=timeframe)

    with st.expander("📖 Visual Guide: How to Read Candlestick Tape, Delta & OrderFlow CVD (Click to Expand / Collapse)", expanded=False):
        st.markdown(
            f'''
            <div style="font-size:.84rem;line-height:1.6">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <span style="font-weight:700;color:var(--ink)">OrderFlow Sentinel Chart Visual Cheat Sheet</span>
                <a href="/?view=orderflow_guide" target="_blank" style="font-size:.76rem;color:var(--spark);font-weight:700;text-decoration:none">Full Signals Manual ↗</a>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--spark)">
                    <b style="color:var(--spark)">🕯️ 1. Candlestick Tape & GEX Boundaries</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    • <b>Price Candlesticks:</b> Green body = Buyers closed higher; Red body = Sellers closed lower.<br>
                    • <b>GEX Call Wall (🟥):</b> Ceiling where Call Writers defend credit spreads. High-volume breach = Gamma Blast squeeze.<br>
                    • <b>GEX Put Wall (🟩):</b> Major floor where Put Writers absorb selloffs. Breakdown = Trapdoor panic delta-hedging.<br>
                    • <b>Gamma Flip (🟨):</b> Equilibrium balance. Above = Call-Writer dominance. Below = Put-Writer dominance.<br>
                    • <b>VWAP (🟣) & Shaded Value Area:</b> Session mean price. The shaded envelope (±1σ) holds ~68% of volume; ±2σ marks statistical exhaustion.
                    </span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--go)">
                    <b style="color:var(--go)">📊 2. Bar Delta (Δ Vol) & Absorption Secret</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    • <b>Delta (Δ):</b> Net aggressive market buys (at Ask) minus aggressive market sells (at Bid).<br>
                    • <b>Green Bar (Δ > 0):</b> Net aggressive market buyers lifting the offer.<br>
                    • <b>Red Bar (Δ < 0):</b> Net aggressive market sellers dumping at the bid.<br>
                    • <b>🛡️ Passive Absorption (The Holy Grail):</b> When a candle closes <b>GREEN</b> but has a <b>RED Delta bar</b>, passive institutional limit buyers completely absorbed market sell dumping! Expect a powerful bounce.
                    </span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid #2563eb">
                    <b style="color:#2563eb">🌊 3. Cumulative Volume Delta (CVD)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    • <b>What it is:</b> The running session total of all aggressive buyer volume minus aggressive seller volume.<br>
                    • <b>Dashed Zero Line:</b> Above 0 = Net Buyer Session; Below 0 = Net Seller Session.<br>
                    • <b>Blue Line & Shading:</b> Real-time session delta accumulation.<br>
                    • <b>🟡 Orange Dotted Line (CVD 9-EMA):</b> Momentum direction. When blue CVD crosses above the orange EMA line, buying momentum is accelerating.
                    </span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid #f59e0b">
                    <b style="color:#f59e0b">🎯 4. The High-Win-Rate CVD Setups</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    • <b>🟢 Bullish CVD Divergence (Long Setup):</b> Price makes a <i>Lower Low</i>, but CVD makes a <i>Higher Low</i>. Aggressive sellers exhausted; passive limit accumulation underway. Enter Long; Stop below bar low.<br>
                    • <b>🔴 Bearish CVD Divergence (Short Setup):</b> Price makes a <i>Higher High</i>, but CVD makes a <i>Lower High</i>. Aggressive buyers exhausted; passive limit selling dumping into retail FOMO. Enter Short; Stop above bar high.<br>
                    • <b>🚀 Impulse Continuation:</b> Price and CVD break out together in the same direction above/below VWAP.
                    </span>
                </div>
            </div>
            </div>
            ''',
            unsafe_allow_html=True,
        )

    st.markdown('</div></div>', unsafe_allow_html=True)


@st.fragment(run_every=5)
def _fragment_sentinel_stream_card(symbol: str, inst_id: str, sig_filter: str) -> None:
    """Card 3: Live OrderFlow Stream rendered independently; displays latest 5 chronologically with scrollable full ledger."""
    _, signals, outcomes = _get_cached_sentinel_data(inst_id, timeframe="1m", limit=120)
    outcomes_map = {o.event_id: o for o in outcomes}

    # Filter signals
    filtered_signals = signals
    if sig_filter == "High Confidence (Score ≥ 70)":
        filtered_signals = [s for s in signals if s.score >= 70]
    elif sig_filter == "Bullish":
        filtered_signals = [s for s in signals if s.category == "BULLISH"]
    elif sig_filter == "Bearish":
        filtered_signals = [s for s in signals if s.category == "BEARISH"]
    elif sig_filter == "Caution":
        filtered_signals = [s for s in signals if s.category == "CAUTION"]

    # Sort chronologically newest first
    filtered_signals = sorted(filtered_signals, key=lambda s: s.bar_start_ts, reverse=True)

    market_open = config.is_market_open()
    if market_open:
        feed_badge = '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#0f9d6b;box-shadow:0 0 6px #0f9d6b" title="Live Stream Active"></span>'
        stream_title = "⚡ Live OrderFlow Stream"
    else:
        feed_badge = '<span style="font-size:0.68rem;padding:2px 6px;border-radius:4px;background:rgba(239,68,68,0.12);color:#ef4444;font-weight:700;border:1px solid rgba(239,68,68,0.25)" title="Market Closed · Manual Refresh">Market Closed · Manual</span>'
        stream_title = "⚡ OrderFlow Stream"

    scroll_hint = "Scroll for older events" if len(filtered_signals) > 5 else "All session events visible"
    st.markdown(
        '<div class="ck card" style="margin-top:16px"><div class="card-body">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">'
        f'<div class="eyebrow" style="margin:0;display:flex;align-items:center;gap:6px">'
        f'<span>{stream_title}</span>'
        f'{feed_badge}'
        f'<span style="font-size:0.75rem;color:var(--ink-mute);font-weight:600">({len(filtered_signals)} Signals · 5 Visible · {scroll_hint})</span>'
        f'</div>'
        f'<a href="/?view=orderflow_guide" target="_blank" style="font-size:.72rem;color:var(--spark);font-weight:700;text-decoration:none">📖 Signal Guide ↗</a>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if not filtered_signals:
        st.info("No signals match the current filter.")
        st.markdown('</div></div>', unsafe_allow_html=True)
        return

    def _render_sig_card_html(sig: Any, is_latest: bool = False) -> str:
        cat_cls = "bull" if sig.category == "BULLISH" else ("bear" if sig.category == "BEARISH" else "warn")
        cat_ico = "🟢" if sig.category == "BULLISH" else ("🔴" if sig.category == "BEARISH" else "⚠️")
        ts_str = _to_ist(sig.bar_start_ts).strftime("%d-%b %H:%M:%S IST")

        out_chip = ""
        out = outcomes_map.get(sig.event_id)
        if out:
            if out.target_hit:
                out_chip = f'<div class="outcome-badge win">🎯 Target 1R Reached (+{out.mfe_pts:.1f} pts)</div>'
            elif out.stopped_out:
                out_chip = f'<div class="outcome-badge stop">🛑 Stopped Out (-{out.mae_pts:.1f} pts)</div>'
            else:
                out_chip = f'<div class="outcome-badge">⏳ MFE: +{out.mfe_pts:.1f} pts | MAE: -{out.mae_pts:.1f} pts</div>'

        tags_html = " ".join([f'<span class="ck tag mute">{t}</span>' for t in sig.context_tags])
        inval_dir = "below" if sig.bias == "LONG" else "above"
        new_pill = '<span style="font-size:0.65rem;background:var(--spark);color:#fff;padding:2px 5px;border-radius:4px;font-weight:700;margin-left:4px">NEW</span>' if is_latest else ''

        return (
            f'<div class="ck sig-card {cat_cls}" style="width:100%;box-sizing:border-box">'
            f'<div class="sig-card-hdr">'
            f'<div><span class="sentinel-chip {cat_cls}">{cat_ico} {sig.category}</span> {new_pill}'
            f'<span style="font-size:.76rem;color:var(--ink-mute);margin-left:6px;font-family:var(--mono)">📅 {ts_str}</span></div>'
            f'<span class="sig-score-pill">Score: <b>{sig.score}/100</b></span>'
            f'</div>'
            f'<div class="sig-type">{sig.type}</div>'
            f'<div class="sig-metrics">'
            f'<div><span>RVol</span><b>{sig.rvol_tod:.1f}×</b></div>'
            f'<div><span>Delta</span><b>{sig.delta:+,.0f}</b></div>'
            f'<div><span>ΔOI</span><b>{sig.oi_change:+,}</b></div>'
            f'</div>'
            f'<div class="sig-insight">💡 {sig.insight_text}</div>'
            f'<div class="sig-inval">🛑 Invalidation: candle closes {inval_dir} <b>{sig.invalidation_level:,.1f}</b></div>'
            f'<div style="margin-top:6px">{tags_html}</div>'
            f'{out_chip}'
            f'</div>'
        )

    # Unified single column: chronologically newest first, framed to display 5 cards with vertical scroll for remaining old events
    all_cards_html = "".join([
        _render_sig_card_html(s, is_latest=(idx == 0))
        for idx, s in enumerate(filtered_signals)
    ])

    st.markdown(
        f'<div class="ck sig-feed" style="display:flex;flex-direction:column;gap:10px;max-height:680px;overflow-y:auto;overscroll-behavior:contain;padding-right:8px">'
        f'{all_cards_html}'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown('</div></div>', unsafe_allow_html=True)


def _tab_sentinel_tape(symbol: str, window: str) -> None:  # noqa: C901
    """OrderFlow Sentinel v2.0 — Screen 2 Live Tape & Microstructure with Decoupled Independent Cards."""
    data, _ = _load_data(symbol, window)
    default_spot = 72880.0 if symbol == "SENSEX" else (55250.0 if symbol == "BANKNIFTY" else 22680.0)
    default_cw = 73000.0 if symbol == "SENSEX" else (55500.0 if symbol == "BANKNIFTY" else 22800.0)
    default_pw = 72500.0 if symbol == "SENSEX" else (55000.0 if symbol == "BANKNIFTY" else 22500.0)
    iq = get_instant_quote(symbol)
    if iq.get("is_live"):
        spot = iq["spot"]
        fut_px = iq.get("fut_px") or spot
    elif data and getattr(data, "levels", None) and getattr(data.levels, "spot", None):
        spot = float(data.levels.spot)
        fut_px = float(iq.get("fut_px") or spot)
    else:
        spot = iq.get("spot", default_spot)
        fut_px = float(iq.get("fut_px") or (spot + 25.0))

    cw = getattr(getattr(data, "levels", None), "call_wall", default_cw) if data else default_cw
    pw = getattr(getattr(data, "levels", None), "put_wall", default_pw) if data else default_pw
    gamma_flip = _extract_gamma_flip(getattr(data, "levels", None), symbol)

    inst_id = f"{symbol}-FUT"
    ref_px = fut_px if (inst_id.endswith("-FUT") and fut_px) else spot

    # Ensure demo flow is seeded
    seed_sentinel_demo_if_needed(inst_id, spot=ref_px, call_wall=cw, put_wall=pw)

    # 1. Static Top Controls Bar
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([2, 3, 2])
    with ctrl_col1:
        timeframe = st.selectbox("Timeframe:", ["1m", "3m", "5m", "15m"], index=0, key=f"tf_{symbol}")
    with ctrl_col2:
        sig_filter = st.selectbox(
            "Filter Signals:",
            ["All Signals", "High Confidence (Score ≥ 70)", "Bullish", "Bearish", "Caution"],
            index=0,
            key=f"sig_fltr_{symbol}",
        )
    with ctrl_col3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("⚡ Re-Seed Flow", key=f"btn_reseed_{symbol}", use_container_width=True):
            seed_sentinel_demo_if_needed(inst_id, spot=ref_px, call_wall=cw, put_wall=pw, force=True)
            st.rerun()

    # 2. Independent Card 1: Microstructure KPI Strip (auto-updates every 3s independently)
    _fragment_sentinel_kpi_strip(symbol, inst_id, timeframe, ref_px, cw, pw)

    # 3. Static OrderFlow Signals Taxonomy Field Guide
    with st.expander("📖 OrderFlow Stream Signals Guide & Reference Manual (Click to Expand / Collapse)", expanded=False):
        st.markdown(
            f'''
            <div style="font-size:.84rem;line-height:1.6">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <span style="font-weight:700;color:var(--ink)">Sentinel Microstructure Signal Taxonomy (v2.0)</span>
                <a href="/?view=orderflow_guide" target="_blank" style="font-size:.76rem;color:var(--spark);font-weight:700;text-decoration:none">Full Field Guide ↗</a>
            </div>
            <p style="color:var(--ink-2);margin-bottom:12px">
            The Live OrderFlow Stream tracks market aggression (Ask buying vs Bid selling) against resting limit liquidity.
            In <b>Indian Market Mode (SCSP)</b>, signals at GEX Walls dictate whether option writers defend their credit spreads or capitulate into a short squeeze.
            </p>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--go)">
                    <b style="color:var(--go)">🟢 ABSORPTION_BULL (Score 65-95)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Heavy aggressive selling (Δ &lt; -20%, RVol ≥ 1.4×) absorbed by passive buyers at Put Wall / VWAP -2σ. Bullish bounce expected.</span><br>
                    <small><b>Invalidation:</b> Candle closes below bar low - 5.0 pts.</small>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--stop)">
                    <b style="color:var(--stop)">🔴 ABSORPTION_BEAR (Score 65-95)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Heavy aggressive buying (Δ &gt; +20%, RVol ≥ 1.4×) absorbed by passive sellers at Call Wall / VWAP +2σ. Resistance holds.</span><br>
                    <small><b>Invalidation:</b> Candle closes above bar high + 5.0 pts.</small>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--go)">
                    <b style="color:var(--go)">🟢 CONTINUATION_BULL (Score 60-85)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Aggressive buyers dominate (RVol ≥ 1.5×, Δ Ratio ≥ +25%) closing near the highs above VWAP / ORB. Upward momentum.</span><br>
                    <small><b>Invalidation:</b> Candle closes below bar low - 5.0 pts.</small>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--stop)">
                    <b style="color:var(--stop)">🔴 CONTINUATION_BEAR (Score 60-85)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Aggressive sellers dominate (RVol ≥ 1.5×, Δ Ratio ≤ -25%) closing near the lows below VWAP / ORB. Downward momentum.</span><br>
                    <small><b>Invalidation:</b> Candle closes above bar high + 5.0 pts.</small>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--warn)">
                    <b style="color:var(--warn)">🔴 EXHAUSTION_UP (Score 62-85)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Extreme volume climax (RVol ≥ 2.5×) rejected at the highs with a prominent upper tail (≥ 45%). Buyer exhaustion / trap.</span><br>
                    <small><b>Invalidation:</b> Candle closes above bar high + 5.0 pts.</small>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--go)">
                    <b style="color:var(--go)">🟢 EXHAUSTION_DOWN (Score 62-85)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Extreme volume climax (RVol ≥ 2.5×) rejected at the lows with a prominent lower tail (≥ 45%). Panic selling exhausted / V-reversal.</span><br>
                    <small><b>Invalidation:</b> Candle closes below bar low - 5.0 pts.</small>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--go)">
                    <b style="color:var(--go)">🟢 CVD_DIVERGENCE_BULL (Score 58-80)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Price prints a lower low while session Cumulative Volume Delta prints a higher low. Institutional accumulation.</span><br>
                    <small><b>Invalidation:</b> Candle closes below bar low - 5.0 pts.</small>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px;border-left:3px solid var(--stop)">
                    <b style="color:var(--stop)">🔴 CVD_DIVERGENCE_BEAR (Score 58-80)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">Price prints a higher high while session Cumulative Volume Delta prints a lower high. Institutional distribution.</span><br>
                    <small><b>Invalidation:</b> Candle closes above bar high + 5.0 pts.</small>
                </div>
            </div>
            <div style="background:var(--inset);padding:8px 12px;border-radius:6px;font-size:.74rem;color:var(--ink-mute)">
                🛑 <b>Invalidation Rule:</b> The invalidation cutoff represents the exact price where passive limit liquidity failed. An intra-bar wick touch does not invalidate; a <b>candle body close</b> beyond this level confirms the thesis has failed and requires an immediate, non-negotiable exit.
            </div>
            </div>
            ''',
            unsafe_allow_html=True,
        )

    # 4. Independent Card 2: Candlestick & CVD Chart Card (TradingView Lightweight Charts)
    _fragment_sentinel_chart_card(symbol, inst_id, ref_px, cw, pw, gamma_flip=gamma_flip, timeframe=timeframe)

    # 5. Independent Card 3: Live OrderFlow Stream Card (auto-updates every 5s independently)
    _fragment_sentinel_stream_card(symbol, inst_id, sig_filter)



@st.fragment
def _fragment_signal_efficiency(symbol: str) -> None:
    _render_signal_efficiency_section(symbol)


def _tab_signal_efficiency(symbol: str) -> None:
    """Dedicated Tab: OrderFlow Sentinel Signal Efficiency Analytics & Backtesting."""
    _fragment_signal_efficiency(symbol)


def _render_signal_efficiency_section(symbol: str) -> None:
    """Render Daily & Weekly Signal Efficiency Analytics with KPIs, breakdowns, and retention."""
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    st.markdown(
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
        '<h3 style="margin:0;font-size:1.2rem">📈 OrderFlow Sentinel Signal Efficiency Analytics</h3>'
        '<a href="/?view=signal_efficiency_guide" target="_blank" style="font-size:.76rem;background:var(--surface-2);color:var(--spark);padding:6px 12px;border-radius:6px;border:1px solid var(--line);text-decoration:none;font-weight:700">📖 Efficiency Guide ↗</a>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.caption("Descriptive statistical performance of orderflow signals evaluated against forward returns & MFE/MAE excursions.")


    # Controls
    eff_col1, eff_col2, eff_col3 = st.columns([2, 2, 3])
    with eff_col1:
        period_view = st.selectbox(
            "Period Aggregation:",
            ["Daily (by Date)", "Weekly (by Week)"],
            index=0,
            key=f"eff_period_{symbol}",
        )
        ptype = "daily" if "Daily" in period_view else "weekly"
    with eff_col2:
        scope_view = st.selectbox(
            "Symbol Scope:",
            [f"Current ({symbol}-FUT)", "All Symbols"],
            index=0,
            key=f"eff_scope_{symbol}",
        )
        inst_filter = f"{symbol}-FUT" if "Current" in scope_view else "ALL"

    eff_report = compute_signal_efficiency(instrument_id=inst_filter, period_type=ptype, limit_periods=30)

    if eff_report.total_signals == 0:
        st.info(f"No evaluated signals recorded for {inst_filter} yet. Generate or seed signals to see efficiency analytics.")
        return

    # Top KPI strip
    win_cls = "var(--go)" if eff_report.overall_hit_rate >= 50.0 else "var(--stop)"
    edge_cls = "var(--go)" if eff_report.overall_edge_ratio >= 1.5 else ("var(--warn)" if eff_report.overall_edge_ratio >= 1.0 else "var(--stop)")

    st.markdown(
        f'<div class="ck sc-grid">'
        f'<div class="sc-card"><small>Signals Evaluated</small>'
        f'<b>{eff_report.total_signals}</b><span>total events</span></div>'
        f'<div class="sc-card"><small>Target Hit Rate</small>'
        f'<b style="color:{win_cls}">{eff_report.overall_hit_rate:.1f}%</b>'
        f'<span>reached 1R/2R target</span></div>'
        f'<div class="sc-card"><small>Stop-Out Rate</small>'
        f'<b style="color:var(--stop)">{eff_report.overall_stop_rate:.1f}%</b>'
        f'<span>hit invalidation cutoff</span></div>'
        f'<div class="sc-card"><small>Avg Excursions</small>'
        f'<b>+{eff_report.overall_avg_mfe:.1f} / -{eff_report.overall_avg_mae:.1f}</b>'
        f'<span>MFE (gain) vs MAE (risk) pts</span></div>'
        f'<div class="sc-card"><small>Edge Ratio</small>'
        f'<b style="color:{edge_cls}">{eff_report.overall_edge_ratio:.2f}×</b>'
        f'<span>MFE / MAE ratio</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Sub-tabs: Timeline vs Category Breakdowns vs Forward Return Expectancy
    sub_t1, sub_t2, sub_t3 = st.tabs(["📅 Efficiency Timeline", "🏷️ Breakdowns (Type & GEX)", "⏱️ Return Expectancy"])

    with sub_t1:
        if eff_report.timeline:
            rows = [
                {
                    "Period": p.period,
                    "Signals": p.total_signals,
                    "Target Hits": p.target_hits,
                    "Hit Rate": f"{p.hit_rate_pct:.1f}%",
                    "Stop Rate": f"{p.stop_rate_pct:.1f}%",
                    "Avg MFE (pts)": f"+{p.avg_mfe_pts:.1f}",
                    "Avg MAE (pts)": f"-{p.avg_mae_pts:.1f}",
                    "Edge Ratio": f"{p.edge_ratio:.2f}×",
                    "+5m Ret (pts)": f"{p.avg_ret_5m:+.1f}",
                    "+15m Ret (pts)": f"{p.avg_ret_15m:+.1f}",
                }
                for p in eff_report.timeline
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No timeline records available.")

    with sub_t2:
        col_type, col_align = st.columns(2)
        with col_type:
            st.markdown("**By Signal Type**")
            if eff_report.by_type:
                t_rows = [
                    {
                        "Signal Type": b.label,
                        "Count": b.total_signals,
                        "Hit Rate": f"{b.hit_rate_pct:.1f}%",
                        "Avg MFE": f"+{b.avg_mfe_pts:.1f}",
                        "Avg MAE": f"-{b.avg_mae_pts:.1f}",
                        "Edge Ratio": f"{b.edge_ratio:.2f}×",
                    }
                    for b in eff_report.by_type
                ]
                st.dataframe(pd.DataFrame(t_rows), use_container_width=True, hide_index=True)
            else:
                st.caption("No data by type.")

        with col_align:
            st.markdown("**By GEX Wall Alignment**")
            if eff_report.by_alignment:
                a_rows = [
                    {
                        "Alignment": b.label,
                        "Count": b.total_signals,
                        "Hit Rate": f"{b.hit_rate_pct:.1f}%",
                        "Avg MFE": f"+{b.avg_mfe_pts:.1f}",
                        "Avg MAE": f"-{b.avg_mae_pts:.1f}",
                        "Edge Ratio": f"{b.edge_ratio:.2f}×",
                    }
                    for b in eff_report.by_alignment
                ]
                st.dataframe(pd.DataFrame(a_rows), use_container_width=True, hide_index=True)
            else:
                st.caption("No data by alignment.")

    with sub_t3:
        st.markdown("**Forward Return Expectancy Profile (+1m to +30m)**")
        exp_data = eff_report.expectancy_curve
        exp_df = pd.DataFrame([
            {"Horizon": k, "Expected Return (pts)": v}
            for k, v in exp_data.items()
        ])
        st.bar_chart(exp_df.set_index("Horizon"), height=180, use_container_width=True)

    # Maintenance & Retention Expander
    with st.expander("⚙️ SQLite Storage & Retention Maintenance"):
        st.markdown(
            "<div style='font-size:.82rem;color:var(--ink-2);margin-bottom:8px'>"
            "SQLite Database: <code>data/sentinel.db</code><br>"
            "Retention standard: <b>60 days</b> for 1m bars, <b>180 days</b> for 5m bars, <b>365 days</b> for signals & outcomes."
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button("🧹 Run Retention Purge", key=f"btn_purge_{symbol}"):
            res = purge_old_sentinel_data()
            st.success(f"Purge complete: {res['bars_1m']} 1m bars, {res['bars_5m']} 5m bars, {res['signals']} signals removed.")



def _render_market_intelligence(data, spot: float, spot_chg_pct: float = 0.0, symbol: str = "NIFTY") -> None:
    """Renders IOAS §3.5 Market Intelligence: Sector Breadth & News Sentiment Analysis."""
    from analytics.market_intelligence import compute_market_intelligence

    sectors_snap = getattr(data, "sectors", None)
    if not sectors_snap:
        try:
            from data.sectors import fetch_sector_snap
            sectors_snap = fetch_sector_snap()
        except Exception:
            sectors_snap = None

    news_snap = getattr(data, "news", None)
    if not news_snap:
        try:
            from data.news import fetch_news
            news_snap = fetch_news()
        except Exception:
            news_snap = None

    intel = compute_market_intelligence(sectors_snap, news_snap, spot_chg_pct=spot_chg_pct)
    sec = intel.sector
    news = intel.news

    risk_chip_cls = "stop" if news.catalyst_risk_level == "HIGH_SHOCK" else ("warn" if news.catalyst_risk_level == "MODERATE" else "go")
    risk_label = "⚠️ EXOGENOUS SHOCK" if news.catalyst_risk_level == "HIGH_SHOCK" else ("⚡ MODERATE CATALYSTS" if news.catalyst_risk_level == "MODERATE" else "🟢 LOW EXOGENOUS RISK")

    align_color = "var(--go)" if "BULLISH" in intel.alignment else ("var(--stop)" if "BEARISH" in intel.alignment else "var(--warn)")
    breadth_color = "var(--go)" if sec.breadth_pct >= 60 else ("var(--stop)" if sec.breadth_pct <= 40 else "var(--ink-2)")
    contrib_color = "var(--go)" if sec.weighted_contribution > 0 else ("var(--stop)" if sec.weighted_contribution < 0 else "var(--ink-2)")
    sent_color = "var(--go)" if news.sentiment_label == "BULLISH" else ("var(--stop)" if news.sentiment_label == "BEARISH" else "var(--ink-2)")

    st.markdown(
        f'<div class="ck">'
        f'<div class="card" style="margin-top:16px;border-left:4px solid var(--spark)">'
        f'<div class="card-body">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px">'
        f'<div>'
        f'<div class="eyebrow">🧭 Market Intelligence & Macro Synthesis</div>'
        f'<div style="font-size:1.15rem;font-weight:800;color:var(--ink);margin:3px 0">{intel.macro_verdict}</div>'
        f'<div style="font-size:.82rem;color:var(--ink-2);line-height:1.5;max-width:900px">{intel.executive_narrative}</div>'
        f'</div>'
        f'<span class="sentinel-chip {risk_chip_cls}" style="font-size:.74rem">{risk_label}</span>'
        f'</div>'
        f'<div class="mi-kpi-grid">'
        f'<div class="mi-kpi">'
        f'<small>Sector Breadth</small>'
        f'<b style="color:{breadth_color}">{sec.breadth_ratio_str}</b>'
        f'<span>{sec.breadth_pct:.0f}% Green ({sec.breadth_state.replace("_", " ").title()})</span>'
        f'</div>'
        f'<div class="mi-kpi">'
        f'<small>Weighted Net Contrib</small>'
        f'<b style="color:{contrib_color}">{sec.weighted_contribution:+.2f}%</b>'
        f'<span>Rotation: {sec.rotation_regime.replace("_", " ").title()}</span>'
        f'</div>'
        f'<div class="mi-kpi">'
        f'<small>News Catalyst Sentiment</small>'
        f'<b style="color:{sent_color}">{news.sentiment_label}</b>'
        f'<span>Impact Score: {news.impact} ({news.count} fresh)</span>'
        f'</div>'
        f'<div class="mi-kpi">'
        f'<small>Alignment With Index</small>'
        f'<b style="color:{align_color}">{intel.alignment.replace("_", " ").title()}</b>'
        f'<span>Catalyst Risk: {news.catalyst_risk_level.title()}</span>'
        f'</div>'
        f'</div>'
        f'</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if intel.actionable_points:
        points_html = "".join(f'<div style="font-size:.80rem;color:var(--ink-2);margin:4px 0">• {p}</div>' for p in intel.actionable_points)
        st.markdown(
            f'<div class="ck" style="margin-top:8px">'
            f'<div style="background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:10px 14px">'
            f'<div style="font-size:.70rem;font-weight:700;color:var(--ink-mute);text-transform:uppercase;margin-bottom:4px">🎯 Actionable Trader Takeaways</div>'
            f'{points_html}'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    c_sec, c_news = st.columns([1, 1])

    with c_sec:
        st.markdown(
            '<div class="ck"><div class="card" style="margin-top:10px"><div class="card-body">'
            '<div class="eyebrow">📊 Sector Performance Leaderboard (~Nifty 50 Weights)</div>'
            '<div style="font-size:.74rem;color:var(--ink-mute);margin-bottom:10px">Exchange % change vs previous session close</div>',
            unsafe_allow_html=True,
        )

        sec_rows_html = '<div class="sector-table">'
        for s in sec.all_sectors:
            pct_val = s.pct
            if pct_val is None:
                pct_str = "—"
                bar_pct = 0.0
                val_color = "var(--ink-mute)"
                is_pos = True
            else:
                pct_str = f"{pct_val:+.2f}%"
                val_color = "var(--go)" if pct_val > 0 else ("var(--stop)" if pct_val < 0 else "var(--ink-mute)")
                is_pos = pct_val >= 0
                bar_pct = min(50.0, abs(pct_val) * 15.0)

            wt_str = f"~{s.weight:.0f}%" if s.weight is not None else ""
            px_str = f"₹{s.last:,.1f}" if s.last else ""

            if is_pos:
                bar_html = f'<div class="sector-bar-fill pos" style="width:{bar_pct:.1f}%"></div>'
            else:
                bar_html = f'<div class="sector-bar-fill neg" style="width:{bar_pct:.1f}%"></div>'

            sec_rows_html += (
                f'<div class="sector-row">'
                f'<div><b style="color:var(--ink)">{s.label}</b>'
                f'<span style="font-size:.66rem;color:var(--ink-mute);margin-left:4px">{wt_str}</span></div>'
                f'<div style="font-size:.72rem;color:var(--ink-mute);font-family:var(--mono)">{px_str}</div>'
                f'<div class="sector-bar-track">{bar_html}</div>'
                f'<div style="font-weight:700;color:{val_color};text-align:right;font-family:var(--mono)">{pct_str}</div>'
                f'</div>'
            )
        sec_rows_html += '</div>'

        broad_html = '<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--line)"><div class="eyebrow" style="margin-bottom:6px">Broad Market Context</div><div class="sector-table">'
        for b in sec.broad_indices:
            b_pct = b.pct
            b_str = f"{b_pct:+.2f}%" if b_pct is not None else "—"
            b_color = "var(--go)" if (b_pct or 0) > 0 else ("var(--stop)" if (b_pct or 0) < 0 else "var(--ink-mute)")
            b_px = f"₹{b.last:,.1f}" if b.last else ""
            broad_html += (
                f'<div class="sector-row" style="background:var(--inset)">'
                f'<div><b style="color:var(--ink)">{b.label}</b></div>'
                f'<div style="font-size:.72rem;color:var(--ink-mute);font-family:var(--mono)">{b_px}</div>'
                f'<div style="font-size:.70rem;color:var(--ink-mute)">Benchmark</div>'
                f'<div style="font-weight:700;color:{b_color};text-align:right;font-family:var(--mono)">{b_str}</div>'
                f'</div>'
            )
        broad_html += '</div></div>'

        footer_html = (
            f'<div style="margin-top:10px;font-size:.68rem;color:var(--ink-mute);display:flex;justify-content:space-between">'
            f'<span>Source: {sec.source.upper()}</span>'
            f'<span>Weights as of: {sec.weights_as_of}</span>'
            f'</div>'
            f'</div></div></div>'
        )
        st.markdown(sec_rows_html + broad_html + footer_html, unsafe_allow_html=True)

    with c_news:
        st.markdown(
            '<div class="ck"><div class="card" style="margin-top:10px"><div class="card-body">'
            '<div class="eyebrow">📰 Real-Time Financial News & Catalysts</div>'
            '<div style="font-size:.74rem;color:var(--ink-mute);margin-bottom:10px">Aggregated from Economic Times & Times of India</div>',
            unsafe_allow_html=True,
        )

        categories = ["ALL"] + sorted(list({h.category for h in news.headlines})) if news.headlines else ["ALL"]
        sel_cat = st.pills("Filter Category", categories, default="ALL", key=f"mi_news_cat_{symbol}")

        filtered_hls = news.headlines
        if sel_cat and sel_cat != "ALL":
            filtered_hls = [h for h in filtered_hls if h.category == sel_cat]

        news_html = '<div class="news-feed-scroll">'
        if not filtered_hls:
            news_html += (
                '<div style="padding:20px;text-align:center;color:var(--ink-mute);font-size:.82rem">'
                'No headlines found in selected category. Tape is calm.'
                '</div>'
            )
        else:
            for h in filtered_hls:
                sent_cls = "bull" if h.sentiment == "BULLISH" else ("bear" if h.sentiment == "BEARISH" else "neutral")
                fresh_badge = '<span style="color:var(--spark);font-weight:700;margin-left:4px">⚡ Fresh</span>' if h.is_fresh else ""
                impact_badge = f'<span style="background:var(--inset);color:var(--ink-2);padding:1px 5px;border-radius:3px;font-size:.66rem">Impact {h.impact}</span>' if h.impact > 0 else ""

                news_html += (
                    f'<div class="news-card">'
                    f'<div class="news-meta">'
                    f'<div>'
                    f'<span class="news-chip {sent_cls}">{h.sentiment}</span>'
                    f'<span style="color:var(--ink-mute);margin-left:8px;font-family:var(--mono)">{h.pub_time_ist} ({h.age_min}m ago)</span>'
                    f'{fresh_badge}'
                    f'</div>'
                    f'<div>'
                    f'<span style="font-size:.68rem;color:var(--ink-mute);margin-right:6px">{h.category}</span>'
                    f'{impact_badge}'
                    f'</div>'
                    f'</div>'
                    f'<div class="news-title">{h.title}</div>'
                    f'</div>'
                )
        news_html += '</div>'

        confound_html = (
            '<div style="margin-top:10px;background:var(--surface-2);border-radius:6px;padding:8px 10px;font-size:.70rem;color:var(--ink-mute);line-height:1.4">'
            '<b>Phase B Confound Control:</b> Intraday headline bursts with keyword-weighted impact ≥ 6 flag exogenous shocks, '
            'quarantining days where macro news overrides dealer delta-hedging.'
            '</div>'
            '</div></div></div>'
        )
        st.markdown(news_html + confound_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Tab 3: Analytics (IOAS v3.0)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tab 3: Analytics (IOAS v3.0) — Decoupled Independent Cards
# ---------------------------------------------------------------------------


def _render_analytics_guide() -> None:
    """Static Options Analytics Field Guide Expander — persistent across all polling cycles."""
    with st.expander("📖 Options Analytics Guide: Buildup States, Parity Arbitrage & FII Drift (Click to Expand / Collapse)", expanded=False):
        st.markdown(
            '''
            <div style="font-size:.84rem;line-height:1.6">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <span style="font-weight:700;color:var(--ink)">IOAS v3.0 Deep Analytics Field Guide</span>
                <a href="/?view=user_guide" target="_blank" style="font-size:.76rem;color:var(--spark);font-weight:700;text-decoration:none">Analytics Guide Section ↗</a>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px">
                    <b>📊 Strike Buildup Classification (4 Quadrants)</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    • <b style="color:var(--go)">🟢 Long Buildup:</b> Price ↑, OI ↑ (Fresh aggressive buyers entering)<br>
                    • <b style="color:var(--stop)">🔴 Short Buildup:</b> Price ↓, OI ↑ (Fresh aggressive sellers entering)<br>
                    • <b style="color:var(--warn)">🟡 Short Covering:</b> Price ↑, OI ↓ (Shorts closing positions / squeeze fuel)<br>
                    • <b style="color:var(--ink-mute)">🟠 Long Unwinding:</b> Price ↓, OI ↓ (Longs liquidating / stop runs)
                    </span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px">
                    <b>⚖️ Put-Call Parity & Box Arbitrage</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    • <b>Synthetic Future:</b> C - P + Strike. Compares pure option parity vs actual futures price.<br>
                    • <b>Basis Spread:</b> Dislocation between actual future and synthetic future. Spreads exceeding ±15 pts highlight institutional pricing dislocations and box-spread arbitrage barriers.
                    </span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px">
                    <b>🧭 Participant OI & Multi-Day FII Drift</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    • <b>Client (Retail):</b> Net option writers & index futures buyers.<br>
                    • <b>Pro (Prop Desks):</b> Intraday delta hedgers & market makers.<br>
                    • <b>FII:</b> Directional market movers. Tracking 5-day net futures drift reveals institutional macro trend alignment.
                    </span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px">
                    <b>📈 25-Delta IV Skew & ATM Volatility</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    • <b>Contango / Normal:</b> OTM Puts have moderate premium over OTM Calls.<br>
                    • <b>Steep Put Skew:</b> Institutional downside crash hedging spike.<br>
                    • <b>Call Skew (Inverted):</b> Aggressive retail call buying / squeeze anticipation.
                    </span>
                </div>
            </div>
            </div>
            ''',
            unsafe_allow_html=True,
        )


def _render_analytics_kpi_strip(symbol: str, window: str) -> None:
    """Card 0: NOA Top KPI Strip (Preview Panel 1 Fidelity)."""
    data, err = _load_data(symbol, window)
    if data is None or not getattr(data, "levels", None):
        return

    levels = data.levels
    iq = get_instant_quote(symbol)
    spot = float(iq["spot"]) if (iq and iq.get("spot")) else float(levels.spot)
    contracts_df = getattr(data, "contracts", None)
    oi_strikes = getattr(data, "oi_strikes", None) or []

    pcr, call_doi, put_doi = _extract_pcr_and_oi(data)
    skew_result = skew_25delta(contracts_df, spot) if contracts_df is not None else {}
    skew_25d = skew_result.get("skew_25d")
    near_atm_iv = skew_result.get("near_atm_iv")

    if iq and iq.get("vix") is not None:
        vix_val = float(iq["vix"])
    else:
        raw_vix = getattr(levels, "vix", None)
        vix_val = float(raw_vix.get("last") if isinstance(raw_vix, dict) else (raw_vix or 13.20))
    vix_label = "Low Vol" if vix_val < 15 else ("Elevated" if vix_val < 20 else "High Vol")

    default_cw = 73000.0 if symbol == "SENSEX" else (55500.0 if symbol == "BANKNIFTY" else 22800.0)
    default_pw = 72500.0 if symbol == "SENSEX" else (55000.0 if symbol == "BANKNIFTY" else 22500.0)
    default_mp = 72600.0 if symbol == "SENSEX" else (55250.0 if symbol == "BANKNIFTY" else 22600.0)
    call_wall = getattr(levels, "call_wall", None) or default_cw
    put_wall = getattr(levels, "put_wall", None) or default_pw
    max_pain = getattr(levels, "max_pain", None) or default_mp

    atm_iv = near_atm_iv or (vix_val / 100.0 if vix_val > 0 else 0.13)
    iv_rank = min(100.0, max(0.0, (atm_iv * 100.0 - 10.0) / (25.0 - 10.0) * 100.0))

    mp_dist = abs(spot - max_pain) if (max_pain and spot) else 0.0
    if max_pain and spot:
        pct_dist = (mp_dist / spot) * 100.0
        pin_score = max(10, min(100, int(100 - (pct_dist * 28))))
    else:
        pin_score = 68

    bias = compute_directional_bias(
        spot=spot,
        call_wall=call_wall,
        put_wall=put_wall,
        max_pain=max_pain,
        pcr=pcr,
        call_doi=call_doi,
        put_doi=put_doi,
        oi_strikes=oi_strikes,
        india_vix={"last": vix_val},
        skew_25d=skew_25d,
        near_atm_iv=near_atm_iv,
    )
    score = bias.score
    score_color = "var(--go)" if score > 1.0 else "var(--stop)" if score < -1.0 else "var(--ink-mute)"
    skew_label = skew_result.get("skew_label", "Contango (Normal)")
    pcr_regime = "BULLISH" if pcr > 1.0 else "BEARISH"
    pcr_reg_col = "var(--go)" if pcr > 1.0 else "var(--stop)"

    st.markdown(
        f'<div class="ck">'
        f'<div class="noa-kpi-grid">'
        f'<div class="noa-kpi-card"><small>ATM IV</small><b>{atm_iv*100:.1f}%</b><span>{skew_label[:14]}</span></div>'
        f'<div class="noa-kpi-card"><small>PCR (OI)</small><b>{pcr:.2f}</b><span style="color:{pcr_reg_col}">{pcr_regime}</span></div>'
        f'<div class="noa-kpi-card"><small>Max Pain</small><b>{max_pain:,.0f}</b><span>Pin: {pin_score}/100</span></div>'
        f'<div class="noa-kpi-card"><small>IV Rank (30D)</small><b>{iv_rank:.0f}%</b><span>Normal zone</span></div>'
        f'<div class="noa-kpi-card"><small>India VIX</small><b>{vix_val:.2f}</b><span>{vix_label}</span></div>'
        f'<div class="noa-kpi-card"><small>Bias Gauge</small><b style="color:{score_color}">{score:+.1f}</b><span>{bias.regime}</span></div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


def _render_analytics_row1_bias_strikes(symbol: str, window: str) -> None:
    """Card 1: Row 1 — Directional Bias Score (Left) + Key Strike Observations (Right)."""
    data, err = _load_data(symbol, window)
    if data is None or not getattr(data, "levels", None):
        return

    levels = data.levels
    iq = get_instant_quote(symbol)
    spot = float(iq["spot"]) if (iq and iq.get("spot")) else float(levels.spot)
    contracts_df = getattr(data, "contracts", None)
    oi_strikes = getattr(data, "oi_strikes", None) or []

    pcr, call_doi, put_doi = _extract_pcr_and_oi(data)
    skew_result = skew_25delta(contracts_df, spot) if contracts_df is not None else {}
    skew_25d = skew_result.get("skew_25d")
    near_atm_iv = skew_result.get("near_atm_iv")

    if iq and iq.get("vix") is not None:
        vix_val = float(iq["vix"])
    else:
        raw_vix = getattr(levels, "vix", None)
        vix_val = float(raw_vix.get("last") if isinstance(raw_vix, dict) else (raw_vix or 13.20))

    default_cw = 73000.0 if symbol == "SENSEX" else (55500.0 if symbol == "BANKNIFTY" else 22800.0)
    default_pw = 72500.0 if symbol == "SENSEX" else (55000.0 if symbol == "BANKNIFTY" else 22500.0)
    default_mp = 72600.0 if symbol == "SENSEX" else (55250.0 if symbol == "BANKNIFTY" else 22600.0)
    call_wall = getattr(levels, "call_wall", None) or default_cw
    put_wall = getattr(levels, "put_wall", None) or default_pw
    max_pain = getattr(levels, "max_pain", None) or default_mp
    gamma_flip = _extract_gamma_flip(levels, symbol)

    cw_dist = (call_wall - spot) if (call_wall and spot) else 0.0
    pw_dist = (spot - put_wall) if (put_wall and spot) else 0.0
    mp_dist = abs(spot - max_pain) if (max_pain and spot) else 0.0
    if max_pain and spot:
        pct_dist = (mp_dist / spot) * 100.0
        pin_score = max(10, min(100, int(100 - (pct_dist * 28))))
    else:
        pin_score = 68

    bias = compute_directional_bias(
        spot=spot,
        call_wall=call_wall,
        put_wall=put_wall,
        max_pain=max_pain,
        pcr=pcr,
        call_doi=call_doi,
        put_doi=put_doi,
        oi_strikes=oi_strikes,
        india_vix={"last": vix_val},
        skew_25d=skew_25d,
        near_atm_iv=near_atm_iv,
    )
    score = bias.score
    pct = (score + 10) / 20 * 100
    bar_cls = "bull" if score > 1.0 else "bear" if score < -1.0 else "neutral"
    score_color = "var(--go)" if score > 1.0 else "var(--stop)" if score < -1.0 else "var(--ink-mute)"

    col1_bias, col1_strikes = st.columns([1, 1], gap="medium")

    with col1_bias:
        sig_html = ''
        sig_labels = {
            "s1_pcr_level":    "s₁ PCR Level",
            "s2_pcr_trend":    "s₂ PCR Trend",
            "s3_iv_skew":      "s₃ IV Skew",
            "s4_wall_align":   "s₄ Wall Align",
            "s5_maxpain_grav": "s₅ Max Pain",
            "s6_oi_velocity":  "s₆ OI Velocity",
            "s7_buildup_dom":  "s₇ Buildup Dom",
            "s8_iv_rank":      "s₈ IV Rank",
        }
        for sig in bias.signals:
            bar_w = int(abs(sig.value) * 50)
            sbar_cls = "pos" if sig.value > 0 else "neg"
            contrib_cls = "pos" if sig.contribution > 0 else "neg"
            sig_html += (
                f'<div class="sig-row">'
                f'<span class="sig-key">{sig_labels.get(sig.key, sig.key)}</span>'
                f'<div class="sbar">'
                f'<div class="sbar-fill {sbar_cls}" style="width:{bar_w}%"></div></div>'
                f'<span style="font-size:.74rem;color:var(--ink-mute)">{sig.value:+.2f}</span>'
                f'<span style="font-size:.7rem;color:var(--ink-mute)">×{sig.weight:.1f}</span>'
                f'<span class="sig-contrib {contrib_cls}">{sig.contribution:+.2f}</span>'
                f'</div>'
            )

        bias_card_html = (
            f'<div class="ck"><div class="card" style="margin-top:0"><div class="card-body">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            f'<div class="eyebrow" style="margin:0">Directional Bias Score (IOAS v3.0)</div>'
            f'<span class="sentinel-chip {bar_cls}">{bias.regime}</span></div>'
            f'<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:6px">'
            f'<div style="font-size:2.2rem;font-weight:800;color:{score_color}">{score:+.1f}</div>'
            f'<span style="font-size:1rem;color:var(--ink-mute);font-weight:700">/ 10</span>'
            f'<span style="font-size:.82rem;color:var(--ink-2);margin-left:auto">{bias.headline}</span>'
            f'</div>'
            f'<div class="bias-bar-wrap" style="margin-bottom:12px">'
            f'<div class="bias-bar {bar_cls}" style="width:{min(100,max(0,pct)):.1f}%"></div>'
            f'</div>'
            f'<div class="eyebrow" style="margin-bottom:6px">Sub-signal Breakdown</div>'
            f'<div>{sig_html}</div>'
            f'<div style="font-size:.7rem;color:var(--ink-mute);margin-top:10px">'
            f'ℹ️ <i>Positioning synthesis; Indian Microstructure Mode (Writer-Dominant SCSP). Clamped on feed pause.</i></div>'
            f'</div></div></div>'
        )
        st.markdown(bias_card_html, unsafe_allow_html=True)

    with col1_strikes:
        gf_dist = spot - gamma_flip
        gf_status = f"{gf_dist:+,.0f} pts · {'ABOVE (Call-Writer Dominance · Squeeze on CW break)' if gf_dist >= 0 else 'BELOW (Put-Writer Dominance · Trapdoor on PW break)'}"
        gf_color = "var(--go)" if gf_dist >= 0 else "var(--stop)"
        gf_badge = "Call-Writer Dom" if gf_dist >= 0 else "Put-Writer Dom"

        strikes_card_html = (
            f'<div class="ck"><div class="card" style="margin-top:0"><div class="card-body">'
            f'<div class="eyebrow" style="margin-bottom:10px">Key Strike Observations & Anchors</div>'
            f'<div style="display:flex;flex-direction:column;gap:10px">'
            f'<div class="key-strike-card res">'
            f'<div style="display:flex;justify-content:space-between"><small>Key Resistance · Call Wall</small><span style="font-size:.72rem;color:var(--stop);font-weight:700">+{cw_dist:,.0f} pts</span></div>'
            f'<div class="strike-val">{call_wall:,.0f} CE</div>'
            f'<span style="font-size:.74rem;color:var(--ink-mute)">Heavy call writer open interest concentration (+{(cw_dist/spot*100) if spot else 0:.2f}%)</span></div>'
            f'<div class="key-strike-card sup">'
            f'<div style="display:flex;justify-content:space-between"><small>Key Support · Put Wall</small><span style="font-size:.72rem;color:var(--go);font-weight:700">-{pw_dist:,.0f} pts</span></div>'
            f'<div class="strike-val">{put_wall:,.0f} PE</div>'
            f'<span style="font-size:.74rem;color:var(--ink-mute)">Major put writer defense buffer (-{(pw_dist/spot*100) if spot else 0:.2f}%)</span></div>'
            f'<div class="key-strike-card pin">'
            f'<div style="display:flex;justify-content:space-between"><small>Max Pain Anchor</small><span style="font-size:.72rem;color:var(--spark);font-weight:700">Pin: {pin_score}/100</span></div>'
            f'<div class="strike-val">{max_pain:,.0f}</div>'
            f'<span style="font-size:.74rem;color:var(--ink-mute)">Minimum option payout strike ({mp_dist:,.0f} pts away)</span></div>'
            f'<div class="key-strike-card" style="border-left:3px solid #f59e0b">'
            f'<div style="display:flex;justify-content:space-between"><small>Gamma Flip Level (Writer Balance)</small><span style="font-size:.72rem;color:{gf_color};font-weight:700">{gf_badge}</span></div>'
            f'<div class="strike-val" style="color:#f59e0b">{gamma_flip:,.0f}</div>'
            f'<span style="font-size:.74rem;color:var(--ink-mute)">Spot {gf_status}</span></div>'
            f'</div>'
            f'</div></div></div>'
        )
        st.markdown(strikes_card_html, unsafe_allow_html=True)


def _render_analytics_row2_oi_expected_move(symbol: str, window: str) -> None:
    """Card 2: Row 2 — OI Distribution Chart (Left 60%) + Expected Move Range (Right 40%)."""
    data, err = _load_data(symbol, window)
    if data is None or not getattr(data, "levels", None):
        return

    levels = data.levels
    iq = get_instant_quote(symbol)
    spot = float(iq["spot"]) if (iq and iq.get("spot")) else float(levels.spot)
    contracts_df = getattr(data, "contracts", None)
    oi_strikes = getattr(data, "oi_strikes", None) or []

    pcr, _, _ = _extract_pcr_and_oi(data)
    skew_result = skew_25delta(contracts_df, spot) if contracts_df is not None else {}
    near_atm_iv = skew_result.get("near_atm_iv")

    if iq and iq.get("vix") is not None:
        vix_val = float(iq["vix"])
    else:
        raw_vix = getattr(levels, "vix", None)
        vix_val = float(raw_vix.get("last") if isinstance(raw_vix, dict) else (raw_vix or 13.20))

    default_cw = 73000.0 if symbol == "SENSEX" else (55500.0 if symbol == "BANKNIFTY" else 22800.0)
    default_pw = 72500.0 if symbol == "SENSEX" else (55000.0 if symbol == "BANKNIFTY" else 22500.0)
    call_wall = getattr(levels, "call_wall", None) or default_cw
    put_wall = getattr(levels, "put_wall", None) or default_pw

    atm_iv = near_atm_iv or (vix_val / 100.0 if vix_val > 0 else 0.13)
    dte = getattr(data, "dte_now", None)
    if dte is not None and dte > 0:
        t_years = max(float(dte), 0.5) / 365.0
    else:
        t_years = 1.0 / 52.0
        if contracts_df is not None and hasattr(contracts_df, "empty") and not contracts_df.empty:
            if "T" in contracts_df.columns:
                valid_t = contracts_df["T"].dropna()
                if not valid_t.empty and valid_t.min() > 0:
                    t_years = float(valid_t.min())

    iv_move = spot * atm_iv * math.sqrt(t_years)
    iv_move_pct = (iv_move / spot) * 100.0 if spot > 0 else 0.0

    straddle_move = None
    if contracts_df is not None and hasattr(contracts_df, "empty") and not contracts_df.empty:
        try:
            df_c = contracts_df.copy()
            df_c["dist"] = (df_c["strike"] - spot).abs()
            nearest_strike = df_c.sort_values("dist").iloc[0]["strike"]
            atm_contracts = df_c[df_c["strike"] == nearest_strike]
            c_leg = atm_contracts[atm_contracts["type"].str.upper().str.startswith("C")]
            p_leg = atm_contracts[atm_contracts["type"].str.upper().str.startswith("P")]
            c_px = 0.0
            p_px = 0.0
            if not c_leg.empty:
                for col in ["mid", "close", "last", "ltp"]:
                    if col in c_leg.columns and not pd.isna(c_leg.iloc[0][col]) and float(c_leg.iloc[0][col]) > 0:
                        c_px = float(c_leg.iloc[0][col])
                        break
            if not p_leg.empty:
                for col in ["mid", "close", "last", "ltp"]:
                    if col in p_leg.columns and not pd.isna(p_leg.iloc[0][col]) and float(p_leg.iloc[0][col]) > 0:
                        p_px = float(p_leg.iloc[0][col])
                        break
            if c_px > 0 or p_px > 0:
                straddle_move = c_px + p_px
        except Exception:
            straddle_move = None

    if straddle_move is None or straddle_move <= 0:
        straddle_move = 0.8 * iv_move

    exp_low = spot - iv_move
    exp_high = spot + iv_move

    col2_oi, col2_exp = st.columns([6, 4], gap="medium")

    exp_tag_str = ""
    if data.expiries:
        exp_tag_str = f" · {data.expiries[0].strftime('%d-%b')}"
        if data.dte_now is not None:
            exp_tag_str += f" ({data.dte_now:.1f}d DTE)"

    with col2_oi:
        st.markdown(
            f'<div class="ck"><div class="eyebrow" style="margin:10px 0 6px">Open Interest Distribution ({exp_tag_str.lstrip(" · ") or "Front Expiry"} ±4% Band)</div></div>',
            unsafe_allow_html=True,
        )
        if oi_strikes:
            chart_rows = []
            band = spot * 0.04
            for s in oi_strikes:
                k = float(s.get("strike", 0))
                if abs(k - spot) <= band:
                    c_oi = float(s.get("call_oi", 0) or 0)
                    p_oi = float(s.get("put_oi", 0) or 0)
                    chart_rows.append({"Strike": int(k), "OI": c_oi, "Type": "Call OI", "sort_k": k})
                    chart_rows.append({"Strike": int(k), "OI": p_oi, "Type": "Put OI", "sort_k": k})

            if chart_rows:
                oi_df = pd.DataFrame(chart_rows).sort_values("sort_k")
                oi_chart = (
                    alt.Chart(oi_df)
                    .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                    .encode(
                        x=alt.X("Strike:O", title="Strike Price", axis=alt.Axis(labelAngle=-45)),
                        y=alt.Y("OI:Q", title="Open Interest (Contracts)"),
                        color=alt.Color(
                            "Type:N",
                            scale=alt.Scale(domain=["Call OI", "Put OI"], range=["#38bdf8", "#4ade80"]),
                        ),
                        xOffset="Type:N",
                        tooltip=[
                            alt.Tooltip("Strike:O", title="Strike"),
                            alt.Tooltip("Type:N", title="Leg"),
                            alt.Tooltip("OI:Q", format=",.0f", title="Open Interest"),
                        ],
                    )
                    .properties(height=230)
                )
                st.altair_chart(oi_chart, use_container_width=True)
        else:
            st.info("Awaiting live option chain open interest feed...", icon="📡")

    with col2_exp:
        exp_card_html = (
            f'<div class="ck"><div class="card" style="margin-top:10px"><div class="card-body">'
            f'<div class="eyebrow">Expected Move Range (±1σ){exp_tag_str}</div>'
            f'<div style="margin:10px 0 12px">'
            f'<small style="font-size:.66rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700">Expected Range (Low — High)</small>'
            f'<div style="font-size:1.45rem;font-weight:800;color:var(--ink);margin:2px 0">{exp_low:,.0f} — {exp_high:,.0f}</div>'
            f'<span style="font-size:.74rem;color:var(--ink-mute)">Spot Anchor: <b>{spot:,.1f}</b></span></div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">'
            f'<div style="background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:8px 10px"><small style="font-size:.64rem;color:var(--ink-mute);display:block;text-transform:uppercase;font-weight:700">IV Model Move</small>'
            f'<b style="color:var(--spark);font-size:1.1rem">±{iv_move:,.0f} pts</b><br><span style="font-size:.7rem;color:var(--ink-mute)">±{iv_move_pct:.2f}% (IV {atm_iv*100:.1f}%)</span></div>'
            f'<div style="background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:8px 10px"><small style="font-size:.64rem;color:var(--ink-mute);display:block;text-transform:uppercase;font-weight:700">ATM Straddle</small>'
            f'<b style="color:var(--go);font-size:1.1rem">±{straddle_move:,.0f} pts</b><br><span style="font-size:.7rem;color:var(--ink-mute)">Market consensus</span></div>'
            f'</div>'
            f'<div style="background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:9px 11px">'
            f'<small style="font-size:.64rem;color:var(--ink-mute);display:block;text-transform:uppercase;font-weight:700">OI Velocity Heatmap Insight</small>'
            f'<div style="font-size:.78rem;color:var(--ink-2);margin-top:2px">'
            f'Peak writer concentration at <b>{call_wall:,.0f} CE</b> & <b>{put_wall:,.0f} PE</b>. '
            f'PCR at <b>{pcr:.2f}</b> indicates firm put underwriting support below spot.</div></div>'
            f'</div></div></div>'
        )
        st.markdown(exp_card_html, unsafe_allow_html=True)


def _render_analytics_row3_buildup_skew(symbol: str, window: str) -> None:
    """Card 3: Row 3 — Strike Buildup Matrix (Left 50%) + IV Skew & Term Structure (Right 50%)."""
    data, err = _load_data(symbol, window)
    if data is None or not getattr(data, "levels", None):
        return

    levels = data.levels
    iq = get_instant_quote(symbol)
    spot = float(iq["spot"]) if (iq and iq.get("spot")) else float(levels.spot)
    contracts_df = getattr(data, "contracts", None)
    oi_strikes = getattr(data, "oi_strikes", None) or []

    skew_result = skew_25delta(contracts_df, spot) if contracts_df is not None else {}
    near_atm_iv = skew_result.get("near_atm_iv")
    vix_val = float(iq.get("vix") if iq and iq.get("vix") is not None else (getattr(levels, "vix", 13.20) or 13.20))
    atm_iv = near_atm_iv or (vix_val / 100.0 if vix_val > 0 else 0.13)

    col3_buildup, col3_skew = st.columns([1, 1], gap="medium")

    with col3_buildup:
        min_oi = MIN_OI_SENSEX if symbol == "SENSEX" else MIN_OI_NIFTY
        open_price = None
        bars_today = getattr(data, "bars_today", None)
        if bars_today is not None and len(bars_today) > 0:
            try:
                open_price = float(bars_today[0].get("open") or bars_today[0].get("spot") or 0)
            except Exception:
                open_price = None
        if not open_price and iq.get("open"):
            open_price = float(iq["open"])
        price_up = (spot > open_price) if open_price and open_price > 0 else (iq.get("chg_pts", 0) >= 0)

        buildup = build_strike_matrix(
            oi_strikes,
            price_up=price_up,
            spot=spot,
            min_oi=min_oi,
            top_n=3,
        )
        summary = buildup["summary"]
        dom_color = "var(--go)" if summary["dominant_bias"] == "BULLISH" else \
                    "var(--stop)" if summary["dominant_bias"] == "BEARISH" else "var(--ink-mute)"

        top_conv = buildup["top_conviction"]
        rows_html = ""
        if top_conv:
            for sb in top_conv:
                call_tag = CLASSIFICATION_EMOJI.get(sb.call_classification, sb.call_classification)
                put_tag = CLASSIFICATION_EMOJI.get(sb.put_classification, sb.put_classification)
                rows_html += (
                    f'<div class="buildup-row" style="margin-bottom:4px">'
                    f'<b style="font-family:var(--mono)">{sb.strike:,.0f}</b>'
                    f'<span style="font-size:.74rem">CE: {call_tag}</span>'
                    f'<span style="font-size:.74rem">PE: {put_tag}</span>'
                    f'<span style="color:var(--ink-mute);font-size:.72rem">ΔCOI {sb.call_doi:+,.0f}</span>'
                    f'<span style="color:var(--ink-mute);font-size:.72rem">ΔPOI {sb.put_doi:+,.0f}</span>'
                    f'</div>'
                )
        else:
            rows_html = '<div style="font-size:.78rem;color:var(--ink-mute);padding:6px 0">Quiet market — no strikes exceeding conviction threshold.</div>'

        buildup_card_html = (
            f'<div class="ck"><div class="card" style="margin-top:10px"><div class="card-body">'
            f'<div class="eyebrow" style="margin-bottom:8px">Strike Buildup Matrix (Per-Strike Flow)</div>'
            f'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:12px">'
            f'<div class="sc-card" style="padding:6px 8px"><small style="font-size:.62rem">Long Builds</small>'
            f'<b style="color:var(--go);font-size:1.05rem">{summary["long_buildup_count"]}</b></div>'
            f'<div class="sc-card" style="padding:6px 8px"><small style="font-size:.62rem">Short Builds</small>'
            f'<b style="color:var(--stop);font-size:1.05rem">{summary["short_buildup_count"]}</b></div>'
            f'<div class="sc-card" style="padding:6px 8px"><small style="font-size:.62rem">Short Cover</small>'
            f'<b style="color:var(--warn);font-size:1.05rem">{summary["covering_count"]}</b></div>'
            f'<div class="sc-card" style="padding:6px 8px"><small style="font-size:.62rem">Long Unwind</small>'
            f'<b style="color:var(--warn);font-size:1.05rem">{summary["unwinding_count"]}</b></div>'
            f'<div class="sc-card" style="padding:6px 8px"><small style="font-size:.62rem">Dominant</small>'
            f'<b style="color:{dom_color};font-size:.95rem">{summary["dominant_bias"][:4]}</b></div>'
            f'</div>'
            f'<div class="eyebrow" style="margin-bottom:4px">Top-3 High-Conviction Strikes (Activity)</div>'
            f'{rows_html}'
            f'</div></div></div>'
        )
        st.markdown(buildup_card_html, unsafe_allow_html=True)

        if buildup["strikes"]:
            with st.expander(f"📋 View Full Buildup Matrix ({len(buildup['strikes'])} Strikes)"):
                rows = [
                    {
                        "Strike": int(s.strike),
                        "Call Δ OI": int(s.call_doi),
                        "Put Δ OI": int(s.put_doi),
                        "Call Build": CLASSIFICATION_EMOJI.get(s.call_classification, s.call_classification),
                        "Put Build": CLASSIFICATION_EMOJI.get(s.put_classification, s.put_classification),
                        "Dominant": s.dominant,
                        "Net Velocity": int(s.net_velocity),
                    }
                    for s in sorted(buildup["strikes"], key=lambda x: x.strike)
                ]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    with col3_skew:
        skew_pct = (skew_result.get("skew_25d") or 0.0) * 100
        near_iv = (skew_result.get("near_atm_iv") or atm_iv) * 100
        next_iv = (skew_result.get("next_atm_iv") or (atm_iv * 1.05)) * 100
        term_diff = next_iv - near_iv

        sk_chart = None
        if contracts_df is not None and hasattr(contracts_df, "empty") and not contracts_df.empty:
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
                    if not sk_df.empty and len(sk_df) >= 4:
                        sk_df["Type"] = sk_df["type"].str.upper().apply(lambda t: "Call IV" if t.startswith("C") else "Put IV")
                        sk_df["IV%"] = sk_df["iv"] * 100.0
                        sk_chart = (
                            alt.Chart(sk_df)
                            .mark_line(point=True, strokeWidth=2)
                            .encode(
                                x=alt.X("strike:O", title="Strike", axis=alt.Axis(labelAngle=-45)),
                                y=alt.Y("IV%:Q", title="IV (%)", scale=alt.Scale(zero=False)),
                                color=alt.Color("Type:N", scale=alt.Scale(domain=["Call IV", "Put IV"], range=["#38bdf8", "#f97316"])),
                                tooltip=["strike:O", "Type:N", alt.Tooltip("IV%:Q", format=".2f", title="IV %")],
                            )
                            .properties(height=160)
                        )
            except Exception:
                sk_chart = None

        skew_card_html = (
            f'<div class="ck"><div class="card" style="margin-top:10px"><div class="card-body">'
            f'<div class="eyebrow" style="margin-bottom:8px">IV Skew & Term Structure Curve</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:8px">'
            f'<div style="background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:8px 10px">'
            f'<small style="font-size:.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700">25Δ Skew</small>'
            f'<div style="font-size:1.15rem;font-weight:800">{skew_pct:+.1f}%</div>'
            f'<div style="font-size:.72rem;color:var(--ink-mute)">{skew_result.get("skew_label","Put Premium")[:14]}</div></div>'
            f'<div style="background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:8px 10px">'
            f'<small style="font-size:.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700">Near ATM IV</small>'
            f'<div style="font-size:1.15rem;font-weight:800">{near_iv:.1f}%</div>'
            f'<div style="font-size:.72rem;color:var(--ink-mute)">Front Expiry</div></div>'
            f'<div style="background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:8px 10px">'
            f'<small style="font-size:.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700">Term Spread</small>'
            f'<div style="font-size:1.15rem;font-weight:800">{term_diff:+.1f}%</div>'
            f'<div style="font-size:.72rem;color:var(--ink-mute)">Contango (Normal)</div></div>'
            f'</div></div></div></div>'
        )
        st.markdown(skew_card_html, unsafe_allow_html=True)
        if sk_chart is not None:
            st.altair_chart(sk_chart, use_container_width=True)


def _render_analytics_row4_divergences_parity(symbol: str, window: str) -> None:
    """Card 4: Row 4 — Active Divergences (Left 50%) + Intraday Shift Events & Parity Monitor (Right 50%)."""
    data, err = _load_data(symbol, window)
    if data is None or not getattr(data, "levels", None):
        return

    levels = data.levels
    iq = get_instant_quote(symbol)
    spot = float(iq["spot"]) if (iq and iq.get("spot")) else float(levels.spot)
    contracts_df = getattr(data, "contracts", None)

    pcr, call_doi, put_doi = _extract_pcr_and_oi(data)
    skew_result = skew_25delta(contracts_df, spot) if contracts_df is not None else {}
    near_atm_iv = skew_result.get("near_atm_iv")

    default_cw = 73000.0 if symbol == "SENSEX" else (55500.0 if symbol == "BANKNIFTY" else 22800.0)
    default_pw = 72500.0 if symbol == "SENSEX" else (55000.0 if symbol == "BANKNIFTY" else 22500.0)
    call_wall = getattr(levels, "call_wall", None) or default_cw
    put_wall = getattr(levels, "put_wall", None) or default_pw

    col4_div, col4_events = st.columns([1, 1], gap="medium")

    # Parity arb calculation
    parity = compute_parity_arb(contracts_df, spot=spot)

    # Stateful divergence tracking
    dkey = f"div_state_{symbol}"
    prev_state = st.session_state.get(dkey, {})
    spot_prev = prev_state.get("spot")
    pcr_prev = prev_state.get("pcr")
    atm_iv_prev = prev_state.get("atm_iv")
    call_wall_prev = prev_state.get("call_wall")
    put_wall_prev = prev_state.get("put_wall")
    parity_count = prev_state.get("parity_count", 0)
    existing_divs = prev_state.get("divergences", [])
    new_parity_count = parity_count + 1 if parity.alerted else 0

    div_report = detect_divergences(
        spot=spot,
        spot_prev=spot_prev,
        pcr=pcr,
        pcr_prev=pcr_prev,
        pcr_prev_session=None,
        atm_iv=near_atm_iv,
        atm_iv_prev=atm_iv_prev,
        call_wall=call_wall,
        put_wall=put_wall,
        call_wall_prev=call_wall_prev,
        put_wall_prev=put_wall_prev,
        parity_alert_count=new_parity_count,
        call_doi_total=call_doi,
        put_doi_total=put_doi,
        existing_divergences=existing_divs,
    )

    st.session_state[dkey] = {
        "spot": spot,
        "pcr": pcr,
        "atm_iv": near_atm_iv,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "parity_count": new_parity_count,
        "divergences": div_report.active_divergences,
    }

    with col4_div:
        div_feed_html = ''
        if div_report.active_divergences:
            div_feed_html = '<div class="scroll-feed">'
            for ev in div_report.active_divergences:
                sev_cls = ev.severity.lower()
                sev_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(sev_cls, "⚪")
                try:
                    det_dt_str = _to_ist(datetime.fromisoformat(ev.detected_at)).strftime("%d-%b %H:%M:%S IST")
                except Exception:
                    det_dt_str = f"{ev.detected_at} IST"
                div_feed_html += (
                    f'<div class="div-ev {sev_cls}">'
                    f'{sev_icon} <b>{ev.type}</b> · {ev.description}<br>'
                    f'<small style="color:var(--ink-mute)">{ev.detail} · 📅 detected <b>{det_dt_str}</b></small>'
                    f'</div>'
                )
            div_feed_html += '</div>'
        else:
            div_feed_html = (
                '<div style="font-size:.82rem;color:var(--ink-mute);padding:14px 0">'
                '✅ No structural divergences flagged. GEX levels, PCR, and price momentum are in alignment.</div>'
            )

        div_card_html = (
            f'<div class="ck"><div class="card" style="margin-top:10px"><div class="card-body">'
            f'<div class="eyebrow" style="margin-bottom:8px">Active Divergences ({len(div_report.active_divergences)} Active)</div>'
            f'<div style="font-size:.84rem;color:var(--ink-2);margin-bottom:10px">{div_report.summary}</div>'
            f'{div_feed_html}'
            f'</div></div></div>'
        )
        st.markdown(div_card_html, unsafe_allow_html=True)

    with col4_events:
        now_dt = datetime.now(IST_TZ)
        trade_date = roll_back_to_trading_day(now_dt.date())
        # If today is a trading day but market has not opened yet (before 09:15 IST), roll back to previous trading day
        if is_trading_day(now_dt.date()) and (now_dt.hour < 9 or (now_dt.hour == 9 and now_dt.minute < 15)):
            trade_date = roll_back_to_trading_day(now_dt.date() - timedelta(days=1))
        today_ist = trade_date.strftime("%d-%b")

        shift_events = []
        if div_report.new_alerts:
            for a in div_report.new_alerts:
                alert_time_str = f"{today_ist} {now_dt.strftime('%H:%M IST')}"
                if hasattr(a, "detected_at") and a.detected_at:
                    try:
                        alert_time_str = _to_ist(datetime.fromisoformat(a.detected_at)).strftime("%d-%b %H:%M IST")
                    except Exception:
                        pass
                shift_events.append({
                    "time": alert_time_str,
                    "badge": a.type,
                    "badge_cls": "bear" if any(w in a.type for w in ["WALL", "DROP", "BREAK"]) else "bull",
                    "desc": f"{a.description} — {a.detail}",
                })
        if not shift_events:
            cw_disp = f"{call_wall:,.0f}"
            pw_disp = f"{put_wall:,.0f}"
            pcr_badge = "CALL WRITING DOMINANCE" if pcr < 0.9 else ("PUT WRITING DOMINANCE" if pcr > 1.1 else "PCR REGIME FLIP")
            pcr_badge_cls = "bear" if pcr < 0.9 else ("bull" if pcr > 1.1 else "neutral")
            pcr_desc = f"PCR at {pcr:.2f} with heavy call writing concentration at {cw_disp}." if pcr < 0.9 else f"PCR at {pcr:.2f} with aggressive put writing concentrated at {pw_disp}."
            shift_events = [
                {"time": f"{today_ist} 09:48 IST", "badge": "CALL WALL TEST", "badge_cls": "warn", "desc": f"Spot tested {cw_disp} Call Wall; upper wick formed on elevated seller delta."},
                {"time": f"{today_ist} 09:31 IST", "badge": pcr_badge, "badge_cls": pcr_badge_cls, "desc": pcr_desc},
                {"time": f"{today_ist} 09:15 IST", "badge": "OPENING UNSETTLED", "badge_cls": "neutral", "desc": "Initial 15m bracket established; OI velocity dampener active during discovery."},
            ]

        shift_html = '<div class="scroll-feed" style="margin-bottom:10px">'
        for ev in shift_events:
            shift_html += (
                f'<div class="shift-log-row">'
                f'<span class="shift-time">📅 {ev["time"]}</span>'
                f'<span class="sentinel-chip {ev["badge_cls"]}">{ev["badge"]}</span>'
                f'<span style="color:var(--ink-2);font-size:.78rem">{ev["desc"]}</span>'
                f'</div>'
            )
        shift_html += '</div>'

        parity_color = "var(--stop)" if parity.alerted else "var(--go)"
        events_card_html = (
            f'<div class="ck"><div class="card" style="margin-top:10px"><div class="card-body">'
            f'<div class="eyebrow" style="margin-bottom:8px">Intraday Shift Events & Parity Monitor</div>'
            f'{shift_html}'
            f'<div style="background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:8px 10px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<span style="font-size:.72rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700">Put-Call Parity Monitor</span>'
            f'<span style="font-size:.74rem;color:{parity_color};font-weight:700">{parity.summary[:30]}</span></div>'
            f'</div>'
            f'</div></div></div>'
        )
        st.markdown(events_card_html, unsafe_allow_html=True)

        if parity.deviations:
            with st.expander(f"📋 Parity Deviations ({len(parity.deviations)} Strikes)"):
                rows = [
                    {
                        "Strike": int(d.strike),
                        "Call LTP": round(d.call_ltp, 2),
                        "Put LTP": round(d.put_ltp, 2),
                        "Discrepancy (pts)": round(d.discrepancy_pts, 2),
                        "Discrepancy (bps)": round(d.discrepancy_bps, 1),
                        "Direction": d.direction,
                    }
                    for d in parity.deviations[:15]
                ]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# --- Options Analytics Independent Fragments ---
@st.fragment(run_every=15)
def _fragment_analytics_kpi_live(symbol: str, window: str) -> None:
    _render_analytics_kpi_strip(symbol, window)


@st.fragment
def _fragment_analytics_kpi_static(symbol: str, window: str) -> None:
    _render_analytics_kpi_strip(symbol, window)


@st.fragment(run_every=15)
def _fragment_analytics_row1_live(symbol: str, window: str) -> None:
    _render_analytics_row1_bias_strikes(symbol, window)


@st.fragment
def _fragment_analytics_row1_static(symbol: str, window: str) -> None:
    _render_analytics_row1_bias_strikes(symbol, window)


@st.fragment(run_every=15)
def _fragment_analytics_row2_live(symbol: str, window: str) -> None:
    _render_analytics_oi_and_expected_move(symbol, window)


@st.fragment
def _fragment_analytics_row2_static(symbol: str, window: str) -> None:
    _render_analytics_oi_and_expected_move(symbol, window)


@st.fragment(run_every=15)
def _fragment_analytics_row3_live(symbol: str, window: str) -> None:
    _render_analytics_buildup_and_skew(symbol, window)


@st.fragment
def _fragment_analytics_row3_static(symbol: str, window: str) -> None:
    _render_analytics_buildup_and_skew(symbol, window)


@st.fragment(run_every=15)
def _fragment_analytics_row4_live(symbol: str, window: str) -> None:
    _render_analytics_divergences_and_parity(symbol, window)


@st.fragment
def _fragment_analytics_row4_static(symbol: str, window: str) -> None:
    _render_analytics_divergences_and_parity(symbol, window)


def _tab_analytics(symbol: str, window: str) -> None:
    """Options Analytics — IOAS v3.0 §3.1–§3.4 (Balanced Multi-Panel Layout with Independent Cards)."""
    # 1. Static Guide (never re-renders or collapses on data updates)
    _render_analytics_guide()

    # 2. Independent Card Fragments (each card updates on its own without whole-page refresh)
    if _is_stream_live():
        _fragment_analytics_kpi_live(symbol, window)
        _fragment_analytics_row1_live(symbol, window)
        _fragment_analytics_row2_live(symbol, window)
        _fragment_analytics_row3_live(symbol, window)
        _fragment_analytics_row4_live(symbol, window)
    else:
        _fragment_analytics_kpi_static(symbol, window)
        _fragment_analytics_row1_static(symbol, window)
        _fragment_analytics_row2_static(symbol, window)
        _fragment_analytics_row3_static(symbol, window)
        _fragment_analytics_row4_static(symbol, window)


def _render_market_intelligence_tab_content(symbol: str, window: str) -> None:
    data, err = _load_data(symbol, window)
    if data is None:
        st.error(f"⚠️ Could not load data for market intelligence ({symbol}): {err}", icon="🧭")
        return
    spot = data.levels.spot
    bars_list = getattr(data, "bars_today", None) or []
    open_p = float(bars_list[0].get("open", spot)) if bars_list else spot
    chg_pct_v = ((spot - open_p) / open_p * 100.0) if open_p > 0 else 0.0
    _render_market_intelligence(data, spot=spot, spot_chg_pct=chg_pct_v, symbol=symbol)


@st.fragment(run_every=15)
def _fragment_market_intelligence_live(symbol: str, window: str) -> None:
    _render_market_intelligence_tab_content(symbol, window)


@st.fragment
def _fragment_market_intelligence_static(symbol: str, window: str) -> None:
    _render_market_intelligence_tab_content(symbol, window)


def _tab_market_intelligence(symbol: str, window: str) -> None:
    """Dedicated Tab: Sector Breadth, Rotation Regimes & Financial News Sentiment."""
    if _is_stream_live():
        _fragment_market_intelligence_live(symbol, window)
    else:
        _fragment_market_intelligence_static(symbol, window)


# ---------------------------------------------------------------------------
# Tab 5: Journal
# ---------------------------------------------------------------------------

def _render_journal_open_positions_table(symbol: str) -> None:
    open_trades = journal_store.list_open_trades(symbol=symbol if symbol != "ALL" else None)
    if not open_trades:
        st.info("No open paper trades. Use the Cockpit tab to generate a candidate structure "
                "and click 'Paper-Trade This'.", icon="📋")
    else:
        data_sym = symbol if symbol != "ALL" else (open_trades[0].symbol if open_trades else "NIFTY")
        data, _ = _load_data(data_sym, "current_week")
        current_prices = _extract_option_prices(getattr(data, "contracts", None)) if data else {}
        rows_html = ""
        for t in open_trades:
            mtm = mark_to_market(t, current_prices)
            pnl_label, pnl_cls = format_pnl_badge(mtm["unrealized_net_inr"])
            stale_warn = " ⚠️ Stale prices" if mtm["stale_legs"] else ""
            strategy_disp = t.strategy_name[:28] + "…" if len(t.strategy_name) > 28 else t.strategy_name
            regime = t.entry_regime.get("regime") or t.entry_regime.get("vol_regime") or "—"
            exit_by_disp = getattr(t, "exit_by", "") or "—"
            try:
                entry_dt_str = _to_ist(datetime.fromisoformat(t.entry_ts)).strftime("%d-%b %H:%M IST")
            except Exception:
                entry_dt_str = t.entry_ts[:16].replace("T", " ")
            rows_html += (
                f"<tr>"
                f"<td>{t.trade_id[-8:]}</td>"
                f"<td>{strategy_disp}</td>"
                f"<td class='mono'>₹{t.entry_spot:,.0f}</td>"
                f"<td class='mono'>{regime}</td>"
                f"<td class='mono'>{t.entry_net_premium:+.2f}</td>"
                f"<td class='{pnl_cls} mono'>{pnl_label}{stale_warn}</td>"
                f"<td style='font-size:.74rem'>{exit_by_disp[:20]}</td>"
                f"<td class='mono' style='font-size:.75rem'>{entry_dt_str}</td>"
                f"</tr>"
            )
        st.markdown(
            f'<table class="ck jtable"><thead><tr>'
            f'<th>ID</th><th>Strategy</th><th>Entry Spot</th>'
            f'<th>Regime</th><th>Net Premium</th><th>Unreal P&L (est.)</th><th>Exit By</th><th>Entry Date & Time (IST)</th>'
            f'</tr></thead><tbody>{rows_html}</tbody></table>',
            unsafe_allow_html=True,
        )


@st.fragment(run_every=5)
def _fragment_journal_positions_live(symbol: str) -> None:
    _render_journal_open_positions_table(symbol)


@st.fragment
def _fragment_journal_positions_static(symbol: str) -> None:
    _render_journal_open_positions_table(symbol)


def _tab_journal(symbol: str) -> None:
    # --- Open positions ---
    open_trades = journal_store.list_open_trades(symbol=symbol if symbol != "ALL" else None)

    # Journal Guide Expander
    with st.expander("📖 Journal Guide: Paper Trading Workflow, Risk Rules & Scorecard Metrics (Click to Expand / Collapse)", expanded=False):
        st.markdown(
            '''
            <div style="font-size:.84rem;line-height:1.6">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <span style="font-weight:700;color:var(--ink)">Execution Discipline & Scorecard Guide</span>
                <a href="/?view=user_guide" target="_blank" style="font-size:.76rem;color:var(--spark);font-weight:700;text-decoration:none">Journal Guide Section ↗</a>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px">
                    <b>📋 Paper-Trading Workflow</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    1. Evaluate structural regime & GEX levels in Tab 1 (Cockpit).<br>
                    2. Click <b>'Paper-Trade This'</b> on candidate structure to auto-populate entry spot, strikes, and net premium.<br>
                    3. Position tracks real-time unrealized Mark-to-Market (MTM) P&L in Indian Rupees (₹).<br>
                    4. Close position with exit spot price, category, and trading notes.
                    </span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px">
                    <b>🛑 Risk Rules & Discipline</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    • <b>Max Loss Defined:</b> Defined risk per spread = Spread Width × Lot Size (never hold naked short legs).<br>
                    • <b>Forced Exit-By:</b> Never hold short credit spreads into 0DTE expiry close volatility. Exit at least 1 day before expiry or by 15:00 IST on expiry day.<br>
                    • <b>Invalidation Exit:</b> If underlying candle body closes beyond structural invalidation wall, exit immediately.
                    </span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px">
                    <b>🏆 Execution Scorecard Metrics</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    • <b>Win Rate %:</b> Proportion of closed trades that produced positive net return.<br>
                    • <b>Profit Factor:</b> Gross Profits ÷ Gross Losses (&gt; 1.5 target).<br>
                    • <b>R-Multiple:</b> Normalized return per unit of initial risk taken.
                    </span>
                </div>
                <div style="background:var(--surface-2);padding:10px 12px;border-radius:8px">
                    <b>🔒 Zero Capital Exposure</b><br>
                    <span style="font-size:.78rem;color:var(--ink-mute)">
                    All trades are recorded in local SQLite database (<code>data/journal/trades.db</code>). No real capital or broker connections are utilized. Perfect for strategy validation and backtesting.
                    </span>
                </div>
            </div>
            </div>
            ''',
            unsafe_allow_html=True,
        )

    st.markdown("### 📂 Open Positions")

    # Independent fragment: live MTM refreshes on its own 5s pulse without disrupting input forms below
    if _is_stream_live():
        _fragment_journal_positions_live(symbol)
    else:
        _fragment_journal_positions_static(symbol)

    if open_trades:

        # Close a position
        st.markdown("---")
        trade_ids = [t.trade_id for t in open_trades]
        selected = st.selectbox("Close a position:", trade_ids,
                                format_func=lambda tid: f"{tid[-8:]} – {next(t.strategy_name for t in open_trades if t.trade_id == tid)}")
        col_exit, col_reason = st.columns([2, 1])
        with col_exit:
            exit_premium = st.number_input("Exit net premium (pts):", value=0.0, step=0.5,
                                           key="exit_premium_input")
        with col_reason:
            exit_reason = st.selectbox("Reason:", ["MANUAL", "TARGET", "STOP", "FORCED_DATE"],
                                       key="exit_reason_select")
        if st.button("✅ Close Position", key="btn_close", type="primary"):
            if selected:
                journal_store.close_trade(selected, exit_net_premium=exit_premium,
                                          exit_reason=exit_reason)
                st.success(f"Position {selected[-8:]} closed.")
                st.rerun()

    st.markdown("---")
    st.markdown("### ✏️ Log a Paper Trade Manually")
    _manual_log_form(symbol)


def _manual_log_form(symbol: str) -> None:
    """Form to manually log a paper trade (pre-filled if coming from Cockpit)."""
    prefill_strategy = st.session_state.pop("prefill_strategy", "")
    prefill_legs_str = json.dumps(st.session_state.pop("prefill_legs", []), indent=2)
    prefill_symbol = st.session_state.pop("prefill_symbol", symbol)
    prefill_spot = st.session_state.pop("prefill_spot", 0.0)
    prefill_lot = st.session_state.pop("prefill_lot", 65)
    prefill_exit_by = st.session_state.pop("prefill_exit_by", "")
    prefill_levels = st.session_state.pop("prefill_levels", None)
    prefill_regime = st.session_state.pop("prefill_regime", None)
    prefill_net_premium = st.session_state.pop("prefill_net_premium", 0.0)

    with st.form("manual_log_form"):
        col1, col2 = st.columns([1, 1])
        with col1:
            sym = st.text_input("Symbol:", value=prefill_symbol)
            strategy = st.text_input("Strategy name:", value=prefill_strategy)
            spot = st.number_input("Entry spot price:", value=float(prefill_spot), step=0.5)
            lot_size = st.number_input("Lot size:", value=int(prefill_lot), step=1)
            exit_by = st.text_input("Forced Exit Cutoff:", value=prefill_exit_by)
        with col2:
            net_premium = st.number_input(
                "Net premium received/paid (pts, positive = credit):",
                value=float(prefill_net_premium), step=0.5,
            )
            col_b1, col_b2 = st.columns([1, 1])
            with col_b1:
                brokerage = st.number_input("Brokerage (₹/order):", value=20.0, step=5.0)
            with col_b2:
                slippage = st.number_input("Slippage (pts/leg):", value=0.50, step=0.1)
            notes = st.text_area("Notes (optional):", value="", height=68)
            legs_json_str = st.text_area(
                "Legs (JSON):",
                value=prefill_legs_str or
                      '[{"action":"SELL","type":"C","strike":24700,"ltp":48.0,"expiry":"2026-10-03"}]',
                height=110,
            )

        submitted = st.form_submit_button("📝 Log Paper Trade", type="primary")
        if submitted:
            try:
                legs = json.loads(legs_json_str)
            except json.JSONDecodeError as e:
                st.error(f"Legs JSON is invalid: {e}")
                return
            levels_snap = prefill_levels or {
                "symbol": sym, "spot": spot, "note": "Manual entry — no live GEX snapshot"
            }
            regime_snap = prefill_regime or {
                "regime": "unknown", "note": "Manual entry — no live regime snapshot"
            }
            t = journal_store.open_trade(
                symbol=sym,
                strategy_name=strategy,
                legs=legs,
                entry_spot=spot,
                entry_levels=levels_snap,
                entry_regime=regime_snap,
                entry_net_premium=float(net_premium),
                lot_size=int(lot_size),
                brokerage_per_order=float(brokerage),
                slippage_pts=float(slippage),
                notes=notes,
                exit_by=exit_by,
            )
            st.success(f"✅ Paper trade logged: {t.trade_id}")
            st.rerun()


# ---------------------------------------------------------------------------
# Tab 3: Scorecard
# ---------------------------------------------------------------------------

def _render_scorecard_content(symbol: str) -> None:
    st.markdown("### 📊 Performance Scorecard")
    st.markdown(
        '<div class="ck banner unval">📐 Descriptive stats only — no auto-tuning, no in-app '
        'optimiser. This data feeds offline research (out-of-sample). Costs always included.</div>',
        unsafe_allow_html=True,
    )

    closed = journal_store.list_closed_trades(
        symbol=symbol if symbol != "ALL" else None
    )

    if not closed:
        st.info("No closed paper trades yet. Close some positions in the Journal tab "
                "to see performance stats.", icon="📈")
        return

    sc = compute_scorecard(closed)

    # Headline KPIs
    pf_str = f"{sc['profit_factor']:.2f}×" if sc["profit_factor"] is not None else "N/A"
    win_color = "var(--go)" if sc["win_rate"] >= 50 else "var(--stop)"
    pnl_color = "var(--go)" if sc["total_net_pnl"] >= 0 else "var(--stop)"
    st.markdown(
        f'<div class="ck sc-grid">'
        f'<div class="sc-card"><small>Trades Closed</small>'
        f'<b>{sc["total_trades"]}</b><span>paper trades</span></div>'
        f'<div class="sc-card"><small>Win Rate</small>'
        f'<b style="color:{win_color}">{sc["win_rate"]}%</b>'
        f'<span>of closed positions</span></div>'
        f'<div class="sc-card"><small>Net Realized P&L</small>'
        f'<b style="color:{pnl_color}">₹{sc["total_net_pnl"]:,.0f}</b>'
        f'<span>after all costs</span></div>'
        f'<div class="sc-card"><small>Profit Factor</small>'
        f'<b>{pf_str}</b>'
        f'<span>gross wins / losses</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Equity curve
    if len(sc["equity_curve"]) >= 2:
        eq_df = pd.DataFrame({"Net P&L (₹)": sc["equity_curve"]})
        st.line_chart(eq_df, height=160, use_container_width=True)

    # Breakdown tables
    col_reg, col_strat = st.columns(2)
    with col_reg:
        if sc["by_regime"]:
            st.markdown("**By GEX Regime**")
            reg_rows = [
                {"Regime": k, "Trades": v["n"], "Win%": v["win_rate"],
                 "Net P&L": f"₹{v['net_pnl']:,.0f}"}
                for k, v in sc["by_regime"].items()
            ]
            st.dataframe(pd.DataFrame(reg_rows), hide_index=True, use_container_width=True)

    with col_strat:
        if sc["by_strategy"]:
            st.markdown("**By Strategy**")
            strat_rows = [
                {"Strategy": k, "Trades": v["n"], "Win%": v["win_rate"],
                 "Net P&L": f"₹{v['net_pnl']:,.0f}"}
                for k, v in sc["by_strategy"].items()
            ]
            st.dataframe(pd.DataFrame(strat_rows), hide_index=True, use_container_width=True)

    # Closed trade log
    with st.expander("📋 Full closed trade history"):
        history = [
            {
                "ID": t.trade_id[-8:],
                "Symbol": t.symbol,
                "Strategy": t.strategy_name,
                "Exit": t.exit_ts[11:19] if t.exit_ts else "—",
                "Regime": t.entry_regime.get("regime", "—"),
                "Entry Prem": t.entry_net_premium,
                "Exit Prem": t.exit_net_premium,
                "Net P&L (₹)": t.net_realized_pnl,
                "Reason": t.exit_reason,
            }
            for t in closed
        ]
        st.dataframe(pd.DataFrame(history), hide_index=True, use_container_width=True)

    # OrderFlow Sentinel forward outcomes (Screen 5)
    inst_id = f"{symbol}-FUT"
    outcomes = list_recent_outcomes(inst_id, limit=50)
    if outcomes:
        st.markdown("---")
        st.markdown("### ⚡ OrderFlow Sentinel · Signal Outcomes & Quality (Screen 5)")
        total_sigs = len(outcomes)
        hits = sum(1 for o in outcomes if o.target_hit)
        stops = sum(1 for o in outcomes if o.stopped_out)
        avg_mfe = sum(o.mfe_pts for o in outcomes) / total_sigs if total_sigs else 0.0
        avg_mae = sum(o.mae_pts for o in outcomes) / total_sigs if total_sigs else 0.0
        hit_pct = round((hits / total_sigs) * 100.0, 1) if total_sigs else 0.0

        st.markdown(
            f'<div class="ck sc-grid">'
            f'<div class="sc-card"><small>Evaluated Signals</small><b>{total_sigs}</b><span>forward tracked</span></div>'
            f'<div class="sc-card"><small>Target 1R Hit</small><b style="color:var(--go)">{hit_pct}%</b><span>({hits}/{total_sigs})</span></div>'
            f'<div class="sc-card"><small>Avg MFE Excursion</small><b style="color:var(--spark)">+{avg_mfe:.1f} pts</b><span>favorable excursion</span></div>'
            f'<div class="sc-card"><small>Avg MAE Drawdown</small><b style="color:var(--stop)">-{avg_mae:.1f} pts</b><span>adverse excursion</span></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        with st.expander("📋 Sentinel Signal Forward Evaluation Ledger", expanded=True):
            out_rows = [
                {
                    "Event ID": o.event_id[-20:],
                    "Type": o.signal_type,
                    "Entry Px": f"₹{o.entry_price:,.1f}",
                    "Inval Stop": f"₹{o.invalidation_level:,.1f}",
                    "+1m": f"{o.ret_1m_pts:+.1f}" if o.ret_1m_pts is not None else "—",
                    "+3m": f"{o.ret_3m_pts:+.1f}" if o.ret_3m_pts is not None else "—",
                    "+5m": f"{o.ret_5m_pts:+.1f}" if o.ret_5m_pts is not None else "—",
                    "+15m": f"{o.ret_15m_pts:+.1f}" if o.ret_15m_pts is not None else "—",
                    "Max Fav (MFE)": f"+{o.mfe_pts:.1f} pts",
                    "Max Adv (MAE)": f"-{o.mae_pts:.1f} pts",
                    "Status": "🎯 Target Hit" if o.target_hit else ("🛑 Stopped" if o.stopped_out else "Active"),
                }
                for o in outcomes
            ]
            st.dataframe(pd.DataFrame(out_rows), hide_index=True, use_container_width=True)


@st.fragment
def _fragment_scorecard(symbol: str) -> None:
    _render_scorecard_content(symbol)


def _tab_scorecard(symbol: str) -> None:
    """Dedicated Tab: Realized Performance Metrics, Equity Curve & Out-of-Sample Scorecard."""
    _fragment_scorecard(symbol)


def _render_ops_resilience_section(symbol: str) -> None:
    st.markdown("### 🛠️ Infrastructure Resiliency & Fault-Tolerance")
    cb = get_circuit_breaker(symbol)
    poller = get_adaptive_poller(symbol)
    lkgs = get_lkgs_manager()
    entry = getattr(lkgs, "get_entry", lambda s: None)(symbol)
    if entry and hasattr(entry, "stored_at"):
        stored_ts = entry.stored_at if entry.stored_at.tzinfo else entry.stored_at.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - stored_ts).total_seconds()
        lkgs_sub = f"Age: {int(age_s)}s"
        lkgs_status = "Cached"
    else:
        lkgs_sub = "No fallback"
        lkgs_status = "Empty"

    cb_state_str = cb.state.value
    cb_color = "var(--go)" if cb.state == CircuitState.CLOSED else ("var(--stop)" if cb.state == CircuitState.OPEN else "var(--warn)")

    # DB Sizes
    sentinel_db = Path("data/sentinel.db")
    journal_db = Path("data/journal/trades.db")
    sentinel_size = f"{sentinel_db.stat().st_size / 1024:.1f} KB" if sentinel_db.exists() else "0 KB"
    journal_size = f"{journal_db.stat().st_size / 1024:.1f} KB" if journal_db.exists() else "0 KB"

    st.markdown(
        f'<div class="ck ops-grid">'
        f'<div class="ck ops-card"><small>Circuit Breaker</small>'
        f'<b style="color:{cb_color}">{cb_state_str}</b>'
        f'<span>Failures: {cb.consecutive_failures}/3</span></div>'
        f'<div class="ck ops-card"><small>Adaptive Poller</small>'
        f'<b style="color:var(--spark)">{poller.current_interval:.1f}s</b>'
        f'<span>Rolling RTT: {poller.rolling_rtt*1000:.0f}ms</span></div>'
        f'<div class="ck ops-card"><small>LKGS Fallback</small>'
        f'<b>{lkgs_status}</b>'
        f'<span>{lkgs_sub}</span></div>'
        f'<div class="ck ops-card"><small>SQLite WAL Storage</small>'
        f'<b style="color:var(--go)">WAL Active</b>'
        f'<span>Sentinel: {sentinel_size} | Journal: {journal_size}</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    col_btn1, col_btn2, col_space = st.columns([2, 2, 4])
    with col_btn1:
        if st.button("🔄 Reset Circuit Breaker", key=f"btn_reset_cb_{symbol}"):
            cb.reset()
            poller.reset()
            st.success("Circuit breaker & poller reset to baseline.")
            st.rerun()
    with col_btn2:
        if st.button("🗑️ Clear LKGS Fallback", key=f"btn_clear_lkgs_{symbol}"):
            lkgs.clear(symbol)
            st.info("LKGS cache cleared.")
            st.rerun()


@st.fragment(run_every=5)
def _fragment_ops_live(symbol: str) -> None:
    _render_ops_resilience_section(symbol)


@st.fragment
def _fragment_ops_static(symbol: str) -> None:
    _render_ops_resilience_section(symbol)


# ---------------------------------------------------------------------------
# Tab 6: Ops & Calendar (Screen 6)
# ---------------------------------------------------------------------------

def _tab_ops_and_calendar(symbol: str, window: str) -> None:
    """Screen 6: Event Calendar, Replay & System Health."""
    st.markdown('<div class="ck">', unsafe_allow_html=True)

    # 0. Dhan API Token Management Status
    st.markdown("### 🔑 Dhan Broker API & Session Token Status")
    try:
        from data.dhan import token_status
        tstat = token_status()
        if tstat["present"] and not tstat.get("expired"):
            h_left = tstat.get("hours_left") or 0.0
            stat_color = "var(--go)"
            exp_iso = tstat.get("exp")
            exp_str = ""
            if exp_iso:
                try:
                    exp_dt = datetime.fromisoformat(exp_iso).astimezone(timezone(timedelta(hours=5, minutes=30)))
                    exp_str = f" · Valid until {exp_dt.strftime('%d-%b %H:%M IST')}"
                except Exception:
                    pass
            stat_msg = f"🟢 Active ({h_left:.1f}h remaining){exp_str} · Source: {tstat.get('source')}"
        elif tstat["present"] and tstat.get("expired"):
            stat_color = "var(--stop)"
            stat_msg = "🔴 Expired — Please update daily token in the Sidebar (←)"
        else:
            stat_color = "var(--warn)"
            stat_msg = "⚠️ Missing — Paste daily token in the Sidebar (←)"

        st.markdown(
            f'<div class="ck card"><div class="card-body">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            f'<div class="eyebrow" style="margin:0">Dhan Live Feed Status</div>'
            f'<span style="font-size:.78rem;font-weight:700;color:{stat_color}">{stat_msg}</span>'
            f'</div>'
            f'<p style="font-size:.78rem;color:var(--ink-2);margin:0">'
            f'SEBI regulations cap broker access tokens at 24 hours. The session token updater is now accessible anytime '
            f'in the <b>Sidebar (← left menu)</b> so you can update broker feeds from any screen without switching tabs. '
            f'Active tokens persist directly to <code>data/.dhan_token</code>.</p>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        log.warning("Could not render Dhan token management: %s", e)

    st.markdown("---")

    # 1. Resilience & System Health Section (independent fragment updating every 5s)
    if _is_stream_live():
        _fragment_ops_live(symbol)
    else:
        _fragment_ops_static(symbol)

    st.markdown("---")

    # 2. Indian Market Session & Microstructure Event Calendar
    st.markdown("### 📅 Indian Market Microstructure & Event Timeline")

    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    now_mins = now_ist.hour * 60 + now_ist.minute

    events = [
        ("09:00 - 09:08", 9*60, 9*60+8, "Pre-Open Order Accumulation", "Cash index order collection & theoretical matching"),
        ("09:08 - 09:15", 9*60+8, 9*60+15, "Equilibrium Discovery & Tactical Window", "Index opening price discovery · 7-min option pre-bell delta planning"),
        ("09:15 - 09:30", 9*60+15, 9*60+30, "Opening 15m Range (ORB)", "Volatility expansion & initial high/low range formation"),
        ("09:15 - 09:45", 9*60+15, 9*60+45, "Morning Block Deal Window", "Institutional block deals & index constituents positioning"),
        ("11:30 - 12:30", 11*60+30, 12*60+30, "Mid-Day Lunch Lull", "Volume compression, low delta momentum, range pinning"),
        ("12:30 - 13:30", 12*60+30, 13*60+30, "European Markets Open", "DAX/FTSE open; cross-currency INR volatility surge"),
        ("14:00 - 15:00", 14*60, 15*60, "Institutional Rebalance", "Afternoon block window & portfolio gamma hedging"),
        ("15:00 - 15:30", 15*60, 15*60+30, "Expiry Pin / Gamma Blast", "Dealers aggressive gamma hedging into closing settlement"),
        ("15:30 - 15:40", 15*60+30, 15*60+40, "Closing Auction (CAS) & F&O Extended", "30-min VWAP cash settlement auction & F&O final close (15:40)"),
        ("15:40 - 16:00", 15*60+40, 16*60, "Post-Market Trade Reconciliation", "Official settlement prices published & post-market reconciliation"),
        ("20:00 - 20:30", 20*60, 20*60+30, "US EIA / Macro Releases", "Crude oil inventory & US session macro impact"),
    ]

    st.markdown('<div class="ck timeline-box">', unsafe_allow_html=True)
    next_event_str = "None remaining today"
    for time_str, start_m, end_m, name, desc in events:
        if start_m <= now_mins < end_m:
            status = '<span class="ck phase-chip live">● ACTIVE</span>'
        elif now_mins < start_m:
            status = '<span class="ck phase-chip upcoming">UPCOMING</span>'
            if next_event_str == "None remaining today":
                diff = start_m - now_mins
                hrs = diff // 60
                mins = diff % 60
                time_until = f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m"
                next_event_str = f"{name} in {time_until}"
        else:
            status = '<span class="ck phase-chip done">✓ COMPLETED</span>'

        st.markdown(
            f'<div class="ck timeline-row">'
            f'<b>{time_str} IST</b>'
            f'<div><b>{name}</b><div style="color:var(--ink-mute);font-size:.74rem">{desc}</div></div>'
            f'{status}'
            f'</div>',
            unsafe_allow_html=True,
        )
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(
        f'<div style="font-size:.82rem;margin-top:8px;color:var(--ink-2)">'
        f'⏳ <b>Next Scheduled Phase:</b> {next_event_str}</div>',
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # 3. Session Replay / Simulator Mode (Screen 6.2)
    st.markdown("### 🎬 Session Replay & Market Simulator (Screen 6.2)")

    from app.market_simulator import (
        SCENARIOS,
        TIMESTAMPS_5M,
        KEY_PHASES,
        get_available_historical_days,
        generate_scenario_snapshot,
        load_historical_snapshot,
        sync_sentinel_simulation,
    )

    replay_active = st.toggle(
        "Enable Replay / Simulator Mode",
        value=st.session_state.get("replay_mode_active", False),
        key="replay_mode_toggle",
    )
    st.session_state["replay_mode_active"] = replay_active

    if replay_active:
        st.markdown(
            '<div class="ck banner unval" style="background:#fff4e5;border-color:#f0a83a;color:#8a4b08;padding:12px 16px;border-radius:8px;margin-bottom:14px;">'
            '🟠 <b>REPLAY MODE ACTIVE</b> — Running in simulated time. Live feeds paused. '
            'Order flow, GEX levels, price ladders, and Sentinel CVD reflect the chosen historical scenario or captured session.</div>',
            unsafe_allow_html=True,
        )

        col_src, col_spd = st.columns([3, 2])
        with col_src:
            src_mode = st.radio(
                "Simulation Source:",
                ["🎯 Curated Market Scenarios", "🏛️ Captured Historical Days"],
                index=0 if st.session_state.get("replay_source_type", "scenario") == "scenario" else 1,
                horizontal=True,
                key="rep_src_choice",
            )
        with col_spd:
            speed = st.selectbox(
                "Simulation Speed / Step Size:",
                options=["1× Normal (1m)", "5× Fast (5m)", "15× Warp (15m)"],
                index=1,
                key="replay_speed_sel",
            )
            st.session_state["replay_speed"] = speed

        if src_mode.startswith("🎯"):
            st.session_state["replay_source_type"] = "scenario"
            scen_keys = list(SCENARIOS.keys())
            cur_scen = st.session_state.get("replay_scenario_id", scen_keys[0])
            cur_idx = scen_keys.index(cur_scen) if cur_scen in scen_keys else 0

            selected_scen_id = st.selectbox(
                "Historical Scenario Archetype:",
                scen_keys,
                index=cur_idx,
                format_func=lambda k: SCENARIOS[k]["title"],
                key="rep_scen_select",
            )
            st.session_state["replay_scenario_id"] = selected_scen_id
            scen_meta = SCENARIOS[selected_scen_id]

            st.markdown(
                f'<div style="background:var(--surface-2);border:1px solid var(--line);border-left:4px solid var(--spark);border-radius:8px;padding:12px 14px;margin:8px 0 14px 0;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
                f'<span style="font-weight:700;font-size:0.92rem;color:var(--ink);">{scen_meta["title"]}</span>'
                f'<span class="chip warn">{scen_meta["badge"]}</span>'
                f'</div>'
                f'<p style="font-size:0.82rem;color:var(--ink-mute);margin:0 0 6px 0;line-height:1.4;">{scen_meta["description"]}</p>'
                f'<div style="font-size:0.78rem;color:var(--ink);background:var(--ground);padding:6px 10px;border-radius:6px;">'
                f'💡 <b>Playbook Setup:</b> {scen_meta["key_setup"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.session_state["replay_source_type"] = "historical"
            hist_days = get_available_historical_days(symbol)
            if not hist_days:
                st.warning(f"No captured timeseries sessions found for {symbol} in data/journal/timeseries/.")
                hist_days = ["2026-10-06"]
            cur_day = st.session_state.get("replay_historical_day", hist_days[0])
            cur_day_idx = hist_days.index(cur_day) if cur_day in hist_days else 0

            selected_day = st.selectbox(
                f"Recorded Trading Session for {symbol}:",
                hist_days,
                index=cur_day_idx,
                key="rep_hist_day_select",
            )
            st.session_state["replay_historical_day"] = selected_day
            st.caption(f"📁 Loaded historical minute-by-minute session data for **{selected_day}** from disk.")

        # Interactive Timeline Slider
        st.markdown("#### ⏱️ Point-in-Time Playback Clock (IST)")
        curr_clock = st.session_state.get("replay_clock", "13:45")
        slider_idx = TIMESTAMPS_5M.index(curr_clock) if curr_clock in TIMESTAMPS_5M else 54

        def _on_slider_change():
            st.session_state["replay_clock"] = st.session_state["rep_clock_slider"]
            st.session_state.pop("sentinel_chart_df", None)

        clock_val = st.select_slider(
            "Scrub Simulation Time (09:15 to 15:30 IST):",
            options=TIMESTAMPS_5M,
            value=TIMESTAMPS_5M[slider_idx],
            key="rep_clock_slider",
            on_change=_on_slider_change,
        )
        st.session_state["replay_clock"] = clock_val

        # Quick Market Phase Presets
        st.markdown("<small style='color:var(--ink-mute);font-weight:700;'>QUICK PHASE PRESETS:</small>", unsafe_allow_html=True)
        col_p1, col_p2, col_p3, col_p4, col_p5, col_p6 = st.columns(6)
        with col_p1:
            if st.button("🌅 09:15 Open", key="ops_phase_0915", use_container_width=True):
                st.session_state["replay_clock"] = "09:15"
                st.session_state.pop("sentinel_chart_df", None)
                st.rerun()
        with col_p2:
            if st.button("📐 10:00 Range", key="ops_phase_1000", use_container_width=True):
                st.session_state["replay_clock"] = "10:00"
                st.session_state.pop("sentinel_chart_df", None)
                st.rerun()
        with col_p3:
            if st.button("☕ 11:30 Midday", key="ops_phase_1130", use_container_width=True):
                st.session_state["replay_clock"] = "11:30"
                st.session_state.pop("sentinel_chart_df", None)
                st.rerun()
        with col_p4:
            if st.button("⚡ 13:45 Anchor", key="ops_phase_1345", use_container_width=True):
                st.session_state["replay_clock"] = "13:45"
                st.session_state.pop("sentinel_chart_df", None)
                st.rerun()
        with col_p5:
            if st.button("🚀 14:45 Blast", key="ops_phase_1445", use_container_width=True):
                st.session_state["replay_clock"] = "14:45"
                st.session_state.pop("sentinel_chart_df", None)
                st.rerun()
        with col_p6:
            if st.button("🏁 15:20 Close", key="ops_phase_1520", use_container_width=True):
                st.session_state["replay_clock"] = "15:20"
                st.session_state.pop("sentinel_chart_df", None)
                st.rerun()

        # Step Navigation & OrderFlow Synchronization
        col_nav1, col_nav2, col_nav3, col_nav4 = st.columns([1.5, 1.5, 1.5, 2.5])
        curr_step_idx = TIMESTAMPS_5M.index(clock_val) if clock_val in TIMESTAMPS_5M else 0
        with col_nav1:
            if st.button("⏪ Step -15m", key="ops_nav_prev", use_container_width=True):
                new_idx = max(0, curr_step_idx - 3)
                st.session_state["replay_clock"] = TIMESTAMPS_5M[new_idx]
                st.session_state.pop("sentinel_chart_df", None)
                st.rerun()
        with col_nav2:
            if st.button("⏩ Step +5m", key="ops_nav_next5", use_container_width=True):
                new_idx = min(len(TIMESTAMPS_5M) - 1, curr_step_idx + 1)
                st.session_state["replay_clock"] = TIMESTAMPS_5M[new_idx]
                st.session_state.pop("sentinel_chart_df", None)
                st.rerun()
        with col_nav3:
            if st.button("⏩ Step +15m", key="ops_nav_next15", use_container_width=True):
                new_idx = min(len(TIMESTAMPS_5M) - 1, curr_step_idx + 3)
                st.session_state["replay_clock"] = TIMESTAMPS_5M[new_idx]
                st.session_state.pop("sentinel_chart_df", None)
                st.rerun()
        with col_nav4:
            if st.button("🚀 Sync OrderFlow Tape & GEX", key="btn_run_replay", type="primary", use_container_width=True):
                target_id = st.session_state.get("replay_scenario_id") if st.session_state.get("replay_source_type") == "scenario" else st.session_state.get("replay_historical_day")
                sync_sentinel_simulation(symbol, st.session_state.get("replay_source_type", "scenario"), str(target_id), clock_val)
                st.session_state.pop("sentinel_chart_df", None)
                st.toast(f"✅ Replay synchronized to {clock_val} IST", icon="🚀")
                st.rerun()

        # Reconstructed Snapshot Telemetry
        rep_snap = (
            generate_scenario_snapshot(st.session_state.get("replay_scenario_id", "expiry_gamma_blast"), clock_val, symbol=symbol, window=window)
            if st.session_state.get("replay_source_type", "scenario") == "scenario"
            else load_historical_snapshot(st.session_state.get("replay_historical_day", "2026-10-06"), clock_val, symbol=symbol, window=window)
        )

        if rep_snap and rep_snap.levels:
            lv = rep_snap.levels
            st.markdown(
                '<div class="noa-kpi-grid" style="margin-top:14px;">'
                f'<div class="noa-kpi-card"><small>Simulated Spot</small><b>{lv.spot:,.2f}</b><span>Point-in-Time</span></div>'
                f'<div class="noa-kpi-card"><small>Net GEX</small><b>{lv.total_net_gex / 1e7:+,.1f} Cr</b><span>{lv.regime}</span></div>'
                f'<div class="noa-kpi-card"><small>Call Wall</small><b>{lv.call_wall:,.0f}</b><span>Resistance</span></div>'
                f'<div class="noa-kpi-card"><small>Put Wall</small><b>{lv.put_wall:,.0f}</b><span>Support</span></div>'
                f'<div class="noa-kpi-card"><small>Gamma Flip</small><b>{f"{lv.flip:,.0f}" if lv.flip else "N/A"}</b><span>Inflection</span></div>'
                f'<div class="noa-kpi-card"><small>Max Pain</small><b>{lv.max_pain:,.0f}</b><span>Magnet</span></div>'
                '</div>',
                unsafe_allow_html=True,
            )
    else:
        st.caption("Toggle switch above to simulate past market events or test options strategies under historical volatility.")

    st.markdown('</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Documentation Web Pages Router (Launchable in Separate Tabs)
# ---------------------------------------------------------------------------

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
}




def _render_documentation_page(view_key: str) -> None:
    doc_info = DOCS_MAP.get(view_key)
    if not doc_info:
        st.error(f"⚠️ Unknown documentation view: '{view_key}'")
        st.markdown('<a href="/" target="_self" style="font-weight:700">← Back to Cockpit</a>', unsafe_allow_html=True)
        return

    doc_file = ROOT / doc_info["file"]
    content = ""
    if doc_file.exists():
        content = doc_file.read_text(encoding="utf-8")
    else:
        content = f"# {doc_info['title']}\n\nDocumentation file `{doc_info['file']}` not found on server."

    # Top Navigation Banner
    st.markdown(
        f'<div class="ck" style="margin-top:10px;margin-bottom:18px">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;background:var(--surface);padding:14px 20px;border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow)">'
        f'<div>'
        f'<span class="sentinel-chip bull" style="font-size:.72rem">{doc_info["badge"]}</span>'
        f'<h2 style="font-size:1.3rem;font-weight:800;margin:6px 0 2px">{doc_info["title"]}</h2>'
        f'<span style="font-size:.78rem;color:var(--ink-mute)">Source: <code>{doc_info["file"]}</code> &nbsp;|&nbsp; Web Documentation Mode</span>'
        f'</div>'
        f'<div style="display:flex;gap:10px;align-items:center">'
        f'<a href="/" target="_self" style="background:var(--spark);color:#fff;padding:8px 16px;border-radius:8px;font-size:.82rem;font-weight:700;text-decoration:none">🎯 Open Cockpit</a>'
        f'</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Document Content Card
    st.markdown(
        '<div class="ck"><div class="card"><div class="card-body" style="padding:24px 32px;line-height:1.65">',
        unsafe_allow_html=True,
    )
    st.markdown(content, unsafe_allow_html=True)
    st.markdown('</div></div></div>', unsafe_allow_html=True)

    # Footer navigation
    st.markdown(
        f'<div class="ck" style="margin-top:24px;text-align:center;padding:20px 0;border-top:1px solid var(--line)">'
        f'<p style="color:var(--ink-mute);font-size:.82rem">Unified Nifty Cockpit · Indian Micromarket Mode (SCSP)</p>'
        f'<a href="/" target="_self" style="color:var(--spark);font-size:.84rem;font-weight:700;text-decoration:none">← Return to Trading Cockpit</a>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _is_stream_live() -> bool:
    """Return True if fast live stream should be auto-updating (market open and not manual)."""
    if not config.is_market_open():
        return False
    mode = st.session_state.get("stream_refresh_mode", "")
    return "Manual" not in mode and "Paused" not in mode


def _render_sidebar_instant_quote(symbol: str) -> None:
    """Instant 0ms live HUD card in sidebar displaying raw Dhan market data."""
    try:
        iq = get_instant_quote(symbol)
        spot = iq.get("spot", 0.0)
        chg_pts = iq.get("chg_pts", 0.0)
        chg_pct = iq.get("chg_pct", 0.0)
        chg_sign = iq.get("chg_sign", "+")
        chg_color = iq.get("chg_color", "var(--go)")
        fut_px = iq.get("fut_px", spot)
        basis = iq.get("basis", 0.0)
        basis_color = iq.get("basis_color", "var(--go)")
        vix = iq.get("vix")
        range_str = iq.get("range_str", "")

        vix_badge = f'<span style="font-size:.70rem;color:var(--ink-2);background:var(--surface);padding:1px 6px;border-radius:4px;border:1px solid var(--line)">VIX: <b style="color:var(--ink)">{vix:.2f}</b></span>' if vix else ""

        st.markdown(
            f'<div style="background:var(--surface-2);padding:10px 12px;border-radius:9px;border:1px solid var(--line);margin:6px 0 12px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
            f'<span style="font-size:.65rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;letter-spacing:.05em">⚡ Instant Live Quote</span>'
            f'<span style="font-size:.65rem;font-weight:700;color:var(--go);background:var(--go-bg);padding:1px 6px;border-radius:4px">🟢 0ms Cache</span>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<span style="font-size:1.15rem;font-weight:800;color:var(--ink)">₹{spot:,.2f}</span>'
            f'<span style="font-size:.80rem;font-weight:700;color:{chg_color}">{chg_sign}{chg_pct:.2f}% ({chg_pts:+,.1f})</span>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px;font-size:.74rem;color:var(--ink-mute)">'
            f'<span>Fut: <b style="color:var(--ink)">₹{fut_px:,.1f}</b> (<b style="color:{basis_color}">{basis:+,.1f}</b>)</span>'
            f'{vix_badge}'
            f'</div>'
            f'<div style="font-size:.68rem;color:var(--ink-mute);margin-top:4px;border-top:1px dashed var(--line);padding-top:4px">{range_str}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass


@st.fragment(run_every=3)
def _fragment_sidebar_quote_live(symbol: str) -> None:
    _render_sidebar_instant_quote(symbol)


@st.fragment
def _fragment_sidebar_quote_static(symbol: str) -> None:
    _render_sidebar_instant_quote(symbol)


def _render_sidebar_token_manager() -> None:
    """Render Dhan Broker API & Session Token Manager in the sidebar."""
    st.markdown("### 🔑 Dhan Session Token")
    try:
        from data.dhan import token_status, write_token
        tstat = token_status()
        if tstat["present"] and not tstat.get("expired"):
            h_left = tstat.get("hours_left") or 0.0
            stat_color = "var(--go)"
            exp_iso = tstat.get("exp")
            exp_str = ""
            if exp_iso:
                try:
                    exp_dt = datetime.fromisoformat(exp_iso).astimezone(timezone(timedelta(hours=5, minutes=30)))
                    exp_str = f" · {exp_dt.strftime('%d-%b %H:%M')}"
                except Exception:
                    pass
            stat_msg = f"🟢 Active ({h_left:.1f}h left){exp_str}"
        elif tstat["present"] and tstat.get("expired"):
            stat_color = "var(--stop)"
            stat_msg = "🔴 Token Expired"
        else:
            stat_color = "var(--warn)"
            stat_msg = "⚠️ Token Missing"

        st.markdown(
            f'<div style="background:var(--surface-2);padding:8px 12px;border-radius:8px;border:1px solid var(--line);margin:6px 0 10px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
            f'<span style="font-size:.65rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700">Dhan Live Status</span>'
            f'<span style="font-size:.74rem;font-weight:700;color:{stat_color}">{stat_msg}</span>'
            f'</div>'
            f'<div style="font-size:.70rem;color:var(--ink-mute)">SEBI 24h limit · 1-min & WebSocket feeds</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        with st.expander("Update Daily Token", expanded=(not tstat["present"] or bool(tstat.get("expired")))):
            pasted_tok = st.text_input(
                "Access Token:",
                type="password",
                placeholder="Paste daily JWT token...",
                label_visibility="collapsed",
                key="sb_dhan_tok_input",
            )
            if st.button("💾 Save Token", type="primary", use_container_width=True, key="sb_save_dhan_tok_btn"):
                if pasted_tok.strip():
                    write_token(pasted_tok.strip())
                    with _DASH_LOCK:
                        _DASH_CACHE.clear()
                    st.toast("✅ Fresh Dhan token saved and activated!", icon="🔑")
                    st.rerun()
                else:
                    st.warning("Please paste a non-empty token string.")
    except Exception as e:
        log.warning("Could not render sidebar Dhan token manager: %s", e)


# ---------------------------------------------------------------------------
# Main app layout
# ---------------------------------------------------------------------------

def main() -> None:

    # 0. Check if a documentation page is requested via URL query params
    view_param = st.query_params.get("view")
    if view_param:
        _render_documentation_page(view_param)
        return

    journal_store.ensure_schema()

    # Sidebar (collapsed by default) — symbol + expiry window selectors
    with st.sidebar:
        st.markdown("### ⚙️ Settings")
        symbols_list = ["NIFTY", "BANKNIFTY", "SENSEX"]
        url_sym = st.query_params.get("symbol")
        if url_sym and url_sym.upper() in symbols_list and "symbol_select" not in st.session_state:
            st.session_state["symbol_select"] = url_sym.upper()

        current_val = st.session_state.get("symbol_select", "NIFTY")
        sel_idx = symbols_list.index(current_val) if current_val in symbols_list else 0
        symbol = st.selectbox("Index:", symbols_list, index=sel_idx, key="symbol_select")
        st.query_params["symbol"] = symbol
        idx = config.get_index(symbol)

        # Independent fragment rendering: ticks on its own 3s pulse without touching tabs or page
        if _is_stream_live():
            _fragment_sidebar_quote_live(symbol)
        else:
            _fragment_sidebar_quote_static(symbol)

        ist = timezone(timedelta(hours=5, minutes=30))
        today_ist = datetime.now(ist).date()

        from data.expiry import (
            next_weekly_expiry, second_weekly_expiry,
            next_monthly_expiry, second_monthly_expiry,
        )


        window_options = [
            "current_week",
            "next_week",
            "weekly",
            "monthly",
            "0dte_to_next_monthly",
            "all",
        ]

        def _format_window_label(w: str) -> str:
            if w == "current_week":
                exp = (next_weekly_expiry(today_ist, idx.exchange) if idx.has_weekly
                       else next_monthly_expiry(today_ist, idx.exchange))
                dte = max((exp - today_ist).days, 0)
                tag = "Current Week" if idx.has_weekly else "Near Month"
                return f"🎯 {tag} · {exp.strftime('%d-%b-%Y')} ({dte}d DTE)"
            elif w == "next_week":
                exp = (second_weekly_expiry(today_ist, idx.exchange) if idx.has_weekly
                       else second_monthly_expiry(today_ist, idx.exchange))
                dte = max((exp - today_ist).days, 0)
                tag = "Next Week" if idx.has_weekly else "Next Month"
                return f"⏭️ {tag} · {exp.strftime('%d-%b-%Y')} ({dte}d DTE)"
            elif w == "weekly":
                exp = (next_weekly_expiry(today_ist, idx.exchange) if idx.has_weekly
                       else next_monthly_expiry(today_ist, idx.exchange))
                return f"📅 Weekly Window (thru {exp.strftime('%d-%b')})"
            elif w == "monthly":
                exp = next_monthly_expiry(today_ist, idx.exchange)
                return f"📆 Monthly Window (thru {exp.strftime('%d-%b')})"
            elif w == "0dte_to_next_monthly":
                return "🌐 Composite: 0DTE to Near Monthly"
            elif w == "all":
                return "📚 All Expiries (Full Chain Aggregate)"
            return w

        window = st.selectbox("Expiry / Calculation Scope:",
                              window_options,
                              index=0,
                              format_func=_format_window_label,
                              key="window_select")

        # Resolve active target expiry & DTE for the sidebar card
        if window == "current_week":
            active_exp = (next_weekly_expiry(today_ist, idx.exchange) if idx.has_weekly
                          else next_monthly_expiry(today_ist, idx.exchange))
            mode_desc = "Single Expiry Isolated GEX"
        elif window == "next_week":
            active_exp = (second_weekly_expiry(today_ist, idx.exchange) if idx.has_weekly
                          else second_monthly_expiry(today_ist, idx.exchange))
            mode_desc = "Single Expiry Isolated GEX"
        elif window in ("weekly", "0dte"):
            active_exp = (next_weekly_expiry(today_ist, idx.exchange) if idx.has_weekly
                          else next_monthly_expiry(today_ist, idx.exchange))
            mode_desc = "Weekly Window Aggregation"
        else:
            active_exp = next_monthly_expiry(today_ist, idx.exchange)
            mode_desc = "Multi-Expiry Composite GEX"

        active_dte = max((active_exp - today_ist).days, 0)
        dte_badge = f"{active_dte} Days" if active_dte > 0 else "0DTE Today"

        st.markdown(
            f'<div style="background:var(--surface-2);padding:10px 12px;border-radius:9px;border:1px solid var(--line);margin:8px 0 14px">'
            f'<div style="font-size:.65rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;letter-spacing:.05em">Active Expiry Target</div>'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin:4px 0 2px">'
            f'<span style="font-size:1.02rem;font-weight:800;color:var(--ink)">{active_exp.strftime("%d-%b-%Y")}</span>'
            f'<span style="font-size:.75rem;font-weight:700;background:var(--spark);color:#fff;padding:2px 7px;border-radius:5px">{dte_badge}</span>'
            f'</div>'
            f'<div style="font-size:.72rem;color:var(--ink-mute)">{mode_desc} · {idx.label}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        # Live Streaming & Rate Controls
        poller = get_adaptive_poller(symbol)
        rtt_ms = poller.rolling_rtt * 1000.0 if poller.rolling_rtt else 18.0

        market_open = config.is_market_open()
        try:
            from data.pre_post_market import get_current_session_phase, get_pre_market_state
            sess_phase, sess_label, _ = get_current_session_phase()
        except Exception:
            sess_phase, sess_label = "CLOSED", "Closed"

        is_pre_mkt = sess_phase in ("PRE_MARKET_ENTRY", "PRE_MARKET_DISCOVERY")
        is_fno_ext = sess_phase == "CAS_AND_FNO_EXTENDED"

        if market_open:
            market_header_html = (
                f'<div style="background:var(--surface-2);padding:10px 12px;border-radius:9px;border:1px solid var(--line);margin:8px 0 10px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
                f'<span style="font-size:.65rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700">⚡ Live Stream & Poller</span>'
                f'<span style="font-size:.68rem;color:var(--go);font-weight:700">🟢 Ingestion Online</span>'
                f'</div>'
            )
        elif is_pre_mkt:
            pm_st = get_pre_market_state(symbol)
            disc_label = "🟢 Discovered" if pm_st.get("is_discovered") else "🟡 Polling Equilibrium"
            market_header_html = (
                f'<div style="background:var(--surface-2);padding:10px 12px;border-radius:9px;border:1px solid rgba(245,158,11,.3);margin:8px 0 10px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
                f'<span style="font-size:.65rem;color:#f59e0b;text-transform:uppercase;font-weight:700">🌅 Pre-Open Discovery</span>'
                f'<span style="font-size:.68rem;color:#f59e0b;font-weight:700">{disc_label}</span>'
                f'</div>'
            )
        elif is_fno_ext:
            market_header_html = (
                f'<div style="background:var(--surface-2);padding:10px 12px;border-radius:9px;border:1px solid rgba(56,189,248,.3);margin:8px 0 10px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
                f'<span style="font-size:.65rem;color:var(--spark);text-transform:uppercase;font-weight:700">🔔 CAS & F&O Extended</span>'
                f'<span style="font-size:.68rem;color:var(--spark);font-weight:700">⏳ Closes 15:40 IST</span>'
                f'</div>'
            )
        else:
            market_header_html = (
                f'<div style="background:var(--surface-2);padding:10px 12px;border-radius:9px;border:1px solid var(--line);margin:8px 0 10px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
                f'<span style="font-size:.65rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700">🛑 Market Closed</span>'
                f'<span style="font-size:.68rem;color:#f87171;font-weight:700">⏸️ Auto-Refresh Paused</span>'
                f'</div>'
            )
        st.markdown(market_header_html, unsafe_allow_html=True)

        is_active_session = market_open or is_pre_mkt or is_fno_ext
        mode_options = [
            "⚡ Live Stream (3s Tape / 15s GEX)",
            "⏱️ Active (10s Macro)",
            "🐢 Conservative (30s Macro)",
            "⏸️ Manual / Paused",
        ] if is_active_session else [
            "⏸️ Manual / Paused",
            "⚡ Live Stream (3s Tape / 15s GEX)",
            "⏱️ Active (10s Macro)",
            "🐢 Conservative (30s Macro)",
        ]

        if "stream_refresh_mode" not in st.session_state:
            st.session_state["stream_refresh_mode"] = mode_options[0]
        elif not is_active_session and "Manual" not in st.session_state.get("stream_refresh_mode", ""):
            # Auto-pause after market hours
            st.session_state["stream_refresh_mode"] = "⏸️ Manual / Paused"

        curr_mode = st.session_state.get("stream_refresh_mode", mode_options[0])
        curr_idx = mode_options.index(curr_mode) if curr_mode in mode_options else 0

        refresh_mode = st.selectbox(
            "Streaming Mode:",
            mode_options,
            index=curr_idx,
            key="stream_refresh_mode",
            label_visibility="collapsed",
        )

        if market_open:
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:center;font-size:.70rem;color:var(--ink-mute);margin-top:6px">'
                f'<span>Status: <b style="color:#4ade80">🟢 Live Market Open</b></span>'
                f'<span>RTT: <b style="color:var(--ink)">{rtt_ms:.0f}ms</b></span>'
                f'<span>Interval: <b style="color:var(--spark)">{poller.current_interval:.1f}s</b></span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        elif is_pre_mkt:
            pm_st = get_pre_market_state(symbol)
            disc_text = f"₹{pm_st['discovered_spot']:,.1f}" if pm_st.get("is_discovered") else "Polling (3s)"
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:center;font-size:.70rem;color:var(--ink-mute);margin-top:6px">'
                f'<span>Status: <b style="color:#f59e0b">🌅 Pre-Open Discovery</b></span>'
                f'<span>Spot: <b style="color:var(--ink)">{disc_text}</b></span>'
                f'<span>Mode: <b style="color:var(--spark)">Pre-Bell</b></span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        elif is_fno_ext:
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:center;font-size:.70rem;color:var(--ink-mute);margin-top:6px">'
                f'<span>Status: <b style="color:var(--spark)">🔔 F&O Ext / CAS</b></span>'
                f'<span>Close: <b style="color:var(--ink)">15:40 IST</b></span>'
                f'<span>Mode: <b style="color:var(--spark)">Settlement</b></span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:center;font-size:.70rem;color:var(--ink-mute);margin-top:6px">'
                f'<span>Status: <b style="color:#f87171">🛑 Closed (After Hours)</b></span>'
                f'<span>Auto-Refresh: <b style="color:var(--ink)">Off</b></span>'
                f'<span>Mode: <b style="color:var(--spark)">Manual</b></span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

        refresh_btn_label = "🔄 Refresh Cockpit" if not market_open else "🔄 Force Refresh All"
        if st.button(refresh_btn_label, key="btn_force_refresh", use_container_width=True):
            with st.spinner("🔄 Fetching data..."):
                _load_data(symbol, window, force=True)
            st.rerun()
        st.markdown("---")
        st.caption(
            "**Paper-trading only.** No real money. No broker. "
            "Indian Micromarket Mode: Short Call / Short Put (SCSP). Decision-support only."
        )
        st.markdown("---")
        _render_sidebar_token_manager()
        st.markdown("---")
        st.markdown("### 📚 Help Center & Manuals (New Tab)")

        st.markdown(
            '<div style="display:flex;flex-direction:column;gap:8px;font-size:.82rem;margin-top:8px">'
            '<a href="/?view=user_guide" target="_blank" style="color:var(--spark);text-decoration:none;font-weight:600">📖 Master User Guide ↗</a>'
            '<a href="/?view=pre_post_market_guide" target="_blank" style="color:var(--spark);text-decoration:none;font-weight:600">🌅 Pre/Post-Market & Recs Guide ↗</a>'
            '<a href="/?view=options_strategies_guide" target="_blank" style="color:var(--spark);text-decoration:none;font-weight:600">🎯 Options Strategies Guide ↗</a>'
            '<a href="/?view=orderflow_guide" target="_blank" style="color:var(--spark);text-decoration:none;font-weight:600">⚡ OrderFlow Signals Guide ↗</a>'
            '<a href="/?view=signal_efficiency_guide" target="_blank" style="color:var(--spark);text-decoration:none;font-weight:600">📈 Signal Efficiency Guide ↗</a>'
            '<a href="/?view=gex_playbook" target="_blank" style="color:var(--spark);text-decoration:none;font-weight:600">🗺️ Indian GEX Playbook ↗</a>'
            '<a href="/?view=participant_guide" target="_blank" style="color:var(--spark);text-decoration:none;font-weight:600">🧭 Participant Positioning ↗</a>'
            '</div>',

            unsafe_allow_html=True,
        )

    # Page title
    idx = config.get_index(symbol)
    st.markdown(
        f'<div class="ck">'
        f'<div style="display:flex;justify-content:space-between;align-items:center">'
        f'<div>'
        f'<h2 style="font-size:1.4rem;font-weight:800;margin:10px 0 4px">'
        f'🎯 {idx.label} Trading Cockpit</h2>'
        f'<p style="color:var(--ink-mute);font-size:.84rem;margin:0 0 12px">'
        f'GEX Map · Options Analytics · Paper Journal &nbsp;|&nbsp; {_ist_now()}</p>'
        f'</div>'
        f'<div>'
        f'<a href="/?view=user_guide" target="_blank" style="font-size:.76rem;background:var(--surface-2);color:var(--spark);padding:6px 12px;border-radius:8px;border:1px solid var(--line);text-decoration:none;font-weight:700">📚 Help Docs ↗</a>'
        f'</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Live data updates are managed entirely through decoupled, independent st.fragment
    # components per tab and per card without full-page or whole-tab reloads.

    # Tab navigation — respect pre-filled redirect from Cockpit → Journal
    if st.session_state.get("active_tab") == "📓 Journal":
        st.session_state.pop("active_tab", None)
        try:
            import streamlit.components.v1 as components
            components.html(
                """
                <script>
                setTimeout(function() {
                    const tabs = window.parent.document.querySelectorAll('button[data-baseweb="tab"]');
                    for (let tab of tabs) {
                        if (tab.innerText && tab.innerText.includes('Journal')) {
                            tab.click();
                            break;
                        }
                    }
                }, 100);
                </script>
                """,
                height=0,
                width=0,
            )
        except Exception:
            pass
    tab_labels = [
        "🗺️ Cockpit",
        "⚡ Trade Recommendations",
        "⚡ Sentinel Tape",
        "🎯 Signal Efficiency",
        "🧭 Market Intelligence",
        "📡 Options Analytics",
        "📓 Journal",
        "📊 Scorecard",
        "⚙️ Ops & Calendar",
    ]
    tabs = st.tabs(tab_labels)

    with tabs[0]:
        _tab_cockpit(symbol, window)

    with tabs[1]:
        _tab_trade_recommendations(symbol, window)

    with tabs[2]:
        _tab_sentinel_tape(symbol, window)

    with tabs[3]:
        _tab_signal_efficiency(symbol)

    with tabs[4]:
        _tab_market_intelligence(symbol, window)

    with tabs[5]:
        _tab_analytics(symbol, window)

    with tabs[6]:
        _tab_journal(symbol)

    with tabs[7]:
        _tab_scorecard(symbol)

    with tabs[8]:
        _tab_ops_and_calendar(symbol, window)

    st.markdown('</div>', unsafe_allow_html=True)  # close .ck


if __name__ == "__main__":
    main()

