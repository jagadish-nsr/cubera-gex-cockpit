"""tests/test_sentinel_simulation_gate.py — Tests for Sentinel Simulator Mode Gating & EOD Fallback.

Verifies:
1. When simulation_mode is False:
   - Primary: attempts live WS/API sync.
   - Fallback: loads authentic EOD snapshot data.
   - Zero-Fabrication: suppresses synthetic fake candle generation if neither is available.
2. When simulation_mode is True:
   - Generates realistic simulation bars.
"""

from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import pytest

from sentinel.feed.demo import seed_sentinel_demo_if_needed, _load_from_eod_snapshot
from sentinel.storage.db import list_recent_bars, clear_sentinel_data, save_bar
from sentinel.models import Bar

IST = timezone(timedelta(hours=5, minutes=30))


def test_seed_sentinel_demo_simulation_mode_off_zero_fabrication():
    """When simulation_mode is False and no live API or EOD snapshot exists, generate ZERO synthetic data."""
    test_inst = "TEST-SIM-OFF-FUT"
    clear_sentinel_data(test_inst)

    try:
        with patch("sentinel.feed.live.sync_live_sentinel_if_needed", return_value=False):
            with patch("sentinel.feed.demo._load_from_eod_snapshot", return_value=False):
                seed_sentinel_demo_if_needed(test_inst, spot=22500.0, force=True, simulation_mode=False)

        bars = list_recent_bars(test_inst, timeframe="1m", limit=10, _auto_populate=False)
        assert len(bars) == 0, "Expected 0 bars when simulation_mode=False and no authentic data available"
    finally:
        clear_sentinel_data(test_inst)


def test_seed_sentinel_demo_simulation_mode_off_eod_snapshot_fallback():
    """When simulation_mode is False and live API fails, fall back to EOD snapshot."""
    test_inst = "TEST-EOD-SNAP-FUT"
    clear_sentinel_data(test_inst)

    try:
        with patch("sentinel.feed.live.sync_live_sentinel_if_needed", return_value=False):
            with patch("sentinel.feed.demo._load_from_eod_snapshot", return_value=True) as mock_eod:
                seed_sentinel_demo_if_needed(test_inst, spot=22500.0, force=True, simulation_mode=False)
                mock_eod.assert_called_once()
    finally:
        clear_sentinel_data(test_inst)


def test_seed_sentinel_demo_simulation_mode_on_generates_bars():
    """When simulation_mode is True, synthetic bars are generated."""
    test_inst = "TEST-SIM-ON-FUT"
    clear_sentinel_data(test_inst)

    try:
        seed_sentinel_demo_if_needed(test_inst, spot=22500.0, force=True, simulation_mode=True)
        bars_1m = list_recent_bars(test_inst, timeframe="1m", limit=400, _auto_populate=False)
        bars_5m = list_recent_bars(test_inst, timeframe="5m", limit=80, _auto_populate=False)

        assert len(bars_1m) > 0, "Expected 1m bars to be generated in simulator mode"
        assert len(bars_5m) > 0, "Expected 5m bars to be aggregated in simulator mode"
        assert bars_1m[-1].close > 0
    finally:
        clear_sentinel_data(test_inst)


def test_load_from_eod_snapshot_from_market_snapshot_db():
    """Test _load_from_eod_snapshot populates EOD anchor bar from market_snapshot.db."""
    test_inst = "TEST-EOD-DB-FUT"
    clear_sentinel_data(test_inst)

    fake_snapshot = {
        "symbol": "TEST-EOD-DB",
        "spot": 22888.0,
        "open": 22800.0,
        "high": 22950.0,
        "low": 22750.0,
        "as_of": "2026-10-19T10:00:00Z",
    }

    try:
        with patch("data.market_snapshot.get_snapshot", return_value=fake_snapshot):
            with patch("sentinel.feed.demo.Path.glob", return_value=[]):
                res = _load_from_eod_snapshot(test_inst, "TEST-EOD-DB")
                assert res is True

        bars = list_recent_bars(test_inst, timeframe="1m", limit=5, _auto_populate=False)
        assert len(bars) > 0
        assert bars[-1].close == 22888.0
        assert bars[-1].open == 22800.0
        assert bars[-1].high == 22950.0
        assert bars[-1].low == 22750.0
    finally:
        clear_sentinel_data(test_inst)
