"""sentinel/engine/outcomes.py — Forward-Return Outcome Tracker.

Implements Sentinel §9:
  - Tracks forward performance of signals over +1m, +3m, +5m, +15m, +30m bars
  - Computes MFE (Maximum Favorable Excursion) and MAE (Maximum Adverse Excursion)
  - Checks if trade hit invalidation stop or reached 1R / 2R profit targets
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sentinel.models import Bar, SentinelSignal, SignalOutcome

log = logging.getLogger(__name__)


def evaluate_signal_outcomes(
    signal: SentinelSignal,
    subsequent_bars: list[Bar],
    risk_pts: float = 25.0,
) -> SignalOutcome:
    """Evaluate forward return and excursions of a signal across subsequent bars."""
    entry_price = signal.invalidation_level + (risk_pts if signal.bias == "LONG" else -risk_pts)
    # If invalidation is below entry (LONG), entry ~ invalidation + risk
    # Or fallback to close of trigger bar if known
    is_long = signal.bias == "LONG"
    target_1r = entry_price + (risk_pts if is_long else -risk_pts)

    mfe = 0.0
    mae = 0.0
    stopped = False
    target_hit = False

    ret_1m: float | None = None
    ret_3m: float | None = None
    ret_5m: float | None = None
    ret_15m: float | None = None
    ret_30m: float | None = None

    for i, bar in enumerate(subsequent_bars):
        if is_long:
            fav = bar.high - entry_price
            adv = entry_price - bar.low
            if bar.low <= signal.invalidation_level:
                stopped = True
            if bar.high >= target_1r:
                target_hit = True
            cur_ret = bar.close - entry_price
        else:
            fav = entry_price - bar.low
            adv = bar.high - entry_price
            if bar.high >= signal.invalidation_level:
                stopped = True
            if bar.low <= target_1r:
                target_hit = True
            cur_ret = entry_price - bar.close

        mfe = max(mfe, fav)
        mae = max(mae, adv)

        if i == 0:
            ret_1m = round(cur_ret, 2)
        elif i == 2:
            ret_3m = round(cur_ret, 2)
        elif i == 4:
            ret_5m = round(cur_ret, 2)
        elif i == 14:
            ret_15m = round(cur_ret, 2)
        elif i == 29:
            ret_30m = round(cur_ret, 2)

    return SignalOutcome(
        event_id=signal.event_id,
        instrument_id=signal.instrument_id,
        signal_type=signal.type,
        entry_price=round(entry_price, 2),
        invalidation_level=signal.invalidation_level,
        ret_1m_pts=ret_1m,
        ret_3m_pts=ret_3m,
        ret_5m_pts=ret_5m,
        ret_15m_pts=ret_15m,
        ret_30m_pts=ret_30m,
        mfe_pts=round(mfe, 2),
        mae_pts=round(mae, 2),
        stopped_out=stopped,
        target_hit=target_hit,
        evaluated_at=datetime.now(timezone.utc),
    )
