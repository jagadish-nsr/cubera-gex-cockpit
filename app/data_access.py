"""Dashboard data pipeline — NO streamlit import, so it is unit-testable offline.

load_dashboard() runs the full chain: pull (NSE / Breeze / Dhan / demo) -> snapshot ->
filter window -> compute_levels -> regime view, and returns a small bundle the
UI renders.

DATA_SOURCE flag (config.py):
  "icici_breeze" — default; ICICI Direct Breeze API (NIFTY, BANKNIFTY, SENSEX).
  "dhan"         — Dhan broker API (NIFTY, BANKNIFTY).
  "nse"          — legacy NSE scraper (NIFTY, BANKNIFTY only).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import config
from data.expiry import filter_expiry_window
from data.nse import from_nse_results
from gex.levels import Levels, compute_levels
from regime.classify import RegimeView, from_levels

WINDOW_CHOICES = ["0dte", "0dte_to_next_monthly", "weekly", "monthly", "all"]


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
    index_label: str = ""                # display name ("Nifty 50" / "Bank Nifty" / "Sensex")
    oi_volume: dict | None = None  # OI/ΔOI/volume aggregates (live fetch only)
    oi_strikes: list | None = None  # per-strike OI/ΔOI within a band of spot
    contracts: object = None       # options snapshot contracts DataFrame
    india_vix: dict | None = None  # {last, change, pct_change}; NSE only, best-effort
    usdinr: dict | None = None     # {last, change, pct_change, as_of}; USD/INR future
    news: dict | None = None       # {count, impact, top}; free RSS, best-effort


def _load_from_nse(idx, window, fetched_at):
    """Load dashboard data from the NSE scraper (original path, unchanged)."""
    if idx.symbol == "SENSEX":
        raise ValueError(
            "SENSEX is not available via the NSE data source. "
            "Set DATA_SOURCE=icici_breeze or DATA_SOURCE=dhan in your .env "
            "to use an API adapter that supports SENSEX."
        )
    from data.news import fetch_news
    from data.nse import (aggregate_oi_volume, fetch_chain, fetch_india_vix,
                          fetch_usdinr, nearest_expiry_premiums,
                          parse_nse_option_chain, per_strike_nearest_changes,
                          per_strike_oi)

    payload = fetch_chain(idx.symbol, has_weekly=idx.has_weekly)
    rows, spot = parse_nse_option_chain(payload)
    india_vix = fetch_india_vix()
    usdinr = fetch_usdinr()
    news = fetch_news()
    source = "nse"

    oi_volume = aggregate_oi_volume(payload)
    oi_strikes_raw = per_strike_oi(payload, spot=spot, band_pts=idx.band_pts)
    prem = nearest_expiry_premiums(rows)
    nc = per_strike_nearest_changes(payload, spot=spot, band_pts=idx.band_pts)

    return rows, spot, source, payload, india_vix, usdinr, news, oi_volume, oi_strikes_raw, prem, nc


def _load_from_breeze(idx, window, fetched_at):
    """Load dashboard data from ICICI Direct Breeze API."""
    from data.icici_breeze import (
        aggregate_oi_volume_breeze,
        fetch_chain_breeze,
        nearest_expiry_premiums_breeze,
        per_strike_oi_breeze,
    )

    api_key = config.ICICI_BREEZE_API_KEY
    api_secret = config.ICICI_BREEZE_API_SECRET
    session_token = config.ICICI_BREEZE_SESSION_TOKEN

    if not (api_key and api_secret and session_token):
        raise RuntimeError(
            "DATA_SOURCE=icici_breeze requires ICICI_BREEZE_API_KEY, "
            "ICICI_BREEZE_API_SECRET, and ICICI_BREEZE_SESSION_TOKEN to be set in .env. "
            "The session token must be refreshed daily."
        )

    rows, spot = fetch_chain_breeze(
        idx.symbol,
        api_key=api_key,
        api_secret=api_secret,
        session_token=session_token,
        has_weekly=idx.has_weekly,
        window=window,
        fetched_at=fetched_at,
        exchange=idx.exchange,
    )
    source = "icici_breeze"

    # Fetch supplementary market context (VIX, USDINR, News) via Breeze, fallback to NSE
    from data.icici_breeze import fetch_india_vix_breeze, fetch_usdinr_breeze
    try:
        from data.news import fetch_news
        news = fetch_news()
    except Exception:
        news = None
    try:
        india_vix = fetch_india_vix_breeze(
            api_key=api_key, api_secret=api_secret, session_token=session_token)
    except Exception:
        india_vix = None
    if india_vix is None:
        try:
            from data.nse import fetch_india_vix
            india_vix = fetch_india_vix()
        except Exception:
            india_vix = None
    try:
        usdinr = fetch_usdinr_breeze(
            api_key=api_key, api_secret=api_secret, session_token=session_token)
    except Exception:
        usdinr = None
    if usdinr is None:
        try:
            from data.nse import fetch_usdinr
            usdinr = fetch_usdinr()
        except Exception:
            usdinr = None

    oi_volume = aggregate_oi_volume_breeze(rows)
    oi_strikes_raw = per_strike_oi_breeze(rows, spot=spot, band_pts=idx.band_pts)
    prem = nearest_expiry_premiums_breeze(rows)
    # Breeze does not provide "change vs prev close" per strike on live chain;
    # we enrich from the EOD store (populated by the background seed below).
    nc: dict = _build_nc_from_eod_store(idx.symbol, spot, fetched_at)

    # --- Seed EOD historical store (best-effort, background-safe) ------------
    # Persist today's live snapshot to the local EOD store so that tomorrow's
    # ΔOI (doi) can be computed. Also backfills the last N trading days if
    # this is the first run (no local history).
    _seed_eod_store_if_needed(
        idx.symbol, spot, fetched_at, rows,
        api_key=api_key, api_secret=api_secret, session_token=session_token,
        exchange=idx.exchange,
    )

    payload = None   # NSE-specific extras not available via Breeze

    return rows, spot, source, payload, india_vix, usdinr, news, oi_volume, oi_strikes_raw, prem, nc


def _load_from_dhan(idx, window, fetched_at):
    """Load dashboard data from the Dhan broker API."""
    from data.dhan import (
        DhanCredentialError,
        aggregate_oi_volume_dhan,
        fetch_chain_dhan,
        nearest_expiry_premiums_dhan,
        per_strike_oi_dhan,
    )

    client_id    = config.DHAN_CLIENT_ID
    access_token = config.DHAN_ACCESS_TOKEN

    if not (client_id and access_token):
        raise RuntimeError(
            "DATA_SOURCE=dhan requires DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN "
            "to be set in your .env file. "
            "Generate them at: https://dhanhq.co/docs/latest/authentication/"
        )

    if idx.symbol == "SENSEX":
        raise ValueError(
            "SENSEX is not supported by the Dhan adapter. "
            "Set DATA_SOURCE=icici_breeze in your .env to access SENSEX."
        )

    rows, spot = fetch_chain_dhan(
        idx.symbol,
        client_id=client_id,
        access_token=access_token,
        has_weekly=idx.has_weekly,
        window=window,
        fetched_at=fetched_at,
        exchange=idx.exchange,
    )
    source = "dhan"

    # Supplementary market context — try Dhan first, fall back to NSE scrape
    from data.dhan import fetch_india_vix_dhan, fetch_usdinr_dhan
    try:
        from data.news import fetch_news
        news = fetch_news()
    except Exception:
        news = None
    try:
        india_vix = fetch_india_vix_dhan(client_id=client_id, access_token=access_token)
    except Exception:
        india_vix = None
    if india_vix is None:
        try:
            from data.nse import fetch_india_vix
            india_vix = fetch_india_vix()
        except Exception:
            india_vix = None
    try:
        usdinr = fetch_usdinr_dhan(client_id=client_id, access_token=access_token)
    except Exception:
        usdinr = None
    if usdinr is None:
        try:
            from data.nse import fetch_usdinr
            usdinr = fetch_usdinr()
        except Exception:
            usdinr = None

    oi_volume      = aggregate_oi_volume_dhan(rows)
    oi_strikes_raw = per_strike_oi_dhan(rows, spot=spot, band_pts=idx.band_pts)
    prem           = nearest_expiry_premiums_dhan(rows)
    nc: dict       = _build_nc_from_eod_store(idx.symbol, spot, fetched_at)

    payload = None  # NSE-specific extras not available via Dhan

    return rows, spot, source, payload, india_vix, usdinr, news, oi_volume, oi_strikes_raw, prem, nc


def _build_nc_from_eod_store(symbol: str, spot: float, fetched_at) -> dict:
    """Build the nearest-change (nc) dict from the local EOD store.

    Returns a dict keyed by strike with {call_doi_near, put_doi_near} populated
    from yesterday's EOD OI. Falls back to empty dict gracefully.
    """
    from datetime import timedelta
    from data.storage import load_oi_strikes_eod

    ist_today = (fetched_at + timedelta(hours=5, minutes=30)).date()
    nc: dict = {}
    # Try last 7 calendar days to find the most recent EOD data
    for offset in range(1, 8):
        prev_d = ist_today - timedelta(days=offset)
        prev_rows = load_oi_strikes_eod(symbol, prev_d)
        if prev_rows:
            for r in prev_rows:
                k = float(r.get("strike", 0))
                typ = str(r.get("type", ""))
                oi = float(r.get("oi") or 0)
                cell = nc.setdefault(k, {})
                if typ == "C":
                    cell["prev_call_oi"] = oi
                elif typ == "P":
                    cell["prev_put_oi"] = oi
            break
    return nc


def _seed_eod_store_if_needed(
    symbol: str, spot: float, fetched_at, live_rows: list[dict],
    *, api_key: str, api_secret: str, session_token: str, exchange: str = "NSE"
) -> None:
    """Persist today's option-chain snapshot to the EOD store (best-effort).

    Also backfills the last 5 trading days if we have no local history at all
    (first-run bootstrap). Runs in-process (not threaded) but all errors are
    swallowed so a failure never blocks the dashboard render.
    """
    import threading
    from datetime import timedelta
    from data.storage import available_eod_dates, save_oi_strikes_eod
    from data.expiry import next_monthly_expiry

    ist_today = (fetched_at + timedelta(hours=5, minutes=30)).date()

    def _seed():
        try:
            existing = available_eod_dates(symbol)

            # Persist today's live chain as EOD snapshot
            eod_rows = []
            for r in live_rows:
                eod_rows.append({
                    "date_ist": str(ist_today),
                    "symbol": symbol,
                    "expiry": str(r.get("expiry", "")),
                    "strike": r.get("strike"),
                    "type": r.get("type"),
                    "oi": r.get("oi"),
                    "doi": r.get("doi"),
                    "volume": r.get("vol"),
                    "close_price": r.get("price"),
                })
            if eod_rows:
                save_oi_strikes_eod(symbol, ist_today, eod_rows)

            # Backfill options: if we have fewer than 3 days of history, fetch the last 5 trading days
            if len(existing) < 3:
                from data.icici_breeze import fetch_options_eod_breeze
                expiry = next_monthly_expiry(ist_today, exchange)
                for offset in range(1, 6):
                    day = ist_today - timedelta(days=offset)
                    if day.weekday() >= 5:  # skip weekends
                        continue
                    if str(day) in [str(d) for d in existing]:
                        continue
                    fetch_options_eod_breeze(
                        symbol, day, spot, expiry,
                        api_key=api_key, api_secret=api_secret, session_token=session_token,
                    )

            # Backfill futures: if we have fewer than 3 days of history, fetch the last 15 calendar days of futures history
            if len(existing) < 3:
                from data.icici_breeze import fetch_futures_history_breeze
                from_date = ist_today - timedelta(days=15)
                fetch_futures_history_breeze(
                    symbol, from_date, ist_today,
                    api_key=api_key, api_secret=api_secret, session_token=session_token,
                    exchange=exchange,
                )
        except Exception:  # noqa: BLE001
            pass

    # Run in a daemon thread so it doesn't block the dashboard render
    t = threading.Thread(target=_seed, daemon=True)
    t.start()


def load_dashboard(window: str | None = None, use_demo: bool = False,
                   symbol: str | None = None) -> DashboardData:
    window = window or config.EXPIRY_WINDOW_DEFAULT
    idx = config.get_index(symbol)             # resolves symbol -> lot size, weekly, band
    fetched_at = datetime.now(timezone.utc)

    if use_demo:
        from data.demo import build_demo_rows
        from data.nse import nearest_expiry_premiums

        rows, spot = build_demo_rows(fetched_at=fetched_at)
        source = "DEMO-SYNTHETIC"
        payload = None
        india_vix = None
        usdinr = None
        news = None
        oi_volume = None
        
        # Synthesize oi_strikes_raw from demo rows
        oi_strikes_raw = []
        for r in rows:
            k = float(r["strike"])
            cell = next((c for c in oi_strikes_raw if c["strike"] == k), None)
            if not cell:
                cell = {"strike": k, "call_oi": 0.0, "call_doi": 0.0, "call_vol": 0.0,
                        "put_oi": 0.0, "put_doi": 0.0, "put_vol": 0.0}
                oi_strikes_raw.append(cell)
            if r["type"] == "C":
                cell["call_oi"] += float(r["oi"])
            else:
                cell["put_oi"] += float(r["oi"])
        oi_strikes_raw.sort(key=lambda x: x["strike"])
        
        prem = nearest_expiry_premiums(rows)
        nc: dict = {}
    elif config.DATA_SOURCE == "dhan":
        rows, spot, source, payload, india_vix, usdinr, news, oi_volume, oi_strikes_raw, prem, nc = \
            _load_from_dhan(idx, window, fetched_at)
    elif config.DATA_SOURCE == "icici_breeze":
        rows, spot, source, payload, india_vix, usdinr, news, oi_volume, oi_strikes_raw, prem, nc = \
            _load_from_breeze(idx, window, fetched_at)
    else:
        rows, spot, source, payload, india_vix, usdinr, news, oi_volume, oi_strikes_raw, prem, nc = \
            _load_from_nse(idx, window, fetched_at)

    snap = from_nse_results(
        rows, spot=spot, expiry_window=window,
        dealer_sign_model=config.DEALER_SIGN_MODEL, source=source,
        fetched_at=fetched_at, oi_timestamp=fetched_at, underlying=idx.symbol,
    )

    # --- Post-close expiry-day handling ---
    # When a 0dte/weekly window is selected and the market has closed on expiry day,
    # T becomes ≤ 0 for those contracts. Advance the filter ref by one day so we
    # fall through to the next weekly/monthly expiry automatically, rather than
    # returning an empty chain or crashing on parity T≤0.
    from datetime import timedelta
    filter_ref = fetched_at.date()
    ist_now = fetched_at + timedelta(hours=5, minutes=30)
    market_closed_today = ist_now.hour * 100 + ist_now.minute >= 1530
    if market_closed_today and window in ("0dte", "weekly"):
        # Try with advanced ref; fall back to current ref if it still yields contracts
        from data.expiry import filter_expiry_window as _few
        next_ref = filter_ref + timedelta(days=1)
        advanced = _few(snap.contracts, window, next_ref,
                        has_weekly=idx.has_weekly, exchange=idx.exchange)
        if not advanced.empty:
            filter_ref = next_ref

    snap.contracts = filter_expiry_window(
        snap.contracts, window, filter_ref,
        has_weekly=idx.has_weekly, exchange=idx.exchange,
    )
    if snap.contracts.empty:
        raise ValueError(
            f"No contracts found for the '{window}' window for {idx.symbol}. "
            "After market close on expiry day the 0dte/weekly window may be empty — "
            "try switching to 'weekly' or 'monthly' window."
        )
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

    # Build oi_strikes with enriched fields (premiums, gex, nearest-expiry doi)
    oi_strikes: list | None = None
    if oi_strikes_raw is not None:
        gex = (dict(zip(levels.per_strike["strike"], levels.per_strike["net_gex"]))
               if levels.per_strike is not None else {})
        for s in oi_strikes_raw:
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
        oi_strikes = oi_strikes_raw

    return DashboardData(
        levels=levels, view=view, fetched_at=fetched_at,
        n_contracts=len(snap.contracts), contracts=snap.contracts, expiries=expiries, window=window, source=source,
        symbol=idx.symbol, index_label=idx.label,
        oi_volume=oi_volume, oi_strikes=oi_strikes, india_vix=india_vix,
        usdinr=usdinr, news=news,
    )
