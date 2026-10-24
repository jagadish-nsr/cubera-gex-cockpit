"""Streamlit UI Fragment: Target Engine (Phase 6).

Provides an interactive scenario dashboard, underlying price targets,
ranked trade candidates, explain drawers, and what-if repricing calculator.
"""

from __future__ import annotations

import time
from typing import Any

import streamlit as st
import pandas as pd
import numpy as np

import config
from target_engine.runner import run_target_engine
from app.data_access import load_dashboard
from target_engine.pricer import reprice, greek_attribution, greeks, price, implied_vol
from target_engine.levels import compute_touch_probability
from target_engine.time_utils import calculate_fractional_t, calculate_horizon_t, to_ist
import importlib
import analytics.strike_ladder as _sl_mod
try:
    importlib.reload(_sl_mod)
except Exception:
    pass
from analytics.strike_ladder import (
    compute_strike_ladder,
    StrikeLadderBundle,
    StrikeLadderRow,
    is_expiry_crossed,
    get_sidebar_active_expiries,
    get_dynamic_active_expiries,
)

# In-memory TTL cache for runner results: (symbol, window, horizon, mode) -> (ts, spot, result)
_TE_RUNNER_CACHE: dict[tuple, tuple[float, float, Any]] = {}


def _fmt_inr_pnl(val: float) -> str:
    """Format rupee PnL with proper sign and thousands separator."""
    if val > 0:
        return f"+₹{val:,.0f}"
    elif val < 0:
        return f"-₹{abs(val):,.0f}"
    return "₹0"


def _render_ladder_dual_matrix_html(bundle: StrikeLadderBundle) -> str:
    """Render Option A: Symmetrical Dual-Wing Matrix (Calls Left | Strike Center | Puts Right)."""
    rows_html = []
    for r in bundle.rows:
        row_cls = "atm-row" if r.is_atm else ""
        k_val = int(r.strike) if float(r.strike).is_integer() else f"{r.strike:.1f}"
        diff_from_spot = r.strike - bundle.spot
        diff_str = f"{diff_from_spot:+.0f}" if float(diff_from_spot).is_integer() else f"{diff_from_spot:+.1f}"

        # Strike cell content
        if r.is_atm:
            strike_content = f'{k_val} <span class="atm-star">ATM</span>'
        else:
            strike_content = f'{k_val} <span style="font-size:0.68rem;color:#64748b;font-weight:normal">({diff_str})</span>'

        # Calls (CE) Wing
        ce = r.ce
        ce_live_tag = (
            f'<span class="dot-live" title="Tick age: {ce.tick_age_s:.1f}s">● live</span>'
            if ce.is_ws_live
            else '<span style="font-size:0.65rem;color:#64748b">snap</span>'
        )
        ce_rr_cls = "rr-high" if ce.rr_ratio >= 1.5 else ("rr-med" if ce.rr_ratio >= 1.0 else "rr-low")
        ce_pnl_color = "#34d399" if ce.tgt1_inr >= 0 else "#f87171"

        # Puts (PE) Wing
        pe = r.pe
        pe_live_tag = (
            f'<span class="dot-live" title="Tick age: {pe.tick_age_s:.1f}s">● live</span>'
            if pe.is_ws_live
            else '<span style="font-size:0.65rem;color:#64748b">snap</span>'
        )
        pe_rr_cls = "rr-high" if pe.rr_ratio >= 1.5 else ("rr-med" if pe.rr_ratio >= 1.0 else "rr-low")
        pe_pnl_color = "#34d399" if pe.tgt1_inr >= 0 else "#f87171"

        rows_html.append(
            f'<tr class="{row_cls}">'
            f'<td style="color:{ce_pnl_color};font-weight:700" class="val-mono">{_fmt_inr_pnl(ce.tgt1_inr)}<div class="sub-pts" style="color:#64748b">T2: {_fmt_inr_pnl(ce.tgt2_inr)}</div></td>'
            f'<td><span class="rr-badge-ladder {ce_rr_cls}">{ce.rr_str}</span><div class="sub-pts" style="color:#94a3b8">P: {ce.p_touch_tgt1:.0%}</div></td>'
            f'<td style="color:#34d399" class="val-mono">₹{ce.opt_tgt2:.2f}<div class="sub-pts" style="color:#10b981">+{ce.tgt2_pts:.1f}</div></td>'
            f'<td style="color:#4ade80" class="val-mono">₹{ce.opt_tgt1:.2f}<div class="sub-pts" style="color:#22c55e">+{ce.tgt1_pts:.1f}</div></td>'
            f'<td style="color:#f87171" class="val-mono">₹{ce.opt_sl:.2f}<div class="sub-pts" style="color:#ef4444">-{ce.sl_pts:.1f}</div></td>'
            f'<td style="color:#38bdf8" class="val-mono">₹{ce.ltp:.2f}<div class="sub-pts" style="display:flex;justify-content:flex-end;gap:4px;align-items:center">{ce_live_tag} <span style="color:#64748b">Δ{ce.delta:+.2f}</span></div></td>'
            f'<td class="col-strike">{strike_content}</td>'
            f'<td style="color:#38bdf8" class="val-mono">₹{pe.ltp:.2f}<div class="sub-pts" style="display:flex;justify-content:flex-end;gap:4px;align-items:center">{pe_live_tag} <span style="color:#64748b">Δ{pe.delta:+.2f}</span></div></td>'
            f'<td style="color:#f87171" class="val-mono">₹{pe.opt_sl:.2f}<div class="sub-pts" style="color:#ef4444">-{pe.sl_pts:.1f}</div></td>'
            f'<td style="color:#4ade80" class="val-mono">₹{pe.opt_tgt1:.2f}<div class="sub-pts" style="color:#22c55e">+{pe.tgt1_pts:.1f}</div></td>'
            f'<td style="color:#34d399" class="val-mono">₹{pe.opt_tgt2:.2f}<div class="sub-pts" style="color:#10b981">+{pe.tgt2_pts:.1f}</div></td>'
            f'<td><span class="rr-badge-ladder {pe_rr_cls}">{pe.rr_str}</span><div class="sub-pts" style="color:#94a3b8">P: {pe.p_touch_tgt1:.0%}</div></td>'
            f'<td style="color:{pe_pnl_color};font-weight:700" class="val-mono">{_fmt_inr_pnl(pe.tgt1_inr)}<div class="sub-pts" style="color:#64748b">T2: {_fmt_inr_pnl(pe.tgt2_inr)}</div></td>'
            f'</tr>'
        )

    return (
        '<div class="ck-ladder-table-wrap">'
        '<table class="ck-ladder-table">'
        '<thead>'
        '<tr>'
        '<th colspan="6" class="th-calls-header">🟢 CALLS (CE) WING</th>'
        '<th rowspan="2" class="th-strike-header">STRIKE</th>'
        '<th colspan="6" class="th-puts-header">🔴 PUTS (PE) WING</th>'
        '</tr>'
        '<tr>'
        '<th style="text-align:right">T1 PnL/Lot</th>'
        '<th style="text-align:right">R : R</th>'
        '<th style="text-align:right">T2 (₹)</th>'
        '<th style="text-align:right">T1 (₹)</th>'
        '<th style="text-align:right">SL (₹)</th>'
        '<th style="text-align:right">LTP (₹)</th>'
        '<th style="text-align:right">LTP (₹)</th>'
        '<th style="text-align:right">SL (₹)</th>'
        '<th style="text-align:right">T1 (₹)</th>'
        '<th style="text-align:right">T2 (₹)</th>'
        '<th style="text-align:right">R : R</th>'
        '<th style="text-align:right">T1 PnL/Lot</th>'
        '</tr>'
        '</thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table>'
        '</div>'
    )


def _render_ladder_single_wing_html(bundle: StrikeLadderBundle, side: str) -> str:
    """Render single-wing focused view (Calls CE only or Puts PE only) with expanded Greeks."""
    is_ce = (side == "CE")
    header_title = "🟢 CALLS (CE) SINGLE-WING LADDER" if is_ce else "🔴 PUTS (PE) SINGLE-WING LADDER"
    header_cls = "th-calls-header" if is_ce else "th-puts-header"

    rows_html = []
    for r in bundle.rows:
        row_cls = "atm-row" if r.is_atm else ""
        k_val = int(r.strike) if float(r.strike).is_integer() else f"{r.strike:.1f}"
        diff_from_spot = r.strike - bundle.spot
        diff_str = f"{diff_from_spot:+.0f}" if float(diff_from_spot).is_integer() else f"{diff_from_spot:+.1f}"

        strike_content = (
            f'{k_val} <span class="atm-star">ATM</span>'
            if r.is_atm
            else f'{k_val} <span style="font-size:0.68rem;color:#64748b;font-weight:normal">({diff_str})</span>'
        )
        leg = r.ce if is_ce else r.pe
        live_tag = (
            f'<span class="dot-live" title="Tick age: {leg.tick_age_s:.1f}s">● live</span>'
            if leg.is_ws_live
            else '<span style="font-size:0.65rem;color:#64748b">snap</span>'
        )
        rr_cls = "rr-high" if leg.rr_ratio >= 1.5 else ("rr-med" if leg.rr_ratio >= 1.0 else "rr-low")
        pnl_color = "#34d399" if leg.tgt1_inr >= 0 else "#f87171"

        rows_html.append(
            f'<tr class="{row_cls}">'
            f'<td class="col-strike" style="text-align:center">{strike_content}</td>'
            f'<td style="color:#38bdf8" class="val-mono">₹{leg.ltp:.2f}<div class="sub-pts">{live_tag}</div></td>'
            f'<td style="color:#f87171" class="val-mono">₹{leg.opt_sl:.2f}<div class="sub-pts" style="color:#ef4444">-{leg.sl_pts:.1f} pts ({leg.sl_pct:.1%})</div></td>'
            f'<td style="color:#4ade80" class="val-mono">₹{leg.opt_tgt1:.2f}<div class="sub-pts" style="color:#22c55e">+{leg.tgt1_pts:.1f} pts ({leg.tgt1_pct:.1%})</div></td>'
            f'<td style="color:#34d399" class="val-mono">₹{leg.opt_tgt2:.2f}<div class="sub-pts" style="color:#10b981">+{leg.tgt2_pts:.1f} pts ({leg.tgt2_pct:.1%})</div></td>'
            f'<td><span class="rr-badge-ladder {rr_cls}">{leg.rr_str}</span></td>'
            f'<td class="val-mono" style="color:#94a3b8">{leg.p_touch_tgt1:.1%}<div class="sub-pts">SL: {leg.p_touch_sl:.1%}</div></td>'
            f'<td style="color:{pnl_color};font-weight:700" class="val-mono">{_fmt_inr_pnl(leg.tgt1_inr)}</td>'
            f'<td style="color:#34d399;font-weight:700" class="val-mono">{_fmt_inr_pnl(leg.tgt2_inr)}</td>'
            f'<td style="color:#cbd5e1;font-size:0.75rem" class="val-mono">Δ: {leg.delta:+.2f} · θ: {leg.theta:+.1f}<div class="sub-pts">IV: {leg.iv * 100:.1f}%</div></td>'
            f'</tr>'
        )

    return (
        '<div class="ck-ladder-table-wrap">'
        '<table class="ck-ladder-table">'
        '<thead>'
        f'<tr><th colspan="10" class="{header_cls}">{header_title}</th></tr>'
        '<tr>'
        '<th style="text-align:center">Strike</th>'
        '<th style="text-align:right">Live LTP (₹)</th>'
        '<th style="text-align:right">Stop Loss (₹)</th>'
        '<th style="text-align:right">Target 1 (₹)</th>'
        '<th style="text-align:right">Target 2 (₹)</th>'
        '<th style="text-align:right">Risk : Reward</th>'
        '<th style="text-align:right">Touch Prob</th>'
        '<th style="text-align:right">T1 PnL / Lot</th>'
        '<th style="text-align:right">T2 PnL / Lot</th>'
        '<th style="text-align:right">Greeks & IV</th>'
        '</tr>'
        '</thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table>'
        '</div>'
    )


@st.fragment(run_every=2)
def render_live_strike_ladder(
    symbol: str,
    spot: float,
    contracts_df: Optional[pd.DataFrame],
    available_expiries: list[str],
    default_expiry: str,
    levels: Any,
    default_horizon: int = 45,
):
    """Fragment rendering live WebSocket streaming target & SL strike ladder (ATM ± 5 strikes)."""
    # Attempt to fetch freshest live spot tick from WebSocket lane
    try:
        from app.spot_ticker import latest
        tick = latest(symbol)
        if tick and tick.get("ltp"):
            spot = float(tick["ltp"])
    except Exception:
        pass

    st.markdown(
        """<style>
@keyframes pulse_live_dot {
    0% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.35; transform: scale(0.85); }
    100% { opacity: 1; transform: scale(1); }
}
.ck-ladder-table-wrap {
    overflow-x: auto;
    border-radius: 8px;
    border: 1px solid #1e293b;
    background: #090d16;
    margin-bottom: 0.75rem;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
}
.ck-ladder-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.81rem;
    color: #e2e8f0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.ck-ladder-table th, .ck-ladder-table td {
    padding: 7px 9px;
    text-align: right;
    border-bottom: 1px solid #1e293b;
}
.ck-ladder-table th {
    font-size: 0.70rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 700;
    background: #0f172a;
    color: #94a3b8;
}
.ck-ladder-table .th-calls-header {
    background: rgba(16, 185, 129, 0.12);
    color: #34d399;
    text-align: center;
    border-right: 1px solid #1e293b;
    font-size: 0.76rem;
    letter-spacing: 0.06em;
}
.ck-ladder-table .th-puts-header {
    background: rgba(239, 68, 68, 0.12);
    color: #f87171;
    text-align: center;
    border-left: 1px solid #1e293b;
    font-size: 0.76rem;
    letter-spacing: 0.06em;
}
.ck-ladder-table .th-strike-header {
    background: rgba(234, 179, 8, 0.15);
    color: #fbbf24;
    text-align: center;
    font-size: 0.78rem;
    font-weight: 800;
    letter-spacing: 0.08em;
    border-left: 1px solid rgba(234, 179, 8, 0.3);
    border-right: 1px solid rgba(234, 179, 8, 0.3);
}
.ck-ladder-table td.col-strike {
    text-align: center;
    background: rgba(15, 23, 42, 0.95);
    border-left: 1px solid rgba(234, 179, 8, 0.25);
    border-right: 1px solid rgba(234, 179, 8, 0.25);
    font-family: var(--mono, ui-monospace, monospace);
    font-weight: 800;
    font-size: 0.86rem;
    color: #f8fafc;
}
.ck-ladder-table tr.atm-row {
    background: rgba(234, 179, 8, 0.08) !important;
}
.ck-ladder-table tr.atm-row td.col-strike {
    background: rgba(234, 179, 8, 0.20) !important;
    color: #fde047 !important;
    box-shadow: inset 0 0 10px rgba(234, 179, 8, 0.25);
}
.ck-ladder-table tr:hover {
    background: rgba(255, 255, 255, 0.035);
}
.val-mono {
    font-family: var(--mono, ui-monospace, monospace);
    font-weight: 700;
}
.sub-pts {
    font-size: 0.69rem;
    margin-top: 1px;
    font-family: var(--mono, ui-monospace, monospace);
}
.rr-badge-ladder {
    display: inline-block;
    padding: 1px 5px;
    border-radius: 4px;
    font-size: 0.70rem;
    font-weight: 750;
    font-family: var(--mono, ui-monospace, monospace);
}
.rr-high { background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.4); }
.rr-med { background: rgba(234, 179, 8, 0.2); color: #facc15; border: 1px solid rgba(234, 179, 8, 0.4); }
.rr-low { background: rgba(148, 163, 184, 0.15); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.3); }
.atm-star {
    display: inline-block;
    background: #eab308;
    color: #0f172a;
    font-size: 0.62rem;
    font-weight: 900;
    padding: 1px 4px;
    border-radius: 3px;
    margin-left: 3px;
    vertical-align: middle;
}
.dot-live {
    color: #22c55e;
    font-size: 0.70rem;
    font-weight: 600;
    animation: pulse_live_dot 1.2s infinite;
}
</style>""",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:0.25rem;margin-bottom:0.75rem">'
        '<div>'
        '<h3 style="margin:0;padding:0;font-size:1.25rem;font-weight:700;color:#f8fafc;display:flex;align-items:center;gap:8px">'
        '⚡ Live Target / SL Strike Ladder <span style="font-size:0.74rem;font-weight:700;color:#38bdf8;background:rgba(56,189,248,.12);padding:2px 8px;border-radius:4px;border:1px solid rgba(56,189,248,.25)">ATM ± 5 STRIKES</span>'
        '</h3>'
        '<p style="margin:0.25rem 0 0 0;font-size:0.82rem;color:#94a3b8">'
        'Real-time WebSocket streaming matrix repricing 11 strikes across Target 1, Target 2, and Stop Loss invalidation with live Greeks & ₹ PnL per lot.'
        '</p>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # 1. Controls bar
    # Uses available_expiries passed from tab (matching sidebar expiry cycle)
    ladder_exp_key = f"sl_ladder_exp_{symbol}"
    # If currently chosen expiry has crossed or is invalid, reset to default active
    if ladder_exp_key in st.session_state:
        cur_ladder_exp = st.session_state[ladder_exp_key]
        if cur_ladder_exp not in available_expiries or is_expiry_crossed(cur_ladder_exp):
            st.session_state[ladder_exp_key] = default_expiry if default_expiry in available_expiries else (available_expiries[0] if available_expiries else "")

    curr_selected = st.session_state.get(ladder_exp_key, default_expiry)
    if curr_selected not in available_expiries or is_expiry_crossed(curr_selected):
        curr_selected = default_expiry if default_expiry in available_expiries else (available_expiries[0] if available_expiries else "")
    sel_idx = available_expiries.index(curr_selected) if curr_selected in available_expiries else 0

    col_l1, col_l2, col_l3, col_l4 = st.columns([2.5, 2.0, 3.2, 2.3])
    with col_l1:
        sel_ladder_exp = st.selectbox(
            "📅 Ladder Expiry",
            options=available_expiries,
            index=sel_idx,
            key=ladder_exp_key,
            help="Select option expiry cycle for the 11-strike ladder (matches sidebar expiry cycle)",
        )
    with col_l2:
        sel_ladder_horizon = st.selectbox(
            "⏱️ Projection Horizon",
            options=[30, 45, 60, 120],
            index=1,
            format_func=lambda h: f"{h} mins",
            key=f"sl_ladder_h_{symbol}",
            help="Target projection time horizon",
        )
    with col_l3:
        view_mode = st.radio(
            "👁️ Matrix View",
            options=["Dual-Wing Matrix", "Calls (CE) Only", "Puts (PE) Only"],
            index=0,
            horizontal=True,
            key=f"sl_ladder_view_{symbol}",
        )
    with col_l4:
        live_stream_toggle = st.toggle(
            "⚡ Live WebSocket",
            value=True,
            key=f"sl_ladder_stream_{symbol}",
            help="Toggle live ticks auto-update from exchange WebSocket feed",
        )

    # 2. Compute 11-strike ladder
    bundle = compute_strike_ladder(
        symbol=symbol,
        spot=spot,
        contracts_df=contracts_df,
        expiry=sel_ladder_exp,
        horizon_minutes=sel_ladder_horizon,
        levels=levels,
        auto_subscribe_ws=live_stream_toggle,
    )

    # 3. Reference Anchors & Ticks Status Bar
    ws_badge = (
        f'<span style="color:#22c55e;font-weight:700;display:inline-flex;align-items:center;gap:4px">'
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;box-shadow:0 0 8px #22c55e;animation:pulse_live_dot 1.5s infinite"></span>'
        f'{bundle.ws_active_count}/11 Live WS Ticks</span>'
        if bundle.ws_active_count > 0
        else '<span style="color:#94a3b8;font-weight:600">○ Snapshot Mode</span>'
    )

    st.markdown(
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:0.75rem">'
        f'<div style="background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.2);border-radius:6px;padding:8px 12px;font-size:0.8rem">'
        f'<div style="display:flex;justify-content:space-between;align-items:center">'
        f'<span style="font-weight:700;color:#34d399">🟢 Bullish / Calls (CE) Reference</span>'
        f'<span style="font-size:0.72rem;color:#94a3b8">Spot Inval: <b style="color:#f87171">{bundle.ce_spot_inval:,.1f}</b></span>'
        f'</div>'
        f'<div style="margin-top:4px;color:#cbd5e1;font-size:0.75rem">'
        f'Target 1: <b style="color:#4ade80">{bundle.ce_tgt1_spot:,.1f}</b> · Target 2: <b style="color:#34d399">{bundle.ce_tgt2_spot:,.1f}</b>'
        f'</div>'
        f'</div>'
        f'<div style="background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.2);border-radius:6px;padding:8px 12px;font-size:0.8rem">'
        f'<div style="display:flex;justify-content:space-between;align-items:center">'
        f'<span style="font-weight:700;color:#f87171">🔴 Bearish / Puts (PE) Reference</span>'
        f'<span style="font-size:0.72rem;color:#94a3b8">Spot Inval: <b style="color:#f87171">{bundle.pe_spot_inval:,.1f}</b></span>'
        f'</div>'
        f'<div style="margin-top:4px;color:#cbd5e1;font-size:0.75rem">'
        f'Target 1: <b style="color:#f87171">{bundle.pe_tgt1_spot:,.1f}</b> · Target 2: <b style="color:#ef4444">{bundle.pe_tgt2_spot:,.1f}</b>'
        f'</div>'
        f'</div>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;font-size:0.75rem;color:#64748b">'
        f'<div>{ws_badge} · Lot Size: <b style="color:#e2e8f0">{bundle.lot_size}</b> · Spot: <b style="color:#38bdf8">{bundle.spot:,.1f}</b> · ATM: <b style="color:#facc15">{int(bundle.atm_strike)}</b></div>'
        f'<div>Updated: <b>{bundle.computed_at}</b></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # 4. Render Table by view mode
    if not bundle.rows:
        st.warning("No option strike ladder data available for the chosen expiry.")
        return

    if view_mode == "Dual-Wing Matrix":
        table_html = _render_ladder_dual_matrix_html(bundle)
    elif view_mode == "Calls (CE) Only":
        table_html = _render_ladder_single_wing_html(bundle, side="CE")
    else:
        table_html = _render_ladder_single_wing_html(bundle, side="PE")

    st.markdown(table_html, unsafe_allow_html=True)

    with st.expander("ℹ️ Strike Ladder Methodology & Parameter Notes", expanded=False):
        st.markdown(f"""
            - **11-Strike Band:** Slices exactly 5 strikes below ATM, the ATM strike, and 5 strikes above ATM ($K_{{\\text{{atm}}-5}}$ to $K_{{\\text{{atm}}+5}}$) using the official index step size ({bundle.symbol} = {bundle.rows[1].strike - bundle.rows[0].strike if len(bundle.rows)>1 else 50:.0f} pts).
            - **Real-Time WebSocket Ingestion:** Ingests exchange binary ticks pushed via Dhan v2 quote tier with millisecond latency, seamlessly falling back to authenticated snapshot quotes outside market hours.
            - **Monotonic Pricing Logic:** Enforces strict financial boundary conditions ($V_{{\\text{{tgt2}}}} > V_{{\\text{{tgt1}}}} > \\text{{LTP}} > V_{{\\text{{sl}}}}$) with forward index drift and regime-dependent implied volatility adjustments.
            - **Barrier Touch Probabilities:** Derived from Brownian motion first-passage reflection principle over the projection horizon ($H = {sel_ladder_horizon}$ mins).
            - **₹ PnL per Lot:** Point changes scaled by exchange contract lot size ({bundle.lot_size} shares/units per lot).
        """)


@st.fragment
def render_target_engine_tab(symbol: str, window: str, data: Any = None):
    """Main entrypoint for rendering the Target Engine tab in the Cockpit."""
    st.markdown("""
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
            <div>
                <h2 style="margin: 0; padding: 0; font-size: 1.5rem; font-weight: 700; color: #f8fafc;">
                    🎯 Target Engine — Scenario & Repricing Suite
                </h2>
                <p style="margin: 0.25rem 0 0 0; font-size: 0.85rem; color: #94a3b8;">
                    Decision-support quant engine: Probability-weighted scenarios, underlying price targets, IV forecasting & Black-76 option repricing.
                </p>
            </div>
            <div>
                <a href="/?view=target_engine_guide" target="_blank" style="font-size: .76rem; background: var(--surface-2); color: var(--spark); padding: 6px 12px; border-radius: 6px; border: 1px solid var(--line); text-decoration: none; font-weight: 700;">
                    📖 Target Engine Guide ↗
                </a>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # 1. Controls bar
    col_c1, col_c2, col_c3 = st.columns([2, 2, 4])
    with col_c1:
        horizon_opt = st.selectbox(
            "Target Horizon",
            options=[30, 60, 120, 240, 375],
            index=2,
            format_func=lambda h: f"{h} mins ({'0.5 hr' if h==30 else f'{h//60} hrs' if h<375 else 'Full Session'})",
            key=f"te_horizon_{symbol}",
        )
    with col_c2:
        model_mode = st.selectbox(
            "Scoring Mode",
            options=["Heuristic Baseline", "Calibrated (Stage B)"],
            index=0,
            key=f"te_mode_{symbol}",
        )
    with col_c3:
        st.caption("Auto-refreshes with live chain updates. All numbers explainable via the Diagnostics drawer below.")

    # 2. Fetch live dashboard snapshot (fast Stale-While-Revalidate without blocking spinners)
    dash = data
    if dash is None:
        try:
            from app.cockpit import _load_data
            dash, _ = _load_data(symbol, window)
        except Exception:
            dash = load_dashboard(window=window, symbol=symbol)

    if not dash or not getattr(dash, "levels", None):
        st.warning("No market data available for Target Engine.")
        return

    # 3. Execute runner pipeline with 8s TTL cache for instant What-If slider adjustments
    now_t = time.time()
    spot_val = float(dash.levels.spot)
    cache_key = (symbol, window, horizon_opt, model_mode)
    cached_entry = _TE_RUNNER_CACHE.get(cache_key)

    if cached_entry and (now_t - cached_entry[0] < 8.0) and abs(cached_entry[1] - spot_val) < 2.0:
        res = cached_entry[2]
    else:
        res = run_target_engine(dashboard_data=dash, horizon_minutes=horizon_opt, persist=False)
        _TE_RUNNER_CACHE[cache_key] = (now_t, spot_val, res)
    sc_set = res.scenario_set
    targets = res.underlying_targets
    iv_fcs = res.iv_forecasts
    cands = res.candidates

    p_bull = sc_set.get_prob("bull")
    p_base = sc_set.get_prob("base")
    p_bear = sc_set.get_prob("bear")

    # 4. Scenario Probability Bar & Edge Status
    edge_bg = "#064e3b" if not sc_set.no_edge else "#78350f"
    edge_text = "CONFIRMED EDGE" if not sc_set.no_edge else "NO EDGE — RANGE DOMINANT"
    dom_color = "#10b981" if sc_set.dominant_scenario == "bull" else ("#ef4444" if sc_set.dominant_scenario == "bear" else "#3b82f6")

    st.markdown(f"""
        <div style="background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 1rem; margin-bottom: 1.25rem;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem;">
                <div>
                    <span style="font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; font-weight: 600;">Dominant Scenario</span>
                    <div style="font-size: 1.25rem; font-weight: 800; color: {dom_color}; text-transform: uppercase;">
                        {sc_set.dominant_scenario} ({max(p_bull, p_base, p_bear):.1%})
                    </div>
                </div>
                <div style="text-align: right;">
                    <span style="background: {edge_bg}; color: #f8fafc; padding: 0.25rem 0.6rem; border-radius: 4px; font-size: 0.75rem; font-weight: 700;">
                        {edge_text}
                    </span>
                    <div style="font-size: 0.8rem; color: #94a3b8; margin-top: 0.25rem;">
                        Conviction: <b>{sc_set.conviction:.0%}</b> | Spot: <b>{dash.levels.spot:,.1f}</b>
                    </div>
                </div>
            </div>
            <!-- Multi-color Segmented Progress Bar -->
            <div style="display: flex; height: 18px; border-radius: 6px; overflow: hidden; background: #334155;">
                <div style="width: {p_bull * 100:.1f}%; background: #10b981;" title="Bull: {p_bull:.1%}"></div>
                <div style="width: {p_base * 100:.1f}%; background: #3b82f6;" title="Base: {p_base:.1%}"></div>
                <div style="width: {p_bear * 100:.1f}%; background: #ef4444;" title="Bear: {p_bear:.1%}"></div>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: #94a3b8; margin-top: 0.35rem;">
                <span>🟢 Bullish: <b>{p_bull:.1%}</b></span>
                <span>🔵 Base (Range): <b>{p_base:.1%}</b></span>
                <span>🔴 Bearish: <b>{p_bear:.1%}</b></span>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # 5. Price Ladder Cards: Bull, Base, Bear
    t_col1, t_col2, t_col3 = st.columns(3)

    with t_col1:
        b_tar = targets.get("bull")
        b_iv = iv_fcs.get("bull")
        st.markdown(f"""
            <div style="background: rgba(16, 185, 129, 0.05); border: 1px solid rgba(16, 185, 129, 0.25); border-radius: 8px; padding: 0.9rem;">
                <div style="font-weight: 700; color: #10b981; font-size: 0.95rem; margin-bottom: 0.4rem;">
                    🚀 Bull Target ({horizon_opt}m)
                </div>
                <div style="font-size: 1.4rem; font-weight: 800; color: #f8fafc;">
                    {b_tar.target:,.1f}
                </div>
                <div style="font-size: 0.8rem; color: #94a3b8; margin-bottom: 0.5rem;">
                    Stop: <span style="color: #f87171;">{b_tar.stop:,.1f}</span>
                </div>
                <div style="font-size: 0.75rem; color: #cbd5e1; line-height: 1.4;">
                    • P(Touch Target): <b>{b_tar.p_touch_target:.1%}</b><br>
                    • P(Close Beyond): <b>{b_tar.p_close_beyond:.1%}</b><br>
                    • Forecast ATM IV: <b>{b_iv.iv_at_horizon:.2%}</b><br>
                    • Anchor: <i>{b_tar.reason}</i>
                </div>
            </div>
        """, unsafe_allow_html=True)

    with t_col2:
        base_tar = targets.get("base")
        base_iv = iv_fcs.get("base")
        st.markdown(f"""
            <div style="background: rgba(59, 130, 246, 0.05); border: 1px solid rgba(59, 130, 246, 0.25); border-radius: 8px; padding: 0.9rem;">
                <div style="font-weight: 700; color: #3b82f6; font-size: 0.95rem; margin-bottom: 0.4rem;">
                    ⚖️ Base / Range Target ({horizon_opt}m)
                </div>
                <div style="font-size: 1.4rem; font-weight: 800; color: #f8fafc;">
                    {base_tar.target:,.1f}
                </div>
                <div style="font-size: 0.8rem; color: #94a3b8; margin-bottom: 0.5rem;">
                    Band Stop: <span style="color: #f87171;">{base_tar.stop:,.1f}</span>
                </div>
                <div style="font-size: 0.75rem; color: #cbd5e1; line-height: 1.4;">
                    • P(Touch Target): <b>{base_tar.p_touch_target:.1%}</b><br>
                    • P(Stay in Band): <b>{base_tar.p_close_beyond:.1%}</b><br>
                    • Forecast ATM IV: <b>{base_iv.iv_at_horizon:.2%}</b><br>
                    • Anchor: <i>{base_tar.reason}</i>
                </div>
            </div>
        """, unsafe_allow_html=True)

    with t_col3:
        bear_tar = targets.get("bear")
        bear_iv = iv_fcs.get("bear")
        st.markdown(f"""
            <div style="background: rgba(239, 68, 68, 0.05); border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px; padding: 0.9rem;">
                <div style="font-weight: 700; color: #ef4444; font-size: 0.95rem; margin-bottom: 0.4rem;">
                    🔻 Bear Target ({horizon_opt}m)
                </div>
                <div style="font-size: 1.4rem; font-weight: 800; color: #f8fafc;">
                    {bear_tar.target:,.1f}
                </div>
                <div style="font-size: 0.8rem; color: #94a3b8; margin-bottom: 0.5rem;">
                    Stop: <span style="color: #f87171;">{bear_tar.stop:,.1f}</span>
                </div>
                <div style="font-size: 0.75rem; color: #cbd5e1; line-height: 1.4;">
                    • P(Touch Target): <b>{bear_tar.p_touch_target:.1%}</b><br>
                    • P(Close Beyond): <b>{bear_tar.p_close_beyond:.1%}</b><br>
                    • Forecast ATM IV: <b>{bear_iv.iv_at_horizon:.2%}</b><br>
                    • Anchor: <i>{bear_tar.reason}</i>
                </div>
            </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='height: 1rem;'></div>", unsafe_allow_html=True)

    # Extract contracts DataFrame, index configuration, and active expiries
    contracts_df = dash.contracts if hasattr(dash, "contracts") and isinstance(dash.contracts, pd.DataFrame) else None
    idx_info = config.get_index(symbol)
    lot_sz = int(idx_info.lot_size)

    available_expiries = get_sidebar_active_expiries(
        symbol=symbol,
        dash=dash,
        contracts_df=contracts_df,
    )
    if not available_expiries:
        available_expiries = [str(dash.levels.expiry) if hasattr(dash.levels, "expiry") else "2026-10-19"]

    sidebar_target_exp = str(dash.levels.expiry).strip() if (dash and hasattr(dash, "levels") and getattr(dash.levels, "expiry", None)) else None
    default_expiry = sidebar_target_exp if (sidebar_target_exp and sidebar_target_exp in available_expiries) else available_expiries[0]

    # 6. Live WebSocket Dynamic Target/SL Strike Ladder (ATM ± 5 Strikes)
    render_live_strike_ladder(
        symbol=symbol,
        spot=spot_val,
        contracts_df=contracts_df,
        available_expiries=available_expiries,
        default_expiry=default_expiry,
        levels=dash.levels,
        default_horizon=horizon_opt,
    )

    st.markdown("<div style='height: 1.5rem;'></div>", unsafe_allow_html=True)

    # 7. Ranked Candidate Strategy Cards
    st.markdown("### 📋 Ranked Trade Candidates (Scenario-Repriced)")
    if cands:
        for idx, cand in enumerate(cands[:4]):
            status_badge = "✅ PASS" if cand.passes_filters else "⚠️ FILTER REJECT"
            badge_color = "#10b981" if cand.passes_filters else "#f59e0b"

            with st.expander(f"Rank #{idx+1} — {cand.structure_type} | Net EV: ₹{cand.expected_value:+,.0f} | R:R: {cand.risk_reward:.2f}:1", expanded=(idx == 0)):
                col_r1, col_r2, col_r3, col_r4 = st.columns(4)
                with col_r1:
                    st.metric("Net EV (Probability-Weighted)", f"₹{cand.expected_value:+,.0f}")
                with col_r2:
                    st.metric("Risk : Reward", f"{cand.risk_reward:.2f} : 1")
                with col_r3:
                    st.metric("Max Loss", f"₹{cand.max_loss_inr:,.0f} ({cand.max_loss_pts:.1f} pts)")
                with col_r4:
                    be_str = ", ".join(f"{b:,.0f}" for b in cand.breakevens)
                    st.metric("Breakeven(s)", be_str)

                # Scenario Payoff matrix table
                st.markdown("**Per-Scenario Repriced Payoff:**")
                sc_rows = []
                for sc_name in ["bull", "base", "bear"]:
                    ot = cand.scenario_targets.get(sc_name)
                    if ot:
                        sc_rows.append({
                            "Scenario": sc_name.upper(),
                            "Target Spot": f"{targets[sc_name].target:,.1f}",
                            "Entry Prem (pts)": f"{ot.entry_premium:.2f}",
                            "Repriced Prem (pts)": f"{ot.target_premium:.2f}",
                            "PnL (pts)": f"{ot.pnl_pts:+.2f}",
                            "PnL (%)": f"{ot.pnl_pct:+.1f}%",
                            "Delta PnL": f"{ot.greek_attribution.delta_pnl:+.1f}",
                            "Vega PnL": f"{ot.greek_attribution.vega_pnl:+.1f}",
                            "Theta Bleed": f"{ot.greek_attribution.theta_pnl:+.1f}",
                        })
                st.dataframe(pd.DataFrame(sc_rows), use_container_width=True, hide_index=True)

                if cand.explain:
                    st.markdown("**Rationale:**")
                    for exp in cand.explain:
                        st.markdown(f"- {exp}")
    else:
        st.info("No candidates generated for current filters.")

    st.markdown("<div style='height: 1.5rem;'></div>", unsafe_allow_html=True)

    # 8. Interactive "What-If" Option Repricer & Target/SL Calculator
    st.markdown("### 🎛️ Interactive 'What-If' Option Repricer & Target/SL Calculator")
    st.caption("Calculate exact option target & stop-loss premiums, PnL per lot, risk:reward, and Greeks for any strike across any expiry.")

    if contracts_df is not None and not contracts_df.empty:
        # Top Contract Selection Bar
        wi_sel_col1, wi_sel_col2, wi_sel_col3, wi_sel_col4 = st.columns([2.5, 2.5, 2, 3])

        with wi_sel_col1:
            wi_exp_key = f"wi_exp_{symbol}"
            if wi_exp_key in st.session_state:
                cur_wi = st.session_state[wi_exp_key]
                if cur_wi not in available_expiries or is_expiry_crossed(cur_wi):
                    st.session_state[wi_exp_key] = available_expiries[0]

            wi_curr_sel = st.session_state.get(wi_exp_key, available_expiries[0])
            if wi_curr_sel not in available_expiries or is_expiry_crossed(wi_curr_sel):
                wi_curr_sel = available_expiries[0]
            wi_idx = available_expiries.index(wi_curr_sel) if wi_curr_sel in available_expiries else 0

            sel_expiry = st.selectbox(
                "📅 Expiry",
                options=available_expiries,
                index=wi_idx,
                key=wi_exp_key,
                help="Select any active expiry contract from the option chain",
            )

        # Filter contracts DataFrame for chosen expiry
        def _match_expiry(exp_val, target_str):
            if hasattr(exp_val, "strftime"):
                return exp_val.strftime("%Y-%m-%d") == target_str
            return str(exp_val).strip() == target_str

        exp_contracts = contracts_df[contracts_df["expiry"].apply(lambda x: _match_expiry(x, sel_expiry))]
        if exp_contracts.empty:
            exp_contracts = contracts_df

        # Clean strikes: sort and convert whole numbers to int (e.g. 22600 instead of 22600.0)
        raw_strikes = sorted(exp_contracts["strike"].dropna().unique())
        if not raw_strikes:
            raw_strikes = sorted(contracts_df["strike"].dropna().unique())
        all_exp_strikes = [int(k) if float(k).is_integer() else float(k) for k in raw_strikes]
        atm_strike = min(all_exp_strikes, key=lambda k: abs(float(k) - float(dash.levels.spot)))
        atm_idx = all_exp_strikes.index(atm_strike)

        # Slice exactly 10 strikes below ATM and 10 strikes above ATM (up to 21 strikes total)
        start_idx = max(0, atm_idx - 10)
        end_idx = min(len(all_exp_strikes), atm_idx + 11)
        nearby_21_strikes = all_exp_strikes[start_idx:end_idx]

        CUSTOM_STRIKE_LABEL = "✏️ Custom Strike (Manual Entry)..."
        strike_dropdown_options = list(nearby_21_strikes) + [CUSTOM_STRIKE_LABEL]

        strike_key = f"wi_strike_{symbol}"
        strike_select_key = f"{strike_key}_select"
        exp_tracker_key = f"wi_last_exp_{symbol}"

        # Guarantee default is ALWAYS set to ATM strike on initial load, or when expiry changes
        if (
            strike_key not in st.session_state
            or st.session_state.get(exp_tracker_key) != sel_expiry
        ):
            st.session_state[strike_key] = atm_strike
            st.session_state[strike_select_key] = atm_strike
            st.session_state[exp_tracker_key] = sel_expiry

        # Keep selectbox value in sync if user had selected a valid nearby strike or custom
        if st.session_state.get(strike_select_key) not in strike_dropdown_options:
            if st.session_state.get(strike_key) in nearby_21_strikes:
                st.session_state[strike_select_key] = st.session_state[strike_key]
            elif st.session_state.get(strike_key) is not None:
                st.session_state[strike_select_key] = CUSTOM_STRIKE_LABEL
            else:
                st.session_state[strike_select_key] = atm_strike

        def _format_strike_choice(k: int | float | str) -> str:
            if isinstance(k, str):
                return k
            val_clean = f"{int(k)}" if float(k).is_integer() else f"{k:.1f}"
            diff = int(round(float(k) - float(atm_strike)))
            if diff == 0:
                return f"{val_clean} · ATM"
            return f"{val_clean} ({diff:+d})"

        with wi_sel_col2:
            current_choice = st.session_state.get(strike_select_key, atm_strike)
            curr_idx = (
                strike_dropdown_options.index(current_choice)
                if current_choice in strike_dropdown_options
                else (strike_dropdown_options.index(atm_strike) if atm_strike in strike_dropdown_options else 0)
            )
            selected_item = st.selectbox(
                "🎯 Strike (21 ATM Band)",
                options=strike_dropdown_options,
                index=curr_idx,
                format_func=_format_strike_choice,
                key=strike_select_key,
                help="10 strikes above and 10 strikes below ATM. Choose 'Custom Strike' to enter any other strike manually.",
            )

            if selected_item == CUSTOM_STRIKE_LABEL:
                strike_step = float(idx_info.strike_step)
                curr_strike_val = st.session_state.get(strike_key)
                init_val = float(curr_strike_val) if (curr_strike_val is not None and not isinstance(curr_strike_val, str)) else float(atm_strike)
                custom_strike_val = st.number_input(
                    "Manual Strike Price",
                    value=init_val,
                    step=strike_step,
                    key=f"wi_custom_strike_val_{symbol}",
                    help="Enter any specific strike price manually",
                )
                sel_strike = int(custom_strike_val) if float(custom_strike_val).is_integer() else float(custom_strike_val)
                st.session_state[strike_key] = sel_strike
            else:
                sel_strike = selected_item
                st.session_state[strike_key] = sel_strike


        with wi_sel_col3:
            sel_type = st.radio(
                "Option Type",
                options=["CE", "PE"],
                horizontal=True,
                key=f"wi_type_{symbol}",
            )

        with wi_sel_col4:
            calc_tab = st.radio(
                "Calculation Mode",
                options=["🎯 Target / SL Calculator", "🎛️ Custom What-If Sliders"],
                horizontal=True,
                key=f"wi_mode_{symbol}",
            )

        # 2. Extract specific contract row & live market reality
        type_prefix = "C" if sel_type == "CE" else "P"
        match_rows = exp_contracts[
            (pd.to_numeric(exp_contracts["strike"], errors="coerce") == float(sel_strike)) &
            (exp_contracts["type"].astype(str).str.upper().isin([sel_type, type_prefix]))
        ]

        now_dt = to_ist()
        T_now = calculate_fractional_t(now_dt, sel_expiry)
        base_fut = float(getattr(dash.levels, "future_price", None) or dash.levels.spot)
        atm_iv_base = float(getattr(dash.levels, "atm_iv", None) or 0.15)

        market_ltp: float | None = None
        strike_iv = atm_iv_base

        if not match_rows.empty:
            crow = match_rows.iloc[0]
            for col in ("mid", "ltp", "price", "last_price"):
                val = crow.get(col)
                if val is not None and not (isinstance(val, float) and np.isnan(val)) and float(val) > 0:
                    market_ltp = float(val)
                    break
            raw_iv = crow.get("iv")
            if raw_iv is not None and not (isinstance(raw_iv, float) and np.isnan(raw_iv)) and float(raw_iv) > 0:
                strike_iv = float(raw_iv) / 100.0 if float(raw_iv) > 1.0 else float(raw_iv)
            elif market_ltp and market_ltp > 0:
                solved_iv = implied_vol(market_ltp, base_fut, sel_strike, T_now, 0.065, sel_type)
                if solved_iv and solved_iv > 0:
                    strike_iv = solved_iv

        p_theo_curr = price(base_fut, sel_strike, T_now, strike_iv, 0.065, sel_type)
        cur_greeks = greeks(base_fut, sel_strike, T_now, strike_iv, 0.065, sel_type)
        entry_premium = market_ltp if (market_ltp is not None and market_ltp > 0) else p_theo_curr

        # Display current contract telemetry strip
        st.markdown(f"""
            <div style="background: rgba(15, 23, 42, 0.85); border: 1px solid #334155; border-radius: 6px; padding: 10px 14px; margin-bottom: 14px;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                    <div>
                        <span style="font-size: 0.85rem; font-weight: 750; color: #f8fafc;">
                            {symbol} {sel_expiry} {int(sel_strike)} {sel_type}
                        </span>
                        <span style="font-size: 0.76rem; color: #94a3b8; margin-left: 8px;">
                            (DTE: <b>{T_now * 365.0:.1f}d</b> · Lot: <b>{lot_sz}</b> · Underlying Fut: <b>{base_fut:,.1f}</b>)
                        </span>
                    </div>
                    <div style="display: flex; gap: 16px; font-size: 0.80rem; color: #cbd5e1;">
                        <span>Current Premium (LTP): <b style="color: #38bdf8; font-size: 0.95rem;">₹{entry_premium:.2f}</b></span>
                        <span>Model Theo: <b style="color: #94a3b8;">₹{p_theo_curr:.2f}</b></span>
                        <span>Strike IV: <b style="color: #facc15;">{strike_iv * 100:.1f}%</b></span>
                        <span>Delta: <b style="color: #34d399;">{cur_greeks.delta:+.3f}</b></span>
                        <span>Theta: <b style="color: #f87171;">{cur_greeks.theta:+.2f} pts/d</b></span>
                        <span>Vega: <b style="color: #c084fc;">{cur_greeks.vega:+.2f} pts/%</b></span>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)

        # 1-SD horizon move for barrier probability calculation
        sigma_h = max(base_fut * strike_iv * np.sqrt(horizon_opt / (375.0 * 252.0)), 15.0)

        if "Target / SL" in calc_tab:
            # Preset default target & stop levels based on dominant scenario / target engine targets
            def_sc = "bull" if sel_type == "CE" else "bear"
            rec_tar = targets.get(def_sc)
            def_tgt_spot = rec_tar.target if rec_tar else (base_fut + 70.0 if sel_type == "CE" else base_fut - 70.0)
            def_sl_spot = rec_tar.stop if rec_tar else (base_fut - 40.0 if sel_type == "CE" else base_fut + 40.0)

            # Quick Preset Buttons
            p_b1, p_b2, p_b3, p_b4 = st.columns([2.5, 2.5, 2.5, 2.5])
            with p_b1:
                b_tgt_val = targets["bull"].target if "bull" in targets else base_fut + 60
                b_stop_val = targets["bull"].stop if "bull" in targets else base_fut - 35
                if st.button(f"🚀 Bull Preset ({b_tgt_val:,.0f} / {b_stop_val:,.0f})", key=f"btn_bull_{symbol}"):
                    st.session_state[f"inp_tgt_{symbol}"] = float(b_tgt_val)
                    st.session_state[f"inp_sl_{symbol}"] = float(b_stop_val)
            with p_b2:
                bear_tgt_val = targets["bear"].target if "bear" in targets else base_fut - 60
                bear_stop_val = targets["bear"].stop if "bear" in targets else base_fut + 35
                if st.button(f"🔻 Bear Preset ({bear_tgt_val:,.0f} / {bear_stop_val:,.0f})", key=f"btn_bear_{symbol}"):
                    st.session_state[f"inp_tgt_{symbol}"] = float(bear_tgt_val)
                    st.session_state[f"inp_sl_{symbol}"] = float(bear_stop_val)
            with p_b3:
                base_pin_val = targets["base"].target if "base" in targets else base_fut
                base_band_val = targets["base"].stop if "base" in targets else base_fut + 50
                if st.button(f"⚖️ Base / Range ({base_pin_val:,.0f})", key=f"btn_base_{symbol}"):
                    st.session_state[f"inp_tgt_{symbol}"] = float(base_pin_val)
                    st.session_state[f"inp_sl_{symbol}"] = float(base_band_val)
            with p_b4:
                if st.button("↺ Reset Defaults", key=f"btn_reset_{symbol}"):
                    st.session_state[f"inp_tgt_{symbol}"] = float(def_tgt_spot)
                    st.session_state[f"inp_sl_{symbol}"] = float(def_sl_spot)
                    st.session_state[strike_key] = atm_strike
                    st.session_state[strike_select_key] = atm_strike
                    st.session_state[f"wi_custom_strike_val_{symbol}"] = float(atm_strike)
                    st.rerun()

            col_tgt_in1, col_tgt_in2, col_tgt_in3, col_tgt_in4 = st.columns(4)
            with col_tgt_in1:
                user_tgt_spot = st.number_input(
                    "Target Spot Level",
                    value=float(st.session_state.get(f"inp_tgt_{symbol}", def_tgt_spot)),
                    step=10.0,
                    key=f"inp_tgt_{symbol}",
                    help="Underlying index level at which target is achieved",
                )
            with col_tgt_in2:
                user_sl_spot = st.number_input(
                    "Stop Loss Spot Level",
                    value=float(st.session_state.get(f"inp_sl_{symbol}", def_sl_spot)),
                    step=10.0,
                    key=f"inp_sl_{symbol}",
                    help="Underlying invalidation index level where position must be exited",
                )
            with col_tgt_in3:
                tgt_mins = st.slider(
                    "Holding Time (mins)",
                    min_value=5,
                    max_value=375,
                    value=horizon_opt,
                    step=15,
                    key=f"wi_tgt_time_{symbol}",
                    help="Estimated elapsed trading minutes to reach target or stop",
                )
            with col_tgt_in4:
                iv_regime_shock = st.selectbox(
                    "IV Scenario Shock",
                    options=[
                        "Auto (Regime-Conditioned)",
                        "Flat IV (0.0 pts)",
                        "Mild Vol Compression (-1.0 pts)",
                        "Strong Vol Compression (-2.5 pts)",
                        "Panic Vol Expansion (+2.0 pts)",
                        "Extreme Vol Squeeze (+4.0 pts)",
                    ],
                    index=0,
                    key=f"wi_iv_regime_{symbol}",
                )

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
                # Auto regime-conditioned:
                # CE target (upward) experiences vol compression, PE target (downward) experiences vol expansion
                if sel_type == "CE":
                    d_iv_tgt = -0.0075 if user_tgt_spot > base_fut else 0.010
                    d_iv_sl = 0.012 if user_sl_spot < base_fut else -0.005
                else:
                    d_iv_tgt = 0.015 if user_tgt_spot < base_fut else -0.010
                    d_iv_sl = -0.008 if user_sl_spot > base_fut else 0.010

            _, T_horizon = calculate_horizon_t(now_dt, tgt_mins, sel_expiry)
            iv_at_tgt = max(0.04, strike_iv + d_iv_tgt)
            iv_at_sl = max(0.04, strike_iv + d_iv_sl)

            # Target & Stop Black-76 repricing
            p_target = price(user_tgt_spot, sel_strike, T_horizon, iv_at_tgt, 0.065, sel_type)
            p_stop = price(user_sl_spot, sel_strike, T_horizon, iv_at_sl, 0.065, sel_type)

            gain_pts = p_target - entry_premium
            gain_pct = (gain_pts / entry_premium) * 100.0 if entry_premium > 0 else 0.0
            gain_inr = gain_pts * lot_sz

            loss_pts = entry_premium - p_stop
            loss_pct = (loss_pts / entry_premium) * 100.0 if entry_premium > 0 else 0.0
            loss_inr = loss_pts * lot_sz

            rr_ratio = (gain_pts / loss_pts) if loss_pts > 0.05 else (9.99 if gain_pts > 0 else 0.0)

            # Barrier reflection touch probabilities
            drift_tgt = 0.25 * sigma_h if sel_type == "CE" else -0.25 * sigma_h
            p_touch_target = compute_touch_probability(abs(user_tgt_spot - base_fut), sigma_h, drift_tgt)
            p_touch_sl = compute_touch_probability(abs(base_fut - user_sl_spot), sigma_h, -0.2 * sigma_h)

            attr_tgt = greek_attribution(base_fut, user_tgt_spot, strike_iv, iv_at_tgt, T_now, T_horizon, sel_strike, 0.065, sel_type)
            attr_sl = greek_attribution(base_fut, user_sl_spot, strike_iv, iv_at_sl, T_now, T_horizon, sel_strike, 0.065, sel_type)

            # Display Key Target / SL Outcome Cards
            st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)
            res_c1, res_c2, res_c3, res_c4 = st.columns(4)

            with res_c1:
                st.metric(
                    "Target Option Premium",
                    f"₹{p_target:.2f}",
                    delta=f"{gain_pts:+.2f} pts ({gain_pct:+.1f}%)",
                )
                st.caption(f"Target PnL / Lot: **₹{gain_inr:+,.0f}** · P(Touch): **{p_touch_target:.1%}**")

            with res_c2:
                st.metric(
                    "Stop Loss Option Premium",
                    f"₹{p_stop:.2f}",
                    delta=f"{-loss_pts:+.2f} pts ({-loss_pct:.1f}%)",
                    delta_color="inverse",
                )
                st.caption(f"Risk / Lot: **₹{-loss_inr:,.0f}** · P(Touch SL): **{p_touch_sl:.1%}**")

            with res_c3:
                rr_color = "normal" if rr_ratio >= 1.8 else "off"
                st.metric(
                    "Risk : Reward Ratio",
                    f"{rr_ratio:.2f} : 1",
                    delta="Favorable" if rr_ratio >= 1.8 else "Sub-Optimal",
                    delta_color=rr_color,
                )
                st.caption(f"Net Spread: **{gain_pts - loss_pts:+.1f} pts** · Breakeven: **{base_fut + (entry_premium if sel_type=='CE' else -entry_premium):,.1f}**")

            with res_c4:
                net_ev_approx = (p_touch_target * gain_inr) - (p_touch_sl * loss_inr)
                st.metric(
                    "Touch-Weighted EV / Lot",
                    f"₹{net_ev_approx:+,.0f}",
                    delta="Positive Expectancy" if net_ev_approx > 0 else "Negative Expectancy",
                )
                st.caption(f"Target IV: **{iv_at_tgt * 100:.1f}%** ({d_iv_tgt * 100:+.1f} pts)")

            # Detailed Greek Decomposition Table
            with st.expander("🔬 Greek Attribution Breakdown at Target & Stop Loss"):
                dec_df = pd.DataFrame([
                    {
                        "Phase": "🎯 Target Reach",
                        "Spot Move": f"{user_tgt_spot - base_fut:+.1f} pts",
                        "Full PnL": f"{gain_pts:+.2f} pts",
                        "Delta PnL": f"{attr_tgt.delta_pnl:+.2f}",
                        "Gamma PnL": f"{attr_tgt.gamma_pnl:+.2f}",
                        "Vega PnL": f"{attr_tgt.vega_pnl:+.2f}",
                        "Theta Bleed": f"{attr_tgt.theta_pnl:+.2f}",
                        "Residual": f"{attr_tgt.residual:+.2f}",
                    },
                    {
                        "Phase": "🛑 Stop Invalidation",
                        "Spot Move": f"{user_sl_spot - base_fut:+.1f} pts",
                        "Full PnL": f"{-loss_pts:+.2f} pts",
                        "Delta PnL": f"{attr_sl.delta_pnl:+.2f}",
                        "Gamma PnL": f"{attr_sl.gamma_pnl:+.2f}",
                        "Vega PnL": f"{attr_sl.vega_pnl:+.2f}",
                        "Theta Bleed": f"{attr_sl.theta_pnl:+.2f}",
                        "Residual": f"{attr_sl.residual:+.2f}",
                    },
                ])
                st.dataframe(dec_df, use_container_width=True, hide_index=True)

        else:
            # Mode B: Custom What-If Parameter Sliders
            wi_s1, wi_s2, wi_s3 = st.columns(3)
            with wi_s1:
                spot_shift_pts = st.slider(
                    "Spot Shift (pts)",
                    min_value=-400,
                    max_value=400,
                    value=50 if sel_type == "CE" else -50,
                    step=10,
                    key=f"wi_spot_{symbol}",
                )
            with wi_s2:
                iv_shift_pts = st.slider(
                    "IV Shift (percentage points)",
                    min_value=-8.0,
                    max_value=8.0,
                    value=0.0,
                    step=0.5,
                    key=f"wi_iv_{symbol}",
                )
            with wi_s3:
                elapsed_mins = st.slider(
                    "Time Elapsed (mins)",
                    min_value=0,
                    max_value=375,
                    value=60,
                    step=15,
                    key=f"wi_time_{symbol}",
                )

            _, T_future = calculate_horizon_t(now_dt, elapsed_mins, sel_expiry)
            new_fut = base_fut + spot_shift_pts
            new_iv = max(0.04, strike_iv + (iv_shift_pts / 100.0))

            p_repriced = price(new_fut, sel_strike, T_future, new_iv, 0.065, sel_type)
            attr_custom = greek_attribution(base_fut, new_fut, strike_iv, new_iv, T_now, T_future, sel_strike, 0.065, sel_type)
            custom_pnl_pts = p_repriced - entry_premium

            wi_res1, wi_res2, wi_res3, wi_res4 = st.columns(4)
            with wi_res1:
                st.metric("Current Premium", f"₹{entry_premium:.2f}")
            with wi_res2:
                st.metric(
                    "Repriced Premium",
                    f"₹{p_repriced:.2f}",
                    delta=f"{custom_pnl_pts:+.2f} pts ({(custom_pnl_pts / entry_premium)*100:+.1f}%)" if entry_premium > 0 else f"{custom_pnl_pts:+.2f} pts",
                )
            with wi_res3:
                st.metric("PnL per Lot", f"₹{custom_pnl_pts * lot_sz:+,.0f}")
            with wi_res4:
                st.metric(
                    "Greek Residual",
                    f"{attr_custom.residual:+.2f} pts",
                    help="Approximation error from non-linearities/gamma/vanna",
                )

            st.caption(
                f"Decomposition: Delta PnL **{attr_custom.delta_pnl:+.2f}** | "
                f"Gamma PnL **{attr_custom.gamma_pnl:+.2f}** | "
                f"Vega PnL **{attr_custom.vega_pnl:+.2f}** | "
                f"Theta Bleed **{attr_custom.theta_pnl:+.2f}** | "
                f"Residual **{attr_custom.residual:+.2f}**"
            )

    # 8. Diagnostics & Explain Drawer
    with st.expander("🔍 Diagnostics, Feature Contributions & Track Record"):
        d_col1, d_col2 = st.columns(2)
        with d_col1:
            st.markdown("**Directional Feature Contributions:**")
            contrib_df = pd.DataFrame([
                {"Feature": k, "Contribution": f"{v:+.4f}"}
                for k, v in sc_set.contributions.items()
            ])
            st.dataframe(contrib_df, use_container_width=True, hide_index=True)
        with d_col2:
            st.markdown("**Model & Quality Diagnostics:**")
            st.write(f"- **Model Version:** `{sc_set.model_version}`")
            st.write(f"- **Conviction Scalar C:** `{sc_set.conviction:.2f}`")
            st.write(f"- **Active Quality Flags:** `{res.feature_vector.quality_flags or ['None (Clean)']}`")
            st.write(f"- **Detected Call Walls:** `{[w.label for w in res.call_walls] or 'None'}`")
            st.write(f"- **Detected Put Walls:** `{[w.label for w in res.put_walls] or 'None'}`")
