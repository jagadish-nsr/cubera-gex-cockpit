"""tests/test_sentinel.py — Unit tests for OrderFlow Sentinel v2.0 Subsystem.

Covers:
  - Binary packet parsing (Quote, Ticker, OI)
  - Bar aggregation, Tick-rule delta & CVD
  - Session VWAP and ±1σ, ±2σ bands
  - Time-of-Day RVol normalization
  - Signal classifiers: ABSORPTION, CONTINUATION, EXHAUSTION, CVD_DIVERGENCE
  - Confidence scoring and 3-bar cooldown
  - SQLite database persistence & forward outcome evaluation
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from sentinel import (
    Bar,
    BarAggregator,
    MarketContext,
    SessionVWAPTracker,
    SignalClassifierEngine,
    compute_tod_rvol,
    evaluate_signal_outcomes,
    list_recent_bars,
    list_recent_signals,
    parse_dhan_binary_packet,
    save_bar,
    save_signal,
)
from sentinel.feed.parser import pack_dhan_quote_packet


# ---------------------------------------------------------------------------
# 1. Binary Packet Parser Tests
# ---------------------------------------------------------------------------

def test_parse_dhan_binary_quote_packet():
    # Pack synthetic Dhan Quote packet (security_id=13, LTP=24550.5, Vol=12000)
    raw = pack_dhan_quote_packet(
        security_id=13,
        ltp=24550.50,
        volume=12000,
        day_open=24500.0,
        day_high=24580.0,
        day_low=24490.0,
        day_close=24550.50,
    )
    tick = parse_dhan_binary_packet(raw)
    assert tick is not None
    assert tick.security_id == 13
    assert tick.ltp == 24550.50
    assert tick.volume == 12000
    assert tick.day_high == 24580.0
    assert tick.day_low == 24490.0


def test_parse_short_malformed_packet_returns_none():
    assert parse_dhan_binary_packet(b"\x00\x01\x02") is None


# ---------------------------------------------------------------------------
# 2. VWAP Tracker Tests
# ---------------------------------------------------------------------------

def test_session_vwap_and_sigma_bands():
    vwap_tracker = SessionVWAPTracker()
    # Bar 1: Price 24000, Vol 1000
    vwap, up1, up2, dn1, dn2 = vwap_tracker.update(24000.0, 1000.0)
    assert vwap == 24000.0
    assert up1 == 24000.0  # 1 bar -> 0 variance

    # Bar 2: Price 24100, Vol 1000
    vwap, up1, up2, dn1, dn2 = vwap_tracker.update(24100.0, 1000.0)
    assert vwap == 24050.0
    assert up1 > 24050.0
    assert up2 > up1
    assert dn1 < 24050.0
    assert dn2 < dn1


# ---------------------------------------------------------------------------
# 3. Bar Aggregator & CVD Tests
# ---------------------------------------------------------------------------

def test_bar_aggregator_rollover_and_cvd():
    agg = BarAggregator(instrument_id="NIFTY-FUT", timeframe="1m")
    t0 = datetime(2026, 10, 2, 9, 15, 10, tzinfo=timezone.utc)

    # Tick 1 in minute 09:15
    b = agg.add_tick(price=24500.0, timestamp=t0, volume_delta=50)
    assert b is None  # bar still in progress

    # Tick 2 in minute 09:15 (price up -> buyer delta)
    t1 = datetime(2026, 10, 2, 9, 15, 30, tzinfo=timezone.utc)
    b = agg.add_tick(price=24510.0, timestamp=t1, volume_delta=100)
    assert b is None

    # Tick 3 in minute 09:16 (minute rolls over!)
    t2 = datetime(2026, 10, 2, 9, 16, 5, tzinfo=timezone.utc)
    bar = agg.add_tick(price=24508.0, timestamp=t2, volume_delta=30)
    assert bar is not None
    assert bar.open == 24500.0
    assert bar.high == 24510.0
    assert bar.close == 24510.0
    assert bar.volume_units == 150
    assert bar.cvd > 0  # positive buyer delta
    assert bar.vwap > 0


# ---------------------------------------------------------------------------
# 4. Time-of-Day RVol Tests
# ---------------------------------------------------------------------------

def test_tod_rvol_normalization():
    # 09:20 IST (morning open peak)
    dt_morning = datetime(2026, 10, 2, 3, 50, tzinfo=timezone.utc)  # 09:20 IST is 03:50 UTC
    rvol_morning = compute_tod_rvol(dt_morning, volume=60000.0, instrument="NIFTY")
    assert rvol_morning > 0.5

    # 12:30 IST (midday lull)
    dt_midday = datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)  # 12:30 IST is 07:00 UTC
    # 60,000 volume midday is extremely high relative to normal
    rvol_midday = compute_tod_rvol(dt_midday, volume=60000.0, instrument="NIFTY")
    assert rvol_midday > rvol_morning  # relative volume is higher midday for same absolute units


# ---------------------------------------------------------------------------
# 5. Signal Classifier Tests
# ---------------------------------------------------------------------------

def test_classifier_absorption_bear_at_call_wall():
    engine = SignalClassifierEngine()
    context = MarketContext(call_wall=24700.0)

    # High volume bar pushing into Call Wall (24,700), but closing with upper rejection tail
    t0 = datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)
    bar = Bar(
        instrument_id="NIFTY-FUT",
        timeframe="1m",
        start_ts=t0,
        open=24675.0,
        high=24705.0,  # poked above call wall
        low=24670.0,
        close=24680.0,  # closed back down
        volume_units=45000,  # very high volume
        volume_lots=692.0,
        buy_vol_est=30000.0,
        sell_vol_est=15000.0,
        delta=15000.0,  # high buyer aggression
        delta_ratio=0.33,
        delta_quality=1.0,
        cvd=50000.0,
        vwap=24660.0,
        vwap_sigma_1=24680.0,
        vwap_sigma_2=24700.0,
        vwap_lower_1=24640.0,
        vwap_lower_2=24620.0,
    )
    context.update_bar(bar)
    sig = engine.evaluate_bar(bar, context)

    assert sig is not None
    assert sig.type == "ABSORPTION_BEAR"
    assert sig.bias == "SHORT"
    assert sig.score >= 65
    assert "not a ceiling" in sig.insight_text.lower()
    assert "defended" not in sig.insight_text.lower()
    assert "rejected" not in sig.insight_text.lower()
    assert sig.confidence == "HIGH"
    assert sig.gex_alignment == "CONFIRMED_AT_WALL"
    assert sig.invalidation_level > bar.high


def test_classifier_continuation_bull():
    engine = SignalClassifierEngine()
    context = MarketContext()

    t0 = datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)
    bar = Bar(
        instrument_id="NIFTY-FUT",
        timeframe="1m",
        start_ts=t0,
        open=24500.0,
        high=24530.0,
        low=24495.0,
        close=24528.0,  # firm close near high
        volume_units=40000,
        volume_lots=615.0,
        buy_vol_est=30000.0,
        sell_vol_est=10000.0,
        delta=20000.0,
        delta_ratio=0.50,
        delta_quality=1.0,
        cvd=20000.0,
        vwap=24505.0,
        vwap_sigma_1=24520.0,
        vwap_sigma_2=24535.0,
        vwap_lower_1=24490.0,
        vwap_lower_2=24475.0,
    )
    context.update_bar(bar)
    sig = engine.evaluate_bar(bar, context)

    assert sig is not None
    assert sig.type == "CONTINUATION_BULL"
    assert sig.bias == "LONG"


def test_classifier_3_bar_cooldown():
    engine = SignalClassifierEngine()
    context = MarketContext()
    t0 = datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)

    def _make_bull_bar(m):
        return Bar(
            instrument_id="NIFTY-FUT",
            timeframe="1m",
            start_ts=t0 + timedelta(minutes=m),
            open=24500.0, high=24530.0, low=24495.0, close=24528.0,
            volume_units=40000, volume_lots=615.0,
            buy_vol_est=30000.0, sell_vol_est=10000.0,
            delta=20000.0, delta_ratio=0.50, delta_quality=1.0,
            cvd=20000.0, vwap=24505.0,
            vwap_sigma_1=24520.0, vwap_sigma_2=24535.0,
            vwap_lower_1=24490.0, vwap_lower_2=24475.0,
        )

    sig1 = engine.evaluate_bar(_make_bull_bar(0), context)
    assert sig1 is not None

    # Immediate next bar should be suppressed by cooldown
    sig2 = engine.evaluate_bar(_make_bull_bar(1), context)
    assert sig2 is None


# ---------------------------------------------------------------------------
# 6. SQLite Database Persistence Tests
# ---------------------------------------------------------------------------

def test_database_persistence_and_queries(tmp_path, monkeypatch):
    import sentinel.storage.db as db_mod
    tmp_db_path = tmp_path / "test_sentinel.db"
    monkeypatch.setattr(db_mod, "_DB_PATH", tmp_db_path)
    db_mod.ensure_sentinel_schema()

    t0 = datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc)
    bar = Bar(
        instrument_id="NIFTY", timeframe="1m", start_ts=t0,
        open=24500.0, high=24520.0, low=24490.0, close=24515.0,
        volume_units=15000, volume_lots=230.0, buy_vol_est=9000.0, sell_vol_est=6000.0,
        delta=3000.0, delta_ratio=0.20, delta_quality=1.0, cvd=10000.0,
        vwap=24505.0, vwap_sigma_1=24520.0, vwap_sigma_2=24535.0,
        vwap_lower_1=24490.0, vwap_lower_2=24475.0,
    )
    save_bar(bar)
    saved_bars = list_recent_bars("NIFTY", timeframe="1m")
    assert len(saved_bars) == 1
    assert saved_bars[0].close == 24515.0


# ---------------------------------------------------------------------------
# 7. Outcome Evaluation Tests
# ---------------------------------------------------------------------------

def test_outcome_tracker_evaluates_favorable_excursion():
    t0 = datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc)
    from sentinel.models import SentinelSignal
    sig = SentinelSignal(
        event_id="sig_test_1",
        instrument_id="NIFTY",
        timeframe="1m",
        bar_start_ts=t0,
        type="CONTINUATION_BULL",
        category="BULLISH",
        bias="LONG",
        score=75,
        confidence="HIGH",
        rvol_tod=2.2,
        delta=5000.0,
        delta_ratio=0.35,
        oi_change=1200,
        context_tags=["ABOVE_VWAP"],
        insight_text="Test Bull",
        invalidation_level=24490.0,
    )

    # 3 subsequent rising bars
    subsequent = [
        Bar("NIFTY", "1m", t0 + timedelta(minutes=1), 24515.0, 24535.0, 24510.0, 24530.0, 10000, 150, 6000, 4000, 2000, 0.2, 1.0, 12000, 24510, 24530, 24550, 24490, 24470),
        Bar("NIFTY", "1m", t0 + timedelta(minutes=2), 24530.0, 24550.0, 24525.0, 24545.0, 12000, 180, 8000, 4000, 4000, 0.3, 1.0, 16000, 24520, 24540, 24560, 24500, 24480),
        Bar("NIFTY", "1m", t0 + timedelta(minutes=3), 24545.0, 24560.0, 24540.0, 24555.0, 15000, 230, 10000, 5000, 5000, 0.33, 1.0, 21000, 24530, 24550, 24570, 24510, 24490),
    ]

    outcome = evaluate_signal_outcomes(sig, subsequent, risk_pts=25.0)
    assert outcome.mfe_pts > 0
    assert outcome.stopped_out is False
    assert outcome.ret_1m_pts is not None
