"""commentary/detectors/volatility.py — D7 Volatility Regime Detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class VolatilityRegimeDetector(BaseDetector):
    """Detects ATM IV spikes, IV crushes, and India VIX intraday surges."""

    def __init__(self):
        cfg = get_detector_config("volatility")
        cooldown = cfg.get("cooldown_minutes", 20)
        super().__init__(name="volatility", cooldown_minutes=cooldown)
        self.iv_spike_pct = cfg.get("iv_spike_pct", 15.0)
        self.vix_surge_pct = cfg.get("vix_surge_pct", 5.0)
        self._initial_atm_iv: float | None = None
        self._prev_vix: float | None = None
        self._last_iv_fired: datetime | None = None
        self._last_vix_fired: datetime | None = None

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        atm_iv = pit_view.atm_iv
        india_vix = float(pit_view.extra_facts.get("india_vix") or 0.0)

        if atm_iv <= 0:
            return events

        # Initialize session baseline
        if self._initial_atm_iv is None:
            self._initial_atm_iv = atm_iv
            self._prev_vix = india_vix
            return events

        # 1. ATM IV Spike / Crush relative to baseline
        iv_shift_pct = ((atm_iv - self._initial_atm_iv) / self._initial_atm_iv) * 100.0

        if abs(iv_shift_pct) >= self.iv_spike_pct:
            delta_iv_m = (pit_view.as_of - self._last_iv_fired).total_seconds() / 60.0 if self._last_iv_fired else 999.0
            if delta_iv_m >= self.cooldown_minutes:
                self._last_iv_fired = pit_view.as_of
                shift_type = "IV_SPIKE" if iv_shift_pct > 0 else "IV_CRUSH"
                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_{shift_type}",
                        ts=pit_view.as_of,
                        type=shift_type,
                        facts={
                            "current_atm_iv": round(atm_iv * 100.0, 2),
                            "baseline_iv": round(self._initial_atm_iv * 100.0, 2),
                            "shift_pct": round(iv_shift_pct, 2),
                            "spot": round(pit_view.spot, 1),
                        },
                        magnitude_sigma=abs(iv_shift_pct) / 20.0,
                    )
                )

        # 2. India VIX Intraday Surge
        if self._prev_vix and self._prev_vix > 0 and india_vix > 0:
            vix_change_pct = ((india_vix - self._prev_vix) / self._prev_vix) * 100.0
            if abs(vix_change_pct) >= self.vix_surge_pct:
                delta_vix_m = (pit_view.as_of - self._last_vix_fired).total_seconds() / 60.0 if self._last_vix_fired else 999.0
                if delta_vix_m >= self.cooldown_minutes:
                    self._last_vix_fired = pit_view.as_of
                    vix_type = "VIX_SURGE" if vix_change_pct > 0 else "VIX_DROP"
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_{vix_type}",
                            ts=pit_view.as_of,
                            type=vix_type,
                            facts={
                                "current_vix": round(india_vix, 2),
                                "prev_vix": round(self._prev_vix, 2),
                                "change_pct": round(vix_change_pct, 2),
                            },
                            magnitude_sigma=abs(vix_change_pct) / 10.0,
                        )
                    )
            self._prev_vix = india_vix

        return events
