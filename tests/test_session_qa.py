"""Hand-checkable tests for Session Desk Q&A (no network)."""

from __future__ import annotations

from analytics.llm_prompts import session_qa_messages
from analytics.session_qa import (
    CONFIRM_LINE,
    SESSION_FAQS,
    ask_session_qa,
    build_session_context,
    list_session_faqs,
    render_faq_brief,
    verify_qa_answer,
)


def _sample_context() -> dict:
    return build_session_context(
        "NIFTY",
        levels={
            "spot": 24500.0,
            "call_wall": 24600.0,
            "put_wall": 24400.0,
            "magnet": 24500.0,
            "flip": 24450.0,
            "max_pain": 24500.0,
            "regime": "POSITIVE",
            "total_net_gex": 2.02e11,
            "oi_timestamp": "2026-11-18T10:15:00+05:30",
            "oi_stale": False,
        },
        alert={
            "stance": "CALL WATCH",
            "confidence": "MED",
            "zone": "TEST_CALL",
            "headline": "Testing call wall",
            "drivers": "Spot is pressing the call-gamma concentration.",
            "trigger": "Hold above 24550",
            "kill": "Back under 24400",
            "outcome": "CALL WATCH · Confidence MED.",
        },
        go={
            "state": "SCOUT",
            "mode": "directional",
            "strategy_key": "call_debit",
            "thesis": "Press into call wall with defined risk.",
            "contracts": [
                {"action": "BUY", "opt_type": "CE", "strike": 24600.0, "expiry": "2026-11-25"},
            ],
            "hard_blocks": [],
            "invalidation": {"spot_below": 24400.0},
        },
        commentary=[
            {"headline": "Spot tagged put wall then bounced", "phase": "MIDDAY", "ts": "10:40"},
        ],
        digest={
            "narrative": "US soft overnight; India open calm.",
            "catalyst_risk": "LOW",
            "overnight_impact": 2,
            "status": "OK",
            "narrative_source": "heuristic",
        },
        session_meta={
            "as_of": "2026-11-18 10:20 IST",
            "session_phase": "REGULAR_SESSION",
            "stale_session": False,
            "oi_stale": False,
        },
        positioning={
            "alert": True,
            "headline": "Overnight long build meets live short covering.",
            "overnight_line": "FII added index futures overnight.",
            "live_line": "Price up, open interest down in the last window.",
            "overnight_state": "long_build",
            "live_label": "short covering",
            "file_date": "2026-11-17",
            "file_is_previous_session": True,
        },
        futures_state={
            "state": "Price up, open interest down",
            "dprice": 42.0,
            "doi": -15000.0,
            "price": 24520.0,
            "as_of": "11:05",
        },
        price_move={
            "spot": 24500.0,
            "prev_close": 24380.0,
            "change_pts": 120.0,
            "change_pct": 0.49,
            "day_open": 24410.0,
            "day_high": 24540.0,
            "day_low": 24390.0,
        },
    )


def test_faq_catalog_ids_stable():
    ids = [f["id"] for f in list_session_faqs()]
    assert ids == [f["id"] for f in SESSION_FAQS]
    assert "futures_positioning" in ids and "price_move" in ids and "alert_tape" in ids
    assert "book_balance" in ids and "lot_expiry" in ids


def test_every_faq_brief_contains_packed_numbers():
    ctx = _sample_context()
    for faq in SESSION_FAQS:
        brief = render_faq_brief(faq["id"], ctx)
        assert CONFIRM_LINE in brief
        if faq["id"] == "futures_positioning":
            assert "short covering" in brief.lower() or "Price up, open interest down" in brief
            assert "FII" in brief or "Overnight" in brief
        if faq["id"] == "price_move":
            assert "+120.0" in brief and "24,380" in brief and "24,600" in brief
        if faq["id"] == "alert_tape":
            assert "CALL WATCH" in brief and "TEST_CALL" in brief and "24550" in brief
        if faq["id"] == "walls_flip":
            assert "24,600" in brief and "24,400" in brief and "24,450" in brief
        if faq["id"] == "why_stance":
            assert "CALL WATCH" in brief and "TEST_CALL" in brief
        if faq["id"] == "go_now":
            assert "SCOUT" in brief and "24,600" in brief
        if faq["id"] == "lot_expiry":
            assert "65" in brief and "Tuesday" in brief
        if faq["id"] == "risk_kill":
            assert "24400" in brief or "24,400" in brief


def test_faq_heuristic_when_llm_off(monkeypatch):
    import analytics.llm_client as lc

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: False)
    ctx = _sample_context()
    out = ask_session_qa("NIFTY", faq_id="walls_flip", context=ctx)
    assert out["source"] == "heuristic"
    assert out["faq_id"] == "walls_flip"
    assert "24,600" in out["answer"]
    assert out["model"] is None


def test_freeform_refuses_when_llm_none(monkeypatch):
    import analytics.llm_client as lc

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: feat == "qa")
    monkeypatch.setattr(lc, "complete_chat", lambda *a, **k: None)
    ctx = _sample_context()
    out = ask_session_qa("NIFTY", question="Should I buy the 25000 call?", context=ctx)
    assert out["source"] == "refuse"
    assert "FAQ" in out["answer"]


def test_polish_rejects_invented_strike(monkeypatch):
    import analytics.llm_client as lc

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: feat == "qa")
    monkeypatch.setattr(lc, "model_name", lambda: "test-model")
    monkeypatch.setattr(
        lc,
        "complete_chat",
        lambda *a, **k: (
            "Spot is fine but watch the secret wall at 25111 instead. "
            + CONFIRM_LINE
        ),
    )
    ctx = _sample_context()
    out = ask_session_qa("NIFTY", faq_id="walls_flip", context=ctx)
    assert out["source"] == "heuristic"
    assert "24,600" in out["answer"]
    assert "25111" not in out["answer"]


def test_verify_rejects_execution_hype():
    ctx = _sample_context()
    ok, viol = verify_qa_answer(
        "Buy now with a market order at 24600. " + CONFIRM_LINE,
        ctx,
    )
    assert ok is False
    assert any("Forbidden" in v or "forbidden" in v.lower() for v in viol)


def test_verify_accepts_stance_echo():
    ctx = _sample_context()
    text = (
        "CALL WATCH at the 24,600 call wall; kill back under 24,400. "
        + CONFIRM_LINE
    )
    ok, viol = verify_qa_answer(text, ctx)
    assert ok is True, viol


def test_prompt_includes_only_supplied_facts():
    ctx = _sample_context()
    msgs = session_qa_messages(ctx, "Why this stance?", faq_id="why_stance")
    blob = " ".join(m["content"] for m in msgs)
    assert "CALL WATCH" in blob and "24400" in blob
    assert "BANKNIFTY" not in blob
    assert "Kentucky" not in blob


def test_trader_faq_prompts_ask_for_meaningful_rephrase():
    ctx = _sample_context()
    for faq_id in ("futures_positioning", "price_move", "alert_tape"):
        msgs = session_qa_messages(
            ctx, "explain", faq_id=faq_id, heuristic_brief=render_faq_brief(faq_id, ctx),
        )
        system = msgs[0]["content"]
        assert "trader" in system.lower()
        assert "every number" in system.lower()
        user = msgs[-1]["content"]
        assert "heuristic_brief" in user


def test_faq_polish_accepted_when_grounded(monkeypatch):
    import analytics.llm_client as lc

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: feat == "qa")
    monkeypatch.setattr(lc, "model_name", lambda: "gemma-test")
    monkeypatch.setattr(
        lc,
        "complete_chat",
        lambda *a, **k: (
            "Call wall 24,600, put wall 24,400, flip 24,450 versus spot 24,500. "
            + CONFIRM_LINE
        ),
    )
    ctx = _sample_context()
    out = ask_session_qa("NIFTY", faq_id="walls_flip", context=ctx)
    assert out["source"] == "genai"
    assert out["model"] == "gemma-test"
    assert "24,600" in out["answer"]


def test_verify_accepts_numbers_from_commentary_headlines():
    """Numbers living in string leaves (commentary headlines) are citable."""
    ctx = _sample_context()
    ctx["commentary"] = [
        {"headline": "Put Wall migration 25,700 to 25,500", "phase": "closing_30m", "ts": "15:16"},
        {"headline": "PCR Skew Extended: 1.45", "phase": "closing_30m", "ts": "15:35"},
    ]
    text = "Put wall moved from 25,700 to 25,500 per the tape. " + CONFIRM_LINE
    ok, viol = verify_qa_answer(text, ctx)
    assert ok is True, viol


def test_verify_still_rejects_number_absent_everywhere():
    ctx = _sample_context()
    ok, viol = verify_qa_answer("Magnet is the secret 25111 strike. " + CONFIRM_LINE, ctx)
    assert ok is False
    assert any("25111" in v for v in viol)


def test_freeform_ungrounded_is_not_llm_off(monkeypatch):
    import analytics.llm_client as lc
    from analytics.session_qa import _FREEFORM_UNGROUNDED

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: True)
    monkeypatch.setattr(lc, "model_name", lambda: "gemma-test")
    monkeypatch.setattr(lc, "complete_chat", lambda *a, **k: "Magnet is 25111. " + CONFIRM_LINE)
    out = ask_session_qa("NIFTY", question="Where is the magnet?", context=_sample_context())
    assert out["source"] == "refuse"
    assert out["answer"] == _FREEFORM_UNGROUNDED
    assert "LLM off" not in out["answer"]
    assert out["reject_reason"] == "ungrounded"


def test_freeform_llm_off_says_off(monkeypatch):
    import analytics.llm_client as lc
    from analytics.session_qa import _FREEFORM_OFF

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: False)
    out = ask_session_qa("NIFTY", question="Where is the call wall?", context=_sample_context())
    assert out["answer"] == _FREEFORM_OFF
    assert out["reject_reason"] == "llm_off"


def test_freeform_no_answer_says_no_answer(monkeypatch):
    import analytics.llm_client as lc
    from analytics.session_qa import _FREEFORM_NO_ANSWER

    monkeypatch.setattr(lc, "llm_enabled", lambda feat: True)
    monkeypatch.setattr(lc, "complete_chat", lambda *a, **k: "")
    out = ask_session_qa("NIFTY", question="Where is the call wall?", context=_sample_context())
    assert out["answer"] == _FREEFORM_NO_ANSWER
    assert out["reject_reason"] == "no_answer"
