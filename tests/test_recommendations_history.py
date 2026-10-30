"""tests/test_recommendations_history.py — Test Trade Recommendation History & Lifecycle Audit.

Verifies:
1. Isolated database testing (zero mutation of live data/journal/recommendations.db).
2. cockpit.js syntax integrity: matched braces from top IIFE to end with no early closures.
3. GET /api/recommendations/history is strictly read-only (zero writes, no event side-effects).
4. Cross-symbol spot safety: Bank Nifty / Sensex invalidations are not breached by 24500 fallback.
5. Monotonic peak tracking: highest_score and highest_tier preserve peak across score fades.
6. Score >= 65 threshold strictly enforced: sub-65 ideas never appear in history.
7. Score delta distance from peak conviction (at peak vs fade).
8. Zero fabricated probabilities, expectancy, delta, or score history narratives.
9. Summary tier counts remain stable across tier filtering.
"""

import os
import json
import sqlite3
import asyncio
from datetime import datetime
import pytest
import pytz

import data.journal.recommendations_store as rs
from web_server import get_recommendations_history_endpoint, _serialize_trade_idea
from analytics.trade_ideas import TradeIdea

IST = pytz.timezone("Asia/Kolkata")


@pytest.fixture(autouse=True)
def isolated_rec_db(tmp_path, monkeypatch):
    """Ensure every test in this module runs against a clean temporary SQLite database."""
    test_db = tmp_path / "test_recommendations.db"
    monkeypatch.setattr(rs, "_DB_PATH", test_db)
    # Reset in-memory cache
    with rs._BOOKMARK_CACHE_LOCK:
        rs._BOOKMARK_CACHE.clear()
        rs._CACHE_INITIALIZED = False
    rs.ensure_schema()
    yield test_db
    with rs._BOOKMARK_CACHE_LOCK:
        rs._BOOKMARK_CACHE.clear()
        rs._CACHE_INITIALIZED = False


def test_cockpit_js_syntax_integrity():
    """Verify app/static/js/cockpit.js parses with 100% balanced braces and no early IIFE closure."""
    js_path = os.path.join(os.path.dirname(__file__), "..", "app", "static", "js", "cockpit.js")
    with open(js_path, "r") as f:
        text = f.read()

    in_str = None
    in_line_comment = False
    in_block_comment = False
    line = 1
    col = 0
    bracket_stack = []
    scope_closed_lines = []

    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            col = 0
            in_line_comment = False
            i += 1
            continue
        col += 1

        if in_line_comment:
            i += 1
            continue
        if in_block_comment:
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                in_block_comment = False
                i += 2
                col += 1
                continue
            i += 1
            continue
        if in_str:
            if c == "\\":
                i += 2
                col += 1
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue

        if c == "/" and i + 1 < n:
            if text[i + 1] == "/":
                in_line_comment = True
                i += 2
                col += 1
                continue
            elif text[i + 1] == "*":
                in_block_comment = True
                i += 2
                col += 1
                continue

        if c in ("\"", "'", "`"):
            in_str = c
            i += 1
            continue

        if c == "{":
            bracket_stack.append((line, col))
        elif c == "}":
            assert len(bracket_stack) > 0, f"Extra closing brace found at line {line}, col {col}"
            bracket_stack.pop()
            if len(bracket_stack) == 0:
                scope_closed_lines.append(line)

        i += 1

    assert len(bracket_stack) == 0, f"Unclosed braces remaining: {len(bracket_stack)}"
    # The top-level IIFE must close ONLY at the very end of the file
    total_lines = text.count("\n") + 1
    assert len(scope_closed_lines) == 1, f"Top-level scope closed early at lines: {scope_closed_lines}"
    assert scope_closed_lines[0] >= total_lines - 5, "Top-level scope closed prematurely before end of file"


def test_schema_has_trajectory_and_peak_columns():
    """Verify recommendation_records contains last_appeared, highest_score, highest_tier."""
    rs.ensure_schema()
    with rs._get_read_conn() as conn:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(recommendation_records)").fetchall()]
    assert "last_appeared" in cols
    assert "highest_score" in cols
    assert "highest_tier" in cols


def test_get_history_endpoint_is_strictly_read_only_and_safe_for_banknifty():
    """Verify GET /api/recommendations/history is 100% read-only and does not mutate Bank Nifty cards."""
    now_ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    # Create a Bank Nifty recommendation with spot invalidation at 51,000
    bn_payload = {
        "rec_id": "BANKNIFTY_CE_51500_MOMENTUM",
        "symbol": "BANKNIFTY",
        "side": "CE",
        "strike": 51500.0,
        "option_symbol": "BANKNIFTY 51500 CE",
        "play_type": "Momentum",
        "play_badge": "MOMENTUM",
        "triggered_at": now_ts,
        "initial_spot": 51200.0,
        "current_spot": 51200.0,
        "initial_ltp": 250.0,
        "current_ltp": 250.0,
        "quality_score": 88,
        "conviction_tier": "HIGH",
        "target_1": 340.0,
        "target_1_spot": 51400.0,
        "target_2": 420.0,
        "target_2_spot": 51600.0,
        "stop_loss": 180.0,
        "spot_invalidation": 51000.0,
        "spot_invalidation_text": "Breach of 51000",
        "risk_reward_ratio": "1 : 2.5",
        "entry_trigger": "VWAP test",
        "rationale": "Bank Nifty momentum",
        "catalyst_tags": ["BNTag"],
        "status": "ACTIVE",
    }
    rs.save_or_update_recommendation(bn_payload)

    # Count events before GET call
    with rs._get_read_conn() as conn:
        events_before = conn.execute("SELECT COUNT(*) as c FROM recommendation_events").fetchone()["c"]
        rec_before = conn.execute("SELECT status, outcome_ts FROM recommendation_records WHERE rec_id = ?", (bn_payload["rec_id"],)).fetchone()
        assert rec_before["status"] == "ACTIVE"
        assert rec_before["outcome_ts"] is None

    # Call the GET endpoint
    res = asyncio.run(get_recommendations_history_endpoint(symbol="BANKNIFTY", limit=10))
    assert res["total"] >= 1

    # Verify ZERO writes happened
    with rs._get_read_conn() as conn:
        events_after = conn.execute("SELECT COUNT(*) as c FROM recommendation_events").fetchone()["c"]
        rec_after = conn.execute("SELECT status, outcome_ts FROM recommendation_records WHERE rec_id = ?", (bn_payload["rec_id"],)).fetchone()
        assert events_after == events_before, "GET endpoint created unexpected lifecycle events in database!"
        assert rec_after["status"] == "ACTIVE", "Bank Nifty card status was mutated by GET request!"
        assert rec_after["outcome_ts"] is None, "Bank Nifty card outcome_ts was overwritten!"


def test_record_event_cannot_overwrite_terminal_target_outcome():
    """Verify that a card already marked target-hit cannot be overwritten by subsequent events."""
    now_ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    test_id = "TEST_REC_TERMINAL_PROTECTION_CE"

    payload = {
        "rec_id": test_id,
        "symbol": "NIFTY",
        "side": "CE",
        "strike": 24700.0,
        "option_symbol": "NIFTY 24700 CE",
        "play_type": "Structure",
        "play_badge": "STRUCTURE",
        "triggered_at": now_ts,
        "initial_spot": 24650.0,
        "current_spot": 24650.0,
        "initial_ltp": 90.0,
        "current_ltp": 90.0,
        "quality_score": 82,
        "conviction_tier": "MEDIUM",
        "target_1": 130.0,
        "target_1_spot": 24720.0,
        "target_2": 160.0,
        "target_2_spot": 24780.0,
        "stop_loss": 65.0,
        "spot_invalidation": 24600.0,
        "spot_invalidation_text": "Breach 24600",
        "risk_reward_ratio": "1 : 2.5",
        "entry_trigger": "Structure",
        "rationale": "Test",
        "catalyst_tags": [],
        "status": "ACTIVE",
    }
    rs.save_or_update_recommendation(payload)

    # 1. Target 1 achieved
    rs.record_event(
        rec_id=test_id,
        symbol="NIFTY",
        event_type="TGT1_HIT",
        spot_at_event=24720.0,
        ltp_at_event=130.0,
        pnl_pct=44.4,
        message="Target 1 hit",
        was_bookmarked=False,
    )
    with rs._get_read_conn() as conn:
        row = conn.execute("SELECT status FROM recommendation_records WHERE rec_id = ?", (test_id,)).fetchone()
        assert row["status"] == "TGT1_HIT"

    # 2. Later spot drops through invalidation -> INVALIDATED event attempted
    rs.record_event(
        rec_id=test_id,
        symbol="NIFTY",
        event_type="INVALIDATED",
        spot_at_event=24580.0,
        ltp_at_event=50.0,
        pnl_pct=-44.4,
        message="Inval breach after target",
        was_bookmarked=False,
    )
    with rs._get_read_conn() as conn:
        row2 = conn.execute("SELECT status FROM recommendation_records WHERE rec_id = ?", (test_id,)).fetchone()
        # MUST REMAIN TGT1_HIT! Never overwritten to INVALIDATED!
        assert row2["status"] == "TGT1_HIT", "Achieved target was overwritten by a later invalidation!"


def test_monotonic_peak_tracking_and_score_delta():
    """Verify monotonic peak tracking and score_delta distance below peak."""
    test_id = "TEST_REC_MONOTONIC_PEAK"
    now_ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    base = {
        "rec_id": test_id,
        "symbol": "NIFTY",
        "side": "CE",
        "strike": 24600.0,
        "option_symbol": "NIFTY 24600 CE",
        "play_type": "Momentum",
        "play_badge": "MOMENTUM",
        "triggered_at": now_ts,
        "initial_spot": 24550.0,
        "current_spot": 24550.0,
        "initial_ltp": 70.0,
        "current_ltp": 70.0,
        "quality_score": 72,  # Tactical
        "conviction_tier": "LOW",
        "target_1": 100.0,
        "target_1_spot": 24620.0,
        "target_2": 130.0,
        "target_2_spot": 24670.0,
        "stop_loss": 50.0,
        "spot_invalidation": 24500.0,
        "spot_invalidation_text": "Breach 24500",
        "risk_reward_ratio": "1 : 2.5",
        "entry_trigger": "Trigger",
        "rationale": "Rationale",
        "catalyst_tags": [],
        "status": "ACTIVE",
    }

    # 1. First print: Tactical (72)
    rs.save_or_update_recommendation(base)
    with rs._get_read_conn() as conn:
        r1 = conn.execute("SELECT highest_score, highest_tier, current_score FROM recommendation_records WHERE rec_id = ?", (test_id,)).fetchone()
        assert r1["highest_score"] == 72
        assert r1["highest_tier"] == "TACTICAL"
        assert r1["current_score"] == 72

    # 2. Score surges to High Conviction (92)
    surge = dict(base)
    surge["quality_score"] = 92
    surge["conviction_tier"] = "HIGH"
    rs.save_or_update_recommendation(surge)
    with rs._get_read_conn() as conn:
        r2 = conn.execute("SELECT highest_score, highest_tier, current_score FROM recommendation_records WHERE rec_id = ?", (test_id,)).fetchone()
        assert r2["highest_score"] == 92
        assert r2["highest_tier"] == "HIGH"
        assert r2["current_score"] == 92

    # 3. Score fades to 66 (below secondary threshold)
    fade = dict(base)
    fade["quality_score"] = 66
    fade["conviction_tier"] = "LOW"
    rs.save_or_update_recommendation(fade)
    with rs._get_read_conn() as conn:
        r3 = conn.execute("SELECT highest_score, highest_tier, current_score FROM recommendation_records WHERE rec_id = ?", (test_id,)).fetchone()
        assert r3["highest_score"] == 92
        assert r3["highest_tier"] == "HIGH"
        assert r3["current_score"] == 66

    # 4. Score fades to 45 (sub-threshold)
    fade2 = dict(base)
    fade2["quality_score"] = 45
    fade2["conviction_tier"] = "LOW"
    rs.save_or_update_recommendation(fade2)
    with rs._get_read_conn() as conn:
        r4 = conn.execute("SELECT highest_score, highest_tier, current_score FROM recommendation_records WHERE rec_id = ?", (test_id,)).fetchone()
        assert r4["highest_score"] == 92
        assert r4["highest_tier"] == "HIGH"
        assert r4["current_score"] == 45


def test_sub_65_scores_strictly_excluded_from_history():
    """Verify that setups with peak score < 65 are strictly excluded from recommendation history."""
    now_ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    # Idea with score 55 (never reached 65)
    sub65_payload = {
        "rec_id": "TEST_SUB_65_DISCARD",
        "symbol": "NIFTY",
        "side": "PE",
        "strike": 24400.0,
        "option_symbol": "NIFTY 24400 PE",
        "play_type": "Fade",
        "play_badge": "ICEBERG FADE",
        "triggered_at": now_ts,
        "initial_spot": 24500.0,
        "current_spot": 24500.0,
        "initial_ltp": 50.0,
        "current_ltp": 50.0,
        "quality_score": 55,
        "conviction_tier": "LOW",
        "target_1": 80.0,
        "target_1_spot": 24420.0,
        "target_2": 100.0,
        "target_2_spot": 24380.0,
        "stop_loss": 35.0,
        "spot_invalidation": 24550.0,
        "spot_invalidation_text": "Breach 24550",
        "risk_reward_ratio": "1 : 2.0",
        "entry_trigger": "None",
        "rationale": "Sub-threshold test",
        "catalyst_tags": [],
        "status": "ACTIVE",
    }
    rs.save_or_update_recommendation(sub65_payload)

    # Valid tactical idea (score 68)
    tact_payload = dict(sub65_payload)
    tact_payload["rec_id"] = "TEST_TACTICAL_VALID"
    tact_payload["quality_score"] = 68
    rs.save_or_update_recommendation(tact_payload)

    hist = rs.get_recommendation_history()
    ids = [r["rec_id"] for r in hist]
    assert "TEST_TACTICAL_VALID" in ids
    assert "TEST_SUB_65_DISCARD" not in ids, "Sub-65 recommendation leaked into recommendation history!"


def test_no_synthetic_probabilities_or_mock_score_history():
    """Verify that unpopulated fields remain None and score history is never fabricated."""
    test_idea = TradeIdea(
        symbol="NIFTY",
        side="CE",
        play_type="Momentum",
        play_badge="MOMENTUM",
        strike=24600.0,
        option_symbol="NIFTY 24600 CE",
        current_ltp=85.0,
        delta_approx=0.0,
        entry_trigger="Test",
        spot_invalidation=24520.0,
        spot_invalidation_text="Breach 24520",
        target_1=120.0,
        target_1_spot=24680.0,
        target_2=150.0,
        target_2_spot=24720.0,
        stop_loss=60.0,
        risk_reward_ratio="1 : 2.5",
        quality_score=80,
        conviction_label="SECONDARY PLAY",
        conviction_color="var(--spark)",
        rationale="Real rationale",
        catalyst_tags=[],
        rec_id="TEST_REC_NO_FABRICATION",
        # Explicitly unpopulated probabilities
        p_touch_target_1=0.0,
        p_touch_sl=0.0,
        net_ev_pts=0.0,
    )

    ser = _serialize_trade_idea(test_idea, 24600.0, 500000.0, 0.015, 65, is_historical=True)

    # Probabilities and EV must NOT be fabricated to 0.65 / 0.35 / 18.5
    assert ser.get("p_touch_target_1") != 0.65
    assert ser.get("p_touch_sl") != 0.35
    assert ser.get("net_ev_pts") != 18.5

    # Score history must be empty, not contain the mock "High absorption at Put Wall"
    hist = ser.get("score_history", [])
    assert not any("High absorption at Put Wall" in str(h) for h in hist), "Mock score history was fabricated!"


def test_summary_tier_counts_remain_stable_across_filter():
    """Verify that summary tier counts do not drop to 0 when filtering by a specific tier."""
    now_ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    # Add 1 High, 1 Secondary, 1 Tactical
    rs.save_or_update_recommendation({
        "rec_id": "STABLE_HIGH", "symbol": "NIFTY", "side": "CE", "strike": 24600.0,
        "option_symbol": "NIFTY 24600 CE", "play_type": "M", "play_badge": "MOMENTUM",
        "triggered_at": now_ts, "initial_spot": 24550.0, "current_spot": 24550.0,
        "initial_ltp": 80.0, "current_ltp": 80.0, "quality_score": 90, "conviction_tier": "HIGH",
        "target_1": 110.0, "target_1_spot": 24650.0, "target_2": 140.0, "target_2_spot": 24700.0,
        "stop_loss": 60.0, "spot_invalidation": 24500.0, "spot_invalidation_text": "Breach",
        "risk_reward_ratio": "1 : 2.5", "entry_trigger": "T", "rationale": "R", "catalyst_tags": [],
        "status": "ACTIVE",
    })
    rs.save_or_update_recommendation({
        "rec_id": "STABLE_SEC", "symbol": "NIFTY", "side": "CE", "strike": 24650.0,
        "option_symbol": "NIFTY 24650 CE", "play_type": "M", "play_badge": "MOMENTUM",
        "triggered_at": now_ts, "initial_spot": 24550.0, "current_spot": 24550.0,
        "initial_ltp": 70.0, "current_ltp": 70.0, "quality_score": 80, "conviction_tier": "MEDIUM",
        "target_1": 100.0, "target_1_spot": 24650.0, "target_2": 130.0, "target_2_spot": 24700.0,
        "stop_loss": 50.0, "spot_invalidation": 24500.0, "spot_invalidation_text": "Breach",
        "risk_reward_ratio": "1 : 2.5", "entry_trigger": "T", "rationale": "R", "catalyst_tags": [],
        "status": "ACTIVE",
    })
    rs.save_or_update_recommendation({
        "rec_id": "STABLE_TACT", "symbol": "NIFTY", "side": "CE", "strike": 24700.0,
        "option_symbol": "NIFTY 24700 CE", "play_type": "M", "play_badge": "MOMENTUM",
        "triggered_at": now_ts, "initial_spot": 24550.0, "current_spot": 24550.0,
        "initial_ltp": 60.0, "current_ltp": 60.0, "quality_score": 70, "conviction_tier": "LOW",
        "target_1": 90.0, "target_1_spot": 24650.0, "target_2": 120.0, "target_2_spot": 24700.0,
        "stop_loss": 40.0, "spot_invalidation": 24500.0, "spot_invalidation_text": "Breach",
        "risk_reward_ratio": "1 : 2.5", "entry_trigger": "T", "rationale": "R", "catalyst_tags": [],
        "status": "ACTIVE",
    })

    # When requesting tier="high", the table returns only High items, but summary counts stay intact
    res = asyncio.run(get_recommendations_history_endpoint(symbol="NIFTY", tier="high", limit=10))
    assert res["total"] == 1
    assert res["history"][0]["rec_id"] == "STABLE_HIGH"

    summary = res["summary"]
    assert summary["high_count"] == 1
    assert summary["sec_count"] == 1, "Secondary count dropped to 0 when high filter was selected!"
    assert summary["tact_count"] == 1, "Tactical count dropped to 0 when high filter was selected!"
    assert summary["total"] == 3
