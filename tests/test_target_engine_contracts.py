"""Consumer contract regressions for Target Engine money paths."""

from __future__ import annotations

import inspect
import math

import pytest

import target_engine.pricer as pricer
from target_engine.contracts import GreekAttribution, IVForecast
from target_engine.iv_forecast import forecast_iv
from target_engine.time_utils import horizon_sigma, trading_session_horizon_years


def test_implied_vol_alias_exists():
    assert hasattr(pricer, "implied_vol")
    assert hasattr(pricer, "implied_volatility")
    assert pricer.implied_volatility is pricer.implied_vol


def test_iv_forecast_field_is_iv_at_horizon():
    fc = forecast_iv("bull", 60, 0.15, spot_move_pct=0.005)
    assert isinstance(fc, IVForecast)
    assert hasattr(fc, "iv_at_horizon")
    assert not hasattr(fc, "sigma_target")
    assert 0.04 <= fc.iv_at_horizon <= 1.20


def test_greek_attribution_total_modeled_pnl_field():
    fields = GreekAttribution.__dataclass_fields__
    assert "total_modeled_pnl" in fields
    assert "full_reprice_pnl" in fields
    assert "total_pnl" not in fields


def test_trade_ideas_imports_live_iv_contract():
    """trade_ideas must call implied_vol / iv_at_horizon / total_modeled_pnl."""
    import analytics.trade_ideas as ti

    src = inspect.getsource(ti._reprice_trade_idea_targets)
    assert "implied_vol" in src
    assert "iv_at_horizon" in src
    assert "total_modeled_pnl" in src
    assert ".sigma_target" not in src
    assert "ga.total_pnl" not in src


def test_pre_post_market_uses_iv_at_horizon():
    import data.pre_post_market as ppm

    src = inspect.getsource(ppm)
    assert "iv_at_horizon" in src
    assert ".sigma_target" not in src


def test_horizon_sigma_shared_across_modules():
    spot, iv, h = 25000.0, 0.12, 120
    a = horizon_sigma(spot, iv, h)
    b = spot * iv * math.sqrt(trading_session_horizon_years(h))
    assert a == pytest.approx(max(b, 15.0))
