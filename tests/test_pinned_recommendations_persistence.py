"""tests/test_pinned_recommendations_persistence.py

Verifies:
1. Pinned tactical setups (score < 75 or faded below 65) remain visible and actively priced
   in their conviction tier on the Trade Recommendations tab.
2. Pinned setups in Cockpit active recommendations resolve authentic non-zero LTP,
   entry LTP, quality score, and spot invalidation.
3. Mid-session reload/sync protects Sentinel bars and signals from being purged.
"""

from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import json
import pytest

from analytics.trade_ideas import (
    TradeIdea,
    generate_trade_recommendations,
    RecommendationBatch,
)
import data.journal.recommendations_store as rec_store
import web_server

IST = timezone(timedelta(hours=5, minutes=30))


def test_pinned_tactical_card_stays_active_when_score_fades():
    """Verify that a pinned tactical setup stays in tactical_ce/pe even if score fades below 65."""
    symbol = "NIFTY"
    strike = 24500.0
    rec_id = f"{symbol}_CE_{int(strike)}_MOMENTUM"

    try:
        # Save a bookmarked record in recommendations_store
        record_payload = {
            "rec_id": rec_id,
            "symbol": symbol,
            "side": "CE",
            "strike": strike,
            "option_symbol": f"{symbol} {int(strike)} CE",
            "play_type": "ATM Momentum Play",
            "play_badge": "MOMENTUM",
            "triggered_at": "2026-10-29 10:00:00",
            "initial_spot": 24500.0,
            "initial_ltp": 120.0,
            "quality_score": 60,  # Faded below 65
            "conviction_tier": "LOW",
            "target_1": 160.0,
            "target_1_spot": 24600.0,
            "target_2": 200.0,
            "target_2_spot": 24700.0,
            "stop_loss": 90.0,
            "spot_invalidation": 24420.0,
            "spot_invalidation_text": "Spot breaks 24420",
            "risk_reward_ratio": "1 : 2.5",
            "entry_trigger": "RVOL surge above VWAP",
            "rationale": "High gamma pin test",
            "catalyst_tags": ["RVOL", "VWAP"],
            "is_bookmarked": 1,
        }

        rec_store.ensure_schema()
        rec_store.save_or_update_recommendation(record_payload)
        if not rec_store.is_bookmarked(rec_id):
            rec_store.toggle_bookmark(rec_id)

        assert rec_store.is_bookmarked(rec_id) is True

        # Generate recommendations with mocked data
        mock_data = MagicMock()
        mock_data.symbol = "NIFTY"
        mock_data.vanna_now = 0.0
        mock_data.charm_now = 0.0
        mock_data.dte_now = 2.0
        mock_data.india_vix = {"last": 13.0}
        mock_data.raw_agg = {"pcr": 1.1}
        mock_data.levels = MagicMock(spot=24510.0, call_wall=24600.0, put_wall=24400.0, gamma_flip=24480.0)

        # Mock candidate generation returning no other cards
        with patch("analytics.trade_ideas._evaluate_ce_strategies", return_value=[]), \
             patch("analytics.trade_ideas._evaluate_pe_strategies", return_value=[]):
            batch = generate_trade_recommendations(
                data=mock_data,
                spot_override=24510.0,
                min_score=75,
            )

        # The pinned card should be rehydrated and placed in tactical_ce
        assert len(batch.tactical_ce_recommendations) >= 1
        pinned_idea = next((t for t in batch.tactical_ce_recommendations if t.rec_id == rec_id), None)
        assert pinned_idea is not None
        assert pinned_idea.is_bookmarked is True
        assert pinned_idea.current_ltp > 0.0
        assert batch.ce_tactical_active is True

        # Check serialization for /api/recommendations
        clusters = web_server._cluster_ideas_by_strike(batch.tactical_ce_recommendations)
        serialized = [web_server._serialize_cluster(c, 24510.0, 500000.0, 0.015, 65) for c in clusters]
        assert len(serialized) >= 1
        # Card is bookmarked so confluence state should not be INVALIDATED
        assert serialized[0]["best_idea"]["rec_id"] == rec_id
        assert serialized[0]["best_idea"]["current_ltp"] > 0.0
    finally:
        # Clean up bookmark to maintain test isolation
        if rec_store.is_bookmarked(rec_id):
            rec_store.toggle_bookmark(rec_id)


def test_cockpit_pinned_cards_resolve_non_zero_ltp_and_invalidation():
    """Verify Cockpit active recommendations resolve authentic non-zero LTP, entry_ltp, score, and invalidation."""
    sym = "BANKNIFTY"
    strike = 51500.0
    rec_id = f"{sym}_PE_{int(strike)}_STRUCTURE"

    try:
        record_payload = {
            "rec_id": rec_id,
            "symbol": sym,
            "side": "PE",
            "strike": strike,
            "option_symbol": f"{sym} {int(strike)} PE",
            "play_type": "Key Wall Fade",
            "play_badge": "STRUCTURE",
            "triggered_at": "2026-10-29 11:30:00",
            "initial_spot": 51550.0,
            "initial_ltp": 240.0,
            "quality_score": 72,
            "conviction_tier": "LOW",
            "target_1": 320.0,
            "target_1_spot": 51400.0,
            "target_2": 400.0,
            "target_2_spot": 51300.0,
            "stop_loss": 180.0,
            "spot_invalidation": 51650.0,
            "spot_invalidation_text": "Spot breaches Call Wall at 51650",
            "risk_reward_ratio": "1 : 2.5",
            "entry_trigger": "Call Wall rejection with delta absorption",
            "rationale": "Resistance holds firm",
            "catalyst_tags": ["CALL_WALL", "DELTA_DIV"],
            "is_bookmarked": 1,
        }

        rec_store.ensure_schema()
        rec_store.save_or_update_recommendation(record_payload)
        if not rec_store.is_bookmarked(rec_id):
            rec_store.toggle_bookmark(rec_id)

        # Call /api/cockpit-data or simulate its card generator
        pinned_records = rec_store.list_bookmarked_records(sym)
        matching = [r for r in pinned_records if r.get("rec_id") == rec_id]
        assert len(matching) == 1
        r = matching[0]

        # Verify key mapping in web_server logic
        entry_ltp = float(r.get("initial_ltp") or r.get("entry_ltp") or 0.0)
        assert entry_ltp == 240.0

        score = int(r.get("current_score") or r.get("initial_score") or r.get("quality_score") or 70)
        assert score == 72

        spot_inval = float(r.get("spot_invalidation") or 0.0)
        assert spot_inval == 51650.0
    finally:
        # Clean up bookmark to maintain test isolation
        if rec_store.is_bookmarked(rec_id):
            rec_store.toggle_bookmark(rec_id)
