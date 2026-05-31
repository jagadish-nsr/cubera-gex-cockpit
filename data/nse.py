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


def fetch_chain(underlying: str = "NIFTY"):
    """Live pull — NOT IMPLEMENTED. Resolve the data source first (see module
    docstring + docs/nifty-nse-adapter-scope.md §5), then implement here."""
    raise NotImplementedError(
        "Pick and verify an NSE data source before implementing fetch_chain "
        "(see docs/nifty-nse-adapter-scope.md §5)."
    )
