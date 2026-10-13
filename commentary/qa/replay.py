"""commentary/qa/replay.py — Point-in-Time Replay Harness for Deterministic Backtesting."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from commentary.models import (
    PointInTimeView,
    PointInTimeViolation,
    CommentaryEntry,
    SuggestionBite,
    StoryState,
)
from commentary.detectors.gap import OpeningGapDetector
from commentary.detectors.walls import WallGexShiftDetector
from commentary.detectors.session_structure import SessionStructureDetector
from commentary.detectors.divergences import DivergenceDetector
from commentary.detectors.impulse import ImpulsiveMoveDetector
from commentary.detectors.volatility import VolatilityRegimeDetector
from commentary.detectors.expiry import ExpiryDynamicsDetector
from commentary.detectors.footprint import InstitutionalFootprintDetector
from commentary.detectors.news import NewsCatalystDetector
from commentary.detectors.pinned import PinnedPositionDetector
from commentary.scoring import SignificanceScorer
from commentary.story_state import StoryStateManager
from commentary.narrator.client import NarratorClient
from commentary.bites.engine import BiteEngine
from commentary.bites.lifecycle import BiteLifecycleEvaluator

logger = logging.getLogger(__name__)


class ReplayHarness:
    """Simulates live market commentary and suggestion bites across historical snapshots."""

    def __init__(self, session_count: int = 25, is_calibrated: bool = True) -> None:
        self.gap_det = OpeningGapDetector()
        self.walls_det = WallGexShiftDetector()
        self.struct_det = SessionStructureDetector()
        self.div_det = DivergenceDetector()
        self.imp_det = ImpulsiveMoveDetector()
        self.vol_det = VolatilityRegimeDetector()
        self.exp_det = ExpiryDynamicsDetector()
        self.foot_det = InstitutionalFootprintDetector()
        self.news_det = NewsCatalystDetector()
        self.pin_det = PinnedPositionDetector()

        self.scorer = SignificanceScorer()
        self.story_mgr = StoryStateManager()
        self.narrator = NarratorClient()
        self.bites_engine = BiteEngine(session_count=session_count, is_calibrated=is_calibrated)

        self.active_bites: list[SuggestionBite] = []
        self.completed_bites: list[SuggestionBite] = []
        self.entries: list[CommentaryEntry] = []

    def replay_sequence(
        self,
        snapshots: list[PointInTimeView],
    ) -> dict[str, Any]:
        """Run full pipeline over time-ordered snapshots."""
        prev_ts: dt.datetime | None = None

        for view in snapshots:
            # Enforce strict chronological point-in-time sequencing
            if prev_ts is not None and view.as_of < prev_ts:
                raise PointInTimeViolation(
                    f"Replay sequencing violation: snapshot ts {view.as_of} is earlier than previous {prev_ts}"
                )
            prev_ts = view.as_of

            self.story_mgr.update_phase(view.as_of)
            state = self.story_mgr.state

            # 1. Run all detectors
            events = []
            events.extend(self.gap_det.detect(view, state))
            events.extend(self.walls_det.detect(view, state))
            events.extend(self.struct_det.detect(view, state))
            events.extend(self.div_det.detect(view, state))
            events.extend(self.imp_det.detect(view, state))
            events.extend(self.vol_det.detect(view, state))
            events.extend(self.exp_det.detect(view, state))
            events.extend(self.foot_det.detect(view, state))
            events.extend(self.news_det.detect(view, state))
            events.extend(self.pin_det.detect(view, state))

            # 2. Score events & check significance
            scored_raw = [self.scorer.score_event(ev, state) for ev in events]
            scored = self.scorer.merge_confluent_events(scored_raw)
            publishable = [s for s in scored if self.scorer.should_publish(s)[0]]

            # 3. Generate commentary entry if publishable events exist
            current_entry: CommentaryEntry | None = None
            if publishable:
                top_scored = max(publishable, key=lambda s: s.significance)
                top_event = top_scored.event
                self.story_mgr.update_thesis(top_event)

                headline, body = self.narrator.narrate(top_event)
                current_entry = CommentaryEntry(
                    id=f"entry-{len(self.entries) + 1}",
                    ts=view.as_of,
                    phase=self.story_mgr.state.phase,
                    event_ids=[s.event.id for s in publishable],
                    headline=headline,
                    body=body,
                    verified=True,
                )
                self.story_mgr.record_entry(current_entry)
                self.entries.append(current_entry)

            # 4. Generate Suggestion Bites
            new_bites = self.bites_engine.generate_bites(view, scored)
            if current_entry and new_bites:
                current_entry.bites.extend(new_bites)
            self.active_bites.extend(new_bites)

            # 5. Evaluate active bites lifecycle
            candle = view.candles_1m[-1] if view.candles_1m else {"close": view.spot}
            still_active: list[SuggestionBite] = []
            for b in self.active_bites:
                updated_b = BiteLifecycleEvaluator.evaluate(b, candle, view.spot, view.as_of)
                if updated_b.state in ("TARGET_HIT", "STOPPED", "INVALIDATED", "EXPIRED"):
                    self.completed_bites.append(updated_b)
                else:
                    still_active.append(updated_b)
            self.active_bites = still_active

        return {
            "entries_count": len(self.entries),
            "completed_bites_count": len(self.completed_bites),
            "active_bites_count": len(self.active_bites),
            "completed_bites": self.completed_bites,
            "entries": self.entries,
        }
