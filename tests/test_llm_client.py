"""Hand-checkable tests for shared LLM client + digest/alert hooks (no network)."""

from __future__ import annotations

from datetime import date, datetime

import analytics.llm_client as llm
import analytics.pre_open_digest as pod
from analytics.llm_prompts import (
    alert_tape_messages,
    digest_narrative_messages,
    parse_alert_tape_rewrite,
)
from analytics.market_alert import _alert_rewrite_ok, _maybe_llm_polish_alert
from analytics.pre_open_digest import build_pre_open_digest


def test_llm_disabled_without_flags(monkeypatch):
    monkeypatch.delenv("CUBERA_LLM", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert llm.llm_enabled("digest") is False


def test_llm_enabled_ollama_without_cloud_key(monkeypatch):
    monkeypatch.setenv("CUBERA_LLM", "1")
    monkeypatch.setenv("CUBERA_LLM_PROVIDER", "ollama")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("CUBERA_LLM_DIGEST", raising=False)
    assert llm.provider() == "ollama"
    assert llm.llm_enabled("digest") is True
    monkeypatch.setenv("CUBERA_LLM_DIGEST", "0")
    assert llm.llm_enabled("digest") is False


def test_llm_enabled_gemini_needs_key(monkeypatch):
    monkeypatch.setenv("CUBERA_LLM", "1")
    monkeypatch.setenv("CUBERA_LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert llm.llm_enabled("digest") is False
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    assert llm.llm_enabled("digest") is True


def test_digest_prompt_contains_only_supplied_titles():
    events = [
        {"title": "RBI rate cut sparks bank rally", "bucket": "india", "impact": 4,
         "category": "Macro & Policy", "sentiment": "BULLISH"},
        {"title": "Dow plunges on Wall Street", "bucket": "us_global", "impact": 3,
         "category": "Markets & Economy", "sentiment": "BEARISH"},
    ]
    msgs = digest_narrative_messages(events, catalyst_risk="MODERATE", overnight_impact=7)
    blob = " ".join(m["content"] for m in msgs)
    assert "RBI rate cut" in blob and "Dow plunges" in blob
    assert "Kentucky" not in blob


def test_digest_uses_genai_when_enabled(monkeypatch):
    monkeypatch.setenv("CUBERA_LLM", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("CUBERA_LLM_DIGEST", "1")
    monkeypatch.setattr(
        "analytics.llm_client.complete_chat",
        lambda *a, **k: (
            "US & global cues — Dow sold off. India — RBI cut lifted banks. "
            "Full headlines with source links are in Real-Time News & Catalysts."
        ),
    )
    monkeypatch.setattr("analytics.llm_client.model_name", lambda: "gemini-2.0-flash")
    # complete_chat is imported inside build — patch module used by pre_open_digest
    import analytics.llm_client as lc
    monkeypatch.setattr(lc, "complete_chat", lambda *a, **k: (
        "US & global cues — Dow sold off. India — RBI cut lifted banks. "
        "Full headlines with source links are in Real-Time News & Catalysts."
    ))
    monkeypatch.setattr(lc, "llm_enabled", lambda feat: True)
    monkeypatch.setattr(lc, "model_name", lambda: "gemini-2.0-flash")

    now = datetime(2026, 6, 13, 8, 30, tzinfo=pod.IST)
    news = {
        "headlines": [{
            "title": "RBI rate cut sparks private bank rally",
            "url": "https://example.com/rbi",
            "age_min": 60,
            "impact": 4,
            "category": "Macro & Policy",
            "sentiment": "BULLISH",
            "pub_time_ist": "07:00 IST",
        }],
    }
    d = build_pre_open_digest(
        news, None, now_ist=now, session_date=date(2026, 6, 13),
        overnight_window_min=960, window_label="test",
    )
    assert d["narrative_source"] == "genai"
    assert "Dow sold off" in d["narrative"]
    assert d["llm_model"] == "gemini-2.0-flash"


def test_digest_keeps_heuristic_when_llm_fails(monkeypatch):
    import analytics.llm_client as lc
    monkeypatch.setattr(lc, "llm_enabled", lambda feat: True)
    monkeypatch.setattr(lc, "complete_chat", lambda *a, **k: None)
    now = datetime(2026, 6, 13, 8, 30, tzinfo=pod.IST)
    news = {
        "headlines": [{
            "title": "RBI rate cut sparks private bank rally",
            "url": "https://example.com/rbi",
            "age_min": 60,
            "impact": 4,
            "category": "Macro & Policy",
            "sentiment": "BULLISH",
            "pub_time_ist": "07:00 IST",
        }],
    }
    d = build_pre_open_digest(
        news, None, now_ist=now, session_date=date(2026, 6, 13),
        overnight_window_min=960, window_label="test",
    )
    assert d["narrative_source"] == "heuristic"
    assert "RBI" in d["narrative"]


def test_parse_alert_tape_rewrite():
    got = parse_alert_tape_rewrite(
        "HEADLINE: Between the walls\nDRIVERS: Spot sits mid-range with no fresh tape trigger."
    )
    assert got == ("Between the walls", "Spot sits mid-range with no fresh tape trigger.")


def test_alert_polish_fail_soft(monkeypatch):
    import analytics.llm_client as lc
    monkeypatch.setattr(lc, "llm_enabled", lambda feat: True)
    monkeypatch.setattr(lc, "complete_chat", lambda *a, **k: None)
    alert = {
        "stance": "STAY OUT",
        "confidence": "MED",
        "headline": "Between walls",
        "drivers": "No fresh catalyst in the book.",
        "trigger": "Wait",
        "kill": "Beyond the wall",
        "zone": "BETWEEN",
    }
    out = _maybe_llm_polish_alert(alert)
    assert out["text_source"] == "heuristic"
    assert out["headline"] == "Between walls"


def test_alert_polish_applies_when_valid(monkeypatch):
    import analytics.llm_client as lc
    monkeypatch.setattr(lc, "llm_enabled", lambda feat: feat == "alert_tape")
    monkeypatch.setattr(
        lc,
        "complete_chat",
        lambda *a, **k: "HEADLINE: Quiet mid-range\nDRIVERS: No fresh orderflow print; walls still frame the tape.",
    )
    monkeypatch.setattr(lc, "model_name", lambda: "gemini-2.0-flash")
    alert = {
        "stance": "STAY OUT",
        "confidence": "MED",
        "headline": "Between walls",
        "drivers": "No fresh catalyst in the book for now.",
        "trigger": "Wait",
        "kill": "Beyond the wall",
        "zone": "BETWEEN",
        "diagnostics": "No fresh catalyst in the book for now.",
    }
    out = _maybe_llm_polish_alert(alert)
    assert out["text_source"] == "genai"
    assert out["headline"] == "Quiet mid-range"
    assert "orderflow" in out["drivers"]


def test_alert_rewrite_rejects_conflicting_stance():
    alert = {"stance": "STAY OUT"}
    assert _alert_rewrite_ok(alert, "Stay calm", "CALL WATCH looks better here today") is False
    assert _alert_rewrite_ok(alert, "Stay calm", "No fresh catalyst in the book today.") is True


def test_alert_prompt_includes_stance_and_kill():
    msgs = alert_tape_messages({
        "stance": "CALL WATCH",
        "confidence": "HIGH",
        "headline": "Testing call wall",
        "drivers": "Spot is pressing the call concentration.",
        "trigger": "Hold above wall",
        "kill": "Back under 24500",
        "zone": "TESTING_CALL",
    })
    blob = msgs[1]["content"]
    assert "CALL WATCH" in blob and "24500" in blob
    assert "trader" in msgs[0]["content"].lower()
