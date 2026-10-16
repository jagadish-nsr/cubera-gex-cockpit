"""Unit tests for analytics/strike_ladder.py."""

import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta

from analytics.strike_ladder import (
    compute_strike_ladder,
    StrikeLadderBundle,
    StrikeLadderRow,
    is_expiry_crossed,
    get_dynamic_active_expiries,
)


def test_compute_strike_ladder_slicing_and_monotonicity():
    # Construct a synthetic 40-strike chain for NIFTY
    strikes = [24000 + (i * 50) for i in range(40)]  # 24000 to 25950
    spot = 24963.0  # ATM will be 24950
    expiry = "2026-10-22"

    rows = []
    for k in strikes:
        rows.append({"expiry": expiry, "strike": float(k), "type": "CE", "mid": 120.0, "iv": 0.14})
        rows.append({"expiry": expiry, "strike": float(k), "type": "PE", "mid": 95.0, "iv": 0.15})
    df = pd.DataFrame(rows)

    bundle = compute_strike_ladder(
        symbol="NIFTY",
        spot=spot,
        contracts_df=df,
        expiry=expiry,
        horizon_minutes=45,
        auto_subscribe_ws=False,
    )

    assert isinstance(bundle, StrikeLadderBundle)
    assert bundle.symbol == "NIFTY"
    assert bundle.atm_strike == 24950.0
    assert len(bundle.rows) == 11

    # Check that rows are ordered ascending
    ladder_strikes = [r.strike for r in bundle.rows]
    assert ladder_strikes == sorted(ladder_strikes)
    assert ladder_strikes[5] == 24950.0  # Exactly at the center (index 5)
    assert bundle.rows[5].is_atm is True

    # Check 5 strikes below and 5 strikes above
    assert ladder_strikes[0] == 24950 - (5 * 50)  # 24700
    assert ladder_strikes[-1] == 24950 + (5 * 50)  # 25200

    # Verify Monotonicity: T2 > T1 > Entry > SL for each row
    for r in bundle.rows:
        # CE leg
        ce = r.ce
        assert ce.side == "CE"
        assert ce.ltp > 0
        assert ce.opt_tgt1 > ce.ltp, f"CE Target 1 ({ce.opt_tgt1}) must exceed Entry ({ce.ltp})"
        assert ce.opt_tgt2 >= ce.opt_tgt1, f"CE Target 2 ({ce.opt_tgt2}) must exceed Target 1 ({ce.opt_tgt1})"
        assert ce.opt_sl < ce.ltp, f"CE Stop Loss ({ce.opt_sl}) must be below Entry ({ce.ltp})"
        assert ce.sl_pts > 0
        assert ce.tgt1_pts > 0
        assert ce.rr_ratio > 0

        # PE leg
        pe = r.pe
        assert pe.side == "PE"
        assert pe.ltp > 0
        assert pe.opt_tgt1 > pe.opt_tgt1 * 0.9  # valid positive target
        assert pe.opt_tgt1 > pe.ltp, f"PE Target 1 ({pe.opt_tgt1}) must exceed Entry ({pe.ltp})"
        assert pe.opt_tgt2 >= pe.opt_tgt1, f"PE Target 2 ({pe.opt_tgt2}) must exceed Target 1 ({pe.opt_tgt1})"
        assert pe.opt_sl < pe.ltp, f"PE Stop Loss ({pe.opt_sl}) must be below Entry ({pe.ltp})"
        assert pe.sl_pts > 0
        assert pe.tgt1_pts > 0
        assert pe.rr_ratio > 0


def test_compute_strike_ladder_ws_quote_integration(monkeypatch):
    spot = 52140.0  # BANKNIFTY ATM will be 52100
    expiry = "2026-10-22"

    # Mock get_option_ws_quote to return a simulated live tick for ATM CE
    def mock_get_quote(symbol, strike, side):
        if symbol == "BANKNIFTY" and strike == 52100.0 and side == "CE":
            return {"ltp": 385.50, "at": 1000000.0}
        return None

    import app.spot_ticker as st_module
    monkeypatch.setattr(st_module, "get_option_ws_quote", mock_get_quote)

    bundle = compute_strike_ladder(
        symbol="BANKNIFTY",
        spot=spot,
        contracts_df=None,  # triggers synthetic grid around spot
        expiry=expiry,
        horizon_minutes=45,
        auto_subscribe_ws=False,
    )

    assert bundle.atm_strike == 52100.0
    assert len(bundle.rows) == 11
    atm_row = next(r for r in bundle.rows if r.is_atm)
    assert atm_row.ce.ltp == 385.50
    assert atm_row.ce.is_ws_live is True
    assert bundle.ws_active_count >= 1


def test_is_expiry_crossed():
    ist = timezone(timedelta(hours=5, minutes=30))
    ref_dt = datetime(2026, 10, 16, 11, 0, tzinfo=ist)

    # Past date
    assert is_expiry_crossed("2026-10-15", now_dt=ref_dt) is True
    # Future dates
    assert is_expiry_crossed("2026-10-19", now_dt=ref_dt) is False
    assert is_expiry_crossed("2026-10-27", now_dt=ref_dt) is False

    # Today before 15:30 IST
    today_morning = datetime(2026, 10, 16, 14, 0, tzinfo=ist)
    assert is_expiry_crossed("2026-10-16", now_dt=today_morning) is False

    # Today at or after 15:30 IST
    today_post_market = datetime(2026, 10, 16, 15, 30, 0, tzinfo=ist)
    assert is_expiry_crossed("2026-10-16", now_dt=today_post_market) is True

    today_evening = datetime(2026, 10, 16, 17, 45, tzinfo=ist)
    assert is_expiry_crossed("2026-10-16", now_dt=today_evening) is True

    # Empty / invalid input
    assert is_expiry_crossed("", now_dt=ref_dt) is True
    assert is_expiry_crossed(None, now_dt=ref_dt) is True


def test_get_dynamic_active_expiries_filtering():
    ist = timezone(timedelta(hours=5, minutes=30))
    # Simulated time: 16-Oct-2026 at 10:00 IST
    ref_dt = datetime(2026, 10, 16, 10, 0, tzinfo=ist)

    extra = ["2026-10-15", "2026-10-19", "2026-10-27", "2026-11-03"]
    active = get_dynamic_active_expiries(
        symbol="NIFTY",
        extra_expiries=extra,
        now_dt=ref_dt,
    )

    # 2026-10-15 must be removed because it crossed
    assert "2026-10-15" not in active
    # Future dates must remain and be sorted
    assert "2026-10-19" in active
    assert "2026-10-27" in active
    assert active == sorted(active)
    assert len(active) > 1

    # Advance time past 19-Oct-2026 15:30 IST
    ref_dt_later = datetime(2026, 10, 19, 15, 31, tzinfo=ist)
    active_later = get_dynamic_active_expiries(
        symbol="NIFTY",
        extra_expiries=extra,
        now_dt=ref_dt_later,
    )
    # 2026-10-19 must now be removed as well
    assert "2026-10-19" not in active_later
    assert "2026-10-27" in active_later


def test_compute_strike_ladder_unmatched_expiry_fallback():
    # When contracts_df only has 2026-10-19, but user selects 2026-10-27
    df = pd.DataFrame([
        {"expiry": "2026-10-19", "strike": 25000.0, "type": "CE", "mid": 100.0, "iv": 0.14},
        {"expiry": "2026-10-19", "strike": 25000.0, "type": "PE", "mid": 80.0, "iv": 0.14},
    ])

    # Selecting a different expiry should not contaminate with 2026-10-19 rows
    bundle = compute_strike_ladder(
        symbol="NIFTY",
        spot=25000.0,
        contracts_df=df,
        expiry="2026-10-27",
        horizon_minutes=45,
        auto_subscribe_ws=False,
    )

    assert bundle.expiry == "2026-10-27"
    assert len(bundle.rows) == 11
    # Check that all 11 rows are validly computed
    for r in bundle.rows:
        assert r.ce.opt_tgt1 > r.ce.ltp
        assert r.pe.opt_tgt1 > r.pe.ltp
