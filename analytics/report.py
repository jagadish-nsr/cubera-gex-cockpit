"""analytics/report.py — Assembles the §3.4 "probable chart" structured report.

Orchestrates bias (§3.1–3.2), strikes (§3.3) and the mandatory §6 caveats into
a single PositioningReport object that both the dashboard tab and any future CLI
command can render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# §6 Non-negotiable caveats — always present, never buried
# ---------------------------------------------------------------------------

CAVEATS: list[str] = [
    "This is a read of **positioning**, not a price prediction. It does not account "
    "for news, macro data, global cues, or cash-market flows.",
    "Category-level OI (Client/FII/DII/Pro) is an **aggregate proxy** — 'Client' "
    "includes retail and HNIs together, not retail alone.",
    "Single-session snapshots are noisy. The **trend view** (5/10/20-day) is more "
    "reliable than any one day's reading.",
    "This tool is for the user's own **decision support** and is not investment advice. "
    "You confirm every trade.",
]


# ---------------------------------------------------------------------------
# Report dataclass
# ---------------------------------------------------------------------------

@dataclass
class PositioningReport:
    """Structured daily positioning-divergence report (§3.4)."""

    # Metadata
    date_ist: str
    generated_at: datetime
    symbol: str
    spot: float | None

    # Category-level biases
    retail_bias:        int
    institutional_bias: int
    dii_bias:           int
    pro_bias:           int
    divergence:         int

    # Per-category breakdown (net_fut, net_call, net_put, bias)
    breakdown: dict = field(default_factory=dict)

    # Rolling trend across windows
    trend_5d:  dict = field(default_factory=dict)
    trend_10d: dict = field(default_factory=dict)
    trend_20d: dict = field(default_factory=dict)

    # Historical divergence series (for chart)
    divergence_series: list[dict] = field(default_factory=list)

    # Narrative
    narrative:      str = ""
    combined_read:  str = ""

    # Strike-level analysis
    max_call:   dict | None = None   # {"strike": float, "call_oi": float}
    max_put:    dict | None = None   # {"strike": float, "put_oi": float}
    top_doi:    list[dict] = field(default_factory=list)
    pcr:        dict = field(default_factory=dict)

    # Stock Futures Risk-Appetite Gauge
    risk_appetite_gauge: list[dict] = field(default_factory=list)

    # §6 caveats — always attached
    caveats: list[str] = field(default_factory=lambda: list(CAVEATS))


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_report(
    symbol: str = "NIFTY",
    window: str = "weekly",
    chain_rows: list[dict] | None = None,
    spot: float | None = None,
) -> PositioningReport:
    """Build a PositioningReport for the current session.

    chain_rows / spot: if supplied (e.g. from st.session_state.data already
    loaded on the GEX tab), no additional Dhan API call is made.  Pass None to
    trigger a fresh fetch — respects the 1-req/3s rate limit via a simple sleep.

    Steps:
    1. Load today's Participant OI (or latest cached) via app.participant_oi.
    2. Compute bias scores and rolling divergence trend.
    3. Compute strike-level analysis from the supplied (or freshly fetched) chain.
    4. Assemble and return a PositioningReport with the §6 caveats.
    """
    import time
    from app.participant_oi import capture_latest, latest_available
    from analytics.bias import (compute_category_biases, divergence_trend,
                                 interpret_divergence, rolling_divergence, WINDOWS)
    from analytics.strikes import (combine_divergence_with_strikes, max_call_oi_strike,
                                   max_put_oi_strike, pcr_from_chain, top_oi_change_strikes)

    # --- 1. Participant OI ---
    got = None
    try:
        got = capture_latest()
    except Exception:
        pass
    if got is None:
        got = latest_available()

    date_ist = "unknown"
    biases: dict = {}
    breakdown: dict = {}

    if got:
        date_ist, parsed = got
        biases = compute_category_biases(parsed)
        breakdown = biases.get("breakdown", {})

    # --- 1.5. Stock Futures Risk-Appetite Gauge ---
    from analytics.stock_futures_gauge import compute_gauge_metrics
    from app.participant_oi import available_days, load_participant_day

    gauge_metrics = []
    if got:
        days = available_days()
        today_idx = days.index(date_ist) if date_ist in days else len(days)
        
        yesterday_parsed = None
        if today_idx > 0:
            yesterday_parsed = load_participant_day(days[today_idx - 1])
            
        # Build history for rolling percentiles (excluding today)
        historical_pcts: dict[str, list[float]] = {"Client": [], "DII": [], "FII": [], "Pro": []}
        history_days = days[:today_idx]
        for d in history_days:
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


    # --- 2. Rolling divergence trend ---
    series = rolling_divergence(n=max(WINDOWS))
    t5  = divergence_trend(series, 5)
    t10 = divergence_trend(series, 10)
    t20 = divergence_trend(series, 20)

    narrative = interpret_divergence(
        divergence=biases.get("divergence", 0),
        retail_bias=biases.get("retail_bias", 0),
        institutional_bias=biases.get("institutional_bias", 0),
        trend_5d=t5,
        trend_10d=t10,
    )

    # --- 3. Strike-level analysis ---
    # If chain_rows not provided, do a fresh Dhan fetch (respecting rate limit)
    if chain_rows is None:
        try:
            import config
            from data.dhan import fetch_chain_dhan, per_strike_oi_dhan

            chain_rows, spot = fetch_chain_dhan(
                symbol,
                client_id=config.DHAN_CLIENT_ID,
                access_token=config.DHAN_ACCESS_TOKEN,
                window=window,
            )
            time.sleep(3)  # Dhan rate limit: 1 req / 3 sec
        except Exception:
            chain_rows = []

    from data.dhan import per_strike_oi_dhan

    per_strike = per_strike_oi_dhan(chain_rows, spot=spot) if chain_rows else []

    max_call = max_call_oi_strike(per_strike)
    max_put  = max_put_oi_strike(per_strike)
    top_doi  = top_oi_change_strikes(per_strike, n=3)
    pcr      = pcr_from_chain(chain_rows) if chain_rows else {}

    strike_analysis = {"max_call": max_call, "max_put": max_put,
                       "top_doi": top_doi, "pcr": pcr}

    combined_read = combine_divergence_with_strikes(biases, strike_analysis, spot or 0)
    
    # Inject Risk-Appetite narrative tags if conditions met
    gauge_narratives = []
    for g in gauge_metrics:
        cat = g["category"]
        rank = g["percentile_rank_60d"]
        delta = g["day_over_day_delta"]
        
        is_extreme = isinstance(rank, int) and (rank >= 80 or rank <= 20)
        is_delta_large = abs(delta) > 50000  # Configurable arbitrary threshold for narrative trigger
        
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
        combined_read += "\n\n**Stock Futures Risk-Appetite:**\n" + "\n".join(f"- {n}" for n in gauge_narratives)


    return PositioningReport(
        date_ist=date_ist,
        generated_at=datetime.now(timezone.utc),
        symbol=symbol,
        spot=spot,
        retail_bias=biases.get("retail_bias", 0),
        institutional_bias=biases.get("institutional_bias", 0),
        dii_bias=biases.get("DII", 0),
        pro_bias=biases.get("Pro", 0),
        divergence=biases.get("divergence", 0),
        breakdown=breakdown,
        trend_5d=t5,
        trend_10d=t10,
        trend_20d=t20,
        divergence_series=series,
        narrative=narrative,
        combined_read=combined_read,
        max_call=max_call,
        max_put=max_put,
        top_doi=top_doi,
        pcr=pcr,
        risk_appetite_gauge=gauge_metrics,
        caveats=list(CAVEATS),
    )
