"""Pinned cards keep their stored tier and do not invent a live premium.

The recommendation journal under data/journal is the live book. These tests
use a temporary database.
"""

from unittest.mock import MagicMock, patch

import pytest

import analytics.trade_ideas as ideas
import data.journal.recommendations_store as rec_store
import web_server


@pytest.fixture
def pinned_db(tmp_path, monkeypatch):
    monkeypatch.setattr(rec_store, "_DB_PATH", tmp_path / "recommendations.db")
    rec_store._CACHE_INITIALIZED = False
    with rec_store._BOOKMARK_CACHE_LOCK:
        rec_store._BOOKMARK_CACHE.clear()
    ideas._MEM_SCORE_HISTORY.clear()
    ideas.clear_latch_manager()
    yield rec_store
    ideas.flush_telemetry(timeout=2.0)
    ideas._MEM_SCORE_HISTORY.clear()
    ideas.clear_latch_manager()
    rec_store._CACHE_INITIALIZED = False
    with rec_store._BOOKMARK_CACHE_LOCK:
        rec_store._BOOKMARK_CACHE.clear()


def _pin(**overrides) -> dict:
    row = {
        "rec_id": "NIFTY_CE_11150_MOMENTUM",
        "symbol": "NIFTY",
        "side": "CE",
        "strike": 11150.0,
        "option_symbol": "NIFTY 11150 CE",
        "play_type": "ATM Momentum Play",
        "play_badge": "MOMENTUM",
        "triggered_at": "2026-10-29 10:00:00",
        "initial_spot": 24500.0,
        "initial_ltp": 120.0,
        "quality_score": 60,
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
        "rationale": "Pinned setup",
        "catalyst_tags": ["RVOL"],
        "is_bookmarked": 1,
    }
    row.update(overrides)
    return row


def _save(store, row: dict) -> None:
    store.ensure_schema()
    store.save_or_update_recommendation(row)
    if not store.is_bookmarked(row["rec_id"]):
        store.toggle_bookmark(row["rec_id"])


def _batch(spot: float = 24510.0):
    mock_data = MagicMock()
    mock_data.symbol = "NIFTY"
    mock_data.vanna_now = 0.0
    mock_data.charm_now = 0.0
    mock_data.dte_now = 2.0
    mock_data.india_vix = {"last": 13.0}
    mock_data.raw_agg = {"pcr": 1.1}
    mock_data.levels = MagicMock(
        spot=spot, call_wall=24600.0, put_wall=24400.0, gamma_flip=24480.0,
    )
    with patch("analytics.trade_ideas._evaluate_ce_strategies", return_value=[]), \
         patch("analytics.trade_ideas._evaluate_pe_strategies", return_value=[]):
        return ideas.generate_trade_recommendations(
            data=mock_data,
            spot_override=spot,
            min_score=75,
        )


def test_pinned_tactical_card_stays_active_when_score_fades(pinned_db):
    row = _pin()
    _save(pinned_db, row)
    batch = _batch()

    pinned = next(t for t in batch.tactical_ce_recommendations if t.rec_id == row["rec_id"])
    assert pinned.is_bookmarked is True
    assert pinned.conviction_tier == "LOW"
    assert pinned.triggered_at == "2026-10-29 10:00:00"
    assert pinned.net_ev_pts == 0.0
    assert pinned.p_touch_target_1 == 0.0
    assert pinned.delta_approx == 0.0
    assert pinned.quote_source == "entry"
    assert batch.ce_tactical_active is True
    assert pinned.rec_id not in {t.rec_id for t in batch.higher_ce_recommendations}


def test_faded_high_pin_stays_in_its_stored_tier(pinned_db):
    row = _pin(
        rec_id="NIFTY_CE_11250_STRUCTURE",
        strike=11250.0,
        option_symbol="NIFTY 11250 CE",
        play_badge="STRUCTURE",
        play_type="Key Wall",
        quality_score=60,
        conviction_tier="HIGH",
        initial_ltp=150.0,
    )
    _save(pinned_db, row)
    batch = _batch()

    pinned = next(t for t in batch.higher_ce_recommendations if t.rec_id == row["rec_id"])
    assert pinned.conviction_tier == "HIGH"
    assert pinned.quality_score == 60
    assert pinned.triggered_at == row["triggered_at"]
    assert all(t.rec_id != row["rec_id"] for t in batch.tactical_ce_recommendations)
    assert all(t.rec_id != row["rec_id"] for t in batch.lower_ce_recommendations)

    ideas.flush_telemetry(timeout=2.0)
    faded = dict(row)
    faded["quality_score"] = 58
    faded["conviction_tier"] = "LOW"
    pinned_db.save_or_update_recommendation(faded)
    stored = pinned_db.get_bookmarked_record(row["rec_id"])
    assert stored["conviction_tier"] == "HIGH"
    assert int(stored["current_score"]) == 58


def test_breached_pin_leaves_every_tier(pinned_db):
    row = _pin(spot_invalidation=24550.0)
    _save(pinned_db, row)
    batch = _batch(spot=24510.0)
    gone = row["rec_id"]
    assert all(t.rec_id != gone for t in batch.higher_ce_recommendations)
    assert all(t.rec_id != gone for t in batch.lower_ce_recommendations)
    assert all(t.rec_id != gone for t in batch.tactical_ce_recommendations)


def test_missing_put_invalidation_is_not_a_breach(pinned_db):
    row = _pin(
        rec_id="NIFTY_PE_11350_STRUCTURE",
        side="PE",
        strike=11350.0,
        option_symbol="NIFTY 11350 PE",
        play_badge="STRUCTURE",
        spot_invalidation=0.0,
        quality_score=70,
        conviction_tier="LOW",
    )
    _save(pinned_db, row)
    batch = _batch(spot=52000.0)
    pinned = next(t for t in batch.tactical_pe_recommendations if t.rec_id == row["rec_id"])
    assert pinned.latch_state != "INVALIDATED"


def test_one_bad_bookmark_does_not_drop_the_others(pinned_db):
    good = _pin(rec_id="NIFTY_CE_11450_MOMENTUM", strike=11450.0, option_symbol="NIFTY 11450 CE")
    bad = _pin(rec_id="NIFTY_CE_11550_MOMENTUM", strike=11550.0, option_symbol="NIFTY 11550 CE")
    _save(pinned_db, good)
    _save(pinned_db, bad)
    pinned_db._CACHE_INITIALIZED = False
    with pinned_db._BOOKMARK_CACHE_LOCK:
        pinned_db._BOOKMARK_CACHE.clear()
    with pinned_db._get_write_conn() as conn:
        conn.execute(
            "UPDATE recommendation_records SET payload_json = ?, catalyst_tags_json = ? WHERE rec_id = ?",
            ("{not-json", "{not-json", bad["rec_id"]),
        )
        conn.commit()

    batch = _batch()
    ids = {t.rec_id for t in batch.tactical_ce_recommendations}
    assert good["rec_id"] in ids
    assert bad["rec_id"] in ids


def test_pinned_premium_uses_a_real_quote_or_marks_the_entry():
    record = {
        "initial_ltp": 240.0,
        "target_1": 320.0,
        "target_2": 400.0,
        "stop_loss": 180.0,
        "spot_invalidation": 51650.0,
        "spot_invalidation_text": "Spot breaches 51650",
        "current_score": 72,
    }
    stale = MagicMock()
    stale.initial_ltp = 999.0
    stale.current_ltp = 310.0
    stale.quote_source = "entry"
    stale.is_ws_live = False
    stale.quality_score = 60
    stale.target_1 = 1.0
    stale.target_2 = 1.0
    stale.stop_loss = 1.0
    stale.spot_invalidation = 1.0
    stale.spot_invalidation_text = ""

    estimated = web_server.resolve_pinned_card_fields(record, stale, ws_ltp=0.0)
    assert estimated["entry_ltp"] == 240.0
    assert estimated["current_ltp"] == 240.0
    assert estimated["ltp_estimated"] is True
    assert estimated["quote_source"] == "entry"
    assert estimated["target_1"] == 320.0
    assert estimated["spot_invalidation"] == 51650.0
    assert estimated["quality_score"] == 60

    chain = MagicMock()
    chain.initial_ltp = 999.0
    chain.current_ltp = 255.0
    chain.quote_source = "chain"
    chain.is_ws_live = False
    chain.quality_score = 72
    chain.target_1 = 330.0
    chain.target_2 = 410.0
    chain.stop_loss = 180.0
    chain.spot_invalidation = 51650.0
    chain.spot_invalidation_text = "live"
    live = web_server.resolve_pinned_card_fields(record, chain, ws_ltp=0.0)
    assert live["current_ltp"] == 255.0
    assert live["entry_ltp"] == 240.0
    assert live["ltp_estimated"] is False
    assert live["quote_source"] == "chain"

    quoted = web_server.resolve_pinned_card_fields(record, chain, ws_ltp=188.5)
    assert quoted["current_ltp"] == 188.5
    assert quoted["ltp_estimated"] is False
    assert quoted["quote_source"] == "ws"


def test_entry_sourced_pin_is_not_marked_with_a_fixed_delta():
    idea = ideas.TradeIdea(
        symbol="NIFTY",
        side="CE",
        play_type="ATM Momentum Play",
        play_badge="MOMENTUM",
        strike=11150.0,
        option_symbol="NIFTY 11150 CE",
        current_ltp=120.0,
        delta_approx=0.0,
        entry_trigger="stored",
        spot_invalidation=24420.0,
        spot_invalidation_text="below 24420",
        target_1=160.0,
        target_1_spot=24600.0,
        target_2=200.0,
        target_2_spot=24700.0,
        stop_loss=90.0,
        risk_reward_ratio="1 : 2.5",
        quality_score=60,
        conviction_label="TACTICAL",
        conviction_color="var(--ink-mute)",
        rationale="pinned",
        initial_ltp=120.0,
        initial_spot=24500.0,
        quote_source="entry",
    )
    ideas.update_trade_idea_live_telemetry(idea, "NIFTY", 24600.0)
    assert idea.current_ltp == 120.0
    assert idea.quote_source == "entry"
    assert idea.initial_ltp == 120.0
    assert idea.stop_loss == 90.0
