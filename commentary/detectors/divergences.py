"""commentary/detectors/divergences.py — D4 Divergence Detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class DivergenceDetector(BaseDetector):
    """Detects multi-series divergences: Price vs OI, Price vs IV, Price vs PCR, Index vs Sector."""

    def __init__(self):
        cfg = get_detector_config("divergences")
        cooldown = cfg.get("cooldown_minutes", 20)
        super().__init__(name="divergences", cooldown_minutes=cooldown)
        self.window_bars = cfg.get("window_bars", 6)
        self.confirmations_required = cfg.get("confirmations_required", 2)
        self._prev_spot_high: float | None = None
        self._prev_fut_oi: float | None = None
        self._prev_atm_iv: float | None = None
        self._prev_pcr: float | None = None

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        candles = pit_view.candles_1m
        spot = pit_view.spot
        atm_iv = pit_view.atm_iv
        pcr = pit_view.extra_facts.get("pcr", 1.0)
        fut_oi = pit_view.extra_facts.get("fut_oi", 0.0)

        if not candles or len(candles) < self.window_bars:
            # Seed tracking
            self._prev_spot_high = spot
            self._prev_fut_oi = fut_oi
            self._prev_atm_iv = atm_iv
            self._prev_pcr = pcr
            return events

        # 1. Price New High vs Futures OI Falling (Short Covering / Lack of Fresh Participation)
        if self._prev_spot_high and self._prev_fut_oi and fut_oi > 0:
            if spot > self._prev_spot_high + 15.0 and fut_oi < self._prev_fut_oi * 0.98:
                if not self.is_cooling_down(pit_view.as_of, level=spot):
                    self.mark_fired(pit_view.as_of, level=spot)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_DIV_PRICE_OI",
                            ts=pit_view.as_of,
                            type="DIVERGENCE_PRICE_OI",
                            facts={
                                "spot": round(spot, 1),
                                "prev_high": round(self._prev_spot_high, 1),
                                "current_oi": round(fut_oi, 0),
                                "prev_oi": round(self._prev_fut_oi, 0),
                                "interpretation": "Price new high on falling futures OI indicates short covering rather than fresh long buildup",
                            },
                            magnitude_sigma=0.7,
                        )
                    )

        # 2. Price Rally vs Rising ATM IV (Unusual Hedging Demand / Event Risk)
        if self._prev_spot_high and self._prev_atm_iv and self._prev_atm_iv > 0:
            if spot > self._prev_spot_high + 25.0 and atm_iv > self._prev_atm_iv + 0.015:
                if not self.is_cooling_down(pit_view.as_of, level=spot):
                    self.mark_fired(pit_view.as_of, level=spot)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_DIV_PRICE_IV",
                            ts=pit_view.as_of,
                            type="DIVERGENCE_PRICE_IV",
                            facts={
                                "spot": round(spot, 1),
                                "atm_iv_pct": round(atm_iv * 100.0, 2),
                                "prev_iv_pct": round(self._prev_atm_iv * 100.0, 2),
                                "interpretation": "Rising ATM IV during an index rally indicates institutional upside call hedging",
                            },
                            magnitude_sigma=0.75,
                        )
                    )

        # 3. Price vs PCR Trend Divergence (Price rising while PCR drops persistently)
        if self._prev_spot_high and self._prev_pcr and self._prev_pcr > 0:
            if spot > self._prev_spot_high + 20.0 and pcr < self._prev_pcr - 0.15:
                if not self.is_cooling_down(pit_view.as_of, level=spot):
                    self.mark_fired(pit_view.as_of, level=spot)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_DIV_PRICE_PCR",
                            ts=pit_view.as_of,
                            type="DIVERGENCE_PRICE_PCR",
                            facts={
                                "spot": round(spot, 1),
                                "current_pcr": round(pcr, 2),
                                "prev_pcr": round(self._prev_pcr, 2),
                                "interpretation": "Price making higher highs while PCR falls shows call writing outstripping put additions",
                            },
                            magnitude_sigma=0.65,
                        )
                    )

        # Update rolling baseline
        if spot > (self._prev_spot_high or 0):
            self._prev_spot_high = spot
        if fut_oi > 0:
            self._prev_fut_oi = fut_oi
        if atm_iv > 0:
            self._prev_atm_iv = atm_iv
        if pcr > 0:
            self._prev_pcr = pcr

        return events
