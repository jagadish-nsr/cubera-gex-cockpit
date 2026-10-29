"""sentinel/engine/outcomes.py — Forward-Return Outcome Tracker.

Implements Sentinel §9:
  - Tracks forward performance of signals over +1m, +3m, +5m, +15m, +30m bars
  - Computes MFE (Maximum Favorable Excursion) and MAE (Maximum Adverse Excursion)
  - Checks if trade hit invalidation stop or reached 1R / 2R profit targets
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
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
    bars_5m: list[Bar] | None = None,
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
    if getattr(signal, "entry_price", 0.0) > 0:
        entry_price = float(signal.entry_price)
    elif subsequent_bars:
        entry_price = float(subsequent_bars[0].open)
    elif signal.invalidation_level > 0:
        entry_price = signal.invalidation_level + (risk_pts if is_long else -risk_pts)
    else:
        entry_price = 22600.0

    if signal.invalidation_level > 0:
        inval_risk = abs(entry_price - signal.invalidation_level)
        if inval_risk > 5.0:
            risk_pts = inval_risk

    # 2. Dynamic target points (Signal target, Structural Wall, or Realized Vol calibrated)
    if getattr(signal, "target_1_pts", 0.0) > 0:
        target_pts = float(signal.target_1_pts)
    elif target_pts is None or target_pts <= 0:
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

    # 3. Track excursion and forward returns on subsequent 1-minute bars
    for i, bar in enumerate(subsequent_bars):
        if is_long:
            fav = bar.high - entry_price
            adv = entry_price - bar.low
            cur_ret = bar.close - entry_price
        else:
            fav = entry_price - bar.low
            adv = bar.high - entry_price
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

    # 4. Invalidation & Target Hit: Evaluated ONLY when a completed 5-min candle CLOSES
    # Long: 5-min close < invalidation_level (INVALIDATED) | 5-min close >= target_price (TARGET_HIT)
    # Short: 5-min close > invalidation_level (INVALIDATED) | 5-min close <= target_price (TARGET_HIT)
    # Intra-bar wicks and incomplete/forming candles do not invalidate or reach targets.
    closed_5m_candles: list[Bar] = []

    if bars_5m is not None:
        for b5 in sorted(bars_5m, key=lambda b: b.start_ts):
            b5_close_ts = b5.start_ts + timedelta(minutes=5)
            if b5_close_ts > signal.bar_start_ts and not getattr(b5, "incomplete", False):
                closed_5m_candles.append(b5)
    elif subsequent_bars:
        from collections import defaultdict
        buckets: dict[datetime, list[Bar]] = defaultdict(list)
        for b in sorted(subsequent_bars, key=lambda b: b.start_ts):
            dt = b.start_ts
            bucket_min = (dt.minute // 5) * 5
            bucket_ts = dt.replace(minute=bucket_min, second=0, microsecond=0)
            buckets[bucket_ts].append(b)

        sorted_buckets = sorted(buckets.items(), key=lambda x: x[0])
        for idx, (b_ts, b_list) in enumerate(sorted_buckets):
            b_close_ts = b_ts + timedelta(minutes=5)
            # A 5-minute candle is closed if:
            # 1. A subsequent bucket exists in subsequent_bars, OR
            # 2. It contains all 5 1-minute bars of that interval
            is_closed = (idx < len(sorted_buckets) - 1) or (len(b_list) >= 5)
            if is_closed and b_close_ts > signal.bar_start_ts:
                closed_5m_candles.append(Bar(
                    instrument_id=signal.instrument_id,
                    timeframe="5m",
                    start_ts=b_ts,
                    open=b_list[0].open,
                    high=max(x.high for x in b_list),
                    low=min(x.low for x in b_list),
                    close=b_list[-1].close,
                    volume_units=sum(x.volume_units for x in b_list),
                    volume_lots=round(sum(x.volume_units for x in b_list) / 65.0, 1),
                    buy_vol_est=sum(x.buy_vol_est for x in b_list),
                    sell_vol_est=sum(x.sell_vol_est for x in b_list),
                    delta=sum(x.delta for x in b_list),
                    delta_ratio=0.0,
                    delta_quality=1.0,
                    cvd=b_list[-1].cvd,
                    vwap=b_list[-1].vwap,
                    vwap_sigma_1=b_list[-1].vwap_sigma_1,
                    vwap_sigma_2=b_list[-1].vwap_sigma_2,
                    vwap_lower_1=b_list[-1].vwap_lower_1,
                    vwap_lower_2=b_list[-1].vwap_lower_2,
                    oi=b_list[-1].oi,
                    delta_oi=0,
                ))

    # Evaluate closed 5-minute candles in chronological order
    for c5 in closed_5m_candles:
        c_close = float(c5.close)
        if is_long:
            if signal.invalidation_level > 0 and c_close < signal.invalidation_level:
                stopped = True
                break
            elif c_close >= target_price:
                target_hit = True
                break
        else:
            if signal.invalidation_level > 0 and c_close > signal.invalidation_level:
                stopped = True
                break
            elif c_close <= target_price:
                target_hit = True
                break

    status_val = "TARGET_HIT" if target_hit else ("INVALIDATED" if stopped else "ACTIVE")

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
        status=status_val,
        evaluated_at=datetime.now(timezone.utc),
    )
