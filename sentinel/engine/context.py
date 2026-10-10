"""sentinel/engine/context.py — Market Structure & GEX Context Engine.

Implements Sentinel §7.4:
  - Tracks Opening Range Box (ORB, first 15 minutes 09:15–09:30 IST)
  - Tracks Session High / Low
  - Evaluates proximity to GEX Call Wall, Put Wall, and Gamma Flip
  - Generates context tags for signal scoring & invalidation levels
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from sentinel.models import Bar

log = logging.getLogger(__name__)


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

    def set_gex_levels(self, levels: Any) -> None:
        """Set current GEX reference levels."""
        if levels is None:
            return
        self.call_wall = getattr(levels, "call_wall", None)
        self.put_wall = getattr(levels, "put_wall", None)
        self.gamma_flip = getattr(levels, "flip", None)
        self.magnet = getattr(levels, "magnet", None)

    def get_context_tags(self, bar: Bar) -> list[str]:
        """Generate descriptive context tags for a completed bar."""
        tags: list[str] = []
        p = bar.close

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

        # GEX Wall Proximity (within 50 pts)
        if self.call_wall and abs(p - self.call_wall) <= 50.0:
            tags.append("NEAR_CALL_WALL")
        if self.put_wall and abs(p - self.put_wall) <= 50.0:
            tags.append("NEAR_PUT_WALL")
        if self.gamma_flip and abs(p - self.gamma_flip) <= 40.0:
            tags.append("NEAR_GAMMA_FLIP")

        # Day Extremes
        if abs(p - self.day_high) <= 15.0:
            tags.append("AT_DAY_HIGH")
        if abs(p - self.day_low) <= 15.0:
            tags.append("AT_DAY_LOW")

        return tags
