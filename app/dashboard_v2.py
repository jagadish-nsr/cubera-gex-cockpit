"""Nifty GEX dashboard (v1a) — read-only. Run via run_app.bat or:
    python -m streamlit run app/dashboard.py

Shows spot, the 5 levels, regime, the one-sentence playbook, a net-GEX-by-strike
chart, and a caveats panel. Read-only: no trade suggestions, no journal yet.
Decision-support only, paper trading only.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datetime import timedelta  # noqa: E402

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from datetime import datetime
import pytz

import config  # noqa: E402
from app.data_access import WINDOW_CHOICES, load_dashboard  # noqa: E402


# ---------------------------------------------------------------------------
# Global CSS — glassmorphism cards, gradient badges, regime glow, dividers
# ---------------------------------------------------------------------------
st.markdown("""
<style>
/* ---- Google Font ---------------------------------------------------- */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* ---- Data-source adapter badge chip --------------------------------- */
.adapter-badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 999px;
    font-size: 0.78rem; font-weight: 600; letter-spacing: 0.02em;
    border: 1px solid rgba(255,255,255,0.15);
    backdrop-filter: blur(6px);
    margin-bottom: 4px;
}
.adapter-badge.dhan {
    background: linear-gradient(135deg, #92400e 0%, #f97316 100%);
    color: #fff7ed;
    box-shadow: 0 0 14px rgba(249,115,22,0.45);
}
.adapter-badge.nse {
    background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%);
    color: #dbeafe;
    box-shadow: 0 0 14px rgba(37,99,235,0.35);
}

/* ---- Source tag line below title ------------------------------------ */
.source-tag {
    display: inline-flex; align-items: center; gap: 8px;
    font-size: 0.75rem; color: rgba(255,255,255,0.55); margin-top: -8px;
}
.source-tag .dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: #22c55e; box-shadow: 0 0 6px #22c55e;
    animation: pulse-dot 2s ease-in-out infinite;
}
@keyframes pulse-dot {
    0%,100% { opacity: 1; transform: scale(1); }
    50%      { opacity: 0.5; transform: scale(1.4); }
}

/* ---- Glassmorphism metric cards ------------------------------------- */
[data-testid="metric-container"] {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 12px 16px;
    transition: border-color .2s, box-shadow .2s;
}
[data-testid="metric-container"]:hover {
    border-color: rgba(255,255,255,0.2);
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
}

/* ---- Gradient section dividers -------------------------------------- */
.grad-divider {
    height: 1px;
    background: linear-gradient(90deg,
        transparent 0%, rgba(99,102,241,0.6) 30%,
        rgba(139,92,246,0.6) 70%, transparent 100%);
    margin: 20px 0 10px 0;
    border: none;
}
.grad-divider-label {
    font-size: 0.7rem; font-weight: 600; letter-spacing: 0.12em;
    text-transform: uppercase; color: rgba(255,255,255,0.35);
    margin-bottom: 14px;
}

/* ---- Regime glow badge ---------------------------------------------- */
.regime-positive {
    display: inline-block; padding: 2px 14px; border-radius: 8px;
    background: rgba(34,197,94,0.12); color: #4ade80;
    border: 1px solid rgba(34,197,94,0.35);
    box-shadow: 0 0 12px rgba(34,197,94,0.2);
    font-weight: 600;
}
.regime-negative {
    display: inline-block; padding: 2px 14px; border-radius: 8px;
    background: rgba(239,68,68,0.12); color: #f87171;
    border: 1px solid rgba(239,68,68,0.35);
    box-shadow: 0 0 12px rgba(239,68,68,0.2);
    font-weight: 600;
}
.regime-flat {
    display: inline-block; padding: 2px 14px; border-radius: 8px;
    background: rgba(148,163,184,0.12); color: #94a3b8;
    border: 1px solid rgba(148,163,184,0.3);
    font-weight: 600;
}
</style>
""", unsafe_allow_html=True)

def is_market_open():
    """Return True if it is currently between 09:15 and 15:30 IST on a non-holiday weekday."""
    return config.is_market_open()



# Keep in sync with IMPACT_FLAG in scripts/analyze_timeseries.py (the day-level gauge).
_NEWS_IMPACT_FLAG = 4


def _run_once_per_session(key: str, fn):
    """Call fn() exactly once per Streamlit session (not per rerun)."""
    if not st.session_state.get(key):
        fn()
        st.session_state[key] = True


def _background_purge():
    """Purge old journal data in a daemon thread (fire-and-forget)."""
    import threading
    def _do():
        try:
            from data.storage import purge_old_data
            purge_old_data(months=2)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


def _smart_default_window(idx) -> str:
    """Return '0dte' if today (IST) is an expiry day for *idx*, else 'weekly'.

    For Bank Nifty (monthly-only since Nov-2024, ``has_weekly=False``), 'weekly'
    collapses to the next monthly expiry inside ``filter_expiry_window`` — so
    'weekly' is the correct default for Bank Nifty on non-expiry days too.

    Uses ``next_weekly_expiry`` / ``next_monthly_expiry`` (holiday-adjusted) to
    compare against today's IST date. Falls back to 'weekly' on any error so a
    stale holiday calendar never crashes the sidebar.
    """
    try:
        from datetime import datetime, timezone, timedelta
        from data.expiry import next_weekly_expiry, next_monthly_expiry

        ist_today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()

        # Is today an expiry day?
        if idx.has_weekly:
            next_exp = next_weekly_expiry(ist_today, idx.exchange)
        else:
            next_exp = next_monthly_expiry(ist_today, idx.exchange)

        if next_exp == ist_today:
            return "0dte"
        return "weekly"
    except Exception:  # noqa: BLE001 — holiday calendar miss → safe fallback
        return "weekly"


def _refresh(window: str, use_demo: bool, symbol: str):
    st.session_state.session_expired = False
    st.session_state.dhan_expired = False
    try:
        # Tab 2 is the India SCSP research lab — uses short_call_short_put convention
        st.session_state.data = load_dashboard(
            window=window, use_demo=use_demo, symbol=symbol,
            sign_model="short_call_short_put",
        )
        st.session_state.err = None
    except SessionExpiredError as e:
        st.session_state.err = str(e)
        st.session_state.session_expired = True
        st.rerun()
    except Exception as e:  # noqa: BLE001 - shown to the user, last-good kept
        if type(e).__name__ == "DhanCredentialError" or "401 Unauthorized" in str(e):
            st.session_state.err = str(e)
            st.session_state.dhan_expired = True
            st.rerun()
        else:
            st.session_state.err = str(e)




def render_positioning_divergence_tab(data):
    """Render the full Positioning Divergence analysis tab (Tab 2).

    Reuses chain data already loaded in session_state.data (zero extra API calls).
    Implements all sections from the positioning-divergence spec:
    §3.1 category biases, §3.2 rolling trend, §3.3 strike layer, §3.4 combined read,
    §6 non-negotiable caveats.
    """
    from analytics.bias import compute_category_biases, divergence_trend, interpret_divergence, rolling_divergence
    from analytics.strikes import (combine_divergence_with_strikes, max_call_oi_strike,
                                   max_put_oi_strike, pcr_from_chain, top_oi_change_strikes)
    from analytics.report import CAVEATS
    from app.participant_oi import capture_latest, latest_available

    # ── §6 Non-negotiable caveats (always top, never buried) ───────────────
    with st.expander("⚠️ Important caveats — please read before acting on this data", expanded=False):
        for caveat in CAVEATS:
            st.markdown(f"- {caveat}")

    st.markdown("### 📊 Positioning Divergence — Who's Long, Who's Short?")

    # ── Load participant OI ─────────────────────────────────────────────────
    got = None
    try:
        got = capture_latest()
    except Exception:
        pass
    if got is None:
        got = latest_available()

    if got is None:
        st.warning(
            "No Participant OI data found. NSE publishes this file after 4:30 PM IST. "
            "Run `scripts/log_participant_oi.py` or wait for it to appear in the journal."
        )
        return

    date_ist, parsed = got
    biases = compute_category_biases(parsed)
    
    from analytics.stock_futures_gauge import compute_gauge_metrics
    from app.participant_oi import available_days, load_participant_day
    
    days = available_days()
    today_idx = days.index(date_ist) if date_ist in days else len(days)
    yesterday_parsed = None
    if today_idx > 0:
        yesterday_parsed = load_participant_day(days[today_idx - 1])
        
    historical_pcts: dict[str, list[float]] = {"Client": [], "DII": [], "FII": [], "Pro": []}
    for d in days[:today_idx]:
        p = load_participant_day(d)
        if not p: continue
        tot_oi = sum((p.get(c, {}).get("fut_stk_long") or 0) for c in historical_pcts)
        if tot_oi > 0:
            for c in historical_pcts:
                if c in p:
                    nl = p[c].get("fut_stk_long") or 0
                    ns = p[c].get("fut_stk_short") or 0
                    historical_pcts[c].append((nl - ns) / tot_oi)
                    
    gauge_metrics = compute_gauge_metrics(parsed, yesterday_parsed, historical_pcts)


    # ── Section 1: Category Biases Scorecard ───────────────────────────────
    st.markdown(f"#### Category Biases — data as of **{date_ist}**")
    st.caption(
        "Bias score = Net Index Futures + Net Index Call − Net Index Put. "
        "Positive = net bullish positioning; negative = net bearish / hedged."
    )

    c1, c2, c3, c4 = st.columns(4)
    def _bias_delta(v):
        return "📈 Net bullish" if v > 0 else "📉 Net bearish"

    c1.metric("🧑‍💼 Client (Retail proxy)", f"{biases.get('Client', 0):+,.0f}",
              _bias_delta(biases.get('Client', 0)), delta_color="off")
    c2.metric("🏦 FII (Smart money)", f"{biases.get('FII', 0):+,.0f}",
              _bias_delta(biases.get('FII', 0)), delta_color="off")
    c3.metric("🏢 DII", f"{biases.get('DII', 0):+,.0f}",
              _bias_delta(biases.get('DII', 0)), delta_color="off")
    c4.metric("⚡ Pro (Prop desks)", f"{biases.get('Pro', 0):+,.0f}",
              _bias_delta(biases.get('Pro', 0)), delta_color="off")

    divergence = biases.get("divergence", 0)
    div_color  = "🔴" if divergence > 500_000 else "🟢" if divergence < -500_000 else "🟡"
    div_label  = ("Large positive — retail crowded long vs FII short" if divergence > 500_000
                  else "Large negative — retail short vs FII long" if divergence < -500_000
                  else "Neutral — no extreme divergence")
    st.metric(
        f"{div_color} Divergence (Client − FII)",
        f"{divergence:+,.0f}",
        div_label,
        delta_color="off",
        help="retail_bias − institutional_bias. Large positive = retail crowded long vs FII; "
             "large negative = short-squeeze setup."
    )

    # ── Section 2: Divergence Trend Chart ──────────────────────────────────
    series = rolling_divergence(n=20)
    t5  = divergence_trend(series, 5)
    t10 = divergence_trend(series, 10)
    t20 = divergence_trend(series, 20)

    if len(series) >= 2:
        st.markdown("#### 📈 Divergence Trend (last 20 sessions)")

        _trend_cols = st.columns(3)
        for col, (label, t) in zip(_trend_cols, [("5-day", t5), ("10-day", t10), ("20-day", t20)]):
            icon = "↗️" if t.get("trend") == "rising" else "↘️" if t.get("trend") == "falling" else "➡️"
            col.metric(f"{icon} {label} trend", t.get("trend", "—").capitalize(),
                       f"{t.get('drift', 0):+,.0f}/session" if t.get("n", 0) >= 2 else "insufficient data",
                       delta_color="off")

        try:
            import altair as alt
            _sdf = pd.DataFrame(series)
            _sdf["divergence_M"] = _sdf["divergence"] / 1_000_000  # display in millions

            base = alt.Chart(_sdf).encode(
                x=alt.X("date:O", title="Date", axis=alt.Axis(labelAngle=-45)),
            )
            div_line = base.mark_line(color="#f97316", strokeWidth=2.5).encode(
                y=alt.Y("divergence_M:Q", title="Divergence (millions)"),
                tooltip=["date", alt.Tooltip("divergence:Q", format=",", title="Divergence")],
            )
            div_points = base.mark_circle(color="#f97316", size=60).encode(
                y="divergence_M:Q",
            )
            zero_line = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
                color="rgba(255,255,255,0.3)", strokeDash=[4, 2]
            ).encode(y="y:Q")
            threshold_hi = alt.Chart(pd.DataFrame({"y": [0.5]})).mark_rule(
                color="rgba(239,68,68,0.4)", strokeDash=[6, 3]
            ).encode(y="y:Q")
            threshold_lo = alt.Chart(pd.DataFrame({"y": [-0.5]})).mark_rule(
                color="rgba(34,197,94,0.4)", strokeDash=[6, 3]
            ).encode(y="y:Q")

            chart = (div_line + div_points + zero_line + threshold_hi + threshold_lo).properties(
                height=260
            )
            st.altair_chart(chart, use_container_width=True)
            st.caption(
                "Orange line = Client−FII divergence. "
                "Red dashed = +500K threshold (retail crowded long). "
                "Green dashed = −500K threshold (short-squeeze zone)."
            )
        except Exception as _ce:
            st.caption(f"(chart unavailable: {_ce})")
    else:
        st.info(
            f"Only {len(series)} day(s) of participant OI data available. "
            "The trend chart needs at least 2 sessions. "
            "Run `scripts/backfill_daily.py --days 20` to seed history."
        )

    # ── Section 3: Bias Breakdown Table ────────────────────────────────────
    with st.expander("🔍 Full bias breakdown (net_fut / net_call / net_put by category)"):
        breakdown = biases.get("breakdown", {})
        if breakdown:
            rows = []
            for who in ("Client", "FII", "DII", "Pro"):
                bd = breakdown.get(who, {})
                rows.append({
                    "Category":  who,
                    "Net Fut Index": f"{bd.get('net_fut', 0):+,.0f}",
                    "Net Call Index": f"{bd.get('net_call', 0):+,.0f}",
                    "Net Put Index": f"{bd.get('net_put', 0):+,.0f}",
                    "Bias Score":   f"{bd.get('bias', 0):+,.0f}",
                })
            st.table(pd.DataFrame(rows))
            st.caption(
                "Bias = Net Fut + Net Call − Net Put. "
                "Negative net_put means net put-writer (bullish); positive net_put means net put-buyer (bearish)."
            )

    # ── Section 3.5: Stock Futures Risk-Appetite ───────────────────────────
    with st.expander("📊 Stock Futures Risk-Appetite (Confidence Filter)"):
        if gauge_metrics:
            g_rows = []
            for g in gauge_metrics:
                rank_str = str(g['percentile_rank_60d']) if isinstance(g['percentile_rank_60d'], int) else g['percentile_rank_60d']
                g_rows.append({
                    "Category": g["category"],
                    "Net Stock Fut": f"{g['net_fut_stock']:+,.0f}",
                    "% of Total OI": f"{g['pct_of_total_oi']*100:.1f}%",
                    "60d Percentile": rank_str,
                    "DoD Delta": f"{g['day_over_day_delta']:+,.0f}",
                })
            st.table(pd.DataFrame(g_rows))
            st.caption("Percentile rank uses a 60-session trailing window. FII gauge acts as a conviction filter; DII acts as hedging intensity.")
        else:
            st.info("No Stock Futures data available.")


    # ── Section 4: Strike-Level OI Analysis ────────────────────────────────
    st.markdown("#### 🎯 Strike-Level OI Analysis (from Dhan live chain)")

    chain_rows = []
    spot_val   = None

    # Use data.oi_strikes — the fully-enriched per-strike list already computed by
    # data_access.py (includes call_doi, put_doi, call_oi, put_oi for every strike).
    # This avoids an extra API call and uses the same data the GEX tab uses.
    per_strike = []
    if data is not None:
        spot_val = data.levels.spot if hasattr(data, "levels") else None
        if data.oi_strikes:
            per_strike = data.oi_strikes
            # Also reconstruct minimal chain_rows so pcr_from_chain works
            # (it needs type/oi/doi per contract, not per-strike aggregates)
            if hasattr(data, "contracts") and data.contracts is not None and not data.contracts.empty:
                try:
                    for _, row in data.contracts.iterrows():
                        chain_rows.append({
                            "type":   row.get("type", "C"),
                            "strike": float(row.get("strike", 0)),
                            "oi":     float(row.get("oi", 0) or 0),
                            "doi":    0.0,  # contracts schema doesn't store doi; pcr uses oi only
                        })
                except Exception:
                    chain_rows = []



    if per_strike:
        max_call = max_call_oi_strike(per_strike)
        max_put  = max_put_oi_strike(per_strike)
        top_doi  = top_oi_change_strikes(per_strike, n=3)
        pcr_data = pcr_from_chain(chain_rows) if chain_rows else {"pcr": float("nan")}

        sc1, sc2, sc3 = st.columns(3)
        if max_call:
            dist = (max_call["strike"] - spot_val) if spot_val else None
            sc1.metric(
                "🔴 Max Call OI (Resistance)",
                f"{max_call['strike']:,.0f}",
                f"{dist:+,.0f} pts" if dist is not None else "",
                delta_color="off",
                help=f"Strike with highest call OI: {max_call['call_oi']:,.0f} contracts",
            )
        if max_put:
            dist = (max_put["strike"] - spot_val) if spot_val else None
            sc2.metric(
                "🟢 Max Put OI (Support)",
                f"{max_put['strike']:,.0f}",
                f"{dist:+,.0f} pts" if dist is not None else "",
                delta_color="off",
                help=f"Strike with highest put OI: {max_put['put_oi']:,.0f} contracts",
            )
        pcr = pcr_data.get("pcr")
        if pcr is not None and pcr == pcr:  # not nan
            pcr_label = "put-heavy" if pcr > 1.3 else "call-heavy" if pcr < 0.7 else "neutral"
            sc3.metric("PCR (OI)", f"{pcr:.2f}", pcr_label, delta_color="off",
                       help="Put/Call OI ratio. >1.3 = heavy put buying; <0.7 = heavy call buying.")

        if top_doi:
            st.markdown("**Top 3 strikes by OI change (fresh money today)**")
            doi_rows = []
            for s in top_doi:
                direction_icon = {"call_build": "📈 Call build", "put_build": "📉 Put build",
                                  "mixed": "🔀 Mixed"}.get(s["direction"], s["direction"])
                doi_rows.append({
                    "Strike":     f"{s['strike']:,.0f}",
                    "Call ΔOI":  f"{s['call_doi']:+,.0f}",
                    "Put ΔOI":   f"{s['put_doi']:+,.0f}",
                    "Total |ΔOI|": f"{s['total_abs_doi']:,.0f}",
                    "Direction":  direction_icon,
                })
            st.table(pd.DataFrame(doi_rows))
            st.caption("ΔOI = today's OI − previous session's OI. Large positive = fresh build-up.")
    else:
        st.info(
            "Refresh the GEX tab first to load the option chain. "
            "Strike-level analysis reuses that data — no extra API calls needed."
        )
        max_call = max_put = pcr_data = None
        per_strike = []

    # ── Section 5: Narrative — Interpreted read ─────────────────────────────
    st.markdown("#### 📝 Positioning Read")

    narrative = interpret_divergence(
        divergence=biases.get("divergence", 0),
        retail_bias=biases.get("retail_bias", 0),
        institutional_bias=biases.get("institutional_bias", 0),
        trend_5d=t5,
        trend_10d=t10,
    )
    st.info(narrative)

    if per_strike and (max_call or max_put):
        strike_analysis = {
            "max_call": max_call, "max_put": max_put, "top_doi": top_doi,
            "pcr": pcr_data,
        }
        combined = combine_divergence_with_strikes(biases, strike_analysis, spot_val or 0)
        
        gauge_narratives = []
        for g in gauge_metrics:
            cat = g["category"]
            rank = g["percentile_rank_60d"]
            delta = g["day_over_day_delta"]
            is_extreme = isinstance(rank, int) and (rank >= 80 or rank <= 20)
            is_delta_large = abs(delta) > 50000
            
            if is_extreme or is_delta_large:
                if cat == "FII":
                    bias_word = "long" if g["net_fut_stock"] > 0 else "short"
                    tag = f"FII stock-futures net-{bias_word} at {rank}th percentile. Genuine risk-appetite proxy adds weight to the index-divergence caution."
                    gauge_narratives.append(tag)
                elif cat == "Client":
                    bias_word = "long" if g["net_fut_stock"] > 0 else "short"
                    tag = f"Retail stock-futures net-{bias_word} at {rank}th percentile — elevated leverage, adds weight to the index-divergence caution."
                    gauge_narratives.append(tag)
                elif cat == "DII" and is_delta_large:
                    tag = f"DII stock-futures {'unwound' if delta < 0 else 'added'} sharply today (delta: {delta:+,} vs. prior session) — possible hedge adjustment; note as context, not a standalone signal."
                    gauge_narratives.append(tag)
                    
        if gauge_narratives:
            combined += "\n\n**Stock Futures Risk-Appetite:**\n" + "\n".join(f"- {n}" for n in gauge_narratives)

        st.markdown("#### 🔗 Combined Positioning + Strike Read")
        st.success(combined)


    # ── Section 7: Data Provenance ──────────────────────────────────────────
    st.caption(
        f"📋 Participant OI: NSE clearing-corp file, as of **{date_ist}** (EOD, market-wide — not per-index). "
        f"Option chain: Dhan API, fetched at {data.fetched_at.strftime('%H:%M UTC') if data else '—'}. "
        "Decision-support only."
    )


def render_gex_scsp_tab():

    with st.expander("🔬 V2 Controls", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            # Index picker FIRST — it drives the symbol, lot size, expiry rules and the
            # per-index journals. Bank Nifty is monthly-only (no weeklies since Nov-2024).
            symbols = list(config.INDICES)
            symbol = st.selectbox("Index", symbols, key="_v2_index", index=symbols.index(config.DEFAULT_INDEX),
                format_func=lambda s: config.INDICES[s].label,
                help="Which index to read. Lot size, expiry rules and the captured journals are all per-index.",
            )
            idx = config.get_index(symbol)
            use_demo = st.checkbox("Use demo data (offline)", key="_v2_use_demo", value=False,
                help="Synthetic chain — runs without exchange data. NOT a market read.",
            )
        
        with col2:
            # Auto-select expiry window: 0dte on expiry day for this index, else weekly.
            _auto_window = _smart_default_window(idx)
            _auto_window_idx = WINDOW_CHOICES.index(_auto_window) if _auto_window in WINDOW_CHOICES else 0
            window = st.selectbox("Expiry window", WINDOW_CHOICES, key="_v2_window", index=_auto_window_idx,
                help="Auto-set: **0dte** when today is an expiry day for this index, **weekly** otherwise.",
            )
            refresh = st.button("🔄 Refresh", key="_v2_refresh", type="primary", use_container_width=True)
            save_ts = st.button("📈 Save snapshot", key="_v2_save_ts", use_container_width=True,
                help="Append the current levels to today's intraday series.",
            )
            
        with col3:
            auto = st.checkbox("⏱️ Auto-refresh", key="_v2_auto", value=False,
                help="Re-pull the chain on a timer (non-blocking).",
            )
            auto_mins = st.selectbox("Interval", [1, 3, 5, 15], key="_v2_auto_mins", index=3,
                format_func=lambda m: f"{m} min", disabled=not auto,
            )
            auto_save = st.checkbox("Save each tick to TS", key="_v2_auto_save", value=False, disabled=not auto)
            
        with col4:
            market_open = is_market_open()
            toggle_label = "🚀 Real-time Mode"
            live_feed = st.toggle(
                toggle_label,
                value=market_open,  # Defaults to ON during market hours, OFF otherwise
                help="Reads from the background daemon at regular intervals."
            )
            live_interval_sec = st.selectbox(
                "Live refresh interval",
                [5, 10, 15, 30, 60],
                index=1,  # default 10s
                format_func=lambda s: f"{s}s",
                disabled=not live_feed,
                help="How often the dashboard polls the background daemon for new data.",
            )
            # Data source badge
            _src_map = {
                "dhan":         ("dhan",  "🔶", "Dhan API"),
                "nse":          ("nse",   "📊", "NSE Scraper"),
            }
            _css_cls, _icon, _src_label = _src_map.get(config.DATA_SOURCE, ("nse", "📊", config.DATA_SOURCE))
            st.markdown(f'<div class="adapter-badge {_css_cls}" style="margin-top:10px">{_icon}&nbsp;{_src_label}</div>', unsafe_allow_html=True)
            st.caption("Active adapter")

    st.title(f"{idx.label} GEX — Dashboard")
    st.caption("Decision-support only — **NOT** financial advice. **Paper trading only.**")

    # Switching index must drop ALL cached data so the new index never shows
    # stale data from the previous one. The per-index snap path also changes.
    if symbol != st.session_state.get("last_symbol"):
        st.session_state["ts_points"] = {}
        st.session_state.pop("blast", None)
        st.session_state["last_symbol"] = symbol
        st.session_state["symbol_changed"] = True
        st.session_state["data"] = None   # CRITICAL: clear stale data from previous index
        st.session_state["err"] = None
    else:
        st.session_state["symbol_changed"] = False


    # Dhan credential missing/expired handler
    if config.DATA_SOURCE == "dhan":
        is_missing = not (config.DHAN_CLIENT_ID and config.DHAN_ACCESS_TOKEN)
        is_expired = st.session_state.get("dhan_expired", False)
        
        if is_missing or is_expired:
            if is_expired:
                st.error("⚠️ Dhan Access Token Expired! The JWT token expires exactly 24 hours after generation. Please generate a new one and enter it below.")
            else:
                st.error(
                    "⚠️ **Dhan credentials missing.** Set `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` "
                    "in your `.env` file to use the Dhan adapter."
                )
            st.markdown("[Generate new token here →](https://dhanhq.co/docs/latest/authentication/)")
            
            with st.form("dhan_token_form", clear_on_submit=True):
                new_token = st.text_input("New Dhan Access Token (JWT):", type="password")
                submitted = st.form_submit_button("✅ Update Token & Reconnect", type="primary")
                if submitted and new_token:
                    import os, re
                    os.environ["DHAN_ACCESS_TOKEN"] = new_token
                    config.DHAN_ACCESS_TOKEN = new_token
                    try:
                        env_path = str(ROOT / ".env")
                        with open(env_path, "r") as f:
                            env_content = f.read()
                        if "DHAN_ACCESS_TOKEN=" in env_content:
                            env_content = re.sub(
                                r'DHAN_ACCESS_TOKEN=.*',
                                f'DHAN_ACCESS_TOKEN={new_token}',
                                env_content
                            )
                        else:
                            env_content += f'\nDHAN_ACCESS_TOKEN={new_token}\n'
                        with open(env_path, "w") as f:
                            f.write(env_content)
                        st.success("Dhan Token saved to .env — daemon will reload automatically.")
                    except Exception as _env_err:
                        st.warning(f"Could not write .env ({_env_err}), running in-memory only.")
                    st.session_state.dhan_expired = False
                    st.session_state.err = None
                    st.session_state.data = None  # force a fresh fetch on next rerun
                    st.rerun()
                elif submitted and not new_token:
                    st.warning("Please enter a token before submitting.")
            st.stop()

    # Non-blocking auto-refresh timer (a new tick value => time to re-pull).
    tick = None
    if auto or live_feed:
        try:
            from streamlit_autorefresh import st_autorefresh
            # live_feed uses the user-selected interval (default 10s)
            # auto uses the auto_mins selectbox (minutes -> ms)
            interval_ms = live_interval_sec * 1_000 if live_feed else auto_mins * 60_000
            tick = st_autorefresh(interval=interval_ms, key="auto_tick")
        except ModuleNotFoundError:
            st.sidebar.warning(
                "Auto-refresh needs `streamlit-autorefresh` "
                "(`pip install streamlit-autorefresh`). Using manual refresh."
            )
            auto = False
            live_feed = False

    st.session_state.setdefault("data", None)
    st.session_state.setdefault("err", None)

    # Purge old journal data once per session (background, non-blocking)
    _run_once_per_session("purge_done", _background_purge)

    auto_fired = auto and tick is not None and tick != st.session_state.get("last_tick")
    live_fired = live_feed and tick is not None and tick != st.session_state.get("last_tick")
    if auto or live_feed:
        st.session_state["last_tick"] = tick

    if (refresh or auto_fired or live_fired or st.session_state.data is None
            or st.session_state.get("symbol_changed")):

        loaded_from_daemon = False
        if live_feed or live_fired:
            import pickle, os as _os, time as _time
            # Per-index snapshot path — daemon writes one file per index
            pkl_path = f"data/journal/latest_snap_{symbol}.pkl"
            # Accept snaps up to 3x the refresh interval before falling back
            max_age = live_interval_sec * 3
            if _os.path.exists(pkl_path) and _time.time() - _os.path.getmtime(pkl_path) < max_age:
                try:
                    with open(pkl_path, "rb") as f:
                        snap = pickle.load(f)
                    # Guard: snap must match the currently requested symbol and window
                    if snap["symbol"] == symbol and snap["window"] == window:
                        # snap["data"] is a DashboardData object written by the daemon
                        st.session_state.data = snap["data"]
                        st.session_state.err = None
                        loaded_from_daemon = True
                        snap_age = _time.time() - _os.path.getmtime(pkl_path)
                        st.session_state["snap_age"] = snap_age
                except Exception:
                    pass  # fall through to direct API call

        if not loaded_from_daemon:
            with st.spinner("Pulling the option chain & computing GEX…"):
                _refresh(window, use_demo, symbol)
                
    if auto:
        st.caption(f"⏱️ Auto-refreshing every {auto_mins} min.")
    elif live_feed:
        import os as _os, time as _time
        pkl_path = f"data/journal/latest_snap_{symbol}.pkl"
        if _os.path.exists(pkl_path):
            snap_age = _time.time() - _os.path.getmtime(pkl_path)
            snap_age_str = f"{snap_age:.0f}s ago"
            if snap_age < live_interval_sec * 2:
                st.caption(f"🚀 Real-time feed active — data updated {snap_age_str} (refreshing every {live_interval_sec}s)")
            else:
                st.warning(
                    f"⚠️ Daemon data for {symbol} is stale ({snap_age_str}). "
                    "Make sure `scripts/start_gex.sh` is running and the session token is valid."
                )
        else:
            if config.DATA_SOURCE in ("dhan", "nse") and symbol == "SENSEX":
                st.info(
                    f"ℹ️ {symbol} daemon feed is not supported by {config.DATA_SOURCE.upper()} adapter — loaded via direct API call."
                )
            elif st.session_state.err:
                st.warning(
                    f"⚠️ No daemon data found for {symbol} (`latest_snap_{symbol}.pkl` missing). "
                    "Make sure `scripts/start_gex.sh` is running. Direct API call also failed."
                )
            else:
                st.caption(
                    f"ℹ️ Loaded {symbol} via direct API call (`latest_snap_{symbol}.pkl` missing). "
                    "To enable background pre-fetching, ensure `scripts/start_gex.sh` is running."
                )

    if st.session_state.err:
        st.warning(f"Refresh failed: {st.session_state.err}")
        if st.session_state.data is not None:
            st.info(
                "Showing the last good data from "
                f"{st.session_state.data.fetched_at:%Y-%m-%d %H:%M} UTC."
            )

    data = st.session_state.data
    if data is None:
        st.stop()

    if save_ts:                                    # explicit click → always write
        if data.source != "DEMO-SYNTHETIC":
            from app.timeseries import (append_row, append_strike_rows,
                                        build_row, build_strike_rows)

            p = append_row(build_row(data), symbol=data.symbol)
            append_strike_rows(build_strike_rows(data), symbol=data.symbol)  # per-strike, same fetch
            st.sidebar.success(f"Saved {data.symbol} snapshot → {p.name}")
        else:
            st.sidebar.warning("Not saved — demo data is synthetic.")
    elif auto_save and auto_fired and data.source != "DEMO-SYNTHETIC":
        from app.timeseries import (append_if_due, append_strike_rows, build_row,
                                    build_strike_rows, in_nse_session)

        # Session guard: a dashboard left open after 15:30 IST keeps re-pulling
        # the SAME frozen post-close chain — without this it would auto-save
        # dozens of duplicate rows (spot frozen, stale=False) and poison the
        # day's file. Only the explicit "Save snapshot" button overrides this.
        if not in_nse_session(data.fetched_at):
            st.sidebar.caption("⏸️ Outside market hours — auto-save paused.")
        else:
            # Guard just below the tick interval so a co-running capture loop (or
            # a double-fire) doesn't double up, without blocking the app's cadence.
            p, written = append_if_due(build_row(data), min_gap_minutes=max(1.0, auto_mins - 1.0),
                                       symbol=data.symbol)
            if written:
                append_strike_rows(build_strike_rows(data), symbol=data.symbol)  # lock-step
                st.sidebar.success(f"Saved {data.symbol} snapshot → {p.name}")

    # Accumulate each in-session live pull so the intraday chart persists across
    # auto-refresh reruns (session_state survives reruns) and the line grows --
    # independent of whether anything is being written to disk. Skip post-close
    # pulls so frozen end-of-day points don't flat-line the chart.
    if data.source not in ("DEMO-SYNTHETIC", "nse") or data.source == "nse":
        from app.timeseries import build_row, in_nse_session

        if in_nse_session(data.fetched_at):
            r = build_row(data)
            st.session_state.setdefault("ts_points", {})[r["timestamp_utc"]] = r

    L, V = data.levels, data.view


    # --- Source / provenance tag line under title -------------------------
    _adapter_labels = {
        "dhan":            "🔶 Dhan API",
        "nse":             "📊 NSE Scraper",
        "DEMO-SYNTHETIC":  "🧪 Demo (synthetic)",
    }
    _adapter_label = _adapter_labels.get(data.source, data.source)
    st.markdown(
        f'<div class="source-tag">'
        f'<span class="dot"></span>'
        f'{_adapter_label}&nbsp;&nbsp;·&nbsp;&nbsp;'
        f'{data.symbol}&nbsp;&nbsp;·&nbsp;&nbsp;'
        f'window: {data.window}&nbsp;&nbsp;·&nbsp;&nbsp;'
        f'fetched {data.fetched_at.strftime("%H:%M UTC")}'
        f'</div>',
        unsafe_allow_html=True,
    )

    # GEX pin-strength conditioning (computed once; shown as a header tag near the
    # magnet AND in the research expander). Live data only.
    pin_stats = (_pin_strength_stats(data.symbol, abs(float(L.total_net_gex)))
                 if data.source != "DEMO-SYNTHETIC" else None)

    if L.dealer_sign_model == "short_call_short_put":
        st.error(
            "⚠️ **INDIAN MARKET MODE ACTIVE (Short Call / Short Put)**\n\n"
            "The standard US 'Positive Regime' is inverted. Institutional dealers are assumed to be "
            "short gamma on BOTH calls and puts. This means the market is structurally in a "
            "**Negative (Volatile) Regime**. Walls act as breakout triggers (gamma blasts) rather than mean-reverting ceilings."
        )

    # --- Market mode (plain language, prominent) ---------------------------
    box = st.info
    if V.mode_label.startswith("RANGE-BOUND"):
        box = st.success
    elif V.mode_label.startswith("TRENDING"):
        box = st.warning
    elif V.mode_label == "CAUTION":
        box = st.error
    box(f"### Market mode: {V.mode_label}\n\n{V.mode_plain}")
    # The calm-vs-wild claim in that label hinges on the dealer sign.
    # We are now natively using the Indian short_call_short_put mode.
    # _sign_calm_wild_note(V)    # --- Market regime (SIGN-FREE, from dealer-gamma concentration) --------
    _gex_regime_banner(data)
    # Turn the regime into a concrete trading POSTURE (mean-revert vs momentum, sizing, stops).
    _regime_posture_note(data)
    # Sign-free, time-of-day magnet-pull overlay — our strongest directional-ish read.
    _magnet_pull_note(data, L)
    # Live GEX pin-strength tag: is the magnet likely to hold today (high gamma) or not?
    _pin_strength_tag(pin_stats)

    st.caption(
        "The mode label above reads behaviour (calm vs wild) off the Indian dealer sign. "
        "The magnet line is **sign-free**. "
        f"Technical read: {V.headline}"
    )

    # Expiry-day flag — gamma effects (pin / blast) are strongest today.
    nearest = data.expiries[0] if data.expiries else None
    today_ist = (data.fetched_at + timedelta(hours=5, minutes=30)).date()
    is_expiry = (nearest == today_ist) if nearest else False
    if is_expiry:
        st.warning("⚡ **EXPIRY DAY** — gamma is at its strongest. Positive gamma → "
                   "strong pin toward the magnet/max pain; a break of a wall can trigger "
                   "a **gamma blast**. See 'What trade fits this mode?' below.")
    if data.source == "DEMO-SYNTHETIC":
        st.info("DEMO data is **synthetic** — do not trade on it.")

    # --- Key metrics -------------------------------------------------------
    _idx = config.get_index(data.symbol)
    today_ist = (data.fetched_at + timedelta(hours=5, minutes=30)).date()
    from data.expiry import describe_window, next_weekly_expiry, next_monthly_expiry
    window_desc = describe_window(
        data.window, today_ist,
        exchange=_idx.exchange,
        has_weekly=_idx.has_weekly,
    )
    # The primary expiry being analyzed — first in the sorted list
    active_expiry = data.expiries[0] if data.expiries else None
    days_to_expiry = (active_expiry - today_ist).days if active_expiry else None
    dte_label = (
        "TODAY" if days_to_expiry == 0
        else f"{days_to_expiry}d away"
        if days_to_expiry is not None else ""
    )

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Spot", f"{L.spot:,.2f}")
    c2.metric("Regime", L.regime)
    c3.metric("Total net GEX", f"{L.total_net_gex:,.0f}")
    # Active expiry date — the most important single piece of context
    c4.metric(
        f"📅 Expiry ({data.window})",
        active_expiry.strftime("%d-%b") if active_expiry else "—",
        dte_label,
        delta_color="off",
        help=f"Active window: {window_desc}. "
             f"Contracts used: {data.n_contracts} across {len(data.expiries)} expiry date(s).",
    )
    ov = data.oi_volume or {}
    pcr = ov.get("pcr_oi")
    if pcr is not None and pcr == pcr:   # not NaN
        # >1 = more put OI (put-heavy); <1 = call-heavy. Exploratory, sign-free.
        c5.metric("PCR (OI)", f"{pcr:.2f}",
                  help="Put/Call open interest across the returned expiries. "
                       ">1 put-heavy, <1 call-heavy. Independent of the dealer-sign "
                       "assumption; exploratory.")
    else:
        c5.metric("PCR (OI)", "—", help="Put/Call OI; live data only.")
    vix = data.india_vix
    if vix:
        chg = vix.get("pct_change")
        c6.metric("India VIX", f"{vix['last']:.2f}",
                  f"{chg:+.2f}%" if chg is not None else None, delta_color="off")
    else:
        c6.metric("India VIX", "—", help="Implied vol; live data only, best-effort.")
    usdinr = data.usdinr
    if usdinr:
        uchg = usdinr.get("pct_change")
        c7.metric("USD/INR (Fut)", f"{usdinr['last']:.4f}",
                  f"{uchg:+.2f}%" if uchg is not None else None, delta_color="off",
                  help="Near-month USD/INR future — the rupee level; live data, best-effort.")
    else:
        c7.metric("USD/INR (Fut)", "—",
                  help="Near-month USD/INR future; live data only, best-effort.")

    # --- Simple levels (position-aware: a wall above price isn't "support") --
    st.markdown("#### Simple levels")

    def _wall(price, kind):
        above = price > L.spot
        if kind == "put":
            emoji = "🟢" if not above else "🟠"
            desc = "support (below price)" if not above else "now OVERHEAD — price fell below it"
        else:
            emoji = "🔴" if above else "🟠"
            desc = "resistance (above price)" if above else "BELOW price — broke above it"
        return emoji, desc

    pe, p_desc = _wall(L.put_wall, "put")
    ce, c_desc = _wall(L.call_wall, "call")
    s1, s2, s3 = st.columns(3)
    s1.metric(f"{pe} Put wall", f"{L.put_wall:,.0f}", f"{L.put_wall - L.spot:+,.0f}", delta_color="off")
    s1.caption(p_desc)
    s2.metric(f"{ce} Call wall", f"{L.call_wall:,.0f}", f"{L.call_wall - L.spot:+,.0f}", delta_color="off")
    s2.caption(c_desc)
    s3.metric("🧲 Magnet (pull)", f"{L.magnet:,.0f}", f"{L.magnet - L.spot:+,.0f}", delta_color="off")

    lo, hi = min(L.put_wall, L.call_wall), max(L.put_wall, L.call_wall)
    flip_txt = f"{L.flip:,.0f}" if L.flip else "the flip"
    if L.spot < lo:
        st.caption(
            f"⚠️ Price is **below both walls** — the nearest dealer levels "
            f"(**{lo:,.0f}, {hi:,.0f}**) are **overhead**. In a calm (positive-gamma) "
            f"regime price often gets pulled **up** toward them; but a break below the "
            f"flip (**{flip_txt}**) can turn it into a downside trend."
        )
    elif L.spot > hi:
        st.caption(
            f"⚠️ Price is **above both walls** (**{lo:,.0f}, {hi:,.0f}**) — extended; "
            "dealers lean against further upside."
        )
    else:
        st.caption(
            f"Likely band when range-bound: **{lo:,.0f} – {hi:,.0f}** ({hi - lo:,.0f} pts "
            "wide). Stops go just **beyond** the walls, never at them."
        )

    for note in _structure_notes(L):
        st.warning(note)

    # News-intensity (free RSS, best-effort). A burst of high-impact headlines means
    # the move is likely exogenous — GEX levels become secondary (headlines override).
    news = data.news
    if news and (news.get("count") or news.get("top")):
        impact, count, top = news.get("impact") or 0, news.get("count") or 0, news.get("top") or ""
        if impact >= _NEWS_IMPACT_FLAG:
            st.warning(
                f"📰 **Elevated news flow** — {count} fresh headline(s), impact {impact}. "
                "Treat GEX levels as **secondary**; an exogenous catalyst can override the "
                f"dealer map." + (f"\n\nLatest: _{top}_" if top else "")
            )
        elif top:
            st.caption(f"📰 News: {count} fresh headline(s), impact {impact}. Latest: {top}")

    # --- Levels table ------------------------------------------------------
    def d(x):
        return f"{x - L.spot:+,.0f}" if x is not None else "—"

    rows = [
        ("🧱 Call wall", f"{L.call_wall:,.0f}", d(L.call_wall), V.call_wall_role),
        ("🧱 Put wall", f"{L.put_wall:,.0f}", d(L.put_wall), V.put_wall_role),
        ("🔀 Gamma flip", f"{L.flip:,.0f}" if L.flip else "NONE", d(L.flip), "calm⇄wild switch"),
        ("🧲 Magnet", f"{L.magnet:,.0f}", d(L.magnet), "peak GEX"),
        ("🎯 Max pain", f"{L.max_pain:,.0f}", d(L.max_pain), "expiry pin"),
    ]
    st.table(pd.DataFrame(rows, columns=["Level", "Price", "Δ from spot (pts)", "Role"]))

    # --- What trade fits this mode? (illustrative, unvalidated) -----------
    _trade_explainer(L, V, data, is_expiry)

    # --- Net-GEX-by-strike chart ------------------------------------------
    _gex_chart(L)

    # --- Dealer-positioning trend (multi-day magnet drift, sign-free) ------
    _magnet_drift_section(data, pin_stats)

    # --- Positioning footprint (per-strike ΔOI build, sign-free) ----------
    _oi_footprint_section(data)

    # --- Dhan greeks panel (delta/gamma/theta/vega/IV from Dhan API) -------
    _dhan_greeks_section(data)

    # --- Intraday mini build-up streaks (3× green/red per strike) ----------
    _buildup_streaks_section(data)

    # --- FUTURES OI build-up (daily bhavcopy + intraday, sign-free) --
    _futures_buildup_section(data)

    # --- Big players: participant-wise OI (EOD, market-wide, sign-free) ----
    _participant_oi_section(data)

    # --- Morning magnet-pull IC + live GEX conditioning (research) --------
    _ic_readout(data, pin_stats)

    # --- Gamma-blast crossover watch (expiry-day; parity-based, sign-free) --
    _gamma_blast_section(data, is_expiry)

    # --- Intraday time-series (Phase A) -----------------------------------
    _timeseries_section(data)

    # --- PCR Trend Analytics (separate tab) --------------------------------
    _pcr_trend_section(data)


    with st.expander("Assumptions & caveats (please read)", expanded=True):
        _idx2 = config.get_index(data.symbol)
        all_exp_str = ", ".join(e.strftime("%d-%b-%Y") for e in data.expiries) if data.expiries else "none"
        st.markdown(
            f"- **Index:** {_idx2.label} (`{data.symbol}`) — "
            + ("WEEKLY + monthly expiries." if _idx2.has_weekly
               else "**MONTHLY-ONLY** (no weeklies since Nov-2024).") + "\n"
            f"- **Lot size:** {_idx2.lot_size} — *VERIFY against the current exchange circular.*\n"
            f"- **Dealer-sign model:** `{L.dealer_sign_model}` — **Indian Market Mode Active.**\n"
            f"- **Expiry weighting:** `{L.expiry_weighting}` — {'all expiries weighted equally.' if L.expiry_weighting == 'equal' else 'near-dated walls weighted up.'}\n"
            f"- **Expiry window:** `{L.expiry_window}` → **{window_desc}** · "
            f"{data.n_contracts} contracts · {len(data.expiries)} expiry date(s): {all_exp_str}\n"
            f"- **OI timestamp:** {L.oi_timestamp} (stale={L.oi_stale}) — OI is end-of-day lagging.\n"
            f"- **Source:** {data.source} · fetched {data.fetched_at:%Y-%m-%d %H:%M} UTC"
        )
        for f in L.flags:
            st.markdown(f"- ⚠️ {f}")

    st.divider()
    st.caption(
        "A map and a logbook, not an oracle. You decide and confirm every trade. Paper only."
    )


def _sign_calm_wild_note(V):
    """Reframe the mode label's calm-vs-wild claim, which hinges on the UNVALIDATED
    dealer sign. Our own both-signs test + a sign-free PCR cross-check LEAN toward
    the inverted India sign (see docs/dealer-sign-validation.md), under which the
    calm/wild reading flips: a NEGATIVE-gamma read becomes the CALMER state, a
    POSITIVE-gamma read the wilder one. Show both, lead with the lean, never assert
    (the lean is not statistically settled)."""
    if V.regime not in ("POSITIVE", "NEGATIVE"):
        return
    if V.regime == "NEGATIVE":
        textbook, inverted = "TRENDING / volatile", "CALM / range-bound"
    else:
        textbook, inverted = "CALM / range-bound", "TRENDING / volatile"
    st.warning(
        "⚖️ **Calm-vs-wild here depends on the *unvalidated* dealer sign — and our "
        "data leans the inverted way.**\n\n"
        f"- US textbook sign → **{textbook}** (what the label above assumes).\n"
        f"- Inverted India sign → **{inverted}**.\n\n"
        f"Our accumulated captures (both-signs test + a sign-free PCR check) **lean "
        f"toward the inverted read**, so this is *more likely* **{inverted}** than the "
        "label suggests — but it is **not statistically settled**. Don't commit to "
        "calm-vs-wild; the walls/flip and the sign-free magnet line below are the "
        "safer guide. Details: `docs/dealer-sign-validation.md`."
    )


_MORNING_CUTOFF_MIN = 12 * 60   # matches MORNING_CUTOFF_MIN in scripts/analyze_ic.py


def _magnet_pull_note(data, L):
    """Sign-free, time-of-day magnet-pull line for the market-mode header.

    The mode label is built on the UNVALIDATED dealer sign. The magnet (peak
    |net_gex| strike) is dealer-sign-INVARIANT, and our IC analysis
    (scripts/analyze_ic.py) found the magnet pull predicts the next hour in the
    MORNING (IC +0.45) and fades after noon — so this is the most landmine-immune
    read of the day. Exploratory (n=11, war-confounded); a map, never a live signal.
    """
    from app.timeseries import in_nse_session

    magnet = L.magnet
    if magnet is None:
        return
    ist = data.fetched_at + timedelta(hours=5, minutes=30)
    minute = ist.hour * 60 + ist.minute
    gap = magnet - L.spot                         # +ve => magnet sits above spot
    tol = max(L.spot * 0.001, 1.0)                # within ~0.1% of spot => "pinned"
    direction = "up" if gap > 0 else "down"
    where = f"**{magnet:,.0f}** ({gap:+,.0f} pts {'above' if gap > 0 else 'below'} spot)"
    in_sess = data.source == "nse" and in_nse_session(data.fetched_at)

    if abs(gap) <= tol:
        st.info(f"🧲 **Price is sitting at the magnet ({magnet:,.0f}).** The pin is "
                "already in — little directional pull either way from here. "
                "*(Magnet is sign-free; exploratory.)*")
    elif not in_sess:
        st.caption(f"🧲 Magnet at {where}. The magnet-pull guide applies *during* the "
                   "market session (strongest before noon IST) — sign-free, exploratory.")
    elif minute < _MORNING_CUTOFF_MIN:
        st.success(
            f"🌅 **Morning ({ist:%H:%M} IST) — pull toward the magnet at {where}.** "
            "Our IC analysis found the **morning** magnet-pull predicts the next hour "
            "(IC **+0.45**, **sign-free** — independent of the unvalidated dealer "
            f"sign), so price has a modest historical tendency to drift **{direction}** "
            "toward the magnet this session. Exploratory (n=11, war-confounded) — a "
            "map, **not a live signal**."
        )
    else:
        st.info(
            f"🕑 **Afternoon ({ist:%H:%M} IST) — magnet at {where}.** Our IC analysis "
            "found the magnet-pull **fades after noon** (afternoon IC ≈ 0), so don't "
            "lean on the pull now — weight the walls / flip below instead. "
            "*(Sign-free, exploratory.)*"
        )


def _structure_notes(L) -> list[str]:
    """Plain-language flags for UNUSUAL wall/magnet geometry (inverted sandwich,
    magnet outside the wall range). These configurations carry meaning that the
    'support below / resistance above' shorthand misses. Reading assumes the
    dealer-sign model (UNVALIDATED for Nifty), so they're framed as tendencies.
    """
    notes: list[str] = []
    lo, hi = min(L.put_wall, L.call_wall), max(L.put_wall, L.call_wall)
    neg = L.regime == "NEGATIVE"

    # 1. Inverted sandwich: heaviest puts sit ABOVE heaviest calls.
    if L.put_wall > L.call_wall:
        msg = (
            f"⚠️ **Inverted walls — put wall ({L.put_wall:,.0f}) is ABOVE the call wall "
            f"({L.call_wall:,.0f}).** The normal support-below / resistance-above sandwich "
            "is flipped: the heaviest puts are **overhead**, typical of a **falling / "
            "bearish structure** where price has slipped below the put concentration. "
            "Don't read the put wall as a floor here"
        )
        msg += (
            " — in this NEGATIVE regime, dealer hedging at that strike **amplifies** moves "
            "rather than cushioning them." if neg else
            ", treat it as overhead supply until reclaimed."
        )
        notes.append(msg)

    # 2. Magnet (strongest pin) sitting OUTSIDE the wall range.
    if L.magnet > hi:
        notes.append(
            f"🧲 **Magnet ({L.magnet:,.0f}) is above both walls** — the strongest pin sits "
            "above the wall band, i.e. gamma is **top-heavy** (call-dominated up high). "
            + ("In this POSITIVE regime price tends to get pulled **up** toward it."
               if not neg else
               "In this NEGATIVE regime that pull is weak — treat the magnet as an "
               "unreliable target, not a destination.")
        )
    elif L.magnet < lo:
        notes.append(
            f"🧲 **Magnet ({L.magnet:,.0f}) is below both walls** — the strongest pin sits "
            "below the wall band, i.e. gamma is **bottom-heavy**. "
            + ("In this POSITIVE regime price tends to get pulled **down** toward it."
               if not neg else
               "In this NEGATIVE regime that pull is weak — an unreliable target.")
        )
    return notes


def _trade_explainer(L, V, data, is_expiry_day=False):
    """Illustrative regime->structure mapping. NOT a validated recommendation."""
    from structures.suggest import suggest_structure

    nearest = data.expiries[0] if data.expiries else None
    cs = suggest_structure(L, V, lot_size=config.get_index(data.symbol).lot_size,
                           nearest_expiry=nearest, is_expiry_day=is_expiry_day)

    with st.expander("💡 What trade fits this mode?  (ILLUSTRATIVE — not validated, paper only)",
                     expanded=False):
        st.markdown(f"**{cs.name}**")
        if cs.legs:
            legs_txt = "   ·   ".join(
                f"{a} {k:,} {'CE' if t == 'C' else 'PE'}" for a, t, k in cs.legs
            )
            st.code(legs_txt, language=None)
        if cs.rationale:
            st.markdown(f"- **Why:** {cs.rationale}")
        if cs.risk_note and cs.risk_note != "—":
            st.markdown(f"- **Defined risk:** {cs.risk_note}")
        st.markdown(f"- **Exit:** {cs.exit_by}")
        if cs.expiry_note:
            st.info(f"⚡ {cs.expiry_note}")
        st.warning(
            "Illustrative textbook structure from the rules — the signal is **NOT "
            "validated for Nifty** (the dealer-sign could even be inverted). Do **not** "
            "trade this; for learning / paper practice only. You decide everything."
        )



def _pcr_trend_section(data):
    """PCR Trend Analytics — today's Put/Call Ratio across multiple time intervals.

    Uses app/pcr_trend.py (no Streamlit import there) for data loading and
    resampling. Merges on-disk snapshots (written by the scheduler or manual save)
    with live session_state points so the chart updates on every auto-refresh tick.

    India-specific note: High PCR = heavy put-writing = bullish structural support.
    This is the OPPOSITE of the US fear-gauge convention.
    """
    from app.pcr_trend import (
        build_pcr_chart,
        load_pcr_today,
        pcr_bias_label,
        pcr_summary,
    )

    st.markdown("---")
    st.markdown("### 📈 PCR Trend Analytics — Today")

    # ------------------------------------------------------------------ #
    # Controls row
    # ------------------------------------------------------------------ #
    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        sel_intervals = st.multiselect(
            "Intervals",
            options=[5, 15, 45],
            default=[5, 15, 45],
            format_func=lambda m: f"{m} min",
            help="Resample the raw 5-min captures into these buckets. "
                 "5-min = raw; 15 / 45-min = smoothed.",
            key="_v2_pcr_intervals",
        )
    with c2:
        show_full = st.checkbox("Full-chain PCR (solid)", value=True,
                                key="_v2_pcr_show_full",
                                help="PCR across all expiries returned by the feed.")
        show_near = st.checkbox("Near-ATM proxy PCR (dashed)", value=True,
                                key="_v2_pcr_show_near",
                                help="PCR from the 20 strikes closest to spot — "
                                     "front-expiry dominated. Shown only when "
                                     "oi_strikes data is available.")
    with c3:
        show_bands = st.checkbox("Reference bands (0.8 / 1.2)", value=True,
                                 key="_v2_pcr_bands",
                                 help="Green = put-writing support (PCR ≥ 1.2). "
                                      "Red = call-writing resistance (PCR ≤ 0.8). "
                                      "India convention (high PCR = bullish).")

    # ------------------------------------------------------------------ #
    # Load data: merge disk + live session points + current fetch
    # ------------------------------------------------------------------ #
    live_pts = dict(st.session_state.get("ts_points", {}))
    df = load_pcr_today(data.symbol, live_points=live_pts)

    # If we have no timeseries rows at all but we DO have a live pcr_oi
    # from the current fetch (e.g. demo mode or first run), inject it as
    # a single "now" point so the chart and metrics show something useful.
    if df.empty or df["pcr_oi"].isna().all():
        _live_pcr = (data.oi_volume or {}).get("pcr_oi")
        if _live_pcr is not None and _live_pcr == _live_pcr:   # not NaN
            from datetime import timezone as _tz
            _fa = data.fetched_at
            if _fa.tzinfo is None:
                _fa = _fa.replace(tzinfo=_tz.utc)
            from datetime import timedelta as _td
            _ist = _fa + _td(hours=5, minutes=30)
            _row = {
                "timestamp_utc": _fa.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "time_ist":      _ist.strftime("%H:%M"),
                "date_ist":      _ist.strftime("%Y-%m-%d"),
                "spot":          round(data.levels.spot, 2),
                "pcr_oi":        round(float(_live_pcr), 4),
                "pcr_near":      float("nan"),
            }
            import pandas as _pd
            df = _pd.DataFrame([_row])


    stats = pcr_summary(df)
    n_pts = stats["n_points"]

    m1, m2, m3, m4 = st.columns(4)
    def _fmt(v, nd=3):
        return f"{v:.{nd}f}" if v is not None else "—"

    delta_v = stats["delta_from_open"]
    delta_s = f"{delta_v:+.3f}" if delta_v is not None else None

    m1.metric("Current PCR (OI)",
              _fmt(stats["current_pcr"]),
              delta=delta_s,
              help="Latest full-chain PCR vs session open.")
    m2.metric("Session High", _fmt(stats["session_high"]))
    m3.metric("Session Low",  _fmt(stats["session_low"]))
    m4.metric("Session Open", _fmt(stats["session_open"]))

    # Bias label
    bias_label, bias_colour = pcr_bias_label(stats["current_pcr"])
    last_t = stats.get("last_time") or "—"
    st.markdown(
        f'<div style="border-left:4px solid {bias_colour};'
        f'background:{bias_colour}18;border-radius:8px;'
        f'padding:8px 14px;margin:4px 0 8px;font-weight:600;'
        f'color:{bias_colour};font-size:14px;">'
        f'🇮🇳&nbsp; {bias_label} &nbsp;·&nbsp; as of {last_t} IST</div>',
        unsafe_allow_html=True,
    )

    # ------------------------------------------------------------------ #
    # Chart
    # ------------------------------------------------------------------ #
    if df.empty or n_pts == 0:
        st.info(
            f"📊 No PCR snapshots captured for **{data.symbol}** today yet.  \n"
            "The chart grows as you refresh. Enable **⏱️ Auto-refresh** or run  \n"
            "`python scripts/log_timeseries.py --symbol " + data.symbol + "` to capture intraday snapshots."
        )

    else:
        intervals_to_show = sel_intervals or [5]
        try:
            chart = build_pcr_chart(
                df,
                intervals=intervals_to_show,
                show_full_chain=show_full,
                show_near_atm=show_near,
                show_bands=show_bands,
                chart_height=340,
            )
            if chart is not None:
                st.altair_chart(chart, use_container_width=True)

                # Legend note
                legend_parts = []
                colour_map = {5: "🔵 5m", 15: "🟣 15m", 45: "🩷 45m"}
                for iv in intervals_to_show:
                    legend_parts.append(colour_map.get(iv, f"{iv}m"))
                st.caption(
                    f"**{' · '.join(legend_parts)}** — solid = full-chain PCR · "
                    "dashed = near-ATM proxy  · "
                    f"🟢 horizontal = put-writing support (PCR {1.2}) · "
                    f"🔴 horizontal = call-writing resistance (PCR {0.8}).  "
                    f"*{n_pts} snapshot(s) loaded.*"
                )
            else:
                st.caption("(PCR chart unavailable — no data points in selected intervals)")
        except Exception as e:  # noqa: BLE001 - chart is non-essential
            st.caption(f"(PCR chart error: {e})")
            st.dataframe(df[["time_ist", "pcr_oi", "pcr_near", "spot"]].tail(20),
                         use_container_width=True)

    # ------------------------------------------------------------------ #
    # India interpretation note
    # ------------------------------------------------------------------ #
    with st.expander("🇮🇳 India PCR interpretation (read before acting)", expanded=False):
        st.markdown(
            "**India is a writer-dominated market — the PCR signal is INVERTED vs the US.**\n\n"
            "| Reading | India convention | US convention (does NOT apply here) |\n"
            "|---|---|---|\n"
            "| PCR > 1.2 | Heavy **put-writing** → structural **support** (bullish) | "
            "Fear / bearish hedging |\n"
            "| PCR < 0.8 | Heavy **call-writing** → structural **resistance** (bearish) | "
            "Complacency / bullish |\n"
            "| PCR ~1.0 | Balanced writing, no directional lean | Same |\n\n"
            "**Source:** NSE Nifty literature (MDPI Economies, 2001–2013). "
            "Full-chain PCR covers all expiries served by the feed; near-ATM proxy "
            "sums OI for the 20 strikes closest to spot (front-expiry dominated). "
            "Both are sign-free and exploratory. Gaps in the chart mean no snapshot "
            "was captured in that window — run the scheduler or use auto-save.\n\n"
            "*Decision-support only. Not a trade rule.*"
        )


def _timeseries_section(data):
    """Phase A: spot vs the migrating levels through the day, + OI/volume build-up."""

    from app.timeseries import available_days, load_day

    ist_today = (data.fetched_at + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    # Merge today's on-disk captures (scheduler / saved) with the points pulled
    # this browser session (held in session_state so they survive auto-refresh
    # reruns), deduped by timestamp -> the line persists and grows. Per-index.
    disk = load_day(ist_today, data.symbol)
    sess = pd.DataFrame(list(st.session_state.get("ts_points", {}).values()))
    if not sess.empty and "date_ist" in sess.columns:
        sess = sess[sess["date_ist"] == ist_today]
    frames = [f for f in (disk, sess) if not f.empty]
    label = ist_today
    if frames:
        df = (pd.concat(frames, ignore_index=True)
              .drop_duplicates(subset="timestamp_utc")
              .sort_values("timestamp_utc"))
    else:
        days = available_days(data.symbol)
        if not days:
            st.markdown("#### Intraday time-series")
            st.caption(
                f"No {data.symbol} snapshots yet. Points accumulate here as you refresh "
                "(live data); they also persist to disk if you enable **Save each tick** "
                f"(sidebar) or run `python scripts/log_timeseries.py --symbol {data.symbol}`."
            )
            return
        label = days[-1]            # fall back to the most recent captured day
        df = load_day(label, data.symbol)

    st.markdown(f"#### Intraday time-series — {label}")
    try:
        import altair as alt

        levels = ["spot", "magnet", "call_wall", "put_wall", "flip"]
        long = df.melt(
            id_vars=["time_ist"], value_vars=[c for c in levels if c in df.columns],
            var_name="level", value_name="price",
        )
        long["price"] = pd.to_numeric(long["price"], errors="coerce")
        long = long.dropna(subset=["price"])
        colors = {"spot": "#000000", "magnet": "#9467bd", "call_wall": "#d62728",
                  "put_wall": "#2ca02c", "flip": "#1f77b4"}
        levels_chart = alt.Chart(long).mark_line(point=True).encode(
            x=alt.X("time_ist:N", title="IST", sort="ascending"),
            y=alt.Y("price:Q", title="Level", scale=alt.Scale(zero=False)),
            color=alt.Color(
                "level:N",
                scale=alt.Scale(domain=list(colors), range=list(colors.values())),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=["time_ist", "level", "price"],
        )
        chart, vix_note = levels_chart, ""
        if "india_vix" in df.columns:                       # overlay VIX (right axis)
            vdf = df[["time_ist", "india_vix"]].copy()
            vdf["india_vix"] = pd.to_numeric(vdf["india_vix"], errors="coerce")
            vdf = vdf.dropna(subset=["india_vix"])
            if not vdf.empty:
                vix_line = alt.Chart(vdf).mark_line(point=True, strokeDash=[4, 2], color="#ff7f0e").encode(
                    x=alt.X("time_ist:N", sort="ascending"),
                    y=alt.Y("india_vix:Q", title="India VIX (%)", scale=alt.Scale(zero=False)),
                    tooltip=["time_ist", "india_vix"],
                )
                chart = alt.layer(levels_chart, vix_line).resolve_scale(y="independent")
                vix_note = " Orange dashed = India VIX (right axis)."
        st.altair_chart(chart, use_container_width=True)
        st.caption(
            "Spot vs the migrating dealer levels through the day — black=spot, "
            "purple=magnet, red=call wall, green=put wall, blue=flip." + vix_note
        )
    except Exception as e:  # noqa: BLE001 - chart is non-essential
        st.caption(f"(time-series chart unavailable: {e})")
        st.dataframe(df, width="stretch")

    _oi_buildup_caption(df)
    _news_day_caption(df)


def _news_day_caption(df):
    """Day-level news read for the time-series section: a NEWS-DOMINATED badge (same
    gauge as scripts/analyze_timeseries.py) + the day's highest-impact headline. Flags
    when the day's move was likely exogenous, so the GEX / dealer-sign read is
    confounded — the dashboard mirror of the offline quarantine."""
    if df.empty or "news_impact" not in df.columns:
        return
    imp = pd.to_numeric(df["news_impact"], errors="coerce")
    if not imp.notna().any():
        return
    peak = float(imp.max())
    top = ""
    if "news_top" in df.columns:
        pos = int(imp.fillna(-1).to_numpy().argmax())
        val = df["news_top"].iloc[pos]
        top = "" if pd.isna(val) else str(val)
    if peak >= _NEWS_IMPACT_FLAG:
        st.warning(
            f"📰 **News-dominated day** (peak headline impact {peak:.0f}). The day's move "
            "was likely driven by an exogenous catalyst, so the GEX / dealer-sign read is "
            "**confounded** — this day is quarantined from sign-validation."
            + (f"\n\nTop headline: _{top}_" if top else "")
        )
    else:
        st.caption(
            f"📰 News this day: peak headline impact {peak:.0f} "
            f"(below the {_NEWS_IMPACT_FLAG} flag — not news-dominated)."
            + (f" Top: {top}" if top else "")
        )


def _oi_buildup_caption(df):
    """OI / volume build-up from the latest captured row (exploratory)."""
    if df.empty or "pcr_oi" not in df.columns:
        return
    last = df.iloc[-1]

    def fmt(key, nd=0):
        try:
            return f"{float(last.get(key)):,.{nd}f}"
        except (TypeError, ValueError):
            return "—"

    st.caption(
        f"**OI / volume build-up (latest {last.get('time_ist', '')} IST):** "
        f"PCR(OI) {fmt('pcr_oi', 2)} · "
        f"Call ΔOI {fmt('call_doi')} / Put ΔOI {fmt('put_doi')} · "
        f"Call vol {fmt('call_vol')} / Put vol {fmt('put_vol')}. "
        "*ΔOI is vs the previous close — put OI building = support forming, "
        "call OI building = resistance forming. Exploratory, across the returned "
        "expiries; not yet validated.*"
    )


_ANCHOR_TIME_IST = (13, 45)   # freeze the anchors at 1:45pm IST (the folklore time)


def _pin_fmt(x):
    return f"{x:+.2f}" if x == x else "n/a"      # NaN-safe IC formatter


def _pin_strength_stats(symbol, cur_gamma):
    """GEX (gamma-magnitude) pin-strength conditioning, computed once and shared by
    the header tag + the research expander. Splits captured days by day-open
    |net GEX| (sign-free) at the median, computes the HIGH vs LOW-gamma morning
    magnet-pull IC, and flags whether TODAY's |net GEX| clears the threshold.
    Returns a dict, or None if too few days / on any error (non-essential)."""
    try:
        from scripts.analyze_ic import spearman_ic
        from scripts.analyze_multifactor import (build_mf_samples,
                                                 day_gamma_magnitude)
        from scripts.analyze_timeseries import load_all

        days = load_all(symbol)
        gm = day_gamma_magnitude(days)
        if len(gm) < 4:
            return None
        thr = float(pd.Series(list(gm.values())).median())
        mf = build_mf_samples(days, 60.0)
        morning = mf[mf["min_ist"] < 12 * 60]
        hi = {d for d, v in gm.items() if v > thr}
        lo = {d for d, v in gm.items() if v <= thr}
        ic_hi, _, n_hi = spearman_ic(morning[morning["day"].isin(hi)], "gex")
        ic_lo, _, n_lo = spearman_ic(morning[morning["day"].isin(lo)], "gex")
        return {"thr": thr, "ic_hi": ic_hi, "ic_lo": ic_lo, "n_hi": n_hi, "n_lo": n_lo,
                "cur": cur_gamma, "strong": cur_gamma > thr,
                "gm": dict(gm)}   # per-day |net GEX| for the magnet-trend pin tags
    except Exception:  # noqa: BLE001 - research overlay is non-essential
        return None


def _pin_strength_tag(stats):
    """Compact live strong/weak-pin tag for the main header (right by the magnet)."""
    if not stats:
        return
    s = stats
    if s["strong"]:
        st.success(
            f"🎯 **Strong-pin regime today** — |net GEX| {s['cur']:,.0f} is ABOVE the "
            f"~{s['thr']:,.0f} threshold, where the morning magnet-pull has historically "
            f"been stronger (IC {_pin_fmt(s['ic_hi'])} vs {_pin_fmt(s['ic_lo'])} in low-gamma). "
            "The pin/magnet is more likely to hold. Sign-free, exploratory.")
    else:
        st.info(
            f"🎯 **Weak-pin regime today** — |net GEX| {s['cur']:,.0f} is BELOW the "
            f"~{s['thr']:,.0f} threshold; the magnet-pull has historically been weaker here "
            f"(IC {_pin_fmt(s['ic_lo'])} vs {_pin_fmt(s['ic_hi'])} in high-gamma). Lean less on "
            "the magnet. Sign-free, exploratory.")


def _ic_readout(data, stats=None):
    """Historical research readout: morning vs afternoon Information Coefficient of
    the magnet-pull signal over ALL captured days, PLUS the live GEX pin-strength
    conditioning (``stats`` from _pin_strength_stats). Backward-looking, sign-free;
    NOT a live signal. See scripts/analyze_ic.py / analyze_multifactor.py."""
    with st.expander("🧪 Morning magnet-pull IC + GEX conditioning (research)", expanded=True):
        try:
            from scripts.analyze_ic import (build_samples, session_split,
                                            spearman_ic)
            from scripts.analyze_timeseries import load_all

            samples = build_samples(load_all(data.symbol), horizon_min=60.0)
            if samples.empty or samples["day"].nunique() < 3 or len(samples) < 20:
                st.caption("Accruing — needs a few more captured days before the IC "
                           "is worth showing. Run `python scripts/analyze_ic.py` later.")
                return
            morning, afternoon = session_split(samples)
            rm, pm, nm = spearman_ic(morning, "magnet_gap")
            ra, pa, na = spearman_ic(afternoon, "magnet_gap")
            c1, c2 = st.columns(2)
            c1.metric("Morning IC (<12:00)", f"{rm:+.2f}",
                      f"p={pm:.3f} · n={nm}", delta_color="off")
            c2.metric("Afternoon IC (≥12:00)", f"{ra:+.2f}",
                      f"p={pa:.3f} · n={na}", delta_color="off")
            st.caption(
                f"IC = rank corr( magnet-gap at t , next-60-min return ), over "
                f"{samples['day'].nunique()} captured days. **These are IN-SAMPLE**; "
                "leave-one-day-out the honest morning IC is **~+0.27** (see "
                "`analyze_multifactor.py`). The pin has historically predicted the next "
                "hour in the **morning**, not the afternoon. **Sign-free**, exploratory, "
                "war-confounded — **not a live trade signal**."
            )

            if stats:
                st.markdown("**🎯 Pin strength — GEX conditioning (live):**")
                g1, g2 = st.columns(2)
                g1.metric("Today |net GEX|", f"{stats['cur']:,.0f}",
                          "HIGH-gamma → strong pin" if stats["strong"] else "LOW-gamma → weak pin",
                          delta_color="off")
                g2.metric("Strong-pin threshold", f"> {stats['thr']:,.0f}",
                          f"morning IC {_pin_fmt(stats['ic_hi'])} vs {_pin_fmt(stats['ic_lo'])}",
                          delta_color="off")
                st.caption(
                    f"On captured days, the morning magnet-pull is stronger when |net GEX| is "
                    f"**above ~{stats['thr']:,.0f}** (median split): IC **{_pin_fmt(stats['ic_hi'])}** "
                    f"(n={stats['n_hi']}) vs **{_pin_fmt(stats['ic_lo'])}** (n={stats['n_lo']}) below. "
                    f"**Today = {'HIGH-gamma, pin STRONGER' if stats['strong'] else 'LOW-gamma, pin WEAKER'}.** "
                    "In-sample median on a few war-confounded days; |net GEX| scale drifts with the "
                    "OI build — directional, not precise."
                )
        except Exception as e:  # noqa: BLE001 - research readout is non-essential
            st.caption(f"(IC readout unavailable: {e})")


def _gamma_blast_section(data, is_expiry):
    """Expiry-day crossover watch on two FIXED strikes (magnet + 1:45pm ATM).

    By put-call parity the anchored strike's call/put premiums cross exactly when
    spot crosses that strike — a breakout pivot that, near expiry, can 'gamma
    blast'. Plus a gamma-concentration strip (candidate blast strikes). Live data
    only; parity-based and SIGN-FREE; decision-support, no directional claim.
    """
    from app.gammablast import (blast_score, crossover_state, detect_crossovers,
                                gamma_concentration, next_strike, pick_anchors,
                                strike_oi, strike_premiums)

    st.markdown("#### ⚡ Gamma-blast crossover watch")
    if not data.oi_strikes:
        st.caption("Per-strike premiums aren't available.")
        return

    L = data.levels
    ist = data.fetched_at + timedelta(hours=5, minutes=30)
    ist_today = ist.strftime("%Y-%m-%d")
    ist_min = ist.hour * 60 + ist.minute
    anchor_min = _ANCHOR_TIME_IST[0] * 60 + _ANCHOR_TIME_IST[1]

    blast = st.session_state.get("blast")
    if not blast or blast.get("date") != ist_today or blast.get("symbol") != data.symbol:
        blast = {"date": ist_today, "symbol": data.symbol, "anchors": None, "points": {}}
        st.session_state["blast"] = blast

    col_a, col_b = st.columns([3, 1])
    with col_b:
        if st.button("📍 Anchor now", key="_v2_anchor", help="Freeze the magnet & ATM strikes at the "
                     "current moment (overrides the 1:45pm auto-anchor)."):
            blast["anchors"] = {**pick_anchors(data.oi_strikes, L.spot, L.magnet),
                                "at": ist.strftime("%H:%M")}
        if st.button("🔊 Test Alert Sound", key="_v2_test_alert", help="Play the crossover alert beep."):
            _play_alert_beep()
    # Auto-anchor at 1:45pm IST if not set yet (only in-session, live).
    if blast["anchors"] is None and ist_min >= anchor_min:
        blast["anchors"] = {**pick_anchors(data.oi_strikes, L.spot, L.magnet),
                            "at": ist.strftime("%H:%M")}

    anchors = blast["anchors"]
    with col_a:
        if anchors is None:
            st.info(f"Anchors freeze automatically at **{_ANCHOR_TIME_IST[0]}:"
                    f"{_ANCHOR_TIME_IST[1]:02d} IST** (now {ist:%H:%M}); or press "
                    "**Anchor now**. The strikes are then held FIXED so a crossover "
                    "means spot crossing that level.")
        else:
            tag = " — ⚡ expiry day" if is_expiry else " (strongest on expiry day)"
            st.caption(f"Anchored at **{anchors['at']} IST**{tag}. "
                       "Crossover = spot crossing the strike (put-call parity).")

    # Accumulate this pull's anchored-strike premiums (survives auto-refresh reruns).
    if anchors:
        mc, mp = strike_premiums(data.oi_strikes, anchors["magnet"])
        ac, ap = strike_premiums(data.oi_strikes, anchors["atm"])
        mco, mpo = strike_oi(data.oi_strikes, anchors["magnet"])
        aco, apo = strike_oi(data.oi_strikes, anchors["atm"])
        blast["points"][ist.strftime("%H:%M")] = {
            "magnet_call": mc, "magnet_put": mp, "atm_call": ac, "atm_put": ap,
            "magnet_coi": mco, "magnet_poi": mpo, "atm_coi": aco, "atm_poi": apo}

        cols = st.columns(2)
        _blast_anchor_block(cols[0], "🧲 Magnet", anchors["magnet"], blast["points"],
                            "magnet", crossover_state, detect_crossovers,
                            data.oi_strikes, blast_score, next_strike, strike_oi)
        _blast_anchor_block(cols[1], "🎯 ATM @1:45", anchors["atm"], blast["points"],
                            "atm", crossover_state, detect_crossovers,
                            data.oi_strikes, blast_score, next_strike, strike_oi)

    # Gamma-concentration strip: where the gamma energy is piling up near spot.
    conc = gamma_concentration(data.oi_strikes, L.spot)
    if conc:
        st.markdown("**Gamma concentration near spot** (candidate blast strikes — "
                    "biggest |gamma exposure|; whether they pin or blast depends on the "
                    "*unvalidated* dealer sign):")
        cdf = pd.DataFrame([{
            "Strike": f"{c['strike']:,.0f}", "Δ spot": f"{c['dist']:+,.0f}",
            "Net GEX": f"{c['net_gex']:,.0f}",
            "Call ΔOI": _fmt_num(c["call_doi"]), "Put ΔOI": _fmt_num(c["put_doi"]),
        } for c in conc])
        st.table(cdf)
    st.caption("Parity-based and **sign-free**; it flags *where* and *when* a pivot "
               "happens, not the direction. Decision-support only — you confirm.")

    # ATM Tracking Section (Greeks, Volume, OI Deltas)
    _render_atm_tracking(data, L)

def _play_alert_beep():
    """Play a loud siren-like alert using HTML5 Web Audio API."""
    import streamlit.components.v1 as components
    js_code = """
    <script>
    try {
        var ctx = new (window.AudioContext || window.webkitAudioContext)();
        var osc = ctx.createOscillator();
        var gainNode = ctx.createGain();
        
        // Square wave for a jarring, critical alert sound
        osc.type = 'square';
        
        var t = ctx.currentTime;
        osc.frequency.setValueAtTime(600, t);
        
        // Alternating siren frequency every 0.3 seconds
        for (var i = 0; i < 12; i++) {
            osc.frequency.setValueAtTime(800, t + (i * 0.3) + 0.15);
            osc.frequency.setValueAtTime(600, t + (i * 0.3) + 0.3);
        }

        // Increase volume (0.8) and taper at the end to prevent popping
        gainNode.gain.setValueAtTime(0.8, t);
        gainNode.gain.linearRampToValueAtTime(0.8, t + 3.4);
        gainNode.gain.linearRampToValueAtTime(0.0, t + 3.6);
        
        osc.connect(gainNode);
        gainNode.connect(ctx.destination);
        
        osc.start(t);
        osc.stop(t + 3.6);
    } catch(e) {}
    </script>
    """
    components.html(js_code, height=0)


def _render_atm_tracking(data, L):
    import streamlit as st
    import pandas as pd
    from app.gammablast import atm_strike

    st.markdown("#### 🔍 Real-Time ATM Options Tracking")
    if not data.oi_strikes:
        st.caption("No per-strike data available.")
        return

    strikes = [s.get("strike") for s in data.oi_strikes]
    atm = atm_strike(strikes, L.spot)
    if atm is None:
        return

    st.caption(f"Tracking exact ATM Strike: **{atm:,.0f}**")

    # Find the row in contracts dataframe for the ATM call and put
    df = data.contracts
    atm_calls = df[(df["strike"] == atm) & (df["type"] == "C")]
    atm_puts = df[(df["strike"] == atm) & (df["type"] == "P")]

    if atm_calls.empty or atm_puts.empty:
        return

    call_row = atm_calls.iloc[0]
    put_row = atm_puts.iloc[0]

    # Track previous state to compute intraday deltas
    prev_state = st.session_state.get("atm_prev_state", {})
    
    c_oi = call_row.get("oi", 0)
    p_oi = put_row.get("oi", 0)
    c_vol = call_row.get("volume", 0)
    p_vol = put_row.get("volume", 0)

    # Compute deltas
    c_doi = c_oi - prev_state.get("call_oi", c_oi)
    p_doi = p_oi - prev_state.get("put_oi", p_oi)
    c_dvol = c_vol - prev_state.get("call_vol", c_vol)
    p_dvol = p_vol - prev_state.get("put_vol", p_vol)

    # Save current state for next refresh
    st.session_state["atm_prev_state"] = {
        "call_oi": c_oi, "put_oi": p_oi,
        "call_vol": c_vol, "put_vol": p_vol
    }

    # Format table
    table_data = [
        {"Side": "Call", "LTP": call_row.get("mid"), "IV": call_row.get("iv"), "Gamma": call_row.get("gamma"), 
         "OI": c_oi, "ΔOI": c_doi, "Volume": c_vol, "ΔVolume": c_dvol},
        {"Side": "Put", "LTP": put_row.get("mid"), "IV": put_row.get("iv"), "Gamma": put_row.get("gamma"), 
         "OI": p_oi, "ΔOI": p_doi, "Volume": p_vol, "ΔVolume": p_dvol},
    ]
    st.table(pd.DataFrame(table_data))
def _blast_anchor_block(col, name, strike, points, key, crossover_state, detect_crossovers,
                        oi_strikes=None, blast_score=None, next_strike=None, strike_oi=None):
    """One anchored strike: current crossover state, a TRUE-vs-FAKE blast score once
    a crossover fires, and a call-vs-put premium chart."""
    series = [{"time_ist": t, "call": v.get(f"{key}_call"), "put": v.get(f"{key}_put")}
              for t, v in sorted(points.items())]
    latest = next((s for s in reversed(series)
                   if s["call"] is not None and s["put"] is not None), None)
    with col:
        if strike is None:
            col.caption(f"{name}: no strike."); return
        col.markdown(f"**{name} — {strike:,.0f}**")
        if latest is None:
            col.caption("waiting for premiums…"); return
        state = crossover_state(latest["call"], latest["put"])
        emoji = {"at_cross": "🔶", "call_rich": "🔼", "put_rich": "🔽"}.get(state["side"], "")
        col.metric(f"{emoji} Call / Put", f"{latest['call']:.1f} / {latest['put']:.1f}",
                   f"{state['diff']:+.1f}", delta_color="off")
        col.caption(state["label"])
        evs = detect_crossovers(series)
        if evs:
            last = evs[-1]
            col.success(f"✅ Crossover at **{last['time_ist']}** "
                        f"({last['from'].replace('_', ' ')} → {last['to'].replace('_', ' ')})"
                        + (f"  ·  {len(evs)} total today" if len(evs) > 1 else ""))
            
            # Beep if a new crossover happened
            prev_evs = st.session_state.get(f"crossovers_{key}", 0)
            if len(evs) > prev_evs:
                _play_alert_beep()
            st.session_state[f"crossovers_{key}"] = len(evs)

            if blast_score is not None:
                _blast_score_readout(col, strike, points, key, last, oi_strikes,
                                     blast_score, next_strike, strike_oi)
        _blast_chart(col, series)


def _blast_score_readout(col, strike, points, key, last_cross, oi_strikes,
                         blast_score, next_strike, strike_oi):
    """Grade the LATEST crossover TRUE (sustaining) vs FAKE (pin/cap) from the anchored
    strike's OI covering + premium expansion since the crossing, plus the wall one strike
    ahead (current snapshot). Sign-free staying-power read — not a direction call."""
    direction = "up" if last_cross["to"] == "call_rich" else "down"
    # Window: stored points from the crossover time forward (covering builds after the cross).
    win = [v for t, v in sorted(points.items()) if t >= last_cross["time_ist"]]
    crossed = [{"call_oi": v.get(f"{key}_coi"), "put_oi": v.get(f"{key}_poi"),
                "call_ltp": v.get(f"{key}_call"), "put_ltp": v.get(f"{key}_put")} for v in win]
    ahead = None
    if next_strike is not None and strike_oi is not None:
        nb = next_strike(oi_strikes, strike, direction)
        nco, npo = strike_oi(oi_strikes, nb)      # current wall size (single snapshot)
        if nb is not None:
            ahead = [{"call_oi": nco, "put_oi": npo, "call_ltp": None, "put_ltp": None}]
    sc = blast_score(direction, crossed, ahead)
    if not sc:
        return
    icon = "🚀" if sc["grade"].startswith("TRUE") else "🎯" if "pin/cap" in sc["grade"] \
        else "🛡️" if "defending" in sc["grade"] else "❓"
    
    # Beep if score breaches 60 for the first time
    prev_score = st.session_state.get(f"blast_score_{key}", 0)
    if sc['score'] >= 60 and prev_score < 60:
        _play_alert_beep()
    st.session_state[f"blast_score_{key}"] = sc['score']

    pe = f"{sc['prem_exp']*100:+.0f}%" if sc["prem_exp"] is not None else "—"
    wr = f"{sc['wall_ratio']:.1f}×" if sc["wall_ratio"] is not None else "—"
    col.markdown(f"{icon} **Blast score {sc['score']}/100** — {sc['grade']}")
    col.caption(f"{sc['itm_side']}-writers ΔOI {sc['cover_frac']*100:+.0f}% "
                f"(−ve = covering = fuel) · premium {pe} · wall ahead {wr}× the strike. "
                "Sign-free staying-power grade, not a direction call — you confirm.")


def _blast_chart(col, series):
    """Call (red) vs Put (green) premium over time for one anchored strike."""
    try:
        import altair as alt

        df = pd.DataFrame(series).dropna(subset=["call", "put"])
        if len(df) < 2:
            return
        long = df.melt(id_vars=["time_ist"], value_vars=["call", "put"],
                       var_name="leg", value_name="premium")
        chart = alt.Chart(long).mark_line(point=True).encode(
            x=alt.X("time_ist:N", title="IST", sort="ascending"),
            y=alt.Y("premium:Q", title="Premium", scale=alt.Scale(zero=False)),
            color=alt.Color("leg:N", scale=alt.Scale(domain=["call", "put"],
                            range=["#d62728", "#2ca02c"]),
                            legend=alt.Legend(title=None, orient="top")),
            tooltip=["time_ist", "leg", "premium"],
        )
        col.altair_chart(chart, use_container_width=True)
    except Exception as e:  # noqa: BLE001 - chart is non-essential
        col.caption(f"(chart unavailable: {e})")


def _fmt_num(x, nd=0):
    try:
        return f"{float(x):,.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _magnet_drift_section(data, stats=None):
    """Multi-day magnet drift = the sign-free dealer-positioning TREND. Compares the
    last few days' daily magnets (from the journal) with today's point-in-time
    magnet. Each day is tagged with its GEX pin-strength (from ``stats``) so a magnet
    set on a weak-pin/low-gamma day can be read as a less reliable anchor."""
    from app.magnet_trend import classify_trend, recent_daily_magnets

    L = data.levels
    ist_today = (data.fetched_at + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    recent = recent_daily_magnets(data.symbol, ist_today, n=5)

    st.markdown("#### 🧲 Dealer-positioning trend (magnet drift)")
    if len(recent) < 2:
        st.caption(f"Need a few prior {data.symbol} days to read the magnet trend — "
                   "accruing from the capture.")
        return

    magnets = [m for _, m in recent]
    info = classify_trend(magnets, L.magnet)

    # Per-day table: magnet + its gamma pin-strength (strong = high |net GEX|).
    gm = (stats or {}).get("gm", {})
    thr = (stats or {}).get("thr")

    def _pin_tag(g):
        if g is None or thr is None:
            return "—"
        return f"🟢 strong ({g:,.0f})" if g > thr else f"⚪ weak ({g:,.0f})"

    rows = [{"Day": d[5:], "Magnet": f"{m:,.0f}", "Pin (|net GEX|)": _pin_tag(gm.get(d))}
            for d, m in recent]
    rows.append({"Day": "today", "Magnet": f"{L.magnet:,.0f}",
                 "Pin (|net GEX|)": (f"🟢 strong ({stats['cur']:,.0f})" if stats and stats.get("strong")
                                     else f"⚪ weak ({stats['cur']:,.0f})" if stats else "—")})
    st.table(pd.DataFrame(rows))
    if thr is not None:
        st.caption(f"🟢 strong-pin days had |net GEX| above the ~{thr:,.0f} threshold "
                   "(magnet held harder); ⚪ weak-pin days are less reliable anchors — so weigh "
                   "the trend by the pin column, not just the magnet moves.")

    trend = info["trend"]
    drift = info.get("drift_pts", 0.0)
    anchor = info.get("anchor", L.magnet)
    if trend == "rising":
        st.success(f"🔼 **Positioning drifting UP** — the magnet rose {drift:+,.0f} pts over "
                   f"{info['n']} days (structural bias higher).")
    elif trend == "falling":
        st.warning(f"🔽 **Positioning drifting DOWN** — the magnet fell {drift:+,.0f} pts over "
                   f"{info['n']} days (structural bias lower).")
    else:
        st.info(f"⏸️ **Positioning anchored ~{anchor:,.0f}** for {info['n']} days — a settled "
                "pin / consolidation (range-bound bias).")

    if info.get("repositioning"):
        st.warning(f"⚠️ Today's magnet {L.magnet:,.0f} has **jumped {info['current_gap']:+,.0f}** "
                   f"vs the {info['n']}-day anchor ({anchor:,.0f}) — **repositioning**; the anchor "
                   "may be moving. Watch for a new trend forming.")
    elif info.get("current_state") == "at":
        st.caption(f"Today's magnet sits AT the {info['n']}-day anchor ({anchor:,.0f}) — "
                   "positioning consistent with recent days.")
    else:
        st.caption(f"Today's magnet is {info.get('current_gap', 0):+,.0f} "
                   f"{info.get('current_state', '')} the {info['n']}-day anchor ({anchor:,.0f}).")

    st.caption("The magnet marks where dealer gamma (big-player option positioning) "
               "concentrates; its multi-day drift is the positioning *trend*. **Sign-free** — "
               "independent of the dealer-sign landmine. Exploratory, not a live signal.")


def _dhan_greeks_section(data):
    """Display per-strike greeks (delta, gamma, theta, vega) and IV sourced
    directly from the Dhan API — no local Black-Scholes required.

    Shown only when DATA_SOURCE=dhan and the contracts DataFrame contains the
    Dhan-provided greek columns. PCR is computed locally from OI data.
    """
    if data.source != "dhan":
        return

    L = data.levels
    df = data.contracts
    if df is None or df.empty:
        return

    # Check if Dhan greeks are present in the contracts DataFrame
    greek_cols = [c for c in ("delta", "gamma", "theta", "vega") if c in df.columns]
    has_iv = "iv" in df.columns
    if not greek_cols and not has_iv:
        return  # old data without greek columns

    st.markdown("---")
    st.markdown("### 🔶 Dhan API — Live Greeks & IV")
    st.caption(
        "Delta, Gamma, Theta, Vega and IV are fetched **directly from the Dhan v2 option-chain "
        "API** — no local Black-Scholes computation. Dhan provides these per-strike in real-time. "
        "IV shown as % (e.g., 9.79 = 9.79% annualised). PCR is computed locally from OI."
    )

    # Filter to near-ATM strikes (10 closest to spot)
    try:
        # Get unique strikes and sort by distance from spot
        strikes_near = (
            df[["strike"]].drop_duplicates()
            .assign(dist=lambda d: (d["strike"] - L.spot).abs())
            .sort_values("dist")
            .head(10)["strike"]
            .sort_values()
            .tolist()
        )

        # Get nearest expiry
        nearest_exp = df["expiry"].min()
        near_df = df[(df["expiry"] == nearest_exp) & (df["strike"].isin(strikes_near))].copy()

        if near_df.empty:
            st.caption("No near-ATM contracts found.")
            return

        # Build a wide table: one row per strike with CE / PE columns
        def _fv(val, nd=4):
            """Format a float value, returning '—' for NaN."""
            try:
                v = float(val)
                if v != v:  # NaN check
                    return "—"
                return f"{v:.{nd}f}"
            except (TypeError, ValueError):
                return "—"

        rows = []
        for strike in sorted(strikes_near):
            ce = near_df[(near_df["strike"] == strike) & (near_df["type"] == "C")]
            pe = near_df[(near_df["strike"] == strike) & (near_df["type"] == "P")]
            ce_row = ce.iloc[0] if not ce.empty else None
            pe_row = pe.iloc[0] if not pe.empty else None

            def _g(row, col, nd=4):
                return _fv(row[col], nd) if row is not None and col in row.index else "—"

            def _iv_pct(row):
                """Display IV as percentage (Dhan stores as decimal after normalisation)."""
                if row is None or "iv" not in row.index:
                    return "—"
                try:
                    v = float(row["iv"])
                    if v != v:  # NaN
                        return "—"
                    # Parser normalised Dhan's % value to decimal; convert back for display
                    return f"{v * 100:.2f}%"
                except (TypeError, ValueError):
                    return "—"

            # Mark ATM strike
            atm_mark = " ➤ ATM" if abs(strike - L.spot) <= (L.spot * 0.003) else ""
            rows.append({
                "Strike": f"{strike:,.0f}{atm_mark}",
                "CE IV%":  _iv_pct(ce_row) if has_iv else "—",
                "CE Δ":   _g(ce_row, "delta"),
                "CE Γ":   _g(ce_row, "gamma", 6),
                "CE Θ":   _g(ce_row, "theta", 2),
                "CE ν":   _g(ce_row, "vega", 2),
                "PE IV%":  _iv_pct(pe_row) if has_iv else "—",
                "PE Δ":   _g(pe_row, "delta"),
                "PE Γ":   _g(pe_row, "gamma", 6),
                "PE Θ":   _g(pe_row, "theta", 2),
                "PE ν":   _g(pe_row, "vega", 2),
            })

        if rows:
            import pandas as _pd
            greek_df = _pd.DataFrame(rows)
            st.dataframe(
                greek_df,
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                f"Showing 10 nearest-ATM strikes · nearest expiry: {nearest_exp} · "
                "Spot = **" + f"{L.spot:,.2f}** · "
                "**IV% = annualised implied vol from Dhan API** (shown as %). "
                "Delta: CE positive, PE negative. "
                "Theta: daily time-decay in ₹. Vega: ₹ change per 1% IV move. "
                "Gamma (Γ) is per-share from Dhan — the GEX engine recomputes gamma "
                "independently for lot-scaled GEX. Sign-free data, decision-support only."
            )

        # --- PCR summary (computed locally from OI) ---
        ov = data.oi_volume or {}
        pcr = ov.get("pcr_oi")
        call_oi = ov.get("call_oi", 0)
        put_oi = ov.get("put_oi", 0)
        call_doi = ov.get("call_doi", 0)
        put_doi = ov.get("put_doi", 0)
        call_vol = ov.get("call_vol", 0)
        put_vol = ov.get("put_vol", 0)

        st.markdown("##### PCR & Aggregate OI (computed from Dhan chain)")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric(
            "PCR (OI)",
            f"{pcr:.3f}" if pcr is not None and pcr == pcr else "—",
            help="Put OI / Call OI — India convention: >1 = put-writing (bullish support), "
                 "<1 = call-writing (resistance). Computed locally from Dhan chain.",
        )
        p2.metric("Total Call OI", f"{call_oi:,.0f}",
                  f"ΔOI {call_doi:+,.0f}" if call_doi else None, delta_color="off")
        p3.metric("Total Put OI", f"{put_oi:,.0f}",
                  f"ΔOI {put_doi:+,.0f}" if put_doi else None, delta_color="off")
        p4.metric("Call Vol / Put Vol",
                  f"{call_vol:,.0f} / {put_vol:,.0f}",
                  help="Total volume across all strikes in the fetched expiry window.")

        if pcr is not None and pcr == pcr:
            if pcr >= 1.2:
                st.success(
                    f"🟢 **PCR {pcr:.3f} — PUT-WRITING SUPPORT** (≥1.2). "
                    "Institutional put-writers are backing the upside. India convention: "
                    "heavy put OI = structural bullish support below spot."
                )
            elif pcr <= 0.8:
                st.warning(
                    f"🔴 **PCR {pcr:.3f} — CALL-WRITING RESISTANCE** (≤0.8). "
                    "Heavy call OI overhead. India convention: "
                    "low PCR = call-writing capping the upside."
                )
            else:
                st.info(
                    f"⬜ **PCR {pcr:.3f} — NEUTRAL** (0.8–1.2). No strong put/call bias."
                )

    except Exception as _e:  # noqa: BLE001 - non-essential panel
        st.caption(f"Greeks panel unavailable: {_e}")


def _oi_footprint_section(data):
    """Per-strike ΔOI footprint: where big players are BUILDING OI (support vs
    resistance) and how it drifts intraday. Raw OI => sign-free; the put-build =
    support / call-build = resistance mapping is the standard exploratory heuristic."""
    from app.magnet_trend import classify_trend
    from app.oi_footprint import (build_footprint, classify_buildup,
                                  footprint_series, intraday_premium_change, top_builds)
    from app.timeseries import load_strikes_day

    st.markdown("#### 🏗️ Positioning footprint (per-strike ΔOI build)")
    if not data.oi_strikes:
        if data.source == "DEMO-SYNTHETIC":
            st.caption("Live data only — per-strike ΔOI isn't available on demo data.")
        else:
            st.caption(f"Per-strike ΔOI is not available from the {data.source} feed.")
        return
    L = data.levels
    fp = build_footprint(data.oi_strikes, spot=L.spot, band_pts=L.spot * 0.03)
    if not fp or fp["support_build"] is None or fp["resistance_build"] is None:
        st.caption("No meaningful OI additions near spot yet.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("🟢 Support building", f"{fp['support_build']:,.0f}",
              f"peak {fp['peak_put_strike']:,.0f}", delta_color="off")
    c2.metric("🔴 Resistance building", f"{fp['resistance_build']:,.0f}",
              f"peak {fp['peak_call_strike']:,.0f}", delta_color="off")
    skew = fp["net_skew"]
    c3.metric("Net ΔOI skew", f"{skew:+,.0f}",
              "put-writing" if skew > 0 else "call-writing", delta_color="off")

    ist_today = (data.fetched_at + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    day_df = load_strikes_day(ist_today, symbol=data.symbol)

    # Top-6 OI build-ups per side, each tagged with the price+OI build-up type
    # (writing / long build-up / short covering / long unwinding). Preferred: the
    # nearest-expiry OI-change + premium-change carried on the live snapshot, both vs
    # prev close (EXACT tag). Fallback for old CSVs/demo: the day's first→latest LTP.
    prem = intraday_premium_change(day_df)
    exact_tag = any(b.get("dprem") is not None
                    for b in data.oi_strikes)  # new-schema live data present?

    def _build_table(side, label):
        builds = top_builds(data.oi_strikes, side, spot=L.spot, band_pts=L.spot * 0.03, top_n=6)
        if not builds:
            return
        rows = []
        for b in builds:
            if b.get("dprem") is not None:            # exact: aligned expiry + window
                tag = classify_buildup(b.get("doi_near"), b["dprem"])
            else:                                     # fallback: intraday first→latest
                dprem = (prem.get(b["strike"]) or {}).get(side)
                tag = classify_buildup(b["doi"], dprem)
            rows.append({
                "Strike": f"{b['strike']:,.0f}",
                "ΔOI": f"{b['doi']:,.0f}",
                "Prem": f"{b['ltp']:,.1f}" if b["ltp"] is not None else "—",
                "Vol": f"{b['vol']:,.0f}" if b["vol"] is not None else "—",
                "Build-up": tag or "—",
            })
        st.markdown(label)
        st.table(pd.DataFrame(rows))

    tcol1, tcol2 = st.columns(2)
    with tcol1:
        _build_table("put", "**🟢 Top support builds (put ΔOI)**")
    with tcol2:
        _build_table("call", "**🔴 Top resistance builds (call ΔOI)**")

    # Intraday drift of the weighted centers (same journal).
    ser = footprint_series(day_df)
    if len(ser) >= 2:
        sup = [s for _, s, _, _ in ser]
        res = [r for _, _, r, _ in ser]
        parts = []
        for lbl, tr in (("Support", classify_trend(sup, sup[-1])),
                        ("Resistance", classify_trend(res, res[-1]))):
            arrow = {"rising": "🔼 rolling UP", "falling": "🔽 rolling DOWN",
                     "flat": "⏸️ steady"}.get(tr["trend"], "")
            parts.append(f"{lbl} {arrow} ({tr['drift_pts']:+,.0f} since open)")
        st.caption("Weighted-center intraday drift — " + "  ·  ".join(parts))

    if skew > 0:
        st.success("Net **put-writing** near spot — big players laying support; "
                   "positioning bias **up / supported**.")
    else:
        st.warning("Net **call-writing** near spot — big players capping upside; "
                   "positioning bias **down / capped**.")
    tag_note = ("**Build-up tag is EXACT** — nearest-expiry OI-change and premium-change, "
                "both vs previous close (aligned expiry + window)."
                if exact_tag else
                "Caveat: premium change is the day's first→latest snapshot (nearest expiry) "
                "while ΔOI is summed across expiries — so the tag is APPROXIMATE (pre-upgrade "
                "data).")
    st.caption(
        "Reads where OI is *being added* (ΔOI vs previous close): put-build = support, "
        "call-build = resistance (standard heuristic). Sign-free (raw OI). **Build-up tag** "
        "= price+OI matrix: OI↑ & premium↓ = *writing*, OI↑ & premium↑ = *long build-up*, "
        "OI↓ & premium↑ = *short covering*, OI↓ & premium↓ = *long unwinding*; **volume is "
        f"intensity, not a classifier**. {tag_note} Exploratory — not a live signal.")


# (side, kind) -> what the intraday streak MEANS directionally.
_STREAK_READ = {
    ("call", "green"): "🐂 call buying — bullish",
    ("call", "red"): "🧱 call writing — resistance / capping",
    ("put", "green"): "🐻 put buying — bearish / hedging",
    ("put", "red"): "🛡️ put writing — support / bullish",
}


def _buildup_streaks_section(data):
    """Intraday mini build-up streaks: strikes printing ≥3 consecutive same-colour
    intervals (green = long build-up OI↑&prem↑, red = writing OI↑&prem↓) between
    snapshots today. Momentum companion to the vs-prev-close footprint tag: it shows
    where OI is building *right now*, accelerating. Sign-free, exploratory."""
    from app.oi_footprint import intraday_buildup_streaks
    from app.timeseries import load_strikes_day

    st.markdown("#### 🔥 Intraday build-up streaks (live, 3× in a row)")
    if data.source == "DEMO-SYNTHETIC":
        st.caption("Live data only — needs the intraday per-strike capture.")
        return
    L = data.levels
    ist_today = (data.fetched_at + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    day_df = load_strikes_day(ist_today, symbol=data.symbol)
    if day_df is None or day_df.empty:
        st.caption("No intraday per-strike snapshots captured yet today.")
        return

    flags = []
    for side in ("call", "put"):
        flags += intraday_buildup_streaks(day_df, side, run=3, spot=L.spot,
                                          band_pts=L.spot * 0.03, only_live=True)
    if not flags:
        st.caption("No **live** build-up streak near spot at the latest snapshot — "
                   "nothing is accelerating right now (stale morning runs are hidden).")
        return
    # Rank LIVE streaks by conviction: turnover (run volume ÷ OI) first, then |cum ΔOI|.
    flags.sort(key=lambda x: (-(x.get("turnover") or -1.0), -abs(x["cum_doi"])))

    rows = []
    for f in flags[:10]:
        dot = "🟢" if f["kind"] == "green" else "🔴"
        turn = f.get("turnover")
        rows.append({
            "Strike": f"{f['strike']:,.0f}",
            "Side": f["side"].upper(),
            "Streak": f"{dot}×{f['run_len']}",
            "Turnover": f"{turn:.1f}×" if turn is not None else "—",
            "Cum ΔOI": f"{f['cum_doi']:+,.0f}",
            "Cum Prem": f"{f['cum_prem_pct']:+.1f}%" if f["cum_prem_pct"] is not None else "—",
            "Since": f["start_time"],
            "Read": _STREAK_READ.get((f["side"], f["kind"]), ""),
        })
    st.table(pd.DataFrame(rows))
    st.caption(
        "Only **live** streaks — one colour running ≥3 intervals and still building at the "
        "latest snapshot (stale runs that already ended are dropped). Green = long build-up "
        "(OI↑ & premium↑), red = writing/short (OI↑ & premium↓). **Turnover** = volume traded "
        "over the run ÷ current OI; rows are ranked by it. ⚠️ Turnover flags the **expected "
        "MOVE SIZE, not its direction** — validated: high turnover predicts a bigger swing "
        "(|move| IC ≈ +0.3) but **no** directional edge (IC ≈ 0), and buying an aggressive "
        "premium spike mean-reverts. So this is a **structure / where-the-money-is** read, NOT "
        "a buy-the-strike signal. Premium nearest-expiry, ΔOI all-expiry, step-over-step; a "
        "headline overrides it.")


_FUT_ICON = {"long build-up": "🐂", "short build-up": "🐻",
             "short covering": "🔄", "long unwinding": "📉", "flat": "➖"}
# FOUR distinct state colors so the trajectory separates all four (not just bull/bear).
# Validated as a categorical set (worst adjacent CVD ΔE 16.2 deutan) — cool green/aqua =
# bullish family, warm orange/red = bearish family. Always paired with icon+label+position.
_FUT_COLOR = {
    "long build-up": "#0ca30c",    # green  — strong bull (fresh longs)
    "short covering": "#1baf7a",   # aqua   — bull, position-squaring (shorts exit)
    "long unwinding": "#eb6834",   # orange — bear, position-squaring (longs exit)
    "short build-up": "#e34948",   # red    — strong bear (fresh shorts)
    "flat": "#898781",
}
_FUT_FRESH = {"long build-up", "short build-up"}  # fresh money (vs position-squaring)
_MUTE = "#898781"                                 # shared muted ink (works light + dark)


def _fut_abbr(x) -> str:
    if x is None or x != x:
        return "—"
    s, a = ("-" if x < 0 else "+"), abs(float(x))
    if a >= 1e6:
        return f"{s}{a / 1e6:.2f}M"
    if a >= 1e3:
        return f"{s}{a / 1e3:.0f}k"
    return f"{s}{a:.0f}"


def _fut_chip(title: str, arrow: str, value: str, color: str, sub: str) -> str:
    return (f'<div style="flex:1;border:1px solid {color}55;background:{color}12;'
            f'border-radius:9px;padding:9px 6px;text-align:center;min-width:0">'
            f'<div style="font-size:10.5px;color:{_MUTE};letter-spacing:.5px">{title}</div>'
            f'<div style="font-size:19px;font-weight:800;color:{color};margin:1px 0">{arrow} {value}</div>'
            f'<div style="font-size:10px;color:{_MUTE}">{sub}</div></div>')


def _fut_matrix_html(active: str | None) -> str:
    axl = f"font-size:10px;color:{_MUTE};display:flex;align-items:center;justify-content:center;font-weight:700"
    vax = axl + ";writing-mode:vertical-rl;transform:rotate(180deg)"

    def cell(lbl: str) -> str:
        icon, color = _FUT_ICON[lbl], _FUT_COLOR[lbl]
        on = lbl == active
        bg = f"{color}26" if on else "rgba(136,135,129,0.06)"
        border = f"2px solid {color}" if on else "1px solid rgba(136,135,129,0.28)"
        glow = f"box-shadow:0 0 0 3px {color}22;" if on else ""
        txt = color if on else _MUTE
        tag = (f'<div style="font-size:9px;color:{color};font-weight:700">◀ today</div>'
               if on else "")
        return (f'<div style="background:{bg};border:{border};{glow}border-radius:8px;'
                f'padding:8px 4px;text-align:center;min-height:56px;display:flex;'
                f'flex-direction:column;justify-content:center;gap:1px">'
                f'<div style="font-size:16px">{icon}</div>'
                f'<div style="font-size:11px;font-weight:{"800" if on else "600"};'
                f'color:{txt};line-height:1.12">{lbl}</div>{tag}</div>')

    return (f'<div style="display:grid;grid-template-columns:30px 1fr 1fr;gap:6px;margin:2px 0 12px">'
            f'<div></div><div style="{axl}">Price ▲</div><div style="{axl}">Price ▼</div>'
            f'<div style="{vax}">OI ▲</div>{cell("long build-up")}{cell("short build-up")}'
            f'<div style="{vax}">OI ▼</div>{cell("short covering")}{cell("long unwinding")}</div>')


def _fut_timeline_html(steps: list, cadence: int = 5) -> str:
    """Fixed FULL-SESSION timeline (09:15→15:30): one slot per ``cadence`` minutes,
    pre-filled as empty/pending boxes and coloured as the session fills in left→right.
    Each captured interval colours its slot by build-up state (4 distinct colours),
    with opacity ∝ |ΔOI%| (strength) and ΔOI%/Δvol on hover. Sign-free."""
    open_m, close_m = 9 * 60 + 15, 15 * 60 + 30
    n = (close_m - open_m) // cadence + 1

    def _hm(t):
        try:
            h, m = str(t).split(":"); return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return None

    slot = {}
    for s in steps:                                  # bucket each interval to its slot
        m = _hm(s.get("time"))
        if m is None:
            continue
        i = round((m - open_m) / cadence)
        if 0 <= i < n:
            slot[i] = s

    cells = ""
    for i in range(n):
        s = slot.get(i)
        if s and s.get("state"):
            c = _FUT_COLOR.get(s["state"], _MUTE)
            dpct = s.get("doi_pct")
            alpha = 0.45 + min(abs(dpct or 0) / 2.0, 1.0) * 0.55   # strength -> opacity
            hx = format(int(round(alpha * 255)), "02x")
            dtxt = f"{dpct:+.2f}%" if dpct is not None else "—"
            title = f'{s.get("time","")} · {s["state"]} · ΔOI {dtxt} · Δvol {_fut_abbr(s.get("dvol"))}'
            cells += f'<div style="flex:1;background:{c}{hx};height:22px" title="{title}"></div>'
        else:                                        # empty / pending slot
            cells += ('<div style="flex:1;background:rgba(136,135,129,0.12);height:22px;'
                      'border-right:1px solid rgba(136,135,129,0.10)" title="pending"></div>')

    ticks = "".join(f'<div style="flex:1;text-align:{a}">{t}</div>'
                    for t, a in [("09:15", "left"), ("11:00", "center"), ("13:00", "center"),
                                 ("15:30", "right")])
    return (f'<div style="display:flex;gap:0;border-radius:5px;overflow:hidden;'
            f'border:1px solid rgba(136,135,129,0.35);margin:2px 0 1px">{cells}</div>'
            f'<div style="display:flex;font-size:9px;color:{_MUTE};margin-bottom:6px">{ticks}</div>')


def _futures_buildup_section(data):
    """Index-futures OI build-up — sign-free DIRECTION, intraday-first. Leads with
    today's LIVE build-up (current vs prev close = ΔPrice × aggregate-ΔOI since the open,
    volume = conviction): a state banner, Price/OI/Volume chips, the 2×2 build-up matrix
    lit by the live state, and a session regime ribbon (the intraday trajectory). EOD is
    demoted to a compact context strip. Color always pairs with icon+label+matrix position."""
    from app.futures_buildup import (capture_futures_snapshot, futures_series,
                                     intraday_futures_buildup, intraday_futures_steps,
                                     latest_intraday_snapshot, load_futures_intraday_day)

    st.markdown(f"#### 🐂🐻 {data.symbol} futures OI build-up — **intraday**")
    if data.source != "nse":
        if data.source == "DEMO-SYNTHETIC":
            st.caption("Live data only — futures build-up needs the derivatives feed.")
        else:
            st.caption(f"NSE derivatives feed required — futures build-up is not supported on {data.source}.")
        return
    ist_today = (data.fetched_at + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    # Best-effort snapshot — ONLY in-session, so an off-hours dashboard load doesn't
    # write stale (frozen post-close) rows into the intraday journal.
    from app.timeseries import in_nse_session
    if in_nse_session(data.fetched_at):
        try:
            capture_futures_snapshot(symbol=data.symbol, min_gap_minutes=4.0)
        except Exception:  # noqa: BLE001 - optional
            pass

    # ===== INTRADAY (the goldmine): current vs prev close, since the session open =====
    day_df = load_futures_intraday_day(ist_today, data.symbol)
    intr = intraday_futures_buildup(day_df)
    if intr and intr.get("buildup"):
        label = intr["buildup"]
        color = _FUT_COLOR.get(label, _MUTE)
        strong = label in _FUT_FRESH
        dp, doi, dvol = intr.get("dprice"), intr.get("doi"), intr.get("dvol")
        doi_pct, turn_pct = intr.get("doi_pct"), intr.get("turnover_pct")
        strength = "fresh money" if strong else "position-squaring"
        st.markdown(
            f'<div style="border-left:6px solid {color};background:{color}18;border-radius:10px;'
            f'padding:13px 18px;display:flex;align-items:center;gap:16px;margin:2px 0 12px">'
            f'<div style="font-size:38px;line-height:1">{_FUT_ICON.get(label, "")}</div>'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:19px;font-weight:800;color:{color};letter-spacing:.3px">{label.upper()}</div>'
            f'<div style="font-size:12px;color:{_MUTE};margin-top:2px">{intr.get("bias") or ""} · '
            f'{strength} · LIVE {intr["start_time"]}–{intr["end_time"]} (n={intr["n"]}) · since prev close</div></div>'
            f'<div style="text-align:right;font-size:12.5px;line-height:1.55;color:{_MUTE};white-space:nowrap">'
            f'ΔPrice <b style="color:inherit;font-size:15px">{_fut_abbr(dp)}</b><br>'
            f'aggΔOI <b style="color:inherit;font-size:15px">{_fut_abbr(doi)}</b></div></div>',
            unsafe_allow_html=True)
        pc = "#0ca30c" if (dp or 0) > 0 else "#d03b3b" if (dp or 0) < 0 else _MUTE
        oc = "#2a78d6" if (doi or 0) > 0 else _MUTE       # blue=adding / muted=reducing (not bull/bear)
        oi_pct = f" ({doi_pct:+.2f}%)" if doi_pct is not None else ""
        turn_txt = f"{turn_pct:.0f}% of OI" if turn_pct is not None else "conviction"
        st.markdown(
            '<div style="display:flex;gap:9px;margin-bottom:10px">'
            + _fut_chip("PRICE", "▲" if (dp or 0) > 0 else "▼", _fut_abbr(dp), pc, "since prev close")
            + _fut_chip("OPEN INTEREST", "▲" if (doi or 0) > 0 else "▼", _fut_abbr(doi) + oi_pct, oc,
                        "positions " + ("added" if (doi or 0) > 0 else "reduced") + " (ΔOI%)")
            + _fut_chip("VOLUME", "Σ", _fut_abbr(dvol), "#c98500", f"turnover {turn_txt}")
            + '</div>', unsafe_allow_html=True)
        st.markdown(_fut_matrix_html(label), unsafe_allow_html=True)
        steps = intraday_futures_steps(day_df)
        if steps:
            st.markdown(f'<div style="font-size:11px;color:{_MUTE};font-weight:600;margin-bottom:1px">'
                        f'📊 Session trajectory · {steps[-1]["time"]} latest · '
                        f'{len(steps)}/{(15*60+30-9*60-15)//5} slots filled '
                        f'(colour = build-up state, opacity = |ΔOI%| strength; hover for ΔOI%/Δvol)</div>'
                        + _fut_timeline_html(steps), unsafe_allow_html=True)
    else:
        # Partial read: with 1 snapshot we can't compute a build-up delta, but the feed's
        # `change` gives price direction now — show live levels instead of a blank wait.
        snap = latest_intraday_snapshot(day_df)
        if snap and snap.get("price"):
            ch = snap.get("change")
            pc = "#0ca30c" if (ch or 0) > 0 else "#d03b3b" if (ch or 0) < 0 else _MUTE
            st.markdown(
                f'<div style="font-size:11px;color:{_MUTE};font-weight:600;margin-bottom:3px">'
                f'⏳ First snapshot {snap["time_ist"]} (n={snap["n"]}) — live levels; '
                f'build-up <b>direction</b> computes at the 2nd snapshot</div>'
                '<div style="display:flex;gap:9px;margin-bottom:8px">'
                + _fut_chip("PRICE (near)", "▲" if (ch or 0) > 0 else "▼", f'{snap["price"]:,.0f}', pc,
                            f'{_fut_abbr(ch)} vs prev close')
                + _fut_chip("OPEN INTEREST", "•", _fut_abbr(snap.get("agg_oi")), "#2a78d6",
                            "level (Δ needs 2nd snap)")
                + _fut_chip("VOLUME", "Σ", _fut_abbr(snap.get("agg_vol")), "#c98500", "traded so far")
                + '</div>', unsafe_allow_html=True)
        else:
            st.info("⏳ **Intraday futures build-up leads here during market hours** — accruing "
                    "from the 5-min capture (forward-only; no free historical intraday). "
                    "This is the direction signal; EOD below is context.")

    # ===== EOD context (demoted): prev-close state + multi-day regime strip =====
    ser = futures_series(data.symbol, n=10)
    if ser:
        latest = ser[-1]
        ec = _FUT_COLOR.get(latest.get("agg_buildup"), _MUTE)
        strip = ""
        for r in ser:
            c = _FUT_COLOR.get(r.get("agg_buildup"), _MUTE)
            strip += (f'<div style="flex:1;background:{c}1f;border:1px solid {c}55;border-radius:6px;'
                      f'padding:4px 1px;text-align:center;min-width:0">'
                      f'<div style="font-size:12px">{_FUT_ICON.get(r.get("agg_buildup"), "")}</div>'
                      f'<div style="font-size:8.5px;color:{_MUTE};white-space:nowrap">{r["date"][5:]}</div></div>')
        st.markdown(
            f'<div style="font-size:10.5px;color:{_MUTE};margin:4px 0 2px">Daily EOD context — '
            f'prev close <b style="color:{ec}">{latest.get("agg_buildup") or "—"}</b> '
            f'(ΔOI {_fut_abbr(latest.get("agg_doi"))}) · last {len(ser)} days (aggregate, rollover-netted):</div>'
            f'<div style="display:flex;gap:3px;margin-bottom:6px">{strip}</div>', unsafe_allow_html=True)
    # 4-state legend (color never alone — decoded here + on every icon/hover).
    leg = "  ".join(f'<span style="color:{_FUT_COLOR[k]}">■</span> {_FUT_ICON[k]} {k}'
                    for k in ("long build-up", "short covering", "long unwinding", "short build-up"))
    st.markdown(f'<div style="font-size:10px;color:{_MUTE};margin-bottom:2px">{leg}</div>',
                unsafe_allow_html=True)
    st.caption("Green/aqua = bullish · orange/red = bearish; **build-up = fresh money, "
               "covering/unwinding = position-squaring (weaker).** Intraday ΔOI is aggregate "
               "(rollover-netted), since session open ≈ prev close. **Sign-free** (futures OI+price, "
               "no dealer-sign landmine); **volume = conviction, not direction.** You confirm.")


def _fii_drift_readout(n: int = 5):
    """Multi-day FII-positioning drift over the last ``n`` captured days — the
    'drift beats level' read (same idea as the magnet drift). FII index-futures
    net position is the closest thing to a smart-money directional tell; whether
    it's getting MORE short or covering matters more than the single-day level.
    Sign-free, decision-support only."""
    from app.participant_oi import participant_series, series_drift

    ser = participant_series("FII", n=n)
    if len(ser) < 2:
        if ser:
            st.caption("FII drift — need ≥2 captured days for a trend (accruing).")
        return
    fut = [r["net_fut"] for r in ser]
    wri = [r["net_writing"] for r in ser]
    fd, wd = series_drift(fut), series_drift(wri)
    days = f"{ser[0]['date']}→{ser[-1]['date']} ({fd['n']}d)"

    # Direction words: for FII net-FUTURES, rising = covering shorts / adding longs
    # = bullish SHIFT; falling = adding shorts = bearish SHIFT.
    fut_word = {"rising": "🐂 turning more BULLISH (covering shorts / adding longs)",
                "falling": "🐻 turning more BEARISH (adding shorts)",
                "flat": "⏸️ steady"}.get(fd["trend"], "")
    # For option-WRITING, rising = writing more (selling premium); the bull/bear
    # sense depends on call-vs-put mix, so keep this one neutral (flow, not direction).
    wri_word = {"rising": "🔼 writing MORE options", "falling": "🔽 writing FEWER / buying back",
                "flat": "⏸️ steady"}.get(wd["trend"], "")

    st.markdown(f"**📈 FII positioning drift** ({days})")
    st.caption(
        f"Net index futures {fut[-1]:+,.0f} — {fut_word} "
        f"({fd['drift']:+,.0f} over window).  ·  "
        f"Net option-writing {wri[-1]:+,.0f} — {wri_word} ({wd['drift']:+,.0f}).")
    if fd["trend"] == "falling":
        st.warning("FII adding index-futures shorts across days — a **cautious/bearish** "
                   "positioning drift (caveat: FII futures shorts are often cash hedges).")
    elif fd["trend"] == "rising":
        st.success("FII covering shorts / adding index-futures longs across days — a "
                   "**constructive/bullish** positioning drift.")


def _participant_oi_section(data):
    """Big players: Participant-wise OI (Client/DII/FII/Pro index F&O). The DIRECT
    positioning read — who is net long/short index futures and who WRITES index options
    (the dealer-sign clue). EOD + market-wide (not per-index), sign-free, exploratory."""
    from app.participant_oi import capture_latest, derive_signals, latest_available

    st.markdown("#### 🏦 Big players (participant-wise OI)")
    got = None
    try:
        got = capture_latest()          # best-effort live fetch + daily cache
    except Exception:  # noqa: BLE001 - fail soft; participant OI is optional EOD context
        got = None
    if got is None:
        got = latest_available()        # fall back to whatever's in the journal
    if got is None:
        st.caption("No participant-OI file captured yet — exchange publishes it after close "
                   "(evening, IST). Run `scripts/log_participant_oi.py` or open the "
                   "dashboard post-close.")
        return

    date_ist, parsed = got
    sig = derive_signals(parsed) or {}
    per = sig.get("per_participant", {})

    def _fut(who):
        v = (per.get(who) or {}).get("net_fut")
        return f"{v:+,.0f}" if v is not None else "—"

    def _wri(who):
        v = (per.get(who) or {}).get("net_opt_writing")
        return f"{v:+,.0f}" if v is not None else "—"

    c1, c2, c3 = st.columns(3)
    c1.metric("FII net index futures", _fut("FII"),
              "long" if (per.get("FII") or {}).get("net_fut", 0) > 0 else "short",
              delta_color="off")
    c2.metric("FII net option-writing", _wri("FII"), delta_color="off")
    c3.metric("Client net option-writing", _wri("Client"), delta_color="off")

    st.caption(f"Net option-writing (short − long calls+puts, >0 = net **writer**) — "
               f"Client {_wri('Client')} · Pro {_wri('Pro')} · FII {_wri('FII')} · "
               f"DII {_wri('DII')}.")
    if sig.get("dealer_sign_hint"):
        st.info(f"🔎 Dealer-sign hint: {sig['dealer_sign_hint']}")

    # --- Multi-day FII-positioning drift (the "drift beats level" read) --------
    _fii_drift_readout()

    st.caption(
        f"Clearing-corp participant OI, **as of {date_ist}** (EOD — T+0 after close). "
        "Market-wide (index+stock file; **index** columns only), NOT split "
        "Nifty-vs-BankNifty. Sign-free (raw long/short counts). The dealer-sign hint is a "
        "*lean*, not a resolution of the landmine — decision-support only.")


def _gex_chart(L):
    """Net GEX by strike within ±5% of spot; spot (black dashed) and flip (blue)."""
    try:
        import altair as alt

        ps = L.per_strike
        if ps is None or ps.empty:
            return
        lo, hi = L.spot * 0.95, L.spot * 1.05
        ps = ps[(ps["strike"] >= lo) & (ps["strike"] <= hi)].copy()
        if ps.empty:
            return
        ps["sign"] = ps["net_gex"].apply(lambda v: "positive" if v >= 0 else "negative")
        bars = alt.Chart(ps).mark_bar().encode(
            x=alt.X("strike:Q", title="Strike"),
            y=alt.Y("net_gex:Q", title="Net GEX (₹ per 1% move)"),
            color=alt.Color(
                "sign:N",
                scale=alt.Scale(domain=["positive", "negative"], range=["#2ca02c", "#d62728"]),
                legend=None,
            ),
            tooltip=["strike", "net_gex"],
        )
        chart = bars + alt.Chart(pd.DataFrame({"x": [L.spot]})).mark_rule(
            color="black", strokeDash=[4, 2]
        ).encode(x="x:Q")
        if L.flip:
            chart = chart + alt.Chart(pd.DataFrame({"x": [L.flip]})).mark_rule(
                color="#1f77b4", size=2
            ).encode(x="x:Q")
        st.altair_chart(chart, use_container_width=True)
        st.caption("Net GEX by strike (±5% of spot). Black dashed = spot, blue = gamma flip.")
    except Exception as e:  # noqa: BLE001 - chart is non-essential
        st.caption(f"(chart unavailable: {e})")




_POSTURE = {
    "calm": ("🟦 **Range-bound / pinning** — MEAN-REVERSION favoured", [
        "Fade extremes back toward the **magnet**; breakouts here tend to fail.",
        "Sell premium near the **walls**; tighter stops; you can size UP.",
    ]),
    "wild": ("🟧 **Trending / expansion** — MOMENTUM favoured", [
        "Respect a **break of a wall**; fading the move is dangerous here.",
        "Buy premium / trade directional; stops **beyond** the walls; size DOWN (bigger swings).",
    ]),
    "mid": ("⬜ **Neutral vol regime** — no strong tilt", [
        "Trade the levels with modest size; wait for a clearer regime to lean.",
    ]),
}

def _regime_posture_note(data):
    """Turn the sign-free vol-REGIME read into a concrete 'how to trade this regime' posture
    (mean-revert vs momentum, premium buy/sell, stop placement, sizing). The regime is validated
    (|vanna|→forward vol); the POSTURE is the standard vol-regime playbook, not a direction call."""
    from app.intraday_features import regime_now

    try:
        vr = regime_now(data.symbol, data.vanna_now, data.charm_now, data.dte_now)
    except Exception:  # noqa: BLE001 - non-essential overlay
        vr = None
    if not vr:
        return
    head, tips = _POSTURE[vr["tag"]]
    body = "".join(f"<div style='font-size:13px;margin-top:3px'>• {t}</div>" for t in tips)
    ex = _posture_example(vr["tag"], data.levels)
    ex_html = (f"<div style='font-size:12.5px;margin-top:6px;padding:6px 10px;background:#00000010;"
               f"border-radius:6px'>{ex}</div>" if ex else "")
    st.markdown(
        f"<div style='border-left:4px solid {_MUTE};background:#00000008;border-radius:8px;"
        f"padding:8px 14px;margin:2px 0 8px'>"
        f"<div style='font-size:14px;font-weight:700'>🎯 How to trade this regime: {head}</div>"
        f"{body}{ex_html}<div style='font-size:12px;color:{_MUTE};margin-top:4px'>Sign-free "
        f"vol-regime posture (≈{vr['exp_rv']:.0f}% expected vol, next {vr['horizon_min']}min) — "
        f"<b>not a direction call</b>; illustrative examples, you confirm every trade.</div></div>",
        unsafe_allow_html=True)

def _posture_example(tag, L):
    """Concrete illustrative example trades wired to the LIVE levels (walls / magnet / spot).
    Illustrative only — decision-support, not advice."""
    def f(x):
        return f"{x:,.0f}" if x is not None and x == x else "—"
    cw, pw, mag, sp = L.call_wall, L.put_wall, L.magnet, L.spot
    if tag == "calm":
        return (f"<b>Examples:</b> a push up to the call wall <b>{f(cw)}</b> → <b>fade</b> back "
                f"toward the magnet <b>{f(mag)}</b> (buy put-spread / sell call-spread); or "
                f"<b>sell a strangle</b> around <b>{f(pw)} / {f(cw)}</b> — pinning favours "
                f"premium sellers. Stops just <i>outside</i> the walls.")
    if tag == "wild":
        return (f"<b>Examples:</b> a clean <b>break above {f(cw)}</b> → go <b>long / buy calls</b> "
                f"(stop back under {f(cw)}); a <b>break below {f(pw)}</b> → <b>short / buy puts</b>. "
                f"<b>Buy</b> options for convexity, don't sell premium, don't fade the move.")
    return (f"<b>Example:</b> wait for spot <b>{f(sp)}</b> to reach a wall (<b>{f(pw)} / {f(cw)}</b>) "
            f"or the flip; trade the level with small size until the regime picks a side.")

def _gex_vol_regime_section(data):
    """The one signal the research phase validated: sign-free dealer-gamma CONCENTRATION →
    forward realized VOLATILITY (SqueezeMetrics; |GEX|→RV IC ≈ −0.17/10d on 2 yrs of raw
    bhavcopy). An EOD vol-REGIME read — high |GEX| = expect calmer/mean-reverting, low =
    wilder/trending. Not a direction call (that's the unvalidated dealer-sign landmine)."""
    from app.gex_history import vol_regime

    st.markdown("#### 🌡️ Volatility regime (dealer gamma · EOD outlook)")
    try:
        vr = vol_regime(data.symbol)
    except Exception:  # noqa: BLE001 - optional/analytical
        vr = None
    if not vr:
        st.caption("Building — needs the raw-bhavcopy GEX history cache "
                   "(`python -c \"from app.gex_history import gex_series; gex_series()\"`).")
        return
    color = {"calm": "#2a78d6", "wild": "#eb6834", "mid": _MUTE}.get(vr["tag"], _MUTE)
    icon = {"calm": "🟦", "wild": "🟧", "mid": "⬜"}.get(vr["tag"], "")
    exp = f"{vr['exp_rv']:.0f}%" if vr.get("exp_rv") == vr.get("exp_rv") else "—"
    lo, hi = vr.get("lo_rv"), vr.get("hi_rv")
    ctx = (f"(this regime historically → ~{exp} realized vol over the next {vr['horizon']}d; "
           f"vs ~{hi:.0f}% high-gamma / ~{lo:.0f}% low-gamma)"
           if lo == lo and hi == hi else "")
    st.markdown(
        f'<div style="border-left:6px solid {color};background:{color}14;border-radius:10px;'
        f'padding:12px 16px;margin:2px 0 6px">'
        f'<div style="font-size:16px;font-weight:800;color:{color}">{icon} {vr["read"]}</div>'
        f'<div style="font-size:13px;color:{color};font-weight:700;margin-top:4px">'
        f'≈ {exp} expected realized vol · next {vr["horizon"]} days</div>'
        f'<div style="font-size:12px;color:{_MUTE};margin-top:3px">|GEX| in the '
        f'<b>{vr["pct"]:.0f}ᵗʰ percentile</b> of {vr["n"]} days · as of EOD {vr["day"]} '
        f'{ctx}</div></div>', unsafe_allow_html=True)
    st.caption("Gamma **concentration** (|GEX|) → forward realized vol (validated Spearman "
               "≈ −0.20 at 10d on 2 yrs; SqueezeMetrics pattern). A VOLATILITY-regime read, "
               "NOT a direction call. EOD (updates after each day's bhavcopy). "
               "Decision-support — you confirm.")


def _current_absgex_cache_units(data):
    """Current |GEX| on the same scale as the intraday-cache distribution, so it can be placed
    in the 5yr Dhan |GEX| percentile. Both are already comparable: the engine's per-strike
    net_gex multiplies NSE OI (contracts) by the lot, while the cache uses Dhan OI which is
    already in shares (= contracts × lot) — so the lot is baked into both, no rescale needed.
    We just match the cache's ATM±10 band by summing the ~21 nearest strikes. None if absent."""
    L = data.levels
    ps = getattr(L, "per_strike", None)
    if ps is None or "net_gex" not in getattr(ps, "columns", []):
        return None
    df = ps.dropna(subset=["net_gex"])
    if df.empty:
        return None
    near = df.iloc[(df["strike"] - float(L.spot)).abs().argsort()[:21]]
    return abs(float(near["net_gex"].sum()))

def _gex_regime_banner(data):
    """SIGN-FREE market-regime headline from dealer-gamma CONCENTRATION (|GEX|), replacing the
    old sign-based 'Market mode'. Study #1/#4 (docs/intraday-results.md): the dealer SIGN carries
    no clean regime/direction info, but the |GEX| BUCKET robustly gauges TRENDING (low |GEX|) vs
    SIDEWAYS (high |GEX|). Live-intraday: current |GEX| placed in the 5yr Dhan distribution, with
    the historical expected forward realized vol for that bucket."""
    from app.intraday_features import intraday_vol_regime, regime_now, regime_daily_move_now

    # DTE-switched dealer greek: |charm| ≤1.5 DTE (sharper near expiry), |vanna| else (Study #8/
    # T1b). Fall back to the |GEX| scale-bridge if the live greeks aren't available (demo/pre-cache).
    try:
        vr = regime_now(data.symbol, data.vanna_now, data.charm_now, data.dte_now)
        if vr is None and data.vanna_now is None:
            vr = intraday_vol_regime(data.symbol, _current_absgex_cache_units(data), metric="net_gex")
        dm = regime_daily_move_now(data.symbol, data.vanna_now, data.charm_now, data.dte_now)
    except Exception:  # noqa: BLE001 - non-essential overlay
        vr = dm = None
    if not vr:
        st.info(f"### Market regime: building…\n\nNeeds the intraday vol-regime (dealer-vanna) "
                f"feature cache for **{data.symbol}** (`python -c \"from app.intraday_features "
                f"import build; build('{data.symbol}')\"`).")
        return
    label = {"calm": "SIDEWAYS / RANGE-BOUND", "wild": "TRENDING / WIDER SWINGS",
             "mid": "NEUTRAL / MIXED"}[vr["tag"]]
    gword = {"calm": "HIGH", "wild": "LOW", "mid": "MID"}[vr["tag"]]
    box = {"calm": st.success, "wild": st.warning, "mid": st.info}[vr["tag"]]
    mname = {"vanna": "dealer-vanna", "charm": "dealer-charm (0-DTE)"}.get(
        vr.get("metric"), "dealer-gamma (|GEX|)")
    tail = vr["read"].split("→")[-1].strip()
    move_line = ""
    if dm and dm.get("hl_pct") == dm.get("hl_pct"):        # concrete daily move behind the label
        try:
            pts = dm["hl_pct"] / 100.0 * data.levels.spot
            move_line = (f"\n\nTypically this bucket moves **≈ {dm['hl_pct']:.1f}% (~{pts:,.0f} pt) "
                         f"high-low per day** (intraday from the open; vs ~{dm['hi_hl']:.1f}% "
                         f"high-concentration / ~{dm['lo_hl']:.1f}% low-concentration).")
        except (TypeError, AttributeError):
            move_line = ""
    box(f"### Market regime: {label}  ·  {gword} concentration\n\n"
        f"{mname.capitalize()} **concentration** sits in the **{vr['pct']:.0f}ᵗʰ percentile** of "
        f"5yr → {tail}. Historically ≈ **{vr['exp_rv']:.1f}% realized vol (annualized)** over the "
        f"next {vr['horizon_min']} min (vs ~{vr['hi_rv']:.1f}% high-concentration / "
        f"~{vr['lo_rv']:.1f}% low-concentration)."
        f"{move_line}")
