"""Ingest heartbeat file + readiness when COCKPIT_INGEST_EXTERNAL=1."""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

from app import ingest_heartbeat as hb
from app.readiness import build_readiness


def test_write_and_probe_fresh(tmp_path: Path):
    path = tmp_path / "ingest.heartbeat"
    hb.set_path_for_tests(path)
    try:
        hb.write_beat(role="ingest")
        probe = hb.probe(stale_after_s=90.0)
        assert probe["ok"] is True
        assert probe["role"] == "ingest"
        assert probe["age_s"] is not None and probe["age_s"] < 5
    finally:
        hb.set_path_for_tests(None)


def test_probe_stale(tmp_path: Path):
    path = tmp_path / "ingest.heartbeat"
    hb.set_path_for_tests(path)
    try:
        path.write_text('{"ts": 1.0, "pid": 1, "role": "ingest"}\n', encoding="utf-8")
        probe = hb.probe(stale_after_s=90.0)
        assert probe["ok"] is False
        assert probe["reason"] == "ingest_heartbeat_stale"
    finally:
        hb.set_path_for_tests(None)


def _token_ok():
    return {
        "present": True, "hours_left": 8.0, "expired": False,
        "source": "file", "malformed": False,
    }


def test_readiness_external_requires_heartbeat(tmp_path: Path, monkeypatch):
    path = tmp_path / "ingest.heartbeat"
    hb.set_path_for_tests(path)
    monkeypatch.setenv("COCKPIT_INGEST_EXTERNAL", "1")
    try:
        with mock.patch("app.live_fetcher.latest", return_value=None), \
             mock.patch("app.latest_snap.read_snap_meta", return_value=None), \
             mock.patch("app.spot_ticker._STORE", {}), \
             mock.patch("data.market_snapshot.get_snapshot", return_value=None), \
             mock.patch("app.ws_spot_feed.get_status", return_value={
                 "ws_connected": True, "is_live": True, "last_tick_age_s": 1.0, "ws_error": None,
             }), \
             mock.patch("data.dhan.token_status", return_value=_token_ok()), \
             mock.patch("resilience.get_circuit_breaker") as cb_fn:
            class _CB:
                state = type("S", (), {"value": "CLOSED"})()
                consecutive_failures = 0
                cooldown_remaining = 0.0
            cb_fn.return_value = _CB()
            payload = build_readiness(market_open=True)
        assert payload["probes"]["ingest"]["required"] is True
        assert "ingest_heartbeat" in " ".join(payload["reasons"])
        assert payload["ready"] is False

        hb.write_beat(
            role="ingest",
            detail={"ws_connected": True, "is_live": True, "last_tick_age_s": 1.0},
        )
        # Still not ready (no chain/spot) but ingest reason gone
        with mock.patch("app.live_fetcher.latest", return_value=None), \
             mock.patch("app.latest_snap.read_snap_meta", return_value=None), \
             mock.patch("app.spot_ticker._STORE", {}), \
             mock.patch("data.market_snapshot.get_snapshot", return_value=None), \
             mock.patch("app.ws_spot_feed.get_status", return_value={
                 "ws_connected": True, "is_live": True, "last_tick_age_s": 1.0, "ws_error": None,
             }), \
             mock.patch("data.dhan.token_status", return_value=_token_ok()), \
             mock.patch("resilience.get_circuit_breaker") as cb_fn:
            class _CB:
                state = type("S", (), {"value": "CLOSED"})()
                consecutive_failures = 0
                cooldown_remaining = 0.0
            cb_fn.return_value = _CB()
            payload2 = build_readiness(market_open=True)
        assert "ingest_heartbeat_stale" not in payload2["reasons"]
        assert "ingest_heartbeat_missing" not in payload2["reasons"]
        assert payload2["probes"]["ingest"]["ok"] is True
    finally:
        monkeypatch.delenv("COCKPIT_INGEST_EXTERNAL", raising=False)
        hb.set_path_for_tests(None)


def test_readiness_combined_missing_file_ok_off_required(tmp_path: Path, monkeypatch):
    path = tmp_path / "ingest.heartbeat"
    hb.set_path_for_tests(path)
    monkeypatch.delenv("COCKPIT_INGEST_EXTERNAL", raising=False)
    try:
        payload = build_readiness(market_open=False)
        assert payload["ready"] is True
        assert payload["probes"]["ingest"]["required"] is False
    finally:
        hb.set_path_for_tests(None)
