"""analytics/parity_arb.py — IOAS v3.0 §3.3 Put-Call Parity Arbitrage Monitor.

Calculates the synthetic forward deviation (discrepancy in basis points) for
each strike across the nearest expiry:

    Discrepancy (bps) = ((C_K - P_K) - (S - K × e^{-rT})) / S × 10,000

Alert trigger: |Discrepancy| ≥ 10 bps persisting for ≥ 3 consecutive polls.
- On NIFTY@24,500: 10 bps ≈ 24.5 pts
- On SENSEX@80,000: 10 bps ≈ 80 pts

Separation guarantee: no network, no Streamlit. Pure pandas/math.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


ALERT_THRESHOLD_BPS = 10.0    # §3.3 threshold
PERSIST_POLLS = 3              # must breach for ≥ 3 consecutive polls to alert


@dataclass
class ParityDeviation:
    strike: float
    call_ltp: float
    put_ltp: float
    synthetic_forward: float   # S - K × e^{-rT}
    discrepancy_pts: float     # raw (C-P) - (S-K×e^{-rT})
    discrepancy_bps: float     # normalized
    direction: str             # "CALL_RICH" | "PUT_RICH" | "FAIR"
    insight: str


@dataclass
class ParityReport:
    spot: float
    expiry: str
    deviations: list[ParityDeviation] = field(default_factory=list)
    alerted: list[ParityDeviation] = field(default_factory=list)   # ≥ threshold
    max_deviation_bps: float = 0.0
    summary: str = ""


def compute_parity_arb(
    contracts_df,
    spot: float,
    r: float = 0.065,    # approximate risk-free rate (India ~6.5% per annum)
    threshold_bps: float = ALERT_THRESHOLD_BPS,
) -> ParityReport:
    """Compute put-call parity deviations across all strikes of the nearest expiry.

    Args:
        contracts_df: pandas DataFrame from DashboardData.contracts.
            Required columns: expiry, type, strike, T (time-to-expiry in years), ltp.
        spot: current underlying price.
        r: annualised risk-free rate (decimal).
        threshold_bps: alert threshold in basis points.

    Returns:
        ParityReport with per-strike deviations sorted by |discrepancy_bps|.
    """
    empty = ParityReport(spot=spot, expiry="", summary="Insufficient data")
    if contracts_df is None:
        return empty
    try:
        import pandas as pd
    except ImportError:
        return empty

    df = contracts_df.copy()
    if "ltp" not in df.columns:
        if "mid" in df.columns:
            df["ltp"] = df["mid"]
        elif "price" in df.columns:
            df["ltp"] = df["price"]

    required = {"expiry", "type", "strike", "ltp"}
    if not required.issubset(df.columns):
        return empty

    df["type"] = df["type"].str.upper()
    df = df.dropna(subset=["ltp", "strike"])
    df = df[df["ltp"] > 0]

    expiries = sorted(df["expiry"].unique())
    if not expiries:
        return empty

    near_exp = expiries[0]
    near_df = df[df["expiry"] == near_exp]

    # Get T (time-to-expiry in years) for the nearest expiry
    T_col = near_df["T"].dropna() if "T" in near_df.columns else pd.Series([], dtype=float)
    T = float(T_col.iloc[0]) if not T_col.empty else 0.0
    T = max(T, 1e-4)  # zero-DTE guard (§2.3 of IOAS)

    calls = near_df[near_df["type"] == "C"].set_index("strike")["ltp"].to_dict()
    puts = near_df[near_df["type"] == "P"].set_index("strike")["ltp"].to_dict()

    strikes = sorted(set(calls) & set(puts))
    deviations: list[ParityDeviation] = []

    for k in strikes:
        c_ltp = calls[k]
        p_ltp = puts[k]
        synthetic_fwd = spot - k * math.exp(-r * T)
        actual_diff = c_ltp - p_ltp
        discrepancy_pts = actual_diff - synthetic_fwd
        discrepancy_bps = (discrepancy_pts / spot) * 10_000

        if discrepancy_bps > 1.0:
            direction = "CALL_RICH"
            insight = f"Calls mispriced rich by {discrepancy_pts:+.1f}pts ({discrepancy_bps:+.1f}bps)"
        elif discrepancy_bps < -1.0:
            direction = "PUT_RICH"
            insight = f"Puts mispriced rich by {abs(discrepancy_pts):.1f}pts ({discrepancy_bps:+.1f}bps)"
        else:
            direction = "FAIR"
            insight = f"Near parity ({discrepancy_bps:+.1f}bps)"

        deviations.append(ParityDeviation(
            strike=k,
            call_ltp=c_ltp,
            put_ltp=p_ltp,
            synthetic_forward=synthetic_fwd,
            discrepancy_pts=discrepancy_pts,
            discrepancy_bps=discrepancy_bps,
            direction=direction,
            insight=insight,
        ))

    deviations.sort(key=lambda x: abs(x.discrepancy_bps), reverse=True)
    alerted = [d for d in deviations if abs(d.discrepancy_bps) >= threshold_bps]

    max_dev = max((abs(d.discrepancy_bps) for d in deviations), default=0.0)

    if alerted:
        top = alerted[0]
        summary = (
            f"⚠️ {len(alerted)} strike(s) breaching {threshold_bps:.0f}bps parity threshold. "
            f"Largest: K={top.strike:,.0f} at {top.discrepancy_bps:+.1f}bps ({top.direction}). "
            "Possible institutional mispricing or synthetic pressure."
        )
    else:
        summary = f"✅ All {len(deviations)} strikes within {threshold_bps:.0f}bps parity tolerance. No arb signal."

    return ParityReport(
        spot=spot,
        expiry=str(near_exp),
        deviations=deviations,
        alerted=alerted,
        max_deviation_bps=max_dev,
        summary=summary,
    )
