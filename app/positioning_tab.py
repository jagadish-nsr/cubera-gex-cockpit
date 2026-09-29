"""app/positioning_tab.py — NSE Positioning Dashboard (Tab 4).

A comprehensive positioning view that assembles all available signals into one
screen.  Designed to be self-contained: it accepts the ``DashboardData`` bundle
already loaded by Tab 1 or 2 (zero extra API calls for the option-chain layer),
plus the flat participant-OI journal (loaded locally).

Sections:
    A. Participant Scorecard  — category biases + Z-scores + composite signal
    B. Rolling Divergence History — 20-session chart + trend metrics
    C. Option Chain OI Map    — call/put bar chart + PCR bands + max pain marker
    D. Volatility Panel       — IV skew, term structure, dealer gamma
    E. Signal Card            — composite score + conviction + watch levels
    F. History / Backtest     — FII net-futures vs next-day Nifty return

Dependency principle: imports from analytics.* (pure functions) and
app.participant_oi (journal I/O only). Network calls only happen via the
``data`` bundle already fetched by another tab.
"""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _fmt(v, suffix: str = "", prefix: str = "") -> str:
    if v is None:
        return "—"
    return f"{prefix}{v:+,.0f}{suffix}" if isinstance(v, (int, float)) else str(v)


# ---------------------------------------------------------------------------
# Composite signal weights (FII is the smart-money proxy per preference)
# ---------------------------------------------------------------------------
_WEIGHTS = {"FII": 0.50, "Pro": 0.20, "Client": 0.20, "DII": 0.10}


def _composite_signal(biases: dict) -> dict:
    """Weighted composite bias score and conviction count.

    Returns {score, label, conviction (0–4), aligned_factors: list[str]}
    """
    score = sum(
        biases.get(cat, 0) * w for cat, w in _WEIGHTS.items()
    )
    # Conviction = count of participants whose bias is aligned with the composite
    aligned = [
        cat for cat, w in _WEIGHTS.items()
        if (score > 0 and biases.get(cat, 0) > 0)
        or (score < 0 and biases.get(cat, 0) < 0)
    ]
    conviction = len(aligned)

    if score > 300_000:
        label = "🟢 Bullish"
    elif score < -300_000:
        label = "🔴 Bearish"
    else:
        label = "🟡 Neutral"

    return {"score": score, "label": label, "conviction": conviction, "aligned": aligned}


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_nse_positioning_tab(data) -> None:
    """Render the full NSE Positioning dashboard tab.

    Parameters
    ----------
    data : DashboardData | None
        The bundle loaded by Tab 1 or 2. If None, the option-chain-dependent
        sections (C, D, E-strike-layer) show graceful fallbacks.
    """
    from analytics.bias import (compute_category_biases, divergence_trend,
                                 interpret_divergence, rolling_divergence)
    from analytics.report import CAVEATS
    from analytics.strikes import (combine_divergence_with_strikes,
                                   max_call_oi_strike, max_put_oi_strike,
                                   pcr_from_chain, top_oi_change_strikes)
    from analytics.zscore import build_zscore_series, rolling_zscore, zscore_label
    from analytics.pcr_bands import pcr_by_band, pcr_band_label
    from analytics.max_pain import max_pain, max_pain_distance
    from analytics.iv_skew import skew_25delta
    from analytics.stock_futures_gauge import compute_gauge_metrics
    from app.participant_oi import (available_days, capture_latest,
                                    latest_available, load_participant_day,
                                    participant_series)

    # ── Caveats (always top) ───────────────────────────────────────────────
    with st.expander("⚠️ Important caveats — read before acting on this data", expanded=False):
        for c in CAVEATS:
            st.markdown(f"- {c}")
        st.markdown(
            "- **Z-scores** measure how stretched current positioning is relative to "
            "its own recent history — not an absolute signal. Requires ≥ 20 sessions of data."
        )

    st.markdown("## 🧭 NSE Participant Positioning Dashboard")
    st.caption(
        "Decision-support only — **NOT** financial advice. "
        "All data is EOD (participant OI) or from the last manual refresh of the GEX tab (option chain)."
    )

    # ── Load participant OI ───────────────────────────────────────────────
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
            "Run `python scripts/backfill_daily.py --days 60 --only participant` "
            "to seed history, or wait for the daily daemon to capture today's file."
        )
        return

    date_ist, parsed = got
    biases = compute_category_biases(parsed)
    days   = available_days()

    # Prepare history for stock-futures gauge and Z-scores
    today_idx = days.index(date_ist) if date_ist in days else len(days)
    yesterday_parsed = load_participant_day(days[today_idx - 1]) if today_idx > 0 else None

    historical_pcts: dict[str, list[float]] = {"Client": [], "DII": [], "FII": [], "Pro": []}
    for d in days[:today_idx]:
        p = load_participant_day(d)
        if not p:
            continue
        tot = sum((p.get(c, {}).get("fut_stk_long") or 0) for c in historical_pcts)
        if tot > 0:
            for c in historical_pcts:
                if c in p:
                    nl = p[c].get("fut_stk_long") or 0
                    ns = p[c].get("fut_stk_short") or 0
                    historical_pcts[c].append((nl - ns) / tot)

    gauge_metrics = compute_gauge_metrics(parsed, yesterday_parsed, historical_pcts)

    # ══════════════════════════════════════════════════════════════════════
    # SECTION A — Participant Scorecard + Z-scores + Composite Signal
    # ══════════════════════════════════════════════════════════════════════
    _render_section_a(biases, days, date_ist, parsed, load_participant_day)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION B — Rolling Divergence History
    # ══════════════════════════════════════════════════════════════════════
    _render_section_b(days, date_ist)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION C — Option Chain OI Map
    # ══════════════════════════════════════════════════════════════════════
    _render_section_c(data)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION D — Volatility Panel (IV skew + term structure + GEX sign)
    # ══════════════════════════════════════════════════════════════════════
    _render_section_d(data)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION E — Signal Card
    # ══════════════════════════════════════════════════════════════════════
    _render_section_e(biases, data, gauge_metrics)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # SECTION F — History / Backtest
    # ══════════════════════════════════════════════════════════════════════
    _render_section_f(days)

    # ── Data provenance footer ─────────────────────────────────────────
    chain_at = data.fetched_at.strftime("%H:%M UTC") if data and data.fetched_at else "—"
    st.caption(
        f"📋 Participant OI: NSE clearing-corp, **{date_ist}** (EOD, market-wide). "
        f"Option chain: Dhan API refreshed at {chain_at}. "
        "Decision-support only."
    )


# ---------------------------------------------------------------------------
# Section A — Participant Scorecard
# ---------------------------------------------------------------------------

def _render_section_a(biases, days, date_ist, parsed, load_fn) -> None:
    from analytics.zscore import rolling_zscore, zscore_label
    from app.participant_oi import derive_signals

    st.markdown(f"### A — Participant Scorecard  ·  data as of **{date_ist}**")
    st.caption(
        "Bias = Net Index Futures + Net Index Call − Net Index Put. "
        "Z-score: how stretched today's FII / Client net-futures is vs the last 60 sessions."
    )
    with st.expander("ℹ️ How to interpret this section", expanded=False):
        st.markdown(
            "**What it indicates**: Shows net futures positions for major participants. The **Z-Score** measures how extreme the positioning is relative to the last 60 days (e.g., +2.0 = heavily long, -2.0 = heavily short).\n\n"
            "**Decision making**: Look for extreme divergences. If FIIs (smart money) have a strongly positive Z-Score while Clients (retail) have a strongly negative Z-Score, a bullish reversal (short squeeze) is highly probable. Never trade against a strong FII trend."
        )

    # Bias metric cards
    col_c, col_f, col_d, col_p = st.columns(4)
    def _bias_icon(v): return "📈" if v > 0 else ("📉" if v < 0 else "➡️")

    col_c.metric("🧑‍💼 Client (Retail)", f"{biases.get('Client', 0):+,.0f}",
                 _bias_icon(biases.get("Client", 0)), delta_color="off")
    col_f.metric("🏦 FII (Smart money)", f"{biases.get('FII', 0):+,.0f}",
                 _bias_icon(biases.get("FII", 0)), delta_color="off")
    col_d.metric("🏢 DII", f"{biases.get('DII', 0):+,.0f}",
                 _bias_icon(biases.get("DII", 0)), delta_color="off")
    col_p.metric("⚡ Pro (Prop desks)", f"{biases.get('Pro', 0):+,.0f}",
                 _bias_icon(biases.get("Pro", 0)), delta_color="off")

    # Divergence + composite signal
    div = biases.get("divergence", 0)
    composite = _composite_signal(biases)
    div_icon = "🔴" if div > 500_000 else ("🟢" if div < -500_000 else "🟡")

    c1, c2, c3 = st.columns(3)
    c1.metric(
        f"{div_icon} Divergence (Client − FII)", f"{div:+,.0f}",
        "Retail crowded long vs FII short" if div > 500_000 else
        "Retail short vs FII long" if div < -500_000 else "No extreme divergence",
        delta_color="off",
    )
    c2.metric(
        "🎯 Composite Signal",
        composite["label"],
        f"Conviction {composite['conviction']}/4 factors aligned",
        delta_color="off",
        help=f"Weighted: FII×0.5 + Pro×0.2 + Client×0.2 + DII×0.1. "
             f"Aligned: {', '.join(composite['aligned']) or 'none'}",
    )

    # Build Z-score series for FII and Client net-futures
    fii_series_raw = [
        derive_signals(load_fn(d) or {}) or {}
        for d in days
    ]
    def _extract(sig_list, cat, fld):
        return [
            s.get("per_participant", {}).get(cat, {}).get(fld)
            for s in sig_list
        ]

    fii_vals   = _extract(fii_series_raw, "FII",    "net_fut")
    client_vals = _extract(fii_series_raw, "Client", "net_fut")

    fii_z    = rolling_zscore(fii_vals,    window=60)
    client_z = rolling_zscore(client_vals, window=60)

    fii_z_str    = f"{fii_z:.2f}" if isinstance(fii_z, float) else str(fii_z)
    client_z_str = f"{client_z:.2f}" if isinstance(client_z, float) else str(client_z)

    c3.metric(
        "📏 FII Net-Futures Z-score",
        fii_z_str,
        zscore_label(fii_z),
        delta_color="off",
        help="How stretched FII net-futures positioning is vs the last 60 sessions. "
             "|Z|≥1.5 = noteworthy, |Z|≥2.0 = extreme.",
    )

    # Client Z-score in expander to keep scoreboard clean
    with st.expander("📏 Client Net-Futures Z-score (retail leverage gauge)"):
        zlabel = zscore_label(client_z)
        st.metric("Client Net-Futures Z-score", client_z_str, zlabel, delta_color="off")
        st.caption(
            "Retail (Client) net-futures exposure vs 60-session history. "
            "Extreme long = retail over-leveraged = contrarian caution signal."
        )

    # Full breakdown table
    with st.expander("🔍 Full bias breakdown (net_fut / net_call / net_put by participant)"):
        breakdown = biases.get("breakdown", {})
        if breakdown:
            rows = [
                {
                    "Participant": who,
                    "Net Fut Index": f"{bd.get('net_fut', 0):+,.0f}",
                    "Net Call Index": f"{bd.get('net_call', 0):+,.0f}",
                    "Net Put Index": f"{bd.get('net_put', 0):+,.0f}",
                    "Bias Score": f"{bd.get('bias', 0):+,.0f}",
                }
                for who, bd in breakdown.items()
            ]
            st.table(pd.DataFrame(rows))
            st.caption(
                "Negative net_put = net put-writer (bullish). "
                "Positive net_put = net put-buyer (bearish/hedged)."
            )


# ---------------------------------------------------------------------------
# Section B — Rolling Divergence History
# ---------------------------------------------------------------------------

def _render_section_b(days, date_ist) -> None:
    from analytics.bias import divergence_trend, rolling_divergence
    from app.participant_oi import load_participant_day, derive_signals

    st.markdown("### B — Rolling Divergence History (last 20 sessions)")
    with st.expander("ℹ️ How to interpret this section", expanded=False):
        st.markdown(
            "**What it indicates**: Tracks the historical trend of FII vs Client index futures positioning against the price chart.\n\n"
            "**Decision making**: Look for **divergences**. If the index makes a lower low but the FII line is steadily rising, they are silently accumulating longs (bullish reversal). If the market makes a new high but FIIs are dumping longs, prepare for a sharp correction."
        )

    series = rolling_divergence(n=20)
    t5  = divergence_trend(series, 5)
    t10 = divergence_trend(series, 10)
    t20 = divergence_trend(series, 20)

    # Trend metrics
    tc1, tc2, tc3 = st.columns(3)
    for col, (label, t) in zip([tc1, tc2, tc3],
                                [("5-day", t5), ("10-day", t10), ("20-day", t20)]):
        icon = "↗️" if t.get("trend") == "rising" else ("↘️" if t.get("trend") == "falling" else "➡️")
        col.metric(
            f"{icon} {label} trend",
            t.get("trend", "—").capitalize(),
            f"{t.get('drift', 0):+,.0f}/session" if t.get("n", 0) >= 2 else "insufficient data",
            delta_color="off",
        )

    if len(series) < 2:
        st.info(
            f"Only {len(series)} day(s) of data. "
            "Run `python scripts/backfill_daily.py --days 60 --only participant` "
            "to seed history."
        )
        return

    # Build enriched series with FII + Client net-futures
    enriched = []
    for row in series:
        p = load_participant_day(row["date"])
        sigs = (derive_signals(p) or {}).get("per_participant", {}) if p else {}
        enriched.append({
            **row,
            "fii_net_fut":    sigs.get("FII",    {}).get("net_fut"),
            "client_net_fut": sigs.get("Client", {}).get("net_fut"),
        })

    try:
        import altair as alt
        sdf = pd.DataFrame(enriched)
        sdf["div_M"] = sdf["divergence"] / 1_000_000

        base = alt.Chart(sdf).encode(
            x=alt.X("date:O", title="Date", axis=alt.Axis(labelAngle=-45)),
        )

        div_line = base.mark_line(color="#f97316", strokeWidth=2.5).encode(
            y=alt.Y("div_M:Q", title="Score (millions)"),
            tooltip=["date:O",
                     alt.Tooltip("divergence:Q", format=",", title="Divergence"),
                     alt.Tooltip("FII:Q", format=",", title="FII Bias"),
                     alt.Tooltip("Client:Q", format=",", title="Client Bias")],
        )
        div_pts = base.mark_circle(color="#f97316", size=60).encode(y="div_M:Q")

        # FII bias series
        sdf["fii_M"] = sdf["FII"] / 1_000_000
        fii_line = base.mark_line(color="#60a5fa", strokeWidth=1.5,
                                   strokeDash=[6, 3]).encode(
            y=alt.Y("fii_M:Q"),
            tooltip=[alt.Tooltip("FII:Q", format=",", title="FII Bias")],
        )

        # Client bias series
        sdf["client_M"] = sdf["Client"] / 1_000_000
        client_line = base.mark_line(color="#f87171", strokeWidth=1.5,
                                      strokeDash=[4, 2]).encode(
            y=alt.Y("client_M:Q"),
            tooltip=[alt.Tooltip("Client:Q", format=",", title="Client Bias")],
        )

        zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
            color="rgba(255,255,255,0.25)", strokeDash=[4, 2]
        ).encode(y="y:Q")
        thi  = alt.Chart(pd.DataFrame({"y": [0.5]})).mark_rule(
            color="rgba(239,68,68,0.35)", strokeDash=[6, 3]
        ).encode(y="y:Q")
        tlo  = alt.Chart(pd.DataFrame({"y": [-0.5]})).mark_rule(
            color="rgba(34,197,94,0.35)", strokeDash=[6, 3]
        ).encode(y="y:Q")

        chart = (div_line + div_pts + fii_line + client_line + zero + thi + tlo).properties(
            height=280
        )
        st.altair_chart(chart, use_container_width=True)
        st.caption(
            "🟠 Orange = Client−FII divergence  ·  🔵 Blue dashed = FII bias  ·  "
            "🔴 Red dashed = Client bias  ·  "
            "Red/green bands = ±500K thresholds."
        )
    except Exception as e:
        st.caption(f"(chart unavailable: {e})")
        # Fallback: table
        st.dataframe(pd.DataFrame(series)[["date", "divergence", "FII", "Client"]])


# ---------------------------------------------------------------------------
# Section C — Option Chain OI Map
# ---------------------------------------------------------------------------

def _render_section_c(data) -> None:
    from analytics.strikes import max_call_oi_strike, max_put_oi_strike, top_oi_change_strikes
    from analytics.pcr_bands import pcr_by_band, pcr_band_label
    from analytics.max_pain import max_pain, max_pain_distance
    import config

    st.markdown("### C — Option Chain OI Map")
    with st.expander("ℹ️ How to interpret this section", expanded=False):
        st.markdown(
            "**What it indicates**: A heatmap of where option writers have placed their bets. Massive Call OI acts as a resistance ceiling, while massive Put OI acts as a support floor.\n\n"
            "**Decision making**: Use this for intraday targets and stop-losses. If the market approaches a massive Call OI wall, it is highly unlikely to break through on expiry day without extreme momentum. Profits can be safely booked near these walls."
        )

    if data is None or not data.oi_strikes:
        st.info(
            "Refresh the GEX tab (Tab 1 or 2) first to load the option chain. "
            "This section reuses that data — no extra API calls."
        )
        return

    per_strike = data.oi_strikes
    spot = data.levels.spot if hasattr(data, "levels") and data.levels else None

    if not spot:
        st.info("Spot price not available — cannot render OI map.")
        return

    # OI walls + max pain
    max_call = max_call_oi_strike(per_strike)
    max_put  = max_put_oi_strike(per_strike)
    idx = config.get_index(getattr(data, "symbol", None))
    mp_result = max_pain(per_strike, lot_size=idx.lot_size)
    mp_strike = mp_result["max_pain_strike"] if mp_result else None
    top_doi   = top_oi_change_strikes(per_strike, n=5)

    # Metric cards
    mc1, mc2, mc3 = st.columns(3)
    if max_call:
        dist = max_call["strike"] - spot
        mc1.metric("🔴 Call Wall (Resistance)", f"{max_call['strike']:,.0f}",
                   f"{dist:+,.0f} pts", delta_color="off",
                   help=f"Call OI: {max_call['call_oi']:,.0f} contracts")
    if max_put:
        dist = max_put["strike"] - spot
        mc2.metric("🟢 Put Wall (Support)", f"{max_put['strike']:,.0f}",
                   f"{dist:+,.0f} pts", delta_color="off",
                   help=f"Put OI: {max_put['put_oi']:,.0f} contracts")
    if mp_strike:
        mp_dist = max_pain_distance(mp_strike, spot)
        mc3.metric("🎯 Max Pain", f"{mp_strike:,.0f}",
                   f"{mp_dist:+,.0f} pts from spot", delta_color="off",
                   help="Strike minimising total option writer payout at expiry.")

    # PCR by band
    band_pts = [200.0, 500.0, 1000.0]
    try:
        bands = pcr_by_band(per_strike, spot, band_pts)
        st.markdown("**PCR by Strike Band**")
        band_cols = st.columns(len(band_pts))
        for col, (label, bdata) in zip(band_cols, bands.items()):
            pcr_val = bdata["pcr"]
            pcr_str = f"{pcr_val:.2f}" if pcr_val == pcr_val else "n/a"
            col.metric(
                f"PCR {label} pts",
                pcr_str,
                pcr_band_label(pcr_val),
                delta_color="off",
                help=f"{bdata['strikes_included']} strikes · Call OI {bdata['call_oi']:,.0f} / Put OI {bdata['put_oi']:,.0f}",
            )
    except Exception as e:
        st.caption(f"PCR bands unavailable: {e}")

    # OI bar chart (butterfly: call OI right, put OI left)
    try:
        import altair as alt

        oi_df = pd.DataFrame(per_strike).copy()
        # Filter to a ±1500 pts band for readability
        oi_df = oi_df[(oi_df["strike"] >= spot - 1500) & (oi_df["strike"] <= spot + 1500)]
        oi_df["call_oi_neg"] = -oi_df["call_oi"]   # left side

        base  = alt.Chart(oi_df)
        calls = base.mark_bar(color="#f97316", opacity=0.85).encode(
            y=alt.Y("strike:O", title="Strike", sort="descending",
                    axis=alt.Axis(labelFontSize=10)),
            x=alt.X("call_oi:Q", title="Call OI", axis=alt.Axis(format=",s")),
            tooltip=["strike:Q",
                     alt.Tooltip("call_oi:Q", format=",", title="Call OI"),
                     alt.Tooltip("call_doi:Q", format=",", title="Call ΔOI")],
        )
        puts = base.mark_bar(color="#60a5fa", opacity=0.85).encode(
            y=alt.Y("strike:O", sort="descending"),
            x=alt.X("call_oi_neg:Q", title="Put OI ←", axis=alt.Axis(format=",s")),
            tooltip=["strike:Q",
                     alt.Tooltip("put_oi:Q", format=",", title="Put OI"),
                     alt.Tooltip("put_doi:Q", format=",", title="Put ΔOI")],
        ).transform_calculate(
            put_oi_neg="-datum.put_oi"
        ).encode(x=alt.X("put_oi_neg:Q", title="← Put OI"))

        # Spot marker
        spot_df = pd.DataFrame({"spot": [spot]})
        # We use a rule on a shared axis — mark the strike nearest to spot
        spot_strike = oi_df.loc[(oi_df["strike"] - spot).abs().idxmin(), "strike"] if not oi_df.empty else None

        chart = alt.layer(calls, puts).resolve_scale(x="independent").properties(
            title=f"Call OI (orange) vs Put OI (blue) · Spot ≈ {spot:,.0f}",
            height=max(200, len(oi_df) * 18),
        )
        st.altair_chart(chart, use_container_width=True)
        st.caption(
            "Orange bars → call OI (resistance zone).  "
            "Blue bars ← put OI (support zone).  "
            "Largest bars = OI walls."
        )
    except Exception as e:
        st.caption(f"OI chart unavailable: {e}")

    # Top ΔOI strikes table
    if top_doi:
        with st.expander("🔄 Top 5 strikes by OI change (fresh money today)"):
            doi_rows = []
            for s in top_doi:
                direction = {"call_build": "📈 Call build", "put_build": "📉 Put build",
                             "mixed": "🔀 Mixed"}.get(s["direction"], s["direction"])
                doi_rows.append({
                    "Strike":       f"{s['strike']:,.0f}",
                    "Call ΔOI":    f"{s['call_doi']:+,.0f}",
                    "Put ΔOI":     f"{s['put_doi']:+,.0f}",
                    "Total |ΔOI|": f"{s['total_abs_doi']:,.0f}",
                    "Direction":   direction,
                })
            st.table(pd.DataFrame(doi_rows))
            st.caption("Large positive ΔOI = fresh build-up. Negative = unwinding.")


# ---------------------------------------------------------------------------
# Section D — Volatility Panel
# ---------------------------------------------------------------------------

def _render_section_d(data) -> None:
    from analytics.iv_skew import skew_25delta

    st.markdown("### D — Volatility Panel")
    with st.expander("ℹ️ How to interpret this section", expanded=False):
        st.markdown(
            "**What it indicates**: \n"
            "- **IV Skew**: Positive skew means puts are more expensive (fear/hedging). Negative means calls are more expensive (greed).\n"
            "- **Max Pain**: The strike price where option writers maximize their profits.\n\n"
            "**Decision making**: On expiry day, spot price acts like a magnet pulling toward Max Pain. If IV Skew suddenly spikes higher during a rally, smart money is buying insurance because they expect a drop."
        )

    if data is None:
        st.info("Refresh the GEX tab to load option chain data.")
        return

    contracts_df = getattr(data, "contracts", None)
    spot = data.levels.spot if hasattr(data, "levels") and data.levels else None

    # IV Skew
    st.markdown("#### IV Skew & Term Structure")
    skew = skew_25delta(contracts_df, spot) if (contracts_df is not None and spot) else {}
    s1, s2, s3 = st.columns(3)

    skew_25 = skew.get("skew_25d")
    s1.metric(
        "📐 25-Delta IV Skew",
        f"{skew_25 * 100:.1f}%" if skew_25 is not None else "—",
        skew.get("skew_label", "insufficient data"),
        delta_color="off",
        help="Put 25Δ IV − Call 25Δ IV. Positive = puts bid (fear). "
             "Uses Dhan-provided delta directly — no BS inversion needed.",
    )

    near_iv = skew.get("near_atm_iv")
    next_iv = skew.get("next_atm_iv")
    near_exp = skew.get("near_expiry", "—")
    next_exp = skew.get("next_expiry", "—")
    s2.metric(
        f"📊 Near ATM IV ({near_exp})",
        f"{near_iv * 100:.1f}%" if near_iv else "—",
        "Front expiry ATM IV",
        delta_color="off",
    )
    s3.metric(
        f"📊 Next ATM IV ({next_exp})",
        f"{next_iv * 100:.1f}%" if next_iv else "—",
        skew.get("term_label", "insufficient data"),
        delta_color="off",
        help="Term structure: next expiry ATM IV vs front expiry ATM IV. "
             "Normal = back month IV higher (time premium). Inverted = stress.",
    )

    if skew.get("put_iv_25d") and skew.get("call_iv_25d"):
        st.caption(
            f"Put 25Δ IV: **{skew['put_iv_25d']*100:.1f}%**  ·  "
            f"Call 25Δ IV: **{skew['call_iv_25d']*100:.1f}%**  ·  "
            f"Skew: **{(skew['skew_25d'] or 0)*100:.1f}%**"
        )

    # Dealer gamma (from GEX engine — already computed)
    st.markdown("#### Dealer Gamma Exposure")
    if data.levels is not None:
        levels = data.levels
        gex_val = getattr(levels, "net_gex", None) or getattr(levels, "total_gex", None)
        gamma_flip = getattr(levels, "gamma_flip", None)

        g1, g2 = st.columns(2)
        if gex_val is not None:
            gex_m = gex_val / 1_000_000
            g1.metric(
                "⚡ Net Dealer GEX",
                f"₹{gex_m:.1f}M",
                "🟢 Positive (stabilising)" if gex_val > 0 else "🔴 Negative (amplifying)",
                delta_color="off",
                help="Positive GEX → dealers buy dips / sell rallies (damping). "
                     "Negative GEX → dealers amplify moves.",
            )
        if gamma_flip is not None:
            dist = gamma_flip - spot if spot else None
            g2.metric(
                "🔀 Gamma Flip Level",
                f"{gamma_flip:,.0f}",
                f"{dist:+,.0f} pts from spot" if dist is not None else "",
                delta_color="off",
                help="Level where dealer gamma flips sign. Below = negative gamma territory (volatile).",
            )
    else:
        st.info("GEX data not available — refresh a GEX tab first.")

    # India VIX
    if getattr(data, "india_vix", None):
        vix = data.india_vix
        st.metric(
            "📉 India VIX",
            f"{vix.get('last', 0):.2f}",
            f"{vix.get('change', 0):+.2f} ({vix.get('pct_change', 0):+.1f}%)",
            delta_color="inverse",
            help="India VIX = 30-day implied volatility of Nifty options (NSE formula).",
        )


# ---------------------------------------------------------------------------
# Section E — Signal Card
# ---------------------------------------------------------------------------

def _render_section_e(biases, data, gauge_metrics) -> None:
    from analytics.bias import divergence_trend, interpret_divergence, rolling_divergence
    from analytics.strikes import (combine_divergence_with_strikes, max_call_oi_strike,
                                   max_put_oi_strike, pcr_from_chain)
    from analytics.max_pain import max_pain, max_pain_distance
    import config

    st.markdown("### E — Signal Card")
    with st.expander("ℹ️ How to interpret this section", expanded=False):
        st.markdown(
            "**What it indicates**: Synthesizes the FII momentum, PCR proximity, and Volatility trends into a single composite daily bias (Bullish, Bearish, Neutral).\n\n"
            "**Decision making**: Use this as a strict directional filter. If the card says **Strong Bearish**, strictly avoid taking long trades that day and focus on shorting opportunities."
        )

    series = rolling_divergence(n=20)
    t5  = divergence_trend(series, 5)
    t10 = divergence_trend(series, 10)

    # Composite signal
    composite = _composite_signal(biases)
    conviction_icons = {0: "⚫", 1: "🔴", 2: "🟡", 3: "🟢", 4: "💚"}
    conv_icon = conviction_icons.get(composite["conviction"], "⚫")

    # Narrative
    narrative = interpret_divergence(
        divergence=biases.get("divergence", 0),
        retail_bias=biases.get("retail_bias", 0),
        institutional_bias=biases.get("institutional_bias", 0),
        trend_5d=t5,
        trend_10d=t10,
    )

    # Signal box
    st.info(
        f"**{composite['label']}**  ·  "
        f"{conv_icon} Conviction: {composite['conviction']}/4 factors aligned  "
        f"({', '.join(composite['aligned']) or 'none'})"
    )
    st.markdown(narrative)

    # Key watch levels
    if data and data.oi_strikes:
        per_strike = data.oi_strikes
        spot = data.levels.spot if data.levels else None
        max_call = max_call_oi_strike(per_strike)
        max_put  = max_put_oi_strike(per_strike)
        idx = config.get_index(getattr(data, "symbol", None))
        mp  = max_pain(per_strike, lot_size=idx.lot_size)

        levels_md = []
        if max_call:
            levels_md.append(f"- **Call wall (resistance)**: {max_call['strike']:,.0f}")
        if max_put:
            levels_md.append(f"- **Put wall (support)**: {max_put['strike']:,.0f}")
        if mp:
            mp_dist = max_pain_distance(mp["max_pain_strike"], spot or 0)
            levels_md.append(f"- **Max pain**: {mp['max_pain_strike']:,.0f} ({mp_dist:+,.0f} pts from spot)")
        if spot:
            levels_md.append(f"- **Spot**: {spot:,.0f}")

        if levels_md:
            st.markdown("**Watch levels:**")
            st.markdown("\n".join(levels_md))

        # Combined participant + strike narrative
        if max_call or max_put:
            strike_analysis = {
                "max_call": max_call, "max_put": max_put,
                "top_doi": [], "pcr": {},
            }
            combined = combine_divergence_with_strikes(biases, strike_analysis, spot or 0)
            st.success(combined)

    # Stock-futures gauge in a compact expander
    with st.expander("📊 Stock Futures Risk-Appetite Gauge (confidence filter)"):
        if gauge_metrics:
            g_rows = [
                {
                    "Participant":   g["category"],
                    "Net Stock Fut": f"{g['net_fut_stock']:+,.0f}",
                    "% Total OI":    f"{g['pct_of_total_oi']*100:.1f}%",
                    "60d Pctile":    str(g["percentile_rank_60d"]),
                    "DoD Delta":     f"{g['day_over_day_delta']:+,.0f}",
                }
                for g in gauge_metrics
            ]
            st.table(pd.DataFrame(g_rows))
            st.caption(
                "FII ≥80th pctile = high risk appetite → supports bullish bias. "
                "FII ≤20th pctile = risk-off → caution. "
                "DII large delta = possible hedge adjustment."
            )
        else:
            st.info("No stock-futures gauge data yet.")


# ---------------------------------------------------------------------------
# Section F — History / Backtest
# ---------------------------------------------------------------------------

def _render_section_f(days) -> None:
    from app.participant_oi import load_participant_day, derive_signals
    from data.storage import last_n_eod_futures

    st.markdown("### F — Participant Positioning vs Next-Day Return")
    st.caption(
        "Shows whether FII net-futures positioning predicts next-session direction. "
        "This is a **validation** section — build confidence in the model before trading it."
    )
    with st.expander("ℹ️ How to interpret this section", expanded=False):
        st.markdown(
            "**What it indicates**: Historical backtest correlating FII Divergence against the actual market returns a few days later.\n\n"
            "**Decision making**: Provides psychological conviction. Seeing a high historical win rate for rare +2.5 Z-Scores gives you the confidence to hold your positions during intraday dips."
        )

    if len(days) < 5:
        st.info(
            f"Only {len(days)} session(s) of participant OI data. "
            "Need ≥ 5 to show a meaningful backtest. "
            "Run `python scripts/backfill_daily.py --days 60 --only participant`."
        )
        return

    # Build cross-join: participant OI → next-day Nifty return
    import config
    eod_rows = last_n_eod_futures(config.DEFAULT_INDEX, n=60)
    eod_by_date: dict[str, dict] = {}
    for r in eod_rows:
        d = str(r.get("date_ist", ""))
        if d:
            eod_by_date[d] = r

    records = []
    sorted_eod_dates = sorted(eod_by_date.keys())

    for i, d in enumerate(days[:-1]):  # skip last (no next-day return yet)
        parsed = load_participant_day(d)
        if not parsed:
            continue
        sigs = (derive_signals(parsed) or {}).get("per_participant", {})
        fii_net_fut = sigs.get("FII", {}).get("net_fut")
        if fii_net_fut is None:
            continue

        # Next trading day with EOD data
        future_eod = [dd for dd in sorted_eod_dates if dd > d]
        if not future_eod:
            continue
        next_day = future_eod[0]
        next_eod = eod_by_date[next_day]

        close_raw = next_eod.get("close")
        prev_raw  = next_eod.get("prev_close")
        try:
            close = float(close_raw)
            prev  = float(prev_raw)
            ret_1d = (close - prev) / prev * 100 if prev > 0 else None
        except (TypeError, ValueError):
            ret_1d = None

        if ret_1d is None:
            continue

        records.append({
            "date":           d,
            "fii_net_fut_M":  fii_net_fut / 1_000_000,
            "ret_1d_pct":     ret_1d,
        })

    if not records:
        st.info(
            "No overlapping participant OI + EOD futures data. "
            "Ensure `data/journal/eod/NIFTY/` has been populated by the timeseries logger."
        )
        return

    df = pd.DataFrame(records)

    try:
        import altair as alt

        # Scatter: FII net-futures vs next-day return
        scatter = alt.Chart(df).mark_circle(size=80, opacity=0.75).encode(
            x=alt.X("fii_net_fut_M:Q", title="FII Net Futures (M lots)", zero=True),
            y=alt.Y("ret_1d_pct:Q", title="Next Day Return (%)", zero=True),
            tooltip=["date:O",
                     alt.Tooltip("fii_net_fut_M:Q", format=".2f", title="FII Net Fut (M)"),
                     alt.Tooltip("ret_1d_pct:Q",    format=".2f", title="Next 1d Return %")],
            color=alt.condition(
                alt.datum.ret_1d_pct > 0,
                alt.value("#4ade80"),
                alt.value("#f87171"),
            ),
        )
        # Zero reference lines
        zero_y = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
            color="rgba(255,255,255,0.25)", strokeDash=[4, 2]
        ).encode(y="y:Q")
        zero_x = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(
            color="rgba(255,255,255,0.25)", strokeDash=[4, 2]
        ).encode(x="x:Q")

        # Regression line
        reg = scatter.transform_regression(
            "fii_net_fut_M", "ret_1d_pct"
        ).mark_line(color="#facc15", strokeWidth=2, strokeDash=[8, 4])

        chart = (scatter + zero_y + zero_x + reg).properties(
            title="FII Net Futures vs Next-Day Nifty Return",
            height=300,
        )
        st.altair_chart(chart, use_container_width=True)
        st.caption(
            "Each dot = one trading session. Yellow line = OLS regression. "
            "Green = next day was up; red = next day was down. "
            "A tilted regression line suggests predictive edge — flat = no edge."
        )

        # Correlation summary
        corr = df["fii_net_fut_M"].corr(df["ret_1d_pct"])
        n = len(df)
        col1, col2 = st.columns(2)
        col1.metric(
            "📈 FII Futures → Next-Day Return Correlation",
            f"{corr:.3f}" if not math.isnan(corr) else "—",
            f"{n} sessions",
            delta_color="off",
            help="Pearson correlation. |r|>0.2 over ≥30 sessions is worth noting; "
                 "|r|>0.4 is economically meaningful.",
        )

        # Direction accuracy
        if n >= 5:
            correct = ((df["fii_net_fut_M"] > 0) == (df["ret_1d_pct"] > 0)).sum()
            accuracy = correct / n * 100
            col2.metric(
                "🎯 Directional Accuracy",
                f"{accuracy:.0f}%",
                f"{correct}/{n} sessions",
                delta_color="off",
                help="% of sessions where FII net-futures sign matched the next-day return sign.",
            )

    except Exception as e:
        st.caption(f"History chart unavailable: {e}")
        st.dataframe(df.head(20))
