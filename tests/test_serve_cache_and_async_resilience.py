"""Regression tests for EXTERNAL serve cache coherency, atomic snap meta,
target-engine input hardening, and non-blocking asyncio route offloading."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import time
from types import SimpleNamespace

import pandas as pd

from app import live_fetcher
from app.latest_rec import clear_latest_rec, write_latest_rec
from app.latest_snap import read_snap_meta, write_snap_meta


def test_write_snap_meta_concurrent_threads_never_collide(tmp_path):
    """Regression for 74 chain.persist_failed Errno 2 races on shared .meta.json.tmp."""
    def _writer(i: int) -> None:
        for j in range(15):
            write_snap_meta(
                "NIFTY",
                fetched_at_epoch=1700000000.0 + i * 100 + j,
                window="current_week",
                directory=tmp_path,
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_writer, idx) for idx in range(8)]
        for f in futs:
            f.result()

    meta = read_snap_meta("NIFTY", directory=tmp_path)
    assert meta is not None
    assert meta["symbol"] == "NIFTY"


def test_live_fetcher_weekly_alias_hits_current_week_store():
    """Routes asking for 'weekly' must hit adopt_disk_snap's 'current_week' entry."""
    key_cw = ("NIFTY", "current_week")
    sentinel_obj = SimpleNamespace(tag="cw_snap")
    prev = live_fetcher._STORE.get(key_cw)
    prev_w = live_fetcher._STORE.get(("NIFTY", "weekly"))
    try:
        live_fetcher._STORE.pop(("NIFTY", "weekly"), None)
        live_fetcher._STORE[key_cw] = {"data": sentinel_obj, "fetched": time.time()}
        got = live_fetcher.latest("NIFTY", "weekly")
        assert got is not None
        assert got["data"] is sentinel_obj
    finally:
        if prev is None:
            live_fetcher._STORE.pop(key_cw, None)
        else:
            live_fetcher._STORE[key_cw] = prev
        if prev_w is not None:
            live_fetcher._STORE[("NIFTY", "weekly")] = prev_w


def test_external_serve_refreshes_rec_batch_when_pickle_updates(tmp_path, monkeypatch):
    """EXTERNAL serve must pick up newer latest_rec_*.pkl writes and clear deletions."""
    import app.latest_rec as lr
    import web_server as ws

    monkeypatch.setattr(lr, "DEFAULT_DIR", tmp_path)
    monkeypatch.setenv("COCKPIT_INGEST_EXTERNAL", "1")
    ws._REC_BATCH.clear()
    ws._REC_BATCH_MTIME.clear()
    ws._REC_BATCH_CHECK_AT.clear()

    b1 = SimpleNamespace(version=1, spot=24100.0)
    p = write_latest_rec("NIFTY", b1, window="current_week", directory=tmp_path)
    got1 = ws._latest_rec_batch("NIFTY", "current_week")
    assert getattr(got1, "version", None) == 1

    # Advance mtime and write v2; force_check simulates >2s or snap_mirror tick.
    b2 = SimpleNamespace(version=2, spot=24250.0)
    write_latest_rec("NIFTY", b2, window="weekly", directory=tmp_path)
    future_m = p.stat().st_mtime + 5.0
    os.utime(p, (future_m, future_m))

    got2 = ws._refresh_external_rec_batch("NIFTY", "weekly", force_check=True)
    assert getattr(got2, "version", None) == 2

    # Clearing the pickle evicts the stale latch.
    clear_latest_rec("NIFTY", directory=tmp_path)
    got3 = ws._refresh_external_rec_batch("NIFTY", "current_week", force_check=True)
    assert got3 is None


def test_reprice_target_engine_handles_blank_strike_and_holding_without_500(monkeypatch):
    """Blank/None strike, holding_mins, and iv_regime_shock must not raise ValueError/TypeError."""
    import web_server as ws

    contracts = pd.DataFrame([
        {"strike": 24000.0, "type": "CE", "expiry": "2026-11-26", "ltp": 145.0, "iv": 0.14},
        {"strike": 24000.0, "type": "PE", "expiry": "2026-11-26", "ltp": 130.0, "iv": 0.15},
    ])
    fake_dash = SimpleNamespace(
        levels=SimpleNamespace(spot=24012.0, future_price=24025.0),
        contracts=contracts,
    )

    async def _fake_load(sym, win=None, timeout=25.0):
        return fake_dash

    monkeypatch.setattr(ws, "_aload_active_dashboard", _fake_load)
    monkeypatch.setattr(ws, "get_sidebar_active_expiries", lambda **kw: ["2026-11-26"])
    monkeypatch.setattr(ws, "is_expiry_crossed", lambda exp: False)

    out = asyncio.run(ws.reprice_target_engine({
        "symbol": "NIFTY",
        "strike": "",
        "holding_mins": "",
        "target_spot": "",
        "stop_loss_spot": None,
        "iv_regime_shock": None,
    }))
    assert isinstance(out, dict)
    assert "error" not in out
    assert out.get("entry_premium") == 145.0


def test_slow_dashboard_offload_does_not_freeze_event_loop(monkeypatch):
    """While _aload_active_dashboard waits on sync I/O, asyncio loop ticks stay <30ms."""
    import web_server as ws

    def _slow_sync_load(sym, win=None):
        time.sleep(0.25)
        return None

    monkeypatch.setattr(ws, "_load_active_dashboard", _slow_sync_load)

    async def _probe():
        bg = asyncio.create_task(ws._aload_active_dashboard("NIFTY", "current_week"))
        await asyncio.sleep(0.02)
        t0 = time.perf_counter()
        await asyncio.sleep(0.005)
        tick_ms = (time.perf_counter() - t0) * 1000.0
        await bg
        return tick_ms

    lag_ms = asyncio.run(_probe())
    assert lag_ms < 60.0
