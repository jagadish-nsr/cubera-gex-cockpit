"""NSE Nifty options adapter — STUB.

Mirrors the SPX project's data/polygon.py split:
  - fetch_chain(...)        : live pull from the chosen NSE source  (NOT built)
  - from_nse_results(...)   : pure mapping raw -> OptionsSnapshot   (skeleton)

⚠️ The data SOURCE is the gating decision (docs/nifty-nse-adapter-scope.md §5).
Pick ONE and verify live before implementing fetch_chain:
  - Official NSE option-chain endpoint (free, but cookie/header-gated, rate-
    limited, blocks bots — OK for a PoC, fragile as a daily driver).
  - Broker API (Kite Connect / Upstox / Fyers / Angel / Dhan) — paid, stable;
    verify option-chain + OI + greeks coverage and pricing LIVE.
  - Data vendor (TrueData / GlobalDatafeeds / ...).

What the engine needs per contract (same as SPX): strike, type, OI, IV (or a
price to derive IV), expiry, plus the Nifty spot (NSE feeds usually include it).
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

import pandas as pd

from data.schema import CONTRACT_COLUMNS, OptionsSnapshot

# NSE close ~15:30 IST = 10:00 UTC. Documented approximation for time-to-expiry.
_SETTLE_HOUR_UTC = 10


def years_to_expiry(expiry: date, now: datetime) -> float:
    settle = datetime.combine(expiry, time(hour=_SETTLE_HOUR_UTC), tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max((settle - now).total_seconds(), 0.0) / (365.25 * 24 * 3600.0)


def from_nse_results(
    rows: list[dict],
    *,
    spot: float,
    expiry_window: str,
    dealer_sign_model: str,
    source: str = "nse",
    fetched_at: datetime | None = None,
    oi_timestamp: datetime | None = None,
    underlying: str = "NIFTY",
) -> OptionsSnapshot:
    """Map normalised NSE chain rows into the common OptionsSnapshot.

    Each input row is expected to provide: expiry (date|str 'YYYY-MM-DD'),
    strike (float), type ('C'/'P' or 'CE'/'PE'), oi, iv (decimal, optional),
    price/ltp (optional, for IV inversion). Adapt the key names to the chosen
    source in ONE place here.
    """
    fetched_at = fetched_at or datetime.now(timezone.utc)
    out = []
    for r in rows:
        exp = r["expiry"]
        if isinstance(exp, str):
            exp = datetime.strptime(exp, "%Y-%m-%d").date()
        t = r["type"].upper()
        t = "C" if t in ("C", "CE", "CALL") else "P"
        out.append({
            "expiry": exp,
            "strike": float(r["strike"]),
            "type": t,
            "oi": float(r.get("oi") or 0.0),
            "iv": float(r["iv"]) if r.get("iv") is not None else float("nan"),
            "gamma": float("nan"),               # recomputed from IV by the engine
            "mid": float(r["price"]) if r.get("price") is not None else float("nan"),
            "T": years_to_expiry(exp, fetched_at),
        })

    contracts = pd.DataFrame(out, columns=CONTRACT_COLUMNS)
    if contracts.empty:
        raise ValueError("NSE chain produced no contracts")

    snap = OptionsSnapshot(
        underlying=underlying,
        spot=float(spot),
        contracts=contracts,
        oi_timestamp=oi_timestamp or fetched_at,
        dealer_sign_model=dealer_sign_model,
        expiry_window=expiry_window,
        source=source,
        fetched_at=fetched_at,
    )
    snap.validate()
    return snap


def parse_nse_option_chain(payload: dict) -> tuple[list[dict], float]:
    """Convert the official NSE option-chain JSON into normalised rows + spot.

    Source: https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY
    Shape: payload["records"]["data"] is a list of rows, each with strikePrice,
    expiryDate ("25-Jun-2026"), and optional "CE"/"PE" dicts carrying
    openInterest, impliedVolatility (PERCENT), lastPrice, bidprice, askPrice.
    payload["records"]["underlyingValue"] is the Nifty spot.

    NOTE: NSE impliedVolatility is in PERCENT (e.g. 13.45) -> divided by 100 here.
    openInterest is a CONTRACT count (multiplied by lot size downstream).
    """
    records = payload.get("records") or {}
    filtered = payload.get("filtered") or {}
    data = records.get("data") or filtered.get("data") or []
    spot = (
        records.get("underlyingValue")
        or filtered.get("underlyingValue")
        or payload.get("underlyingValue")
    )
    if spot is None:
        raise ValueError("NSE payload missing underlyingValue (spot)")

    def _mid(side: dict) -> float | None:
        # v3 uses buyPrice1/sellPrice1; the old endpoint used bidprice/askPrice.
        bid = side.get("buyPrice1") or side.get("bidprice")
        ask = side.get("sellPrice1") or side.get("askPrice")
        if bid and ask and bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        lp = side.get("lastPrice")
        return lp if lp else None

    def _iv(side: dict):
        iv = side.get("impliedVolatility")
        return (iv / 100.0) if iv else None  # percent -> decimal; 0/None -> None

    rows: list[dict] = []
    for row in data:
        for side_key, typ in (("CE", "C"), ("PE", "P")):
            side = row.get(side_key)
            if not side:
                continue
            # v3: expiry lives in the side dict (DD-MM-YYYY); old endpoint: row-level
            # (DD-Mon-YYYY). Fall back to the row's expiryDates list if a side omits it.
            exp_raw = side.get("expiryDate") or row.get("expiryDate")
            if not exp_raw:
                ed = row.get("expiryDates")
                if isinstance(ed, (list, tuple)) and ed:
                    exp_raw = ed[0]
                elif isinstance(ed, str):
                    exp_raw = ed
            if not exp_raw:
                continue  # cannot place on the expiry timeline -> skip this contract
            strike = side.get("strikePrice", row.get("strikePrice"))
            rows.append({
                "expiry": _parse_nse_date(exp_raw),
                "strike": float(strike),
                "type": typ,
                "oi": side.get("openInterest") or 0.0,
                "iv": _iv(side),
                "price": _mid(side),
            })
    if not rows:
        raise ValueError("NSE payload produced no contracts")
    return rows, float(spot)


def aggregate_oi_volume(payload: dict) -> dict:
    """Aggregate OI / ΔOI / volume across the chain, split call vs put.

    Pulled straight from the raw NSE payload because the engine schema
    deliberately drops these (the GEX maths doesn't need them). The time-series
    uses them to track *OI build-up* (put OI building = support forming; call OI
    building = resistance forming) and *volume build-up* alongside the levels.

    Scope = whatever expiries the feed returned (labelled in the UI). ``pcr_oi``
    is put/call OI. Defensive: a missing field counts as 0, never raises.
    """
    records = payload.get("records") or {}
    filtered = payload.get("filtered") or {}
    data = records.get("data") or filtered.get("data") or []
    agg = {k: 0.0 for k in
           ("call_oi", "put_oi", "call_doi", "put_doi", "call_vol", "put_vol")}
    for row in data:
        for side_key, pfx in (("CE", "call"), ("PE", "put")):
            side = row.get(side_key)
            if not side:
                continue
            agg[f"{pfx}_oi"] += float(side.get("openInterest") or 0.0)
            agg[f"{pfx}_doi"] += float(side.get("changeinOpenInterest") or 0.0)
            agg[f"{pfx}_vol"] += float(side.get("totalTradedVolume") or 0.0)
    agg["pcr_oi"] = (agg["put_oi"] / agg["call_oi"]) if agg["call_oi"] else float("nan")
    return agg


def per_strike_oi(payload: dict, spot: float | None = None,
                  band_pts: float | None = None) -> list[dict]:
    """Per-strike OI / ΔOI / volume, split call vs put — one dict per strike.

    Same raw fields as ``aggregate_oi_volume`` (openInterest, changeinOpenInterest,
    totalTradedVolume), but kept per strike instead of summed, so Phase B can ask
    whether price respects the strike where OI is BUILDING (a put-OI build acting
    as support, a call-OI build as resistance) — the question aggregate ΔOI can't
    answer. Strikes are summed across whatever expiries the feed returned (same
    scope as the aggregate). When ``spot`` and ``band_pts`` are given, only strikes
    within ``band_pts`` of spot are returned (far-OTM strikes carry negligible OI).

    Each dict: strike, call_oi, call_doi, call_vol, put_oi, put_doi, put_vol.
    Defensive: a missing field counts as 0, never raises. Sorted by strike.
    """
    records = payload.get("records") or {}
    filtered = payload.get("filtered") or {}
    data = records.get("data") or filtered.get("data") or []
    by_strike: dict[float, dict] = {}
    for row in data:
        k = row.get("strikePrice")
        if k is None:
            continue
        k = float(k)
        if spot is not None and band_pts is not None and abs(k - spot) > band_pts:
            continue
        cell = by_strike.setdefault(k, {
            "strike": k, "call_oi": 0.0, "call_doi": 0.0, "call_vol": 0.0,
            "put_oi": 0.0, "put_doi": 0.0, "put_vol": 0.0})
        for side_key, pfx in (("CE", "call"), ("PE", "put")):
            side = row.get(side_key)
            if not side:
                continue
            cell[f"{pfx}_oi"] += float(side.get("openInterest") or 0.0)
            cell[f"{pfx}_doi"] += float(side.get("changeinOpenInterest") or 0.0)
            cell[f"{pfx}_vol"] += float(side.get("totalTradedVolume") or 0.0)
    return [by_strike[k] for k in sorted(by_strike)]


def parse_india_vix(payload: dict) -> dict | None:
    """Extract India VIX from an NSE allIndices payload.

    Returns {last, change, pct_change} or None if absent/malformed. India VIX is
    the market's *implied* (expected) 30-day vol — complementary to GEX (which is
    a *positioning* read) and, crucially, computed without our dealer-sign
    assumption, so it's an independent anchor for the Phase B sign validation.
    """
    for d in payload.get("data") or []:
        name = (d.get("indexSymbol") or d.get("index") or "").upper()
        if name == "INDIA VIX":
            last = d.get("last")
            if last is None:
                return None
            def _num(x):
                return float(x) if x is not None else None
            return {"last": float(last), "change": _num(d.get("variation")),
                    "pct_change": _num(d.get("percentChange"))}
    return None


def parse_usdinr(payload: dict) -> dict | None:
    """Extract the USD/INR near-month futures last from an NSE marketStatus payload.

    NSE's marketStatus carries a ``currencyfuture`` marketState entry — the headline
    USD/INR future (the rupee level a trader watches). Returns
    {last, change, pct_change, as_of} or None if absent/empty (e.g. a blank ``last``
    on a holiday). ``variation``/``percentChange`` are populated live, blank at close.
    USD/INR is FX context, independent of our dealer-sign assumption.
    """
    for m in payload.get("marketState") or []:
        if (m.get("market") or "").lower() == "currencyfuture":
            last = m.get("last")
            try:
                last = float(last)
            except (TypeError, ValueError):
                return None

            def _num(x):
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None

            return {"last": last, "change": _num(m.get("variation")),
                    "pct_change": _num(m.get("percentChange")), "as_of": m.get("tradeDate")}
    return None


def _parse_nse_date(s: str):
    """Parse NSE date strings. v3 responses use DD-MM-YYYY ('30-06-2026');
    the v3 request param and the old endpoint use DD-Mon-YYYY ('30-Jun-2026')."""
    from datetime import datetime as _dt
    for fmt in ("%d-%m-%Y", "%d-%b-%Y"):
        try:
            return _dt.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised NSE date: {s!r}")


# Browser-like headers confirmed working from a live capture of the NSE
# option-chain page (Akamai bot-manager passes with these + primed cookies).
# Accept-Encoding is limited to gzip/deflate so `requests` can decode it without
# the brotli/zstd packages.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.nseindia.com/option-chain",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

_V3_URL = "https://www.nseindia.com/api/option-chain-v3"
_INDICES_URL = "https://www.nseindia.com/api/option-chain-indices"
_ALL_INDICES_URL = "https://www.nseindia.com/api/allIndices"  # carries India VIX
_MARKET_STATUS_URL = "https://www.nseindia.com/api/marketStatus"  # carries USD/INR future


def _primed_session(timeout: int = 10):
    """A requests.Session with NSE's Akamai cookies primed (homepage + page)."""
    import requests

    s = requests.Session()
    s.headers.update(_BROWSER_HEADERS)
    s.get("https://www.nseindia.com", timeout=timeout)
    s.get("https://www.nseindia.com/option-chain", timeout=timeout)
    return s


def _nse_date(d) -> str:
    return d.strftime("%d-%b-%Y")  # e.g. 30-Jun-2026


def fetch_chain_v3(symbol: str, expiry: str, session=None, timeout: int = 10) -> dict:
    """One expiry via the current v3 endpoint (confirmed live: 200 OK).

    `expiry` is an NSE date string like "30-Jun-2026".
    """
    s = session or _primed_session(timeout)
    r = s.get(
        _V3_URL,
        params={"type": "Indices", "symbol": symbol, "expiry": expiry},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def fetch_chain(symbol: str = "NIFTY", expiries: list | None = None, timeout: int = 10) -> dict:
    """Live pull, assembled into a single {records:{data, underlyingValue}} payload.

    Strategy (NSE bot-gates this; run from a normal/Indian connection):
      1. Prime Akamai cookies.
      2. Try the all-expiries `option-chain-indices` endpoint — one call, done.
      3. Fall back to looping the per-expiry `option-chain-v3` endpoint over the
         in-window expiries (Tuesdays through the next monthly), merging results.

    Pass `expiries` (list of date/"DD-Mon-YYYY") to override discovery — useful if
    NSE's expiry list comes from a call you capture in your browser.
    """
    s = _primed_session(timeout)

    # 2) all-expiries endpoint (matches the parser directly)
    try:
        r = s.get(_INDICES_URL, params={"symbol": symbol}, timeout=timeout)
        if r.ok:
            payload = r.json()
            if (payload.get("records") or {}).get("data"):
                return payload
    except Exception:  # noqa: BLE001 - fall through to v3
        pass

    # 3) per-expiry v3 loop
    exp_strs = _resolve_expiries(expiries)
    merged_data: list[dict] = []
    spot = None
    errors = []
    for e in exp_strs:
        try:
            p = fetch_chain_v3(symbol, e, session=s, timeout=timeout)
            rec = p.get("records") or p.get("filtered") or {}
            merged_data.extend(rec.get("data", []))
            spot = spot or rec.get("underlyingValue")
        except Exception as ex:  # noqa: BLE001
            errors.append(f"{e}: {ex}")
    if not merged_data:
        raise RuntimeError(
            "NSE fetch produced no data. NSE blocks bots/datacenter IPs — run from "
            f"a normal connection. v3 errors: {errors or 'indices endpoint also empty'}. "
            "If the expiry list is wrong, capture the expiry-dropdown call in your "
            "browser and pass expiries=[...]."
        )
    return {"records": {"data": merged_data, "underlyingValue": spot}}


def fetch_india_vix(session=None, timeout: int = 10) -> dict | None:
    """Best-effort live India VIX via the allIndices endpoint.

    Returns {last, change, pct_change} or None on ANY failure: VIX is secondary
    context, so a miss must never break a GEX snapshot (the chain stays the
    primary, fail-loud data). One extra small NSE call per snapshot.
    """
    try:
        s = session or _primed_session(timeout)
        r = s.get(_ALL_INDICES_URL, timeout=timeout)
        r.raise_for_status()
        return parse_india_vix(r.json())
    except Exception:  # noqa: BLE001 - fail soft; VIX is optional context
        return None


def fetch_usdinr(session=None, timeout: int = 10) -> dict | None:
    """Best-effort live USD/INR near-month future via the marketStatus endpoint.

    Returns {last, change, pct_change, as_of} or None on ANY failure: FX is secondary
    context, so a miss must never break a GEX snapshot. One extra small NSE call.
    """
    try:
        s = session or _primed_session(timeout)
        r = s.get(_MARKET_STATUS_URL, timeout=timeout)
        r.raise_for_status()
        return parse_usdinr(r.json())
    except Exception:  # noqa: BLE001 - fail soft; USD/INR is optional context
        return None


def _resolve_expiries(expiries: list | None) -> list[str]:
    """Expiry strings to fetch. If not provided, generate weekly (Tuesday)
    expiries from today through the next monthly, using the NSE calendar."""
    from datetime import date, timedelta

    from data.expiry import next_monthly_expiry, next_weekly_expiry

    if expiries:
        return [e if isinstance(e, str) else _nse_date(e) for e in expiries]

    today = date.today()
    end = next_monthly_expiry(today)
    out, d = [], next_weekly_expiry(today)
    while d <= end:
        out.append(_nse_date(d))
        d = d + timedelta(days=7)
    return out
