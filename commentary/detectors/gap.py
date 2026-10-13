"""commentary/detectors/gap.py — D1 Opening Gap & Gap Fill Detector."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class OpeningGapDetector(BaseDetector):
    """Detects opening gap size, sigma magnitude, and relation to major GEX walls."""

    def __init__(self):
        cfg = get_detector_config("gap")
        cooldown = cfg.get("cooldown_minutes", 30)
        super().__init__(name="opening_gap", cooldown_minutes=cooldown)
        self.min_gap_sigma = cfg.get("min_gap_sigma", 0.35)
        self._initial_gap_fired = False
        self._open_price: float | None = None
        self._prior_close: float | None = None
        self._initial_gap_pts: float = 0.0

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []

        if pit_view.prior_close <= 0:
            return events

        # Extract opening price from candles if available, else current spot
        candles = pit_view.candles_1m
        if candles:
            open_p = float(candles[0].get("open", pit_view.spot))
        else:
            open_p = pit_view.spot

        if not self._initial_gap_fired:
            gap_pts = open_p - pit_view.prior_close
            gap_pct = (gap_pts / pit_view.prior_close) * 100.0
            sigma_1d = pit_view.sigma_1d
            gap_sigma = gap_pts / sigma_1d if sigma_1d > 0 else 0.0

            if abs(gap_sigma) >= self.min_gap_sigma:
                self._open_price = open_p
                self._prior_close = pit_view.prior_close
                self._initial_gap_pts = gap_pts
                self._initial_gap_fired = True
                self.mark_fired(pit_view.as_of, level=open_p)

                evt_type = "GAP_UP" if gap_pts > 0 else "GAP_DOWN"
                nearest_wall = pit_view.call_wall if gap_pts > 0 else pit_view.put_wall
                dist_wall = abs(open_p - nearest_wall)

                facts = {
                    "open": round(open_p, 1),
                    "prior_close": round(pit_view.prior_close, 1),
                    "gap_pts": round(gap_pts, 1),
                    "gap_pct": round(gap_pct, 2),
                    "gap_sigma": round(gap_sigma, 2),
                    "nearest_wall": round(nearest_wall, 1),
                    "dist_to_wall": round(dist_wall, 1),
                    "call_wall": round(pit_view.call_wall, 1),
                    "put_wall": round(pit_view.put_wall, 1),
                }

                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_{evt_type}",
                        ts=pit_view.as_of,
                        type=evt_type,
                        facts=facts,
                        magnitude_sigma=abs(gap_sigma),
                        quality_flags=[],
                    )
                )
            else:
                # Below minimum gap threshold
                self._initial_gap_fired = True
                return events

        # Follow-up: Gap fill evaluation if gap exists and session is underway
        elif self._open_price and abs(self._initial_gap_pts) > 5.0 and not self.is_cooling_down(pit_view.as_of):
            # Check how much of initial gap is retraced
            # If gap was +100, and current spot is open - 50, retrace = 50%
            current_spot = pit_view.spot
            retrace_pts = self._open_price - current_spot if self._initial_gap_pts > 0 else current_spot - self._open_price
            retrace_pct = (retrace_pts / abs(self._initial_gap_pts)) * 100.0

            if retrace_pct >= 90.0:
                self.mark_fired(pit_view.as_of, level=current_spot)
                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_GAP_FILLED",
                        ts=pit_view.as_of,
                        type="GAP_FILLED",
                        facts={
                            "open": round(self._open_price, 1),
                            "prior_close": round(self._prior_close or 0.0, 1),
                            "current_spot": round(current_spot, 1),
                            "retrace_pct": round(retrace_pct, 1),
                        },
                        magnitude_sigma=0.5,
                    )
                )

        return events
