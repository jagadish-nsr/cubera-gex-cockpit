"""commentary/detectors/footprint.py — D3 Institutional Footprint & Microstructure Detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class InstitutionalFootprintDetector(BaseDetector):
    """Detects institutional-style aggressive volume, 4-state futures buildup, and orderbook depth absorption."""

    def __init__(self):
        cfg = get_detector_config("institutional_footprint")
        cooldown = cfg.get("cooldown_minutes", 20)
        super().__init__(name="institutional_footprint", cooldown_minutes=cooldown)
        self.vol_spike_ratio = cfg.get("vol_spike_ratio", 2.0)
        self.depth_imbalance_ratio = cfg.get("depth_imbalance_ratio", 2.5)
        self._last_sig_fired: datetime | None = None
        self._last_buildup_fired: datetime | None = None

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        signals = pit_view.sentinel_signals

        # 1. Evaluate Sentinel Microstructure Signals (Absorption & Exhaustion)
        if signals:
            recent_sig = signals[-1]
            sig_type = recent_sig.get("signal_type", "")
            sig_score = float(recent_sig.get("score", 0.0))

            if sig_score >= 70.0 and ("ABSORPTION" in sig_type or "CONTINUATION" in sig_type or "EXHAUSTION" in sig_type):
                delta_sig_m = (pit_view.as_of - self._last_sig_fired).total_seconds() / 60.0 if self._last_sig_fired else 999.0
                if delta_sig_m >= self.cooldown_minutes:
                    self._last_sig_fired = pit_view.as_of
                    # Institutional footprint label mapping
                    if "ABSORPTION_BULL" in sig_type:
                        fp_label = "institutional-style passive absorption at support"
                    elif "ABSORPTION_BEAR" in sig_type:
                        fp_label = "institutional-style passive absorption at resistance"
                    elif "CONTINUATION_BULL" in sig_type:
                        fp_label = "institutional-style aggressive long drive"
                    elif "CONTINUATION_BEAR" in sig_type:
                        fp_label = "institutional-style aggressive short drive"
                    else:
                        fp_label = "institutional-style volume exhaustion"

                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_INST_FOOTPRINT_{sig_type}",
                            ts=pit_view.as_of,
                            type="INSTITUTIONAL_FOOTPRINT",
                            facts={
                                "footprint_label": fp_label,
                                "sentinel_signal": sig_type,
                                "score": sig_score,
                                "spot": round(pit_view.spot, 1),
                                "rvol": round(float(recent_sig.get("rvol", 1.5)), 2),
                                "delta_ratio": round(float(recent_sig.get("delta_ratio", 0.0)), 2),
                                "evidence": [
                                    f"Sentinel OrderFlow Score: {sig_score:.0f}",
                                    f"Volume burst ratio: {float(recent_sig.get('rvol', 1.5)):.1f}x",
                                    f"Aggressive tape delta: {float(recent_sig.get('delta_ratio', 0.0)):+.1%}",
                                ],
                            },
                            magnitude_sigma=sig_score / 100.0,
                        )
                    )

        # 2. Futures 4-State Matrix Buildup Check from extra_facts
        buildup_state = pit_view.extra_facts.get("futures_buildup_state")
        oi_change_pct = float(pit_view.extra_facts.get("futures_oi_change_pct", 0.0))

        if buildup_state and abs(oi_change_pct) >= 4.0:
            delta_b_m = (pit_view.as_of - self._last_buildup_fired).total_seconds() / 60.0 if self._last_buildup_fired else 999.0
            if delta_b_m >= self.cooldown_minutes:
                self._last_buildup_fired = pit_view.as_of
                if buildup_state == "LONG_BUILDUP":
                    label = "institutional-style long buildup"
                elif buildup_state == "SHORT_BUILDUP":
                    label = "institutional-style short buildup"
                elif buildup_state == "SHORT_COVERING":
                    label = "institutional-style short covering"
                else:
                    label = "institutional-style long unwinding"

                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_FUT_BUILDUP_{buildup_state}",
                        ts=pit_view.as_of,
                        type="INSTITUTIONAL_BUILDUP",
                        facts={
                            "footprint_label": label,
                            "buildup_state": buildup_state,
                            "oi_change_pct": round(oi_change_pct, 2),
                            "spot": round(pit_view.spot, 1),
                            "evidence": [f"Futures open interest shifted by {oi_change_pct:+.1f}% alongside price displacement"],
                        },
                        magnitude_sigma=0.75,
                    )
                )

        return events
