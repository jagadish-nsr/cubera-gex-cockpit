"""tests/test_sentinel_recommendations.py — Unit Tests for Sentinel OrderFlow Trade Recommendations.

Verifies:
1. ATM option strike resolution and directional bias mapping (LONG -> CE, SHORT -> PE).
2. Equivalent spot levels on Cash Index (spot entry, invalidation, targets).
3. Target Engine Black-76 repricing with live DTE/IV (tests stub contract context).
4. Single active entry per strike: stacks contributing signals, elevates target,
   and keeps protective spot invalidation.
5. Dynamic fallback: invalidating the max-target signal on a 5m close falls back.
6. Execution triggers:
   - Target Hit on TOUCH.
   - SPOT_INVAL only on completed 5-minute cash CLOSE (wicks do not stop out).
7. Paper trades open only when auto-paper is enabled (or confirm); metrics + refusals.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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

    # Tests that expect paper trades enable auto-paper; production default is off.
    sr.set_auto_paper_enabled(True)
    sr.reset_refusal_metrics()
    monkeypatch.setattr(
        sr,
        "_resolve_contract_context",
        lambda symbol: {
            "expiry": "2026-11-10",
            "expiry_tag": "10NOV2026",
            "dte_days": 3.0,
            "atm_iv": 0.135,
        },
    )

    orig_resolver = rec_store.resolve_live_option_ltp
    def _mock_resolve_ltp(symbol, strike, side, opt_sym, options_ltp_map):
        res = orig_resolver(symbol, strike, side, opt_sym, options_ltp_map)
        if res is not None and res > 0:
            return res
        return 150.0  # authentic test option premium
    monkeypatch.setattr(rec_store, "resolve_live_option_ltp", _mock_resolve_ltp)
    yield
    sr.set_auto_paper_enabled(False)
    sr.reset_refusal_metrics()


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

    # Signal 1: Conservative target (Tgt: 24560, Inval: 24470)
    sig1 = _make_signal(
        event_id="sig_low_tgt",
        entry=24510.0,
        inval=24470.0,
        target_pts=50.0,  # 24560
        ts=t0,
    )
    rec = sr.on_sentinel_signal(sig1, current_spot=24510.0)
    opt_tgt_sig1 = rec["option_target"]

    # Signal 2: Aggressive target with tight invalidation (Tgt: 24605, Inval: 24490)
    sig2 = _make_signal(
        event_id="sig_high_tgt",
        entry=24515.0,
        inval=24490.0,
        target_pts=90.0,  # 24605 (MAX target)
        ts=t0 + timedelta(minutes=1),
    )
    rec = sr.on_sentinel_signal(sig2, current_spot=24515.0)
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

    # Effective option target fell back to sig1's target; protective spot inval falls back too
    assert updated_rec["option_target"] == opt_tgt_sig1
    assert updated_rec["spot_target"] == 24560.0
    assert updated_rec["spot_invalidation"] == 24470.0


def test_target_hit_on_touch():
    """Requirement 6: Target Hit triggers immediately on TOUCH of target price."""
    sig = _make_signal(entry=24510.0, inval=24475.0, target_pts=50.0)
    rec = sr.on_sentinel_signal(sig, current_spot=24510.0)
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


def test_spot_inval_only_on_5m_candle_close_wicks_ignored():
    """Stop = SPOT_INVAL on completed 5m cash close. Option SL wicks do not stop out."""
    t0 = datetime.now(IST).replace(hour=9, minute=30, second=0, microsecond=0)
    sig = _make_signal(entry=24510.0, inval=24470.0, target_pts=50.0, ts=t0)
    rec = sr.on_sentinel_signal(sig, current_spot=24510.0)
    sl_px = rec["option_sl"]

    # 1. Intra-bar wick: spot and option below levels, but no completed 5m close
    events_wick = sr.evaluate_sentinel_recommendations_lifecycle(
        symbol="NIFTY",
        current_spot=24465.0,
        options_ltp_map={"24500_CE": sl_px - 10.0},
        bars_5m=[],
    )
    assert len(events_wick) == 0
    rec_check = rec_store.get_active_sentinel_recommendation("NIFTY", 24500.0, "CE")
    assert rec_check is not None
    assert rec_check["status"] == "ACTIVE"

    # 2. Completed 5-minute candle closes beyond spot invalidation
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

    assert len(events_close) == 1
    assert events_close[0]["status"] == "SPOT_INVAL"

    rec_after = rec_store.list_sentinel_recommendations("NIFTY", status="ALL")[0]
    assert rec_after["status"] == "SPOT_INVAL"
    assert rec_after["exit_reason"] == "SPOT_INVAL"

    pt_after = journal_store.get_trade(rec["paper_trade_id"])
    assert pt_after.status == "CLOSED"
    assert pt_after.exit_reason == "SPOT_INVAL"


def test_daily_metrics_computation():
    """Requirement 7: Verify daily metrics (Win rate %, PnL ₹, trades count, R:R)."""
    # Create 1 winning recommendation (Target Hit)
    sig_win = _make_signal(event_id="sig_w1", entry=24510.0, inval=24475.0, target_pts=50.0)
    rec_win = sr.on_sentinel_signal(sig_win, current_spot=24510.0)
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

    sig = _make_signal(event_id="sig_api_1", entry=24510.0, inval=24475.0, target_pts=50.0)
    sr.on_sentinel_signal(sig, current_spot=24510.0)

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
    assert "auto_paper" in data
    assert isinstance(data["auto_paper"], bool)
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

    # Opt in to recommendation listener and mock market open
    monkeypatch.setattr("config.is_market_open", lambda: True)
    sentinel_db.enable_recommendation_listener()
    try:
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
    finally:
        sentinel_db.disable_recommendation_listener()


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


def test_zero_fabrication_missing_or_placeholder_spot_refused():
    """Ship Blocker 2: Missing spot or placeholder spot must REFUSE recommendation generation."""
    # 1. Spot is zero/blank
    sig_zero = _make_signal(entry=0.0, inval=0.0)
    assert sr.on_sentinel_signal(sig_zero, current_spot=0.0) is None

    # 2. NIFTY display placeholder 24500.0 is refused
    sig_placeholder = _make_signal(entry=24500.0, inval=24470.0)
    assert sr.on_sentinel_signal(sig_placeholder, current_spot=24500.0) is None

    # 3. SENSEX display placeholder 73000.0 is refused
    sig_sensex_ph = _make_signal(inst="SENSEX-FUT", entry=73000.0, inval=72900.0)
    assert sr.on_sentinel_signal(sig_sensex_ph, current_spot=73000.0) is None

    # Verify zero recommendations or paper trades were recorded
    assert len(rec_store.list_active_sentinel_recommendations("NIFTY")) == 0
    assert len(rec_store.list_active_sentinel_recommendations("SENSEX")) == 0


def test_zero_fabrication_missing_live_premium_refused(monkeypatch):
    """Ship Blocker 3: Missing live option quote must REFUSE recommendation, never invent Black-Scholes."""
    # Force option resolver to return None (no live quote)
    monkeypatch.setattr(rec_store, "resolve_live_option_ltp", lambda *args, **kwargs: None)

    sig = _make_signal(entry=24515.0, inval=24480.0)
    rec = sr.on_sentinel_signal(sig, current_spot=24515.0, options_ltp_map={})
    assert rec is None  # Refused! No fabricated BS premium!
    assert len(rec_store.list_active_sentinel_recommendations("NIFTY")) == 0


def test_unsupported_finnifty_symbol_refused():
    """Correctness Gap: FINNIFTY is not in config.INDICES and must be cleanly refused."""
    sig_fin = _make_signal(inst="FINNIFTY-FUT", entry=23000.0, inval=22950.0)
    rec = sr.on_sentinel_signal(sig_fin, current_spot=23000.0)
    assert rec is None


def test_market_closed_and_opt_in_gate_blocks_save_signal_trade_creation(tmp_path, monkeypatch):
    """Ship Blocker 4: Non-live save_signal (history, demo, market closed) must NOT open paper trades."""
    from sentinel.storage import db as sentinel_db

    sentinel_test_db = tmp_path / "test_sentinel_closed.db"
    monkeypatch.setattr(sentinel_db, "_DB_PATH", sentinel_test_db)
    sentinel_db.ensure_sentinel_schema()

    sig = _make_signal(event_id="sig_off_hours", entry=24512.0, inval=24480.0)

    # 1. When listener is NOT enabled (default for history, demo, session sync):
    sentinel_db.disable_recommendation_listener()
    monkeypatch.setattr("config.is_market_open", lambda: True)
    sentinel_db.save_signal(sig)
    assert rec_store.get_active_sentinel_recommendation("NIFTY", 24500.0, "CE") is None

    # 2. When listener is enabled but market is CLOSED:
    sentinel_db.enable_recommendation_listener()
    monkeypatch.setattr("config.is_market_open", lambda: False)
    sentinel_db.save_signal(sig)
    assert rec_store.get_active_sentinel_recommendation("NIFTY", 24500.0, "CE") is None

    sentinel_db.disable_recommendation_listener()


def test_sync_save_signal_does_not_mint_even_when_listener_armed(tmp_path, monkeypatch):
    """Session sync must pass emit_recommendations=False so backfill never mints setups."""
    from sentinel.storage import db as sentinel_db

    sentinel_test_db = tmp_path / "test_sentinel_sync.db"
    monkeypatch.setattr(sentinel_db, "_DB_PATH", sentinel_test_db)
    sentinel_db.ensure_sentinel_schema()
    monkeypatch.setattr("config.is_market_open", lambda: True)
    sentinel_db.enable_recommendation_listener()
    try:
        sig = _make_signal(event_id="sig_sync_01", entry=24512.0, inval=24480.0)
        sentinel_db.save_signal(sig, emit_recommendations=False)
        assert rec_store.get_active_sentinel_recommendation("NIFTY", 24500.0, "CE") is None
    finally:
        sentinel_db.disable_recommendation_listener()


def test_auto_paper_default_off_and_confirm_opens():
    """Paper trades stay pending until confirm (or auto-paper)."""
    sr.set_auto_paper_enabled(False)
    sig = _make_signal(event_id="sig_paper_off", entry=24512.0, inval=24480.0, target_pts=50.0)
    rec = sr.on_sentinel_signal(sig, current_spot=24512.0)
    assert rec is not None
    assert rec["paper_trade_id"] is None

    confirmed = sr.confirm_paper_trade(rec["rec_id"])
    assert confirmed is not None
    assert confirmed["paper_trade_id"] is not None
    pt = journal_store.get_trade(confirmed["paper_trade_id"])
    assert pt is not None and pt.status == "OPEN"


def test_quality_gate_and_missing_target_refused():
    """Low score / missing target_1_pts must refuse and increment refusal metrics."""
    sr.reset_refusal_metrics()
    low = _make_signal(event_id="sig_low", entry=24512.0, inval=24480.0, score=40)
    assert sr.on_sentinel_signal(low, current_spot=24512.0) is None

    no_tgt = _make_signal(event_id="sig_notgt", entry=24512.0, inval=24480.0, target_pts=0.0)
    assert sr.on_sentinel_signal(no_tgt, current_spot=24512.0) is None

    refusals = sr.get_refusal_metrics()
    assert refusals.get("low_score_or_type", 0) >= 1
    assert refusals.get("missing_target_pts", 0) >= 1


def test_stack_raises_protective_spot_invalidation():
    """CE stack keeps the higher (tighter) spot invalidation among active signals."""
    t0 = datetime.now(IST).replace(hour=9, minute=30, second=0, microsecond=0)
    sig1 = _make_signal(event_id="sig_prot1", entry=24510.0, inval=24470.0, target_pts=40.0, ts=t0)
    sr.on_sentinel_signal(sig1, current_spot=24510.0)
    sig2 = _make_signal(
        event_id="sig_prot2", entry=24515.0, inval=24490.0, target_pts=50.0,
        ts=t0 + timedelta(minutes=2),
    )
    rec = sr.on_sentinel_signal(sig2, current_spot=24515.0)
    assert rec["spot_invalidation"] == 24490.0


def test_stable_rec_id_is_session_symbol_strike_side():
    sig = _make_signal(event_id="sig_id1", entry=24512.0, inval=24480.0, target_pts=50.0)
    rec = sr.on_sentinel_signal(sig, current_spot=24512.0)
    assert rec["rec_id"].startswith("srec_")
    assert "NIFTY" in rec["rec_id"]
    assert "24500" in rec["rec_id"]
    assert rec["rec_id"].endswith("_CE")
    assert rec.get("expiry")
    assert rec.get("atm_iv") == 0.135


def test_missing_atm_iv_refused(monkeypatch):
    """No live ATM IV → refuse; never invent a default vol."""
    sr.reset_refusal_metrics()
    monkeypatch.setattr(sr, "_resolve_contract_context", lambda symbol: None)
    sig = _make_signal(event_id="sig_no_iv", entry=24512.0, inval=24480.0, target_pts=50.0)
    assert sr.on_sentinel_signal(sig, current_spot=24512.0) is None
    assert sr.get_refusal_metrics().get("missing_atm_iv", 0) >= 1
    assert len(rec_store.list_active_sentinel_recommendations("NIFTY")) == 0


def test_bad_invalidation_side_refused():
    """LONG invalidation at/above spot (or SHORT at/below) must refuse."""
    sr.reset_refusal_metrics()
    long_bad = _make_signal(event_id="sig_bad_long", bias="LONG", entry=24512.0, inval=24520.0, target_pts=40.0)
    assert sr.on_sentinel_signal(long_bad, current_spot=24512.0) is None

    short_bad = _make_signal(
        event_id="sig_bad_short", bias="SHORT", sig_type="ABSORPTION_BEAR",
        entry=24512.0, inval=24490.0, target_pts=40.0,
    )
    assert sr.on_sentinel_signal(short_bad, current_spot=24512.0) is None
    assert sr.get_refusal_metrics().get("bad_invalidation", 0) >= 2


def test_pe_spot_inval_on_5m_close_above_invalidation():
    """SHORT/PE stop fires when a completed 5m cash close is above spot invalidation."""
    t0 = datetime.now(IST).replace(hour=10, minute=0, second=0, microsecond=0)
    sig = _make_signal(
        event_id="sig_pe_inval",
        bias="SHORT",
        sig_type="ABSORPTION_BEAR",
        entry=24538.0,
        inval=24570.0,
        target_pts=60.0,
        ts=t0,
    )
    rec = sr.on_sentinel_signal(sig, current_spot=24538.0)
    assert rec is not None and rec["side"] == "PE"

    bar_5m = Bar(
        instrument_id="NIFTY",
        timeframe="5m",
        start_ts=t0,
        open=24540.0,
        high=24580.0,
        low=24530.0,
        close=24575.0,  # above inval 24570
        volume_units=12000,
        volume_lots=180.0,
        buy_vol_est=7000.0,
        sell_vol_est=5000.0,
        delta=2000.0,
        delta_ratio=0.2,
        delta_quality=1.0,
        cvd=2000.0,
        vwap=24550.0,
        vwap_sigma_1=24580.0,
        vwap_sigma_2=24610.0,
        vwap_lower_1=24520.0,
        vwap_lower_2=24490.0,
    )
    events = sr.evaluate_sentinel_recommendations_lifecycle(
        symbol="NIFTY",
        current_spot=24575.0,
        options_ltp_map={f"{int(rec['strike'])}_PE": float(rec["option_entry"]) - 5.0},
        bars_5m=[bar_5m],
    )
    assert len(events) == 1
    assert events[0]["status"] == "SPOT_INVAL"


def test_spot_inval_without_live_ltp_still_closes(monkeypatch):
    """SPOT_INVAL must resolve even when the live option quote is missing at stop time."""
    t0 = datetime.now(IST).replace(hour=9, minute=45, second=0, microsecond=0)
    sig = _make_signal(event_id="sig_no_ltp_stop", entry=24510.0, inval=24470.0, target_pts=50.0, ts=t0)
    rec = sr.on_sentinel_signal(sig, current_spot=24510.0)
    assert rec is not None

    monkeypatch.setattr(sr, "_resolve_option_entry_ltp", lambda *a, **k: None)
    bar_5m = Bar(
        instrument_id="NIFTY",
        timeframe="5m",
        start_ts=t0,
        open=24500.0,
        high=24505.0,
        low=24460.0,
        close=24465.0,
        volume_units=10000,
        volume_lots=150.0,
        buy_vol_est=3000.0,
        sell_vol_est=7000.0,
        delta=-4000.0,
        delta_ratio=-0.4,
        delta_quality=1.0,
        cvd=-4000.0,
        vwap=24490.0,
        vwap_sigma_1=24520.0,
        vwap_sigma_2=24550.0,
        vwap_lower_1=24470.0,
        vwap_lower_2=24440.0,
    )
    events = sr.evaluate_sentinel_recommendations_lifecycle(
        symbol="NIFTY",
        current_spot=24465.0,
        options_ltp_map={},
        bars_5m=[bar_5m],
    )
    assert len(events) == 1
    assert events[0]["status"] == "SPOT_INVAL"
    assert events[0]["exit_price"] > 0


def test_metrics_count_legacy_sl_hit_with_spot_inval():
    """Daily metrics treat legacy SL_HIT rows as stops alongside SPOT_INVAL."""
    t0 = datetime.now(IST).replace(hour=11, minute=0, second=0, microsecond=0)
    sig = _make_signal(event_id="sig_legacy_metrics", entry=24510.0, inval=24475.0, target_pts=50.0, ts=t0)
    rec = sr.on_sentinel_signal(sig, current_spot=24510.0)
    assert rec is not None
    rec_store.update_sentinel_recommendation_status(
        rec_id=rec["rec_id"],
        status="SL_HIT",
        exit_price=float(rec["option_sl"]),
        exit_reason="STOP",
        outcome_ts=t0.strftime("%Y-%m-%d %H:%M:%S"),
    )
    metrics = sr.get_sentinel_daily_metrics("NIFTY", session_date="today")
    assert metrics["sl_hits"] >= 1
    assert metrics["spot_inval_hits"] == metrics["sl_hits"]
    assert metrics["resolved_trades"] >= 1


def test_stack_appends_paper_trade_note():
    """Stacking a second signal appends a level-sync note on the linked paper trade."""
    t0 = datetime.now(IST).replace(hour=9, minute=20, second=0, microsecond=0)
    sig1 = _make_signal(event_id="sig_note1", entry=24510.0, inval=24470.0, target_pts=40.0, ts=t0)
    rec1 = sr.on_sentinel_signal(sig1, current_spot=24510.0)
    pt_id = rec1["paper_trade_id"]
    assert pt_id

    sig2 = _make_signal(
        event_id="sig_note2", entry=24515.0, inval=24490.0, target_pts=80.0,
        ts=t0 + timedelta(minutes=2),
    )
    sr.on_sentinel_signal(sig2, current_spot=24515.0)
    pt = journal_store.get_trade(pt_id)
    assert pt is not None
    assert "Stacked" in (pt.notes or "")
    assert "sig_note2" in (pt.notes or "")


def test_live_manager_arms_listener_only_via_arm_helper(tmp_path, monkeypatch):
    """LiveSentinelManager must not enable recommendations in __init__; only via arm helper."""
    from sentinel.feed.live import LiveSentinelManager
    from sentinel.storage import db as sentinel_db

    sentinel_test_db = tmp_path / "test_sentinel_arm.db"
    monkeypatch.setattr(sentinel_db, "_DB_PATH", sentinel_test_db)
    sentinel_db.ensure_sentinel_schema()
    sentinel_db.disable_recommendation_listener()

    mgr = LiveSentinelManager()
    assert mgr._recommendations_armed is False
    assert sentinel_db.is_recommendation_listener_enabled() is False

    mgr._arm_recommendations_if_needed()
    assert mgr._recommendations_armed is True
    assert sentinel_db.is_recommendation_listener_enabled() is True

    sentinel_db.disable_recommendation_listener()


@pytest.mark.anyio
async def test_api_auto_paper_and_confirm_paper_endpoints():
    """POST /auto-paper and /confirm-paper round-trip through the web API."""
    from tests.test_web_server_endpoints import asgi_request

    sr.set_auto_paper_enabled(False)
    sig = _make_signal(event_id="sig_api_paper", entry=24512.0, inval=24480.0, target_pts=50.0)
    rec = sr.on_sentinel_signal(sig, current_spot=24512.0)
    assert rec is not None
    assert rec.get("paper_trade_id") is None

    status, _, body = await asgi_request(
        "POST",
        "/api/recommendations/sentinel/auto-paper",
        body={"enabled": True},
    )
    assert status == 200
    payload = json.loads(body.decode())
    assert payload["ok"] is True
    assert payload["auto_paper"] is True

    status, _, body = await asgi_request("GET", "/api/recommendations/sentinel?symbol=NIFTY&session=today")
    assert status == 200
    listing = json.loads(body.decode())
    assert listing.get("auto_paper") is True

    # Confirm path: force a pending-paper row then confirm via API
    sr.set_auto_paper_enabled(False)
    sig2 = _make_signal(
        event_id="sig_api_confirm",
        entry=24612.0,
        inval=24580.0,
        target_pts=40.0,
    )
    rec2 = sr.on_sentinel_signal(sig2, current_spot=24612.0)
    assert rec2 is not None
    assert rec2.get("paper_trade_id") is None

    status, _, body = await asgi_request(
        "POST",
        f"/api/recommendations/sentinel/{rec2['rec_id']}/confirm-paper",
    )
    assert status == 200
    confirmed = json.loads(body.decode())
    assert confirmed["ok"] is True
    assert confirmed["recommendation"]["paper_trade_id"]

    # Reset for other tests
    sr.set_auto_paper_enabled(True)


def test_trade_recommendations_mini_card_markup_present():
    """Trade Recommendations tab ships the same mini→expand card pattern as Cockpit."""
    from pathlib import Path

    js = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "cockpit.js"
    text = js.read_text(encoding="utf-8")
    assert "toggleRecCardDetails" in text
    assert "rec-card-full-" in text
    assert "cockpit-card-mini-header" in text
    assert "View Full Card Details" in text
    html = (Path(__file__).resolve().parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "cockpit.js?v=6.53" in html


