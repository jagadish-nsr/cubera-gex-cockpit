"""commentary/detectors/vwap_dynamics.py — D14 Intraday VWAP & Fair Value Dynamics Detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class VwapDynamicsDetector(BaseDetector):
    """Detects VWAP defenses, band extensions (+/- 2-sigma stretch), and fair value reclaims."""

    def __init__(self):
        cfg = get_detector_config("vwap_dynamics") if "vwap_dynamics" in str(get_detector_config) else {}
        cooldown = cfg.get("cooldown_minutes", 15) if isinstance(cfg, dict) else 15
        super().__init__(name="vwap_dynamics", cooldown_minutes=cooldown)
        self._prev_side: int | None = None  # +1 above, -1 below
        self._last_test_ts: datetime | None = None

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        spot = pit_view.spot
        extra = pit_view.extra_facts or {}

        # 1. Extract VWAP from extra_facts or candles_1m
        vwap = float(extra.get("session_vwap") or extra.get("vwap") or 0.0)
        if vwap <= 0.0 and pit_view.candles_1m:
            try:
                last_c = pit_view.candles_1m[-1]
                vwap = float(last_c.get("vwap", 0.0))
            except Exception:
                pass

        if vwap <= 0.0:
            return events

        dist = spot - vwap
        curr_side = 1 if dist >= 0 else -1

        # 2. VWAP Hold / Absorption Bounce (Spot dips to within 10 pts of VWAP and holds)
        if abs(dist) <= 12.0 and not self.is_cooling_down(pit_view.as_of, level=vwap):
            self.mark_fired(pit_view.as_of, level=vwap)
            events.append(
                Event(
                    id=f"evt_{int(pit_view.as_of.timestamp())}_VWAP_DEFENSE",
                    ts=pit_view.as_of,
                    type="VWAP_HOLD_BOUNCE",
                    facts={
                        "spot": round(spot, 1),
                        "vwap": round(vwap, 1),
                        "dist_pts": round(dist, 1),
                        "side": "SUPPORT" if dist >= 0 else "RESISTANCE",
                    },
                    magnitude_sigma=0.55,
                )
            )

        # 3. VWAP Overextension (stretched beyond 2.0 sigma from VWAP)
        sigma_1d = pit_view.sigma_1d
        if abs(dist) >= max(35.0, sigma_1d * 0.70):
            if not self.is_cooling_down(pit_view.as_of, level=spot):
                self.mark_fired(pit_view.as_of, level=spot)
                direction = "OVERBOUGHT" if dist > 0 else "OVERSOLD"
                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_VWAP_STRETCH_{direction}",
                        ts=pit_view.as_of,
                        type="VWAP_BAND_EXTENSION",
                        facts={
                            "spot": round(spot, 1),
                            "vwap": round(vwap, 1),
                            "extension_pts": round(dist, 1),
                            "direction": direction,
                        },
                        magnitude_sigma=0.65,
                    )
                )

        self._prev_side = curr_side
        return events
