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

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import config  # noqa: E402
from app.data_access import WINDOW_CHOICES, load_dashboard  # noqa: E402

st.set_page_config(page_title="Nifty GEX", page_icon="📈", layout="wide")


def _refresh(window: str, use_demo: bool):
    try:
        st.session_state.data = load_dashboard(window=window, use_demo=use_demo)
        st.session_state.err = None
    except Exception as e:  # noqa: BLE001 - shown to the user, last-good kept
        st.session_state.err = str(e)


def main():
    st.title("Nifty GEX — Dashboard")
    st.caption("Decision-support only — **NOT** financial advice. **Paper trading only.**")
    st.error(
        "⚠️ The dealer-sign model is **UNVALIDATED for Nifty** — the regime could be "
        "inverted. Use this for practice and learning only, not real trades."
    )

    with st.sidebar:
        st.header("Controls")
        use_demo = st.checkbox(
            "Use demo data (offline)", value=False,
            help="Synthetic chain — runs without NSE. NOT a market read.",
        )
        window = st.selectbox("Expiry window", WINDOW_CHOICES, index=0)
        refresh = st.button("🔄 Refresh", type="primary", width="stretch")

    st.session_state.setdefault("data", None)
    st.session_state.setdefault("err", None)

    if refresh or st.session_state.data is None:
        with st.spinner("Pulling the option chain & computing GEX…"):
            _refresh(window, use_demo)

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

    L, V = data.levels, data.view

    # --- Market mode (plain language, prominent) ---------------------------
    box = st.info
    if V.mode_label.startswith("RANGE-BOUND"):
        box = st.success
    elif V.mode_label.startswith("TRENDING"):
        box = st.warning
    elif V.mode_label == "CAUTION":
        box = st.error
    box(f"### Market mode: {V.mode_label}\n\n{V.mode_plain}")
    st.caption(
        "GEX reads market *behaviour* (calm vs wild), not a guaranteed direction. "
        f"Technical read: {V.headline}"
    )
    if data.source == "DEMO-SYNTHETIC":
        st.info("DEMO data is **synthetic** — do not trade on it.")

    # --- Key metrics -------------------------------------------------------
    c1, c2, c3 = st.columns(3)
    c1.metric("Spot", f"{L.spot:,.2f}")
    c2.metric("Regime", L.regime)
    c3.metric("Total net GEX", f"{L.total_net_gex:,.0f}")

    # --- Simple levels (from the put/call walls) ---------------------------
    st.markdown("#### Simple levels")
    s1, s2, s3 = st.columns(3)
    s1.metric("🟢 Support (put wall)", f"{L.put_wall:,.0f}", f"{L.put_wall - L.spot:+,.0f}")
    s2.metric("🔴 Resistance (call wall)", f"{L.call_wall:,.0f}", f"{L.call_wall - L.spot:+,.0f}")
    s3.metric("🧲 Middle / magnet", f"{L.magnet:,.0f}", f"{L.magnet - L.spot:+,.0f}")
    width = L.call_wall - L.put_wall
    st.caption(
        f"Likely band when range-bound: **{L.put_wall:,.0f} – {L.call_wall:,.0f}** "
        f"({width:,.0f} pts wide). Stops go just **beyond** the walls, never at them."
    )

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

    # --- Net-GEX-by-strike chart ------------------------------------------
    _gex_chart(L)

    # --- Caveats -----------------------------------------------------------
    with st.expander("Assumptions & caveats (please read)", expanded=True):
        st.markdown(
            f"- **Lot size:** {config.CONTRACT_SIZE} — *VERIFY against the current NSE circular.*\n"
            f"- **Dealer-sign model:** `{L.dealer_sign_model}` — **UNVALIDATED for India.**\n"
            f"- **Expiry weighting:** `{L.expiry_weighting}` — near-dated walls weighted up.\n"
            f"- **Expiry window:** {L.expiry_window} · contracts {data.n_contracts} · "
            f"expiries {len(data.expiries)}\n"
            f"- **OI timestamp:** {L.oi_timestamp} (stale={L.oi_stale}) — OI is end-of-day lagging.\n"
            f"- **Source:** {data.source} · fetched {data.fetched_at:%Y-%m-%d %H:%M} UTC"
        )
        for f in L.flags:
            st.markdown(f"- ⚠️ {f}")

    st.divider()
    st.caption(
        "A map and a logbook, not an oracle. You decide and confirm every trade. Paper only."
    )


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
        st.altair_chart(chart, width="stretch")
        st.caption("Net GEX by strike (±5% of spot). Black dashed = spot, blue = gamma flip.")
    except Exception as e:  # noqa: BLE001 - chart is non-essential
        st.caption(f"(chart unavailable: {e})")


main()
