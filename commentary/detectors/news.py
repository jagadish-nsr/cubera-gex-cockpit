"""commentary/detectors/news.py — D6 News-Linked Catalyst Detector."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState


class NewsCatalystDetector(BaseDetector):
    """Correlates high-impact news releases with concurrent market impulses and volatility shifts."""

    def __init__(self):
        cfg = get_detector_config("news")
        super().__init__(name="news_catalyst", cooldown_minutes=15)
        self.match_window_before_min = cfg.get("match_window_before_min", 10)
        self.match_window_after_min = cfg.get("match_window_after_min", 3)
        self.relevance_threshold = cfg.get("relevance_threshold", 4)
        self._matched_headline_ids: set[str] = set()

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []
        news_items = pit_view.news_items

        if not news_items:
            return events

        as_of = pit_view.as_of

        for item in news_items:
            item_id = item.get("id") or item.get("headline", "")
            if item_id in self._matched_headline_ids:
                continue

            pub_ts = item.get("published_ts")
            if not isinstance(pub_ts, datetime):
                continue

            # Ensure tz-aware comparison
            if pub_ts.tzinfo is None and as_of.tzinfo is not None:
                pub_ts = pub_ts.replace(tzinfo=as_of.tzinfo)

            # Check if published within [-10m, +3m] window of as_of
            delta_sec = (as_of - pub_ts).total_seconds()
            is_in_window = (-self.match_window_after_min * 60.0) <= delta_sec <= (self.match_window_before_min * 60.0)

            score = int(item.get("impact_score") or item.get("score") or 3)

            if is_in_window and score >= self.relevance_threshold:
                self._matched_headline_ids.add(item_id)
                events.append(
                    Event(
                        id=f"evt_{int(as_of.timestamp())}_NEWS_CATALYST",
                        ts=as_of,
                        type="NEWS_CATALYST",
                        facts={
                            "headline": item.get("headline", ""),
                            "source": item.get("source", "RSS"),
                            "published_ts": pub_ts.strftime("%H:%M IST"),
                            "impact_score": score,
                            "spot": round(pit_view.spot, 1),
                            "phrasing": "possible catalyst coinciding with session price action",
                        },
                        magnitude_sigma=0.8,
                    )
                )

        return events
