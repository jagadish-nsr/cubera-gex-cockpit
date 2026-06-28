"""Dashboard data pipeline — NO streamlit import, so it is unit-testable offline.

load_dashboard() runs the full chain: pull (NSE or demo) -> snapshot -> filter
window -> compute_levels -> regime view, and returns a small bundle the UI renders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import config
from data.expiry import filter_expiry_window
from data.nse import from_nse_results
from gex.levels import Levels, compute_levels
from regime.classify import RegimeView, from_levels

WINDOW_CHOICES = ["0dte_to_next_monthly", "weekly", "0dte", "monthly", "all"]


@dataclass
class DashboardData:
    levels: Levels
    view: RegimeView
    fetched_at: datetime
    n_contracts: int
    expiries: list
    window: str
    source: str
    oi_volume: dict | None = None  # OI/ΔOI/volume aggregates (live NSE only)
    oi_strikes: list | None = None  # per-strike OI/ΔOI within a band of spot (live NSE)
    india_vix: dict | None = None  # {last, change, pct_change}; live NSE, best-effort
    usdinr: dict | None = None     # {last, change, pct_change, as_of}; USD/INR future
    news: dict | None = None       # {count, impact, top}; free RSS, best-effort


def load_dashboard(window: str | None = None, use_demo: bool = False) -> DashboardData:
    window = window or config.EXPIRY_WINDOW_DEFAULT
    fetched_at = datetime.now(timezone.utc)

    if use_demo:
        from data.demo import build_demo_rows

        rows, spot = build_demo_rows(fetched_at=fetched_at)
        source = "DEMO-SYNTHETIC"
        payload = None
        india_vix = None
        usdinr = None
        news = None
    else:
        from data.news import fetch_news
        from data.nse import (aggregate_oi_volume, fetch_chain, fetch_india_vix,
                              fetch_usdinr, nearest_expiry_premiums,
                              parse_nse_option_chain, per_strike_oi)

        payload = fetch_chain(config.UNDERLYING)   # single live pull
        rows, spot = parse_nse_option_chain(payload)
        india_vix = fetch_india_vix()              # secondary call, fail-soft
        usdinr = fetch_usdinr()                    # USD/INR future, fail-soft
        news = fetch_news()                        # free RSS, fail-soft
        source = "nse"

    snap = from_nse_results(
        rows, spot=spot, expiry_window=window,
        dealer_sign_model=config.DEALER_SIGN_MODEL, source=source,
        fetched_at=fetched_at, oi_timestamp=fetched_at, underlying=config.UNDERLYING,
    )
    snap.contracts = filter_expiry_window(snap.contracts, window, fetched_at.date())
    snap.validate()

    levels = compute_levels(
        snap, contract_size=config.CONTRACT_SIZE,
        grid_range=config.FLIP_GRID_RANGE, grid_step=config.FLIP_GRID_STEP,
        oi_stale_hours=config.OI_STALE_HOURS,
        expiry_weighting=config.EXPIRY_WEIGHTING,
        weight_halflife_days=config.EXPIRY_WEIGHT_HALFLIFE_DAYS,
    )
    view = from_levels(levels)
    expiries = sorted(snap.contracts["expiry"].unique().tolist())
    oi_volume = aggregate_oi_volume(payload) if payload is not None else None
    oi_strikes = (per_strike_oi(payload, spot=spot, band_pts=config.OI_STRIKE_BAND_PTS)
                  if payload is not None else None)
    if oi_strikes is not None:
        # Enrich each strike with the nearest-expiry premium (for the crossover
        # watch) and the engine's gamma exposure (net_gex, for the Δgamma map).
        prem = nearest_expiry_premiums(rows)
        gex = (dict(zip(levels.per_strike["strike"], levels.per_strike["net_gex"]))
               if levels.per_strike is not None else {})
        for s in oi_strikes:
            k = s["strike"]
            p = prem.get(k, {})
            s["call_ltp"] = p.get("call_ltp")
            s["put_ltp"] = p.get("put_ltp")
            s["net_gex"] = gex.get(k)
    return DashboardData(
        levels=levels, view=view, fetched_at=fetched_at,
        n_contracts=len(snap.contracts), expiries=expiries, window=window, source=source,
        oi_volume=oi_volume, oi_strikes=oi_strikes, india_vix=india_vix,
        usdinr=usdinr, news=news,
    )
