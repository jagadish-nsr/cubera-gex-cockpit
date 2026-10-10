"""Synthetic, clearly-labelled demo Nifty chain so the CLI runs offline without
NSE. NOT a market read — never trade on it.

Mids are priced from Black-Scholes (call) and put-call parity (put), so they are
internally consistent and the parity step recovers the planted (r, q). Returns
normalised rows in the from_nse_results() input format.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

from data.expiry import next_monthly_expiry
from data.nse import years_to_expiry
from gex.bs import bs_call_price


DEFAULT_DEMO_SPOTS = {
    "NIFTY": 24500.0,
    "BANKNIFTY": 51400.0,
    "SENSEX": 72382.0,
}

DEFAULT_DEMO_STEPS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "SENSEX": 100,
}


def fetch_real_sensex_spot() -> float | None:
    """Best-effort live/recent market spot for SENSEX when broker token is expired."""
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    }
    try:
        r = requests.get(
            "https://query2.finance.yahoo.com/v8/finance/chart/%5EBSESN?interval=1d",
            headers=headers,
            timeout=3,
        )
        if r.ok:
            meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
            px = meta.get("regularMarketPrice") or meta.get("chartPreviousClose")
            if px and float(px) > 10000:
                return float(px)
    except Exception:
        pass
    return None


def build_demo_rows(
    spot: float | None = None,
    r: float = 0.066,
    q: float = 0.013,
    iv: float = 0.13,
    expiry: date | None = None,
    fetched_at: datetime | None = None,
    symbol: str = "NIFTY",
):
    fetched_at = fetched_at or datetime.now(timezone.utc)
    sym = (symbol or "NIFTY").upper()
    if spot is None or spot <= 0:
        if sym == "SENSEX":
            live_px = fetch_real_sensex_spot()
            spot = live_px if live_px else DEFAULT_DEMO_SPOTS.get(sym, 72382.0)
        else:
            spot = DEFAULT_DEMO_SPOTS.get(sym, 24500.0)


    step = DEFAULT_DEMO_STEPS.get(sym, 50)
    exchange = "BSE" if sym == "SENSEX" else "NSE"
    has_weekly = sym in ("NIFTY", "SENSEX")

    import data.expiry as expiry_mod
    next_weekly_expiry = getattr(expiry_mod, "next_weekly_expiry", None)
    second_weekly_expiry = getattr(expiry_mod, "second_weekly_expiry", None)
    next_monthly_expiry_fn = getattr(expiry_mod, "next_monthly_expiry", next_monthly_expiry)

    if second_weekly_expiry is None and next_weekly_expiry is not None:
        from datetime import timedelta
        def second_weekly_expiry(ref, exchange="NSE"):
            w1 = next_weekly_expiry(ref, exchange)
            return next_weekly_expiry(w1 + timedelta(days=1), exchange)

    if expiry is not None:
        expiries = [expiry]
    else:
        m = next_monthly_expiry_fn(fetched_at.date(), exchange)
        if has_weekly and next_weekly_expiry is not None:
            w1 = next_weekly_expiry(fetched_at.date(), exchange)
            w2 = second_weekly_expiry(fetched_at.date(), exchange) if second_weekly_expiry else w1
            expiries = sorted({w1, w2, m})
        else:
            expiries = [m]

    # Generate 41 clean, unique strikes around spot (range -20 to +20) to satisfy pre-analytics validators (>=20/25)
    base = round(spot / step) * step
    strikes = [base + step * i for i in range(-20, 21)]

    rows = []
    for exp in expiries:
        T = max(years_to_expiry(exp, fetched_at), 1e-4)
        DF = math.exp(-r * T)
        F = spot * math.exp((r - q) * T)
        for k in strikes:
            dist = (k - spot) / step
            call_oi = int(70000 * math.exp(-0.15 * (dist - 2) ** 2)) + 2000   # call wall above
            put_oi = int(90000 * math.exp(-0.15 * (dist + 2) ** 2)) + 2000    # put wall below
            call = float(bs_call_price(spot, k, T, iv, r, q))
            put = call - DF * (F - k)  # parity -> consistent & positive
            rows.append({
                "expiry": exp,
                "strike": k,
                "type": "C",
                "oi": call_oi,
                "iv": iv,
                "price": max(call, 0.05),
                "change": 5.0 if dist < 0 else -5.0,
                "volume": int(call_oi * 0.4),
            })
            rows.append({
                "expiry": exp,
                "strike": k,
                "type": "P",
                "oi": put_oi,
                "iv": iv,
                "price": max(put, 0.05),
                "change": -5.0 if dist < 0 else 5.0,
                "volume": int(put_oi * 0.4),
            })
    return rows, spot


def build_demo_payload(
    symbol: str = "NIFTY",
    spot: float | None = None,
    fetched_at: datetime | None = None,
) -> dict:
    """Build an NSE-shaped JSON payload dict from demo rows so that
    parse_nse_option_chain, aggregate_oi_volume, and per_strike_oi can consume it."""
    rows, sp = build_demo_rows(spot=spot, fetched_at=fetched_at, symbol=symbol)
    by_strike_exp: dict[tuple[float, str], dict] = {}
    for r in rows:
        exp_str = r["expiry"].strftime("%d-%b-%Y")
        key = (float(r["strike"]), exp_str)
        cell = by_strike_exp.setdefault(key, {
            "strikePrice": r["strike"],
            "expiryDate": exp_str,
            "CE": {},
            "PE": {},
        })
        side_key = "CE" if r["type"] == "C" else "PE"
        cell[side_key] = {
            "strikePrice": r["strike"],
            "expiryDate": exp_str,
            "openInterest": float(r["oi"]),
            "changeinOpenInterest": float(r["oi"] * 0.05 if side_key == "PE" else -r["oi"] * 0.02),
            "totalTradedVolume": float(r.get("volume", r["oi"] * 0.3)),
            "impliedVolatility": float(r["iv"] * 100.0),
            "lastPrice": float(r["price"]),
            "buyPrice1": float(r["price"] * 0.995),
            "sellPrice1": float(r["price"] * 1.005),
            "change": float(r.get("change", 0.0)),
        }
    data_list = list(by_strike_exp.values())
    return {
        "records": {
            "data": data_list,
            "underlyingValue": float(sp),
            "timestamp": (fetched_at or datetime.now(timezone.utc)).strftime("%d-%b-%Y %H:%M:%S"),
        }
    }


