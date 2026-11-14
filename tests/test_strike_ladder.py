"""Unit tests for analytics/strike_ladder.py."""

from types import SimpleNamespace
from datetime import datetime, timezone, timedelta

import pandas as pd
import pytest

from analytics.strike_ladder import (
    compute_strike_ladder,
    StrikeLadderBundle,
    is_expiry_crossed,
    get_dynamic_active_expiries,
)


def _levels(atm_iv: float = 0.14, call_wall: float = 0.0, put_wall: float = 0.0):
    return SimpleNamespace(atm_iv=atm_iv, call_wall=call_wall, put_wall=put_wall, vwap=None)


def test_compute_strike_ladder_slicing_and_monotonicity():
    # Construct a synthetic 40-strike chain for NIFTY
    strikes = [24000 + (i * 50) for i in range(40)]  # 24000 to 25950
    spot = 24963.0  # ATM will be 24950
    expiry = "2026-10-22"

    rows = []
    for k in strikes:
        # Distance-scaled mids so BS T1 stays actionable vs entry.
        ce_mid = max(5.0, 250.0 - abs(k - spot) * 0.35)
        pe_mid = max(5.0, 220.0 - abs(k - spot) * 0.35)
        rows.append({"expiry": expiry, "strike": float(k), "type": "CE", "mid": ce_mid, "iv": 0.14})
        rows.append({"expiry": expiry, "strike": float(k), "type": "PE", "mid": pe_mid, "iv": 0.15})
    df = pd.DataFrame(rows)

    bundle = compute_strike_ladder(
        symbol="NIFTY",
        spot=spot,
        contracts_df=df,
        expiry=expiry,
        horizon_minutes=120,
        levels=_levels(0.14, call_wall=25200.0, put_wall=24700.0),
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

    # Every wing keeps its chain quote; actionable rows have monotonic T/SL.
    for r in bundle.rows:
        assert r.ce is not None and r.ce.ltp > 0
        assert r.pe is not None and r.pe.ltp > 0
        if r.ce.targets_ok:
            assert r.ce.opt_tgt1 > r.ce.ltp
            assert r.ce.opt_tgt2 >= r.ce.opt_tgt1
            assert r.ce.opt_sl < r.ce.ltp
        if r.pe.targets_ok:
            assert r.pe.opt_tgt1 > r.pe.ltp
            assert r.pe.opt_tgt2 >= r.pe.opt_tgt1
            assert r.pe.opt_sl < r.pe.ltp


def test_atm_ce_keeps_chain_quote_when_targets_not_actionable():
    """Regression: tight bull map + vol crush must not wipe ATM/OTM CE LTP."""
    spot = 25144.0
    atm = 25150.0
    expiry = "2026-11-17"
    rows = []
    for k in range(int(atm - 5 * 50), int(atm + 6 * 50), 50):
        # Rich ATM CE print relative to a short call-wall hop → T1 often ≤ LTP.
        ce = 180.0 if k >= atm - 50 else max(40.0, 300.0 - (atm - k) * 0.8)
        pe = max(20.0, 40.0 + (k - (atm - 250)) * 0.4)
        rows.append({"expiry": expiry, "strike": float(k), "type": "CE", "ltp": ce, "iv": 0.12})
        rows.append({"expiry": expiry, "strike": float(k), "type": "PE", "ltp": pe, "iv": 0.14})
    df = pd.DataFrame(rows)

    bundle = compute_strike_ladder(
        symbol="NIFTY",
        spot=spot,
        contracts_df=df,
        expiry=expiry,
        horizon_minutes=120,
        levels=_levels(0.126, call_wall=25200.0, put_wall=25000.0),
        auto_subscribe_ws=False,
    )
    assert len(bundle.rows) == 11
    atm_row = next(r for r in bundle.rows if r.is_atm)
    assert atm_row.ce is not None, "ATM CE wing must keep chain quote"
    assert atm_row.ce.ltp > 0
    assert atm_row.ce.delta is not None
    # Targets may be refused — that must not null the whole wing.
    if not atm_row.ce.targets_ok:
        assert atm_row.ce.opt_tgt1 is None
        assert atm_row.ce.opt_sl is None
    # PE on same strike still present
    assert atm_row.pe is not None and atm_row.pe.ltp > 0


def test_compute_strike_ladder_ws_quote_integration(monkeypatch):
    spot = 52140.0  # BANKNIFTY ATM will be 52100
    expiry = "2026-10-22"

    def mock_get_quote(symbol, strike, side):
        if symbol == "BANKNIFTY" and strike == 52100.0 and side == "CE":
            return {"ltp": 385.50, "at": 1000000.0}
        return None

    import app.spot_ticker as st_module
    monkeypatch.setattr(st_module, "get_option_ws_quote", mock_get_quote)

    # Minimal chain so PE also has a print; CE ATM comes from WS.
    rows = []
    for k in range(51600, 52700, 100):
        rows.append({"expiry": expiry, "strike": float(k), "type": "CE", "ltp": 200.0, "iv": 0.16})
        rows.append({"expiry": expiry, "strike": float(k), "type": "PE", "ltp": 180.0, "iv": 0.17})
    df = pd.DataFrame(rows)

    bundle = compute_strike_ladder(
        symbol="BANKNIFTY",
        spot=spot,
        contracts_df=df,
        expiry=expiry,
        horizon_minutes=120,
        levels=_levels(0.16),
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

    assert is_expiry_crossed("2026-10-15", now_dt=ref_dt) is True
    assert is_expiry_crossed("2026-10-19", now_dt=ref_dt) is False
    assert is_expiry_crossed("2026-10-27", now_dt=ref_dt) is False

    today_morning = datetime(2026, 10, 16, 14, 0, tzinfo=ist)
    assert is_expiry_crossed("2026-10-16", now_dt=today_morning) is False

    today_post_market = datetime(2026, 10, 16, 15, 30, 0, tzinfo=ist)
    assert is_expiry_crossed("2026-10-16", now_dt=today_post_market) is True

    today_evening = datetime(2026, 10, 16, 17, 45, tzinfo=ist)
    assert is_expiry_crossed("2026-10-16", now_dt=today_evening) is True

    assert is_expiry_crossed("", now_dt=ref_dt) is True
    assert is_expiry_crossed(None, now_dt=ref_dt) is True


def test_get_dynamic_active_expiries_filtering():
    ist = timezone(timedelta(hours=5, minutes=30))
    ref_dt = datetime(2026, 10, 16, 10, 0, tzinfo=ist)

    extra = ["2026-10-15", "2026-10-19", "2026-10-27", "2026-11-03"]
    active = get_dynamic_active_expiries(
        symbol="NIFTY",
        extra_expiries=extra,
        now_dt=ref_dt,
    )

    assert "2026-10-15" not in active
    assert "2026-10-19" in active
    assert "2026-10-27" in active
    assert active == sorted(active)
    assert len(active) > 1

    ref_dt_later = datetime(2026, 10, 19, 15, 31, tzinfo=ist)
    active_later = get_dynamic_active_expiries(
        symbol="NIFTY",
        extra_expiries=extra,
        now_dt=ref_dt_later,
    )
    assert "2026-10-19" not in active_later
    assert "2026-10-27" in active_later


def test_compute_strike_ladder_unmatched_expiry_fallback():
    # When contracts_df only has 2026-10-19, but user selects 2026-10-27
    df = pd.DataFrame([
        {"expiry": "2026-10-19", "strike": 25000.0, "type": "CE", "mid": 100.0, "iv": 0.14},
        {"expiry": "2026-10-19", "strike": 25000.0, "type": "PE", "mid": 80.0, "iv": 0.14},
    ])

    bundle = compute_strike_ladder(
        symbol="NIFTY",
        spot=25000.0,
        contracts_df=df,
        expiry="2026-10-27",
        horizon_minutes=120,
        levels=_levels(0.14),
        auto_subscribe_ws=False,
    )

    assert bundle.expiry == "2026-10-27"
    # Unmatched expiry → synthetic grid without LTP → empty wings skipped.
    # Must not contaminate with 2026-10-19 rows.
    assert all(r.ce is None or r.ce.ltp > 0 for r in bundle.rows)
