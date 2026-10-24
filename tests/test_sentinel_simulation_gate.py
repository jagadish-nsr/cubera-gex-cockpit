"""Sentinel tape loader: live feed, else EOD snapshot, else write nothing."""

from unittest.mock import patch

from sentinel.feed.demo import seed_sentinel_demo_if_needed, _load_from_eod_snapshot
from sentinel.storage.db import list_recent_bars, clear_sentinel_data


def test_seed_writes_nothing_when_live_and_eod_are_missing():
    test_inst = "TEST-SIM-OFF-FUT"
    clear_sentinel_data(test_inst)

    try:
        with patch("sentinel.feed.live.sync_live_sentinel_if_needed", return_value=False):
            with patch("sentinel.feed.demo._load_from_eod_snapshot", return_value=False):
                seed_sentinel_demo_if_needed(test_inst, spot=22500.0, force=True)

        bars = list_recent_bars(test_inst, timeframe="1m", limit=10, _auto_populate=False)
        assert len(bars) == 0
    finally:
        clear_sentinel_data(test_inst)


def test_holiday_prefix_does_not_fabricate_bars():
    """A TEST or HOLIDAY instrument id is not a switch that writes a synthetic session."""
    test_inst = "HOLIDAY-NIFTY-FUT"
    clear_sentinel_data(test_inst)

    try:
        with patch("sentinel.feed.live.sync_live_sentinel_if_needed", return_value=False):
            with patch("sentinel.feed.demo._load_from_eod_snapshot", return_value=False):
                seed_sentinel_demo_if_needed(test_inst, spot=22500.0, force=True)

        bars = list_recent_bars(test_inst, timeframe="1m", limit=10, _auto_populate=False)
        assert len(bars) == 0
    finally:
        clear_sentinel_data(test_inst)


def test_seed_falls_back_to_eod_snapshot():
    test_inst = "TEST-EOD-SNAP-FUT"
    clear_sentinel_data(test_inst)

    try:
        with patch("sentinel.feed.live.sync_live_sentinel_if_needed", return_value=False):
            with patch("sentinel.feed.demo._load_from_eod_snapshot", return_value=True) as mock_eod:
                seed_sentinel_demo_if_needed(test_inst, spot=22500.0, force=True)
                mock_eod.assert_called_once()
    finally:
        clear_sentinel_data(test_inst)


def test_load_from_eod_snapshot_from_market_snapshot_db():
    """Test _load_from_eod_snapshot populates an EOD anchor bar from market_snapshot.db."""
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
