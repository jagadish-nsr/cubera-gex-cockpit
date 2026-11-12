"""Spot ingest wakes at cash pre-open (09:00); continuous session stays is_market_open (09:15)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import config

IST = timezone(timedelta(hours=5, minutes=30))


class _Clock:
    preopen_min = 9 * 60
    discovery_min = 9 * 60 + 7
    open_min = 9 * 60 + 15
    fno_close_min = 15 * 60 + 40


def test_spot_ingest_opens_at_0900_not_0915(monkeypatch):
    monkeypatch.setattr("data.holidays.is_holiday", lambda d: False)
    monkeypatch.setattr("data.session_calendar.is_muhurat_session", lambda d: False)
    monkeypatch.setattr("data.session_calendar.clock_for", lambda d: _Clock())

    at_0859 = datetime(2026, 11, 13, 8, 59, tzinfo=IST)
    at_0900 = datetime(2026, 11, 13, 9, 0, tzinfo=IST)
    at_0910 = datetime(2026, 11, 13, 9, 10, tzinfo=IST)
    at_0920 = datetime(2026, 11, 13, 9, 20, tzinfo=IST)

    assert config.is_spot_ingest_open(at_0859) is False
    assert config.is_market_open(at_0859) is False

    assert config.is_spot_ingest_open(at_0900) is True
    assert config.is_market_open(at_0900) is False  # must not mix gates

    assert config.is_spot_ingest_open(at_0910) is True
    assert config.is_market_open(at_0910) is False

    assert config.is_spot_ingest_open(at_0920) is True
    assert config.is_market_open(at_0920) is True


def test_spot_ingest_closed_weekend_and_after_fno(monkeypatch):
    monkeypatch.setattr("data.holidays.is_holiday", lambda d: False)
    monkeypatch.setattr("data.session_calendar.is_muhurat_session", lambda d: False)
    monkeypatch.setattr("data.session_calendar.clock_for", lambda d: _Clock())

    sat = datetime(2026, 11, 14, 9, 5, tzinfo=IST)
    assert config.is_spot_ingest_open(sat) is False

    after = datetime(2026, 11, 13, 15, 40, tzinfo=IST)
    assert config.is_spot_ingest_open(after) is False


def test_handle_spot_tick_does_not_invent_open_premarket(monkeypatch):
    """Pre-open WS LTP must not be written as day-open (poisons discovery upgrade)."""
    import app.spot_ticker as st
    from app.ws_spot_feed import _handle_spot_tick

    monkeypatch.setattr(config, "is_market_open", lambda *a, **k: False)
    st._STORE.pop("NIFTY", None)
    try:
        _handle_spot_tick(
            {"security_id": 13, "ltp": 25213.5, "open": 0.0, "prev_close": 25255.05},
            {"NIFTY": 13},
        )
        ohlc = (st._STORE.get("NIFTY") or {}).get("ohlc") or {}
        assert ohlc.get("ltp") == 25213.5
        assert float(ohlc.get("open") or 0.0) == 0.0
        assert ohlc.get("as_of", "").startswith("2026-") or ":" in str(ohlc.get("as_of"))
    finally:
        st._STORE.pop("NIFTY", None)


def test_handle_spot_tick_may_seed_open_after_bell(monkeypatch):
    import app.spot_ticker as st
    from app.ws_spot_feed import _handle_spot_tick

    monkeypatch.setattr(config, "is_market_open", lambda *a, **k: True)
    st._STORE.pop("NIFTY", None)
    try:
        _handle_spot_tick(
            {"security_id": 13, "ltp": 25250.0, "open": 0.0, "prev_close": 25255.05},
            {"NIFTY": 13},
        )
        ohlc = (st._STORE.get("NIFTY") or {}).get("ohlc") or {}
        assert float(ohlc.get("open") or 0.0) == 25250.0
    finally:
        st._STORE.pop("NIFTY", None)


def test_instant_quote_still_prefers_prior_book_before_bell(monkeypatch):
    """UI instant quote must not flip to live pre-open via is_market_open mix-up."""
    import app.spot_ticker as st

    monkeypatch.setattr(config, "is_market_open", lambda *a, **k: False)
    monkeypatch.setattr(st, "ensure", lambda *a, **k: None)
    monkeypatch.setattr(
        "data.market_snapshot.resolve_offmarket_snapshot",
        lambda symbol: {
            "spot": 25255.05,
            "prev_close": 25044.65,
            "open": 25044.65,
            "high": 25300.0,
            "low": 25000.0,
            "as_of": "2026-11-12T10:10:00+00:00",
        },
    )
    st._STORE["NIFTY"] = {
        "ohlc": {
            "ltp": 25213.5,
            "open": 0.0,
            "prev_close": 25255.05,
            "as_of": "2026-11-13 09:05:00",
        },
        "fut": None,
        "quote": None,
        "at": 0,
        "ws_at": None,
    }
    try:
        q = st.get_instant_quote("NIFTY")
        # Off-market with snap present uses snap, not the live pre-open _STORE ohlc.
        assert abs(float(q.get("spot") or 0) - 25255.05) < 0.1
    finally:
        st._STORE.pop("NIFTY", None)
