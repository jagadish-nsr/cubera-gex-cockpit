"""analytics/iv_skew.py — IV skew and term structure from Dhan option chain data.

Pure function: no network, no streamlit. Input is the contracts DataFrame from
DashboardData.contracts (the options snapshot DataFrame produced by data_access.py).

Dhan provides delta per contract — this means we can find the 25-delta strikes
WITHOUT a BS inversion: simply find the call/put whose |delta| is closest to 0.25.

Separation guarantee: imports nothing from app or data layers at module level.
All heavy imports are inside functions (lazy) to stay unit-testable with fixtures.
"""

from __future__ import annotations

import math


def _nearest_delta_strike(
    df,                 # pandas DataFrame subset for one type (C or P) and one expiry
    target_delta: float,
    spot: float,
) -> dict | None:
    """Return the row (as dict) whose |delta| is closest to ``target_delta``.

    For calls: target_delta = +0.25 (delta positive).
    For puts:  target_delta = -0.25 (delta negative) → we use abs.
    """
    try:
        import pandas as pd
        if df.empty or "delta" not in df.columns or "iv" not in df.columns:
            return None
        df = df.dropna(subset=["delta", "iv"])
        if df.empty:
            return None
        df = df.copy()
        df["_delta_dist"] = (df["delta"].abs() - target_delta).abs()
        best = df.nsmallest(1, "_delta_dist").iloc[0]
        return best.to_dict()
    except Exception:
        return None


def skew_25delta(
    contracts_df,       # pandas DataFrame from DashboardData.contracts
    spot: float,
) -> dict:
    """Compute the 25-delta IV skew and ATM term structure.

    Methodology:
        1. Take the NEAREST expiry (front-month, where skew is most tradeable).
        2. Find the call whose |delta| ≈ 0.25 → call_iv_25d.
        3. Find the put  whose |delta| ≈ 0.25 → put_iv_25d.
        4. Skew = put_iv_25d − call_iv_25d.
           Positive skew = puts more expensive than calls (typical/fear bid).
           Negative skew = calls more expensive (unusual, rare squeeze).
        5. For term structure: ATM IV of nearest expiry vs. second expiry.
           ATM strike = strike closest to spot.

    Returns:
        {
          "skew_25d":         float | None,  # put_iv_25d − call_iv_25d (decimal)
          "put_iv_25d":       float | None,
          "call_iv_25d":      float | None,
          "skew_label":       str,           # "put-bid", "call-bid", "neutral", "insufficient data"
          "near_atm_iv":      float | None,  # ATM IV of nearest expiry
          "next_atm_iv":      float | None,  # ATM IV of second expiry (term structure)
          "term_structure":   float | None,  # next_atm_iv − near_atm_iv (positive = normal backwardation)
          "term_label":       str,
          "near_expiry":      str | None,
          "next_expiry":      str | None,
        }
    """
    empty = {
        "skew_25d": None, "put_iv_25d": None, "call_iv_25d": None,
        "skew_label": "insufficient data",
        "near_atm_iv": None, "next_atm_iv": None, "term_structure": None,
        "term_label": "insufficient data",
        "near_expiry": None, "next_expiry": None,
    }

    try:
        import pandas as pd
    except ImportError:
        return empty

    if contracts_df is None or (hasattr(contracts_df, "empty") and contracts_df.empty):
        return empty

    df = contracts_df.copy()

    # Need: expiry, type, delta, iv, strike, T
    required = {"expiry", "type", "iv", "strike"}
    if not required.issubset(df.columns):
        return empty

    df["type"] = df["type"].str.upper()
    df = df.dropna(subset=["iv"])
    df = df[df["iv"] > 0]

    expiries = sorted(df["expiry"].unique())
    if not expiries:
        return empty

    near_exp = expiries[0]
    next_exp = expiries[1] if len(expiries) > 1 else None

    near_df = df[df["expiry"] == near_exp]
    near_calls = near_df[near_df["type"] == "C"]
    near_puts  = near_df[near_df["type"] == "P"]

    # --- 25-delta IV skew ---
    call_row = _nearest_delta_strike(near_calls, target_delta=0.25, spot=spot)
    put_row  = _nearest_delta_strike(near_puts,  target_delta=0.25, spot=spot)

    call_iv = float(call_row["iv"]) if call_row and math.isfinite(float(call_row["iv"])) else None
    put_iv  = float(put_row["iv"])  if put_row  and math.isfinite(float(put_row["iv"]))  else None

    skew = (put_iv - call_iv) if (put_iv is not None and call_iv is not None) else None
    if skew is not None:
        if skew > 0.02:
            skew_label = "put-bid (fear premium)"
        elif skew < -0.02:
            skew_label = "call-bid (rare squeeze)"
        else:
            skew_label = "neutral"
    else:
        skew_label = "insufficient data"

    # --- ATM IV for term structure ---
    def _atm_iv(subset_df) -> float | None:
        if subset_df.empty:
            return None
        # ATM = strike closest to spot among both calls and puts
        subset_df = subset_df.copy()
        subset_df["_dist"] = (subset_df["strike"] - spot).abs()
        atm = subset_df.nsmallest(4, "_dist")  # a few strikes around ATM
        vals = atm["iv"].dropna()
        if vals.empty:
            return None
        v = float(vals.median())
        return v if math.isfinite(v) else None

    near_atm_iv = _atm_iv(near_df)
    next_atm_iv = _atm_iv(df[df["expiry"] == next_exp]) if next_exp is not None else None

    if near_atm_iv is not None and next_atm_iv is not None:
        term = next_atm_iv - near_atm_iv
        if term > 0.01:
            term_label = "normal (back month bid)"
        elif term < -0.01:
            term_label = "inverted (front month bid)"
        else:
            term_label = "flat"
    else:
        term = None
        term_label = "insufficient data"

    return {
        "skew_25d":       skew,
        "put_iv_25d":     put_iv,
        "call_iv_25d":    call_iv,
        "skew_label":     skew_label,
        "near_atm_iv":    near_atm_iv,
        "next_atm_iv":    next_atm_iv,
        "term_structure": term,
        "term_label":     term_label,
        "near_expiry":    str(near_exp),
        "next_expiry":    str(next_exp) if next_exp is not None else None,
    }
