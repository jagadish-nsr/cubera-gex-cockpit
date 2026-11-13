"""EXTERNAL-serve readiness: shared meta/SQLite/heartbeat — no local WS required."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app import ingest_heartbeat as hb
from app import live_fetcher
from app.latest_snap import read_snap_meta, write_snap_meta
from app.readiness import _chain_probe, build_readiness


def _token_ok():
    return {
        "present": True, "hours_left": 8.0, "expired": False,
        "source": "file", "malformed": False,
    }


def _cb_closed(cb_fn):
    class _CB:
        state = type("S", (), {"value": "CLOSED"})()
        consecutive_failures = 0
        cooldown_remaining = 0.0
    cb_fn.return_value = _CB()


def test_latest_finds_windowed_publish():
    """Publish under current_week; bare latest() must see it (readiness key bug)."""
    live_fetcher._STORE.clear()

    class _Dash:
        is_degraded = False
        degraded_reason = ""
        source = "dhan"

    live_fetcher._STORE[("NIFTY", "current_week")] = {
        "data": _Dash(),
        "fetched": time.time() - 2.0,
        "as_of": None,
        "err": None,
        "window": "current_week",
    }
    try:
        rec = live_fetcher.latest("NIFTY")
        assert rec is not None and rec.get("data") is not None
        probe = _chain_probe("NIFTY")
        assert probe["ok"] is True
        assert probe["age_s"] is not None
        assert probe["source"] == "memory"
    finally:
        live_fetcher._STORE.clear()


def test_chain_probe_uses_meta_when_memory_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.latest_snap.meta_path_for", lambda symbol, directory=tmp_path: tmp_path / f"{symbol}.meta.json")
    monkeypatch.setattr("app.latest_snap.snap_path_for", lambda symbol, directory=tmp_path: tmp_path / f"{symbol}.pkl")
    write_snap_meta("NIFTY", fetched_at_epoch=time.time() - 5.0, window="current_week", directory=tmp_path)
    with mock.patch("app.live_fetcher.latest", return_value=None):
        # Force read_snap_meta to use tmp_path via patched path helpers
        from app import readiness as rd
        out = rd._chain_from_meta("NIFTY", {
            "symbol": "NIFTY", "has_snapshot": False, "age_s": None,
            "degraded": False, "ok": False, "reason": "",
        })
    # read_snap_meta uses default directory unless we patch read_snap_meta itself
    meta = read_snap_meta("NIFTY", directory=tmp_path)
    assert meta is not None
    with mock.patch("app.latest_snap.read_snap_meta", return_value=meta), \
         mock.patch("app.live_fetcher.latest", return_value=None):
        probe = _chain_probe("NIFTY")
    assert probe["ok"] is True
    assert probe["age_s"] is not None and probe["age_s"] < 60


def test_external_ready_via_shared_artifacts(tmp_path: Path, monkeypatch):
    path = tmp_path / "ingest.heartbeat"
    hb.set_path_for_tests(path)
    monkeypatch.setenv("COCKPIT_INGEST_EXTERNAL", "1")
    now = time.time()
    meta = {
        "fetched_at_epoch": now - 3.0,
        "window": "current_week",
        "degraded": False,
        "degraded_reason": "",
    }
    snap = {
        "spot": 25000.0,
        "as_of": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    try:
        hb.write_beat(
            role="ingest",
            detail={"ws_connected": True, "is_live": True, "last_tick_age_s": 1.0},
        )
        with mock.patch("app.live_fetcher.latest", return_value=None), \
             mock.patch("app.latest_snap.read_snap_meta", return_value=meta), \
             mock.patch("app.spot_ticker._STORE", {}), \
             mock.patch("data.market_snapshot.get_snapshot", return_value=snap), \
             mock.patch("data.dhan.token_status", return_value=_token_ok()), \
             mock.patch("resilience.get_circuit_breaker") as cb_fn:
            _cb_closed(cb_fn)
            payload = build_readiness(market_open=True)
        assert payload["probes"]["ingest"]["ok"] is True
        assert payload["probes"]["feed"]["source"] == "ingest_heartbeat"
        assert payload["probes"]["feed"]["ok"] is True
        assert payload["probes"]["chains"]["NIFTY"]["ok"] is True
        assert payload["probes"]["spots"]["NIFTY"]["ok"] is True
        assert payload["ready"] is True
        assert payload["reasons"] == []
    finally:
        monkeypatch.delenv("COCKPIT_INGEST_EXTERNAL", raising=False)
        hb.set_path_for_tests(None)


def test_external_stale_heartbeat_not_ready(tmp_path: Path, monkeypatch):
    path = tmp_path / "ingest.heartbeat"
    hb.set_path_for_tests(path)
    monkeypatch.setenv("COCKPIT_INGEST_EXTERNAL", "1")
    path.write_text(
        '{"ts": 1.0, "pid": 1, "role": "ingest", '
        '"detail": {"ws_connected": true, "is_live": true, "last_tick_age_s": 1}}\n',
        encoding="utf-8",
    )
    meta = {"fetched_at_epoch": time.time(), "degraded": False, "degraded_reason": ""}
    snap = {
        "spot": 25000.0,
        "as_of": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    try:
        with mock.patch("app.live_fetcher.latest", return_value=None), \
             mock.patch("app.latest_snap.read_snap_meta", return_value=meta), \
             mock.patch("app.spot_ticker._STORE", {}), \
             mock.patch("data.market_snapshot.get_snapshot", return_value=snap), \
             mock.patch("data.dhan.token_status", return_value=_token_ok()), \
             mock.patch("resilience.get_circuit_breaker") as cb_fn:
            _cb_closed(cb_fn)
            payload = build_readiness(market_open=True)
        assert payload["ready"] is False
        assert any("ingest_heartbeat" in r for r in payload["reasons"])
    finally:
        monkeypatch.delenv("COCKPIT_INGEST_EXTERNAL", raising=False)
        hb.set_path_for_tests(None)


def test_ensure_chain_ingest_noop_when_external(monkeypatch):
    monkeypatch.setenv("COCKPIT_INGEST_EXTERNAL", "1")
    called = []

    def _fake_ensure(*args, **kwargs):
        called.append((args, kwargs))

    monkeypatch.setattr("app.live_fetcher.ensure", _fake_ensure)
    import web_server
    web_server._ensure_chain_ingest("NIFTY", "current_week")
    assert called == []
    monkeypatch.delenv("COCKPIT_INGEST_EXTERNAL", raising=False)
    web_server._ensure_chain_ingest("NIFTY", "current_week")
    assert len(called) == 1
