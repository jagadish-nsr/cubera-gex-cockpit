"""Tests for Phase 5 supervision singletons, cross-process Dhan rate gate, and WS token guard."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

import data.dhan_feed as dhan_feed
import data.dhan_live as dl
from scripts.run_ingest import acquire_ingest_singleton

ROOT = Path(__file__).resolve().parents[1]


def test_ingest_singleton_blocks_second_writer(tmp_path):
    lock_file = tmp_path / "ingest.lock"
    fh1 = acquire_ingest_singleton(lock_file)
    try:
        with pytest.raises(RuntimeError, match="already holds"):
            acquire_ingest_singleton(lock_file)
    finally:
        fh1.close()

    # Once closed/exited, a new writer can acquire cleanly.
    fh2 = acquire_ingest_singleton(lock_file)
    fh2.close()


def test_cross_process_dhan_gate_honors_external_stamp(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "_GATE_DIR", tmp_path)
    monkeypatch.setattr(dl, "_CROSS_PROCESS_GATE", True)
    orig_limits = dict(dl._LIMITS)
    orig_buckets = dict(dl._BUCKETS)
    try:
        dl._LIMITS.clear()
        dl._LIMITS.update({"chain": 0.25, "quote": 0.05})
        dl._BUCKETS.clear()
        dl._BUCKETS.update(
            {k: {"last": 0.0, "hi": 0, "cv": threading.Condition(), "calls": []} for k in dl._LIMITS}
        )
        # Simulate another OS process having just claimed a chain slot 20ms ago
        now = time.time()
        (tmp_path / "chain.stamp").write_text(f"{now - 0.02:.6f}\n", encoding="utf-8")

        t0 = time.perf_counter()
        dl._gate("chain")
        elapsed = time.perf_counter() - t0
        assert elapsed >= 0.18, f"Expected cross-process wait >= 0.18s, got {elapsed:.3f}s"
    finally:
        dl._LIMITS.clear()
        dl._LIMITS.update(orig_limits)
        dl._BUCKETS.clear()
        dl._BUCKETS.update(orig_buckets)


def test_http_429_pushes_shared_stamp_forward(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "_GATE_DIR", tmp_path)
    monkeypatch.setattr(dl, "_CROSS_PROCESS_GATE", True)
    orig_limits = dict(dl._LIMITS)
    orig_buckets = dict(dl._BUCKETS)
    try:
        dl._LIMITS.clear()
        dl._LIMITS.update({"chain": 0.2, "quote": 0.05})
        dl._BUCKETS.clear()
        dl._BUCKETS.update(
            {k: {"last": 0.0, "hi": 0, "cv": threading.Condition(), "calls": []} for k in dl._LIMITS}
        )

        class _Resp429:
            ok = False
            status_code = 429
            headers = {"Retry-After": "4.0"}

        import requests

        monkeypatch.setattr(requests, "post", lambda *a, **kw: _Resp429())
        # Stub the auth header so the test never needs a local Dhan token.
        monkeypatch.setattr(dl, "_headers", lambda: {"access-token": "test"})
        resp = dl._post("/marketfeed/quote", {"IDX_I": [13]})
        assert resp.status_code == 429

        # Virtual last should push next allowed slot ~4.0s into the future
        stamp_val = float((tmp_path / "quote.stamp").read_text(encoding="utf-8").strip())
        next_allowed_in = (stamp_val + dl._LIMITS["quote"]) - time.time()
        assert next_allowed_in >= 3.2, f"Expected >=3.2s cooldown after 429, got {next_allowed_in:.2f}s"
    finally:
        dl._LIMITS.clear()
        dl._LIMITS.update(orig_limits)
        dl._BUCKETS.clear()
        dl._BUCKETS.update(orig_buckets)


def test_dhan_feed_connect_rejects_expired_jwt_before_dialing(monkeypatch):
    monkeypatch.setattr(dhan_feed, "_secret", lambda env, fn: "fake-value")
    import data.dhan as dhan_mod

    monkeypatch.setattr(
        dhan_mod,
        "token_status",
        lambda: {"present": False, "expired": True, "malformed": False, "hours_left": -12.0},
    )
    dialed = {"called": False}

    import websocket

    def _fail_dial(*args, **kwargs):
        dialed["called"] = True
        raise AssertionError("websocket.create_connection should not be called when JWT is expired")

    monkeypatch.setattr(websocket, "create_connection", _fail_dial)
    with pytest.raises(RuntimeError, match="expired"):
        dhan_feed.connect({13: {"segment": "IDX_I", "security_id": 13}})
    assert dialed["called"] is False


def test_supervision_shell_scripts_syntax_valid():
    for rel in ("run_prod.sh", "scripts/promote.sh", "scripts/rollback.sh", "run_timeseries.sh"):
        res = subprocess.run(["bash", "-n", str(ROOT / rel)], capture_output=True, text=True)
        assert res.returncode == 0, f"{rel} failed bash -n: {res.stderr}"
