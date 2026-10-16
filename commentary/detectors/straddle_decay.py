"""commentary/detectors/straddle_decay.py — D16 Straddle Decay & Midday Range Squeeze Detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class StraddleDecayDetector(BaseDetector):
    """Detects option premium decay pace and midday range squeezes."""

    def __init__(self):
        cfg = get_detector_config("straddle_decay") if "straddle_decay" in str(get_detector_config) else {}
        cooldown = cfg.get("cooldown_minutes", 30) if isinstance(cfg, dict) else 30
        super().__init__(name="straddle_decay", cooldown_minutes=cooldown)
        self._reported_squeeze = False

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        candles = pit_view.candles_1m
        spot = pit_view.spot
        extra = pit_view.extra_facts or {}

        # 1. Range Squeeze during Midday Phase (11:30 - 13:30)
        if state.phase in ("midday", "morning") and candles and len(candles) >= 30 and not self._reported_squeeze:
            last_30 = candles[-30:]
            h = max(float(c.get("high", spot)) for c in last_30)
            l = min(float(c.get("low", spot)) for c in last_30)
            range_pts = h - l
            # If 30-minute range is less than 35 pts (compression)
            if range_pts <= 35.0:
                if not self.is_cooling_down(pit_view.as_of):
                    self._reported_squeeze = True
                    self.mark_fired(pit_view.as_of)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_RANGE_SQUEEZE",
                            ts=pit_view.as_of,
                            type="RANGE_COMPRESSION",
                            facts={
                                "range_pts": round(range_pts, 1),
                                "high": round(h, 1),
                                "low": round(l, 1),
                                "spot": round(spot, 1),
                                "window_minutes": 30,
                            },
                            magnitude_sigma=0.55,
                        )
                    )

        # 2. ATM Straddle Decay Pace
        straddle_info = extra.get("straddle_decay")
        if straddle_info and isinstance(straddle_info, dict):
            pts_decayed = float(straddle_info.get("decay_pts", 0.0))
            curr_straddle = float(straddle_info.get("current_straddle", 0.0))
            pct = float(straddle_info.get("decay_pct", 0.0))
            if pts_decayed >= 15.0 or pct >= 10.0:
                if not self.is_cooling_down(pit_view.as_of):
                    self.mark_fired(pit_view.as_of)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_STRADDLE_DECAY",
                            ts=pit_view.as_of,
                            type="ATM_STRADDLE_DECAY",
                            facts={
                                "current_straddle": round(curr_straddle, 1),
                                "decay_pts": round(pts_decayed, 1),
                                "decay_pct": round(pct, 1),
                                "spot": round(spot, 1),
                            },
                            magnitude_sigma=0.50,
                        )
                    )

        return events
