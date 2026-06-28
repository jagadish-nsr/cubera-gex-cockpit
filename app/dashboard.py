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

import config  # noqa: E402
from app.data_access import WINDOW_CHOICES, load_dashboard  # noqa: E402

st.set_page_config(page_title="Nifty GEX", page_icon="📈", layout="wide")

# Keep in sync with IMPACT_FLAG in scripts/analyze_timeseries.py (the day-level gauge).
_NEWS_IMPACT_FLAG = 4


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
        save_ts = st.button(
            "📈 Save snapshot to time-series", width="stretch",
            help="Append the current levels to today's intraday series "
                 "(data/journal/timeseries/). Live NSE only.",
        )
        st.divider()
        auto = st.checkbox(
            "⏱️ Auto-refresh", value=False,
            help="Re-pull the chain on a timer (non-blocking).",
        )
        auto_mins = st.selectbox(
            "Interval", [1, 3, 5, 15], index=3,
            format_func=lambda m: f"{m} min", disabled=not auto,
        )
        auto_save = st.checkbox(
            "Save each tick to time-series", value=False, disabled=not auto,
            help="Append a snapshot on every auto tick (live NSE) — in-app "
                 "capture while the dashboard stays open.",
        )

    # Non-blocking auto-refresh timer (a new tick value => time to re-pull).
    tick = None
    if auto:
        try:
            from streamlit_autorefresh import st_autorefresh

            tick = st_autorefresh(interval=auto_mins * 60_000, key="auto_tick")
        except ModuleNotFoundError:
            st.sidebar.warning(
                "Auto-refresh needs `streamlit-autorefresh` "
                "(`pip install streamlit-autorefresh`). Using manual refresh."
            )
            auto = False

    st.session_state.setdefault("data", None)
    st.session_state.setdefault("err", None)

    auto_fired = auto and tick is not None and tick != st.session_state.get("last_tick")
    if auto:
        st.session_state["last_tick"] = tick

    if refresh or auto_fired or st.session_state.data is None:
        with st.spinner("Pulling the option chain & computing GEX…"):
            _refresh(window, use_demo)
    if auto:
        st.caption(f"⏱️ Auto-refreshing every {auto_mins} min.")

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
        if data.source == "nse":
            from app.timeseries import (append_row, append_strike_rows,
                                        build_row, build_strike_rows)

            p = append_row(build_row(data))
            append_strike_rows(build_strike_rows(data))   # per-strike OI, same fetch
            st.sidebar.success(f"Saved snapshot → {p.name}")
        else:
            st.sidebar.warning("Not saved — demo data is synthetic.")
    elif auto_save and auto_fired and data.source == "nse":
        from app.timeseries import (append_if_due, append_strike_rows, build_row,
                                    build_strike_rows, in_nse_session)

        # Session guard: a dashboard left open after 15:30 IST keeps re-pulling
        # the SAME frozen post-close chain — without this it would auto-save
        # dozens of duplicate rows (spot frozen, stale=False) and poison the
        # day's file. Only the explicit "Save snapshot" button overrides this.
        if not in_nse_session(data.fetched_at):
            st.sidebar.caption("⏸️ Outside NSE hours — auto-save paused.")
        else:
            # Guard just below the tick interval so a co-running capture loop (or
            # a double-fire) doesn't double up, without blocking the app's cadence.
            p, written = append_if_due(build_row(data), min_gap_minutes=max(1.0, auto_mins - 1.0))
            if written:
                append_strike_rows(build_strike_rows(data))  # lock-step with the row
                st.sidebar.success(f"Saved snapshot → {p.name}")

    # Accumulate each in-session live pull so the intraday chart persists across
    # auto-refresh reruns (session_state survives reruns) and the line grows --
    # independent of whether anything is being written to disk. Skip post-close
    # pulls so frozen end-of-day points don't flat-line the chart.
    if data.source == "nse":
        from app.timeseries import build_row, in_nse_session

        if in_nse_session(data.fetched_at):
            r = build_row(data)
            st.session_state.setdefault("ts_points", {})[r["timestamp_utc"]] = r

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
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Spot", f"{L.spot:,.2f}")
    c2.metric("Regime", L.regime)
    c3.metric("Total net GEX", f"{L.total_net_gex:,.0f}")
    vix = data.india_vix
    if vix:
        chg = vix.get("pct_change")
        c4.metric("India VIX", f"{vix['last']:.2f}",
                  f"{chg:+.2f}%" if chg is not None else None, delta_color="off")
    else:
        c4.metric("India VIX", "—", help="Implied vol; live NSE only, best-effort.")
    usdinr = data.usdinr
    if usdinr:
        uchg = usdinr.get("pct_change")
        c5.metric("USD/INR (NSE fut)", f"{usdinr['last']:.4f}",
                  f"{uchg:+.2f}%" if uchg is not None else None, delta_color="off",
                  help="NSE near-month USD/INR future — the rupee level; live NSE, best-effort.")
    else:
        c5.metric("USD/INR (NSE fut)", "—",
                  help="NSE near-month USD/INR future; live NSE only, best-effort.")

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

    # --- Intraday time-series (Phase A) -----------------------------------
    _timeseries_section(data)

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
    cs = suggest_structure(L, V, lot_size=config.CONTRACT_SIZE, nearest_expiry=nearest,
                           is_expiry_day=is_expiry_day)

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


def _timeseries_section(data):
    """Phase A: spot vs the migrating levels through the day, + OI/volume build-up."""
    from app.timeseries import available_days, load_day

    ist_today = (data.fetched_at + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    # Merge today's on-disk captures (scheduler / saved) with the points pulled
    # this browser session (held in session_state so they survive auto-refresh
    # reruns), deduped by timestamp -> the line persists and grows.
    disk = load_day(ist_today)
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
        days = available_days()
        if not days:
            st.markdown("#### Intraday time-series")
            st.caption(
                "No snapshots yet. Points accumulate here as you refresh (live NSE); "
                "they also persist to disk if you enable **Save each tick** (sidebar) "
                "or run `python scripts/log_timeseries.py`."
            )
            return
        label = days[-1]            # fall back to the most recent captured day
        df = load_day(label)

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
        st.altair_chart(chart, width="stretch")
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
