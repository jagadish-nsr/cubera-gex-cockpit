"""Parser + stale tests from recorded Deribit / Delta samples."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crypto.feeds.stale import STALE_THRESHOLD_S, age_seconds, is_stale
from crypto.providers.delta.parsers import (
    aggressor_from_buyer_role,
    parse_compact_ticker_greeks,
    parse_rest_option_ticker,
    parse_ws_trade,
)
from crypto.providers.deribit.parsers import (
    FIELD_FINDINGS,
    parse_option_ticker,
    parse_trade,
    parse_ws_subscription,
    unwrap_trades,
)
from crypto.providers.deribit.rest_snapshot import select_instruments

FIXTURES = Path(__file__).parent / "fixtures" / "recorded_samples.json"


@pytest.fixture(scope="module")
def samples() -> dict:
    with open(FIXTURES, encoding="utf-8") as fh:
        return json.load(fh)


def test_deribit_option_ticker_gamma_and_iv(samples):
    c = parse_option_ticker(samples["deribit_option_ticker"])
    assert c.option_type == "C"
    assert c.strike == 59000
    assert c.gamma == pytest.approx(0.0005, rel=0, abs=1e-9)
    # mark_iv percent → fraction
    assert 0.2 < c.iv < 2.0
    assert c.oi >= 0
    assert FIELD_FINDINGS["aggressor_side_field"] == "direction"


def test_deribit_trade_aggressor_direction(samples):
    t = parse_trade(samples["deribit_trades"][0])
    assert t.side in {"buy", "sell"}
    assert t.side == samples["deribit_trades"][0]["direction"]
    assert t.venue == "deribit"
    assert t.symbol


def test_deribit_ws_subscription_unwrap(samples):
    parsed = parse_ws_subscription(samples["deribit_ws_ticker"])
    assert parsed is not None
    channel, data = parsed
    assert channel.startswith("ticker.")
    assert data["instrument_name"].endswith("-C")

    parsed_t = parse_ws_subscription(samples["deribit_ws_trades"])
    assert parsed_t is not None
    _, trades = parsed_t
    rows = unwrap_trades(trades)
    assert len(rows) >= 1
    assert parse_trade(rows[0]).side in {"buy", "sell"}


def test_delta_ws_trade_aggressor_from_buyer_role(samples):
    buy = parse_ws_trade(samples["delta_ws_trade"])
    assert aggressor_from_buyer_role("t") == "buy"
    assert buy.side == "buy"
    sell = parse_ws_trade(samples["delta_ws_trade_maker_buyer"])
    assert sell.side == "sell"
    assert sell.symbol == "BTCUSD"


def test_delta_compact_greeks_and_rest_option(samples):
    g = samples["delta_ws_ticker"]["d"][0]["g"]
    delta, gamma = parse_compact_ticker_greeks(g)
    assert delta == pytest.approx(0.01939861)
    assert gamma == pytest.approx(0.00006382)
    c = parse_rest_option_ticker(samples["delta_rest_option"])
    assert c.instrument.startswith("C-BTC-")
    assert c.option_type == "C"
    assert c.gamma == pytest.approx(0.00012)


def test_stale_threshold_two_seconds():
    now = datetime.now(timezone.utc)
    fresh = now - timedelta(seconds=1.5)
    old = now - timedelta(seconds=2.5)
    assert not is_stale(fresh, now=now)
    assert is_stale(old, now=now)
    assert age_seconds(old, now=now) > STALE_THRESHOLD_S
    assert is_stale(None)


def test_select_instruments_band():
    spot = 60000.0
    rows = [
        {
            "instrument_name": f"BTC-1-{k}-C",
            "strike": float(k),
            "expiration_timestamp": 1_000,
            "is_active": True,
            "state": "open",
        }
        for k in (50000, 59000, 60000, 61000, 80000)
    ]
    rows += [
        {
            "instrument_name": f"BTC-2-{k}-P",
            "strike": float(k),
            "expiration_timestamp": 2_000,
            "is_active": True,
            "state": "open",
        }
        for k in (59500, 60500)
    ]
    chosen = select_instruments(rows, spot, max_expiries=1, strike_band_pct=0.08)
    strikes = {r["strike"] for r in chosen}
    assert 60000 in strikes
    assert 50000 not in strikes
    assert 80000 not in strikes
