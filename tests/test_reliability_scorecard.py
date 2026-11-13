"""Pillar G4 — reliability scorecard markdown (no invented counts)."""

from __future__ import annotations

from unittest import mock

from app.reliability_scorecard import (
    build_reliability_scorecard,
    render_scorecard_markdown,
    rollup_readiness_minutes,
)


def test_scorecard_marks_missing_rollups():
    with mock.patch(
        "app.reliability_scorecard.rollup_readiness_minutes",
        return_value={"available": False, "ready_minutes": None, "not_ready_minutes": None},
    ):
        card = build_reliability_scorecard(
            session_date="2026-11-13",
            version="5.3.124",
            health={
                "version": "5.3.124",
                "readiness": {
                    "degrade_mode": "OFF_SESSION",
                    "reasons": [],
                    "probes": {"ingest": {"age_s": 1.2, "required": True, "reason": ""}},
                },
            },
            ops={"events_summary": {"failures": {}}},
            checklist={"pass": True, "failed": []},
        )
    assert card["session_date"] == "2026-11-13"
    by_metric = {r["metric"]: r["value"] for r in card["rows"]}
    assert by_metric["Process version"] == "5.3.124"
    assert by_metric["Ready minutes (session)"] == "not rolled up"
    assert "Reliability scorecard" in card["markdown"]
    assert "Do not invent" in card["markdown"]


def test_rollup_readiness_minutes_from_events():
    events = [
        {"ts": "2026-11-13T09:20:10+05:30", "message": "ready", "fields": {"ready": True}},
        {"ts": "2026-11-13T09:21:10+05:30", "message": "not_ready", "fields": {"ready": False}},
        {"ts": "2026-11-13T09:21:40+05:30", "message": "not_ready", "fields": {"ready": False}},  # dup minute
    ]
    rolled = rollup_readiness_minutes(session_date="2026-11-13", events=events)
    assert rolled["available"] is True
    assert rolled["ready_minutes"] == 1
    assert rolled["not_ready_minutes"] == 1
    assert rolled["burn_pct"] == 50.0


def test_scorecard_uses_rollup():
    with mock.patch(
        "app.reliability_scorecard.rollup_readiness_minutes",
        return_value={
            "available": True,
            "ready_minutes": 350,
            "not_ready_minutes": 8,
            "total_minutes": 358,
            "burn_pct": 2.23,
        },
    ):
        card = build_reliability_scorecard(
            session_date="2026-11-13",
            version="5.3.132",
            health={"readiness": {"degrade_mode": "OFF_SESSION", "reasons": [], "probes": {}}},
            ops={"events_summary": {"failures": {}}},
            checklist={"pass": True, "failed": []},
        )
    by_metric = {r["metric"]: r["value"] for r in card["rows"]}
    assert by_metric["Ready minutes (session)"] == 350
    assert by_metric["Not-ready minutes"] == 8


def test_render_scorecard_table():
    md = render_scorecard_markdown(
        [{"metric": "Watchdog restarts", "value": 0, "note": "ok"}],
        session_date="2026-11-13",
    )
    assert "| Watchdog restarts | 0 | ok |" in md
