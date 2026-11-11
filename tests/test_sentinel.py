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
from sentinel.models import SentinelSignal
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
        oi_fresh=True,
    )
    context.update_bar(bar)
    sig = engine.evaluate_bar(bar, context)

    assert sig is not None
    assert sig.type == "CONTINUATION_BULL"
    assert sig.bias == "LONG"


def test_continuation_skips_when_futures_oi_is_frozen():
    """A flat OI reprint is not a positioning tape (2026-11-05 all-day freeze)."""
    engine = SignalClassifierEngine()
    context = MarketContext()
    t0 = datetime(2026, 11, 5, 7, 0, tzinfo=timezone.utc)
    bar = Bar(
        instrument_id="NIFTY-FUT",
        timeframe="1m",
        start_ts=t0,
        open=24500.0,
        high=24530.0,
        low=24495.0,
        close=24528.0,
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
        oi=13_481_715,
        delta_oi=0,
        oi_fresh=False,
    )
    context.update_bar(bar)
    assert engine.evaluate_bar(bar, context) is None


def test_aggregator_marks_oi_stale_after_five_flat_bars():
    agg = BarAggregator("NIFTY-FUT", timeframe="1m")
    t0 = datetime(2026, 11, 5, 3, 45, tzinfo=timezone.utc)
    bars = []
    for i in range(7):
        ts = t0 + timedelta(minutes=i)
        # One tick to open the minute, one in the next minute to finalize.
        agg.add_tick(24500.0 + i, ts, volume_delta=100, oi=13_481_715)
        done = agg.add_tick(24501.0 + i, ts + timedelta(minutes=1), volume_delta=100, oi=13_481_715)
        if done is not None:
            bars.append(done)
    assert len(bars) >= 5
    assert bars[0].oi_fresh is True
    assert bars[3].oi_fresh is True
    assert bars[4].oi_fresh is False
    assert bars[-1].oi_fresh is False


def test_micro_continuation_needs_a_gamma_strike():
    """Floor-68 micro CONTINUATION floods without a wall gate (2026-11-05)."""
    engine = SignalClassifierEngine()
    context = MarketContext()
    context.call_wall = 24600.0
    context.put_wall = 24400.0
    burst = {
        "instrument_id": "NIFTY-FUT",
        "timestamp": datetime(2026, 11, 5, 8, 0, tzinfo=timezone.utc),
        "volume_15s": 2000,
        "delta_15s": 1500,
        "delta_ratio_15s": 0.55,
        "latest_price": 24550.0,
        "depth_imbalance": 0.3,
    }
    assert engine.evaluate_micro_burst(burst, context) is None

    burst_at_wall = dict(burst, latest_price=24605.0)
    sig = engine.evaluate_micro_burst(burst_at_wall, context)
    assert sig is not None
    assert sig.type == "CONTINUATION_BULL"
    assert sig.score >= 70
    assert sig.trigger_speed == "MICRO_BURST"


def test_classifier_exhaustion_up_and_down_categories():
    """Verify EXHAUSTION_UP is strictly BEARISH/SHORT and EXHAUSTION_DOWN is BULLISH/LONG."""
    engine = SignalClassifierEngine()
    # EXHAUSTION_UP only fires at the call wall (14d tape: ungated fades were 9% hit).
    context = MarketContext(call_wall=24540.0)

    # Buying climax rejection at midday (12:15 IST is 06:45 UTC) -> EXHAUSTION_UP (BEARISH / SHORT)
    # Using delta_ratio 0.05 so absorption / continuation are bypassed and exhaustion fires
    t0 = datetime(2026, 10, 2, 6, 45, tzinfo=timezone.utc)
    bar_up = Bar(
        instrument_id="NIFTY-FUT",
        timeframe="1m",
        start_ts=t0,
        open=24500.0,
        high=24550.0,
        low=24495.0,
        close=24505.0,  # close near low of bar (close_pos <= 0.35), upper wick = 45/55 >= 0.40
        volume_units=60000,  # rvol >> 2.2
        volume_lots=923.0,
        buy_vol_est=31500.0,
        sell_vol_est=28500.0,
        delta=3000.0,
        delta_ratio=0.05,  # within (-0.18, 0.18) to avoid absorption
        delta_quality=1.0,
        cvd=3000.0,
        vwap=24500.0,
        vwap_sigma_1=24520.0,
        vwap_sigma_2=24540.0,
        vwap_lower_1=24480.0,
        vwap_lower_2=24460.0,
    )
    context.update_bar(bar_up)
    sig_up = engine.evaluate_bar(bar_up, context)
    assert sig_up is not None
    assert sig_up.type == "EXHAUSTION_UP"
    assert sig_up.category == "BEARISH"
    assert sig_up.bias == "SHORT"


def test_classifier_exhaustion_up_skips_without_call_wall():
    engine = SignalClassifierEngine()
    context = MarketContext()  # no call wall
    t0 = datetime(2026, 10, 2, 6, 45, tzinfo=timezone.utc)
    bar_up = Bar(
        instrument_id="NIFTY-FUT",
        timeframe="1m",
        start_ts=t0,
        open=24500.0,
        high=24550.0,
        low=24495.0,
        close=24505.0,
        volume_units=60000,
        volume_lots=923.0,
        buy_vol_est=31500.0,
        sell_vol_est=28500.0,
        delta=3000.0,
        delta_ratio=0.05,
        delta_quality=1.0,
        cvd=3000.0,
        vwap=24500.0,
        vwap_sigma_1=24520.0,
        vwap_sigma_2=24540.0,
        vwap_lower_1=24480.0,
        vwap_lower_2=24460.0,
    )
    context.update_bar(bar_up)
    assert engine.evaluate_bar(bar_up, context) is None

    # Selling climax rejection -> EXHAUSTION_DOWN (BULLISH / LONG)
    engine_down = SignalClassifierEngine()
    context_down = MarketContext()
    bar_down = Bar(
        instrument_id="NIFTY-FUT",
        timeframe="1m",
        start_ts=t0 + timedelta(minutes=5),
        open=24500.0,
        high=24505.0,
        low=24450.0,
        close=24495.0,  # close near high of bar (close_pos >= 0.65), lower wick = 45/55 >= 0.40
        volume_units=60000,  # rvol >> 2.2
        volume_lots=923.0,
        buy_vol_est=28500.0,
        sell_vol_est=31500.0,
        delta=-3000.0,
        delta_ratio=-0.05,  # within (-0.18, 0.18) to avoid absorption
        delta_quality=1.0,
        cvd=-3000.0,
        vwap=24500.0,
        vwap_sigma_1=24520.0,
        vwap_sigma_2=24540.0,
        vwap_lower_1=24480.0,
        vwap_lower_2=24460.0,
    )
    context_down.update_bar(bar_down)
    sig_down = engine_down.evaluate_bar(bar_down, context_down)
    assert sig_down is not None
    assert sig_down.type == "EXHAUSTION_DOWN"
    assert sig_down.category == "BULLISH"
    assert sig_down.bias == "LONG"


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
            oi_fresh=True,  # CONTINUATION requires proven OI move (default is False)
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


def test_5m_close_invalidation_and_target_rules():
    """Verify that a signal is invalidated or reaches target ONLY on completed 5-min candle closes."""
    t0 = datetime(2026, 10, 15, 9, 20, 0, tzinfo=timezone.utc)
    sig_long = SentinelSignal(
        event_id="sig_long_test",
        instrument_id="NIFTY",
        timeframe="1m",
        bar_start_ts=t0,
        type="ABSORPTION_LOW",
        category="BULLISH",
        bias="LONG",
        score=80,
        confidence="HIGH",
        rvol_tod=2.0,
        delta=3000.0,
        delta_ratio=0.3,
        oi_change=500,
        context_tags=["TEST"],
        insight_text="Test Bull Absorption",
        invalidation_level=24480.0,
        entry_price=24500.0,
        target_1_pts=50.0,  # Target is 24550.0
    )

    # 1. 1-minute bars with wick dipping below 24480, but 5m candle closes at 24485 (above invalidation)
    # 09:20–09:25 interval (minutes 20, 21, 22, 23, 24)
    bars_wick_dip = [
        Bar("NIFTY", "1m", t0 + timedelta(minutes=1), 24500.0, 24505.0, 24470.0, 24485.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        Bar("NIFTY", "1m", t0 + timedelta(minutes=2), 24485.0, 24490.0, 24465.0, 24482.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        Bar("NIFTY", "1m", t0 + timedelta(minutes=3), 24482.0, 24488.0, 24478.0, 24484.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        Bar("NIFTY", "1m", t0 + timedelta(minutes=4), 24484.0, 24490.0, 24480.0, 24486.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        # 09:25 bar starts next interval, closing the 09:20–09:25 candle with close=24486
        Bar("NIFTY", "1m", t0 + timedelta(minutes=5), 24486.0, 24495.0, 24485.0, 24490.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
    ]
    out1 = evaluate_signal_outcomes(sig_long, bars_wick_dip)
    # Wick pierced to 24465, but 5m candle closed at 24486 (> 24480) -> NOT invalidated!
    assert out1.stopped_out is False
    assert out1.status == "ACTIVE"

    # 2. 5-minute candle closes below 24480 (at 24475) -> INVALIDATED!
    bars_5m_inval = [
        Bar("NIFTY", "1m", t0 + timedelta(minutes=1), 24500.0, 24505.0, 24480.0, 24485.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        Bar("NIFTY", "1m", t0 + timedelta(minutes=2), 24485.0, 24490.0, 24475.0, 24480.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        Bar("NIFTY", "1m", t0 + timedelta(minutes=3), 24480.0, 24482.0, 24472.0, 24476.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        Bar("NIFTY", "1m", t0 + timedelta(minutes=4), 24476.0, 24478.0, 24470.0, 24475.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        # 09:25 bar closes the candle
        Bar("NIFTY", "1m", t0 + timedelta(minutes=5), 24475.0, 24480.0, 24472.0, 24478.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
    ]
    out2 = evaluate_signal_outcomes(sig_long, bars_5m_inval)
    # The 09:20–09:25 5m candle closed at 24475 (< 24480) -> INVALIDATED!
    assert out2.stopped_out is True
    assert out2.status == "INVALIDATED"

    # 3. 5-minute candle closes above target (24555 >= 24550) -> TARGET_HIT!
    bars_5m_target = [
        Bar("NIFTY", "1m", t0 + timedelta(minutes=1), 24500.0, 24520.0, 24500.0, 24518.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        Bar("NIFTY", "1m", t0 + timedelta(minutes=2), 24518.0, 24535.0, 24515.0, 24530.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        Bar("NIFTY", "1m", t0 + timedelta(minutes=3), 24530.0, 24545.0, 24528.0, 24540.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        Bar("NIFTY", "1m", t0 + timedelta(minutes=4), 24540.0, 24560.0, 24538.0, 24555.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        # 09:25 bar closes the candle
        Bar("NIFTY", "1m", t0 + timedelta(minutes=5), 24555.0, 24565.0, 24550.0, 24560.0, 1000, 15, 500, 500, 0, 0, 1, 0, 0, 0, 0, 0, 0),
    ]
    out3 = evaluate_signal_outcomes(sig_long, bars_5m_target)
    assert out3.target_hit is True
    assert out3.stopped_out is False
    assert out3.status == "TARGET_HIT"


# ---------------------------------------------------------------------------
# GEX context wiring / magnet gate (2026-11-09 deep-dive fixes)
# ---------------------------------------------------------------------------

def _cont_bull_bar(instrument_id: str, t0: datetime, close: float, low: float | None = None) -> Bar:
    lo = close - 30.0 if low is None else low
    return Bar(
        instrument_id=instrument_id,
        timeframe="1m",
        start_ts=t0,
        open=lo + 5.0,
        high=close + 2.0,
        low=lo,
        close=close,
        volume_units=40000,
        volume_lots=615.0,
        buy_vol_est=30000.0,
        sell_vol_est=10000.0,
        delta=20000.0,
        delta_ratio=0.50,
        delta_quality=1.0,
        cvd=20000.0,
        vwap=close - 20.0,
        vwap_sigma_1=close - 5.0,
        vwap_sigma_2=close + 10.0,
        vwap_lower_1=close - 35.0,
        vwap_lower_2=close - 50.0,
        oi_fresh=True,
    )


def test_walls_reach_context_tags():
    """Hand-check: spot within Nifty 50 pts of call wall → NEAR_CALL_WALL."""
    ctx = MarketContext(symbol="NIFTY", call_wall=25000.0, put_wall=24900.0, magnet=25000.0)
    bar = _cont_bull_bar("NIFTY-FUT", datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc), 25020.0)
    tags = ctx.get_context_tags(bar)
    assert "NEAR_CALL_WALL" in tags
    assert "NEAR_MAGNET" in tags
    assert "NEAR_PUT_WALL" not in tags


def test_sensex_radius_tags_wall_120_pts_away():
    """Sensex proximity is 150 pts — a wall 120 away must tag; Nifty 50 would miss."""
    from sentinel.engine.context import gex_proximity_radii

    assert gex_proximity_radii("SENSEX")[0] == 150.0
    assert gex_proximity_radii("NIFTY")[0] == 50.0
    assert gex_proximity_radii("BANKNIFTY-FUT")[0] == 100.0

    ctx = MarketContext(symbol="SENSEX", call_wall=81000.0, magnet=81000.0)
    bar = _cont_bull_bar("SENSEX-FUT", datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc), 81120.0)
    tags = ctx.get_context_tags(bar)
    assert "NEAR_CALL_WALL" in tags
    assert "NEAR_MAGNET" in tags

    nifty = MarketContext(symbol="NIFTY", call_wall=25000.0)
    far = _cont_bull_bar("NIFTY-FUT", datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc), 25120.0)
    assert "NEAR_CALL_WALL" not in nifty.get_context_tags(far)


def test_continuation_suppressed_near_magnet_on_0dte_afternoon(monkeypatch):
    """0-DTE 14:00 IST at the magnet must not mint CONTINUATION_BULL."""
    monkeypatch.setattr(
        "data.session_flags.is_expiry_day",
        lambda sym, day: True,
    )
    engine = SignalClassifierEngine()
    ctx = MarketContext(symbol="NIFTY", call_wall=25000.0, magnet=25000.0)
    # 14:00 IST = 08:30 UTC
    t0 = datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc)
    bar = _cont_bull_bar("NIFTY-FUT", t0, 25010.0)
    assert "NEAR_MAGNET" in ctx.get_context_tags(bar)
    sig = engine.evaluate_bar(bar, ctx)
    assert sig is None or sig.type != "CONTINUATION_BULL"


def test_continuation_near_magnet_morning_marks_counter_trend(monkeypatch):
    """Outside the blast window, continuation still fires but is COUNTER_TREND at magnet."""
    monkeypatch.setattr(
        "data.session_flags.is_expiry_day",
        lambda sym, day: True,
    )
    engine = SignalClassifierEngine()
    ctx = MarketContext(symbol="NIFTY", call_wall=25000.0, magnet=25000.0)
    # 10:30 IST = 05:00 UTC
    t0 = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
    bar = _cont_bull_bar("NIFTY-FUT", t0, 25010.0)
    sig = engine.evaluate_bar(bar, ctx)
    assert sig is not None
    assert sig.type == "CONTINUATION_BULL"
    assert sig.gex_alignment == "COUNTER_TREND"
    # COUNTER_TREND + INSIDE_ORB caps below the recommendation floor (70)
    assert sig.score <= 69


def test_continuation_suppressed_when_chop_high():
    """Dreiss chop ≥ 61.8 blocks CONTINUATION so CVD/absorption can fire."""
    engine = SignalClassifierEngine()
    ctx = MarketContext(symbol="NIFTY", call_wall=24800.0, magnet=24800.0)
    ctx.chop_high = True
    t0 = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
    # Far from magnet so only chop gate applies
    bar = _cont_bull_bar("NIFTY-FUT", t0, 24950.0)
    assert "CHOPPY" in ctx.get_context_tags(bar)
    sig = engine.evaluate_bar(bar, ctx)
    assert sig is None or sig.type != "CONTINUATION_BULL"


def test_continuation_orb_breakout_can_clear_rec_floor():
    """Open-space continuation (above ORB) without counter-trend keeps full score."""
    engine = SignalClassifierEngine()
    ctx = MarketContext(
        symbol="NIFTY",
        call_wall=26000.0,
        magnet=26000.0,
        orb_high=24900.0,
        orb_low=24800.0,
        orb_complete=True,
    )
    t0 = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
    bar = _cont_bull_bar("NIFTY-FUT", t0, 24950.0)
    tags = ctx.get_context_tags(bar)
    assert "ABOVE_ORB_HIGH" in tags
    assert "INSIDE_ORB" not in tags
    sig = engine.evaluate_bar(bar, ctx)
    assert sig is not None
    assert sig.type == "CONTINUATION_BULL"
    assert sig.gex_alignment == "NEUTRAL"
    assert sig.score >= 70


def test_largest_1m_gap_minutes():
    from sentinel.feed.live import largest_1m_gap_minutes

    t0 = datetime(2026, 11, 9, 3, 45, tzinfo=timezone.utc)  # 09:15 IST
    bars = [
        _cont_bull_bar("SENSEX-FUT", t0, 81000.0),
        _cont_bull_bar("SENSEX-FUT", t0 + timedelta(minutes=1), 81010.0),
        _cont_bull_bar("SENSEX-FUT", t0 + timedelta(minutes=8), 81020.0),  # 6-min hole
    ]
    assert largest_1m_gap_minutes(bars) == 6
    assert largest_1m_gap_minutes(bars[:2]) == 0


def test_continuation_suppressed_when_elevated_chop_inside_orb():
    """Chop 55–61.8 inside ORB must not mint CONTINUATION (9-Nov V-reversal)."""
    engine = SignalClassifierEngine()
    ctx = MarketContext(
        symbol="NIFTY",
        call_wall=26000.0,
        magnet=26000.0,
        orb_high=25000.0,
        orb_low=24800.0,
        orb_complete=True,
    )
    ctx.chop_high = False
    ctx.chop_value = 58.0
    t0 = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
    bar = _cont_bull_bar("NIFTY-FUT", t0, 24950.0)
    tags = ctx.get_context_tags(bar)
    assert "INSIDE_ORB" in tags
    assert "CHOPPY" not in tags  # only chop_high tags CHOPPY
    sig = engine.evaluate_bar(bar, ctx)
    assert sig is None or sig.type != "CONTINUATION_BULL"


def test_continuation_elevated_chop_allows_orb_breakout():
    """Elevated chop alone does not block an ABOVE_ORB continuation above VWAP."""
    engine = SignalClassifierEngine()
    ctx = MarketContext(
        symbol="NIFTY",
        call_wall=26000.0,
        magnet=26000.0,
        orb_high=24900.0,
        orb_low=24800.0,
        orb_complete=True,
    )
    ctx.chop_high = False
    ctx.chop_value = 58.0
    t0 = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
    bar = _cont_bull_bar("NIFTY-FUT", t0, 24950.0)
    assert "ABOVE_ORB_HIGH" in ctx.get_context_tags(bar)
    assert "ABOVE_VWAP" in ctx.get_context_tags(bar)
    sig = engine.evaluate_bar(bar, ctx)
    assert sig is not None
    assert sig.type == "CONTINUATION_BULL"


def test_continuation_bull_requires_above_vwap_not_orb_alone():
    """11 Nov: ABOVE_ORB_HIGH while BELOW_VWAP must not mint CONT_BULL."""
    engine = SignalClassifierEngine()
    ctx = MarketContext(
        symbol="NIFTY",
        call_wall=26000.0,
        magnet=26000.0,
        orb_high=24900.0,
        orb_low=24800.0,
        orb_complete=True,
    )
    t0 = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
    bar = _cont_bull_bar("NIFTY-FUT", t0, 24950.0)
    # Force VWAP above close so the bar is BELOW_VWAP but still ABOVE_ORB_HIGH.
    bar.vwap = 25050.0
    bar.vwap_sigma_1 = 25080.0
    bar.vwap_sigma_2 = 25110.0
    bar.vwap_lower_1 = 25020.0
    bar.vwap_lower_2 = 24990.0
    tags = ctx.get_context_tags(bar)
    assert "ABOVE_ORB_HIGH" in tags
    assert "BELOW_VWAP" in tags
    sig = engine.evaluate_bar(bar, ctx)
    assert sig is None or sig.type != "CONTINUATION_BULL"


def test_continuation_at_day_high_capped_below_rec_floor():
    """11 Nov AT_DAY_HIGH CONT_BULL chase stays journaled but below score 70."""
    engine = SignalClassifierEngine()
    ctx = MarketContext(
        symbol="NIFTY",
        call_wall=26000.0,
        magnet=26000.0,
        orb_high=24900.0,
        orb_low=24800.0,
        orb_complete=True,
    )
    ctx.day_high = 24950.0
    t0 = datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc)
    bar = _cont_bull_bar("NIFTY-FUT", t0, 24950.0)
    tags = ctx.get_context_tags(bar)
    assert "AT_DAY_HIGH" in tags
    assert "ABOVE_VWAP" in tags
    sig = engine.evaluate_bar(bar, ctx)
    assert sig is not None
    assert sig.type == "CONTINUATION_BULL"
    assert sig.score <= 69


def test_silence_watchdog_schedules_resync_on_stale_ticks(monkeypatch):
    """Silent futures feed must resync without waiting for a completed bar."""
    import time as _time
    from sentinel.feed.live import LiveSentinelManager, _TICK_SILENCE_SEC

    mgr = LiveSentinelManager()
    mgr._aggregators["SENSEX-FUT"] = object()  # armed
    mgr._instrument_symbol["SENSEX-FUT"] = "SENSEX"
    mgr._last_tick_time["SENSEX-FUT"] = _time.time() - (_TICK_SILENCE_SEC + 5)
    called = []
    monkeypatch.setattr(mgr, "_session_active_for_silence", lambda: True)
    monkeypatch.setattr(
        mgr, "_schedule_gap_resync",
        lambda iid, sym: called.append((iid, sym)),
    )
    monkeypatch.setattr(
        "sentinel.feed.live.list_recent_bars",
        lambda *a, **k: [],
    )
    mgr._poll_silent_instruments()
    assert ("SENSEX-FUT", "SENSEX") in called


def test_silence_watchdog_ignores_historical_hole_when_tip_fresh(monkeypatch):
    """11 Nov Sensex: mid-window hole + fresh ticks must not spam resync."""
    import time as _time
    from types import SimpleNamespace
    from sentinel.feed.live import LiveSentinelManager

    mgr = LiveSentinelManager()
    mgr._aggregators["SENSEX-FUT"] = object()
    mgr._instrument_symbol["SENSEX-FUT"] = "SENSEX"
    mgr._last_tick_time["SENSEX-FUT"] = _time.time()  # fresh ticks
    called = []
    monkeypatch.setattr(mgr, "_session_active_for_silence", lambda: True)
    monkeypatch.setattr(
        mgr, "_schedule_gap_resync",
        lambda iid, sym: called.append((iid, sym)),
    )
    # Non-empty bars so poll evaluates trailing gap; force trailing=0.
    monkeypatch.setattr(
        "sentinel.feed.live.list_recent_bars",
        lambda *a, **k: [SimpleNamespace(start_ts=datetime.now(timezone.utc))],
    )
    monkeypatch.setattr("sentinel.feed.live.trailing_1m_gap_minutes", lambda *a, **k: 0)
    monkeypatch.setattr("sentinel.feed.live.largest_1m_gap_minutes", lambda *a, **k: 5)
    mgr._poll_silent_instruments()
    assert called == []


def test_trailing_1m_gap_minutes_ignores_mid_window_hole():
    from types import SimpleNamespace
    from sentinel.feed.live import trailing_1m_gap_minutes, largest_1m_gap_minutes, IST

    now = datetime(2026, 11, 11, 12, 0, tzinfo=IST)
    bars = []
    for i in range(10):
        if i in (3, 4, 5):
            continue
        ts = now - timedelta(minutes=9 - i)
        bars.append(SimpleNamespace(start_ts=ts))
    assert largest_1m_gap_minutes(bars, now_ist=now) >= 3
    assert trailing_1m_gap_minutes(bars, now_ist=now) == 0


def test_silence_watchdog_heals_when_latest_bar_lags(monkeypatch):
    """Prod-style death: bars frozen at 10:25 — trailing gap heals even if ticks look fresh."""
    import time as _time
    from types import SimpleNamespace
    from sentinel.feed.live import LiveSentinelManager

    mgr = LiveSentinelManager()
    # Aggregator map empty (futures path dead) — fixed watch list must still heal.
    mgr._last_tick_time["NIFTY-FUT"] = _time.time()
    mgr._instrument_symbol["NIFTY-FUT"] = "NIFTY"
    called = []
    monkeypatch.setattr(mgr, "_session_active_for_silence", lambda: True)
    monkeypatch.setattr(
        mgr, "_schedule_gap_resync",
        lambda iid, sym: called.append((iid, sym)),
    )
    monkeypatch.setattr(
        "sentinel.feed.live.list_recent_bars",
        lambda iid, **k: (
            [SimpleNamespace(start_ts=datetime(2026, 11, 11, 10, 25))]
            if iid == "NIFTY-FUT"
            else []
        ),
    )
    monkeypatch.setattr(
        "sentinel.feed.live.trailing_1m_gap_minutes",
        lambda bars, now_ist=None: 10 if bars else 0,
    )
    mgr._poll_silent_instruments()
    assert ("NIFTY-FUT", "NIFTY") in called


def test_sync_backfill_marks_oi_stale_after_five_flat(monkeypatch):
    """Candle sync must not force oi_fresh=True on a frozen OI reprint."""
    import pandas as pd
    from sentinel.feed.live import LiveSentinelManager
    from sentinel.storage.db import clear_sentinel_data, list_recent_bars

    clear_sentinel_data("TESTOI-FUT")
    now_ts = datetime.now(timezone.utc).timestamp() - 600
    rows = []
    for i in range(7):
        rows.append({
            "open": 22500.0 + i,
            "high": 22510.0 + i,
            "low": 22495.0 + i,
            "close": 22505.0 + i,
            "volume": 5000,
            "oi": 1_000_000,  # flat all the way
            "timestamp": now_ts + i * 60,
        })
    mock_df = pd.DataFrame(rows)
    monkeypatch.setattr(
        "data.dhan.nearest_futures",
        lambda sym: [{"security_id": 1, "symbol": "TESTOI-FUT", "lot": 65}],
    )
    monkeypatch.setattr("data.dhan.fetch_futures_intraday", lambda *a, **k: mock_df)
    mgr = LiveSentinelManager()
    ok = mgr.sync_live_session("TESTOI-FUT", "TESTOI", force=True)
    assert ok is True
    bars = list_recent_bars("TESTOI-FUT", "1m", limit=10, session_only=False, _auto_populate=False)
    assert len(bars) >= 6
    # Chronological: first flat reprints stay fresh; after 5 flats, stale.
    assert bars[0].oi_fresh is True
    assert bars[-1].oi_fresh is False
    # Live aggregator inherits the flat streak so the next tick stays gated.
    agg = mgr._aggregators["TESTOI-FUT"]
    assert agg._oi_flat_bars >= 5
    clear_sentinel_data("TESTOI-FUT")
