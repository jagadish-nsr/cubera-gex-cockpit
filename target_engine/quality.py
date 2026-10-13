"""Data quality flags and validation rules for Target Engine (T1 spec).

Downstream stages down-weight or suppress output when quality flags are set.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Set


class QualityFlag(str, Enum):
    """Quality flags indicating potential market data degradation or regime anomalies."""
    STALE_SNAPSHOT = "stale_snapshot"
    THIN_CHAIN = "thin_chain"
    MISSING_DEPTH = "missing_depth"
    FIRST_SNAPSHOT_OF_DAY = "first_snapshot_of_day"
    WIDE_SPREADS = "wide_spreads"
    EXPIRY_DAY = "expiry_day"
    EVENT_WINDOW = "event_window"
    EXTREME_VOLATILITY = "extreme_volatility"


def validate_snapshot_quality(
    n_contracts: int,
    staleness_seconds: float,
    dte_calendar_days: float,
    has_depth: bool = False,
    minutes_from_open: float = 30.0,
    median_spread_pct: float = 1.0,
    max_acceptable_staleness: float = 180.0,
    min_contracts_threshold: int = 20,
) -> List[str]:
    """Inspect snapshot attributes and return active quality flag tags."""
    flags: List[str] = []

    if staleness_seconds > max_acceptable_staleness:
        flags.append(QualityFlag.STALE_SNAPSHOT.value)

    if n_contracts < min_contracts_threshold:
        flags.append(QualityFlag.THIN_CHAIN.value)

    if not has_depth:
        flags.append(QualityFlag.MISSING_DEPTH.value)

    if minutes_from_open < 15.0:
        flags.append(QualityFlag.FIRST_SNAPSHOT_OF_DAY.value)

    if median_spread_pct > 5.0:
        flags.append(QualityFlag.WIDE_SPREADS.value)

    if dte_calendar_days <= 1.0:
        flags.append(QualityFlag.EXPIRY_DAY.value)

    return flags
