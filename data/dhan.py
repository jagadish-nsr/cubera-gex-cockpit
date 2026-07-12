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

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DHAN_BASE = "https://api.dhan.co/v2"
NIFTY_SECURITY_ID = 13           # NIFTY index underlying (from the Dhan docs example)
_REQUIRED = ("open", "high", "low", "close", "volume", "oi", "iv", "spot", "strike")

# Index-underlying securityIds (from the Dhan scrip master) + the expiry interval that actually
# has options: NIFTY still lists weeklies; BANKNIFTY is monthly-only since Nov-2024.
DHAN_UNDERLYING = {"NIFTY": 13, "BANKNIFTY": 25}
DHAN_OPT_FLAG = {"NIFTY": "WEEK", "BANKNIFTY": "MONTH"}
_SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"


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
