"""Resilience: EXTERNAL snap mirror, heartbeat feed keys, mid-session token reconnect."""

from __future__ import annotations

import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import ingest_heartbeat as hb
from app import live_fetcher
from app import snap_mirror
from app.latest_snap import write_snap_meta
from app.ws_spot_feed import _RECONNECT, request_reconnect


def _dash():
    return SimpleNamespace(is_degraded=False, degraded_reason="", source="dhan")


def test_heartbeat_never_publishes_empty_detail(tmp_path: Path):
    path = tmp_path / "ingest.heartbeat"
    hb.set_path_for_tests(path)
    try:
        with mock.patch.object(hb, "_local_feed_detail", return_value={}):
            hb.write_beat(role="ingest", detail={})
        beat = hb.read_beat()
        assert beat is not None
        detail = beat["detail"]
        assert "ws_connected" in detail
        assert "is_live" in detail
        assert "last_tick_age_s" in detail
    finally:
        hb.set_path_for_tests(None)


def test_request_reconnect_arms_flag_without_stopping_thread():
    _RECONNECT.clear()
    with mock.patch("app.ws_spot_feed._THREAD", None), \
         mock.patch("app.ws_spot_feed.ensure_running") as ensure:
        request_reconnect("token_refresh")
        ensure.assert_called_once()
    assert _RECONNECT.is_set()
    _RECONNECT.clear()


def test_after_token_refresh_requests_ws_reconnect(monkeypatch):
    from data import dhan as dhan_mod

    called = []

    def _reconnect(reason="token_refresh"):
        called.append(reason)

    monkeypatch.setattr("app.ws_spot_feed.request_reconnect", _reconnect)
    out = dhan_mod.after_token_refresh()
    assert out["ws_reconnect"] is True
    assert called == ["token_refresh"]


def test_adopt_disk_snap_loads_newer_pickle(tmp_path: Path):
    live_fetcher._STORE.clear()
    sym = "NIFTY"
    fetched = datetime.now(timezone.utc)
    dash = _dash()
    path = tmp_path / f"latest_snap_{sym}.pkl"
    with path.open("wb") as handle:
        pickle.dump(
            {"data": dash, "symbol": sym, "window": "current_week", "fetched_at": fetched},
            handle,
        )
    write_snap_meta(
        sym,
        fetched_at_epoch=fetched.timestamp(),
        window="current_week",
        directory=tmp_path,
    )
    # Stale memory
    live_fetcher._STORE[(sym, "current_week")] = {
        "data": dash,
        "fetched": time.time() - 900.0,
        "as_of": None,
        "err": None,
        "window": "current_week",
    }
    assert live_fetcher.adopt_disk_snap(sym, directory=tmp_path) is True
    rec = live_fetcher.latest(sym)
    assert rec is not None
    age = time.time() - float(rec["fetched"].timestamp())
    assert age < 60
    live_fetcher._STORE.clear()


def test_mirror_once_drops_zombie_when_meta_is_fresher(tmp_path: Path, monkeypatch):
    live_fetcher._STORE.clear()
    monkeypatch.setenv("COCKPIT_INGEST_EXTERNAL", "1")
    sym = "BANKNIFTY"
    dash = _dash()
    live_fetcher._STORE[(sym, "current_week")] = {
        "data": dash,
        "fetched": time.time() - 1800.0,
        "as_of": None,
        "err": None,
        "window": "current_week",
    }
    fetched = datetime.now(timezone.utc)
    path = tmp_path / f"latest_snap_{sym}.pkl"
    with path.open("wb") as handle:
        pickle.dump(
            {"data": dash, "symbol": sym, "window": "current_week", "fetched_at": fetched},
            handle,
        )
    write_snap_meta(
        sym,
        fetched_at_epoch=fetched.timestamp(),
        window="current_week",
        directory=tmp_path,
    )
    monkeypatch.setattr(
        "app.latest_snap.read_snap_meta",
        lambda symbol, directory=None: {
            "fetched_at_epoch": fetched.timestamp(),
            "window": "current_week",
            "degraded": False,
        },
    )
    monkeypatch.setattr(
        "app.latest_snap.snap_path_for",
        lambda symbol, directory=None: path,
    )
    try:
        out = snap_mirror.mirror_once([sym])
        assert out.get(sym) is True
        rec = live_fetcher.latest(sym)
        assert rec is not None
        assert time.time() - float(rec["fetched"].timestamp()) < 60
    finally:
        live_fetcher._STORE.clear()
        monkeypatch.delenv("COCKPIT_INGEST_EXTERNAL", raising=False)
