"""tests/test_sentinel_efficiency.py — Unit tests for Sentinel Signal Efficiency Analytics & Retention."""

from datetime import datetime, timezone, timedelta
import pytest

from sentinel.models import SentinelSignal, SignalOutcome
from sentinel.storage.db import save_signal, save_outcome, ensure_sentinel_schema, purge_old_sentinel_data, clear_sentinel_data
from sentinel.engine.efficiency import compute_signal_efficiency


@pytest.fixture(autouse=True)
def setup_db():
    ensure_sentinel_schema()
    yield


def test_signal_efficiency_empty():
    """Empty database returns zeroed summary without raising exceptions."""
    clear_sentinel_data("TEST-FUT")
    rep = compute_signal_efficiency("TEST-FUT", period_type="daily")
    assert rep.total_signals == 0
    assert rep.overall_hit_rate == 0.0
    assert rep.timeline == []


def test_signal_efficiency_aggregations():
    """Verify daily and weekly hit rate, edge ratio, and expectancy aggregations."""
    clear_sentinel_data("TEST-EFF")
    base_ts = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone.utc)

    # 1. Bullish winner signal
    sig1 = SentinelSignal(
        event_id="sig_test_1",
        instrument_id="TEST-EFF",
        timeframe="1m",
        bar_start_ts=base_ts,
        type="CONTINUATION_BULL",
        category="BULLISH",
        bias="LONG",
        score=80,
        confidence="HIGH",
        rvol_tod=2.5,
        delta=5000.0,
        delta_ratio=0.3,
        oi_change=10000,
        context_tags=["ABOVE_VWAP"],
        insight_text="Test bull",
        invalidation_level=24000.0,
        gex_alignment="CONFIRMED_AT_WALL",
    )
    save_signal(sig1)
    out1 = SignalOutcome(
        event_id="sig_test_1",
        instrument_id="TEST-EFF",
        signal_type="CONTINUATION_BULL",
        entry_price=24025.0,
        invalidation_level=24000.0,
        ret_1m_pts=15.0,
        ret_3m_pts=25.0,
        ret_5m_pts=30.0,
        ret_15m_pts=35.0,
        ret_30m_pts=40.0,
        mfe_pts=45.0,
        mae_pts=5.0,
        stopped_out=False,
        target_hit=True,
    )
    save_outcome(out1)

    # 2. Bearish stopped-out signal
    sig2 = SentinelSignal(
        event_id="sig_test_2",
        instrument_id="TEST-EFF",
        timeframe="1m",
        bar_start_ts=base_ts + timedelta(minutes=15),
        type="ABSORPTION_BEAR",
        category="BEARISH",
        bias="SHORT",
        score=75,
        confidence="HIGH",
        rvol_tod=3.0,
        delta=-4000.0,
        delta_ratio=-0.25,
        oi_change=-5000,
        context_tags=["NEAR_CALL_WALL"],
        insight_text="Test bear",
        invalidation_level=24100.0,
        gex_alignment="NEUTRAL",
    )
    save_signal(sig2)
    out2 = SignalOutcome(
        event_id="sig_test_2",
        instrument_id="TEST-EFF",
        signal_type="ABSORPTION_BEAR",
        entry_price=24075.0,
        invalidation_level=24100.0,
        ret_1m_pts=-10.0,
        ret_3m_pts=-20.0,
        ret_5m_pts=-25.0,
        ret_15m_pts=-30.0,
        ret_30m_pts=-30.0,
        mfe_pts=5.0,
        mae_pts=30.0,
        stopped_out=True,
        target_hit=False,
    )
    save_outcome(out2)

    # Compute daily report
    rep = compute_signal_efficiency("TEST-EFF", period_type="daily")
    assert rep.total_signals == 2
    assert rep.overall_hit_rate == 50.0
    assert rep.overall_stop_rate == 50.0
    assert rep.overall_avg_mfe == 25.0   # (45 + 5) / 2
    assert rep.overall_avg_mae == 17.5   # (5 + 30) / 2
    assert rep.overall_edge_ratio == 1.43 # 25 / 17.5
    assert rep.expectancy_curve["+5m"] == 2.5 # (30 - 25) / 2

    # Verify breakdowns
    assert len(rep.by_type) == 2
    bull_item = next(b for b in rep.by_type if b.label == "CONTINUATION_BULL")
    assert bull_item.hit_rate_pct == 100.0
    assert bull_item.avg_mfe_pts == 45.0

    # Compute weekly report
    rep_w = compute_signal_efficiency("TEST-EFF", period_type="weekly")
    assert rep_w.total_signals == 2
    assert len(rep_w.timeline) == 1

    # Cleanup
    clear_sentinel_data("TEST-EFF")


def test_purge_old_sentinel_data():
    """Verify retention purge function executes successfully."""
    res = purge_old_sentinel_data(bars_1m_days=9999, bars_5m_days=9999, signals_days=9999)
    assert "bars_1m" in res
    assert "bars_5m" in res
    assert "signals" in res
    assert "outcomes" in res
