"""sentinel/engine/context.py — Market Structure & GEX Context Engine.

Implements Sentinel §7.4:
  - Tracks Opening Range Box (ORB, first 15 minutes 09:15–09:30 IST)
  - Tracks Session High / Low
  - Evaluates proximity to GEX Call Wall, Put Wall, Gamma Flip, and Magnet
  - Generates context tags for signal scoring & invalidation levels
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

from sentinel.models import Bar

log = logging.getLogger(__name__)


def gex_proximity_radii(symbol: str | None) -> tuple[float, float]:
    """Return (wall_or_magnet_pts, flip_pts) for the index.

    Nifty 50 / Bank Nifty 100 / Sensex 150. Sensex is wider than its 100-pt
    strike step so a wall ~120 pts away still tags. Never substring-match NIFTY
    inside BANKNIFTY — uses config.index_key.
    """
    try:
        import config
        key = config.index_key(symbol)
    except Exception:
        key = "NIFTY"
    if key == "SENSEX":
        wall = 150.0
    elif key == "BANKNIFTY":
        wall = 100.0
    else:
        wall = 50.0
    return wall, wall * 0.8


@dataclass
class MarketContext:
    orb_high: float | None = None
    orb_low: float | None = None
    orb_complete: bool = False
    day_high: float = 0.0
    day_low: float = float("inf")
    call_wall: float | None = None
    put_wall: float | None = None
    gamma_flip: float | None = None
    magnet: float | None = None
    symbol: str | None = None
    # Path-shape gate for continuation (Dreiss chop ≥ 61.8 → boxed tape).
    chop_high: bool = False
    chop_value: float | None = None

    def update_bar(self, bar: Bar) -> None:
        """Update context with new bar."""
        if bar.high > self.day_high:
            self.day_high = bar.high
        if bar.low < self.day_low:
            self.day_low = bar.low

        # Check ORB window (09:15 to 09:30 IST)
        ts = bar.start_ts
        if ts.tzinfo is not None:
            ist = ts.astimezone(timezone(timedelta(hours=5, minutes=30)))
        else:
            ist = ts

        time_val = ist.hour * 100 + ist.minute
        if 915 <= time_val < 930:
            self.orb_high = max(self.orb_high or bar.high, bar.high)
            self.orb_low = min(self.orb_low or bar.low, bar.low)
        elif time_val >= 930 and not self.orb_complete:
            self.orb_complete = True
            log.info("MarketContext: ORB finalized [Low: %.2f, High: %.2f]", self.orb_low or 0, self.orb_high or 0)

    def set_gex_levels(self, levels: Any = None, **kwargs: Any) -> None:
        """Set current GEX reference levels from an object, mapping, or kwargs."""
        if levels is not None:
            if isinstance(levels, dict):
                getter = levels.get
                self.call_wall = getter("call_wall", self.call_wall)
                self.put_wall = getter("put_wall", self.put_wall)
                self.gamma_flip = getter("flip", getter("gamma_flip", self.gamma_flip))
                self.magnet = getter("magnet", self.magnet)
                if getter("symbol"):
                    self.symbol = getter("symbol")
            else:
                cw = getattr(levels, "call_wall", None)
                pw = getattr(levels, "put_wall", None)
                flip = getattr(levels, "flip", None)
                if flip is None:
                    flip = getattr(levels, "gamma_flip", None)
                mag = getattr(levels, "magnet", None)
                if cw is not None:
                    self.call_wall = cw
                if pw is not None:
                    self.put_wall = pw
                if flip is not None:
                    self.gamma_flip = flip
                if mag is not None:
                    self.magnet = mag
        if "call_wall" in kwargs and kwargs["call_wall"] is not None:
            self.call_wall = kwargs["call_wall"]
        if "put_wall" in kwargs and kwargs["put_wall"] is not None:
            self.put_wall = kwargs["put_wall"]
        if "flip" in kwargs and kwargs["flip"] is not None:
            self.gamma_flip = kwargs["flip"]
        if "gamma_flip" in kwargs and kwargs["gamma_flip"] is not None:
            self.gamma_flip = kwargs["gamma_flip"]
        if "magnet" in kwargs and kwargs["magnet"] is not None:
            self.magnet = kwargs["magnet"]
        if kwargs.get("symbol"):
            self.symbol = kwargs["symbol"]

    def refresh_chop(self, bars_1m: list[Any] | None) -> None:
        """Update chop_high from recent 1m bars. Fail soft if meter is not ready."""
        try:
            from analytics.choppiness import CHOP_HIGH, compute_choppiness_meter

            meter = compute_choppiness_meter(bars_1m)
            chop = meter.get("chop")
            self.chop_value = None if chop is None else float(chop)
            self.chop_high = self.chop_value is not None and self.chop_value >= float(CHOP_HIGH)
        except Exception:
            log.debug("MarketContext.refresh_chop failed", exc_info=True)

    def get_context_tags(self, bar: Bar) -> list[str]:
        """Generate descriptive context tags for a completed bar."""
        tags: list[str] = []
        p = bar.close
        sym = self.symbol or bar.instrument_id
        wall_r, flip_r = gex_proximity_radii(sym)

        # VWAP positioning
        if bar.vwap > 0:
            if p >= bar.vwap:
                tags.append("ABOVE_VWAP")
                if p >= bar.vwap_sigma_2:
                    tags.append("ABOVE_VWAP_2SIGMA")
                elif p >= bar.vwap_sigma_1:
                    tags.append("ABOVE_VWAP_1SIGMA")
            else:
                tags.append("BELOW_VWAP")
                if p <= bar.vwap_lower_2:
                    tags.append("BELOW_VWAP_2SIGMA")
                elif p <= bar.vwap_lower_1:
                    tags.append("BELOW_VWAP_1SIGMA")

        # ORB positioning
        if self.orb_complete and self.orb_high and self.orb_low:
            if p > self.orb_high:
                tags.append("ABOVE_ORB_HIGH")
            elif p < self.orb_low:
                tags.append("BELOW_ORB_LOW")
            else:
                tags.append("INSIDE_ORB")

        # GEX proximity — index-scaled radii
        if self.call_wall and abs(p - self.call_wall) <= wall_r:
            tags.append("NEAR_CALL_WALL")
        if self.put_wall and abs(p - self.put_wall) <= wall_r:
            tags.append("NEAR_PUT_WALL")
        if self.gamma_flip and abs(p - self.gamma_flip) <= flip_r:
            tags.append("NEAR_GAMMA_FLIP")
        if self.magnet and abs(p - self.magnet) <= wall_r:
            tags.append("NEAR_MAGNET")

        if self.chop_high:
            tags.append("CHOPPY")

        # Day Extremes
        if abs(p - self.day_high) <= 15.0:
            tags.append("AT_DAY_HIGH")
        if abs(p - self.day_low) <= 15.0:
            tags.append("AT_DAY_LOW")

        return tags
