"""commentary/detectors/walls.py — D2 OI Wall & GEX Shifts Detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class WallGexShiftDetector(BaseDetector):
    """Detects Wall tests, Wall breaks, Wall migrations, and Gamma Flip crossings."""

    def __init__(self):
        cfg = get_detector_config("walls")
        cooldown = cfg.get("cooldown_minutes", 15)
        super().__init__(name="walls_and_gex", cooldown_minutes=cooldown)
        self.wall_test_sigma_band = cfg.get("wall_test_sigma_band", 0.10)
        self._prev_call_wall: float | None = None
        self._prev_put_wall: float | None = None
        self._prev_flip: float | None = None
        self._prev_gex_sign: int | None = None
        self._tested_walls: set[float] = set()
        self._broken_walls: set[float] = set()

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        spot = pit_view.spot
        cw = pit_view.call_wall
        pw = pit_view.put_wall
        flip = pit_view.gamma_flip
        gex_sign = pit_view.net_gex_sign
        sigma_1d = pit_view.sigma_1d
        test_band_pts = max(10.0, sigma_1d * self.wall_test_sigma_band)

        # 1. Wall Migration Check
        if self._prev_call_wall is not None and cw > 0 and cw != self._prev_call_wall:
            events.append(
                Event(
                    id=f"evt_{int(pit_view.as_of.timestamp())}_CW_MIGRATION",
                    ts=pit_view.as_of,
                    type="WALL_MIGRATION",
                    facts={
                        "wall_type": "CALL_WALL",
                        "old_strike": self._prev_call_wall,
                        "new_strike": cw,
                        "spot": spot,
                    },
                    magnitude_sigma=0.7,
                )
            )
        if self._prev_put_wall is not None and pw > 0 and pw != self._prev_put_wall:
            events.append(
                Event(
                    id=f"evt_{int(pit_view.as_of.timestamp())}_PW_MIGRATION",
                    ts=pit_view.as_of,
                    type="WALL_MIGRATION",
                    facts={
                        "wall_type": "PUT_WALL",
                        "old_strike": self._prev_put_wall,
                        "new_strike": pw,
                        "spot": spot,
                    },
                    magnitude_sigma=0.7,
                )
            )

        # 2. Gamma Flip Crossing Check
        if self._prev_flip is not None and flip > 0:
            if (self._prev_flip <= spot and flip > spot) or (self._prev_flip >= spot and flip < spot):
                if not self.is_cooling_down(pit_view.as_of, level=flip):
                    self.mark_fired(pit_view.as_of, level=flip)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_FLIP_CROSS",
                            ts=pit_view.as_of,
                            type="FLIP_LEVEL_CROSS",
                            facts={
                                "spot": spot,
                                "flip_level": flip,
                                "regime": "POSITIVE_GAMMA" if gex_sign > 0 else "NEGATIVE_GAMMA",
                            },
                            magnitude_sigma=0.8,
                        )
                    )

        # 3. GEX Regime Shift Check
        if self._prev_gex_sign is not None and gex_sign != self._prev_gex_sign and gex_sign != 0:
            events.append(
                Event(
                    id=f"evt_{int(pit_view.as_of.timestamp())}_GEX_REGIME_CHANGE",
                    ts=pit_view.as_of,
                    type="REGIME_CHANGE",
                    facts={
                        "spot": spot,
                        "old_regime": "POSITIVE_GAMMA" if self._prev_gex_sign > 0 else "NEGATIVE_GAMMA",
                        "new_regime": "POSITIVE_GAMMA" if gex_sign > 0 else "NEGATIVE_GAMMA",
                        "flip_level": flip,
                    },
                    magnitude_sigma=0.85,
                )
            )

        # 4. Wall Test & Wall Break Checks
        # Call Wall Test / Break
        if cw > 0:
            dist_cw = cw - spot
            if dist_cw < -5.0:  # Spot closed above Call Wall
                if cw not in self._broken_walls and not self.is_cooling_down(pit_view.as_of, level=cw):
                    self._broken_walls.add(cw)
                    self.mark_fired(pit_view.as_of, level=cw)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_CW_BREAK",
                            ts=pit_view.as_of,
                            type="WALL_BREAK",
                            facts={"wall_type": "CALL_WALL", "strike": cw, "spot": spot, "overshoot_pts": round(abs(dist_cw), 1)},
                            magnitude_sigma=1.1,
                        )
                    )
            else:
                if dist_cw > 15.0:
                    self._broken_walls.discard(cw)

                if abs(dist_cw) <= test_band_pts and cw not in self._tested_walls:
                    if not self.is_cooling_down(pit_view.as_of, level=cw):
                        self._tested_walls.add(cw)
                        self.mark_fired(pit_view.as_of, level=cw)
                        events.append(
                            Event(
                                id=f"evt_{int(pit_view.as_of.timestamp())}_CW_TEST",
                                ts=pit_view.as_of,
                                type="WALL_TEST",
                                facts={"wall_type": "CALL_WALL", "strike": cw, "spot": spot, "dist_pts": round(abs(dist_cw), 1)},
                                magnitude_sigma=0.6,
                            )
                        )

        # Put Wall Test / Break
        if pw > 0:
            dist_pw = spot - pw
            if dist_pw < -5.0:  # Spot closed below Put Wall
                if pw not in self._broken_walls and not self.is_cooling_down(pit_view.as_of, level=pw):
                    self._broken_walls.add(pw)
                    self.mark_fired(pit_view.as_of, level=pw)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_PW_BREAK",
                            ts=pit_view.as_of,
                            type="WALL_BREAK",
                            facts={"wall_type": "PUT_WALL", "strike": pw, "spot": spot, "undershoot_pts": round(abs(dist_pw), 1)},
                            magnitude_sigma=1.1,
                        )
                    )
            else:
                if dist_pw > 15.0:
                    self._broken_walls.discard(pw)

                if abs(dist_pw) <= test_band_pts and pw not in self._tested_walls:
                    if not self.is_cooling_down(pit_view.as_of, level=pw):
                        self._tested_walls.add(pw)
                        self.mark_fired(pit_view.as_of, level=pw)
                        events.append(
                            Event(
                                id=f"evt_{int(pit_view.as_of.timestamp())}_PW_TEST",
                                ts=pit_view.as_of,
                                type="WALL_TEST",
                                facts={"wall_type": "PUT_WALL", "strike": pw, "spot": spot, "dist_pts": round(abs(dist_pw), 1)},
                                magnitude_sigma=0.6,
                            )
                        )

        # Update cache tracking
        self._prev_call_wall = cw
        self._prev_put_wall = pw
        self._prev_flip = flip
        self._prev_gex_sign = gex_sign

        return events
