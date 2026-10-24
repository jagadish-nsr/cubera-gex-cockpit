"""Imply the risk-free rate r and dividend yield q from put-call parity.

No hard-coded macro inputs (user requirement). For one expiry, across paired
strikes:

    C - P = e^(-rT) * (F - K)
          = (DF * F)  +  (-DF) * K        with DF = e^(-rT)

So a least-squares line of (C - P) vs K gives:
    slope     = -DF            -> DF = -slope,  r = -ln(DF) / T
    intercept =  DF * F        -> F  = intercept / DF
    q = r - ln(F / S) / T      (from F = S * e^((r-q)T))

Everything is derived from the same snapshot, is point-in-time, and is
inspectable (we return the fit diagnostics). Degenerate fits FLAG rather than
return a fabricated rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class ParityFit:
    expiry: object
    T: float
    r: float | None
    q: float | None
    forward: float | None
    n_strikes: int
    flagged: bool
    note: str


def imply_forward_and_rate(
    expiry_contracts: pd.DataFrame,
    spot: float,
    expiry=None,
    moneyness_band: float = 0.05,
) -> ParityFit:
    """expiry_contracts: rows for a SINGLE expiry with columns strike, type, mid, T.

    Fits parity on strikes within +/- moneyness_band of spot (most liquid, where
    parity holds tightest), falling back to all paired strikes if too few.
    """
    df = expiry_contracts.copy()
    df["type"] = df["type"].str.upper()
    T = float(df["T"].iloc[0])

    # Duplicate rows at one strike (two prints, a bad join) collapse to the median
    # mid so parity has one price per strike.
    calls = df[df["type"] == "C"].groupby("strike")["mid"].median()
    puts = df[df["type"] == "P"].groupby("strike")["mid"].median()
    common = sorted(set(calls.index) & set(puts.index))

    def _fail(note: str, n: int) -> ParityFit:
        return ParityFit(expiry, T, None, None, None, n, True, note)

    if T <= 0:
        return _fail("expiry already at/!past settlement (T<=0)", len(common))
    if len(common) < 2:
        return _fail("fewer than 2 paired call/put strikes — cannot fit parity", len(common))

    K = np.array(common, dtype=float)
    y = np.array([calls[k] - puts[k] for k in common], dtype=float)
    valid = np.isfinite(y) & np.isfinite(K)
    K, y = K[valid], y[valid]

    band = (K >= spot * (1 - moneyness_band)) & (K <= spot * (1 + moneyness_band))
    if band.sum() >= 2:
        Kf, yf = K[band], y[band]
    else:
        Kf, yf = K, y  # fall back to all paired strikes

    if len(Kf) < 2:
        return _fail("insufficient finite paired prices for parity fit", len(Kf))

    slope, intercept = np.polyfit(Kf, yf, 1)
    DF = -slope
    if not (0 < DF <= 1.5):
        return _fail(
            f"non-physical discount factor DF={DF:.4f} from parity slope — "
            f"flagging (illiquid/crossed prices)",
            len(Kf),
        )

    r = -math.log(DF) / T
    forward = intercept / DF
    if forward <= 0:
        return _fail(f"non-physical implied forward F={forward:.2f}", len(Kf))
    q = r - math.log(forward / spot) / T

    return ParityFit(
        expiry=expiry,
        T=T,
        r=r,
        q=q,
        forward=forward,
        n_strikes=len(Kf),
        flagged=False,
        note=f"DF={DF:.5f}, F={forward:.2f}, fit on {len(Kf)} strikes",
    )


@dataclass
class SpotFit:
    spot: float
    carry: float | None        # implied cost-of-carry c = r - q (slope of ln F vs T)
    n_expiries: int
    flagged: bool
    note: str


def _fit_df_forward(expiry_df: pd.DataFrame, center: float | None = None, band: float = 0.05):
    """Fit C-P = DF*(F-K) for one expiry WITHOUT needing spot. Returns (DF, F, n)
    or None. If center given, restrict to strikes within +/-band of it (cuts
    deep-ITM spread noise); otherwise use all paired strikes."""
    df = expiry_df.copy()
    df["type"] = df["type"].str.upper()
    # collapse duplicate strikes by median mid
    calls = df[df["type"] == "C"].groupby("strike")["mid"].median()
    puts = df[df["type"] == "P"].groupby("strike")["mid"].median()
    common = sorted(set(calls.index) & set(puts.index))
    if len(common) < 2:
        return None
    K = np.array(common, dtype=float)
    y = np.array([calls[k] - puts[k] for k in common], dtype=float)
    ok = np.isfinite(K) & np.isfinite(y)
    K, y = K[ok], y[ok]
    if center is not None:
        m = (K >= center * (1 - band)) & (K <= center * (1 + band))
        if m.sum() >= 3:
            K, y = K[m], y[m]
    if len(K) < 2:
        return None
    slope, intercept = np.polyfit(K, y, 1)
    DF = -slope
    if not (0 < DF <= 1.5):
        return None
    F = intercept / DF
    if F <= 0:
        return None
    return DF, F, len(K)


def imply_spot_from_chain(contracts: pd.DataFrame) -> SpotFit:
    """Derive spot from the chain alone (no Indices entitlement needed).

    Per expiry, parity gives an implied forward F_i with F_i = S * e^(c*T_i).
    So ln(F_i) = ln(S) + c*T_i is linear in T_i -> regress across expiries and
    read spot off the intercept. Robust, market-derived, point-in-time.
    """
    Ts, lnFs = [], []
    for expiry, grp in contracts.groupby("expiry"):
        T = float(grp["T"].iloc[0])
        if T <= 0:
            continue
        rough = _fit_df_forward(grp)
        if rough is None:
            continue
        # refit near the rough forward to reduce wing noise
        refined = _fit_df_forward(grp, center=rough[1]) or rough
        Ts.append(T)
        lnFs.append(math.log(refined[1]))

    n = len(Ts)
    if n == 0:
        raise ValueError("cannot imply spot: no expiry produced a usable parity forward")
    if n == 1:
        F = math.exp(lnFs[0])
        return SpotFit(
            spot=F, carry=None, n_expiries=1, flagged=True,
            note=f"only 1 expiry — using forward {F:.2f} as spot proxy (carry not separable)",
        )
    carry, intercept = np.polyfit(np.array(Ts), np.array(lnFs), 1)
    spot = math.exp(intercept)
    return SpotFit(
        spot=spot, carry=float(carry), n_expiries=n, flagged=False,
        note=f"regressed ln(F) on T across {n} expiries; carry c={carry:.4f}",
    )


def attach_parity_rates(contracts: pd.DataFrame, spot: float):
    """Attach per-expiry parity-implied r, q to every contract row.

    Returns (contracts_with_rq, {expiry: ParityFit}). Expiries whose parity fit
    is flagged get r=q=NaN on their rows so downstream code can detect/flag them
    rather than silently using a wrong rate.
    """
    fits: dict = {}
    out = contracts.copy()
    out["r"] = np.nan
    out["q"] = np.nan
    for expiry, grp in contracts.groupby("expiry"):
        fit = imply_forward_and_rate(grp, spot, expiry=expiry)
        fits[expiry] = fit
        if not fit.flagged:
            mask = out["expiry"] == expiry
            out.loc[mask, "r"] = fit.r
            out.loc[mask, "q"] = fit.q
    return out, fits
