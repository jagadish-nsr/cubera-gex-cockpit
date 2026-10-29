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
from sentinel.feed.live import IST, _minute_floor
from sentinel.models import Bar, SentinelSignal, SignalOutcome
from sentinel.storage.db import (
    list_recent_bars,
    list_recent_signals,
    list_recent_outcomes,
    clear_sentinel_data,
    save_bar,
    save_signal,
    save_outcome,
)


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

    def _session_clock(self):
        """A market-hours clock that is not in the future relative to the machine."""
        real_now = datetime.now(IST)
        if real_now.hour < 10:
            session_now = (real_now - timedelta(days=1)).replace(
                hour=11, minute=30, second=0, microsecond=0,
            )
        else:
            session_now = real_now.replace(second=0, microsecond=0)

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return session_now.replace(tzinfo=None)
                return session_now.astimezone(tz)

        return session_now, _Clock

    def _bar(self, start_ts: datetime, *, close: float, delta: float, volume: int = 4000) -> Bar:
        return Bar(
            instrument_id="TEST-FUT",
            timeframe="1m",
            start_ts=start_ts,
            open=close - 2,
            high=close + 4,
            low=close - 6,
            close=close,
            volume_units=volume,
            volume_lots=round(volume / 65.0, 1),
            buy_vol_est=round((volume + delta) / 2.0, 1),
            sell_vol_est=round((volume - delta) / 2.0, 1),
            delta=delta,
            delta_ratio=round(delta / volume, 4),
            delta_quality=1.0,
            cvd=delta,
            vwap=close,
            vwap_sigma_1=close + 8,
            vwap_sigma_2=close + 16,
            vwap_lower_1=close - 8,
            vwap_lower_2=close - 16,
            oi=1000000,
            delta_oi=0,
            oi_fresh=True,
            incomplete=False,
        )

    @patch("data.holidays.is_trading_day", return_value=True)
    @patch("data.dhan.nearest_futures")
    @patch("data.dhan.fetch_futures_intraday")
    def test_in_session_resync_keeps_live_sweeps_and_bars(self, mock_fetch, mock_nearest, _trading):
        """A mid-session reload fills missing minutes and leaves live sweeps in sqlite."""
        session_now, clock = self._session_clock()
        kept_ts = session_now - timedelta(minutes=6)
        gap_ts = session_now - timedelta(minutes=5)
        live_bar = self._bar(kept_ts, close=22555.0, delta=7777.0)
        save_bar(live_bar)
        sweep = SentinelSignal(
            event_id="sig_test_micro_buyer_sweep",
            instrument_id="TEST-FUT",
            timeframe="15s",
            bar_start_ts=kept_ts + timedelta(seconds=12),
            type="CONTINUATION_BULL",
            category="BULLISH",
            bias="LONG",
            score=68,
            confidence="MEDIUM",
            rvol_tod=2.0,
            delta=4200.0,
            delta_ratio=0.55,
            oi_change=0,
            context_tags=["MICRO_BURST", "INSTANT_TAPE"],
            insight_text="⚡ Buyer sweep (+9000 units in 15s, Δ +4,200).",
            invalidation_level=22540.0,
            trigger_speed="MICRO_BURST",
        )
        save_signal(sweep)
        save_outcome(SignalOutcome(
            event_id=sweep.event_id,
            instrument_id="TEST-FUT",
            signal_type=sweep.type,
            entry_price=22555.0,
            invalidation_level=22540.0,
            mfe_pts=12.5,
            mae_pts=1.0,
        ))

        mock_nearest.return_value = [{"security_id": 99999, "symbol": "TEST-FUT", "lot": 65}]
        mock_fetch.return_value = pd.DataFrame([
            {
                "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
                "volume": 10, "oi": 1, "timestamp": int(kept_ts.timestamp()),
            },
            {
                "open": 22560.0, "high": 22570.0, "low": 22558.0, "close": 22566.0,
                "volume": 8000, "oi": 1001000, "timestamp": int(gap_ts.timestamp()),
            },
        ])
        mgr = LiveSentinelManager()
        with patch("sentinel.feed.live.datetime", clock), \
                patch("sentinel.feed.live.clear_sentinel_data") as wipe:
            ok = mgr.sync_live_session("TEST-FUT", "TEST", call_wall=22600.0, put_wall=22400.0, force=True)
        self.assertTrue(ok)
        wipe.assert_not_called()

        bars = list_recent_bars("TEST-FUT", "1m", limit=10, session_only=False, _auto_populate=False)
        by_minute = {_minute_floor(b.start_ts): b for b in bars}
        kept = by_minute[_minute_floor(kept_ts)]
        self.assertEqual(kept.close, 22555.0)
        self.assertEqual(kept.delta, 7777.0)
        gap = by_minute[_minute_floor(gap_ts)]
        self.assertEqual(gap.close, 22566.0)

        signals = list_recent_signals("TEST-FUT", limit=20)
        self.assertIn(sweep.event_id, [s.event_id for s in signals])
        outcomes = list_recent_outcomes("TEST-FUT", limit=20)
        self.assertIn(sweep.event_id, [o.event_id for o in outcomes])
        kept_outcome = next(o for o in outcomes if o.event_id == sweep.event_id)
        self.assertEqual(kept_outcome.mfe_pts, 12.5)

    @patch("data.holidays.is_trading_day", return_value=True)
    @patch("data.dhan.nearest_futures")
    @patch("data.dhan.fetch_futures_intraday")
    def test_day_rollover_still_clears_previous_session(self, mock_fetch, mock_nearest, _trading):
        session_now, clock = self._session_clock()
        stale_ts = (session_now - timedelta(days=10)).replace(hour=15, minute=20, second=0, microsecond=0)
        save_bar(self._bar(stale_ts, close=21000.0, delta=50.0))
        save_signal(SentinelSignal(
            event_id="sig_previous_session",
            instrument_id="TEST-FUT",
            timeframe="15s",
            bar_start_ts=stale_ts,
            type="CONTINUATION_BEAR",
            category="BEARISH",
            bias="SHORT",
            score=60,
            confidence="MEDIUM",
            rvol_tod=1.2,
            delta=-100.0,
            delta_ratio=-0.4,
            oi_change=0,
            context_tags=["MICRO_BURST"],
            insight_text="⚡ Seller sweep from the prior session.",
            invalidation_level=21020.0,
            trigger_speed="MICRO_BURST",
        ))
        bar_ts = session_now - timedelta(minutes=4)
        mock_nearest.return_value = [{"security_id": 99999, "symbol": "TEST-FUT", "lot": 65}]
        mock_fetch.return_value = pd.DataFrame([{
            "open": 22500.0, "high": 22510.0, "low": 22495.0, "close": 22508.0,
            "volume": 5000, "oi": 1000000, "timestamp": int(bar_ts.timestamp()),
        }])
        mgr = LiveSentinelManager()
        with patch("sentinel.feed.live.datetime", clock):
            ok = mgr.sync_live_session("TEST-FUT", "TEST", force=True)
        self.assertTrue(ok)
        signals = list_recent_signals("TEST-FUT", limit=20)
        self.assertNotIn("sig_previous_session", [s.event_id for s in signals])
        bars = list_recent_bars("TEST-FUT", "1m", limit=10, session_only=False, _auto_populate=False)
        self.assertTrue(any(b.close == 22508.0 for b in bars))
        self.assertFalse(any(b.close == 21000.0 for b in bars))

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
