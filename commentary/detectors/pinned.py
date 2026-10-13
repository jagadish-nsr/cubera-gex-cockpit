"""commentary/detectors/pinned.py — D11 Pinned Recommendations & Active Position Monitor."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


_PINNED_COMMENTARY_CACHE: dict[str, str] = {}


class PinnedPositionDetector(BaseDetector):
    """Monitors pinned options recommendations against live spot and premium targets."""

    def __init__(self):
        cfg = get_detector_config("pinned")
        cooldown = cfg.get("cooldown_minutes", 15)
        super().__init__(name="pinned_positions", cooldown_minutes=cooldown)
        self.target_approach_pct = cfg.get("target_approach_pct", 10.0)
        self.invalidation_band_pts = cfg.get("invalidation_band_pts", 15.0)
        self._target1_fired_recs: set[str] = set()
        self._target2_fired_recs: set[str] = set()
        self._inval_fired_recs: set[str] = set()

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        pinned_recs = pit_view.pinned_recommendations

        if not pinned_recs:
            return events

        spot = pit_view.spot
        as_of = pit_view.as_of

        for rec in pinned_recs:
            rec_id = rec.get("rec_id", "")
            if not rec_id:
                continue

            side = rec.get("side", "CE").upper()
            strike = float(rec.get("strike", 0.0))
            badge = rec.get("play_badge", "")
            t1_spot = float(rec.get("target_1_spot", 0.0))
            t2_spot = float(rec.get("target_2_spot", 0.0))
            inval_spot = float(rec.get("spot_invalidation", 0.0))
            init_spot = float(rec.get("initial_spot", spot))
            curr_score = int(rec.get("quality_score", 0))

            is_call = side == "CE"

            # 1. Target 1 Reached
            if t1_spot > 0 and rec_id not in self._target1_fired_recs:
                t1_hit = (spot >= t1_spot) if is_call else (spot <= t1_spot)
                if t1_hit:
                    self._target1_fired_recs.add(rec_id)
                    _PINNED_COMMENTARY_CACHE[rec_id] = f"Target 1 achieved (+{round(abs(spot - init_spot), 1)} pts). Trailing stop to breakeven recommended."
                    events.append(
                        Event(
                            id=f"evt_{int(as_of.timestamp())}_{rec_id}_T1_HIT",
                            ts=as_of,
                            type="PINNED_TARGET_HIT",
                            facts={
                                "rec_id": rec_id,
                                "side": side,
                                "strike": strike,
                                "play_badge": badge,
                                "target_hit": 1,
                                "target_spot": t1_spot,
                                "current_spot": round(spot, 1),
                                "gain_pts": round(abs(spot - init_spot), 1),
                                "advice": "Target 1 achieved. Trailing stop to breakeven or locking partial profit recommended.",
                            },
                            magnitude_sigma=0.9,
                        )
                    )
                    continue

            # 2. Target 1 Approached (within 10% distance)
            if t1_spot > 0 and rec_id not in self._target1_fired_recs:
                dist_to_t1 = abs(t1_spot - spot)
                total_target_span = max(10.0, abs(t1_spot - init_spot))
                if dist_to_t1 <= (total_target_span * 0.20):  # within 20% of goal
                    if not self.is_cooling_down(as_of, level=strike):
                        self.mark_fired(as_of, level=strike)
                        _PINNED_COMMENTARY_CACHE[rec_id] = f"Approaching Target 1 (within {round(dist_to_t1, 1)} pts). Monitor momentum."
                        events.append(
                            Event(
                                id=f"evt_{int(as_of.timestamp())}_{rec_id}_T1_APPROACH",
                                ts=as_of,
                                type="PINNED_TARGET_APPROACH",
                                facts={
                                    "rec_id": rec_id,
                                    "side": side,
                                    "strike": strike,
                                    "play_badge": badge,
                                    "target_spot": t1_spot,
                                    "current_spot": round(spot, 1),
                                    "dist_to_target": round(dist_to_t1, 1),
                                },
                                magnitude_sigma=0.6,
                            )
                        )

            # 3. Invalidation Wall Warning
            if inval_spot > 0 and rec_id not in self._inval_fired_recs:
                dist_inval = abs(spot - inval_spot)
                is_threatening = (spot <= inval_spot + self.invalidation_band_pts) if is_call else (spot >= inval_spot - self.invalidation_band_pts)
                if is_threatening:
                    self._inval_fired_recs.add(rec_id)
                    _PINNED_COMMENTARY_CACHE[rec_id] = f"Warning: Spot within {round(dist_inval, 1)} pts of invalidation wall (₹{inval_spot:,.0f})."
                    events.append(
                        Event(
                            id=f"evt_{int(as_of.timestamp())}_{rec_id}_INVAL_WARNING",
                            ts=as_of,
                            type="PINNED_INVALIDATION_WARNING",
                            facts={
                                "rec_id": rec_id,
                                "side": side,
                                "strike": strike,
                                "play_badge": badge,
                                "invalidation_spot": inval_spot,
                                "current_spot": round(spot, 1),
                                "dist_pts": round(dist_inval, 1),
                                "warning": "Spot is testing the structural invalidation wall; thesis under immediate pressure.",
                            },
                            magnitude_sigma=0.85,
                        )
                    )

        return events
