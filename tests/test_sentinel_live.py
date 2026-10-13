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
        now_ts = 1791870000.0
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


if __name__ == "__main__":
    unittest.main()
