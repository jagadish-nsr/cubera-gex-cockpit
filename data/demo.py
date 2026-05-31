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


def build_demo_rows(
    spot: float = 23500.0,
    r: float = 0.066,
    q: float = 0.013,
    iv: float = 0.13,
    expiry: date | None = None,
    fetched_at: datetime | None = None,
):
    fetched_at = fetched_at or datetime.now(timezone.utc)
    expiry = expiry or next_monthly_expiry(fetched_at.date())
    T = max(years_to_expiry(expiry, fetched_at), 1e-4)
    DF = math.exp(-r * T)
    F = spot * math.exp((r - q) * T)

    step = 50  # Nifty strike spacing
    strikes = [round((spot + step * i) / step) * step for i in range(-8, 9)]
    rows = []
    for k in strikes:
        dist = (k - spot) / step
        call_oi = int(70000 * math.exp(-0.15 * (dist - 2) ** 2)) + 2000   # call wall above
        put_oi = int(90000 * math.exp(-0.15 * (dist + 2) ** 2)) + 2000    # put wall below
        call = float(bs_call_price(spot, k, T, iv, r, q))
        put = call - DF * (F - k)  # parity -> consistent & positive
        rows.append({"expiry": expiry, "strike": k, "type": "C", "oi": call_oi,
                     "iv": iv, "price": max(call, 0.05)})
        rows.append({"expiry": expiry, "strike": k, "type": "P", "oi": put_oi,
                     "iv": iv, "price": max(put, 0.05)})
    return rows, spot
