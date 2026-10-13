"""Black-76 Option Pricing Engine (T5 Quant Module).

Computes analytical prices, Greeks (Delta, Gamma, Vega, Theta, Vanna, Volga),
robust implied volatility via Newton-Raphson + Bisection fallback,
hypothetical scenario repricing, and Greek attribution decompositions.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple, Union

import numpy as np
from scipy.stats import norm

from target_engine.contracts import GreekAttribution, OptionGreeks


def price(
    F: float,
    K: float,
    T: float,
    sigma: float,
    r: float = 0.065,
    option_type: str = "CE",
) -> float:
    """Black-76 option pricing formula on forward/futures price F.

    Parameters
    ----------
    F : float
        Current underlying futures / forward price (must be > 0).
    K : float
        Strike price (must be > 0).
    T : float
        Time to expiry in annualized calendar years (must be > 0).
    sigma : float
        Implied volatility as decimal (e.g., 0.15 for 15% IV).
    r : float
        Annualized continuously compounded risk-free rate.
    option_type : str
        "CE" (Call) or "PE" (Put).

    Returns
    -------
    float
        Option theoretical price in currency units.
    """
    if F <= 0.0 or K <= 0.0:
        raise ValueError("Forward F and Strike K must be strictly positive.")

    opt = option_type.upper()
    df = math.exp(-r * T)

    # Near-zero time or near-zero volatility -> return discounted intrinsic value
    if T <= 1e-8 or sigma <= 1e-6:
        if opt in ("CE", "CALL", "C"):
            return df * max(0.0, F - K)
        return df * max(0.0, K - F)

    vol_sqrt_t = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t

    if opt in ("CE", "CALL", "C"):
        return df * (F * norm.cdf(d1) - K * norm.cdf(d2))
    elif opt in ("PE", "PUT", "P"):
        return df * (K * norm.cdf(-d2) - F * norm.cdf(-d1))
    else:
        raise ValueError(f"Unknown option_type '{option_type}', expected 'CE' or 'PE'.")


def greeks(
    F: float,
    K: float,
    T: float,
    sigma: float,
    r: float = 0.065,
    option_type: str = "CE",
) -> OptionGreeks:
    """Calculate analytical Greeks under the Black-76 model.

    Greeks conventions:
    - Delta: dPrice / dF (discounted for forward)
    - Gamma: d^2Price / dF^2
    - Vega: dPrice / d(1% IV), i.e., price change per 0.01 change in volatility
    - Theta: dPrice / d(calendar day elapsed), expressed per day (negative for long)
    - Vanna: dVega / dF (sensitivity of Vega to forward move)
    - Volga: dVega / dSigma (vomma / convexity of Vega to volatility)
    """
    opt = option_type.upper()
    df = math.exp(-r * T)

    if T <= 1e-8 or sigma <= 1e-6:
        is_call = opt in ("CE", "CALL", "C")
        if is_call:
            delta = df if F > K else 0.0
        else:
            delta = -df if K > F else 0.0
        return OptionGreeks(delta=delta, gamma=0.0, vega=0.0, theta=0.0, vanna=0.0, volga=0.0)

    sqrt_t = math.sqrt(T)
    vol_sqrt_t = sigma * sqrt_t
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t

    pdf_d1 = norm.pdf(d1)
    cdf_d1 = norm.cdf(d1)
    cdf_d2 = norm.cdf(d2)

    is_call = opt in ("CE", "CALL", "C")

    # Delta
    if is_call:
        delta = df * cdf_d1
    else:
        delta = -df * norm.cdf(-d1)

    # Gamma (identical for Call and Put)
    gamma = (df * pdf_d1) / (F * vol_sqrt_t)

    # Vega per 1 percentage point (0.01) of IV
    # Raw vega = df * F * sqrt_t * pdf_d1
    raw_vega = df * F * sqrt_t * pdf_d1
    vega = raw_vega * 0.01

    # Theta per calendar day (1/365 year)
    # dV/dt (where t is elapsed time, dT = -dt)
    term1 = -(df * F * pdf_d1 * sigma) / (2.0 * sqrt_t)
    if is_call:
        theta_annual = term1 + r * df * (F * cdf_d1 - K * cdf_d2)
    else:
        theta_annual = term1 + r * df * (K * norm.cdf(-d2) - F * norm.cdf(-d1))
    theta = theta_annual / 365.0

    # Vanna (change in vega per point move in forward)
    # d(raw_vega)/dF = raw_vega * (1 - d1 / (sigma * sqrt_t)) / F = -df * pdf_d1 * d2 / sigma
    vanna = (-df * pdf_d1 * d2 / sigma) * 0.01

    # Volga / Vomma (second derivative with respect to sigma)
    # d(raw_vega)/dSigma = raw_vega * d1 * d2 / sigma
    volga = (raw_vega * d1 * d2 / sigma) * 0.0001

    return OptionGreeks(
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        vanna=vanna,
        volga=volga,
    )


def implied_vol(
    market_price: float,
    F: float,
    K: float,
    T: float,
    r: float = 0.065,
    option_type: str = "CE",
    tol: float = 1e-6,
    max_iter: int = 50,
) -> Optional[float]:
    """Solve for Black-76 implied volatility using Newton-Raphson with Bisection fallback.

    Parameters
    ----------
    market_price : float
        Observed market premium.
    F : float
        Forward price.
    K : float
        Strike price.
    T : float
        Time to expiry in years.
    r : float
        Risk-free rate.
    option_type : str
        "CE" or "PE".
    tol : float
        Convergence precision in volatility units (default: 1e-6).
    max_iter : int
        Maximum Newton iterations before falling back to bisection.

    Returns
    -------
    Optional[float]
        Implied volatility as decimal (e.g. 0.185 for 18.5%), or None if no arbitrage-free IV exists.
    """
    if market_price <= 0.0 or F <= 0.0 or K <= 0.0 or T <= 1e-8:
        return None

    opt = option_type.upper()
    df = math.exp(-r * T)
    is_call = opt in ("CE", "CALL", "C")

    # Intrinsic value bounds (no-arbitrage lower bound)
    intrinsic = df * max(0.0, F - K) if is_call else df * max(0.0, K - F)
    if market_price < intrinsic - 1e-5:
        # Below intrinsic value -> no positive IV can price this option
        return None

    # Theoretical upper bounds
    max_price = df * F if is_call else df * K
    if market_price >= max_price:
        return None

    # Step 1: Initial guess (Corrado-Miller / Brenner-Subrahmanyam approximation)
    # For near-ATM, sigma ~= sqrt(2*pi/T) * (price / (df * F))
    sqrt_t = math.sqrt(T)
    approx_atm = math.sqrt(2.0 * math.pi / T) * (market_price / max(df * F, 1.0))
    sigma = min(max(approx_atm, 0.05), 1.5)

    # Step 2: Newton-Raphson iteration
    for _ in range(max_iter):
        p = price(F, K, T, sigma, r, opt)
        diff = p - market_price

        if abs(diff) < 1e-7:
            return sigma

        # Vega derivative w.r.t sigma (undivided)
        d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
        raw_vega = df * F * sqrt_t * norm.pdf(d1)

        if raw_vega < 1e-12:
            break  # Vega too small, jump to bisection

        step = diff / raw_vega
        sigma -= step

        if sigma <= 0.001 or sigma >= 5.0 or math.isnan(sigma):
            break  # Stepped outside valid range, jump to bisection

        if abs(step) < tol:
            return sigma

    # Step 3: Bisection fallback (guaranteed monotonic convergence)
    low = 0.0001
    high = 5.0

    p_low = price(F, K, T, low, r, opt)
    p_high = price(F, K, T, high, r, opt)

    if market_price < p_low or market_price > p_high:
        return None

    for _ in range(70):
        mid = 0.5 * (low + high)
        p_mid = price(F, K, T, mid, r, opt)
        diff = p_mid - market_price

        if abs(diff) < 1e-6 or (high - low) < tol:
            return mid

        if diff > 0.0:
            high = mid
        else:
            low = mid

    return 0.5 * (low + high)


def reprice(
    F_new: float,
    K: float,
    T_new: float,
    sigma_new: float,
    r: float = 0.065,
    option_type: str = "CE",
) -> float:
    """Reprice an option at hypothetical future forward, time, and volatility."""
    return price(F=F_new, K=K, T=T_new, sigma=sigma_new, r=r, option_type=option_type)


def greek_attribution(
    F_entry: float,
    F_target: float,
    sigma_entry: float,
    sigma_target: float,
    T_entry: float,
    T_target: float,
    K: float,
    r: float = 0.065,
    option_type: str = "CE",
) -> GreekAttribution:
    """Decompose option price change into Delta, Gamma, Vega, Theta, and residual.

    Parameters
    ----------
    F_entry, F_target : float
        Initial and target underlying forward prices.
    sigma_entry, sigma_target : float
        Initial and target implied volatilities (decimals).
    T_entry, T_target : float
        Initial and target annualized times to expiry.
    K : float
        Strike price.
    r : float
        Risk-free rate.
    option_type : str
        "CE" or "PE".

    Returns
    -------
    GreekAttribution
        Complete breakdown of the price change with residual term.
    """
    # 1. Entry price and analytical Greeks at entry
    price_entry = price(F_entry, K, T_entry, sigma_entry, r, option_type)
    price_target = price(F_target, K, T_target, sigma_target, r, option_type)
    full_reprice_pnl = price_target - price_entry

    g = greeks(F_entry, K, T_entry, sigma_entry, r, option_type)

    # 2. Input displacements
    dF = F_target - F_entry
    # Volatility change in percentage points (1% = 0.01)
    dSigma_pts = (sigma_target - sigma_entry) * 100.0
    # Elapsed calendar days (T_entry - T_target >= 0)
    dT_days = max(0.0, (T_entry - T_target) * 365.0)

    # 3. Component contributions
    delta_pnl = g.delta * dF
    gamma_pnl = 0.5 * g.gamma * (dF ** 2)
    vega_pnl = g.vega * dSigma_pts
    # Theta is defined per elapsed calendar day (negative for long options)
    theta_pnl = g.theta * dT_days

    total_modeled_pnl = delta_pnl + gamma_pnl + vega_pnl + theta_pnl
    residual = full_reprice_pnl - total_modeled_pnl

    return GreekAttribution(
        delta_pnl=delta_pnl,
        gamma_pnl=gamma_pnl,
        vega_pnl=vega_pnl,
        theta_pnl=theta_pnl,
        residual=residual,
        total_modeled_pnl=total_modeled_pnl,
        full_reprice_pnl=full_reprice_pnl,
    )


def compute_path_grid(
    F_entry: float,
    K: float,
    T_entry: float,
    sigma_entry: float,
    target_sigma: float,
    spot_shifts: list[float],
    time_fractions: list[float],
    r: float = 0.065,
    option_type: str = "CE",
) -> list[dict]:
    """Compute premium matrix across a grid of (spot move x time elapsed).

    Exposes theta bleed if the move takes longer than expected.
    """
    grid = []
    for f_mult in spot_shifts:
        f_val = F_entry * f_mult
        row = {"forward": f_val, "spot_shift_pct": (f_mult - 1.0) * 100.0}
        for frac in time_fractions:
            # frac = 0.0 means at entry, frac = 1.0 means at horizon end
            t_val = max(1e-6, T_entry * (1.0 - frac))
            # Linear interpolation of sigma between entry and target
            sig_val = sigma_entry + frac * (target_sigma - sigma_entry)
            p = price(f_val, K, t_val, sig_val, r, option_type)
            row[f"t_{int(frac*100)}pct"] = p
        grid.append(row)
    return grid


def reconciliation_check(
    quoted_price: float,
    F: float,
    K: float,
    T: float,
    quoted_iv: float,
    r: float = 0.065,
    option_type: str = "CE",
    tolerance_pts: float = 2.0,
) -> Tuple[bool, float, float]:
    """Mandatory T5 reconciliation check between model price and quoted price.

    Returns (is_reconciled, model_price, gap_pts).
    """
    model_p = price(F, K, T, quoted_iv, r, option_type)
    gap = abs(model_p - quoted_price)
    return (bool(gap <= tolerance_pts), float(model_p), float(gap))
