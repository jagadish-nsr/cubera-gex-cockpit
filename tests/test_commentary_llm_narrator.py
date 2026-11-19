"""Grounded LLM rewrite for session commentary story entries (no network)."""

from __future__ import annotations

import datetime as dt

from analytics.llm_prompts import commentary_story_messages, parse_commentary_story_rewrite
from commentary.models import Event
from commentary.narrator.client import NarratorClient


def _gap_event() -> Event:
    return Event(
        id="evt-gap-1",
        ts=dt.datetime(2026, 11, 19, 9, 20, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))),
        type="GAP_UP",
        facts={
            "open": 24600.0,
            "gap_pts": 120.0,
            "gap_pct": 0.49,
            "call_wall": 24800.0,
            "symbol": "NIFTY",
        },
        magnitude_sigma=1.2,
    )


def test_parse_commentary_story_rewrite():
    got = parse_commentary_story_rewrite(
        "HEADLINE: Gap lifts into the call book\n"
        "BODY: NIFTY opened 120 pts higher at 24,600 toward the 24,800 call wall."
    )
    assert got is not None
    assert "call" in got[0].lower()
    assert "24,600" in got[1] or "24600" in got[1]


def test_story_prompt_includes_session_context():
    msgs = commentary_story_messages(
        event_type="GAP_UP",
        event_facts={"open": 24600.0, "gap_pts": 120.0, "call_wall": 24800.0},
        template_headline="Gap-Up Open",
        template_body="Opened higher.",
        session_context={
            "spot": 24610.0,
            "put_wall": 24400.0,
            "phase": "opening_15m",
            "market_alert": {"stance": "CALL WATCH", "kill": "Back under 24400"},
            "positioning": {"live_label": "short covering"},
            "price_move": {"change_pts": 120.0},
        },
    )
    blob = " ".join(m["content"] for m in msgs)
    assert "24800" in blob and "24400" in blob and "opening_15m" in blob
    assert "CALL WATCH" in blob and "short covering" in blob
    assert "market_alert" in msgs[0]["content"].lower()
    assert "trader" in msgs[0]["content"].lower()


def test_narrative_context_merges_overrides(monkeypatch):
    from analytics import session_qa as sq

    monkeypatch.setattr(
        sq,
        "gather_session_context",
        lambda symbol: {
            "symbol": "NIFTY",
            "spot": 24000.0,
            "call_wall": 24100.0,
            "put_wall": 23900.0,
            "regime": "POSITIVE",
            "net_gex": 1e11,
            "oi_timestamp": "2026-11-19T09:30:00+05:30",
            "market_alert": {"stance": "STAY OUT", "kill": "n/a"},
            "positioning": {"headline": "Quiet futures tape"},
            "futures_state": {"state": "Price or open interest is flat"},
            "price_move": {"change_pts": 10.0},
            "digest": {"catalyst_risk": "LOW", "narrative": "Calm overnight."},
            "go": {"state": "SCOUT", "thesis": "Watch the wall", "contracts": [{"strike": 24100}]},
        },
    )
    ctx = sq.build_narrative_session_context(
        "NIFTY",
        overrides={"spot": 24610.0, "call_wall": 24800.0, "phase": "morning"},
        story_thesis={"bias": "bull", "confidence": 0.6},
    )
    assert ctx["spot"] == 24610.0
    assert ctx["call_wall"] == 24800.0
    assert ctx["market_alert"]["stance"] == "STAY OUT"
    assert ctx["positioning"]["headline"] == "Quiet futures tape"
    assert ctx["running_thesis"]["bias"] == "bull"
    assert ctx["go"]["state"] == "SCOUT"


def test_narrator_stays_template_when_llm_off(monkeypatch):
    import analytics.llm_client as lc

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: False)
    client = NarratorClient(enable_llm=True)
    hl, body = client.narrate(_gap_event(), context={"spot": 24610.0, "put_wall": 24400.0})
    assert "Gap-Up Open" in hl
    assert client.last_text_source == "heuristic"
    assert "24,600" in body or "24600" in body


def test_narrator_applies_grounded_rewrite(monkeypatch):
    import analytics.llm_client as lc

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: feat == "commentary")
    monkeypatch.setattr(lc, "model_name", lambda: "gemma-test")
    monkeypatch.setattr(
        lc,
        "complete_chat",
        lambda *a, **k: (
            "HEADLINE: Gap opens toward the call wall\n"
            "BODY: NIFTY opened at 24600.0, up 120.0 pts (0.49%), pressing toward the "
            "24800.0 call wall with put wall 24400.0 still below."
        ),
    )
    client = NarratorClient(enable_llm=True)
    hl, body = client.narrate(
        _gap_event(),
        context={"spot": 24610.0, "put_wall": 24400.0, "phase": "opening_15m"},
    )
    assert client.last_text_source == "genai"
    assert client.last_llm_model == "gemma-test"
    assert "call wall" in hl.lower() or "Gap" in hl
    assert "24600" in body and "24800" in body and "24400" in body


def test_narrator_rejects_invented_strike(monkeypatch):
    import analytics.llm_client as lc

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: feat == "commentary")
    monkeypatch.setattr(
        lc,
        "complete_chat",
        lambda *a, **k: (
            "HEADLINE: Gap chase\n"
            "BODY: Spot ripped toward the secret wall at 25999."
        ),
    )
    client = NarratorClient(enable_llm=True)
    hl, body = client.narrate(_gap_event(), context={"put_wall": 24400.0})
    assert client.last_text_source == "heuristic"
    assert "Gap-Up Open" in hl
    assert "25999" not in body
