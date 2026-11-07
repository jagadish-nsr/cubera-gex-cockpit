"""tests/test_sentinel_recommendations.py — Unit Tests for Sentinel OrderFlow Trade Recommendations.

Verifies:
1. ATM option strike resolution and directional bias mapping (LONG -> CE, SHORT -> PE).
2. Equivalent spot levels on Cash Index (spot entry, invalidation, targets).
3. Target Engine Black-76 repricing for option entry, target, and stop loss.
4. Single active entry per strike: second signal for the same strike does not duplicate,
   stacks contributing signals, and elevates target to MAX.
5. Dynamic fallback: invalidating the max-target signal on a 5m close causes the target
   to fall back to the next highest active signal target.
6. Execution triggers:
   - Target Hit on TOUCH.
   - Stop Loss Hit ONLY on completed 5-minute candle CLOSE (wicks do not stop out).
7. Automatic paper trade creation and closure, plus daily success metrics computation.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from analytics import sentinel_recommendations as sr
from data.journal import recommendations_store as rec_store
from data.journal import store as journal_store
from sentinel.models import Bar, SentinelSignal

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(autouse=True)
def isolate_databases(tmp_path, monkeypatch):
    """Ensure all tests run against fresh temporary SQLite databases."""
    rec_db = tmp_path / "test_recommendations.db"
    paper_db = tmp_path / "test_paper_trades.db"

    monkeypatch.setattr(rec_store, "_DB_PATH", rec_db)
    monkeypatch.setattr(journal_store, "_DB_PATH", paper_db)

    rec_store.ensure_schema()
    journal_store.ensure_schema()
    yield


def _make_signal(
    event_id: str = "sig_test_1",
    inst: str = "NIFTY-FUT",
    bias: str = "LONG",
    sig_type: str = "CONTINUATION_BULL",
    entry: float = 24510.0,
    inval: float = 24480.0,
    target_pts: float = 60.0,
    score: int = 85,
    ts: datetime | None = None,
) -> SentinelSignal:
    start_ts = ts or datetime.now(IST)
    return SentinelSignal(
        event_id=event_id,
        instrument_id=inst,
        timeframe="1m",
        bar_start_ts=start_ts,
        type=sig_type,
        category="BULLISH" if bias == "LONG" else "BEARISH",
        bias=bias,
        score=score,
        confidence="HIGH",
        rvol_tod=2.1,
        delta=125000.0,
        delta_ratio=0.35,
        oi_change=45000,
        context_tags=["ABOVE_VWAP"],
        insight_text="Test OrderFlow Signal",
        invalidation_level=inval,
        entry_price=entry,
        target_1_pts=target_pts,
        status="ACTIVE",
    )


def test_atm_option_resolution_and_target_repricing():
    """Verify ATM strike, spot levels, Target Engine repricing, and paper trade auto-open."""
    sig = _make_signal(
        event_id="sig_long_01",
        inst="NIFTY-FUT",
        bias="LONG",
        entry=24512.0,
        inval=24485.0,
        target_pts=50.0,
    )

    rec = sr.on_sentinel_signal(sig, current_spot=24512.0)
    assert rec is not None
    assert rec["symbol"] == "NIFTY"
    assert rec["strike"] == 24500.0  # nearest 50 step ATM
    assert rec["side"] == "CE"
    assert rec["spot_entry"] == 24512.0
    assert rec["spot_invalidation"] == 24485.0
    assert rec["spot_target"] == 24562.0  # 24512 + 50
    assert rec["status"] == "ACTIVE"
    assert rec["option_entry"] > 0
    assert rec["option_target"] > rec["option_entry"]
    assert rec["option_sl"] < rec["option_entry"]

    # Verify auto paper trade opened
    assert rec["paper_trade_id"] is not None
    pt = journal_store.get_trade(rec["paper_trade_id"])
    assert pt is not None
    assert pt.status == "OPEN"
    assert pt.symbol == "NIFTY"
    assert pt.legs[0]["strike"] == 24500.0
    assert pt.legs[0]["type"] == "CE"


def test_short_signal_creates_put_option():
    """Verify SHORT bias creates ATM PE recommendation with below-spot target and above-spot inval."""
    sig = _make_signal(
        event_id="sig_short_01",
        inst="NIFTY-FUT",
        bias="SHORT",
        sig_type="ABSORPTION_BEAR",
        entry=24538.0,
        inval=24570.0,
        target_pts=60.0,
    )

    rec = sr.on_sentinel_signal(sig, current_spot=24538.0)
    assert rec is not None
    assert rec["strike"] == 24550.0  # nearest 50 step ATM
    assert rec["side"] == "PE"
    assert rec["spot_invalidation"] == 24570.0
    assert rec["spot_target"] == 24478.0  # 24538 - 60
    assert rec["option_target"] > rec["option_entry"]
    assert rec["option_sl"] < rec["option_entry"]


def test_single_active_entry_per_strike_and_target_max():
    """Requirement 8: Only 1 active entry per strike. Target elevated to MAX across active signals."""
    t0 = datetime.now(IST).replace(hour=9, minute=30, second=0, microsecond=0)
    sig1 = _make_signal(
        event_id="sig_s1",
        entry=24505.0,
        inval=24480.0,
        target_pts=40.0,  # Spot Target: 24545
        ts=t0,
    )
    rec1 = sr.on_sentinel_signal(sig1, current_spot=24505.0)
    rec_id = rec1["rec_id"]
    pt1_id = rec1["paper_trade_id"]
    t1_opt1 = rec1["option_target"]

    # Second signal arrives 3 minutes later for the same strike (24500 CE) with higher target
    t1 = t0 + timedelta(minutes=3)
    sig2 = _make_signal(
        event_id="sig_s2",
        entry=24515.0,
        inval=24490.0,
        target_pts=80.0,  # Spot Target: 24595 (higher target)
        ts=t1,
    )
    rec2 = sr.on_sentinel_signal(sig2, current_spot=24515.0)

    # Must be the exact same recommendation and paper trade!
    assert rec2["rec_id"] == rec_id
    assert rec2["paper_trade_id"] == pt1_id

    # Active recommendations count in DB must still be 1!
    active_list = rec_store.list_active_sentinel_recommendations("NIFTY")
    assert len(active_list) == 1

    # Effective option target must be elevated to MAX
    assert rec2["option_target"] >= t1_opt1
    assert rec2["spot_target"] == 24595.0

    # Contributing signals list must have both signals
    assert len(rec2["contributing_signals"]) == 2


def test_invalidation_fallback_to_next_highest_active_target():
    """Requirement 8: If max-target signal is invalidated on a 5m close, fallback to next highest target."""
    t0 = datetime.now(IST).replace(hour=9, minute=30, second=0, microsecond=0)

    # Signal 1: Conservative target (Tgt: 24550, Inval: 24470)
    sig1 = _make_signal(
        event_id="sig_low_tgt",
        entry=24500.0,
        inval=24470.0,
        target_pts=50.0,  # 24550
        ts=t0,
    )
    rec = sr.on_sentinel_signal(sig1, current_spot=24500.0)
    opt_tgt_sig1 = rec["option_target"]

    # Signal 2: Aggressive target with tight invalidation (Tgt: 24600, Inval: 24490)
    sig2 = _make_signal(
        event_id="sig_high_tgt",
        entry=24510.0,
        inval=24490.0,
        target_pts=90.0,  # 24600 (MAX target)
        ts=t0 + timedelta(minutes=1),
    )
    rec = sr.on_sentinel_signal(sig2, current_spot=24510.0)
    opt_tgt_sig2 = rec["option_target"]
    assert opt_tgt_sig2 > opt_tgt_sig1

    # Now simulate a 5-minute bar closing at 24485 (below sig2 inval 24490, but ABOVE sig1 inval 24470)
    bar_5m = Bar(
        instrument_id="NIFTY",
        timeframe="5m",
        start_ts=t0,
        open=24500.0,
        high=24515.0,
        low=24480.0,
        close=24485.0,  # Closed at 24485
        volume_units=10000,
        volume_lots=150.0,
        buy_vol_est=4000.0,
        sell_vol_est=6000.0,
        delta=-2000.0,
        delta_ratio=-0.2,
        delta_quality=1.0,
        cvd=-2000.0,
        vwap=24500.0,
        vwap_sigma_1=24530.0,
        vwap_sigma_2=24560.0,
        vwap_lower_1=24470.0,
        vwap_lower_2=24440.0,
    )

    # Run lifecycle evaluation
    sr.evaluate_sentinel_recommendations_lifecycle(
        symbol="NIFTY",
        current_spot=24485.0,
        bars_5m=[bar_5m],
    )

    # Recommendation should still be ACTIVE because sig1 inval (24470) is not breached!
    updated_rec = rec_store.get_active_sentinel_recommendation("NIFTY", 24500.0, "CE")
    assert updated_rec is not None
    assert updated_rec["status"] == "ACTIVE"

    # But sig2 should be invalidated and target should have fallen back to sig1 target!
    contrib = updated_rec["contributing_signals"]
    assert len(contrib) == 2
    s2 = next(s for s in contrib if s["signal_id"] == "sig_high_tgt")
    assert s2["status"] == "INVALIDATED"

    # Effective option target fell back to sig1's target
    assert updated_rec["option_target"] == opt_tgt_sig1
    assert updated_rec["spot_target"] == 24550.0


def test_target_hit_on_touch():
    """Requirement 6: Target Hit triggers immediately on TOUCH of target price."""
    sig = _make_signal(entry=24500.0, inval=24475.0, target_pts=50.0)
    rec = sr.on_sentinel_signal(sig, current_spot=24500.0)
    pt_id = rec["paper_trade_id"]
    tgt_px = rec["option_target"]

    # Option tick touches target price
    events = sr.evaluate_sentinel_recommendations_lifecycle(
        symbol="NIFTY",
        current_spot=24520.0,
        options_ltp_map={"24500_CE": tgt_px + 2.0},
    )

    assert len(events) == 1
    assert events[0]["status"] == "TARGET_HIT"

    # Verify rec status in store
    rec_after = rec_store.list_sentinel_recommendations("NIFTY", status="ALL")[0]
    assert rec_after["status"] == "TARGET_HIT"

    # Verify paper trade is CLOSED with TARGET reason
    pt_after = journal_store.get_trade(pt_id)
    assert pt_after.status == "CLOSED"
    assert pt_after.exit_reason == "TARGET"
    assert abs(pt_after.exit_net_premium) >= tgt_px
    assert pt_after.net_realized_pnl > 0


def test_stop_loss_only_on_5m_candle_close_wicks_ignored():
    """Requirement 6: Stop Loss does NOT trigger on intra-bar wick dip, ONLY on 5m candle close."""
    t0 = datetime.now(IST).replace(hour=9, minute=30, second=0, microsecond=0)
    sig = _make_signal(entry=24500.0, inval=24470.0, target_pts=50.0, ts=t0)
    rec = sr.on_sentinel_signal(sig, current_spot=24500.0)
    sl_px = rec["option_sl"]

    # 1. Intra-bar wick dip below SL price (e.g. tick at sl_px - 10.0)
    # BUT without a completed 5-minute candle closing below SL
    events_wick = sr.evaluate_sentinel_recommendations_lifecycle(
        symbol="NIFTY",
        current_spot=24465.0,  # spot dipped
        options_ltp_map={"24500_CE": sl_px - 10.0},  # option wicked below SL
        bars_5m=[],  # no 5m closed bar yet
    )
    # MUST NOT STOP OUT ON WICK!
    assert len(events_wick) == 0
    rec_check = rec_store.get_active_sentinel_recommendation("NIFTY", 24500.0, "CE")
    assert rec_check is not None
    assert rec_check["status"] == "ACTIVE"

    # 2. Now a completed 5-minute candle closes below SL
    bar_5m_sl = Bar(
        instrument_id="NIFTY",
        timeframe="5m",
        start_ts=t0,
        open=24500.0,
        high=24505.0,
        low=24460.0,
        close=24465.0,  # Closed at 24465 < inval 24470
        volume_units=15000,
        volume_lots=200.0,
        buy_vol_est=3000.0,
        sell_vol_est=12000.0,
        delta=-9000.0,
        delta_ratio=-0.6,
        delta_quality=1.0,
        cvd=-9000.0,
        vwap=24490.0,
        vwap_sigma_1=24520.0,
        vwap_sigma_2=24550.0,
        vwap_lower_1=24470.0,
        vwap_lower_2=24440.0,
    )

    events_close = sr.evaluate_sentinel_recommendations_lifecycle(
        symbol="NIFTY",
        current_spot=24465.0,
        options_ltp_map={"24500_CE": sl_px - 5.0},
        bars_5m=[bar_5m_sl],
    )

    # MUST STOP OUT ON 5-MIN CANDLE CLOSE!
    assert len(events_close) == 1
    assert events_close[0]["status"] == "SL_HIT"

    rec_after = rec_store.list_sentinel_recommendations("NIFTY", status="ALL")[0]
    assert rec_after["status"] == "SL_HIT"
    assert rec_after["exit_reason"] == "STOP"

    # Paper trade closed as STOP
    pt_after = journal_store.get_trade(rec["paper_trade_id"])
    assert pt_after.status == "CLOSED"
    assert pt_after.exit_reason == "STOP"


def test_daily_metrics_computation():
    """Requirement 7: Verify daily metrics (Win rate %, PnL ₹, trades count, R:R)."""
    # Create 1 winning recommendation (Target Hit)
    sig_win = _make_signal(event_id="sig_w1", entry=24500.0, inval=24475.0, target_pts=50.0)
    rec_win = sr.on_sentinel_signal(sig_win, current_spot=24500.0)
    sr.evaluate_sentinel_recommendations_lifecycle(
        symbol="NIFTY",
        current_spot=24560.0,
        options_ltp_map={"24500_CE": rec_win["option_target"] + 5.0},
    )

    # Create 1 losing recommendation (SL Hit) on a different strike (24600 CE)
    t0 = datetime.now(IST)
    sig_loss = _make_signal(event_id="sig_l1", entry=24600.0, inval=24580.0, target_pts=50.0, ts=t0)
    rec_loss = sr.on_sentinel_signal(sig_loss, current_spot=24600.0)

    bar_5m = Bar(
        instrument_id="NIFTY",
        timeframe="5m",
        start_ts=t0,
        open=24600.0,
        high=24605.0,
        low=24570.0,
        close=24575.0,  # Below 24580 inval
        volume_units=10000,
        volume_lots=150.0,
        buy_vol_est=4000.0,
        sell_vol_est=6000.0,
        delta=-2000.0,
        delta_ratio=-0.2,
        delta_quality=1.0,
        cvd=-2000.0,
        vwap=24600.0,
        vwap_sigma_1=24630.0,
        vwap_sigma_2=24660.0,
        vwap_lower_1=24570.0,
        vwap_lower_2=24540.0,
    )
    sr.evaluate_sentinel_recommendations_lifecycle(
        symbol="NIFTY",
        current_spot=24575.0,
        options_ltp_map={"24600_CE": rec_loss["option_sl"] - 5.0},
        bars_5m=[bar_5m],
    )

    # Compute daily metrics
    metrics = sr.get_sentinel_daily_metrics("NIFTY", session_date="today")
    assert metrics["total_trades"] == 2
    assert metrics["active_trades"] == 0
    assert metrics["resolved_trades"] == 2
    assert metrics["target_hits"] == 1
    assert metrics["sl_hits"] == 1
    assert metrics["win_rate_pct"] == 50.0
    assert "realized_pnl_inr" in metrics
    assert "realized_rr" in metrics


@pytest.mark.anyio
async def test_api_sentinel_recommendations_endpoint():
    """Verify GET /api/recommendations/sentinel endpoint returns list & metrics via ASGI."""
    from web_server import app

    sig = _make_signal(event_id="sig_api_1", entry=24500.0, inval=24475.0, target_pts=50.0)
    sr.on_sentinel_signal(sig, current_spot=24500.0)

    clean_path = "/api/recommendations/sentinel"
    query_str = b"symbol=NIFTY&session=today&status=ALL"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": clean_path,
        "raw_path": clean_path.encode(),
        "query_string": query_str,
        "headers": [],
    }

    receive_state = {"done": False}

    async def receive():
        if not receive_state["done"]:
            receive_state["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    response_status = [None]
    response_body = []

    async def send(message):
        if message["type"] == "http.response.start":
            response_status[0] = message["status"]
        elif message["type"] == "http.response.body":
            response_body.append(message.get("body", b""))

    await app(scope, receive, send)
    assert response_status[0] == 200

    data = json.loads(b"".join(response_body).decode())
    assert data["symbol"] == "NIFTY"
    assert "metrics" in data
    assert "recommendations" in data
    assert len(data["recommendations"]) >= 1
    rec = data["recommendations"][0]
    assert rec["symbol"] == "NIFTY"
    assert rec["strike"] == 24500.0
    assert rec["side"] == "CE"
    assert rec["status"] == "ACTIVE"


def test_signal_save_event_triggers_recommendation_engine(tmp_path, monkeypatch):
    """Verify that saving an orderflow signal immediately event-triggers the recommendation engine."""
    from sentinel.storage import db as sentinel_db

    sentinel_test_db = tmp_path / "test_sentinel.db"
    monkeypatch.setattr(sentinel_db, "_DB_PATH", sentinel_test_db)
    sentinel_db.ensure_sentinel_schema()

    # 1. Save an initial LONG signal -> should trigger recommendation engine automatically
    sig1 = _make_signal(
        event_id="sig_evt_01",
        inst="NIFTY-FUT",
        bias="LONG",
        sig_type="CONTINUATION_BULL",
        entry=24508.0,
        inval=24480.0,
        target_pts=45.0,
    )
    sentinel_db.save_signal(sig1)

    rec1 = rec_store.get_active_sentinel_recommendation("NIFTY", 24500.0, "CE")
    assert rec1 is not None
    assert rec1["status"] == "ACTIVE"
    assert rec1["strike"] == 24500.0
    assert rec1["side"] == "CE"
    assert rec1["spot_entry"] == 24508.0
    assert rec1["latest_signal_type"] == "CONTINUATION_BULL"
    assert len(rec1["contributing_signals"]) == 1

    # 2. Save a subsequent signal on the same strike with higher target
    sig2 = _make_signal(
        event_id="sig_evt_02",
        inst="NIFTY-FUT",
        bias="LONG",
        sig_type="ABSORPTION_BULL",
        entry=24512.0,
        inval=24490.0,
        target_pts=80.0,  # Elevated target
    )
    sentinel_db.save_signal(sig2)

    rec2 = rec_store.get_active_sentinel_recommendation("NIFTY", 24500.0, "CE")
    assert rec2 is not None
    assert rec2["rec_id"] == rec1["rec_id"]  # Single active entry per strike preserved
    assert len(rec2["contributing_signals"]) == 2
    assert rec2["latest_signal_type"] == "ABSORPTION_BULL"
    assert rec2["spot_target"] == 24512.0 + 80.0  # Elevated max target


def test_custom_signal_listener_registration():
    """Verify custom signal listener can register, receive events, and unregister cleanly."""
    from sentinel.storage import db as sentinel_db

    received: list[SentinelSignal] = []

    def custom_listener(sig: SentinelSignal):
        received.append(sig)

    sentinel_db.register_signal_listener(custom_listener)
    try:
        sig = _make_signal(event_id="sig_listener_test")
        sentinel_db.dispatch_signal_event(sig)
        assert len(received) == 1
        assert received[0].event_id == "sig_listener_test"
    finally:
        sentinel_db.unregister_signal_listener(custom_listener)

    # After unregistering, listener should not be called
    sentinel_db.dispatch_signal_event(sig)
    assert len(received) == 1



