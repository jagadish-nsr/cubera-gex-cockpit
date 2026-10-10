"""Tests for Market Hours auto-refresh cutoffs and manual mode behavior."""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import config
from sentinel.feed.demo import step_sentinel_live_feed


IST = timezone(timedelta(hours=5, minutes=30))


def test_is_market_open_regular_trading_hours():
    # Tuesday at 11:30 AM IST -> Open
    dt_open = datetime(2026, 10, 6, 11, 30, 0, tzinfo=IST)
    assert config.is_market_open(dt_open) is True

    # Tuesday at 09:15 AM IST (market open boundary) -> Open
    dt_open_boundary = datetime(2026, 10, 6, 9, 15, 0, tzinfo=IST)
    assert config.is_market_open(dt_open_boundary) is True

    # Tuesday at 15:29:59 IST (right before close) -> Open
    dt_near_close = datetime(2026, 10, 6, 15, 29, 0, tzinfo=IST)
    assert config.is_market_open(dt_near_close) is True


def test_is_market_open_before_and_after_hours():
    # Tuesday at 08:45 AM IST -> Closed
    dt_early = datetime(2026, 10, 6, 8, 45, 0, tzinfo=IST)
    assert config.is_market_open(dt_early) is False

    # Tuesday at 15:30 IST (market close boundary) -> Closed
    dt_close_boundary = datetime(2026, 10, 6, 15, 30, 0, tzinfo=IST)
    assert config.is_market_open(dt_close_boundary) is False

    # Tuesday at 16:15 IST (4:15 PM) -> Closed
    dt_late = datetime(2026, 10, 6, 16, 15, 0, tzinfo=IST)
    assert config.is_market_open(dt_late) is False

    # Tuesday at 18:10 IST (evening after hours) -> Closed
    dt_evening = datetime(2026, 10, 6, 18, 10, 0, tzinfo=IST)
    assert config.is_market_open(dt_evening) is False


def test_is_market_open_weekends():
    # Saturday at 11:30 AM IST -> Closed
    dt_sat = datetime(2026, 10, 10, 11, 30, 0, tzinfo=IST)
    assert config.is_market_open(dt_sat) is False

    # Sunday at 11:30 AM IST -> Closed
    dt_sun = datetime(2026, 10, 11, 11, 30, 0, tzinfo=IST)
    assert config.is_market_open(dt_sun) is False


def test_step_sentinel_live_feed_pauses_after_hours():
    # Mock market closed
    with patch("config.is_market_open", return_value=False):
        with patch("sentinel.feed.demo._live_feed_engine.step") as mock_step:
            # Without force: should return immediately and not step
            step_sentinel_live_feed("NIFTY-FUT", 24550.0, 24700.0, 24400.0, force=False)
            assert not mock_step.called

            # With force=True: should step even after hours
            step_sentinel_live_feed("NIFTY-FUT", 24550.0, 24700.0, 24400.0, force=True)
            assert mock_step.called
