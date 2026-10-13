"""commentary/detectors/impulse.py — D5 Impulsive Move & Velocity Detector."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class ImpulsiveMoveDetector(BaseDetector):
    """Detects rapid displacement and volume expansion beyond intraday sigma thresholds."""

    def __init__(self):
        cfg = get_detector_config("impulse")
        cooldown = cfg.get("cooldown_minutes", 15)
        super().__init__(name="impulsive_move", cooldown_minutes=cooldown)
        self.speed_sigma = cfg.get("speed_sigma", 1.0)
        self.vol_burst_mult = cfg.get("vol_burst_mult", 2.2)

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        candles = pit_view.candles_1m

        # Need at least 10 bars to measure impulse and baseline volume
        if not candles or len(candles) < 10:
            return events

        # Look at last 5 bars vs prior 15 bars
        recent_bars = candles[-5:]
        prior_bars = candles[-20:-5] if len(candles) >= 20 else candles[:-5]

        if not prior_bars:
            return events

        # Calculate 5-minute displacement
        start_p = float(recent_bars[0].get("open", pit_view.spot))
        end_p = float(recent_bars[-1].get("close", pit_view.spot))
        move_pts = end_p - start_p

        # Expected 5-minute move: sigma_5m = sigma_1d * sqrt(5 / 375)
        sigma_5m = max(5.0, pit_view.sigma_1d * math.sqrt(5.0 / 375.0))
        move_sigma = move_pts / sigma_5m

        # Volume burst ratio
        avg_prior_vol = max(1.0, sum(float(b.get("volume", 0)) for b in prior_bars) / len(prior_bars))
        recent_vol = max(1.0, sum(float(b.get("volume", 0)) for b in recent_bars) / len(recent_bars))
        vol_ratio = recent_vol / avg_prior_vol

        if abs(move_sigma) >= self.speed_sigma and vol_ratio >= self.vol_burst_mult:
            if not self.is_cooling_down(pit_view.as_of, level=end_p):
                self.mark_fired(pit_view.as_of, level=end_p)
                direction = "BULLISH" if move_pts > 0 else "BEARISH"

                # Check if near GEX wall (absorption signature)
                cw = pit_view.call_wall
                pw = pit_view.put_wall
                is_at_wall = (move_pts > 0 and abs(end_p - cw) < 15.0) or (move_pts < 0 and abs(end_p - pw) < 15.0)
                classification = "ABSORPTION" if is_at_wall else "FOLLOW_THROUGH"

                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_IMPULSE_{direction}",
                        ts=pit_view.as_of,
                        type="IMPULSIVE_MOVE",
                        facts={
                            "direction": direction,
                            "move_pts": round(move_pts, 1),
                            "move_sigma": round(move_sigma, 2),
                            "volume_ratio": round(vol_ratio, 2),
                            "window_minutes": 5,
                            "classification": classification,
                            "end_spot": round(end_p, 1),
                        },
                        magnitude_sigma=abs(move_sigma),
                    )
                )

        return events
