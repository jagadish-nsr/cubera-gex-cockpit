"""tests/test_market_simulator.py — Unit tests for the Session Replay & Market Simulator.
"""

from __future__ import annotations

import pytest
from app.market_simulator import (
    SCENARIOS,
    TIMESTAMPS_5M,
    KEY_PHASES,
    generate_scenario_snapshot,
    load_historical_snapshot,
    get_available_historical_days,
    sync_sentinel_simulation,
    _scenario_spot_and_gex,
)
from sentinel.storage.db import list_recent_bars


def test_timeline_and_scenarios_exist():
    assert len(TIMESTAMPS_5M) == 76
    assert TIMESTAMPS_5M[0] == "09:15"
    assert TIMESTAMPS_5M[-1] == "15:30"
    assert "expiry_gamma_blast" in SCENARIOS
    assert "iron_condor_pin" in SCENARIOS
    assert "trend_breakdown" in SCENARIOS
    assert "09:15" in KEY_PHASES
    assert "14:45" in KEY_PHASES


def test_expiry_gamma_blast_progression():
    # Morning pin around 22650
    morn = _scenario_spot_and_gex("expiry_gamma_blast", "10:30")
    assert 22620.0 <= morn["spot"] <= 22680.0
    assert morn["regime"] == "POSITIVE"

    # Afternoon squeeze build-up
    mid = _scenario_spot_and_gex("expiry_gamma_blast", "14:15")
    assert mid["spot"] > 22670.0
    assert mid["regime"] == "NEGATIVE"

    # Late gamma blast breakout
    late = _scenario_spot_and_gex("expiry_gamma_blast", "15:10")
    assert late["spot"] > 22780.0
    assert late["regime"] == "NEGATIVE"
    assert "explosive" in late["mode_label"].lower()


def test_iron_condor_pin_progression():
    snap_open = _scenario_spot_and_gex("iron_condor_pin", "09:15")
    snap_mid = _scenario_spot_and_gex("iron_condor_pin", "12:00")
    snap_close = _scenario_spot_and_gex("iron_condor_pin", "15:30")

    # High positive gamma throughout
    assert snap_open["net_gex"] > 100_000_000.0
    assert snap_mid["net_gex"] > 100_000_000.0
    assert snap_close["net_gex"] > 100_000_000.0
    # Spot stays strictly between walls
    for snap in (snap_open, snap_mid, snap_close):
        assert snap["put_wall"] < snap["spot"] < snap["call_wall"]


def test_trend_breakdown_progression():
    early = _scenario_spot_and_gex("trend_breakdown", "09:30")
    mid = _scenario_spot_and_gex("trend_breakdown", "12:30")
    late = _scenario_spot_and_gex("trend_breakdown", "15:00")

    assert early["spot"] > mid["spot"] > late["spot"]
    assert late["regime"] == "NEGATIVE"


def test_generate_scenario_snapshot():
    snap = generate_scenario_snapshot("expiry_gamma_blast", "14:45", symbol="NIFTY")
    assert snap is not None
    assert snap.levels.spot > 22650.0
    assert snap.symbol == "NIFTY"
    assert len(snap.oi_strikes) >= 15
    assert snap.day_ohlc is not None
    assert snap.day_ohlc["high"] >= snap.levels.spot


def test_historical_snapshot_loading():
    days = get_available_historical_days("NIFTY")
    assert len(days) > 0
    # Load snapshot for first available day
    snap = load_historical_snapshot(days[0], "14:00", symbol="NIFTY")
    assert snap is not None
    assert snap.levels.spot > 0
    assert snap.symbol == "NIFTY"
    assert snap.day_ohlc["close"] == snap.levels.spot


def test_sync_sentinel_simulation():
    sync_sentinel_simulation("NIFTY", "scenario", "expiry_gamma_blast", "11:30")
    bars_1m = list_recent_bars("NIFTY-FUT", "1m", limit=10)
    assert len(bars_1m) > 0
    assert bars_1m[0].close > 0
    assert bars_1m[0].volume_units > 0
