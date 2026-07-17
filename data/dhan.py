"""Dhan broker API adapter for the Indian GEX engine.

Provides the same public surface as data/icici_breeze.py and data/nse.py so
app/data_access.py can swap providers with a single flag (config.DATA_SOURCE).

Authentication:
  - client_id   : static — from Dhan developer portal.
  - access_token: static — no daily refresh required (unlike ICICI Breeze).
    Generate at: https://dhanhq.co/docs/latest/authentication/

Supported indices:
  - NIFTY     (NSE F&O)
  - BANKNIFTY (NSE F&O)

Notes on Dhan option-chain response:
  - IV and greeks are NOT provided by /optionchain — they are derived here using
    Black-Scholes (same path as the other adapters: iv -> NaN triggers the
    engine's own BS inversion from mid-price).
  - open_interest IS returned per strike per right.
  - The spot price is available in the option-chain response.
  - Expiry format in responses: "YYYY-MM-DD".
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

# ---------- Configurable knobs ------------------------------------------------

import os
ATM_BAND_STRIKES: int = int(os.environ.get("GEX_ATM_BAND_STRIKES", "20"))

# Dhan API base URL
_DHAN_BASE_URL = "https://api.dhan.co/v2"

# Dhan security/underlying IDs for index option chains
_DHAN_UNDERLYING_ID = {
    "NIFTY":     "13",
    "BANKNIFTY": "25",
}

# Dhan exchange segment codes for F&O
_DHAN_EXCHANGE_SEGMENT = {
    "NIFTY":     "NSE_FNO",
    "BANKNIFTY": "NSE_FNO",
}

# NSE close ~15:30 IST = 10:00 UTC
_SETTLE_HOUR_UTC = 10


class DhanAPIError(Exception):
    """Raised on a non-200 or error-body response from the Dhan API."""
    pass


class DhanCredentialError(Exception):
    """Raised when Dhan credentials are missing or invalid."""
    pass


# ---------- Low-level HTTP helpers -------------------------------------------

def _dhan_headers(access_token: str) -> dict:
    return {
        "access-token": access_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _post(url: str, access_token: str, payload: dict) -> Any:
    """POST request against Dhan API. Raises DhanAPIError on failure."""
    try:
        import requests
    except ImportError:
        raise ImportError(
            "requests is required for the Dhan adapter. "
            "Install it with: pip install requests"
        )
    resp = requests.post(
        url, headers=_dhan_headers(access_token), json=payload, timeout=20
    )
    if resp.status_code == 401:
        raise DhanCredentialError(
            "Dhan API returned 401 Unauthorized. "
            "Check DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in your .env."
        )
    if not resp.ok:
        raise DhanAPIError(
            f"Dhan API {url} returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


# ---------- Expiry helpers ---------------------------------------------------

def _parse_dhan_expiry(expiry_str: str) -> date:
    """Parse a Dhan expiry string (YYYY-MM-DD) into a date object."""
    try:
        return date.fromisoformat(expiry_str[:10])
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"Unrecognised Dhan expiry format: {expiry_str!r}"
        ) from exc


# ---------- Option chain fetch -----------------------------------------------

def _fetch_option_chain_raw(
    symbol: str,
    expiry: date,
    access_token: str,
    client_id: str,
) -> dict:
    """Fetch raw option chain data from Dhan for one expiry."""
    underlying_id = _DHAN_UNDERLYING_ID.get(symbol)
    if underlying_id is None:
        raise ValueError(
            f"Dhan adapter does not support symbol {symbol!r}. "
            f"Supported: {sorted(_DHAN_UNDERLYING_ID)}"
        )

    url = f"{_DHAN_BASE_URL}/optionchain"
    payload = {
        "UnderlyingScrip": int(underlying_id),
        "UnderlyingSeg": _DHAN_EXCHANGE_SEGMENT[symbol],
        "Expiry": expiry.strftime("%Y-%m-%d"),
    }
    return _post(url, access_token, payload)


def _fetch_expiry_list(
    symbol: str,
    access_token: str,
    client_id: str,
) -> list:
    """Fetch available expiry dates for a symbol from Dhan."""
    underlying_id = _DHAN_UNDERLYING_ID.get(symbol)
    if underlying_id is None:
        raise ValueError(f"Symbol {symbol!r} not supported by Dhan adapter.")

    url = f"{_DHAN_BASE_URL}/optionchain/expirylist"
    payload = {
        "UnderlyingScrip": int(underlying_id),
        "UnderlyingSeg": _DHAN_EXCHANGE_SEGMENT[symbol],
    }
    data = _post(url, access_token, payload)
    # Response: {"data": {"expiryList": ["2026-07-10", "2026-07-31", ...]}}
    expiry_list = (data.get("data") or {}).get("expiryList") or []
    return sorted([_parse_dhan_expiry(e) for e in expiry_list])


def parse_dhan_option_chain(
    raw: dict,
    symbol: str,
    expiry: date,
    fetched_at: datetime,
) -> tuple:
    """Parse Dhan option chain response into normalised rows.

    Returns (rows, spot) where each row has the same keys as icici_breeze rows:
    expiry, strike, type, oi, doi, vol, price, iv, T.
    spot is None if the underlying price is not available.
    """
    data = raw.get("data") or {}

    # Extract spot
    spot = None
    raw_spot = data.get("underlying_scrip_price") or data.get("last_price")
    if raw_spot is not None:
        try:
            spot = float(raw_spot)
        except (ValueError, TypeError):
            spot = None

    oc = data.get("oc") or {}
    if not oc:
        raise ValueError(
            f"Dhan option chain returned no data for {symbol} expiry {expiry}. "
            "Check your credentials and that the symbol/expiry combination is valid."
        )

    rows = []
    settle = datetime.combine(
        expiry,
        datetime.min.time().replace(hour=_SETTLE_HOUR_UTC),
        tzinfo=timezone.utc,
    )
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    T = max((settle - fetched_at).total_seconds(), 0.0) / (365.25 * 24 * 3600)

    for strike_str, legs in oc.items():
        try:
            strike = float(strike_str)
        except (ValueError, TypeError):
            log.warning("Dhan: skipping non-numeric strike %r", strike_str)
            continue

        for right_key, contract_type in (("ce", "C"), ("pe", "P")):
            leg = legs.get(right_key)
            if leg is None:
                continue

            oi  = float(leg.get("open_interest") or 0)
            doi = float(leg.get("change_in_oi") or 0)
            vol = float(leg.get("volume") or 0)

            bid = leg.get("bid_price")
            ask = leg.get("ask_price")
            ltp = leg.get("last_price")
            if bid is not None and ask is not None:
                try:
                    price = (float(bid) + float(ask)) / 2.0
                except (ValueError, TypeError):
                    price = float(ltp) if ltp is not None else float("nan")
            elif ltp is not None:
                try:
                    price = float(ltp)
                except (ValueError, TypeError):
                    price = float("nan")
            else:
                price = float("nan")

            rows.append({
                "expiry": expiry,
                "strike": strike,
                "type":   contract_type,
                "oi":     oi,
                "doi":    doi,
                "vol":    vol,
                "price":  price,
                "iv":     float("nan"),  # Dhan doesn't provide IV; engine derives it
                "T":      T,
            })

    return rows, spot


# ---------- Public fetch surface ---------------------------------------------

def fetch_chain_dhan(
    symbol: str,
    *,
    client_id: str,
    access_token: str,
    has_weekly: bool = True,
    window: str = "0dte",
    fetched_at=None,
    exchange: str = "NSE",
    expiries=None,
):
    """Fetch the full option chain for ``symbol`` from the Dhan API.

    Returns (rows, spot) where rows is a list of normalised contract dicts and
    spot is the underlying index level. Mirrors fetch_chain_breeze() surface.
    """
    fetched_at = fetched_at or datetime.now(timezone.utc)

    if expiries is None:
        expiries = _fetch_expiry_list(symbol, access_token, client_id)
        if not expiries:
            raise RuntimeError(
                f"Dhan: no expiries returned for {symbol}. "
                "Check credentials and that the market is not on a holiday."
            )
        today = (fetched_at + timedelta(hours=5, minutes=30)).date()
        future = [e for e in expiries if e >= today]
        if window == "0dte":
            expiries = future[:1]
        elif window in ("weekly", "monthly"):
            expiries = future[:2]
        else:
            expiries = future  # all / 0dte_to_next_monthly

    all_rows = []
    spot = None

    for exp in expiries:
        try:
            raw = _fetch_option_chain_raw(symbol, exp, access_token, client_id)
            rows, exp_spot = parse_dhan_option_chain(raw, symbol, exp, fetched_at)
            all_rows.extend(rows)
            if spot is None and exp_spot is not None:
                spot = exp_spot
        except DhanAPIError as exc:
            log.warning("Dhan: failed to fetch %s expiry %s: %s", symbol, exp, exc)
            continue

    if not all_rows:
        raise RuntimeError(
            f"Dhan: no option chain data for {symbol} across expiries {expiries}. "
            "Check credentials, symbol support, and whether the market is live."
        )
    if spot is None:
        raise RuntimeError(f"Dhan: could not determine spot price for {symbol}.")

    log.info(
        "Dhan: fetched %d contracts for %s across %d expiries; spot=%.2f",
        len(all_rows), symbol, len(expiries), spot,
    )
    return all_rows, spot


# ---------- OI aggregation helpers ------------------------------------------

def aggregate_oi_volume_dhan(rows: list) -> dict:
    """Aggregate total call/put OI, delta-OI, and volume. Same schema as breeze."""
    call_oi = put_oi = call_doi = put_doi = call_vol = put_vol = 0.0
    for r in rows:
        if r["type"] == "C":
            call_oi  += r.get("oi",  0) or 0
            call_doi += r.get("doi", 0) or 0
            call_vol += r.get("vol", 0) or 0
        else:
            put_oi  += r.get("oi",  0) or 0
            put_doi += r.get("doi", 0) or 0
            put_vol += r.get("vol", 0) or 0
    pcr = (put_oi / call_oi) if call_oi > 0 else float("nan")
    return {
        "call_oi":  call_oi,
        "put_oi":   put_oi,
        "pcr_oi":   pcr,
        "call_doi": call_doi,
        "put_doi":  put_doi,
        "call_vol": call_vol,
        "put_vol":  put_vol,
    }


def per_strike_oi_dhan(
    rows: list,
    spot=None,
    band_pts=None,
) -> list:
    """Per-strike OI/delta-OI/volume, filtered to ±band_pts of spot if given.

    Same output schema as per_strike_oi_breeze().
    """
    strikes = {}
    for r in rows:
        k = float(r["strike"])
        if spot is not None and band_pts is not None:
            if abs(k - spot) > band_pts:
                continue
        cell = strikes.setdefault(k, {
            "strike":   k,
            "call_oi":  0.0, "call_doi": 0.0, "call_vol": 0.0,
            "put_oi":   0.0, "put_doi":  0.0, "put_vol":  0.0,
        })
        if r["type"] == "C":
            cell["call_oi"]  += r.get("oi",  0) or 0
            cell["call_doi"] += r.get("doi", 0) or 0
            cell["call_vol"] += r.get("vol", 0) or 0
        else:
            cell["put_oi"]  += r.get("oi",  0) or 0
            cell["put_doi"] += r.get("doi", 0) or 0
            cell["put_vol"] += r.get("vol", 0) or 0
    return sorted(strikes.values(), key=lambda x: x["strike"])


def nearest_expiry_premiums_dhan(rows: list) -> dict:
    """Extract call/put mid-prices for the nearest expiry only.

    Same output schema as nearest_expiry_premiums_breeze().
    Returns {strike: {"call_ltp": float|None, "put_ltp": float|None}}.
    """
    if not rows:
        return {}
    nearest = min(r["expiry"] for r in rows)
    near_rows = [r for r in rows if r["expiry"] == nearest]
    result = {}
    for r in near_rows:
        k = float(r["strike"])
        cell = result.setdefault(k, {"call_ltp": None, "put_ltp": None})
        price = r.get("price")
        if price is not None and not (isinstance(price, float) and math.isnan(price)):
            if r["type"] == "C":
                cell["call_ltp"] = price
            else:
                cell["put_ltp"] = price
    return result


# ---------- Market context (VIX, USDINR) ------------------------------------

def fetch_india_vix_dhan(client_id: str, access_token: str) -> dict | None:
    """Fetch India VIX via Dhan quote endpoint. Returns None on failure."""
    try:
        url = f"{_DHAN_BASE_URL}/marketfeed/quote"
        payload = {"NSE_INDEX": [26000]}  # India VIX security ID on NSE
        data = _post(url, access_token, payload)
        vix_data = (data.get("data") or {}).get("NSE_INDEX", {}).get("26000", {})
        last = vix_data.get("last_price") or vix_data.get("LTP")
        chg  = vix_data.get("change")
        pct  = vix_data.get("change_percentage")
        if last is None:
            return None
        return {
            "last":       float(last),
            "change":     float(chg) if chg is not None else None,
            "pct_change": float(pct) if pct is not None else None,
        }
    except Exception as exc:  # noqa: BLE001
        log.debug("Dhan: India VIX fetch failed: %s", exc)
        return None


def fetch_usdinr_dhan(client_id: str, access_token: str) -> dict | None:
    """Fetch USD/INR. Returns None; data_access falls back to NSE scrape."""
    return None
