"""Session-read card polish (positioning + chop) — no network."""

from __future__ import annotations

from analytics.llm_prompts import (
    choppiness_card_messages,
    parse_choppiness_rewrite,
    parse_positioning_rewrite,
    positioning_card_messages,
)
from analytics.session_read_polish import polish_choppiness, polish_positioning_flow


def test_positioning_prompt_is_trader_facing():
    msgs = positioning_card_messages({
        "headline": "Overnight long build meets live short covering.",
        "overnight_line": "FII added.",
        "live_line": "Price up, OI down.",
        "overnight_state": "long_build",
        "live_label": "short covering",
    })
    assert "trader" in msgs[0]["content"].lower()
    assert "FII added" in msgs[1]["content"]


def test_parse_positioning_rewrite():
    got = parse_positioning_rewrite(
        "HEADLINE: Covering into the lift\n"
        "OVERNIGHT: FII added index futures overnight.\n"
        "LIVE: Price up with open interest down."
    )
    assert got == (
        "Covering into the lift",
        "FII added index futures overnight.",
        "Price up with open interest down.",
    )


def test_parse_choppiness_rewrite():
    got = parse_choppiness_rewrite(
        "HEADLINE: Path is boxed\nLINE: Chop 72.0 of 100; kept 18% of the walk."
    )
    assert got == ("Path is boxed", "Chop 72.0 of 100; kept 18% of the walk.")


def test_polish_fail_soft_when_llm_off(monkeypatch):
    import analytics.llm_client as lc

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: False)
    flow = {
        "headline": "Overnight long build meets live short covering.",
        "overnight_line": "FII added.",
        "live_line": "Price up, OI down.",
    }
    out = polish_positioning_flow(flow)
    assert out["text_source"] == "heuristic"
    assert out["headline"] == flow["headline"]

    chop = {"state": "choppy", "headline": "Price is boxed in", "line": "Chop 70.0 of 100."}
    cout = polish_choppiness(chop)
    assert cout["text_source"] == "heuristic"
    assert cout["headline"] == "Price is boxed in"


def test_polish_applies_when_valid(monkeypatch):
    import analytics.llm_client as lc

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: feat == "session_reads")
    monkeypatch.setattr(lc, "model_name", lambda: "gemma-test")
    monkeypatch.setattr(
        lc,
        "complete_chat",
        lambda *a, **k: (
            "HEADLINE: Shorts covering the lift\n"
            "OVERNIGHT: FII added overnight.\n"
            "LIVE: Price up, open interest down."
        ),
    )
    from analytics.llm_background import drain

    flow = {
        "headline": "raw",
        "overnight_line": "FII added overnight.",
        "live_line": "Price up, open interest down.",
    }
    first = polish_positioning_flow(flow)
    assert first["text_source"] == "heuristic"          # the model never blocks the request
    assert first["headline"] == "raw"
    assert drain()
    out = polish_positioning_flow(flow)
    assert out["text_source"] == "genai"
    assert out["headline"] == "Shorts covering the lift"
    assert out["llm_model"] == "gemma-test"
