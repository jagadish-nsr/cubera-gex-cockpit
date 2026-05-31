"""Vectorised Black-Scholes gamma and implied-vol inversion.

Gamma is recomputed here for the flip profile (gex-math skill: never reuse a
vendor's static gamma across hypothetical spots). All functions are pure and
operate elementwise on numpy arrays.

BS gamma with continuous dividend yield q:

    d1    = [ln(S/K) + (r - q + sigma^2 / 2) * T] / (sigma * sqrt(T))
    gamma = e^(-q*T) * N'(d1) / (S * sigma * sqrt(T))

gamma is identical for calls and puts at the same (S, K, T, sigma, r, q).
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


def bs_gamma(S, K, T, sigma, r=0.0, q=0.0):
    """Per-1-share Black-Scholes gamma. Inputs broadcast as numpy arrays.

    Degenerate inputs (T<=0, sigma<=0, S<=0, K<=0) yield gamma 0 rather than
    nan/inf — an expired or zero-vol contract contributes no hedging gamma.
    """
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    r = np.asarray(r, dtype=float)
    q = np.asarray(q, dtype=float)

    valid = (T > 0) & (sigma > 0) & (S > 0) & (K > 0)
    # Compute on a safe copy to avoid divide-by-zero warnings, then mask.
    Ts = np.where(valid, T, 1.0)
    sig = np.where(valid, sigma, 1.0)
    Ss = np.where(valid, S, 1.0)
    Ks = np.where(valid, K, 1.0)

    sqrtT = np.sqrt(Ts)
    d1 = (np.log(Ss / Ks) + (r - q + 0.5 * sig**2) * Ts) / (sig * sqrtT)
    gamma = np.exp(-q * Ts) * norm.pdf(d1) / (Ss * sig * sqrtT)
    return np.where(valid, gamma, 0.0)


def bs_call_price(S, K, T, sigma, r=0.0, q=0.0):
    """Black-Scholes call price (used to back out IV when a vendor omits it)."""
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    sqrtT = np.sqrt(np.where(T > 0, T, 1.0))
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    return S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)


def implied_vol_call(price, S, K, T, r=0.0, q=0.0, lo=1e-4, hi=5.0, tol=1e-6, iters=100):
    """Back out IV for a single call via bisection. Returns nan if no bracket.

    Used only as a fallback when the vendor does not populate implied_volatility.
    Scalar inputs (one contract at a time) — keep it dumb and inspectable.
    """
    if not (price > 0 and S > 0 and K > 0 and T > 0):
        return float("nan")
    f = lambda s: float(bs_call_price(S, K, T, s, r, q)) - price
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return float("nan")  # price outside model range -> FLAG upstream
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        fmid = f(mid)
        if abs(fmid) < tol:
            return mid
        if flo * fmid < 0:
            hi, fhi = mid, fmid
        else:
            lo, flo = mid, fmid
    return 0.5 * (lo + hi)
