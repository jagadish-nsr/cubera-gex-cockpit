"""commentary/detectors/sector_breadth.py — D15 Sector Breadth & Heavyweight Divergence Detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class SectorBreadthDetector(BaseDetector):
    """Detects Sector Leadership, Broad-based Participation, and Heavyweight Divergences."""

    def __init__(self):
        cfg = get_detector_config("sector_breadth") if "sector_breadth" in str(get_detector_config) else {}
        cooldown = cfg.get("cooldown_minutes", 30) if isinstance(cfg, dict) else 30
        super().__init__(name="sector_breadth", cooldown_minutes=cooldown)
        self._reported_leaders: set[str] = set()

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        extra = pit_view.extra_facts or {}
        sectors = extra.get("sectors") or {}

        # Sectors can be a dict with "rows" or a list of dicts
        rows = sectors.get("rows") if isinstance(sectors, dict) else (sectors if isinstance(sectors, list) else [])
        if not rows or len(rows) < 4:
            return events

        green_count = 0
        red_count = 0
        top_gainers: list[tuple[str, float]] = []
        top_losers: list[tuple[str, float]] = []

        for r in rows:
            name = str(r.get("name") or r.get("sector", "")).strip()
            chg = float(r.get("change_pct") or r.get("pct_change", 0.0))
            if chg > 0:
                green_count += 1
                top_gainers.append((name, chg))
            elif chg < 0:
                red_count += 1
                top_losers.append((name, chg))

        top_gainers.sort(key=lambda x: x[1], reverse=True)
        top_losers.sort(key=lambda x: x[1])

        # 1. Sector Leadership (e.g. Banking or Financials up strongly > +1.0%)
        if top_gainers and top_gainers[0][1] >= 1.0:
            lead_name, lead_pct = top_gainers[0]
            if lead_name not in self._reported_leaders and not self.is_cooling_down(pit_view.as_of):
                self._reported_leaders.add(lead_name)
                self.mark_fired(pit_view.as_of)
                laggard_str = f"{top_losers[0][0]} ({top_losers[0][1]:+.2f}%)" if top_losers else "None"
                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_SECTOR_LEAD",
                        ts=pit_view.as_of,
                        type="SECTOR_LEADERSHIP",
                        facts={
                            "leader": lead_name,
                            "leader_pct": round(lead_pct, 2),
                            "laggard": laggard_str,
                            "green_count": green_count,
                            "total_count": len(rows),
                            "spot": pit_view.spot,
                        },
                        magnitude_sigma=0.55,
                    )
                )

        # 2. Broad-Based Market Advance (> 75% sectors positive)
        elif len(rows) >= 8 and (green_count / len(rows)) >= 0.75:
            if not self.is_cooling_down(pit_view.as_of):
                self.mark_fired(pit_view.as_of)
                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_SECTOR_BROAD",
                        ts=pit_view.as_of,
                        type="BROAD_BASED_ADVANCE",
                        facts={
                            "green_count": green_count,
                            "total_count": len(rows),
                            "top_gainer": top_gainers[0][0] if top_gainers else "",
                            "top_gainer_pct": round(top_gainers[0][1], 2) if top_gainers else 0.0,
                            "spot": pit_view.spot,
                        },
                        magnitude_sigma=0.60,
                    )
                )

        return events
