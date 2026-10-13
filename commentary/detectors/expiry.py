"""commentary/detectors/expiry.py — D8 Expiry Dynamics & Pinning Detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class ExpiryDynamicsDetector(BaseDetector):
    """Detects Max Pain pinning, 0DTE gamma concentration, and expiry afternoon unwinding."""

    def __init__(self):
        cfg = get_detector_config("expiry")
        cooldown = cfg.get("cooldown_minutes", 15)
        super().__init__(name="expiry_dynamics", cooldown_minutes=cooldown)
        self.max_pain_pin_sigma = cfg.get("max_pain_pin_sigma", 0.15)
        self._max_pain_pinned_fired = False

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        dte = float(pit_view.extra_facts.get("dte", 3.0))

        # Only active on near-expiry or 0DTE sessions (DTE < 1.2 days)
        if dte > 1.2:
            return events

        spot = pit_view.spot
        max_pain = pit_view.max_pain
        sigma_1d = pit_view.sigma_1d

        # 1. Max Pain Pinning Behavior Check
        if max_pain > 0 and sigma_1d > 0:
            dist_pain = abs(spot - max_pain)
            pain_sigma = dist_pain / sigma_1d

            if pain_sigma <= self.max_pain_pin_sigma and not self._max_pain_pinned_fired:
                if not self.is_cooling_down(pit_view.as_of):
                    self._max_pain_pinned_fired = True
                    self.mark_fired(pit_view.as_of, level=max_pain)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_MAX_PAIN_PIN",
                            ts=pit_view.as_of,
                            type="MAX_PAIN_PINNING",
                            facts={
                                "spot": round(spot, 1),
                                "max_pain": round(max_pain, 1),
                                "dist_pts": round(dist_pain, 1),
                                "dte": round(dte, 2),
                                "caution": "High dealer gamma pinning; premium decay accelerating into strike",
                            },
                            magnitude_sigma=0.7,
                            quality_flags=["EXPIRY_DAY_HIGH_GAMMA"],
                        )
                    )

        # 2. Expiry Afternoon Gamma Blast / Unwind Window (after 14:30 IST)
        now_dt = pit_view.as_of
        if now_dt.hour == 14 and now_dt.minute >= 45:
            # Check if spot is escaping the pin or breaking out of walls
            cw = pit_view.call_wall
            pw = pit_view.put_wall
            is_escaping = (spot > cw) or (spot < pw)
            if is_escaping and not self.is_cooling_down(pit_view.as_of, level=spot):
                self.mark_fired(pit_view.as_of, level=spot)
                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_EXPIRY_GAMMA_UNWIND",
                        ts=pit_view.as_of,
                        type="EXPIRY_GAMMA_UNWIND",
                        facts={
                            "spot": round(spot, 1),
                            "call_wall": round(cw, 1),
                            "put_wall": round(pw, 1),
                            "time": now_dt.strftime("%H:%M"),
                            "caution": "Closing settlement gamma squeeze in progress",
                        },
                        magnitude_sigma=1.1,
                        quality_flags=["EXPIRY_DAY_HIGH_GAMMA"],
                    )
                )

        return events
