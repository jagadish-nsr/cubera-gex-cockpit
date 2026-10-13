"""commentary/detectors/session_structure.py — D9 Session Structure & Range Breakout Detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class SessionStructureDetector(BaseDetector):
    """Detects Opening Range Breakouts (ORB 15m), VWAP crosses, and Day High/Low tests."""

    def __init__(self):
        cfg = get_detector_config("session_structure")
        cooldown = cfg.get("cooldown_minutes", 15)
        super().__init__(name="session_structure", cooldown_minutes=cooldown)
        self.orb_window_minutes = cfg.get("orb_window_minutes", 15)
        self._orb_high: float | None = None
        self._orb_low: float | None = None
        self._orb_established = False
        self._orb_broken_up = False
        self._orb_broken_down = False
        self._prev_vwap_side: int | None = None  # +1 above VWAP, -1 below VWAP

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        candles = pit_view.candles_1m
        spot = pit_view.spot

        if not candles:
            return events

        # 1. Establish Opening 15-min Range (ORB)
        if not self._orb_established:
            if len(candles) >= self.orb_window_minutes:
                first_15 = candles[:self.orb_window_minutes]
                self._orb_high = max(float(c.get("high", spot)) for c in first_15)
                self._orb_low = min(float(c.get("low", spot)) for c in first_15)
                self._orb_established = True
                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_ORB_ESTABLISHED",
                        ts=pit_view.as_of,
                        type="ORB_ESTABLISHED",
                        facts={
                            "orb_high": round(self._orb_high, 1),
                            "orb_low": round(self._orb_low, 1),
                            "orb_range_pts": round(self._orb_high - self._orb_low, 1),
                            "spot": round(spot, 1),
                        },
                        magnitude_sigma=0.5,
                    )
                )

        # 2. Check ORB Breakouts
        if self._orb_established and self._orb_high and self._orb_low:
            if spot > self._orb_high and not self._orb_broken_up:
                self._orb_broken_up = True
                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_ORB_BREAK_UP",
                        ts=pit_view.as_of,
                        type="ORB_BREAK_UP",
                        facts={
                            "orb_high": round(self._orb_high, 1),
                            "spot": round(spot, 1),
                            "extension_pts": round(spot - self._orb_high, 1),
                        },
                        magnitude_sigma=0.75,
                    )
                )
            elif spot < self._orb_low and not self._orb_broken_down:
                self._orb_broken_down = True
                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_ORB_BREAK_DOWN",
                        ts=pit_view.as_of,
                        type="ORB_BREAK_DOWN",
                        facts={
                            "orb_low": round(self._orb_low, 1),
                            "spot": round(spot, 1),
                            "extension_pts": round(self._orb_low - spot, 1),
                        },
                        magnitude_sigma=0.75,
                    )
                )

        # 3. Check Session VWAP Crosses if VWAP is in candles/facts
        session_vwap = pit_view.extra_facts.get("session_vwap")
        if session_vwap and session_vwap > 0:
            curr_side = 1 if spot >= session_vwap else -1
            if self._prev_vwap_side is not None and curr_side != self._prev_vwap_side:
                if not self.is_cooling_down(pit_view.as_of, level=session_vwap):
                    self.mark_fired(pit_view.as_of, level=session_vwap)
                    cross_type = "VWAP_RECLAIM" if curr_side > 0 else "VWAP_LOSS"
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_{cross_type}",
                            ts=pit_view.as_of,
                            type=cross_type,
                            facts={
                                "spot": round(spot, 1),
                                "vwap": round(session_vwap, 1),
                                "cross_direction": "ABOVE" if curr_side > 0 else "BELOW",
                            },
                            magnitude_sigma=0.65,
                        )
                    )
            self._prev_vwap_side = curr_side

        return events
