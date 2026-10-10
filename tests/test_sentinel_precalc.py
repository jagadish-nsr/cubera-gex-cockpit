"""Tests for OrderFlow Sentinel chart profile pre-calculation, caching, and instant switching."""

from datetime import datetime, timezone, timedelta
import time
from sentinel.models import Bar
from app.cockpit import (
    _build_sentinel_chart_spec,
    _prewarm_all_profiles,
    _SENTINEL_CHART_CACHE,
    ORDERFLOW_PROFILES,
)


def _make_sample_bars(n: int = 30) -> list[Bar]:
    base_ts = datetime(2026, 10, 8, 9, 15, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(n):
        bars.append(
            Bar(
                instrument_id="NIFTY-FUT",
                timeframe="1m",
                start_ts=base_ts + timedelta(minutes=i),
                open=22650.0 + i,
                high=22660.0 + i,
                low=22645.0 + i,
                close=22655.0 + i,
                volume_units=1200 + i * 10,
                volume_lots=18.0,
                buy_vol_est=700.0,
                sell_vol_est=500.0,
                delta=200.0,
                delta_ratio=0.18,
                delta_quality=1.0,
                cvd=200.0 * (i + 1),
                vwap=22652.0 + i * 0.5,
                vwap_sigma_1=22662.0 + i * 0.5,
                vwap_sigma_2=22672.0 + i * 0.5,
                vwap_lower_1=22642.0 + i * 0.5,
                vwap_lower_2=22632.0 + i * 0.5,
                oi=150000,
                delta_oi=1200,
            )
        )
    return bars


def test_prewarm_all_profiles_caches_all_presets():
    """Verify that _prewarm_all_profiles pre-calculates and caches all 4 profile charts in advance."""
    bars = _make_sample_bars(30)
    cw, pw, gf = 22800.0, 22500.0, 22650.0

    # Clear cache for isolated test
    _SENTINEL_CHART_CACHE.clear()

    _prewarm_all_profiles(
        bars=bars,
        call_wall=cw,
        put_wall=pw,
        gamma_flip=gf,
        signals=None,
        chart_style="Candlesticks",
        main_height=340,
    )

    # All 4 profiles must be pre-calculated and cached
    assert len(_SENTINEL_CHART_CACHE) == len(ORDERFLOW_PROFILES)

    # Verify that requesting any of the 4 profiles hits the cache immediately (< 1ms)
    for prof_name, cfg in ORDERFLOW_PROFILES.items():
        t0 = time.perf_counter()
        chart = _build_sentinel_chart_spec(
            bars=bars,
            call_wall=cw,
            put_wall=pw,
            gamma_flip=gf,
            signals=None,
            show_cvd=cfg["cvd"],
            show_delta=cfg["delta"],
            show_vwap=cfg["vwap"],
            show_bands=cfg["bands"],
            show_walls=cfg["walls"],
            show_ema=cfg["ema"],
            chart_style="Candlesticks",
            main_height=340,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert chart is not None, f"Chart for {prof_name} must exist"
        assert elapsed_ms < 1.0, f"Cache retrieval for {prof_name} must be < 1ms (was {elapsed_ms:.4f}ms)"
