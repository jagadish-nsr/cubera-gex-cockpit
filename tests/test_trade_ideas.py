"""tests/test_trade_ideas.py — Tests for Top 3 CE/PE Options Recommendation Engine."""

import pytest
from analytics.trade_ideas import generate_trade_recommendations, TradeIdea, RecommendationBatch
from gex.levels import Levels
import pandas as pd


def _mock_levels(spot=24500.0, cw=24700.0, pw=24300.0, flip=24520.0):
    return Levels(
        spot=spot,
        call_wall=cw,
        put_wall=pw,
        magnet=spot,
        flip=flip,
        max_pain=spot,
        total_net_gex=100000.0,
        regime="CALM",
        dealer_sign_model="default",
        expiry_window="weekly",
        oi_timestamp=None,
        oi_stale=False,
        source="demo",
    )


class MockDashboardData:
    def __init__(self, spot=24500.0, symbol="NIFTY"):
        self.symbol = symbol
        self.levels = _mock_levels(spot=spot)
        self.oi_strikes = [
            {"strike": spot - 100, "call_ltp": 160.0, "put_ltp": 70.0, "call_oi": 50000, "put_oi": 120000, "call_doi": 1200, "put_doi": 5000},
            {"strike": spot - 50, "call_ltp": 125.0, "put_ltp": 95.0, "call_oi": 60000, "put_oi": 100000, "call_doi": 1800, "put_doi": 4000},
            {"strike": spot, "call_ltp": 95.0, "put_ltp": 95.0, "call_oi": 150000, "put_oi": 150000, "call_doi": 3500, "put_doi": 3200},
            {"strike": spot + 50, "call_ltp": 70.0, "put_ltp": 125.0, "call_oi": 110000, "put_oi": 70000, "call_doi": 4500, "put_doi": 1500},
            {"strike": spot + 100, "call_ltp": 50.0, "put_ltp": 160.0, "call_oi": 180000, "put_oi": 40000, "call_doi": 6000, "put_doi": 800},
            {"strike": spot + 200, "call_ltp": 25.0, "put_ltp": 230.0, "call_oi": 250000, "put_oi": 20000, "call_doi": 9500, "put_doi": 200},
        ]
        self.contracts = None


def test_generate_recommendations_basic():
    data = MockDashboardData(spot=24500.0, symbol="NIFTY")
    batch = generate_trade_recommendations(data, min_score=75)

    assert batch.__class__.__name__ == "RecommendationBatch"
    assert batch.symbol == "NIFTY"
    assert batch.spot == 24500.0
    assert batch.min_score_filter == 75

    # Check that all 7 candidate strategies are evaluated for CE and PE
    assert len(batch.all_ce_candidates) == 7
    assert len(batch.all_pe_candidates) == 7

    expected_badges = {"MOMENTUM", "STRUCTURE", "GAMMA SQUEEZE", "ICEBERG FADE", "VANNA-CHARM", "VOL DISPERSION", "RATIO REVERSAL"}
    assert set(t.play_badge for t in batch.all_ce_candidates) == expected_badges
    assert set(t.play_badge for t in batch.all_pe_candidates) == expected_badges

    # Check that filtered recommendations only include setups with score >= 75
    for t in batch.ce_recommendations:
        assert t.quality_score >= 75
        assert "HIGH CONVICTION" in t.conviction_label
    for t in batch.pe_recommendations:
        assert t.quality_score >= 75
        assert "HIGH CONVICTION" in t.conviction_label

    # Verify descending sort order by quality score
    ce_scores = [t.quality_score for t in batch.ce_recommendations]
    assert ce_scores == sorted(ce_scores, reverse=True)
    pe_scores = [t.quality_score for t in batch.pe_recommendations]
    assert pe_scores == sorted(pe_scores, reverse=True)

    for t in batch.all_ce_candidates + batch.all_pe_candidates:
        assert t.__class__.__name__ == "TradeIdea"
        assert t.current_ltp > 0.0
        assert t.stop_loss < t.current_ltp
        assert t.target_1 > t.current_ltp
        assert t.target_2 > t.target_1
        assert 0 <= t.quality_score <= 100
        assert len(t.rationale) > 10
        assert len(t.entry_trigger) > 5


def test_generate_recommendations_failsoft_empty_data():
    batch = generate_trade_recommendations(None, min_score=0)
    assert batch.symbol == "NIFTY"
    assert len(batch.all_ce_candidates) == 7
    assert len(batch.all_pe_candidates) == 7
    for t in batch.all_ce_candidates:
        assert t.current_ltp > 0.0


def test_generate_recommendations_bias_influence():
    data = MockDashboardData(spot=24500.0)

    class MockBias:
        score = 6.5
        regime = "Bullish"

    batch_bull = generate_trade_recommendations(data, bias_result=MockBias(), min_score=0)
    
    class MockBearBias:
        score = -6.5
        regime = "Bearish"

    batch_bear = generate_trade_recommendations(data, bias_result=MockBearBias(), min_score=0)

    avg_bull_ce = sum(t.quality_score for t in batch_bull.all_ce_candidates) / 7
    avg_bear_ce = sum(t.quality_score for t in batch_bear.all_ce_candidates) / 7
    assert avg_bull_ce > avg_bear_ce


def test_generate_recommendations_score_filter():
    data = MockDashboardData(spot=24500.0)
    
    # Threshold = 75: only >= 75
    batch_75 = generate_trade_recommendations(data, min_score=75)
    for t in batch_75.ce_recommendations + batch_75.pe_recommendations:
        assert t.quality_score >= 75

    # Threshold = 0: returns all 7
    batch_0 = generate_trade_recommendations(data, min_score=0)
    assert len(batch_0.ce_recommendations) == 7
    assert len(batch_0.pe_recommendations) == 7

    # Threshold = 99: empty if no setups reach 99
    batch_99 = generate_trade_recommendations(data, min_score=99)
    assert len(batch_99.ce_recommendations) == 0
    assert "Capital preservation" in batch_99.executive_summary


def test_position_sizing_basic():
    from analytics.trade_ideas import compute_position_size, PositionSizeInfo

    idea = TradeIdea(
        symbol="NIFTY",
        side="CE",
        play_type="ATM Momentum",
        play_badge="MOMENTUM",
        strike=24500.0,
        option_symbol="NIFTY 24500 CE",
        current_ltp=150.0,
        delta_approx=0.50,
        entry_trigger="VWAP test",
        spot_invalidation=24450.0,
        spot_invalidation_text="Below VWAP",
        target_1=200.0,
        target_1_spot=24550.0,
        target_2=250.0,
        target_2_spot=24600.0,
        stop_loss=110.0,  # 40 pts risk per share
        risk_reward_ratio="1 : 2.5",
        quality_score=85,  # Grade A+
        conviction_label="HIGH CONVICTION",
        conviction_color="green",
        rationale="Strong momentum",
    )

    # 40 pts risk * 65 lot size = 2600 INR risk per lot
    # Account 500k, 1.5% risk = 7500 INR max risk
    # Raw lots = 7500 // 2600 = 2 lots
    ps = compute_position_size(idea, account_capital=500_000.0, risk_pct=1.5, lot_size=65)
    assert isinstance(ps, PositionSizeInfo)
    assert ps.suggested_lots == 2
    assert ps.total_quantity == 130
    assert ps.capital_outlay_inr == 130 * 150.0
    assert ps.max_risk_inr == 130 * (150.0 - 110.0)
    assert ps.target_1_pnl_inr == 130 * (200.0 - 150.0)
    assert ps.target_2_pnl_inr == 130 * (250.0 - 150.0)
    assert not ps.is_outlay_capped


def test_position_sizing_outlay_cap():
    from analytics.trade_ideas import compute_position_size

    # Very expensive option
    idea = TradeIdea(
        symbol="NIFTY",
        side="CE",
        play_type="ATM Momentum",
        play_badge="MOMENTUM",
        strike=24500.0,
        option_symbol="NIFTY 24500 CE",
        current_ltp=500.0,
        delta_approx=0.50,
        entry_trigger="VWAP test",
        spot_invalidation=24450.0,
        spot_invalidation_text="Below VWAP",
        target_1=700.0,
        target_1_spot=24550.0,
        target_2=900.0,
        target_2_spot=24600.0,
        stop_loss=480.0,  # tight stop: 20 pts
        risk_reward_ratio="1 : 10",
        quality_score=90,
        conviction_label="HIGH CONVICTION",
        conviction_color="green",
        rationale="Deep ITM test",
    )

    # Account 100k, 15% outlay cap = 15k
    # 1 lot = 65 * 500 = 32,500 > 15,000
    ps = compute_position_size(idea, account_capital=100_000.0, risk_pct=3.0, lot_size=65, max_outlay_pct=0.15)
    assert ps.suggested_lots == 1
    assert ps.is_outlay_capped


def test_batch_includes_position_sizes():
    data = MockDashboardData(spot=24500.0)
    batch = generate_trade_recommendations(data)
    for t in batch.ce_recommendations + batch.pe_recommendations:
        assert t.position_size is not None
        assert t.position_size.suggested_lots >= 1
        assert t.position_size.capital_outlay_inr > 0
        assert t.position_size.max_risk_inr > 0


def test_live_spot_delta_adjustment():
    from analytics.trade_ideas import _lookup_chain_data

    # Chain snapshot at spot 24500: CE LTP=120, delta=0.50
    oi_map = {
        24500.0: {
            "strike": 24500.0,
            "call_ltp": 120.0,
            "call_oi": 50000.0,
            "call_doi": 5000.0,
            "put_ltp": 115.0,
            "put_oi": 60000.0,
            "put_doi": 6000.0,
        }
    }
    contracts_map = {
        ("C", 24500.0): (120.0, 0.50, 50000.0, 5000.0),
        ("P", 24500.0): (115.0, -0.48, 60000.0, 6000.0),
    }

    # At identical spot (chain_spot=24500, live spot=24500): no adjustment
    ltp_c, delta_c, _, _, is_ws = _lookup_chain_data(
        24500.0, True, None, None, spot=24500.0, step=50,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=24500.0
    )
    assert ltp_c == 120.0
    assert delta_c == 0.50
    assert is_ws is False

    # Live spot ticks up by +20 points (spot=24520):
    # Call moves up by delta * 20 = 0.50 * 20 = +10.0 -> LTP = 130.0
    ltp_c_tick, _, _, _, _ = _lookup_chain_data(
        24500.0, True, None, None, spot=24520.0, step=50,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=24500.0
    )
    assert ltp_c_tick == 130.0

    # Put moves down by delta * 20 = -0.48 * 20 = -9.6 -> LTP = 105.4
    ltp_p_tick, _, _, _, _ = _lookup_chain_data(
        24500.0, False, None, None, spot=24520.0, step=50,
        oi_map=oi_map, contracts_map=contracts_map, chain_spot=24500.0
    )
    assert ltp_p_tick == 105.4


def test_ws_option_tick_takes_priority(monkeypatch):
    from analytics.trade_ideas import _lookup_chain_data
    from app.spot_ticker import push_ws_option_tick

    # Simulate live WebSocket trade tick arriving from exchange
    push_ws_option_tick("NIFTY", 24500.0, "CE", {"ltp": 142.5, "oi": 75000.0})

    ltp_ws, delta, oi, _, is_ws = _lookup_chain_data(
        24500.0, True, None, None, spot=24500.0, step=50,
        chain_spot=24500.0, symbol="NIFTY"
    )
    assert is_ws is True
    assert ltp_ws == 142.5
    assert oi == 75000.0


def test_higher_conviction_and_fallback_gating():
    data = MockDashboardData(spot=24500.0)
    batch = generate_trade_recommendations(data)

    # Check that candidates have rec_id, triggered_at, and conviction_tier
    for t in batch.all_ce_candidates + batch.all_pe_candidates:
        assert t.rec_id != ""
        assert t.triggered_at != ""
        assert t.conviction_tier in ("HIGH", "MEDIUM", "LOW")

    # If higher conviction (> 85) setups exist, fallback is False and all displayed have score > 85
    if batch.higher_ce_recommendations:
        assert batch.ce_fallback_active is False
        for t in batch.ce_recommendations:
            assert t.quality_score > 85
    elif batch.lower_ce_recommendations:
        # Fallback mode active: all displayed have 75 <= score <= 85
        assert batch.ce_fallback_active is True
        for t in batch.ce_recommendations:
            assert 75 <= t.quality_score <= 85


def test_bookmarking_and_recommendations_store():
    import data.journal.recommendations_store as rs
    data = MockDashboardData(spot=24500.0)
    batch = generate_trade_recommendations(data)

    if batch.all_ce_candidates:
        sample = batch.all_ce_candidates[0]
        # Toggle bookmark
        new_state = rs.toggle_bookmark(sample.rec_id)
        assert new_state is True
        assert rs.is_bookmarked(sample.rec_id) is True

        bm_list = rs.list_bookmarked_records("NIFTY")
        assert any(r["rec_id"] == sample.rec_id for r in bm_list)

        # Toggle bookmark off
        off_state = rs.toggle_bookmark(sample.rec_id)
        assert off_state is False
        assert rs.is_bookmarked(sample.rec_id) is False




