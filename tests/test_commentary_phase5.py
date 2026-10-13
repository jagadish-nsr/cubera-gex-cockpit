"""tests/test_commentary_phase5.py — Verification of Phase 5 Narrator & Verifier Subsystem."""

from __future__ import annotations

import datetime as dt
from commentary.models import Event, SuggestionBite
from commentary.narrator.templates import render_event_template, render_bite_template
from commentary.narrator.verifier import verify_commentary
from commentary.narrator.client import NarratorClient


def test_verifier_catches_forbidden_phrases():
    text_bad = "This is a guaranteed win and a sure shot trade! Buy now for easy money."
    facts = {"spot": 24500.0}
    is_valid, violations = verify_commentary(text_bad, facts)
    assert not is_valid
    assert len(violations) >= 3
    assert any("guaranteed" in v for v in violations)
    assert any("sure shot" in v for v in violations)


def test_verifier_direction_mismatch():
    facts = {"spot": 24600.0, "spot_delta": 65.0}
    # Market went up by 65 points, but prose claims it collapsed
    text_contradict = "Spot crashed heavily to 24600."
    is_valid, violations = verify_commentary(text_contradict, facts, spot_delta=65.0)
    assert not is_valid
    assert any("Directional mismatch" in v for v in violations)


def test_verifier_numeric_hallucination():
    facts = {"call_wall": 24800.0, "spot": 24650.0}
    # Text mentions 25999.0 which is not in facts
    text_unverified = "Spot 24650 is targeting the imaginary level 25999."
    is_valid, violations = verify_commentary(text_unverified, facts, strict_numeric=True)
    assert not is_valid
    assert any("25999" in v for v in violations)


def test_render_event_templates_and_client():
    now = dt.datetime(2026, 10, 13, 9, 30, tzinfo=dt.timezone.utc)
    ev_gap = Event(
        id="evt-gap-1",
        ts=now,
        type="GAP_UP",
        facts={"open": 24600.0, "gap_pts": 120.0, "gap_pct": 0.49, "call_wall": 24800.0},
        magnitude_sigma=1.2,
    )
    client = NarratorClient()
    headline, body = client.narrate(ev_gap)
    assert "Gap-Up Open" in headline
    assert "24,600.0" in body or "24600" in body
    assert "+120.0 pts" in body

    # Verify that template output passes verifier
    is_valid, violations = verify_commentary(f"{headline} {body}", ev_gap.facts)
    assert is_valid
    assert len(violations) == 0


def test_render_bite_templates():
    now = dt.datetime.now(dt.timezone.utc)
    bite = SuggestionBite(
        id="bite-test-1",
        entry_id="entry-1",
        ts=now,
        type="ACT-IF",
        headline="Bullish Breakout Potential",
        condition={"field": "close_5m", "op": ">", "val": 24650.0},
        action_frame="Bull Call Spread 24600/24800 CE",
        structure={"legs": "24600CE / 24800CE"},
        levels={"trigger": 24650.0, "invalidation": 24550.0, "target": 24800.0},
        risk={"rr": 2.5, "max_loss_per_lot": 1500.0},
        valid_until=now + dt.timedelta(minutes=30),
        scenario_link="bull",
        badge={"mode": "shadow", "calibrated": False},
        facts_used=["spot", "call_wall"],
    )
    client = NarratorClient()
    text = client.narrate_bite(bite)
    assert "24,650" in text
    assert "Bull Call Spread" in text
    assert "24,800" in text
