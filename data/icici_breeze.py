"""ICICI Direct Breeze API adapter for the Indian GEX engine.

Provides the same surface as data/nse.py so app/data_access.py can swap
providers with a single flag (config.DATA_SOURCE).

Authentication:
  - api_key   : static — from ICICI Direct developer portal.
  - api_secret: static — paired with api_key.
  - session_token: DAILY — must be refreshed each trading day.
    Obtain at: https://api.icicidirect.com/apiuser/login?api_key=<encoded_key>

Supported indices:
  - NIFTY     (NFO exchange)
  - BANKNIFTY (NFO exchange)
  - SENSEX    (BSE/BFO exchange — only available via Breeze, not NSE scraper)

Notes on Breeze option-chain response:
  - IV and greeks are NOT provided by get_option_chain_quotes — they are derived
    here using Black-Scholes (same path as the NSE adapter: iv -> NaN triggers
    the engine's own BS inversion from mid-price).
  - open_interest IS returned per strike per right.
  - The spot price is fetched separately via get_quotes on the nearest futures or
    via the option chain's underlying_price field (when available).
  - Expiry format in responses: "YYYY-MM-DDTHH:MM:SS.000Z" (ISO).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache

log = logging.getLogger(__name__)

# ---------- Configurable knobs ------------------------------------------------

# How many strikes ATM (+/-) to pull for EOD per-strike OI.
# 10 = 21 total API calls (ATM +/- 10).  Make this configurable via .env or here.
import os
ATM_BAND_STRIKES: int = int(os.environ.get("GEX_ATM_BAND_STRIKES", "10"))

class SessionExpiredError(Exception):
    """Raised when the ICICI Breeze session token is invalid or expired."""
    pass

# Settle hour UTC — BSE/NSE close ~15:30 IST = 10:00 UTC
_SETTLE_HOUR_UTC = 10

# Breeze exchange codes per index symbol
_EXCHANGE_MAP = {
    "NIFTY":     "NFO",
    "BANKNIFTY": "NFO",
    "SENSEX":    "BFO",
}

# Breeze stock_code per index symbol (as used in get_option_chain_quotes and get_quotes)
_STOCK_CODE_MAP = {
    "NIFTY":     "NIFTY",
    "BANKNIFTY": "CNXBAN",
    "SENSEX":    "BSESEN",
}

# For spot price: Breeze get_quotes uses a different exchange code
_SPOT_EXCHANGE_MAP = {
    "NIFTY":     "NSE",
    "BANKNIFTY": "NSE",
    "SENSEX":    "BSE",
}


def _get_breeze(api_key: str, api_secret: str, session_token: str):
    """Return an authenticated BreezeConnect instance.

    Raises ImportError if breeze-connect is not installed, and SessionExpiredError
    on authentication failure due to an expired/invalid session token.

    Results are not cached because session_token may change between invocations
    in long-running processes.
    """
    try:
        import os
        import sys

        # breeze-connect SDK runs `import config` at module-level which shadows our
        # project's config.py. We must remove ALL paths that resolve to the project
        # root before importing it (just for the first import; after that it's cached
        # in sys.modules and path patching is irrelevant for subsequent calls).
        if "breeze_connect" not in sys.modules:
            cwd = os.path.realpath(os.getcwd())
            original_path = sys.path[:]
            # Strip '', '.', and any path that resolves to the project root
            def _is_local(p: str) -> bool:
                if not p or p == ".":
                    return True
                try:
                    return os.path.realpath(p) == cwd
                except Exception:
                    return False
            sys.path = [p for p in sys.path if not _is_local(p)]
            local_config = sys.modules.pop("config", None)
            try:
                from breeze_connect import BreezeConnect  # type: ignore  # noqa: F401
            finally:
                # Clean up breeze's config from sys.modules and restore ours
                sys.modules.pop("config", None)
                if local_config is not None:
                    sys.modules["config"] = local_config
                sys.path = original_path
        else:
            from breeze_connect import BreezeConnect  # type: ignore  # noqa: F401 (already cached)

    except ImportError as e:
        raise ImportError(
            "breeze-connect is not installed. Run: pip install breeze-connect"
        ) from e

    breeze = BreezeConnect(api_key=api_key)
    try:
        breeze.generate_session(api_secret=api_secret, session_token=session_token)
    except Exception as exc:
        # generate_session ONLY fails when credentials are wrong or the session
        # token is expired/invalid — always surface as SessionExpiredError so the
        # UI prompt fires regardless of the exact message wording from the API.
        raise SessionExpiredError(f"Breeze authentication failed: {exc}") from exc
    return breeze



def years_to_expiry(expiry: date, now: datetime) -> float:
    settle = datetime.combine(expiry, time(hour=_SETTLE_HOUR_UTC), tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max((settle - now).total_seconds(), 0.0) / (365.25 * 24 * 3600.0)


def _parse_breeze_expiry(s: str) -> date:
    """Parse a Breeze expiry string to a date.

    Breeze uses ISO 8601 format: "2026-07-31T06:00:00.000Z"
    Or sometimes just: "09-Jul-2026"
    """
    for fmt in ("%Y-%m-%dT%H:%M:%S.000Z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised Breeze expiry format: {s!r}")


def _breeze_expiry_str(d: date) -> str:
    """Convert a date to the Breeze ISO expiry string format."""
    return d.strftime("%Y-%m-%dT06:00:00.000Z")


def parse_breeze_option_chain(
    payload: dict,
    symbol: str,
    fetched_at: datetime,
) -> tuple[list[dict], float]:
    """Convert a raw Breeze get_option_chain_quotes response into normalised rows + spot.

    The Breeze response shape (confirmed from SDK docs and GitHub examples):
    {
      "Success": [
        {
          "expiry_date": "2026-07-31T06:00:00.000Z",
          "strike_price": "24000",
          "right": "call",   # or "put"
          "open_interest": "12345",
          "last_rate": "150.25",
          "change_in_open_interest": "500",
          "total_quantity_traded": "45000",
          "best_bid_price": "150.00",
          "best_offer_price": "150.50",
          "underlying_price": "24100.50",   # spot — may be present
          ...
        },
        ...
      ]
    }

    IV is NOT provided by Breeze — left as NaN for the engine to invert from mid.
    OI and volume fields are returned as strings — cast defensively.
    """
    records = payload.get("Success") or payload.get("success") or []
    if not records:
        error = payload.get("Error") or payload.get("error") or "empty response"
        err_str = str(error).lower()
        if "session" in err_str or "token" in err_str or "invalid" in err_str or "auth" in err_str:
            raise SessionExpiredError(f"Session expired: {error}")
        raise ValueError(f"Breeze option chain for {symbol} returned no data: {error}")

    spot: float | None = None
    rows: list[dict] = []

    for r in records:
        # --- expiry ---
        exp_raw = r.get("expiry_date") or r.get("expiryDate")
        if not exp_raw:
            continue
        try:
            exp = _parse_breeze_expiry(str(exp_raw))
        except ValueError:
            log.warning("Breeze: skipping unrecognised expiry %r", exp_raw)
            continue

        # --- strike ---
        strike_raw = r.get("strike_price") or r.get("strikePrice")
        if strike_raw is None:
            continue
        try:
            strike = float(strike_raw)
        except (TypeError, ValueError):
            continue

        # --- type: "call" / "put" ---
        right_raw = (r.get("right") or r.get("option_type") or "").lower()
        if right_raw in ("call", "c", "ce"):
            typ = "C"
        elif right_raw in ("put", "p", "pe"):
            typ = "P"
        else:
            log.warning("Breeze: skipping row with unknown right=%r", right_raw)
            continue

        # --- OI ---
        def _num(key):
            val = r.get(key)
            try:
                return float(val) if val not in (None, "", "0") else 0.0
            except (TypeError, ValueError):
                return 0.0

        oi = _num("open_interest") or _num("openInterest")

        # --- price (mid) ---
        bid = _num("best_bid_price") or _num("bestBidPrice")
        ask = _num("best_offer_price") or _num("bestOfferPrice")
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
        else:
            ltp = _num("last_rate") or _num("lastRate") or _num("ltp")
            mid = ltp if ltp > 0 else float("nan")

        # --- spot (underlying price, same across rows) ---
        if spot is None:
            sp = r.get("spot_price") or r.get("underlying_price") or r.get("underlyingPrice")
            if sp is not None:
                try:
                    spot = float(sp)
                except (TypeError, ValueError):
                    pass

        rows.append({
            "expiry": exp,
            "strike": strike,
            "type": typ,
            "oi": oi,
            "iv": float("nan"),   # Breeze does not provide IV; engine inverts from mid
            "price": mid if mid == mid else None,  # NaN -> None
        })

    if not rows:
        raise ValueError(f"Breeze option chain for {symbol}: no valid rows after parsing")

    return rows, spot


def aggregate_oi_volume_breeze(rows: list[dict]) -> dict:
    """Aggregate OI / volume across parsed Breeze rows, split call vs put.

    Same shape as data.nse.aggregate_oi_volume — used by load_dashboard when
    DATA_SOURCE='icici_breeze'.  doi (change in OI) is included where present.
    """
    agg = {k: 0.0 for k in
           ("call_oi", "put_oi", "call_doi", "put_doi", "call_vol", "put_vol")}
    for r in rows:
        pfx = "call" if r.get("type") == "C" else "put"
        agg[f"{pfx}_oi"] += float(r.get("oi") or 0.0)
        agg[f"{pfx}_doi"] += float(r.get("doi") or 0.0)
        agg[f"{pfx}_vol"] += float(r.get("vol") or 0.0)
    agg["pcr_oi"] = (agg["put_oi"] / agg["call_oi"]) if agg["call_oi"] else float("nan")
    return agg


def per_strike_oi_breeze(
    rows: list[dict],
    spot: float | None = None,
    band_pts: float | None = None,
) -> list[dict]:
    """Per-strike OI / ΔOI / volume — same shape as data.nse.per_strike_oi."""
    by_strike: dict[float, dict] = {}
    for r in rows:
        k = float(r["strike"])
        if spot is not None and band_pts is not None and abs(k - spot) > band_pts:
            continue
        pfx = "call" if r.get("type") == "C" else "put"
        cell = by_strike.setdefault(k, {
            "strike": k, "call_oi": 0.0, "call_doi": 0.0, "call_vol": 0.0,
            "put_oi": 0.0, "put_doi": 0.0, "put_vol": 0.0,
        })
        cell[f"{pfx}_oi"] += float(r.get("oi") or 0.0)
        cell[f"{pfx}_doi"] += float(r.get("doi") or 0.0)
        cell[f"{pfx}_vol"] += float(r.get("vol") or 0.0)
    return [by_strike[k] for k in sorted(by_strike)]


def nearest_expiry_premiums_breeze(rows: list[dict]) -> dict:
    """Per-strike call/put LTP for the nearest expiry — same shape as data.nse equivalent."""
    exps = [r["expiry"] for r in rows if r.get("expiry") is not None]
    if not exps:
        return {}
    nearest = min(exps)
    out: dict[float, dict] = {}
    for r in rows:
        if r.get("expiry") != nearest:
            continue
        k = float(r["strike"])
        cell = out.setdefault(k, {"call_ltp": None, "put_ltp": None})
        cell["call_ltp" if r["type"] == "C" else "put_ltp"] = r.get("price")
    return out


def fetch_option_chain(
    symbol: str,
    *,
    api_key: str,
    api_secret: str,
    session_token: str,
    expiry_date: date | None = None,
    right: str = "",
    strike_price: str = "",
) -> dict:
    """Fetch the full option chain from Breeze for the given index symbol.

    Returns the raw Breeze payload dict (with "Success" list).
    If expiry_date is None the nearest available expiry is used (Breeze returns
    all strikes for that expiry; call this per-expiry and merge for multi-expiry).
    right="" fetches both calls and puts in a single call (recommended).

    Exchange mapping:
      NIFTY / BANKNIFTY -> NFO
      SENSEX            -> BFO
    """
    breeze = _get_breeze(api_key, api_secret, session_token)
    exchange = _EXCHANGE_MAP.get(symbol.upper(), "NFO")
    stock_code = _STOCK_CODE_MAP.get(symbol.upper(), symbol.upper())
    expiry_str = _breeze_expiry_str(expiry_date) if expiry_date else ""
    log.debug("Breeze fetch_option_chain: %s on %s expiry=%r", stock_code, exchange, expiry_str)
    resp = breeze.get_option_chain_quotes(
        stock_code=stock_code,
        exchange_code=exchange,
        product_type="options",
        expiry_date=expiry_str,
        right=right,
        strike_price=strike_price,
    )
    return resp


def fetch_expiry_list(
    symbol: str,
    *,
    api_key: str,
    api_secret: str,
    session_token: str,
) -> list[date]:
    """Discover available expiry dates for a symbol via Breeze.

    Fetches a small slice of the chain (nearest expiry, calls only) and collects
    all expiry_date values.  Falls back to an empty list on any error.
    """
    try:
        # Breeze requires either expiry_date or strike_price to be present.
        # We fetch the spot price to guess a nearby strike.
        spot = fetch_spot_price(symbol, api_key=api_key, api_secret=api_secret, session_token=session_token)
        strike_guess = str(int(round((spot or 24000.0) / 100.0) * 100))

        resp = fetch_option_chain(
            symbol,
            api_key=api_key, api_secret=api_secret, session_token=session_token,
            right="call",
            strike_price=strike_guess,
        )
        records = resp.get("Success") or []
        exps = set()
        for r in records:
            raw = r.get("expiry_date") or r.get("expiryDate")
            if raw:
                try:
                    exps.add(_parse_breeze_expiry(str(raw)))
                except ValueError:
                    pass
        return sorted(exps)
    except SessionExpiredError:
        raise  # Propagate so the UI can prompt for a new token
    except Exception as exc:  # noqa: BLE001
        log.warning("Breeze: fetch_expiry_list failed for %s: %s", symbol, exc)
        return []


def fetch_spot_price(
    symbol: str,
    *,
    api_key: str,
    api_secret: str,
    session_token: str,
) -> float | None:
    """Best-effort spot price for the index.

    For NSE indices, uses get_quotes with exchange_code="NSE".
    For BSE (SENSEX), uses exchange_code="BSE".
    Returns None on any failure — spot is also embedded in option-chain rows, so
    this is a secondary fallback.
    """
    breeze = _get_breeze(api_key, api_secret, session_token)
    exchange = _SPOT_EXCHANGE_MAP.get(symbol.upper(), "NSE")
    stock_code = _STOCK_CODE_MAP.get(symbol.upper(), symbol.upper())
    try:
        resp = breeze.get_quotes(
            stock_code=stock_code,
            exchange_code=exchange,
            expiry_date="",
            product_type="cash",
            right="",
            strike_price="",
        )
        records = resp.get("Success") or resp.get("success") or []
        for r in records:
            if r.get("exchange_code") == exchange:
                try:
                    return float(r.get("ltp") or 0.0)
                except (ValueError, TypeError):
                    pass
    except Exception as exc:  # noqa: BLE001
        log.debug("Breeze get_quotes (spot) failed for %s: %s", symbol, exc)
    return None


def fetch_chain_breeze(
    symbol: str,
    *,
    api_key: str,
    api_secret: str,
    session_token: str,
    expiries: list[date] | None = None,
    has_weekly: bool = True,
    window: str = "all",
    fetched_at: datetime | None = None,
    exchange: str = "NSE",
) -> tuple[list[dict], float]:
    """Fetch the full option chain for symbol via Breeze, across all in-window expiries.

    Merges per-expiry calls (Breeze returns one expiry per call) into a combined
    list of normalised rows and the spot price. Mirrors the interface of
    data.nse.fetch_chain + parse_nse_option_chain combined.

    If expiries is None, the list is discovered automatically from Breeze.
    Returns (rows, spot) where rows is the combined normalised list.
    """
    # Discover expiry list if not provided
    if expiries is None:
        expiries = fetch_expiry_list(
            symbol, api_key=api_key, api_secret=api_secret, session_token=session_token
        )
    if not expiries:
        # If expiries list is empty after a successful (non-expired) fetch,
        # raise RuntimeError. SessionExpiredError is re-raised directly above.
        raise RuntimeError(
            f"Breeze: no expiry dates found for {symbol}. Check your credentials and "
            "confirm that the index is supported for your Breeze account."
        )

    # Pre-filter expiries to minimize API calls (Breeze requires 2 calls per expiry)
    if window != "all":
        from data.expiry import next_monthly_expiry, next_weekly_expiry
        ref = (fetched_at or datetime.now(timezone.utc)).date()
        filtered = []
        for exp in expiries:
            if window == "0dte":
                if exp == ref:
                    filtered.append(exp)
            elif window == "weekly":
                end = next_weekly_expiry(ref, exchange) if has_weekly else next_monthly_expiry(ref, exchange)
                if ref <= exp <= end:
                    filtered.append(exp)
            elif window in ("monthly", "0dte_to_next_monthly"):
                if ref <= exp <= next_monthly_expiry(ref, exchange):
                    filtered.append(exp)
        expiries = filtered

    all_rows: list[dict] = []
    spot: float | None = None
    errors: list[str] = []

    import concurrent.futures

    def _fetch_leg(e, r):
        return fetch_option_chain(
            symbol,
            api_key=api_key, api_secret=api_secret, session_token=session_token,
            expiry_date=e,
            right=r,
        )

    # Use ThreadPoolExecutor to fetch all calls and puts concurrently.
    # max_workers=10 is a safe balance for the Breeze API.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for exp in expiries:
            futures[(exp, "call")] = executor.submit(_fetch_leg, exp, "call")
            futures[(exp, "put")] = executor.submit(_fetch_leg, exp, "put")

        for exp in expiries:
            try:
                payload_call = futures[(exp, "call")].result()
                payload_put = futures[(exp, "put")].result()
                
                records = (payload_call.get("Success") or payload_call.get("success") or []) + \
                          (payload_put.get("Success") or payload_put.get("success") or [])
                
                if not records:
                    raise ValueError("Breeze option chain returned no data for calls or puts")
                    
                payload = {"Success": records}
                exp_rows, exp_spot = parse_breeze_option_chain(payload, symbol, datetime.now(timezone.utc))
                all_rows.extend(exp_rows)
                if spot is None:
                    spot = exp_spot
            except Exception as exc:  # noqa: BLE001 - log per-expiry errors, continue
                errors.append(f"{exp}: {exc}")
                log.warning("Breeze: chain fetch failed for %s expiry %s: %s", symbol, exp, exc)

    if not all_rows:
        raise RuntimeError(
            f"Breeze: no option chain data for {symbol}. "
            f"Expiry errors: {errors or 'none'}. "
            "Verify Breeze credentials and that SENSEX/BFO is enabled for your account."
        )

    if spot is None:
        # Last resort: separate spot fetch
        spot = fetch_spot_price(
            symbol, api_key=api_key, api_secret=api_secret, session_token=session_token
        )
    if spot is None:
        raise ValueError(f"Breeze: could not determine spot price for {symbol}")

    return all_rows, spot


# =============================================================================
# Historical data fetchers (EOD)
# =============================================================================

def fetch_futures_history_breeze(
    symbol: str,
    from_date: date,
    to_date: date,
    *,
    api_key: str,
    api_secret: str,
    session_token: str,
    exchange: str = "NSE",
) -> list[dict]:
    """Fetch EOD futures OHLCV + OI for `symbol` from Breeze over the given date range.

    Returns a list of dicts with keys:
      date_ist, symbol, expiry, open, high, low, close, prev_close, dprice,
      oi, doi, volume.

    Uses the holiday-aware near_month_futures_expiry() to resolve the correct
    contract. Falls back gracefully to an empty list on errors.
    """
    from data.holidays import near_month_futures_expiry
    from data.storage import save_futures_eod

    breeze = _get_breeze(api_key, api_secret, session_token)
    stock_code = _STOCK_CODE_MAP.get(symbol.upper(), symbol.upper())
    exch_code = _EXCHANGE_MAP.get(symbol.upper(), "NFO")

    # Resolve near-month futures expiry (holiday-aware)
    expiry = near_month_futures_expiry(ref=from_date, exchange=exchange)
    expiry_str = _breeze_expiry_str(expiry)

    from_str = from_date.strftime("%Y-%m-%dT00:00:00.000Z")
    to_str   = to_date.strftime("%Y-%m-%dT23:59:59.000Z")

    log.info("Fetching %s futures history %s to %s (expiry %s)", symbol, from_date, to_date, expiry)
    try:
        resp = breeze.get_historical_data_v2(
            interval="1day",
            from_date=from_str,
            to_date=to_str,
            stock_code=stock_code,
            exchange_code=exch_code,
            product_type="futures",
            expiry_date=expiry_str,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Breeze futures history fetch failed for %s: %s", symbol, exc)
        return []

    records = resp.get("Success") or resp.get("success") or []
    rows = []
    prev_close: float | None = None
    for r in records:
        def _n(k, default=None):
            v = r.get(k)
            try:
                return float(v) if v not in (None, "") else default
            except (TypeError, ValueError):
                return default

        close = _n("close")
        if close is None:
            prev_close = _n("close") if prev_close is None else prev_close
            continue

        dprice = (close - prev_close) if (prev_close is not None and close is not None) else None

        # Parse the date from datetime string e.g. "2026-07-07T00:00:00.000Z"
        raw_dt = r.get("datetime") or r.get("date") or ""
        try:
            d = datetime.strptime(str(raw_dt).split("T")[0], "%Y-%m-%d").date()
            date_ist = str(d)
        except ValueError:
            date_ist = str(from_date)

        row = {
            "date_ist": date_ist,
            "symbol": symbol.upper(),
            "expiry": str(expiry),
            "open": _n("open"),
            "high": _n("high"),
            "low": _n("low"),
            "close": close,
            "prev_close": prev_close,
            "dprice": dprice,
            "oi": _n("open_interest") or _n("volume"),  # Breeze field may vary
            "doi": None,
            "volume": _n("volume"),
        }
        rows.append(row)
        prev_close = close

        # Persist to local store
        try:
            from datetime import date as _date
            save_futures_eod(symbol, d, [row])
        except Exception:  # noqa: BLE001
            pass

    log.info("Fetched %d futures EOD rows for %s", len(rows), symbol)
    return rows


def fetch_options_eod_breeze(
    symbol: str,
    d: date,
    spot: float,
    expiry: date,
    *,
    api_key: str,
    api_secret: str,
    session_token: str,
    atm_band_strikes: int | None = None,
    strike_step: float | None = None,
) -> list[dict]:
    """Fetch EOD per-strike OI for the given `symbol`, date, and expiry.

    Queries ATM +/- `atm_band_strikes` strikes (default: ATM_BAND_STRIKES from config).
    Each strike requires 2 Breeze API calls (call + put). Results are persisted to
    data/journal/eod/<SYMBOL>/<DATE>_oi_strikes.csv.

    Returns a list of per-strike dicts with keys matching OI_STRIKE_EOD_FIELDS.
    """
    from data.storage import save_oi_strikes_eod

    band = atm_band_strikes if atm_band_strikes is not None else ATM_BAND_STRIKES
    # Infer strike step from symbol if not provided
    if strike_step is None:
        strike_step = {"NIFTY": 50.0, "BANKNIFTY": 100.0, "SENSEX": 100.0}.get(symbol.upper(), 50.0)

    atm = round(spot / strike_step) * strike_step
    strikes = [atm + strike_step * i for i in range(-band, band + 1)]

    breeze = _get_breeze(api_key, api_secret, session_token)
    stock_code = _STOCK_CODE_MAP.get(symbol.upper(), symbol.upper())
    exch_code = _EXCHANGE_MAP.get(symbol.upper(), "NFO")
    expiry_str = _breeze_expiry_str(expiry)

    from_str = d.strftime("%Y-%m-%dT00:00:00.000Z")
    to_str   = d.strftime("%Y-%m-%dT23:59:59.000Z")

    log.info(
        "Fetching %s EOD options OI for %s, %d strikes around ATM %.0f",
        symbol, d, len(strikes), atm
    )

    rows: list[dict] = []
    for strike in strikes:
        for right in ("call", "put"):
            try:
                resp = breeze.get_historical_data_v2(
                    interval="1day",
                    from_date=from_str,
                    to_date=to_str,
                    stock_code=stock_code,
                    exchange_code=exch_code,
                    product_type="options",
                    expiry_date=expiry_str,
                    right=right,
                    strike_price=str(int(strike)),
                )
                records = resp.get("Success") or resp.get("success") or []
                for r in records:
                    def _n(k):
                        v = r.get(k)
                        try:
                            return float(v) if v not in (None, "") else None
                        except (TypeError, ValueError):
                            return None
                    rows.append({
                        "date_ist": str(d),
                        "symbol": symbol.upper(),
                        "expiry": str(expiry),
                        "strike": strike,
                        "type": "C" if right == "call" else "P",
                        "oi": _n("open_interest"),
                        "doi": None,  # prev-close DOI requires prior day's row
                        "volume": _n("volume"),
                        "close_price": _n("close"),
                    })
            except Exception as exc:  # noqa: BLE001
                log.debug("EOD options fetch: %s %s %.0f %s — %s", symbol, d, strike, right, exc)

    # Enrich DOI: diff vs yesterday's OI from local store
    try:
        from data.storage import load_oi_strikes_eod
        from datetime import timedelta
        prev_d = d - timedelta(days=1)
        # Roll back to find the last available trading day
        for _ in range(7):
            prev_rows = load_oi_strikes_eod(symbol, prev_d)
            if prev_rows:
                break
            prev_d -= timedelta(days=1)
        else:
            prev_rows = None

        if prev_rows:
            prev_oi = {(float(r["strike"]), r["type"]): float(r["oi"] or 0) for r in prev_rows}
            for row in rows:
                key = (float(row["strike"]), row["type"])
                prev = prev_oi.get(key)
                if prev is not None and row["oi"] is not None:
                    row["doi"] = float(row["oi"]) - prev
    except Exception:  # noqa: BLE001
        pass

    if rows:
        try:
            save_oi_strikes_eod(symbol, d, rows)
        except Exception:  # noqa: BLE001
            pass

    log.info("Fetched %d EOD option-strike rows for %s %s", len(rows), symbol, d)
    return rows


def fetch_india_vix_breeze(
    *,
    api_key: str,
    api_secret: str,
    session_token: str,
) -> dict | None:
    """Fetch India VIX live quote via Breeze. Returns {last, change, pct_change} or None."""
    breeze = _get_breeze(api_key, api_secret, session_token)
    try:
        resp = breeze.get_quotes(
            stock_code="INDIAVIX",
            exchange_code="NSE",
            expiry_date="",
            product_type="cash",
            right="",
            strike_price="",
        )
        records = resp.get("Success") or resp.get("success") or []
        for r in records:
            try:
                last = float(r.get("ltp") or 0)
                change = float(r.get("change") or 0)
                prev = last - change
                pct = (change / prev * 100) if prev else 0.0
                return {"last": last, "change": change, "pct_change": round(pct, 2)}
            except (TypeError, ValueError):
                continue
    except Exception as exc:  # noqa: BLE001
        log.debug("Breeze India VIX fetch failed: %s", exc)
    return None


def fetch_usdinr_breeze(
    *,
    api_key: str,
    api_secret: str,
    session_token: str,
) -> dict | None:
    """Fetch USD/INR near-month futures via Breeze. Returns {last, change, pct_change, as_of} or None."""
    from data.holidays import near_month_futures_expiry
    breeze = _get_breeze(api_key, api_secret, session_token)
    expiry = near_month_futures_expiry()
    expiry_str = _breeze_expiry_str(expiry)
    try:
        resp = breeze.get_quotes(
            stock_code="USDINR",
            exchange_code="CDS",
            expiry_date=expiry_str,
            product_type="futures",
            right="",
            strike_price="",
        )
        records = resp.get("Success") or resp.get("success") or []
        for r in records:
            try:
                last = float(r.get("ltp") or 0)
                change = float(r.get("change") or 0)
                prev = last - change
                pct = (change / prev * 100) if prev else 0.0
                return {
                    "last": last,
                    "change": change,
                    "pct_change": round(pct, 2),
                    "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            except (TypeError, ValueError):
                continue
    except Exception as exc:  # noqa: BLE001
        log.debug("Breeze USDINR fetch failed: %s", exc)
    return None
