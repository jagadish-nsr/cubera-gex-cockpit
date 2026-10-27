"""Manual, 06:30, and token-save Dhan sync. The chain pull is injected."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.admin.clock import IST
from app.dhan_sync import (
    offmarket_refresh_line,
    read_schedule_date,
    request_sync,
    reset_for_tests,
    schedule_due,
    scheduler_tick,
    seconds_until_morning,
    status,
)

TOKEN_OK = lambda: {"present": True, "expired": False}  # noqa: E731


class _Levels:
    pass


class _Dash:
    pass


def _dash(spot=24500.0, degraded=False, source="dhan"):
    levels = _Levels()
    levels.spot = spot
    levels.call_wall = spot + 100
    levels.put_wall = spot - 100
    dash = _Dash()
    dash.levels = levels
    dash.source = source
    dash.is_degraded = degraded
    dash.degraded_reason = "stale" if degraded else ""
    dash.fetched_at = datetime(2026, 10, 27, 1, 0, tzinfo=timezone.utc)
    dash.day_ohlc = {"ltp": spot, "open": spot - 20, "high": spot + 30, "low": spot - 40, "prev_close": spot - 10}
    dash.india_vix = {"last": 12.4}
    return dash


def _wait():
    for _ in range(100):
        row = status()
        if not row["running"]:
            return row
        time.sleep(0.02)
    raise AssertionError(status())


@pytest.fixture(autouse=True)
def _clean():
    reset_for_tests()
    yield
    reset_for_tests()


def test_morning_sync_is_due_from_0630_once_per_day():
    before = datetime(2026, 10, 27, 6, 29, tzinfo=IST)
    at = datetime(2026, 10, 27, 6, 30, tzinfo=IST)
    assert schedule_due(before, None) is False
    assert schedule_due(at, None) is True
    assert schedule_due(datetime(2026, 10, 27, 7, 5, tzinfo=IST), None) is True
    assert schedule_due(at, "2026-10-27") is False
    assert 60 < seconds_until_morning(before) < 70


def test_scheduler_tick_requests_once(tmp_path, monkeypatch):
    path = tmp_path / "dhan_sync_state.json"
    calls = []
    monkeypatch.setattr(
        "app.dhan_sync.request_sync",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"status": "started"},
    )
    now = datetime(2026, 10, 27, 6, 30, tzinfo=IST)
    assert scheduler_tick(now, path) is True
    assert scheduler_tick(now, path) is False
    assert read_schedule_date(path) == "2026-10-27"
    assert calls[0][0] == ("schedule",)
    assert calls[0][1]["window"] == "current_week"


def test_offmarket_line_is_one_stamp():
    stamp = "27-10-2026 06:30:00 IST"
    assert offmarket_refresh_line(stamp, morning=True) == f"Prior-session book. Data refreshed {stamp}."
    assert offmarket_refresh_line(stamp, morning=False) == f"Data refreshed {stamp}."
    assert "unavailable" in offmarket_refresh_line("", morning=False)


def test_closed_sync_publishes_each_index_and_records_the_stamp():
    published = []
    quotes = []

    def loader(**kwargs):
        assert kwargs["use_demo"] is False
        assert kwargs["live_source"] == "dhan"
        assert kwargs["window"] == "monthly"
        assert kwargs["force_live"] is True
        return _dash()

    row = request_sync(
        "manual",
        window="monthly",
        loader=loader,
        publisher=lambda sym, window, data: published.append((sym, window, data.source)) or data.fetched_at,
        quote_fn=lambda sym, ohlc: quotes.append((sym, ohlc["ltp"])),
        market_open=lambda: False,
        token_fn=TOKEN_OK,
    )
    assert row["status"] == "started"
    done = _wait()
    assert done["status"] == "ok"
    assert done["last_error"] is None
    assert [item[0] for item in published] == ["NIFTY", "BANKNIFTY", "SENSEX"]
    assert {item[1] for item in published} == {"monthly"}
    assert [item[0] for item in quotes] == ["NIFTY", "BANKNIFTY", "SENSEX"]
    assert done["refreshed_at_display"] == "27-10-2026 06:30:00 IST"


def test_second_request_waits_on_the_one_already_running():
    gate = __import__("threading").Event()
    entered = __import__("threading").Event()

    def loader(**kwargs):
        entered.set()
        assert gate.wait(2)
        return _dash()

    first = request_sync(
        "token",
        symbols=("NIFTY",),
        loader=loader,
        publisher=lambda sym, window, data: data.fetched_at,
        quote_fn=lambda *a, **k: None,
        market_open=lambda: False,
        token_fn=TOKEN_OK,
    )
    assert entered.wait(2)
    second = request_sync(
        "manual",
        symbols=("NIFTY",),
        loader=loader,
        publisher=lambda *a, **k: None,
        market_open=lambda: False,
        token_fn=TOKEN_OK,
    )
    assert first["status"] == "started"
    assert second["status"] == "already_running"
    gate.set()
    assert _wait()["status"] == "ok"


def test_missing_token_does_not_pull():
    def loader(**kwargs):
        raise AssertionError("loader should not run")

    row = request_sync("schedule", loader=loader, market_open=lambda: False, token_fn=lambda: {"present": False})
    assert row["status"] == "skipped"
    assert status()["running"] is False


def test_degraded_book_is_an_error_and_is_not_published():
    published = []

    def loader(**kwargs):
        if kwargs["symbol"] == "BANKNIFTY":
            return _dash(degraded=True)
        return _dash()

    request_sync(
        "manual",
        loader=loader,
        publisher=lambda sym, window, data: published.append(sym) or data.fetched_at,
        quote_fn=lambda *a, **k: None,
        market_open=lambda: False,
        token_fn=TOKEN_OK,
    )
    done = _wait()
    assert published == ["NIFTY", "SENSEX"]
    assert done["status"] == "error"
    assert "BANKNIFTY" in done["last_error"]
    assert done["symbols"]["NIFTY"]["ok"] is True


def test_open_session_wakes_the_live_loop_and_does_not_pull_again(monkeypatch):
    calls = []
    monkeypatch.setattr("app.dhan_sync.OPEN_WAIT_S", 1.0)

    def ensure(symbol, use_demo, live_source, interval_s, window=None):
        calls.append(("ensure", symbol, window))

    def force(symbol, window=None):
        calls.append(("force", symbol, window))

    def latest(symbol, window=None):
        return {"data": _dash(), "fetched": datetime.now(timezone.utc), "err": None}

    monkeypatch.setattr("app.live_fetcher.ensure", ensure)
    monkeypatch.setattr("app.live_fetcher.force", force)
    monkeypatch.setattr("app.live_fetcher.latest", latest)

    def loader(**kwargs):
        raise AssertionError("open session must use the live loop")

    request_sync(
        "manual",
        symbols=("NIFTY",),
        window="current_week",
        loader=loader,
        market_open=lambda: True,
        token_fn=TOKEN_OK,
        poll_s=0.01,
    )
    done = _wait()
    assert done["status"] == "ok"
    assert ("force", "NIFTY", "current_week") in calls
    assert calls[0][0] == "ensure"


def test_publish_snapshot_writes_the_pickle_and_refuses_a_demo(monkeypatch):
    from app import live_fetcher

    snap_dir = Path("tests_artifacts/dhan_sync_pickle")
    if snap_dir.exists():
        for child in snap_dir.glob("latest_snap_*.pkl"):
            child.unlink()
    snap_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(live_fetcher, "_SNAP_DIR", snap_dir)
    dash = _dash()
    fetched = live_fetcher.publish_snapshot("NIFTY", "current_week", dash)
    assert fetched == dash.fetched_at
    assert (snap_dir / "latest_snap_NIFTY.pkl").exists()
    stored = live_fetcher.latest("NIFTY", "current_week")
    assert stored["data"] is dash
    with pytest.raises(ValueError):
        live_fetcher.publish_snapshot("NIFTY", "current_week", _dash(source="DEMO-SYNTHETIC"))


def test_synced_quote_replaces_a_quiet_store_and_leaves_a_live_tick():
    from app import spot_ticker

    saved = spot_ticker._STORE.get("NIFTY")
    try:
        spot_ticker._STORE["NIFTY"] = {"ohlc": {"ltp": 1}, "fut": None, "quote": None, "at": 0, "ws_at": None, "err": None}
        spot_ticker.note_synced_book("NIFTY", {"ltp": 24500, "prev_close": 24400, "open": 24410, "high": 24520, "low": 24380})
        assert spot_ticker._STORE["NIFTY"]["ohlc"]["ltp"] == 24500

        spot_ticker._STORE["NIFTY"]["ws_at"] = time.time()
        spot_ticker._STORE["NIFTY"]["ohlc"] = {"ltp": 24510}
        spot_ticker.note_synced_book("NIFTY", {"ltp": 24000, "prev_close": 24400, "open": 24410, "high": 24520, "low": 24380})
        assert spot_ticker._STORE["NIFTY"]["ohlc"]["ltp"] == 24510
    finally:
        if saved is None:
            spot_ticker._STORE.pop("NIFTY", None)
        else:
            spot_ticker._STORE["NIFTY"] = saved


def test_save_token_persists_through_write_token_and_starts_sync(monkeypatch):
    from web_server import save_dhan_token

    seen = {}

    def write_token(tok):
        seen["token"] = tok

    def fake_sync(trigger, window="current_week", **kwargs):
        seen["trigger"] = trigger
        seen["window"] = window
        return {"status": "started", "running": True}

    monkeypatch.setattr("data.dhan.write_token", write_token)
    monkeypatch.setattr("app.dhan_sync.request_sync", fake_sync)
    result = asyncio.run(save_dhan_token({"token": "aaa.bbb.ccc"}))
    assert seen == {"token": "aaa.bbb.ccc", "trigger": "token", "window": "current_week"}
    assert result["sync"]["status"] == "started"
    assert "Syncing" in result["message"]


def test_save_token_rejects_an_empty_or_malformed_token(monkeypatch):
    from web_server import save_dhan_token

    with pytest.raises(HTTPException) as empty:
        asyncio.run(save_dhan_token({"token": "  "}))
    assert empty.value.status_code == 400

    def write_token(tok):
        raise ValueError("Invalid JWT token format. Dhan access token must have 3 parts separated by dots.")

    monkeypatch.setattr("data.dhan.write_token", write_token)
    with pytest.raises(HTTPException) as bad:
        asyncio.run(save_dhan_token({"token": "not-a-jwt"}))
    assert bad.value.status_code == 400


def test_sync_routes_are_registered():
    from web_server import app

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/ops/sync-status" in paths
    assert "/api/ops/resync-snapshot" in paths


def test_offhours_chain_stays_on_disk_until_an_explicit_sync(monkeypatch, tmp_path):
    """Ordinary off-hours reads do not call Dhan. force_live does."""
    from data import dhan_live

    monkeypatch.chdir(tmp_path)
    cache_dir = tmp_path / "data" / ".cache"
    cache_dir.mkdir(parents=True)
    cached = {"records": {"data": [{"strikePrice": 24000}], "underlyingValue": 24100}}
    (cache_dir / "last_chain_NIFTY.json").write_text(json.dumps(cached), encoding="utf-8")
    monkeypatch.setattr("config.is_market_open", lambda *a, **k: False)
    posts = []

    def _post(*args, **kwargs):
        posts.append(args[0] if args else kwargs.get("path"))

        class _Resp:
            ok = True

            def json(self):
                return {"data": {}}

        return _Resp()

    monkeypatch.setattr(dhan_live, "_post", _post)
    monkeypatch.setattr(dhan_live, "_window_expiries", lambda *a, **k: ["2026-11-03"])
    monkeypatch.setattr(dhan_live, "_to_nse_rows", lambda *a, **k: ([{"strikePrice": 24500}], 24550.0))

    quiet = dhan_live.fetch_chain("NIFTY")
    assert quiet["records"]["underlyingValue"] == 24100
    assert posts == []

    live = dhan_live.fetch_chain("NIFTY", force_live=True)
    assert live["records"]["underlyingValue"] == 24550
    assert "/optionchain" in posts
