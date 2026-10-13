"""Realized Volatility Engine for Target Engine.

Computes annualized realized volatility across multiple estimators (Close-to-Close,
Parkinson, Garman-Klass) from historical candles or intraday bar series.
"""

from __future__ import annotations

import math
from typing import List, Optional, Union
import numpy as np
import pandas as pd


def compute_close_to_close_rv(
    prices: Union[List[float], np.ndarray, pd.Series],
    trading_periods_per_year: float = 252.0,
) -> float:
    """Calculate annualized close-to-close realized volatility.

    Parameters
    ----------
    prices : array-like
        Chronological sequence of prices.
    trading_periods_per_year : float
        Annualization factor (252 for daily, 252 * 75 for 5-minute bars).
    """
    if len(prices) < 2:
        return 0.15  # Fallback default vol

    p = np.asarray(prices, dtype=float)
    p = p[p > 0]
    if len(p) < 2:
        return 0.15

    log_returns = np.diff(np.log(p))
    if len(log_returns) == 0:
        return 0.15

    std_dev = np.std(log_returns, ddof=1) if len(log_returns) > 1 else np.std(log_returns)
    annualized_rv = float(std_dev * math.sqrt(trading_periods_per_year))
    return max(0.01, min(annualized_rv, 3.0))


def compute_parkinson_rv(
    highs: Union[List[float], np.ndarray],
    lows: Union[List[float], np.ndarray],
    trading_periods_per_year: float = 252.0,
) -> float:
    """Calculate Parkinson high-low volatility estimator.

    5x more statistically efficient than close-to-close for intraday data.
    """
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    valid = (h > 0) & (l > 0) & (h >= l)
    h, l = h[valid], l[valid]

    if len(h) < 2:
        return 0.15

    log_hl = np.log(h / l)
    variance = np.mean(log_hl ** 2) / (4.0 * math.log(2.0))
    annualized_rv = float(math.sqrt(variance * trading_periods_per_year))
    return max(0.01, min(annualized_rv, 3.0))


def compute_garman_klass_rv(
    opens: Union[List[float], np.ndarray],
    highs: Union[List[float], np.ndarray],
    lows: Union[List[float], np.ndarray],
    closes: Union[List[float], np.ndarray],
    trading_periods_per_year: float = 252.0,
) -> float:
    """Calculate Garman-Klass volatility estimator using OHLC bars."""
    o = np.asarray(opens, dtype=float)
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)

    valid = (o > 0) & (h > 0) & (l > 0) & (c > 0) & (h >= l)
    o, h, l, c = o[valid], h[valid], l[valid], c[valid]

    if len(o) < 2:
        return 0.15

    term1 = 0.5 * (np.log(h / l) ** 2)
    term2 = (2.0 * math.log(2.0) - 1.0) * (np.log(c / o) ** 2)
    variance = np.mean(term1 - term2)

    if variance <= 0.0:
        return compute_close_to_close_rv(c, trading_periods_per_year)

    annualized_rv = float(math.sqrt(variance * trading_periods_per_year))
    return max(0.01, min(annualized_rv, 3.0))
