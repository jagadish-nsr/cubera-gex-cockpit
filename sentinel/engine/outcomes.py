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
    risk_pts: float | None = None,
    target_pts: float | None = None,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
    sigma_h: float | None = None,
) -> SignalOutcome:
    """Evaluate forward return and excursions of a signal across subsequent bars with dynamic vol & wall targets."""
    inst = str(signal.instrument_id).upper()

    # 1. Dynamic risk points
    if risk_pts is None or risk_pts <= 0:
        if "SENSEX" in inst:
            base_risk = 75.0
        elif "BANKNIFTY" in inst:
            base_risk = 50.0
        else:
            base_risk = 25.0
        risk_pts = base_risk

    is_long = signal.bias == "LONG"
    if signal.invalidation_level > 0:
        entry_price = signal.invalidation_level + (risk_pts if is_long else -risk_pts)
    else:
        entry_price = subsequent_bars[0].open if subsequent_bars else 22600.0

    # 2. Dynamic target points (Structural Wall & Realized Vol calibrated)
    if target_pts is None or target_pts <= 0:
        if is_long and call_wall > entry_price:
            dist_w = call_wall - entry_price
            target_pts = min(dist_w, max(risk_pts * 1.5, 30.0))
        elif (not is_long) and put_wall > 0 and put_wall < entry_price:
            dist_w = entry_price - put_wall
            target_pts = min(dist_w, max(risk_pts * 1.5, 30.0))
        elif sigma_h and sigma_h > 0:
            target_pts = max(0.6 * sigma_h, risk_pts * 1.5)
        else:
            target_pts = max(risk_pts * 1.5, 30.0)

    target_price = entry_price + (target_pts if is_long else -target_pts)

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
            if bar.high >= target_price:
                target_hit = True
            cur_ret = bar.close - entry_price
        else:
            fav = entry_price - bar.low
            adv = bar.high - entry_price
            if bar.high >= signal.invalidation_level:
                stopped = True
            if bar.low <= target_price:
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
