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

    # Tuesday at 15:30 IST (market close boundary) -> Closed for continuous cash, Open for FnO
    dt_close_boundary = datetime(2026, 10, 6, 15, 30, 0, tzinfo=IST)
    assert config.is_market_open(dt_close_boundary, include_fno_extended=False) is False
    assert config.is_market_open(dt_close_boundary) is True

    # Tuesday at 15:35 IST -> Open for F&O session, Closed for regular cash
    dt_fno_ext = datetime(2026, 10, 6, 15, 35, 0, tzinfo=IST)
    assert config.is_market_open(dt_fno_ext, include_fno_extended=False) is False
    assert config.is_market_open(dt_fno_ext) is True

    # Tuesday at 15:40 IST (F&O close boundary) -> Closed for both
    dt_fno_close = datetime(2026, 10, 6, 15, 40, 0, tzinfo=IST)
    assert config.is_market_open(dt_fno_close) is False

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


def test_is_market_open_weekday_holidays():
    # Friday, 2026-10-02 (Gandhi Jayanti) during trading hours -> Closed
    dt_gandhi = datetime(2026, 10, 2, 11, 30, 0, tzinfo=IST)
    assert config.is_market_open(dt_gandhi) is False

    # Tuesday, 2026-10-20 (NSE Holiday) during trading hours -> Closed
    dt_holiday_tue = datetime(2026, 10, 20, 11, 30, 0, tzinfo=IST)
    assert config.is_market_open(dt_holiday_tue) is False


def test_sentinel_seed_anchors_to_prior_trading_day_on_weekday_holiday():
    from sentinel.feed.demo import seed_sentinel_demo_if_needed
    from sentinel.storage.db import list_recent_bars

    # Simulate running on Friday, 2026-10-02 (Gandhi Jayanti)
    fake_holiday_now = datetime(2026, 10, 2, 11, 30, 0, tzinfo=IST)
    inst_id = "HOLIDAY-NIFTY-FUT"
    try:
        with patch("sentinel.feed.demo.datetime") as mock_dt:
            mock_dt.now.return_value = fake_holiday_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            mock_dt.fromisoformat = datetime.fromisoformat
            seed_sentinel_demo_if_needed(inst_id, spot=22500.0, force=True)

        bars = list_recent_bars(inst_id, "1m", 5)
        assert len(bars) > 0
        # Must anchor to 2026-10-01 (Thursday, the preceding trading day before Gandhi Jayanti)
        assert bars[-1].start_ts.strftime("%Y-%m-%d") == "2026-10-01"
    finally:
        from sentinel.storage.db import clear_sentinel_data
        clear_sentinel_data(inst_id)


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


def test_web_server_load_active_dashboard_cold_fallback_uses_demo_when_closed():
    """Verify that if market is closed and no snapshot files exist, _load_active_dashboard uses demo mode instead of hitting live APIs."""
    from web_server import _load_active_dashboard, _DASH_MEM_CACHE
    from pathlib import Path

    _DASH_MEM_CACHE.clear()

    with patch("config.is_market_open", return_value=False):
        with patch.object(Path, "exists", return_value=False):
            with patch("web_server.load_dashboard") as mock_load:
                mock_load.return_value = None
                try:
                    _load_active_dashboard("NIFTY", "weekly")
                except Exception:
                    pass
                assert mock_load.called
                _, kwargs = mock_load.call_args
                assert kwargs.get("use_demo") is True


def test_gex_daemon_pauses_when_market_closed():
    """Verify gex_daemon skips load_dashboard when config.is_market_open() returns False."""
    from scripts.gex_daemon import run_daemon
    import time

    with patch("config.is_market_open", return_value=False):
        with patch("scripts.gex_daemon.load_dashboard") as mock_load:
            # Let it run one iteration and then raise an exception to exit cleanly
            with patch("time.sleep", side_effect=KeyboardInterrupt("stop loop")):
                try:
                    run_daemon(ignore_session=False)
                except KeyboardInterrupt:
                    pass
                assert not mock_load.called


def test_gamma_blast_concluded_when_market_closed():
    """Verify evaluate_dashboard_gamma_blast returns SESSION CONCLUDED with score 0 off-market."""
    from app.gammablast import evaluate_dashboard_gamma_blast
    from unittest.mock import MagicMock

    dash = MagicMock()
    dash.levels.spot = 22888.0
    dash.levels.call_wall = 23000.0
    dash.levels.put_wall = 22500.0
    dash.fetched_at = datetime(2026, 10, 19, 21, 30, 0, tzinfo=timezone.utc)

    with patch("config.is_market_open", return_value=False):
        gb = evaluate_dashboard_gamma_blast(dash, "NIFTY")
        assert gb["state"] == "NO_TRIGGER"
        assert gb["badge"] == "GAMMA BLAST: SESSION CONCLUDED"
        assert gb["score"] == 0
        assert "closed" in gb["description"].lower()


def test_market_snapshot_rejects_corrupted_walls():
    """Verify save_snapshot and _load_active_dashboard reject corrupt outlier walls (e.g. 17000 for NIFTY)."""
    from web_server import _load_active_dashboard, _DASH_MEM_CACHE
    from unittest.mock import MagicMock

    _DASH_MEM_CACHE.clear()
    fake_dash = MagicMock()
    fake_dash.levels.spot = 22888.95
    fake_dash.levels.call_wall = 23000.0
    fake_dash.levels.put_wall = 22500.0

    with patch("config.is_market_open", return_value=False):
        with patch("web_server.load_dashboard", return_value=fake_dash):
            # Corrupted db snapshot with 17000
            with patch("data.market_snapshot.get_snapshot", return_value={
                "spot": 22888.95,
                "call_wall": 17000.0,
                "put_wall": 17000.0,
            }):
                dash = _load_active_dashboard("NIFTY", "weekly")
                # Must NOT overwrite authentic snapshot walls with corrupted 17000.0
                assert dash.levels.call_wall != 17000.0
                assert dash.levels.put_wall != 17000.0
                assert dash.levels.call_wall >= 19000.0
                assert dash.levels.put_wall >= 19000.0


