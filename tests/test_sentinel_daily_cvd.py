"""Tests for OrderFlow Sentinel daily closing CVD recording and 10-day history."""

from datetime import datetime, timezone, timedelta
from sentinel.models import Bar
from sentinel.storage.db import (
    save_bar,
    save_daily_cvd,
    list_daily_cvd,
    record_daily_closing_cvd,
    ensure_historical_daily_cvd,
    ensure_sentinel_schema,
    _get_write_conn,
)

IST = timezone(timedelta(hours=5, minutes=30))


def test_save_and_list_daily_cvd():
    ensure_sentinel_schema()
    inst = "TEST-DAILY-FUT"

    rec1 = {
        "trade_date": "2026-10-10",
        "instrument_id": inst,
        "symbol": "TEST",
        "closing_cvd": 15400.0,
        "buy_volume": 65000,
        "sell_volume": 49600,
        "total_volume": 114600,
        "net_delta": 15400.0,
        "closing_price": 22500.0,
        "vwap": 22480.0,
    }
    rec2 = {
        "trade_date": "2026-10-11",
        "instrument_id": inst,
        "symbol": "TEST",
        "closing_cvd": -8200.0,
        "buy_volume": 42000,
        "sell_volume": 50200,
        "total_volume": 92200,
        "net_delta": -8200.0,
        "closing_price": 22450.0,
        "vwap": 22460.0,
    }

    save_daily_cvd(rec1)
    save_daily_cvd(rec2)

    rows = list_daily_cvd(inst, limit=10)
    assert len(rows) == 2
    # Chronological oldest first:
    assert rows[0]["trade_date"] == "2026-10-10"
    assert rows[0]["closing_cvd"] == 15400.0
    assert rows[0]["buy_volume"] == 65000
    assert rows[1]["trade_date"] == "2026-10-11"
    assert rows[1]["closing_cvd"] == -8200.0


def test_record_daily_closing_cvd_from_bars():
    ensure_sentinel_schema()
    inst = "TEST-CALC-FUT"
    dt = "2026-10-09"
    base_ts = datetime(2026, 10, 9, 9, 15, tzinfo=IST)

    # Save 3 1m bars
    for i in range(3):
        b = Bar(
            instrument_id=inst,
            timeframe="1m",
            start_ts=base_ts + timedelta(minutes=i),
            open=22500.0 + i * 5,
            high=22510.0 + i * 5,
            low=22495.0 + i * 5,
            close=22505.0 + i * 5,
            volume_units=1000,
            volume_lots=15.0,
            buy_vol_est=600.0,
            sell_vol_est=400.0,
            delta=200.0,
            delta_ratio=0.20,
            delta_quality=1.0,
            cvd=200.0 * (i + 1),
            vwap=22502.0,
            vwap_sigma_1=22515.0,
            vwap_sigma_2=22525.0,
            vwap_lower_1=22490.0,
            vwap_lower_2=22480.0,
            oi=100000,
            delta_oi=500,
        )
        save_bar(b)

    rec = record_daily_closing_cvd(inst, symbol="TEST", trade_date=dt)
    assert rec is not None
    assert rec["trade_date"] == dt
    assert rec["closing_cvd"] == 600.0  # Last bar CVD
    assert rec["total_volume"] == 3000
    assert rec["net_delta"] == 600.0
    assert rec["buy_volume"] == 1800
    assert rec["sell_volume"] == 1200


def test_ensure_historical_daily_cvd_returns_10_days():
    recs = ensure_historical_daily_cvd("NIFTY-FUT", symbol="NIFTY", days=10)
    assert len(recs) >= 10
    for r in recs:
        assert "trade_date" in r
        assert "closing_cvd" in r
        assert "buy_volume" in r
        assert "sell_volume" in r
        assert r["buy_volume"] >= 0
        assert r["sell_volume"] >= 0
