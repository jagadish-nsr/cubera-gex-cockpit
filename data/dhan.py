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
                         opt_type: str = "CALL", expiry_flag: str = "WEEK", expiry_code: int = 0,
                         interval: str = "1", security_id: int = NIFTY_SECURITY_ID,
                         required=_REQUIRED, timeout: int = 40) -> dict | None:
    """POST charts/rollingoption for one relative strike/type over [from_date, to_date)
    (YYYY-MM-DD; toDate non-inclusive). ``strike`` = 'ATM' or 'ATM+1'..'ATM+10'/'ATM-1'..
    'ATM-10'. Returns the raw JSON ({'data': {'ce': {...arrays...}, 'pe': {...}}}) or None on
    a soft failure. Raises only on a missing token."""
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
