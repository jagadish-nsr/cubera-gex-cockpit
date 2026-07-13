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
    symbol: str = config.DEFAULT_INDEX   # which index this bundle is for
    index_label: str = ""                # display name ("Nifty 50" / "Bank Nifty")
    oi_volume: dict | None = None  # OI/ΔOI/volume aggregates (live NSE only)
    oi_strikes: list | None = None  # per-strike OI/ΔOI within a band of spot (live NSE)
    india_vix: dict | None = None  # {last, change, pct_change}; live NSE, best-effort
    usdinr: dict | None = None     # {last, change, pct_change, as_of}; USD/INR future
    news: dict | None = None       # {count, impact, top}; free RSS, best-effort
    vanna_now: float | None = None  # live dealer-vanna exposure (cache units) for the regime gauge


def _live_vanna(contracts, spot: float, lot: float) -> float | None:
    """Current dealer-vanna exposure |Σ vanna·(callOI−putOI)| over the ~21 nearest strikes of the
    nearest expiry, ×lot so it matches the Dhan feature-cache scale (Dhan OI is already in shares).
    The gauge's regime metric (sharper vol predictor than |GEX|, Study #8). None if unusable."""
    import numpy as np
    from scipy.stats import norm
    try:
        df = contracts.dropna(subset=["strike", "oi", "iv", "T"])
        exp = df["expiry"].min()
        d = df[df["expiry"] == exp]
        T = float(d["T"].iloc[0])
        if not (T > 0) or not (spot > 0) or not lot:
            return None
        typ = d["type"].astype(str).str.upper()
        calls = d[typ.str.startswith(("C",))].set_index("strike")
        puts = d[typ.str.startswith(("P",))].set_index("strike")
        strikes = sorted(set(calls.index) & set(puts.index), key=lambda k: abs(k - spot))[:21]
        sq = T ** 0.5
        total = 0.0
        for k in strikes:
            coi = float(calls.loc[k, "oi"]); poi = float(puts.loc[k, "oi"])
            iv = float(calls.loc[k, "iv"]) if k >= spot else float(puts.loc[k, "iv"])
            if not (iv == iv and iv > 0):
                continue
            d1 = (np.log(spot / k) + 0.5 * iv * iv * T) / (iv * sq)
            d2 = d1 - iv * sq
            total += (-norm.pdf(d1) * d2 / iv) * (coi - poi)
        return abs(total) * float(lot)
    except Exception:  # noqa: BLE001 - gauge overlay is non-essential
        return None


def load_dashboard(window: str | None = None, use_demo: bool = False,
                   symbol: str | None = None) -> DashboardData:
    window = window or config.EXPIRY_WINDOW_DEFAULT
    idx = config.get_index(symbol)             # resolves symbol -> lot size, weekly, band
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
                              parse_nse_option_chain, per_strike_nearest_changes,
                              per_strike_oi)

        payload = fetch_chain(idx.symbol, has_weekly=idx.has_weekly)  # single live pull
        rows, spot = parse_nse_option_chain(payload)
        india_vix = fetch_india_vix()              # secondary call, fail-soft
        usdinr = fetch_usdinr()                    # USD/INR future, fail-soft
        news = fetch_news()                        # free RSS, fail-soft
        source = "nse"

    snap = from_nse_results(
        rows, spot=spot, expiry_window=window,
        dealer_sign_model=config.DEALER_SIGN_MODEL, source=source,
        fetched_at=fetched_at, oi_timestamp=fetched_at, underlying=idx.symbol,
    )
    snap.contracts = filter_expiry_window(snap.contracts, window, fetched_at.date(),
                                          has_weekly=idx.has_weekly)
    snap.validate()

    levels = compute_levels(
        snap, contract_size=idx.lot_size,
        grid_range=config.FLIP_GRID_RANGE, grid_step=config.FLIP_GRID_STEP,
        oi_stale_hours=config.OI_STALE_HOURS,
        expiry_weighting=config.EXPIRY_WEIGHTING,
        weight_halflife_days=config.EXPIRY_WEIGHT_HALFLIFE_DAYS,
    )
    view = from_levels(levels)
    expiries = sorted(snap.contracts["expiry"].unique().tolist())
    oi_volume = aggregate_oi_volume(payload) if payload is not None else None
    oi_strikes = (per_strike_oi(payload, spot=spot, band_pts=idx.band_pts)
                  if payload is not None else None)
    if oi_strikes is not None:
        # Enrich each strike with the nearest-expiry premium (for the crossover
        # watch) and the engine's gamma exposure (net_gex, for the Δgamma map).
        prem = nearest_expiry_premiums(rows)
        # Nearest-expiry OI-change + premium-change (both vs prev close) so the
        # footprint build-up tag is exact (aligned expiry + window), not approximate.
        nc = per_strike_nearest_changes(payload, spot=spot, band_pts=idx.band_pts)
        gex = (dict(zip(levels.per_strike["strike"], levels.per_strike["net_gex"]))
               if levels.per_strike is not None else {})
        for s in oi_strikes:
            k = s["strike"]
            p = prem.get(k, {})
            s["call_ltp"] = p.get("call_ltp")
            s["put_ltp"] = p.get("put_ltp")
            s["net_gex"] = gex.get(k)
            c = nc.get(k, {})
            s["call_doi_near"] = c.get("call_doi_near")
            s["put_doi_near"] = c.get("put_doi_near")
            s["call_dprem"] = c.get("call_dprem")
            s["put_dprem"] = c.get("put_dprem")
    vanna_now = _live_vanna(snap.contracts, float(levels.spot), idx.lot_size)
    return DashboardData(
        levels=levels, view=view, fetched_at=fetched_at,
        n_contracts=len(snap.contracts), expiries=expiries, window=window, source=source,
        symbol=idx.symbol, index_label=idx.label,
        oi_volume=oi_volume, oi_strikes=oi_strikes, india_vix=india_vix,
        usdinr=usdinr, news=news, vanna_now=vanna_now,
    )
