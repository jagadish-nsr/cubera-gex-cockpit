"""tests/test_sentinel_live.py — Unit tests for live Dhan WebSocket & Intraday Ingestion for Sentinel."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
from sentinel.feed.live import (
    LiveSentinelManager,
    sync_live_sentinel_if_needed,
    push_live_tick_to_sentinel,
)
from sentinel.models import Bar, SentinelSignal
from sentinel.storage.db import list_recent_bars, clear_sentinel_data


class TestSentinelLive(unittest.TestCase):
    def setUp(self):
        clear_sentinel_data("TEST-FUT")

    def tearDown(self):
        clear_sentinel_data("TEST-FUT")

    @patch("data.dhan.nearest_futures")
    @patch("data.dhan.fetch_futures_intraday")
    def test_sync_live_session_success(self, mock_fetch, mock_nearest):
        mock_nearest.return_value = [{"security_id": 99999, "symbol": "TEST-FUT", "lot": 65}]
        now_ts = datetime.now(timezone.utc).timestamp() - 300
        mock_df = pd.DataFrame([
            {
                "open": 22500.0,
                "high": 22510.0,
                "low": 22495.0,
                "close": 22505.0,
                "volume": 5000,
                "oi": 1000000,
                "timestamp": now_ts,
            },
            {
                "open": 22505.0,
                "high": 22520.0,
                "low": 22502.0,
                "close": 22518.0,
                "volume": 6500,
                "oi": 1010000,
                "timestamp": now_ts + 60,
            },
        ])
        mock_fetch.return_value = mock_df

        mgr = LiveSentinelManager()

        from sentinel.feed import live as live_mod

        def _fetch_must_not_hold_feed_lock(*_a, **_k):
            acquired = live_mod._LOCK.acquire(blocking=False)
            if acquired:
                live_mod._LOCK.release()
            self.assertTrue(acquired, "broker fetch ran while the sentinel feed lock was held")
            return mock_df

        mock_fetch.side_effect = _fetch_must_not_hold_feed_lock
        ok = mgr.sync_live_session("TEST-FUT", "TEST", call_wall=22600.0, put_wall=22400.0, force=True)
        self.assertTrue(ok)

        bars = list_recent_bars("TEST-FUT", "1m", limit=5)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1].close, 22518.0)
        self.assertEqual(bars[-1].volume_units, 6500)
        self.assertGreater(bars[-1].vwap, 0.0)

    def test_on_tick_advances_bar(self):
        mgr = LiveSentinelManager()
        # Mock aggregator
        mock_agg = MagicMock()
        mock_bar = Bar(
            instrument_id="TEST-FUT",
            timeframe="1m",
            start_ts=datetime.now(timezone.utc),
            open=22500.0,
            high=22510.0,
            low=22490.0,
            close=22505.0,
            volume_units=1000,
            volume_lots=15.4,
            buy_vol_est=600.0,
            sell_vol_est=400.0,
            delta=200.0,
            delta_ratio=0.2,
            delta_quality=1.0,
            cvd=200.0,
            vwap=22502.0,
            vwap_sigma_1=22510.0,
            vwap_sigma_2=22515.0,
            vwap_lower_1=22495.0,
            vwap_lower_2=22490.0,
            oi=50000,
            delta_oi=1000,
            oi_fresh=True,
            incomplete=False,
        )
        mock_agg.add_tick.return_value = mock_bar
        mgr._aggregators["TEST-FUT"] = mock_agg

        pkt = {
            "security_id": 99999,
            "ltp": 22505.0,
            "ltq": 65,
            "volume": 25000,
            "oi": 50000,
            "ltt": int(datetime.now(timezone.utc).timestamp()),
        }
        mgr.on_tick(pkt, "TEST")

        mock_agg.add_tick.assert_called_once()
        bars = list_recent_bars("TEST-FUT", "1m", limit=5)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 22505.0)

    def test_missing_aggregator_schedules_one_sync(self):
        """A tick with no in-memory aggregator starts one session sync, not one per tick."""
        mgr = LiveSentinelManager()
        started = []

        class _InlineThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target
                started.append(name)

            def start(self):
                self._target()

        calls = []

        def _sync(instrument_id, symbol, **kwargs):
            calls.append((instrument_id, symbol, kwargs))
            return False

        mgr.sync_live_session = _sync
        pkt = {"ltp": 22505.0, "ltq": 65, "volume": 100, "ltt": int(datetime.now(timezone.utc).timestamp())}
        with patch("sentinel.feed.live.threading.Thread", _InlineThread):
            mgr.on_tick(pkt, "TEST")
            mgr.on_tick(pkt, "TEST")
        self.assertEqual(calls, [("TEST-FUT", "TEST", {})])
        self.assertEqual(len(started), 1)

    def test_live_session_isolation_never_returns_yesterday_data(self):
        """Verify that during an active live market session, list_recent_bars strictly isolates to today."""
        from sentinel.storage.db import save_bar
        import pytz
        IST = pytz.timezone("Asia/Kolkata")
        today_ist = datetime.now(IST)
        yesterday_ist = today_ist - timedelta(days=1)

        # Save a bar with yesterday's timestamp
        yest_bar = Bar(
            instrument_id="TEST-FUT",
            timeframe="1m",
            start_ts=yesterday_ist.replace(hour=15, minute=29, second=0, microsecond=0),
            open=22400.0, high=22410.0, low=22395.0, close=22405.0,
            volume_units=1000, volume_lots=15.4,
            buy_vol_est=500.0, sell_vol_est=500.0,
            delta=0.0, delta_ratio=0.0, delta_quality=1.0,
            cvd=1000.0, vwap=22400.0,
            vwap_sigma_1=22410.0, vwap_sigma_2=22420.0,
            vwap_lower_1=22390.0, vwap_lower_2=22380.0,
            oi=50000, delta_oi=0, oi_fresh=True, incomplete=False,
        )
        save_bar(yest_bar)

        # Mock active market session at 10:30 AM on a weekday
        mock_market_now = today_ist.replace(hour=10, minute=30, second=0, microsecond=0)
        # Ensure it's treated as a weekday
        if mock_market_now.weekday() >= 5:
            mock_market_now = mock_market_now - timedelta(days=mock_market_now.weekday() - 3)

        with patch("sentinel.storage.db.datetime") as mock_dt, patch("data.holidays.is_trading_day", return_value=True):
            mock_dt.now.return_value = mock_market_now
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            # Query with session_only=True and _auto_populate=False
            bars = list_recent_bars("TEST-FUT", "1m", limit=10, session_only=True, _auto_populate=False)
            # Must be empty for today, NOT containing yesterday's bar!
            self.assertEqual(len(bars), 0)

            # Save a bar with today's timestamp
            today_bar = Bar(
                instrument_id="TEST-FUT",
                timeframe="1m",
                start_ts=mock_market_now.replace(minute=25),
                open=22500.0, high=22510.0, low=22495.0, close=22505.0,
                volume_units=2000, volume_lots=30.8,
                buy_vol_est=1200.0, sell_vol_est=800.0,
                delta=400.0, delta_ratio=0.2, delta_quality=1.0,
                cvd=400.0, vwap=22503.0,
                vwap_sigma_1=22512.0, vwap_sigma_2=22520.0,
                vwap_lower_1=22490.0, vwap_lower_2=22480.0,
                oi=51000, delta_oi=1000, oi_fresh=True, incomplete=False,
            )
            save_bar(today_bar)

            bars_today = list_recent_bars("TEST-FUT", "1m", limit=10, session_only=True, _auto_populate=False)
            self.assertEqual(len(bars_today), 1)
            self.assertEqual(bars_today[0].start_ts.date(), mock_market_now.date())
            self.assertEqual(bars_today[0].cvd, 400.0)


if __name__ == "__main__":
    unittest.main()
