"""commentary/detectors/secondary_walls.py — D12 Secondary GEX Walls & Strike Cluster Detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class SecondaryWallDetector(BaseDetector):
    """Detects tests of Secondary GEX Walls (Call Wall #2, Put Wall #2) and strike-level OI buildup."""

    def __init__(self):
        cfg = get_detector_config("secondary_walls") if "secondary_walls" in str(get_detector_config) else {}
        cooldown = cfg.get("cooldown_minutes", 15) if isinstance(cfg, dict) else 15
        super().__init__(name="secondary_walls", cooldown_minutes=cooldown)
        self._prev_cw2: float | None = None
        self._prev_pw2: float | None = None
        self._tested_strikes: set[float] = set()

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        spot = pit_view.spot
        extra = pit_view.extra_facts or {}

        # 1. Extract secondary walls
        cw2 = float(extra.get("call_wall_2") or 0.0)
        pw2 = float(extra.get("put_wall_2") or 0.0)

        # Fallback: extract from oi_strikes if available
        oi_strikes = extra.get("oi_strikes") or []
        if (cw2 <= 0 or pw2 <= 0) and oi_strikes and isinstance(oi_strikes, list):
            try:
                calls_sorted = sorted(oi_strikes, key=lambda x: float(x.get("call_oi", 0.0)), reverse=True)
                puts_sorted = sorted(oi_strikes, key=lambda x: float(x.get("put_oi", 0.0)), reverse=True)
                if len(calls_sorted) >= 2 and cw2 <= 0:
                    cw2 = float(calls_sorted[1].get("strike", 0.0))
                if len(puts_sorted) >= 2 and pw2 <= 0:
                    pw2 = float(puts_sorted[1].get("strike", 0.0))
            except Exception:
                pass

        # 2. Check Secondary Call Wall Test
        if cw2 > 0 and cw2 != pit_view.call_wall:
            dist_cw2 = cw2 - spot
            if abs(dist_cw2) <= 15.0 and cw2 not in self._tested_strikes:
                if not self.is_cooling_down(pit_view.as_of, level=cw2):
                    self._tested_strikes.add(cw2)
                    self.mark_fired(pit_view.as_of, level=cw2)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_CW2_TEST",
                            ts=pit_view.as_of,
                            type="SECONDARY_WALL_TEST",
                            facts={
                                "wall_type": "SECONDARY_CALL_WALL",
                                "strike": cw2,
                                "spot": spot,
                                "dist_pts": round(abs(dist_cw2), 1),
                                "primary_wall": pit_view.call_wall,
                            },
                            magnitude_sigma=0.55,
                        )
                    )

        # 3. Check Secondary Put Wall Test
        if pw2 > 0 and pw2 != pit_view.put_wall:
            dist_pw2 = spot - pw2
            if abs(dist_pw2) <= 15.0 and pw2 not in self._tested_strikes:
                if not self.is_cooling_down(pit_view.as_of, level=pw2):
                    self._tested_strikes.add(pw2)
                    self.mark_fired(pit_view.as_of, level=pw2)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_PW2_TEST",
                            ts=pit_view.as_of,
                            type="SECONDARY_WALL_TEST",
                            facts={
                                "wall_type": "SECONDARY_PUT_WALL",
                                "strike": pw2,
                                "spot": spot,
                                "dist_pts": round(abs(dist_pw2), 1),
                                "primary_wall": pit_view.put_wall,
                            },
                            magnitude_sigma=0.55,
                        )
                    )

        # 4. Check Strike OI Buildup near spot
        top_buildup = extra.get("top_strike_buildup")
        if top_buildup and isinstance(top_buildup, dict):
            k = float(top_buildup.get("strike", 0.0))
            side = top_buildup.get("side", "CALL")
            doi = float(top_buildup.get("doi", 0.0))
            if doi > 100000 and abs(spot - k) < pit_view.sigma_1d:
                if not self.is_cooling_down(pit_view.as_of, level=k):
                    self.mark_fired(pit_view.as_of, level=k)
                    events.append(
                        Event(
                            id=f"evt_{int(pit_view.as_of.timestamp())}_STRIKE_OI_{side}",
                            ts=pit_view.as_of,
                            type="STRIKE_OI_SURGE",
                            facts={
                                "strike": k,
                                "side": side,
                                "doi": doi,
                                "spot": spot,
                            },
                            magnitude_sigma=0.60,
                        )
                    )

        self._prev_cw2 = cw2
        self._prev_pw2 = pw2
        return events
