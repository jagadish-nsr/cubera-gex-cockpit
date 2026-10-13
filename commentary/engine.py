"""commentary/engine.py — Real-time Session Commentary Engine Orchestrator."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from commentary.models import (
    PointInTimeView,
    CommentaryEntry,
    SuggestionBite,
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
from commentary.storage.db import save_commentary_entry, get_recent_commentary_entries

logger = logging.getLogger(__name__)
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class SessionCommentaryEngine:
    """Live commentary coordinator that evaluates market ticks, detects events, and streams entries."""

    def __init__(self, symbol: str = "NIFTY") -> None:
        self.symbol = symbol.upper()
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
        self.bites_engine = BiteEngine(session_count=0, is_calibrated=False)

        self._active_bites: list[SuggestionBite] = []
        self._last_eval_ts: dt.datetime | None = None

    def evaluate_live(
        self,
        symbol: str,
        data: Any,
        candles_1m: list[dict[str, Any]] | None = None,
        force_publish_top: bool = False,
    ) -> list[CommentaryEntry]:
        """Evaluate current live dashboard data and publish new commentary entries."""
        if data is None or not getattr(data, "levels", None):
            return []

        now_ist = dt.datetime.now(IST)

        # 1. Extract true market levels
        levels = data.levels
        spot = float(getattr(levels, "spot", 22600.0))
        prev_close = float(getattr(levels, "prev_close", 0) or spot)
        atm_iv = float(getattr(levels, "atm_iv", 0) or 0.14)
        call_wall = float(getattr(levels, "call_wall", spot + 100))
        put_wall = float(getattr(levels, "put_wall", spot - 100))
        flip = float(getattr(levels, "flip", spot))
        max_pain = float(getattr(levels, "max_pain", spot))
        net_gex_sign = int(getattr(levels, "net_gex_sign", 1 if spot >= flip else -1))

        # Get pinned recommendations
        pinned_records = []
        try:
            from data.journal.recommendations_store import list_bookmarked_records
            pinned_records = list_bookmarked_records(symbol)
        except Exception:
            pass

        # Build strict PointInTimeView
        pit_view = PointInTimeView(
            as_of=now_ist,
            symbol=symbol.upper(),
            spot=spot,
            prior_close=prev_close,
            atm_iv=atm_iv,
            expected_move_1d=float(getattr(levels, "expected_move", 100.0) or 100.0),
            call_wall=call_wall,
            put_wall=put_wall,
            gamma_flip=flip,
            max_pain=max_pain,
            net_gex_sign=net_gex_sign,
            candles_1m=candles_1m or [],
            pinned_recommendations=pinned_records,
        )

        # 2. Update phase
        self.story_mgr.update_phase(now_ist)
        state = self.story_mgr.state

        # 3. Run detectors
        events = []
        events.extend(self.gap_det.detect(pit_view, state))
        events.extend(self.walls_det.detect(pit_view, state))
        events.extend(self.struct_det.detect(pit_view, state))
        events.extend(self.div_det.detect(pit_view, state))
        events.extend(self.imp_det.detect(pit_view, state))
        events.extend(self.vol_det.detect(pit_view, state))
        events.extend(self.exp_det.detect(pit_view, state))
        events.extend(self.foot_det.detect(pit_view, state))
        events.extend(self.news_det.detect(pit_view, state))
        events.extend(self.pin_det.detect(pit_view, state))

        if not events:
            return []

        # 4. Score and merge
        scored_raw = [self.scorer.score_event(ev, state) for ev in events]
        scored = self.scorer.merge_confluent_events(scored_raw)
        publishable = [s for s in scored if self.scorer.should_publish(s)[0]]

        if not publishable and force_publish_top and scored:
            publishable = [max(scored, key=lambda s: s.significance)]

        new_entries: list[CommentaryEntry] = []
        if publishable:
            top_scored = max(publishable, key=lambda s: s.significance)
            top_event = top_scored.event
            self.story_mgr.update_thesis(top_event)

            headline, body = self.narrator.narrate(top_event)
            entry_id = f"entry-{int(now_ist.timestamp())}-{top_event.type.lower()}"

            entry = CommentaryEntry(
                id=entry_id,
                ts=now_ist,
                phase=state.phase,
                event_ids=[s.event.id for s in publishable],
                headline=headline,
                body=body,
                verified=True,
                tags=[top_event.type] + list(top_event.quality_flags),
                facts={
                    "spot": spot,
                    "call_wall": call_wall,
                    "put_wall": put_wall,
                    "gamma_flip": flip,
                    "expected_move": float(getattr(levels, "expected_move", 100.0) or 100.0),
                    "event_type": top_event.type,
                    "event_facts": top_event.facts,
                },
            )

            # Generate Bites
            bites = self.bites_engine.generate_bites(pit_view, publishable)
            entry.bites.extend(bites)

            # Persist
            save_commentary_entry(entry, symbol=symbol)
            self.story_mgr.record_entry(entry)
            new_entries.append(entry)

        return new_entries


_ENGINES: dict[str, SessionCommentaryEngine] = {}


def get_session_commentary_engine(symbol: str = "NIFTY") -> SessionCommentaryEngine:
    """Return persistent singleton commentary engine for the given symbol."""
    sym = symbol.upper()
    if sym not in _ENGINES:
        _ENGINES[sym] = SessionCommentaryEngine(sym)
    return _ENGINES[sym]
