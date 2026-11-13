"""Pillar G4 — reliability scorecard markdown (no invented counts)."""

from __future__ import annotations

from app.reliability_scorecard import build_reliability_scorecard, render_scorecard_markdown


def test_scorecard_marks_missing_rollups():
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


def test_render_scorecard_table():
    md = render_scorecard_markdown(
        [{"metric": "Watchdog restarts", "value": 0, "note": "ok"}],
        session_date="2026-11-13",
    )
    assert "| Watchdog restarts | 0 | ok |" in md
