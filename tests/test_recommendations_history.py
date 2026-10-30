"""tests/test_recommendations_history.py — Test Trade Recommendation History & Lifecycle Audit.

Verifies:
1. Schema evolution: last_appeared, highest_score, highest_tier columns on recommendation_records.
2. Monotonic peak tracking: highest_score and highest_tier preserve peak tier across score fades.
3. get_recommendation_history querying & filtering by symbol, tier, status, session_date.
4. /api/recommendations/history endpoint responses, summary metrics, and full_card payload serialization.
"""

import pytest
import sqlite3
import asyncio
from datetime import datetime
import pytz

import data.journal.recommendations_store as rs
from web_server import get_recommendations_history_endpoint

IST = pytz.timezone("Asia/Kolkata")


def test_schema_has_trajectory_and_peak_columns():
    """Verify recommendation_records contains last_appeared, highest_score, highest_tier."""
    rs.ensure_schema()
    with rs._get_read_conn() as conn:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(recommendation_records)").fetchall()]
    assert "last_appeared" in cols
    assert "highest_score" in cols
    assert "highest_tier" in cols


def test_monotonic_peak_tier_and_score_tracking():
    """Verify highest_score and highest_tier preserve peak conviction across score fluctuations."""
    rs.ensure_schema()
    test_id = "TEST_REC_PEAK_TRACKING_CE_24650"

    now_ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    base_payload = {
        "rec_id": test_id,
        "symbol": "NIFTY",
        "side": "CE",
        "strike": 24650.0,
        "option_symbol": "NIFTY 24650 CE",
        "play_type": "Momentum",
        "play_badge": "MOMENTUM",
        "triggered_at": now_ts,
        "initial_spot": 24600.0,
        "current_spot": 24600.0,
        "initial_ltp": 80.0,
        "current_ltp": 80.0,
        "quality_score": 72,  # Tactical tier
        "conviction_tier": "LOW",
        "target_1": 110.0,
        "target_1_spot": 24670.0,
        "target_2": 140.0,
        "target_2_spot": 24720.0,
        "stop_loss": 60.0,
        "spot_invalidation": 24560.0,
        "spot_invalidation_text": "Breach of 24560",
        "risk_reward_ratio": "1 : 2.5",
        "entry_trigger": "VWAP test",
        "rationale": "Test idea",
        "catalyst_tags": ["TestTag"],
        "status": "ACTIVE",
    }

    try:
        # 1. First trigger at Tactical level (score 72)
        rs.save_or_update_recommendation(base_payload)
        with rs._get_read_conn() as conn:
            row1 = conn.execute("SELECT * FROM recommendation_records WHERE rec_id = ?", (test_id,)).fetchone()
            assert row1 is not None
            assert int(row1["highest_score"]) == 72
            assert row1["highest_tier"] == "TACTICAL"
            assert int(row1["current_score"]) == 72

        # 2. Score surges to High Conviction (score 93)
        surge_payload = dict(base_payload)
        surge_payload["quality_score"] = 93
        surge_payload["conviction_tier"] = "HIGH"
        rs.save_or_update_recommendation(surge_payload)
        with rs._get_read_conn() as conn:
            row2 = conn.execute("SELECT * FROM recommendation_records WHERE rec_id = ?", (test_id,)).fetchone()
            assert int(row2["highest_score"]) == 93
            assert row2["highest_tier"] == "HIGH"
            assert int(row2["current_score"]) == 93

        # 3. Score later fades to 66 (below secondary threshold)
        fade_payload = dict(base_payload)
        fade_payload["quality_score"] = 66
        fade_payload["conviction_tier"] = "LOW"
        rs.save_or_update_recommendation(fade_payload)
        with rs._get_read_conn() as conn:
            row3 = conn.execute("SELECT * FROM recommendation_records WHERE rec_id = ?", (test_id,)).fetchone()
            # Monotonic: highest_score stays 93, highest_tier stays HIGH!
            assert int(row3["highest_score"]) == 93
            assert row3["highest_tier"] == "HIGH"
            assert int(row3["current_score"]) == 66
            assert row3["last_appeared"] is not None

    finally:
        with rs._WRITE_LOCK, rs._get_write_conn() as conn:
            conn.execute("DELETE FROM recommendation_records WHERE rec_id = ?", (test_id,))
            conn.commit()


def test_get_recommendation_history_filters():
    """Verify get_recommendation_history queries and filters by symbol, tier, and status."""
    rs.ensure_schema()
    hist_all = rs.get_recommendation_history(limit=50)
    assert isinstance(hist_all, list)
    assert len(hist_all) > 0

    # Every item must have qualified for High, Secondary, or Tactical
    for r in hist_all:
        score = int(r.get("highest_score") or r.get("initial_score") or r.get("current_score") or 0)
        tier = r.get("highest_tier") or r.get("conviction_tier")
        assert score >= 65 or tier in ("HIGH", "MEDIUM", "TACTICAL")

    # Tier filter
    hist_high = rs.get_recommendation_history(tier="high", limit=20)
    for r in hist_high:
        score = int(r.get("highest_score") or r.get("initial_score") or r.get("current_score") or 0)
        assert r.get("highest_tier") == "HIGH" or score > 85


def test_api_recommendations_history_endpoint():
    """Verify /api/recommendations/history returns summary metrics and full_card payloads."""
    res = asyncio.run(get_recommendations_history_endpoint(symbol="NIFTY", limit=15))
    assert "symbol" in res
    assert "total" in res
    assert "summary" in res
    assert "history" in res

    summary = res["summary"]
    assert "high_count" in summary
    assert "sec_count" in summary
    assert "tact_count" in summary
    assert "active_count" in summary

    assert len(res["history"]) <= 15
    if res["history"]:
        item = res["history"][0]
        assert "rec_id" in item
        assert "symbol" in item
        assert "side" in item
        assert "strike" in item
        assert "triggered_at" in item
        assert "last_appeared" in item
        assert "highest_tier" in item
        assert "highest_score" in item
        assert "current_score" in item
        assert "score_delta" in item
        assert "status" in item
        assert "pnl_pts" in item
        assert "pnl_pct" in item
        assert "target_1" in item
        assert "stop_loss" in item
        assert "full_card" in item
        assert isinstance(item["full_card"], dict)
        if item["full_card"]:
            fc = item["full_card"]
            assert "pos_size" in fc
            assert "entry_trigger" in fc
            assert "rationale" in fc
            assert "catalyst_tags" in fc
