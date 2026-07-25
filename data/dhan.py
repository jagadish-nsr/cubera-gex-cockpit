"""DhanHQ v2 API client — historical (expired) options data at 1-min, incl. OI + IV.

The one source that fills our intraday-history gap: the `charts/rollingoption` endpoint
returns continuous expired-option candles (OHLC + volume + **OI** + **IV** + spot + strike)
at 1/5/15/25/60-min, up to 5 years back, keyed by a RELATIVE strike (ATM ± N) and expiry
(WEEK/MONTH). NIFTY index securityId = 13.  Docs: https://dhanhq.co/docs/v2/expired-options-data

CREDENTIALS: never hard-coded / logged. The access token is read from the env var
``DHAN_ACCESS_TOKEN`` or a gitignored file ``data/.dhan_token`` (and optional client id from
``DHAN_CLIENT_ID`` / ``data/.dhan_client_id``). Create the token file once with the token
generated from the Dhan web platform. Decision-support/research only.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DHAN_BASE = "https://api.dhan.co/v2"
NIFTY_SECURITY_ID = 13           # NIFTY index underlying (from the Dhan docs example)
_REQUIRED = ("open", "high", "low", "close", "volume", "oi", "iv", "spot", "strike")

# Index-underlying securityIds (from the Dhan scrip master) + the expiry interval that actually
# has options: NIFTY still lists weeklies; BANKNIFTY is monthly-only since Nov-2024.
DHAN_UNDERLYING = {"NIFTY": 13, "BANKNIFTY": 25}
DHAN_OPT_FLAG = {"NIFTY": "WEEK", "BANKNIFTY": "MONTH"}
_SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"

# --- Live option-chain adapter constants ------------------------------------
# Base URL for the live option-chain endpoints (v2 REST)
_DHAN_BASE_URL = "https://api.dhan.co/v2"

# Security IDs for index underlyings (IDX_I segment)
# NIFTY = 13, BANKNIFTY = 25 (same as DHAN_UNDERLYING above)
_DHAN_UNDERLYING_ID: dict[str, int] = {
    "NIFTY":     13,
    "BANKNIFTY": 25,
}

# Exchange segment strings required by the Dhan option-chain endpoint
_DHAN_EXCHANGE_SEGMENT: dict[str, str] = {
    "NIFTY":     "IDX_I",   # NSE index segment
    "BANKNIFTY": "IDX_I",   # NSE index segment
}


def _secret(env: str, fname: str) -> str | None:
    v = os.environ.get(env)
    if v:
        return v.strip()
    for p in (ROOT / "data" / fname, Path.home() / fname):
        try:
            if p.exists():
                return p.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return None


def _headers() -> dict:
    token = _secret("DHAN_ACCESS_TOKEN", ".dhan_token")
    if not token:
        raise RuntimeError("No Dhan access token — set env DHAN_ACCESS_TOKEN or create "
                           "data/.dhan_token (gitignored) with the token from the Dhan platform.")
    h = {"access-token": token, "Accept": "application/json", "Content-Type": "application/json"}
    cid = _secret("DHAN_CLIENT_ID", ".dhan_client_id")
    if cid:
        h["client-id"] = cid
    return h


def fetch_rolling_option(from_date: str, to_date: str, strike: str = "ATM",
                         opt_type: str = "CALL", expiry_flag: str = "WEEK", expiry_code: int = 1,
                         interval: str = "1", security_id: int = NIFTY_SECURITY_ID,
                         required=_REQUIRED, timeout: int = 40) -> dict | None:
    """POST charts/rollingoption for one relative strike/type over [from_date, to_date)
    (YYYY-MM-DD; toDate non-inclusive). ``strike`` = 'ATM' or 'ATM+1'..'ATM+10'/'ATM-1'..
    'ATM-10'. ``expiry_code`` is 1-based (1 = nearest expiry of the flag, 2 = next …; 0 is
    rejected by the API as "required"). Returns the raw JSON ({'data': {'ce': {...arrays...},
    'pe': {...}}}) or None on a soft failure. Raises only on a missing token."""
    import requests

    body = {
        "exchangeSegment": "NSE_FNO", "interval": str(interval),
        "securityId": security_id, "instrument": "OPTIDX",
        "expiryFlag": expiry_flag, "expiryCode": int(expiry_code),
        "strike": strike, "drvOptionType": opt_type,
        "requiredData": list(required), "fromDate": from_date, "toDate": to_date,
    }
    try:
        r = requests.post(f"{DHAN_BASE}/charts/rollingoption", json=body,
                          headers=_headers(), timeout=timeout)
        if r.status_code != 200:
            return {"_error": r.status_code, "_body": r.text[:400]}
        return r.json()
    except RuntimeError:
        raise
    except Exception as e:  # noqa: BLE001 - soft-fail on network/parse
        return {"_error": "exception", "_body": str(e)[:400]}


INTRADAY_DIR = ROOT / "data" / "journal" / "dhan_intraday"
_PAGE_DAYS = 42                  # rolling-WEEK stitch breaks past ~4 weekly expiries per call
_OFFSETS = tuple(range(-10, 11))  # ATM-10 … ATM+10 (index options near expiry)


def _rel_strike(off: int) -> str:
    return "ATM" if off == 0 else (f"ATM+{off}" if off > 0 else f"ATM{off}")


def fetch_option_matrix(from_date: str, to_date: str, offsets=_OFFSETS,
                        flag: str = "WEEK", code: int = 1, interval: str = "1",
                        security_id: int = NIFTY_SECURITY_ID, sleep: float = 0.15,
                        retries: int = 2):
    """One page (≤~45d): fetch every ATM-offset CALL & PUT and return a tidy LONG DataFrame —
    one row per (candle, strike-offset, side) with the actual absolute ``strike``/``spot`` each
    candle carries. None if nothing came back. Polite ``sleep`` between calls; a soft failure
    is retried ``retries`` times before that series is skipped (logged to the returned meta)."""
    import time
    import pandas as pd

    frames, misses = [], []
    for off in offsets:
        for side, opt in (("CE", "CALL"), ("PE", "PUT")):
            df = None
            for attempt in range(retries + 1):
                resp = fetch_rolling_option(from_date, to_date, _rel_strike(off), opt,
                                            flag, code, interval, security_id)
                if resp and "_error" in resp:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                df = to_frame(resp, side.lower())
                break
            if df is None or df.empty:
                misses.append((off, side))
            else:
                df["off"] = off
                df["side"] = side
                frames.append(df)
            time.sleep(sleep)
    if not frames:
        return None, misses
    return pd.concat(frames, ignore_index=True), misses


_STORE_COLS = ("time_ist", "off", "side", "strike", "spot",
               "open", "high", "low", "close", "volume", "oi", "iv")


def _day_path(store: str, day: str) -> Path:
    return INTRADAY_DIR / store / f"{day}.parquet"


def save_intraday_day(store: str, day: str, df) -> int:
    """Write one trading day's long-format option matrix to parquet under the ``store`` key
    (e.g. 'NIFTY' for weeklies, 'NIFTY_M' for monthlies). Returns row count."""
    p = _day_path(store, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = [c for c in _STORE_COLS if c in df.columns]
    out = df[keep].sort_values(["time_ist", "off", "side"]).reset_index(drop=True)
    out.to_parquet(p, index=False)
    return len(out)


def load_intraday_day(store: str, day: str):
    """Read one day's option matrix (long format) for a ``store`` key, or None if absent."""
    import pandas as pd
    p = _day_path(store, day)
    return pd.read_parquet(p) if p.exists() else None


def available_intraday_days(store: str = "NIFTY") -> list[str]:
    d = INTRADAY_DIR / store
    if not d.exists():
        return []
    return sorted(f.stem for f in d.glob("*.parquet"))


def backfill_intraday_options(start: str, end: str, symbol: str = "NIFTY",
                              offsets=_OFFSETS, flag: str | None = None, code: int = 1,
                              interval: str = "1", security_id: int | None = None,
                              store: str | None = None, log=print) -> dict:
    """Page over [start, end] in ~42-day WEEK-safe windows (newest→oldest), fetch the full
    ATM±N CE/PE matrix, and write one parquet per trading day. ``flag``/``security_id`` default
    from the symbol (WEEK+13 for NIFTY, MONTH+25 for BANKNIFTY); ``store`` (the on-disk key)
    defaults to the symbol — pass e.g. 'NIFTY_M' to keep a non-default expiry book separate.
    Resumable: a page whose newest trading day is already on disk is skipped without an API
    call. Returns a small summary."""
    from datetime import date, timedelta

    flag = flag or DHAN_OPT_FLAG.get(symbol, "WEEK")
    security_id = security_id or DHAN_UNDERLYING.get(symbol, NIFTY_SECURITY_ID)
    store = store or symbol
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    have = set(available_intraday_days(store))
    pages, days_written, rows_written, all_misses = 0, 0, 0, 0
    hi = d1
    while hi >= d0:
        lo = max(d0, hi - timedelta(days=_PAGE_DAYS - 1))
        pages += 1
        mat, misses = fetch_option_matrix(lo.isoformat(), (hi + timedelta(days=1)).isoformat(),
                                          offsets, flag, code, interval, security_id)
        all_misses += len(misses)
        if mat is None:
            log(f"  page {lo}..{hi}: no data ({len(misses)} misses)")
        else:
            wrote = 0
            for day, g in mat.groupby("date_ist"):
                if day in have:
                    continue
                n = save_intraday_day(store, day, g)
                have.add(day)
                days_written += 1
                rows_written += n
                wrote += 1
            log(f"  page {lo}..{hi}: {mat['date_ist'].nunique()} days, "
                f"{wrote} new, {rows_written:,} rows so far ({len(misses)} misses)")
        hi = lo - timedelta(days=1)
    return {"pages": pages, "days_written": days_written, "rows_written": rows_written,
            "misses": all_misses, "days_on_disk": len(have)}


DHAN_FUT_DIR = ROOT / "data" / "journal" / "dhan_futures"
_FUT_MAX_DAYS = 90               # charts/intraday hard cap (both window size and lookback)


def near_month_future_id(symbol: str = "NIFTY") -> dict | None:
    """Look up the CURRENT near-month index-futures securityId (+ expiry, lot) from the Dhan
    scrip master. The active contract is the only one whose intraday history is fetchable —
    expired contracts drop out of the master, so futures intraday depth is ~one contract life."""
    import io
    from datetime import date
    import requests
    import pandas as pd

    r = requests.get(_SCRIP_MASTER, timeout=90)
    if r.status_code != 200:
        return None
    df = pd.read_csv(io.BytesIO(r.content), low_memory=False)
    f = df[(df["SEM_INSTRUMENT_NAME"] == "FUTIDX")
           & (df["SEM_TRADING_SYMBOL"].astype(str).str.startswith(f"{symbol}-"))].copy()
    if f.empty:
        return None
    f["exp"] = pd.to_datetime(f["SEM_EXPIRY_DATE"], errors="coerce")
    f = f[f["exp"].dt.date >= date.today()].sort_values("exp")
    if f.empty:
        return None
    row = f.iloc[0]
    return {"security_id": int(row["SEM_SMST_SECURITY_ID"]), "symbol": row["SEM_TRADING_SYMBOL"],
            "expiry": row["exp"].date().isoformat(), "lot": int(row["SEM_LOT_UNITS"])}


def fetch_futures_intraday(security_id: int, from_date: str, to_date: str,
                           instrument: str = "FUTIDX", interval: str = "1",
                           segment: str = "NSE_FNO", timeout: int = 40):
    """POST charts/intraday for one futures contract over [from_date, to_date] (≤90d). Returns a
    tidy DataFrame (date_ist/time_ist + OHLC + volume + **oi**) or None. Raises only on missing
    token."""
    import requests
    import pandas as pd

    body = {"securityId": str(security_id), "exchangeSegment": segment, "instrument": instrument,
            "interval": str(interval), "oi": True, "fromDate": from_date, "toDate": to_date}
    try:
        r = requests.post(f"{DHAN_BASE}/charts/intraday", json=body, headers=_headers(),
                          timeout=timeout)
    except RuntimeError:
        raise
    except Exception:  # noqa: BLE001
        return None
    if r.status_code != 200:
        return None
    d = r.json() or {}
    ts = d.get("timestamp") or []
    if not ts:
        return None
    cols = {k: v for k, v in d.items()
            if isinstance(v, list) and len(v) == len(ts) and k != "timestamp"}
    df = pd.DataFrame({**cols, "timestamp": ts})
    if "open_interest" in df.columns:
        df = df.rename(columns={"open_interest": "oi"})
    dt = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df["date_ist"] = dt.dt.strftime("%Y-%m-%d")
    df["time_ist"] = dt.dt.strftime("%H:%M")
    return df


_FUT_STORE_COLS = ("time_ist", "open", "high", "low", "close", "volume", "oi")


def backfill_futures_intraday(symbol: str = "NIFTY", start: str | None = None,
                              end: str | None = None, security_id: int | None = None,
                              log=print) -> dict:
    """Backfill 1-min near-month index-futures (OHLC + volume + OI) to one parquet per trading
    day under dhan_futures/<SYMBOL>/. The API only serves the last 90 days AND only the active
    contract, so depth is inherently shallow (~one contract's life) — logged, not worked around.
    Resolves the near-month securityId from the scrip master unless one is passed."""
    from datetime import date, timedelta

    meta = None
    if security_id is None:
        meta = near_month_future_id(symbol)
        if not meta:
            log(f"  no active {symbol} future in the scrip master")
            return {"days_written": 0, "rows_written": 0}
        security_id = meta["security_id"]
        log(f"  near-month {meta['symbol']} (id {security_id}, exp {meta['expiry']}, lot {meta['lot']})")
    d1 = date.fromisoformat(end) if end else date.today()
    floor = d1 - timedelta(days=_FUT_MAX_DAYS - 1)
    d0 = max(date.fromisoformat(start), floor) if start else floor
    df = fetch_futures_intraday(security_id, d0.isoformat(), (d1 + timedelta(days=1)).isoformat())
    if df is None or df.empty:
        log(f"  {symbol}: no futures candles in {d0}..{d1}")
        return {"days_written": 0, "rows_written": 0}
    days_written = rows_written = 0
    out_dir = DHAN_FUT_DIR / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    for day, g in df.groupby("date_ist"):
        keep = [c for c in _FUT_STORE_COLS if c in g.columns]
        g[keep].sort_values("time_ist").reset_index(drop=True).to_parquet(
            out_dir / f"{day}.parquet", index=False)
        days_written += 1
        rows_written += len(g)
    log(f"  {symbol}: {days_written} days, {rows_written:,} rows "
        f"({df['date_ist'].min()}..{df['date_ist'].max()})")
    return {"days_written": days_written, "rows_written": rows_written,
            "security_id": security_id, "meta": meta}


def load_futures_intraday_day(symbol: str, day: str):
    import pandas as pd
    p = DHAN_FUT_DIR / symbol / f"{day}.parquet"
    return pd.read_parquet(p) if p.exists() else None


def to_frame(resp: dict, side: str = "ce"):
    """Parse the parallel-array response for one side ('ce'/'pe') into a tidy DataFrame
    (one row per candle: the requested fields + a UTC-epoch ``timestamp`` and IST ``time``).
    None if empty/absent."""
    if not resp or "_error" in resp:
        return None
    d = (resp.get("data") or {}).get(side)
    if not d:
        return None
    import pandas as pd
    ts = d.get("timestamp") or []
    if not ts:
        return None
    cols = {k: v for k, v in d.items()
            if k != "timestamp" and isinstance(v, list) and len(v) == len(ts)}
    df = pd.DataFrame({**cols, "timestamp": ts})
    dt = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df["date_ist"] = dt.dt.strftime("%Y-%m-%d")
    df["time_ist"] = dt.dt.strftime("%H:%M")
    return df


# --- LIVE DATA ADAPTER (from local branch) ---
_SETTLE_HOUR_UTC = 10  # 15:30 IST


class DhanAPIError(Exception):
    """Raised on a non-200 or error-body response from the Dhan API."""
    pass


class DhanCredentialError(Exception):
    """Raised when Dhan credentials are missing or invalid."""
    pass


# ---------- Low-level HTTP helpers -------------------------------------------

def _dhan_headers(access_token: str, client_id: str | None = None) -> dict:
    h = {
        "access-token": access_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if client_id:
        h["client-id"] = str(client_id)
    return h


def _post(url: str, access_token: str, payload: dict, client_id: str | None = None) -> Any:
    """POST request against Dhan API. Raises DhanAPIError on failure."""
    try:
        import requests
    except ImportError:
        raise ImportError(
            "requests is required for the Dhan adapter. "
            "Install it with: pip install requests"
        )
    resp = requests.post(
        url, headers=_dhan_headers(access_token, client_id=client_id), json=payload, timeout=20
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
    return _post(url, access_token, payload, client_id=client_id)


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
    data = _post(url, access_token, payload, client_id=client_id)
    # Response: {"data": ["2026-07-21", "2026-07-28", ...], "status": "success"}
    raw_data = data.get("data")
    if isinstance(raw_data, list):
        expiry_list = raw_data
    elif isinstance(raw_data, dict):
        expiry_list = raw_data.get("expiryList") or []
    else:
        expiry_list = []
    
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

    Dhan v2 provides greeks (delta, gamma, theta, vega) and implied_volatility
    directly in the response — these are extracted and stored so the engine can
    use them without local Black-Scholes recalculation.

    DOI (change in OI) is computed as oi - previous_oi using the previous_oi
    field provided by Dhan, which reflects the prior session's OI.

    PCR is NOT a dedicated Dhan endpoint — it is computed locally from the
    aggregated call/put OI in aggregate_oi_volume_dhan().

    spot is None if the underlying price is not available.
    """
    data = raw.get("data") or {}

    # Extract spot — Dhan v2 uses "last_price" at the top level for the underlying
    spot = None
    raw_spot = data.get("last_price") or data.get("underlying_scrip_price")
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

            # OI and DOI — Dhan provides current OI and previous_oi directly
            oi      = float(leg.get("oi") or leg.get("open_interest") or 0)
            prev_oi = float(leg.get("previous_oi") or 0)
            # doi = change in OI from previous session (positive = build-up)
            doi = oi - prev_oi

            vol = float(leg.get("volume") or 0)

            # Price: prefer top_bid/top_ask mid, fallback to last_price, then average_price
            bid = leg.get("top_bid_price")
            ask = leg.get("top_ask_price")
            ltp = leg.get("last_price")
            avg = leg.get("average_price")
            if bid is not None and ask is not None:
                try:
                    price = (float(bid) + float(ask)) / 2.0
                except (ValueError, TypeError):
                    price = float(ltp) if ltp is not None else float("nan")
            elif ltp is not None:
                try:
                    price = float(ltp)
                except (ValueError, TypeError):
                    price = float(avg) if avg is not None else float("nan")
            elif avg is not None:
                try:
                    price = float(avg)
                except (ValueError, TypeError):
                    price = float("nan")
            else:
                price = float("nan")

            # --- Greeks — Dhan provides delta, gamma, theta, vega directly ---
            # This avoids local Black-Scholes recalculation for display purposes.
            # The GEX engine still recomputes gamma internally for its own math.
            greeks_raw = leg.get("greeks") or {}
            def _safe_float(v) -> float:
                try:
                    return float(v) if v is not None else float("nan")
                except (ValueError, TypeError):
                    return float("nan")

            delta = _safe_float(greeks_raw.get("delta"))
            gamma = _safe_float(greeks_raw.get("gamma"))
            theta = _safe_float(greeks_raw.get("theta"))
            vega  = _safe_float(greeks_raw.get("vega"))

            # --- Implied Volatility — available directly from Dhan ---------
            iv_raw = leg.get("implied_volatility")
            iv = _safe_float(iv_raw)
            # Dhan returns IV as a percentage (e.g., 9.79); normalise to decimal
            # for consistency with the rest of the codebase (0.0979)
            if not math.isnan(iv) and iv > 1.0:
                iv = iv / 100.0

            rows.append({
                "expiry": expiry,
                "strike": strike,
                "type":   contract_type,
                "oi":     oi,
                "doi":    doi,
                "vol":    vol,
                "price":  price,
                "iv":     iv,      # from Dhan API (normalised to decimal)
                "T":      T,
                # Extra greeks from Dhan — stored for display in dashboard
                "delta":  delta,
                "gamma":  gamma,   # Dhan's own gamma (per-share, not lot-scaled)
                "theta":  theta,
                "vega":   vega,
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
        data = _post(url, access_token, payload, client_id=client_id)
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
