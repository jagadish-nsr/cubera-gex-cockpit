"""commentary/detectors/base.py — Base Detector Interface and State."""

from __future__ import annotations

import abc
from datetime import datetime
from typing import Any

from commentary.models import Event, PointInTimeView, StoryState


class BaseDetector(abc.ABC):
    """Abstract base class for all market event detectors."""

    def __init__(self, name: str, cooldown_minutes: int = 15):
        self.name = name
        self.cooldown_minutes = cooldown_minutes
        self._last_fired_ts: datetime | None = None
        self._last_level: float | None = None

    def is_cooling_down(self, ts: datetime, level: float | None = None, min_distance_pts: float = 20.0) -> bool:
        """Check if detector is currently in cooldown or subject to hysteresis."""
        if self._last_fired_ts is None:
            return False
        delta_m = (ts - self._last_fired_ts).total_seconds() / 60.0
        if delta_m < self.cooldown_minutes:
            # If same level or within hysteresis window, enforce cooldown
            if level is not None and self._last_level is not None:
                if abs(level - self._last_level) < min_distance_pts:
                    return True
            return True
        return False

    def mark_fired(self, ts: datetime, level: float | None = None) -> None:
        """Record when the detector fired to enforce cooldown and prevent spam."""
        self._last_fired_ts = ts
        self._last_level = level

    @abc.abstractmethod
    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        """Evaluate point-in-time view and state, emitting structured events if detected."""
        pass
