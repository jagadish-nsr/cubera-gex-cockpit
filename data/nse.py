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
    spot = records.get("underlyingValue")
    if spot is None:
        raise ValueError("NSE payload missing records.underlyingValue (spot)")

    def _mid(side: dict) -> float | None:
        bid, ask = side.get("bidprice"), side.get("askPrice")
        if bid and ask and bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        lp = side.get("lastPrice")
        return lp if lp else None

    def _iv(side: dict):
        iv = side.get("impliedVolatility")
        return (iv / 100.0) if iv else None  # percent -> decimal; 0/None -> None

    rows: list[dict] = []
    for row in records.get("data", []):
        for side_key, typ in (("CE", "C"), ("PE", "P")):
            side = row.get(side_key)
            if not side:
                continue
            rows.append({
                "expiry": _parse_nse_date(row["expiryDate"]),
                "strike": float(row["strikePrice"]),
                "type": typ,
                "oi": side.get("openInterest") or 0.0,
                "iv": _iv(side),
                "price": _mid(side),
            })
    if not rows:
        raise ValueError("NSE payload produced no contracts")
    return rows, float(spot)


def _parse_nse_date(s: str):
    from datetime import datetime as _dt
    return _dt.strptime(s, "%d-%b-%Y").date()


def fetch_chain(symbol: str = "NIFTY", retries: int = 3, timeout: int = 10) -> dict:
    """Live pull from the official NSE endpoint (PoC source).

    NSE bot-gates the API: it requires browser-like headers AND a session cookie
    primed by first visiting the option-chain page. It also rate-limits / blocks
    datacenter IPs — this is most reliable from a normal (Indian) connection.
    Returns the raw JSON payload; pass it to parse_nse_option_chain().
    """
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/option-chain",
    }
    s = requests.Session()
    s.headers.update(headers)
    last_err = None
    for _ in range(retries):
        try:
            # prime cookies (homepage then the option-chain page)
            s.get("https://www.nseindia.com", timeout=timeout)
            s.get("https://www.nseindia.com/option-chain", timeout=timeout)
            r = s.get(
                "https://www.nseindia.com/api/option-chain-indices",
                params={"symbol": symbol}, timeout=timeout,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - surface after retries
            last_err = e
    raise RuntimeError(
        f"NSE fetch failed after {retries} tries ({last_err}). NSE blocks bots / "
        "datacenter IPs — run from a normal connection, or switch to a broker API."
    )
