"""Hand-checkable tests for the staged pre-open opening watch.

Never invents VWAP. Never calls generate_trade_recommendations.
"""

from __future__ import annotations

import math
from unittest.mock import patch

from analytics.pre_open_watch import _estimate_first_5m, build_pre_open_watch


def _discovered_pm(**overrides) -> dict:
    base = {
        "is_discovered": True,
        "discovered_spot": 24550.0,
        "prev_close": 24400.0,
        "gap_pts": 150.0,
        "gap_pct": 0.61,
        "call_wall": 24600.0,
        "put_wall": 24300.0,
        "gamma_flip": 24450.0,
        "gap_regime": "CALL_WALL_BREACH_SQUEEZE",
        "favored_play": "WATCH HOLD ABOVE CALL STRIKE",
    }
    base.update(overrides)
    return base


def test_undiscovered_unavailable():
    w = build_pre_open_watch({"is_discovered": False}, symbol="NIFTY", market_open=False)
    assert w["status"] == "UNAVAILABLE"
    assert w["structure"] is None
    assert w["side_hint"] == "NONE"


def test_none_pm_unavailable():
    w = build_pre_open_watch(None, symbol="NIFTY", market_open=False)
    assert w["status"] == "UNAVAILABLE"


def test_gap_up_into_pos_gamma_no_fade_no_structure():
    w = build_pre_open_watch(
        _discovered_pm(
            gap_regime="GAP_UP_INTO_POS_GAMMA",
            favored_play="MAP ONLY — NO FADE",
            discovered_spot=24500.0,
            call_wall=24650.0,
        ),
        symbol="NIFTY",
        market_open=False,
    )
    assert w["status"] == "STAGED"
    assert w["side_hint"] == "NONE"
    assert w["structure"] is None
    assert "fade" in w["title"].lower() or "MAP" in w["title"] or "no fade" in w["trigger"].lower()
    assert "24650" in w["trigger"] or "24,650" in w["trigger"]


def test_gap_down_into_pos_gamma_no_bounce_no_structure():
    w = build_pre_open_watch(
        _discovered_pm(
            gap_regime="GAP_DOWN_INTO_POS_GAMMA",
            favored_play="MAP ONLY — NO BOUNCE",
            discovered_spot=24200.0,
            gap_pts=-200.0,
        ),
        symbol="NIFTY",
        market_open=False,
    )
    assert w["side_hint"] == "NONE"
    assert w["structure"] is None


def test_call_wall_breach_ce_structure_when_iv():
    w = build_pre_open_watch(
        _discovered_pm(),
        symbol="NIFTY",
        market_open=False,
        atm_iv=0.14,
        dte_days=2.0,
    )
    assert w["status"] == "STAGED"
    assert w["side_hint"] == "CE"
    assert "24,600" in w["trigger"] or "24600" in w["trigger"]
    assert w["structure"] is not None
    assert w["structure"]["side"] == "CE"
    assert w["structure"]["buy_strike"] == 24600.0
    assert w["structure"]["short_strike"] == 24650.0  # beyond CW by one Nifty step
    assert w["structure"]["lot_size"] == 65
    assert w["structure"]["premium_note"] in ("priced", "unpriced")
    if w["structure"]["premium_note"] == "priced":
        assert w["structure"]["net_debit_pts"] is not None
        assert w["structure"]["max_loss_inr"] is not None


def test_call_wall_breach_structure_null_without_iv_still_has_strikes():
    w = build_pre_open_watch(
        _discovered_pm(),
        symbol="NIFTY",
        market_open=False,
        atm_iv=None,
        dte_days=None,
    )
    assert w["side_hint"] == "CE"
    assert w["structure"] is not None
    assert w["structure"]["premium_note"] == "unpriced"
    assert w["structure"]["buy_strike"] == 24600.0
    assert w["structure"]["short_strike"] == 24650.0


def test_put_cascade_pe_structure():
    w = build_pre_open_watch(
        _discovered_pm(
            gap_regime="PUT_WALL_BREAKDOWN_CASCADE",
            favored_play="WATCH HOLD BELOW PUT STRIKE",
            discovered_spot=24250.0,
            gap_pts=-150.0,
        ),
        symbol="NIFTY",
        market_open=False,
        atm_iv=0.16,
        dte_days=1.0,
    )
    assert w["side_hint"] == "PE"
    assert w["structure"] is not None
    assert w["structure"]["side"] == "PE"
    assert w["structure"]["buy_strike"] == 24300.0
    assert w["structure"]["short_strike"] == 24250.0  # beyond PW by one step down


def test_short_gamma_wall_test_map_only_structure_null():
    w = build_pre_open_watch(
        _discovered_pm(
            gap_regime="SHORT_GAMMA_WALL_TEST",
            favored_play="WATCH THE CALL STRIKE",
            discovered_spot=24590.0,
        ),
        symbol="NIFTY",
        market_open=False,
        atm_iv=0.14,
        dte_days=2.0,
    )
    assert w["side_hint"] == "NONE"
    assert w["structure"] is None
    assert "hold" in w["trigger"].lower() or "reject" in w["trigger"].lower()


def test_auction_balanced_none():
    w = build_pre_open_watch(
        _discovered_pm(
            gap_regime="AUCTION_BALANCED",
            favored_play="NO BOOK TRADE",
            gap_pts=5.0,
            discovered_spot=24405.0,
        ),
        symbol="NIFTY",
        market_open=False,
    )
    assert w["side_hint"] == "NONE"
    assert w["structure"] is None


def test_after_market_open_superseded():
    w = build_pre_open_watch(_discovered_pm(), symbol="NIFTY", market_open=True)
    assert w["status"] == "SUPERSEDED"
    assert "superseded" in w["title"].lower() or "Ideas" in w["confirm_after"]


def test_never_calls_generate_trade_recommendations():
    """Staged watch must not wake the Ideas publisher path."""
    with patch("analytics.trade_ideas.generate_trade_recommendations") as mock_gen:
        # Import after patch so any accidental import still hits the mock name
        build_pre_open_watch(
            _discovered_pm(),
            symbol="NIFTY",
            market_open=False,
            atm_iv=0.14,
            dte_days=2.0,
        )
        mock_gen.assert_not_called()


def test_banknifty_lot_and_step():
    w = build_pre_open_watch(
        _discovered_pm(
            call_wall=52000.0,
            put_wall=51000.0,
            discovered_spot=52100.0,
            prev_close=51500.0,
        ),
        symbol="BANKNIFTY",
        market_open=False,
        atm_iv=0.18,
        dte_days=5.0,
    )
    assert w["structure"] is not None
    assert w["structure"]["lot_size"] == 30
    assert w["structure"]["short_strike"] == 52100.0  # 52000 + 100
    assert w["structure"]["width"] == 100.0


def test_map_only_favored_kills_structure_even_on_ce_regime():
    """Defensive: MAP ONLY favored_play never invents a fade/bounce structure."""
    w = build_pre_open_watch(
        _discovered_pm(favored_play="MAP ONLY — NO FADE"),
        symbol="NIFTY",
        market_open=False,
        atm_iv=0.14,
        dte_days=2.0,
    )
    assert w["side_hint"] == "CE"  # regime still CE
    assert w["structure"] is None


def test_first_5m_hand_check_nifty_iv():
    """24500 × 0.12 → σ₅ = spot·IV·√(5/(375·252)) ≈ 21.4 pts."""
    open_px, iv = 24500.0, 0.12
    expected_sigma = open_px * iv * math.sqrt(5.0 / (375.0 * 252.0))
    est = _estimate_first_5m(open_px, iv)
    assert est is not None
    assert est["method"] == "iv_sigma_5m"
    assert abs(est["sigma_pts"] - round(expected_sigma, 1)) < 0.05
    assert abs(est["high"] - round(open_px + expected_sigma, 1)) < 0.05
    assert abs(est["low"] - round(open_px - expected_sigma, 1)) < 0.05
    assert 21.0 <= est["sigma_pts"] <= 22.0


def test_first_5m_null_without_iv():
    assert _estimate_first_5m(24500.0, None) is None
    assert _estimate_first_5m(24500.0, 0.0) is None
    w = build_pre_open_watch(_discovered_pm(), symbol="NIFTY", market_open=False, atm_iv=None)
    assert w["first_5m_estimate"] is None


def test_first_5m_attached_on_map_only_regime():
    """Map level — present even when structure is null."""
    w = build_pre_open_watch(
        _discovered_pm(
            gap_regime="GAP_UP_INTO_POS_GAMMA",
            favored_play="MAP ONLY — NO FADE",
            discovered_spot=24500.0,
            call_wall=24520.0,  # inside the ~±21 envelope
        ),
        symbol="NIFTY",
        market_open=False,
        atm_iv=0.12,
    )
    assert w["structure"] is None
    assert w["first_5m_estimate"] is not None
    assert w["first_5m_estimate"]["high"] >= 24520.0
    assert "Call-gamma" in w["first_5m_estimate"]["note"]


def test_undiscovered_has_no_first_5m():
    w = build_pre_open_watch({"is_discovered": False}, symbol="NIFTY", market_open=False, atm_iv=0.12)
    assert w.get("first_5m_estimate") is None


def test_structure_target_price_ce():
    """CE target spot = min(short, open+σ₅); long-leg premium repriced there."""
    open_px = 24620.0  # above CW 24600 — squeeze print
    w = build_pre_open_watch(
        _discovered_pm(discovered_spot=open_px, call_wall=24600.0),
        symbol="NIFTY",
        market_open=False,
        atm_iv=0.12,
        dte_days=2.0,
    )
    s = w["structure"]
    assert s is not None
    assert s["buy_strike"] == 24600.0
    assert s["short_strike"] == 24650.0
    assert s["target_spot"] is not None
    # open+σ₅ ≈ 24620+21.5 ≈ 24641.5; capped at short 24650 → ~24641.5
    assert s["target_spot"] <= s["short_strike"]
    assert s["target_spot"] >= open_px
    assert s["target_buy_pts"] is not None and s["target_buy_pts"] > 0
    assert s["target_net_pts"] is not None
    assert s["target_net_pts"] >= s.get("net_debit_pts") or s["target_net_pts"] >= 0


def test_structure_target_price_pe():
    w = build_pre_open_watch(
        _discovered_pm(
            gap_regime="PUT_WALL_BREAKDOWN_CASCADE",
            favored_play="WATCH HOLD BELOW PUT STRIKE",
            discovered_spot=24280.0,
            put_wall=24300.0,
            gap_pts=-120.0,
        ),
        symbol="NIFTY",
        market_open=False,
        atm_iv=0.14,
        dte_days=1.0,
    )
    s = w["structure"]
    assert s is not None
    assert s["side"] == "PE"
    assert s["target_spot"] is not None
    assert s["target_spot"] >= s["short_strike"]
    assert s["target_spot"] <= 24280.0
    assert s["target_buy_pts"] is not None
