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
    charm_now: float | None = None  # live dealer-charm exposure (better vol metric at 0-DTE)
    dte_now: float | None = None    # days to nearest expiry (picks vanna vs charm for the gauge)
    day_ohlc: dict | None = None    # true index day OHLC {open,high,low,close,ltp} (Dhan feed only)
    source_note: str | None = None  # human note when the feed fell back (e.g. Dhan token expired)
    proxy_vanna: float | None = None  # live |vanna| of the regime-proxy index (SENSEX borrows NIFTY)
    proxy_symbol: str | None = None   # which index the regime is borrowed from (None = own)
    sectors: dict | None = None       # {rows:[{label,pct,last,group}], source} sector-rotation strip
    contracts: object = None           # options snapshot contracts DataFrame (v2 tab strike analysis)


def _live_greeks(contracts, spot: float, lot: float) -> dict:
    """Current dealer VANNA & CHARM exposure |Σ greek·(callOI−putOI)| over the ~21 nearest strikes
    of the nearest expiry, ×lot to match the Dhan cache scale (Dhan OI is already in shares) —
    plus days-to-expiry. The gauge uses charm ≤1.5 DTE (sharper there, Study #8/T1b) else vanna.
    Returns {vanna, charm, dte} (values may be None) — non-essential, never raises."""
    import numpy as np
    from scipy.stats import norm
    out = {"vanna": None, "charm": None, "dte": None}
    try:
        df = contracts.dropna(subset=["strike", "oi", "iv", "T"])
        exp = df["expiry"].min()
        d = df[df["expiry"] == exp]
        T = float(d["T"].iloc[0])
        if not (T > 0) or not (spot > 0) or not lot:
            return out
        out["dte"] = T * 365.0
        typ = d["type"].astype(str).str.upper()
        calls = d[typ.str.startswith(("C",))].set_index("strike")
        puts = d[typ.str.startswith(("P",))].set_index("strike")
        strikes = sorted(set(calls.index) & set(puts.index), key=lambda k: abs(k - spot))[:21]
        sq = T ** 0.5
        vanna = charm = 0.0
        for k in strikes:
            coi = float(calls.loc[k, "oi"]); poi = float(puts.loc[k, "oi"])
            iv = float(calls.loc[k, "iv"]) if k >= spot else float(puts.loc[k, "iv"])
            if not (iv == iv and iv > 0):
                continue
            d1 = (np.log(spot / k) + 0.5 * iv * iv * T) / (iv * sq)
            d2 = d1 - iv * sq
            phi = norm.pdf(d1)
            vanna += (-phi * d2 / iv) * (coi - poi)
            charm += (phi * d2 / (2.0 * T)) * (coi - poi)
        out["vanna"] = abs(vanna) * float(lot)
        out["charm"] = abs(charm) * float(lot)
        return out
    except Exception:  # noqa: BLE001 - gauge overlay is non-essential
        return out


def _fetch_live_chain(idx, live_source: str | None):
    """Pull the LIVE chain as an NSE-shaped payload, honouring the Dhan-primary / NSE-backup
    policy (config.LIVE_SOURCE). Returns (payload, source, day_ohlc, note). Dhan failures (expired
    24h token, un-subscribed Data API, network, or a shape mismatch that makes the adapter raise)
    fall back to the NSE scrape — a stale token degrades the feed, never breaks the dashboard."""
    from data.nse import fetch_chain as nse_fetch
    pref = (live_source or config.LIVE_SOURCE or "auto").lower()
    note = None
    if pref in ("dhan", "auto"):
        has_token = bool(config.DHAN_CLIENT_ID and config.DHAN_ACCESS_TOKEN)
        if pref == "dhan" or has_token:
            try:
                from data import dhan_live
                payload = dhan_live.fetch_chain(idx.symbol)     # throttled, ≥3s/expiry
                ohlc = dhan_live.fetch_index_ohlc(idx.symbol)   # true day OHLC, best-effort
                return payload, "dhan", ohlc, None
            except Exception as e:  # noqa: BLE001 - fall back to NSE, flagged
                note = f"Dhan unavailable ({type(e).__name__}: {str(e)[:120]}) — using NSE."
        elif pref == "auto":
            note = "No Dhan token — using NSE. Paste a token to enable the Dhan feed."
    payload = nse_fetch(idx.symbol, has_weekly=idx.has_weekly)
    source = "nse (Dhan fallback)" if note and "using NSE" in note and pref != "nse" else "nse"
    return payload, source, None, note


def _proxy_vanna(proxy_symbol: str, live_source: str | None,
                 sign_model: str | None = None) -> float | None:
    """Live dealer-|vanna| of a correlated proxy index (e.g. NIFTY for SENSEX), for a BORROWED
    regime read. One nearest-expiry chain (Dhan if a token is present, else NSE), snapshot ->
    _live_greeks. Best-effort: None on ANY failure, so the regime just falls back to the index's
    own vanna. See the SENSEX-vanna study (NIFTY vanna grades SENSEX's move; SENSEX's own does not)."""
    try:
        from data.nse import parse_nse_option_chain
        pidx = config.get_index(proxy_symbol)
        pref = (live_source or config.LIVE_SOURCE or "auto").lower()
        has_token = bool(config.DHAN_CLIENT_ID and config.DHAN_ACCESS_TOKEN)
        if pref in ("dhan", "auto") and has_token:
            from data import dhan_live
            payload = dhan_live.fetch_chain(pidx.symbol, max_expiries=1)  # nearest expiry is enough
        else:
            from data.nse import fetch_chain as nse_fetch
            payload = nse_fetch(pidx.symbol, has_weekly=pidx.has_weekly)
        rows, spot = parse_nse_option_chain(payload)
        snap = from_nse_results(
            rows, spot=spot, expiry_window="all",
            dealer_sign_model=(sign_model or config.DEALER_SIGN_MODEL),
            fetched_at=datetime.now(timezone.utc), underlying=pidx.symbol,
        )
        return _live_greeks(snap.contracts, float(spot), pidx.lot_size).get("vanna")
    except Exception:  # noqa: BLE001 - borrowed regime is best-effort context
        return None


def _prefer_dhan(live_source) -> bool:
    """True when the live feed will be Dhan (token present + not forced to NSE) — in which case
    India VIX rides the bundled index-OHLC quote and no separate NSE VIX call is needed."""
    pref = (live_source or config.LIVE_SOURCE or "auto").lower()
    if pref == "nse":
        return False
    return bool(config.DHAN_CLIENT_ID and config.DHAN_ACCESS_TOKEN)


def load_dashboard(window: str | None = None, use_demo: bool = False,
                   symbol: str | None = None, live_source: str | None = None,
                   sign_model: str | None = None) -> DashboardData:
    """Load a full DashboardData bundle.

    Parameters
    ----------
    sign_model : str | None
        Dealer gamma sign convention. Pass explicitly per-tab to isolate models:
          - Tab 1 (remote/US): ``"long_call_short_put"``
          - Tab 2 (India SCSP): ``"short_call_short_put"``
        Falls back to ``config.DEALER_SIGN_MODEL`` when None (legacy callers).
    """
    window = window or config.EXPIRY_WINDOW_DEFAULT
    idx = config.get_index(symbol)             # resolves symbol -> lot size, weekly, band
    fetched_at = datetime.now(timezone.utc)
    day_ohlc = None
    source_note = None
    proxy_vanna = None

    if use_demo:
        from data.demo import build_demo_rows

        rows, spot = build_demo_rows(fetched_at=fetched_at)
        source = "DEMO-SYNTHETIC"
        payload = None
        india_vix = None
        usdinr = None
        news = None
        sectors = None
    else:
        import concurrent.futures as _cf
        from data.news import fetch_news
        from data.sectors import fetch_sector_snap
        from data.nse import (aggregate_oi_volume, nearest_expiry_premiums,
                              parse_nse_option_chain, per_strike_nearest_changes,
                              per_strike_oi)

        usdinr = None          # USD/INR retired — Dhan has no live contract and it's unresearched
        # Run news, the proxy pull, and (NSE-only) VIX CONCURRENTLY with the main chain so they hide
        # under its ~10-15s instead of adding serially. On Dhan, VIX rides the index-OHLC quote
        # (day_ohlc["vix"]) — no separate call. All fail-soft.
        _dhan = _prefer_dhan(live_source)
        _nse_vix = None
        # Sector strip: NOT on the fetch path. This returns the cached rows instantly and
        # self-refreshes on its own 15-min background timer, so the slow-moving rotation context
        # never competes with the chain / futures pull for time or worker threads.
        sectors = fetch_sector_snap()
        _resolved_sign_model = sign_model or config.DEALER_SIGN_MODEL
        with _cf.ThreadPoolExecutor(max_workers=3) as _ex:
            _fx = {"news": _ex.submit(fetch_news)}
            if idx.regime_proxy:
                _fx["proxy"] = _ex.submit(_proxy_vanna, idx.regime_proxy, live_source, _resolved_sign_model)
            if not _dhan:
                from data.nse import fetch_india_vix as _nvx
                _fx["vix"] = _ex.submit(_nvx)
            payload, source, day_ohlc, source_note = _fetch_live_chain(idx, live_source)
            rows, spot = parse_nse_option_chain(payload)
            news = _fx["news"].result()
            proxy_vanna = _fx["proxy"].result() if "proxy" in _fx else None
            _nse_vix = _fx["vix"].result() if "vix" in _fx else None
        india_vix = (day_ohlc or {}).pop("vix", None) or _nse_vix

    _resolved_sign_model = sign_model or config.DEALER_SIGN_MODEL
    snap = from_nse_results(
        rows, spot=spot, expiry_window=window,
        dealer_sign_model=_resolved_sign_model, source=source,
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
            s["call_oi_near"] = c.get("call_oi_near")   # nearest-expiry abs OI (trailing windows)
            s["put_oi_near"] = c.get("put_oi_near")
    gk = _live_greeks(snap.contracts, float(levels.spot), idx.lot_size)
    return DashboardData(
        levels=levels, view=view, fetched_at=fetched_at,
        contracts=snap.contracts,
        n_contracts=len(snap.contracts), expiries=expiries, window=window, source=source,
        symbol=idx.symbol, index_label=idx.label,
        oi_volume=oi_volume, oi_strikes=oi_strikes, india_vix=india_vix,
        usdinr=usdinr, news=news, vanna_now=gk["vanna"], charm_now=gk["charm"], dte_now=gk["dte"],
        day_ohlc=day_ohlc, source_note=source_note,
        proxy_vanna=proxy_vanna, proxy_symbol=(idx.regime_proxy if proxy_vanna is not None else None),
        sectors=sectors,
    )
