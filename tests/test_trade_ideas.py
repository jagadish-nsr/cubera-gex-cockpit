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


def test_live_spot_delta_adjustment(monkeypatch):
    from analytics.trade_ideas import _lookup_chain_data
    monkeypatch.setattr("app.spot_ticker.get_option_ws_quote", lambda *a, **k: None)

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


def test_target_engine_reprice_and_probabilities():
    data = MockDashboardData(spot=24500.0)
    batch = generate_trade_recommendations(data, min_score=0)

    for idea in batch.all_ce_candidates + batch.all_pe_candidates:
        assert 0.0 < idea.p_touch_target_1 < 1.0
        assert 0.0 < idea.p_touch_sl < 1.0
        assert isinstance(idea.net_ev_pts, float)
        assert isinstance(idea.greek_attribution, dict)
        assert "delta_pnl" in idea.greek_attribution
        assert idea.target_1 > idea.current_ltp
        assert idea.target_2 > idea.target_1
        assert idea.stop_loss < idea.current_ltp
        assert "1 :" in idea.risk_reward_ratio


def test_delta_window_strike_selection():
    from analytics.trade_ideas import _select_strike_by_delta_window

    contracts_map = {
        ("C", 24450.0): (150.0, 0.62, 10000, 1000),
        ("C", 24500.0): (110.0, 0.51, 20000, 2000),
        ("C", 24550.0): (75.0, 0.39, 30000, 3000),
        ("C", 24600.0): (45.0, 0.27, 40000, 4000),
        ("C", 24650.0): (25.0, 0.18, 50000, 5000),
        ("P", 24500.0): (105.0, -0.49, 20000, 2000),
        ("P", 24450.0): (70.0, -0.38, 30000, 3000),
        ("P", 24400.0): (40.0, -0.26, 40000, 4000),
        ("P", 24350.0): (20.0, -0.17, 50000, 5000),
    }

    # Calls
    k_atm_c = _select_strike_by_delta_window(0.50, True, 24500.0, contracts_map, 50)
    assert k_atm_c == 24500.0  # 0.51 is closest to 0.50

    k_struct_c = _select_strike_by_delta_window(0.40, True, 24550.0, contracts_map, 50)
    assert k_struct_c == 24550.0  # 0.39 is closest to 0.40

    k_sq_c = _select_strike_by_delta_window(0.28, True, 24600.0, contracts_map, 50)
    assert k_sq_c == 24600.0  # 0.27 is closest to 0.28

    k_vc_c = _select_strike_by_delta_window(0.20, True, 24650.0, contracts_map, 50)
    assert k_vc_c == 24650.0  # 0.18 is closest to 0.20

    # Puts
    k_atm_p = _select_strike_by_delta_window(0.50, False, 24500.0, contracts_map, 50)
    assert k_atm_p == 24500.0  # |-0.49| = 0.49 closest to 0.50

    k_struct_p = _select_strike_by_delta_window(0.40, False, 24450.0, contracts_map, 50)
    assert k_struct_p == 24450.0  # |-0.38| = 0.38 closest to 0.40

    k_sq_p = _select_strike_by_delta_window(0.28, False, 24400.0, contracts_map, 50)
    assert k_sq_p == 24400.0  # |-0.26| = 0.26 closest to 0.28


def test_ev_quality_gating():
    from analytics.trade_ideas import _apply_ev_quality_gate

    # Case 1: Negative EV setup with high raw score (e.g. 88)
    # Must be clamped to max 74 (Grade B+), never reaching Grade A/A+
    adj_score, label, color = _apply_ev_quality_gate(88, ev=-2.5, pt1=0.35)
    assert adj_score == 74
    assert "TACTICAL OPPORTUNITY (Grade B+)" in label

    # Case 2: Zero EV setup
    adj_score2, label2, _ = _apply_ev_quality_gate(80, ev=0.0, pt1=0.40)
    assert adj_score2 == 74
    assert "TACTICAL OPPORTUNITY (Grade B+)" in label2

    # Case 3: Strong positive EV setup (EV >= 5.0, pt1 >= 0.45, raw score >= 75)
    # Must receive conviction bonus (+4 pts)
    adj_score3, label3, _ = _apply_ev_quality_gate(78, ev=8.2, pt1=0.52)
    assert adj_score3 == 82
    assert "HIGH CONVICTION (Grade A+)" in label3

    # Case 4: Moderate positive EV setup (EV = 2.0)
    # Retains raw score without bonus or penalty
    adj_score4, label4, _ = _apply_ev_quality_gate(76, ev=2.0, pt1=0.40)
    assert adj_score4 == 76
    assert "HIGH CONVICTION (Grade A)" in label4


def test_volatility_horizon_scaling():
    from analytics.trade_ideas import _calc_horizon_sigma

    spot = 24500.0
    eff_iv = 0.14
    step = 50

    # 30 min vs 90 min: sigma must grow with square root of horizon
    sigma_30 = _calc_horizon_sigma(spot, eff_iv, 30.0, step)
    sigma_90 = _calc_horizon_sigma(spot, eff_iv, 90.0, step)

    assert sigma_90 > sigma_30
    ratio = sigma_90 / sigma_30
    # sqrt(90 / 30) = sqrt(3) ~ 1.732
    import pytest
    assert ratio == pytest.approx(1.732, rel=0.05)


def test_scenario_confluence_scoring():
    from analytics.trade_ideas import _compute_trade_score

    class MockScenarioSet:
        dominant_scenario = "bull"
        no_edge = False

        def get_prob(self, name: str) -> float:
            return 0.55 if name == "bull" else 0.20

    scen = MockScenarioSet()

    # Call trade score aligned with Bull dominant scenario
    score_ce_aligned, _, _ = _compute_trade_score(
        "CE", "ATM Momentum", 3.0, 24500.0, 24500.0, 24700.0, 24300.0,
        None, 1000, scenario_set=scen
    )
    score_ce_base, _, _ = _compute_trade_score(
        "CE", "ATM Momentum", 3.0, 24500.0, 24500.0, 24700.0, 24300.0,
        None, 1000, scenario_set=None
    )
    assert score_ce_aligned > score_ce_base

    # Put trade score counter to Bull dominant scenario
    score_pe_counter, _, _ = _compute_trade_score(
        "PE", "ATM Momentum", -3.0, 24500.0, 24500.0, 24700.0, 24300.0,
        None, 1000, scenario_set=scen
    )
    score_pe_base, _, _ = _compute_trade_score(
        "PE", "ATM Momentum", -3.0, 24500.0, 24500.0, 24700.0, 24300.0,
        None, 1000, scenario_set=None
    )
    assert score_pe_counter < score_pe_base


def test_compute_trend_context():
    from analytics.trade_ideas import compute_trend_context

    class MockBar:
        def __init__(self, close, high, low):
            self.close = close
            self.high = high
            self.low = low

    # 1. Bullish sequence where close rises steadily
    bull_bars = [MockBar(close=24500 + i * 5, high=24500 + i * 5 + 4, low=24500 + i * 5 - 3) for i in range(25)]
    ctx_bull = compute_trend_context(bull_bars)
    assert ctx_bull["ema_trend"] == "BULLISH"
    assert ctx_bull["ema9"] is not None
    assert ctx_bull["ema21"] is not None
    assert ctx_bull["ema9"] > ctx_bull["ema21"]
    assert ctx_bull["atr"] > 0

    # 2. Bearish sequence where close falls steadily
    bear_bars = [MockBar(close=24600 - i * 5, high=24600 - i * 5 + 3, low=24600 - i * 5 - 4) for i in range(25)]
    ctx_bear = compute_trend_context(bear_bars)
    assert ctx_bear["ema_trend"] == "BEARISH"
    assert ctx_bear["ema9"] < ctx_bear["ema21"]

    # 3. Empty or short bars fallback safely
    ctx_empty = compute_trend_context([])
    assert ctx_empty["ema_trend"] == "NEUTRAL"
    assert ctx_empty["atr"] == 35.0


def test_ema_trend_influence_on_trade_score():
    from analytics.trade_ideas import _compute_trade_score

    # CE Momentum with Bullish EMA trend vs Bearish EMA trend
    score_ce_bull, _, _ = _compute_trade_score(
        "CE", "ATM Momentum", 2.0, 24500.0, 24500.0, 24700.0, 24300.0,
        None, 1000, ema_trend="BULLISH"
    )
    score_ce_bear, _, _ = _compute_trade_score(
        "CE", "ATM Momentum", 2.0, 24500.0, 24500.0, 24700.0, 24300.0,
        None, 1000, ema_trend="BEARISH"
    )
    # Bullish trend boosts CE momentum (+8 vs -12 penalty, 20 pts diff)
    assert score_ce_bull > score_ce_bear
    assert (score_ce_bull - score_ce_bear) == 20

    # PE Momentum with Bearish EMA trend vs Bullish EMA trend
    score_pe_bear, _, _ = _compute_trade_score(
        "PE", "ATM Momentum", -2.0, 24500.0, 24500.0, 24700.0, 24300.0,
        None, 1000, ema_trend="BEARISH"
    )
    score_pe_bull, _, _ = _compute_trade_score(
        "PE", "ATM Momentum", -2.0, 24500.0, 24500.0, 24700.0, 24300.0,
        None, 1000, ema_trend="BULLISH"
    )
    assert score_pe_bear > score_pe_bull
    assert (score_pe_bear - score_pe_bull) == 20


def test_continuous_sigmoid_vwap_no_cliff_oscillation():
    from analytics.trade_ideas import _compute_trade_score

    vwap = 24500.0
    # Micro-tick below VWAP vs micro-tick above VWAP (0.10 pt tick across VWAP)
    spot_below = vwap - 0.05
    spot_above = vwap + 0.05

    score_below, _, _ = _compute_trade_score(
        "CE", "ATM Momentum", 2.0, spot_below, vwap, 24700.0, 24300.0,
        None, 1000, atr=35.0
    )
    score_above, _, _ = _compute_trade_score(
        "CE", "ATM Momentum", 2.0, spot_above, vwap, 24700.0, 24300.0,
        None, 1000, atr=35.0
    )

    # In old binary scoring, crossing VWAP caused a 20-point cliff jump (-8 to +12).
    # With continuous sigmoid scoring, a 0.10 pt tick moves the score by <= 1 pt.
    diff = abs(score_above - score_below)
    assert diff <= 1, f"Expected smooth continuous transition near VWAP, got swing of {diff} pts"


def test_structural_atr_invalidation_widening():
    from analytics.trade_ideas import generate_trade_recommendations
    from tests.test_trade_ideas import MockDashboardData

    spot = 24500.0
    data = MockDashboardData(spot=spot)
    batch = generate_trade_recommendations(data, min_score=0)

    for rec in batch.all_ce_candidates:
        inval_dist = abs(spot - rec.spot_invalidation)
        # Structural invalidation must sit outside the normal 15-25 pt 5m noise band
        assert inval_dist >= 30.0, f"{rec.play_badge} CE invalidation {rec.spot_invalidation} is only {inval_dist} pts away"

    for rec in batch.all_pe_candidates:
        inval_dist = abs(spot - rec.spot_invalidation)
        assert inval_dist >= 30.0, f"{rec.play_badge} PE invalidation {rec.spot_invalidation} is only {inval_dist} pts away"


def test_compute_adx_regimes():
    from analytics.trade_ideas import compute_adx

    # 1. Insufficient bars -> fallback neutral
    adx_val, regime = compute_adx([])
    assert adx_val == 20.0
    assert regime == "NEUTRAL"

    # 2. Strong uptrend: consecutive expanding higher highs and higher lows
    trending_bars = []
    base_p = 24000.0
    for i in range(30):
        h = base_p + i * 20.0 + 15.0
        l = base_p + i * 20.0 - 5.0
        c = base_p + i * 20.0 + 10.0
        trending_bars.append({"high": h, "low": l, "close": c})

    adx_trend, regime_trend = compute_adx(trending_bars, period=14)
    assert adx_trend >= 25.0
    assert regime_trend == "TRENDING"

    # 3. Choppy range-bound oscillation
    chop_bars = []
    for i in range(40):
        offset = 5.0 if i % 2 == 0 else -5.0
        chop_bars.append({
            "high": 24500.0 + offset + 2.0,
            "low": 24500.0 + offset - 2.0,
            "close": 24500.0 + offset,
        })
    adx_chop, regime_chop = compute_adx(chop_bars, period=14)
    assert adx_chop < 20.0
    assert regime_chop == "CHOP"


def test_compute_cvd_divergence():
    from analytics.trade_ideas import compute_cvd_divergence

    # Insufficient bars
    assert compute_cvd_divergence([]) == "NONE"

    # Bullish Absorption: Price makes lower low, but cumulative delta makes higher low
    bullish_bars = [
        {"close": 24500.0, "delta": 100},
        {"close": 24480.0, "delta": -50},
        {"close": 24460.0, "delta": 300},  # Price down, CVD aggressive absorption
        {"close": 24450.0, "delta": 400},
        {"close": 24440.0, "delta": 500},
    ]
    assert compute_cvd_divergence(bullish_bars) == "BULLISH"

    # Bearish Absorption: Price makes higher high, but cumulative delta makes lower low / heavy selling
    bearish_bars = [
        {"close": 24400.0, "delta": -100},
        {"close": 24450.0, "delta": -200},
        {"close": 24480.0, "delta": -400},
        {"close": 24510.0, "delta": -600},
        {"close": 24530.0, "delta": -800},
    ]
    assert compute_cvd_divergence(bearish_bars) == "BEARISH"


def test_compute_sector_breadth_context():
    from analytics.trade_ideas import compute_sector_breadth_context

    # None or empty rows
    ctx = compute_sector_breadth_context(None)
    assert ctx["breadth"] == "NEUTRAL"

    # Bullish aligned: Banking, IT, Energy all positive
    bull_rows = [
        {"sector": "NIFTY BANK", "change_pct": 1.15},
        {"sector": "NIFTY IT", "change_pct": 0.85},
        {"sector": "NIFTY OIL & GAS", "change_pct": 0.60},
    ]
    ctx_bull = compute_sector_breadth_context(bull_rows)
    assert ctx_bull["breadth"] == "BULLISH"
    assert ctx_bull["bull_count"] >= 2

    # Bearish aligned: Heavy negative breadth
    bear_rows = [
        {"sector": "NIFTY BANK", "change_pct": -1.25},
        {"sector": "NIFTY IT", "change_pct": -0.70},
        {"sector": "NIFTY OIL & GAS", "change_pct": -0.90},
    ]
    ctx_bear = compute_sector_breadth_context(bear_rows)
    assert ctx_bear["breadth"] == "BEARISH"
    assert ctx_bear["bear_count"] >= 2


def test_compute_fii_positioning_context(monkeypatch):
    import analytics.trade_ideas as ti

    # Heavy long FII futures
    monkeypatch.setattr(
        "app.participant_oi.latest_available",
        lambda: ("2026-10-10", {"FII": {"fut_long": 100000, "fut_short": 40000}}),
    )
    ctx_long = ti.compute_fii_positioning_context()
    assert ctx_long["fii_bias"] == "BULLISH"
    assert ctx_long["net_fut"] == 60000

    # Heavy short FII futures
    monkeypatch.setattr(
        "app.participant_oi.latest_available",
        lambda: ("2026-10-10", {"FII": {"fut_long": 20000, "fut_short": 80000}}),
    )
    ctx_short = ti.compute_fii_positioning_context()
    assert ctx_short["fii_bias"] == "BEARISH"
    assert ctx_short["net_fut"] == -60000


def test_build_spread_alternative():
    from analytics.trade_ideas import _build_spread_alternative, TradeIdea

    idea_ce = TradeIdea(
        symbol="NIFTY",
        side="CE",
        play_type="ATM Momentum Play",
        play_badge="MOMENTUM",
        strike=24500.0,
        option_symbol="NIFTY 24500 CE",
        current_ltp=120.0,
        delta_approx=0.50,
        entry_trigger="Breakout",
        spot_invalidation=24450.0,
        spot_invalidation_text="Below 24450",
        target_1=160.0,
        target_1_spot=24600.0,
        target_2=200.0,
        target_2_spot=24700.0,
        stop_loss=80.0,
        risk_reward_ratio="1 : 2.0",
        quality_score=80,
        conviction_label="High",
        conviction_color="#22c55e",
        rationale="Strong momentum",
        rec_id="NIFTY_CE_24500_MOMENTUM",
    )

    contracts_map = {
        ("C", 24550.0): (70.0, 0.40, 24500.0, 100.0),
        ("P", 24450.0): (65.0, -0.40, 24500.0, 100.0),
    }

    # Bull Call Spread
    spread_ce = _build_spread_alternative(idea=idea_ce, contracts_map=contracts_map, step=50)
    assert spread_ce is not None
    assert spread_ce["spread_name"] == "Bull Call Spread"
    assert spread_ce["buy_strike"] == 24500.0
    assert spread_ce["sell_strike"] == 24550.0
    assert spread_ce["net_debit"] == 50.0  # 120 - 70
    assert spread_ce["breakeven_spot"] == 24550.0

    # Bear Put Spread
    idea_pe = TradeIdea(
        symbol="NIFTY",
        side="PE",
        play_type="ATM Momentum Play",
        play_badge="MOMENTUM",
        strike=24500.0,
        option_symbol="NIFTY 24500 PE",
        current_ltp=110.0,
        delta_approx=-0.50,
        entry_trigger="Breakdown",
        spot_invalidation=24550.0,
        spot_invalidation_text="Above 24550",
        target_1=150.0,
        target_1_spot=24400.0,
        target_2=190.0,
        target_2_spot=24300.0,
        stop_loss=70.0,
        risk_reward_ratio="1 : 2.0",
        quality_score=80,
        conviction_label="High",
        conviction_color="#ef4444",
        rationale="Strong momentum",
        rec_id="NIFTY_PE_24500_MOMENTUM",
    )
    spread_pe = _build_spread_alternative(idea=idea_pe, contracts_map=contracts_map, step=50)
    assert spread_pe is not None
    assert spread_pe["spread_name"] == "Bear Put Spread"
    assert spread_pe["buy_strike"] == 24500.0
    assert spread_pe["sell_strike"] == 24450.0
    assert spread_pe["net_debit"] == 45.0  # 110 - 65
    assert spread_pe["breakeven_spot"] == 24455.0  # 24500 - 45


def test_recommendation_latch_manager():
    from analytics.trade_ideas import RecommendationLatchManager, TradeIdea

    manager = RecommendationLatchManager(dwell_lease_s=90.0)
    idea = TradeIdea(
        symbol="NIFTY",
        side="CE",
        play_type="ATM Momentum Play",
        play_badge="MOMENTUM",
        strike=24500.0,
        option_symbol="NIFTY 24500 CE",
        current_ltp=100.0,
        delta_approx=0.50,
        target_1=140.0,
        target_2=180.0,
        stop_loss=75.0,
        risk_reward_ratio="1:2.0",
        conviction_label="High",
        conviction_color="#22c55e",
        quality_score=80,
        spot_invalidation=24450.0,
        target_1_spot=24600.0,
        target_2_spot=24700.0,
        entry_trigger="Breakout",
        spot_invalidation_text="Below 24450",
        rationale="Strong momentum",
        rec_id="NIFTY_CE_24500_MOMENTUM",
    )

    # 1. First evaluation: qualifies & becomes ACTIVE
    processed = manager.process_and_latch("NIFTY", 24500.0, [idea], min_score=75)
    assert processed[0].latch_state == "ACTIVE"

    # 2. Tick noise: quality score temporarily dips to 70 (>= 65), stays ACTIVE within lease
    idea.quality_score = 70
    processed = manager.process_and_latch("NIFTY", 24500.0, [idea], min_score=75)
    assert processed[0].latch_state == "ACTIVE"

    # 3. Instant Invalidation on spot breach (spot drops below 24450 for CE)
    processed = manager.process_and_latch("NIFTY", 24430.0, [idea], min_score=75)
    assert processed[0].latch_state == "INVALIDATED"

    # 4. Target Hit (spot rallies to target 1 spot 24620 >= 24600)
    idea.spot_invalidation = 24400.0  # reset inval
    processed = manager.process_and_latch("NIFTY", 24620.0, [idea], min_score=75)
    assert processed[0].latch_state == "TARGET_HIT"


def test_batch_includes_phase_2_and_3_enrichments():
    from analytics.trade_ideas import generate_trade_recommendations
    from tests.test_trade_ideas import MockDashboardData

    data = MockDashboardData(spot=24500.0)
    batch = generate_trade_recommendations(data, min_score=0)

    top_ce = batch.all_ce_candidates[0]
    assert top_ce.spread_alternative is not None
    assert "spread_name" in top_ce.spread_alternative
    assert top_ce.adx_regime in ("CHOP", "TRENDING", "NEUTRAL")
    assert top_ce.cvd_divergence in ("BULLISH_ABSORPTION", "BEARISH_ABSORPTION", "NONE")
    assert top_ce.latch_state in ("ACTIVE", "QUALIFIED", "TARGET_HIT", "INVALIDATED", "DEGRADED")


def test_target_bounds_and_realistic_rr():
    """Verify that target option pricing and R:R ratios are bounded within realistic intraday limits."""
    # Build a scenario with large spot to call wall distance (e.g. 2000 pts)
    rows = [
        {"strike": 22700.0, "type": "C", "mid": 73.3, "delta": 0.45, "oi": 1000000, "doi": 50000},
        {"strike": 22700.0, "type": "P", "mid": 80.0, "delta": -0.55, "oi": 1000000, "doi": 50000},
    ]
    df = pd.DataFrame(rows)
    levels = _mock_levels(spot=22650.0, cw=24600.0, pw=22500.0, flip=22650.0)
    data = MockDashboardData(spot=22650.0, symbol="NIFTY")
    data.levels = levels
    data.contracts = df

    batch = generate_trade_recommendations(data, min_score=0)
    assert batch is not None
    for idea in batch.all_recommendations:
        # T1 must not exceed 4x entry premium
        assert idea.target_1 <= idea.current_ltp * 4.05
        # T2 must not exceed 6.5x entry premium
        assert idea.target_2 <= idea.current_ltp * 6.55
        # R:R must be realistic (< 20.0, not 172.7!)
        rr_val = float(idea.risk_reward_ratio.split(":")[-1].strip())
        assert rr_val < 20.0


def test_off_market_hours_recommendation_gating(monkeypatch):
    """Verify that recommendation generation is gated off outside market hours."""
    from unittest.mock import patch
    import config
    from app.cockpit import _get_active_recommendation_batch, _REC_BATCH_CACHE

    _REC_BATCH_CACHE.clear()
    monkeypatch.setattr(config, "is_market_open", lambda: False)

    data = MockDashboardData(spot=24500.0, symbol="NIFTY")
    session_state = {"replay_mode_active": False}

    with patch("streamlit.session_state", session_state):
        # When market is closed and no cache exists, must return None without generating
        batch = _get_active_recommendation_batch("NIFTY", data)
        assert batch is None
        assert "NIFTY" not in _REC_BATCH_CACHE

        # When in replay mode, generation is allowed
        session_state["replay_mode_active"] = True
        batch_replay = _get_active_recommendation_batch("NIFTY", data)
        assert batch_replay is not None


