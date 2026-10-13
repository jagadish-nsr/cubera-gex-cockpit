"""tests/test_commentary_phase4.py — Unit tests for Phase 4: Scoring, Confluence & Story State."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from commentary.models import Event, StoryState
from commentary.scoring import SignificanceScorer
from commentary.story_state import StoryStateManager

IST = timezone(timedelta(hours=5, minutes=30))


def test_significance_scorer_and_pacing():
    scorer = SignificanceScorer()
    state = StoryState(phase="morning")
    t0 = datetime(2026, 10, 13, 9, 45, tzinfo=IST)

    # 1. High magnitude event (1.5 sigma) -> High score
    e_high = Event(
        id="evt_1",
        ts=t0,
        type="WALL_BREAK",
        facts={"strike": 25000.0, "spot": 25020.0},
        magnitude_sigma=1.5,
    )
    scored = scorer.score_event(e_high, state)
    assert scored.significance >= 0.65

    pub, reason = scorer.should_publish(scored)
    assert pub is True
    assert "Passed publication gate" in reason

    # 2. Subsequent event 2 minutes later -> Paced by min_gap_minutes (8m)
    e_quick = Event(
        id="evt_2",
        ts=t0 + timedelta(minutes=2),
        type="VWAP_RECLAIM",
        facts={"spot": 25010.0},
        magnitude_sigma=1.3,
    )
    scored_quick = scorer.score_event(e_quick, state)
    pub_quick, reason_quick = scorer.should_publish(scored_quick)
    assert pub_quick is False
    assert "Paced" in reason_quick


def test_confluence_merging():
    scorer = SignificanceScorer()
    state = StoryState(phase="morning")
    t0 = datetime(2026, 10, 13, 10, 0, tzinfo=IST)

    s1 = scorer.score_event(
        Event(id="evt_wall", ts=t0, type="WALL_TEST", facts={"strike": 25000.0}, magnitude_sigma=0.8),
        state,
    )
    s2 = scorer.score_event(
        Event(id="evt_news", ts=t0 + timedelta(minutes=3), type="NEWS_CATALYST", facts={"headline": "Policy update"}, magnitude_sigma=0.75),
        state,
    )

    merged = scorer.merge_confluent_events([s1, s2])
    assert len(merged) == 1  # Successfully merged into one compound entry
    assert merged[0].event.id == "evt_wall"
    assert "evt_news" in merged[0].merged_event_ids
    assert merged[0].significance > s1.significance


def test_story_state_manager():
    mgr = StoryStateManager()
    t_open = datetime(2026, 10, 13, 9, 20, tzinfo=IST)
    assert mgr.determine_phase(t_open) == "opening_15m"

    t_mid = datetime(2026, 10, 13, 12, 15, tzinfo=IST)
    assert mgr.determine_phase(t_mid) == "midday"

    t_close = datetime(2026, 10, 13, 15, 10, tzinfo=IST)
    assert mgr.determine_phase(t_close) == "closing_30m"

    # Test thread lifecycle
    mgr.open_thread("thread_cw", "Testing Call Wall at 25,000", t_open, {"strike": 25000.0})
    assert len(mgr.state.open_threads) == 1

    mgr.close_thread("thread_cw")
    assert len(mgr.state.open_threads) == 0

    # Test thesis update
    evt_break = Event(
        id="evt_break",
        ts=t_open,
        type="WALL_BREAK",
        facts={"wall_type": "CALL_WALL", "strike": 25000.0},
        magnitude_sigma=1.2,
    )
    mgr.update_thesis(evt_break)
    assert mgr.state.running_thesis["bias"] == "bullish"
    assert mgr.state.running_thesis["confidence"] == 0.75
