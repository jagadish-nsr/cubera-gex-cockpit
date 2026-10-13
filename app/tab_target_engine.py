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

from target_engine.runner import run_target_engine
from app.data_access import load_dashboard
from target_engine.pricer import reprice, greek_attribution, greeks, price
from target_engine.time_utils import calculate_fractional_t, calculate_horizon_t, to_ist

# In-memory TTL cache for runner results: (symbol, window, horizon, mode) -> (ts, spot, result)
_TE_RUNNER_CACHE: dict[tuple, tuple[float, float, Any]] = {}


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
                    • P(Touch Pin): <b>{base_tar.p_touch_target:.1%}</b><br>
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

    # 6. Ranked Candidate Strategy Cards
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

    # 7. Interactive "What-If" Calculator
    st.markdown("### 🎛️ Interactive 'What-If' Option Repricer")
    st.caption("Evaluate theoretical option premium under custom spot, IV, and elapsed time shifts.")

    contracts_df = dash.contracts if hasattr(dash, "contracts") and isinstance(dash.contracts, pd.DataFrame) else None
    if contracts_df is not None and not contracts_df.empty:
        c_strikes = sorted(contracts_df["strike"].dropna().unique())
        atm_strike = min(c_strikes, key=lambda k: abs(k - dash.levels.spot))

        wi_c1, wi_c2, wi_c3, wi_c4 = st.columns(4)
        with wi_c1:
            sel_strike = st.selectbox("Strike", options=c_strikes, index=c_strikes.index(atm_strike), key=f"wi_strike_{symbol}")
        with wi_c2:
            sel_type = st.radio("Type", options=["CE", "PE"], horizontal=True, key=f"wi_type_{symbol}")
        with wi_c3:
            spot_shift_pts = st.slider("Spot Shift (pts)", min_value=-300, max_value=300, value=50, step=10, key=f"wi_spot_{symbol}")
        with wi_c4:
            iv_shift_pts = st.slider("IV Shift (points)", min_value=-5.0, max_value=5.0, value=0.0, step=0.5, key=f"wi_iv_{symbol}")

        elapsed_mins = st.slider("Time Elapsed (mins)", min_value=0, max_value=375, value=60, step=15, key=f"wi_time_{symbol}")

        # Compute repriced value
        now_dt = to_ist()
        expiry_str = str(dash.expiries[0])
        T_now = calculate_fractional_t(now_dt, expiry_str)
        _, T_future = calculate_horizon_t(now_dt, elapsed_mins, expiry_str)

        base_fut = float(getattr(dash.levels, "future_price", None) or dash.levels.spot)
        new_fut = base_fut + spot_shift_pts
        base_iv = float(getattr(dash.levels, "atm_iv", None) or 0.15)
        new_iv = max(0.04, base_iv + (iv_shift_pts / 100.0))

        p_curr = price(base_fut, sel_strike, T_now, base_iv, 0.065, sel_type)
        p_repriced = price(new_fut, sel_strike, T_future, new_iv, 0.065, sel_type)
        attr = greek_attribution(base_fut, new_fut, base_iv, new_iv, T_now, T_future, sel_strike, 0.065, sel_type)

        wi_res1, wi_res2, wi_res3, wi_res4 = st.columns(4)
        with wi_res1:
            st.metric("Current Premium", f"₹{p_curr:.2f}")
        with wi_res2:
            st.metric("Repriced Premium", f"₹{p_repriced:.2f}", delta=f"{p_repriced - p_curr:+.2f} pts")
        with wi_res3:
            lot_sz = 65 if "NIFTY" in symbol else 30
            st.metric("PnL per Lot", f"₹{(p_repriced - p_curr) * lot_sz:+,.0f}")
        with wi_res4:
            st.metric("Greek Residual", f"{attr.residual:+.2f} pts", help="Approximation error from non-linearities/gamma/vanna")

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
