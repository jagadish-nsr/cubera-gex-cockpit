"""sentinel/engine/aggregator.py — Bar Aggregator & Microstructure Metrics Engine.

Implements Sentinel §7.1:
  - 1m and 5m Bar Aggregator driven by exchange timestamps
  - Tick-rule Delta estimation (aggressor classification)
  - Cumulative Volume Delta (CVD) running session tracker
  - Session VWAP with ±1σ and ±2σ standard deviation bands
  - High-precision integer / float math with zero divide-by-zero risk
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import config
from sentinel.models import Bar

log = logging.getLogger(__name__)


class SessionVWAPTracker:
    """Tracks running intraday Volume Weighted Average Price (VWAP) and ±1σ, ±2σ bands."""

    def __init__(self) -> None:
        self.cum_vol: float = 0.0
        self.cum_pv: float = 0.0
        self.cum_p2v: float = 0.0  # Sum of P^2 * V for one-pass variance calculation

    def update(self, price: float, volume: float) -> tuple[float, float, float, float, float]:
        """Update session stats with a new bar/tick.

        Returns (vwap, upper_1sigma, upper_2sigma, lower_1sigma, lower_2sigma).
        """
        if volume <= 0 or price <= 0:
            return self.get_bands(fallback_price=price)

        self.cum_vol += volume
        self.cum_pv += price * volume
        self.cum_p2v += (price ** 2) * volume

        return self.get_bands(fallback_price=price)

    def get_bands(self, fallback_price: float = 0.0) -> tuple[float, float, float, float, float]:
        if self.cum_vol <= 0:
            p = fallback_price
            return p, p, p, p, p

        vwap = self.cum_pv / self.cum_vol
        # Variance: E[P^2] - (E[P])^2
        variance = max(0.0, (self.cum_p2v / self.cum_vol) - (vwap ** 2))
        sigma = math.sqrt(variance)

        return (
            round(vwap, 2),
            round(vwap + sigma, 2),
            round(vwap + 2 * sigma, 2),
            round(vwap - sigma, 2),
            round(vwap - 2 * sigma, 2),
        )

    def reset(self) -> None:
        self.cum_vol = 0.0
        self.cum_pv = 0.0
        self.cum_p2v = 0.0


from collections import deque

class BarAggregator:
    """Aggregates raw ticks into completed 1-minute and 5-minute Bars with micro-burst detection."""

    def __init__(self, instrument_id: str, timeframe: str = "1m") -> None:
        self.instrument_id = instrument_id
        self.timeframe = timeframe
        self.vwap_tracker = SessionVWAPTracker()
        self.running_cvd: float = 0.0

        # Current in-progress bar state
        self.current_bar_start: datetime | None = None
        self.open_p: float = 0.0
        self.high_p: float = 0.0
        self.low_p: float = 0.0
        self.close_p: float = 0.0
        self.bar_volume: int = 0
        self.bar_buy_vol: float = 0.0
        self.bar_sell_vol: float = 0.0
        self.bar_depth_imbalances: list[float] = []
        self.last_tick_price: float | None = None
        self.last_tick_direction: int = 1  # 1 for buy, -1 for sell (tick rule default)
        self.bar_oi: int = 0
        self.prev_bar_oi: int = 0

        # Sub-minute rolling micro-burst detector (15-second window)
        self.rolling_ticks: deque = deque()
        self.last_micro_burst_ts: datetime | None = None

    def _get_bar_boundary(self, dt: datetime) -> datetime:
        """Align datetime down to minute boundary."""
        minute = dt.minute
        if self.timeframe == "5m":
            minute = (minute // 5) * 5
        return dt.replace(minute=minute, second=0, microsecond=0)

    def add_tick(
        self,
        price: float,
        timestamp: datetime,
        volume_delta: int = 1,
        oi: int = 0,
        depth_imbalance: float = 0.0,
    ) -> Bar | None:
        """Process an incoming tick with hybrid tick-rule and depth-imbalance weighting.

        Returns completed Bar when minute boundary rolls over, else None.
        """
        if price <= 0:
            return None

        # Determine tick direction via Lee-Ready tick rule
        if self.last_tick_price is not None:
            if price > self.last_tick_price:
                self.last_tick_direction = 1
            elif price < self.last_tick_price:
                self.last_tick_direction = -1
        self.last_tick_price = price

        vol = max(1, volume_delta)

        # Hybrid microstructure delta: blend Lee-Ready tick aggressor with order book depth imbalance
        base_buyer_weight = 0.70 if self.last_tick_direction > 0 else (0.30 if self.last_tick_direction < 0 else 0.50)
        depth_adj = max(-0.25, min(0.25, depth_imbalance * 0.25))
        buyer_pct = max(0.05, min(0.95, base_buyer_weight + depth_adj))

        buy_v = float(vol) * buyer_pct
        sell_v = float(vol) * (1.0 - buyer_pct)

        # Maintain 15-second rolling micro-burst window
        self.rolling_ticks.append((timestamp, price, vol, buy_v, sell_v, depth_imbalance))
        cutoff = timestamp - timedelta(seconds=15)
        while self.rolling_ticks and self.rolling_ticks[0][0] < cutoff:
            self.rolling_ticks.popleft()

        bar_start = self._get_bar_boundary(timestamp)
        completed_bar: Bar | None = None

        if self.current_bar_start is not None and bar_start > self.current_bar_start:
            # Bar rolled over -> finalize previous bar
            completed_bar = self._finalize_bar()
            self._start_new_bar(bar_start, price, vol, buy_v, sell_v, oi, depth_imbalance)
        elif self.current_bar_start is None:
            self._start_new_bar(bar_start, price, vol, buy_v, sell_v, oi, depth_imbalance)
        else:
            # Continue current bar
            self.high_p = max(self.high_p, price)
            self.low_p = min(self.low_p, price)
            self.close_p = price
            self.bar_volume += vol
            self.bar_buy_vol += buy_v
            self.bar_sell_vol += sell_v
            self.bar_depth_imbalances.append(depth_imbalance)
            if oi > 0:
                self.bar_oi = oi

        return completed_bar

    def check_micro_burst(self, now: datetime) -> dict | None:
        """Inspect rolling 15s window for high-velocity orderflow surges or absorption before bar close."""
        if len(self.rolling_ticks) < 4:
            return None

        # Cooldown: at least 15s between micro-burst alerts
        if self.last_micro_burst_ts and (now - self.last_micro_burst_ts).total_seconds() < 15.0:
            return None

        tot_vol = sum(t[2] for t in self.rolling_ticks)
        tot_buy = sum(t[3] for t in self.rolling_ticks)
        tot_sell = sum(t[4] for t in self.rolling_ticks)
        delta = tot_buy - tot_sell
        delta_ratio = delta / tot_vol if tot_vol > 0 else 0.0

        # Minimum threshold for micro-burst (e.g. at least 500 units and 35% delta bias)
        min_burst_vol = 500 if config.index_key(self.instrument_id) == "NIFTY" else 300
        if tot_vol >= min_burst_vol and abs(delta_ratio) >= 0.35:
            latest_depth = self.rolling_ticks[-1][5]
            recent_px = [t[1] for t in self.rolling_ticks]
            self.last_micro_burst_ts = now
            return {
                "instrument_id": self.instrument_id,
                "timestamp": now,
                "volume_15s": tot_vol,
                "delta_15s": delta,
                "delta_ratio_15s": round(delta_ratio, 3),
                "high_15s": max(recent_px),
                "low_15s": min(recent_px),
                "latest_price": recent_px[-1],
                "depth_imbalance": latest_depth,
            }
        return None

    def _start_new_bar(
        self,
        bar_start: datetime,
        price: float,
        volume: int,
        buy_v: float,
        sell_v: float,
        oi: int,
        depth_imbalance: float = 0.0,
    ) -> None:
        self.current_bar_start = bar_start
        self.open_p = price
        self.high_p = price
        self.low_p = price
        self.close_p = price
        self.bar_volume = volume
        self.bar_buy_vol = buy_v
        self.bar_sell_vol = sell_v
        self.bar_depth_imbalances = [depth_imbalance]
        self.prev_bar_oi = self.bar_oi if self.bar_oi > 0 else oi
        self.bar_oi = oi if oi > 0 else self.prev_bar_oi

    def _finalize_bar(self) -> Bar:
        typical_price = (self.high_p + self.low_p + self.close_p) / 3.0
        vwap, up1, up2, dn1, dn2 = self.vwap_tracker.update(typical_price, float(self.bar_volume))

        delta = self.bar_buy_vol - self.bar_sell_vol
        self.running_cvd += delta

        delta_ratio = delta / self.bar_volume if self.bar_volume > 0 else 0.0
        delta_oi = self.bar_oi - self.prev_bar_oi if self.prev_bar_oi > 0 else 0
        avg_depth = sum(self.bar_depth_imbalances) / max(1, len(self.bar_depth_imbalances)) if self.bar_depth_imbalances else 0.0

        bar = Bar(
            instrument_id=self.instrument_id,
            timeframe=self.timeframe,
            start_ts=self.current_bar_start or datetime.now(timezone.utc),
            open=round(self.open_p, 2),
            high=round(self.high_p, 2),
            low=round(self.low_p, 2),
            close=round(self.close_p, 2),
            volume_units=self.bar_volume,
            volume_lots=round(self.bar_volume / 65.0, 1),
            buy_vol_est=round(self.bar_buy_vol, 1),
            sell_vol_est=round(self.bar_sell_vol, 1),
            delta=round(delta, 1),
            delta_ratio=round(delta_ratio, 4),
            delta_quality=1.0,
            cvd=round(self.running_cvd, 1),
            vwap=vwap,
            vwap_sigma_1=up1,
            vwap_sigma_2=up2,
            vwap_lower_1=dn1,
            vwap_lower_2=dn2,
            oi=self.bar_oi,
            delta_oi=delta_oi,
            oi_fresh=True,
            incomplete=False,
            depth_imbalance=round(avg_depth, 4),
        )
        return bar

    def flush(self) -> Bar | None:
        """Force finalize current in-progress bar (e.g. at end of stream)."""
        if self.current_bar_start is None:
            return None
        bar = self._finalize_bar()
        self.current_bar_start = None
        return bar


def aggregate_bars(bars_1m: list[Bar], target_minutes: int = 5) -> list[Bar]:
    """Aggregate a sequence of 1-minute bars into N-minute bars (e.g. 3m, 5m, 15m).

    Groups consecutive 1m bars into interval buckets aligned to exchange boundaries.
    Preserves all microstructure properties (CVD, session VWAP bands, Delta, OI).
    """
    if not bars_1m or target_minutes <= 1:
        return bars_1m

    from collections import defaultdict

    sorted_bars = sorted(bars_1m, key=lambda b: b.start_ts)
    buckets: dict[datetime, list[Bar]] = defaultdict(list)
    tf_str = f"{target_minutes}m"

    for b in sorted_bars:
        dt = b.start_ts
        bucket_min = (dt.minute // target_minutes) * target_minutes
        bucket_ts = dt.replace(minute=bucket_min, second=0, microsecond=0)
        buckets[bucket_ts].append(b)

    bars_out: list[Bar] = []
    for bucket_ts, b_list in sorted(buckets.items(), key=lambda x: x[0]):
        if not b_list:
            continue
        first_b = b_list[0]
        last_b = b_list[-1]

        tot_vol = sum(b.volume_units for b in b_list)
        tot_delta = sum(b.delta for b in b_list)
        delta_ratio = round(tot_delta / tot_vol, 3) if tot_vol > 0 else 0.0
        avg_depth = sum(getattr(b, "depth_imbalance", 0.0) for b in b_list) / max(1, len(b_list))

        bars_out.append(Bar(
            instrument_id=first_b.instrument_id,
            timeframe=tf_str,
            start_ts=bucket_ts,
            open=first_b.open,
            high=max(b.high for b in b_list),
            low=min(b.low for b in b_list),
            close=last_b.close,
            volume_units=tot_vol,
            volume_lots=round(tot_vol / 65.0, 1),
            buy_vol_est=sum(b.buy_vol_est for b in b_list),
            sell_vol_est=sum(b.sell_vol_est for b in b_list),
            delta=tot_delta,
            delta_ratio=delta_ratio,
            delta_quality=min(b.delta_quality for b in b_list),
            cvd=last_b.cvd,
            vwap=last_b.vwap,
            vwap_sigma_1=last_b.vwap_sigma_1,
            vwap_sigma_2=last_b.vwap_sigma_2,
            vwap_lower_1=last_b.vwap_lower_1,
            vwap_lower_2=last_b.vwap_lower_2,
            oi=last_b.oi,
            delta_oi=sum(b.delta_oi for b in b_list),
            oi_fresh=last_b.oi_fresh,
            incomplete=False,
            depth_imbalance=round(avg_depth, 4),
        ))

    return bars_out


def aggregate_1m_to_5m(bars_1m: list[Bar]) -> list[Bar]:
    """Aggregate a sequence of 1-minute bars into 5-minute bars."""
    return aggregate_bars(bars_1m, target_minutes=5)
