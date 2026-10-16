"""commentary/detectors/pcr_flow.py — D13 Put-Call Ratio (PCR) Flow & Dynamics Detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class PcrFlowDetector(BaseDetector):
    """Detects Put-Call Ratio (PCR) surges, contractions, and sentiment extremes."""

    def __init__(self):
        cfg = get_detector_config("pcr_flow") if "pcr_flow" in str(get_detector_config) else {}
        cooldown = cfg.get("cooldown_minutes", 20) if isinstance(cfg, dict) else 20
        super().__init__(name="pcr_flow", cooldown_minutes=cooldown)
        self._prev_pcr: float | None = None

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        extra = pit_view.extra_facts or {}
        pcr = float(extra.get("pcr") or extra.get("pcr_oi") or 0.0)

        # Fallback: compute from oi_strikes
        if pcr <= 0.0 and "oi_strikes" in extra:
            oi_strikes = extra.get("oi_strikes") or []
            if isinstance(oi_strikes, list) and oi_strikes:
                tot_call = sum(float(x.get("call_oi", 0.0)) for x in oi_strikes)
                tot_put = sum(float(x.get("put_oi", 0.0)) for x in oi_strikes)
                if tot_call > 0:
                    pcr = tot_put / tot_call

        if pcr <= 0.0:
            return events

        if self._prev_pcr is not None:
            delta_pcr = pcr - self._prev_pcr

            # 1. PCR Bullish Surge (Aggressive Put Writing support)
            if delta_pcr >= 0.10 or (self._prev_pcr < 1.15 <= pcr) or (self._prev_pcr < 1.30 <= pcr):
                if not self.is_cooling_down(pit_view.as_of):
                    self.mark_fired(pit_view.as_of)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_PCR_SURGE",
                            ts=pit_view.as_of,
                            type="PCR_SHIFT",
                            facts={
                                "direction": "BULLISH",
                                "old_pcr": round(self._prev_pcr, 3),
                                "new_pcr": round(pcr, 3),
                                "delta_pcr": round(delta_pcr, 3),
                                "spot": pit_view.spot,
                            },
                            magnitude_sigma=0.60,
                        )
                    )

            # 2. PCR Bearish Slump (Call Writing dominance or Put Unwinding)
            elif delta_pcr <= -0.10 or (self._prev_pcr > 0.90 >= pcr) or (self._prev_pcr > 0.75 >= pcr):
                if not self.is_cooling_down(pit_view.as_of):
                    self.mark_fired(pit_view.as_of)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_PCR_SLUMP",
                            ts=pit_view.as_of,
                            type="PCR_SHIFT",
                            facts={
                                "direction": "BEARISH",
                                "old_pcr": round(self._prev_pcr, 3),
                                "new_pcr": round(pcr, 3),
                                "delta_pcr": round(delta_pcr, 3),
                                "spot": pit_view.spot,
                            },
                            magnitude_sigma=0.60,
                        )
                    )

            # 3. PCR Extreme Skew (> 1.45 or < 0.65)
            elif pcr >= 1.45 and self._prev_pcr < 1.45:
                if not self.is_cooling_down(pit_view.as_of):
                    self.mark_fired(pit_view.as_of)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_PCR_EXTREME_BULL",
                            ts=pit_view.as_of,
                            type="PCR_SHIFT",
                            facts={
                                "direction": "EXTREME_OVERBOUGHT",
                                "pcr": round(pcr, 3),
                                "spot": pit_view.spot,
                            },
                            magnitude_sigma=0.65,
                        )
                    )

        self._prev_pcr = pcr
        return events
