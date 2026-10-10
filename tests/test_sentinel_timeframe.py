"""Tests for Sentinel 1m to 5m candle aggregation and timeframe retrieval."""

from datetime import datetime, timezone, timedelta
import pytest
from sentinel.models import Bar
from sentinel.engine.aggregator import aggregate_1m_to_5m
from sentinel.storage.db import (
    ensure_sentinel_schema,
    clear_sentinel_data,
    save_bar,
    list_recent_bars,
)


def _make_1m_bar(instrument_id: str, ts: datetime, close_offset: float = 0.0) -> Bar:
    return Bar(
        instrument_id=instrument_id,
        timeframe="1m",
        start_ts=ts,
        open=24500.0 + close_offset,
        high=24510.0 + close_offset,
        low=24490.0 + close_offset,
        close=24505.0 + close_offset,
        volume_units=1000,
        volume_lots=15.4,
        buy_vol_est=600.0,
        sell_vol_est=400.0,
        delta=200.0,
        delta_ratio=0.20,
        delta_quality=1.0,
        cvd=200.0,
        vwap=24502.0,
        vwap_sigma_1=24512.0,
        vwap_sigma_2=24522.0,
        vwap_lower_1=24492.0,
        vwap_lower_2=24482.0,
        oi=100000,
        delta_oi=500,
    )


def test_aggregate_1m_to_5m_buckets():
    """Verify that five 1m bars are aggregated into a single 5m bar with correct boundaries."""
    base_ts = datetime(2026, 10, 6, 9, 15, 0, tzinfo=timezone.utc)
    bars_1m = [_make_1m_bar("NIFTY-FUT", base_ts + timedelta(minutes=i), float(i)) for i in range(5)]
    
    bars_5m = aggregate_1m_to_5m(bars_1m)
    assert len(bars_5m) == 1
    b5 = bars_5m[0]
    assert b5.timeframe == "5m"
    assert b5.start_ts == base_ts
    assert b5.open == bars_1m[0].open
    assert b5.close == bars_1m[-1].close
    assert b5.high == max(b.high for b in bars_1m)
    assert b5.low == min(b.low for b in bars_1m)
    assert b5.volume_units == 5000
    assert b5.delta == 1000.0
    assert b5.delta_oi == 2500


def test_5m_retrieval_fallback_synthesis(tmp_path, monkeypatch):
    """Verify that querying 5m bars when only 1m bars are in the DB synthesizes 5m bars on the fly."""
    import sentinel.storage.db as db_mod
    tmp_db_path = tmp_path / "test_sentinel.db"
    monkeypatch.setattr(db_mod, "_DB_PATH", tmp_db_path)
    
    ensure_sentinel_schema()
    base_ts = datetime(2026, 10, 6, 9, 15, 0, tzinfo=timezone.utc)
    for i in range(10):
        b1 = _make_1m_bar("NIFTY-FUT", base_ts + timedelta(minutes=i), float(i))
        save_bar(b1)
        
    # Verify 1m stored
    stored_1m = list_recent_bars("NIFTY-FUT", timeframe="1m")
    assert len(stored_1m) == 10
    
    # Query 5m: should synthesize 2 bars (9:15 and 9:20)
    stored_5m = list_recent_bars("NIFTY-FUT", timeframe="5m")
    assert len(stored_5m) == 2
    assert stored_5m[0].timeframe == "5m"
    assert stored_5m[1].timeframe == "5m"

    # Query 3m: should synthesize 4 bars (9:15, 9:18, 9:21, 9:24)
    stored_3m = list_recent_bars("NIFTY-FUT", timeframe="3m")
    assert len(stored_3m) >= 3
    assert stored_3m[0].timeframe == "3m"


def test_chart_customization_call():
    """Verify that _render_sentinel_chart accepts all modular customization parameters without error."""
    from app.cockpit import _render_sentinel_chart
    from unittest.mock import patch

    base_ts = datetime(2026, 10, 6, 9, 15, 0, tzinfo=timezone.utc)
    bars = [_make_1m_bar("NIFTY-FUT", base_ts + timedelta(minutes=i), float(i)) for i in range(10)]

    # Patch st.altair_chart so we can verify the compound chart is built
    with patch("streamlit.altair_chart") as mock_alt:
        # 1. Full OrderFlow (all on, with EMAs)
        _render_sentinel_chart(
            bars,
            call_wall=24600.0,
            put_wall=24400.0,
            show_cvd=True,
            show_delta=True,
            show_vwap=True,
            show_bands=True,
            show_walls=True,
            show_ema=True,
            chart_style="Candlesticks",
            main_height=340,
        )
        assert mock_alt.called
        chart_obj = mock_alt.call_args[0][0]
        assert chart_obj is not None

        mock_alt.reset_mock()

        # 2. Pure Price (minimalist, no subplots, Area style)
        _render_sentinel_chart(
            bars,
            call_wall=24600.0,
            put_wall=24400.0,
            show_cvd=False,
            show_delta=False,
            show_vwap=False,
            show_bands=False,
            show_walls=True,
            show_ema=False,
            chart_style="Area / Line",
            main_height=260,
        )
        assert mock_alt.called
        chart_obj = mock_alt.call_args[0][0]
        assert chart_obj is not None
