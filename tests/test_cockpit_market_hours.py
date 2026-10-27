"""Tests for Market Hours auto-refresh cutoffs and manual mode behavior."""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import config


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


def test_live_hours_reads_published_store_without_dhan():
    """HTTP path must read live_fetcher.latest and never call load_dashboard(use_demo=False)."""
    from web_server import _load_active_dashboard, _DASH_MEM_CACHE
    from unittest.mock import MagicMock

    _DASH_MEM_CACHE.clear()
    fake = MagicMock()
    fake.levels.spot = 22600.0
    fake.levels.call_wall = 23000.0
    fake.levels.put_wall = 22500.0
    fake.india_vix = {"last": 12.0}

    with patch("config.is_market_open", return_value=True):
        with patch("web_server.load_dashboard") as mock_load:
            with patch("web_server._ensure_chain_ingest"):
                with patch("app.live_fetcher.latest", return_value={"data": fake, "err": None}):
                    with patch("app.spot_ticker.latest", return_value=None):
                        dash = _load_active_dashboard("NIFTY", "current_week")
    assert dash.levels.call_wall == 23000.0
    mock_load.assert_not_called()


def test_live_hours_cold_start_skips_pickle_and_does_not_call_dhan():
    """A live cold start must not unpickle (that holds the GIL and stalls ticks),
    must not pull Dhan on the request, and must not serve the 24,500 demo book."""
    from web_server import _load_active_dashboard, _DASH_MEM_CACHE
    from unittest.mock import MagicMock

    _DASH_MEM_CACHE.clear()
    pickled = MagicMock(name="pickle")
    fixture = MagicMock(name="demo")
    fixture.levels.spot = 24500.0

    with patch("config.is_market_open", return_value=True):
        with patch("web_server.load_dashboard", return_value=fixture) as mock_load:
            with patch("web_server._ensure_chain_ingest"):
                with patch("app.live_fetcher.latest", return_value=None):
                    with patch("web_server._from_lkgs", return_value=None):
                        with patch("web_server._unpickle_latest_snap", return_value=pickled) as mock_unpickle:
                            with patch("web_server._overlay_live_spot", side_effect=lambda d, s: d):
                                with patch("web_server._refresh_book_view", side_effect=lambda d: d):
                                    dash = _load_active_dashboard("NIFTY", "current_week")
    assert dash is None
    mock_unpickle.assert_not_called()
    mock_load.assert_not_called()


def test_live_hours_does_not_load_the_demo_book():
    from web_server import _load_active_dashboard, _DASH_MEM_CACHE

    _DASH_MEM_CACHE.clear()
    with patch("config.is_market_open", return_value=True):
        with patch("web_server.load_dashboard") as mock_load:
            with patch("web_server._ensure_chain_ingest"):
                with patch("app.live_fetcher.latest", return_value=None):
                    with patch("web_server._from_lkgs", return_value=None):
                        with patch("web_server._unpickle_latest_snap", return_value=None):
                            dash = _load_active_dashboard("NIFTY", "current_week")
    assert dash is None
    mock_load.assert_not_called()


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


