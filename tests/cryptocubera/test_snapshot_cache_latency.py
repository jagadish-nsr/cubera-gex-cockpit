"""CRY-21: sub-ms compute for cached snapshot + underlying switch."""

from __future__ import annotations

import asyncio
import os
import statistics
import time

import pytest

os.environ["CRYPTO_DB_URL"] = "sqlite:///:memory:"
os.environ.pop("CRYPTO_LIVE_FEEDS", None)
os.environ.pop("CRYPTO_UNDERLYING", None)

from crypto.config import load_config, set_flag, set_underlying
from crypto.service import CockpitService
from crypto.store.db import get_engine, init_db


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 't.db'}")
    monkeypatch.delenv("CRYPTO_LIVE_FEEDS", raising=False)
    monkeypatch.delenv("CRYPTO_UNDERLYING", raising=False)
    load_config(reload=True)
    get_engine(reload=True)
    init_db(reload=True)
    set_flag("assets.btc", True, actor="test")
    set_flag("assets.eth", True, actor="test")
    set_flag("assets.sol", True, actor="test")
    set_flag("feeds.deribit", False, actor="test")
    set_flag("feeds.delta", False, actor="test")
    set_flag("feeds.mock", True, actor="test")
    set_flag("intel.gemma", False, actor="test")
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    set_flag("exec.live_place", False, actor="test")
    set_flag("exec.auto", False, actor="test")
    load_config()["underlying"] = "BTC"
    yield
    load_config(reload=True)


def test_cached_snapshot_and_switch_are_sub_ms():
    svc = CockpitService()

    async def _run():
        await svc.prewarm_snapshots(["BTC", "ETH", "SOL"])

        # Snapshot hot path
        snap_ms = []
        for _ in range(50):
            t0 = time.perf_counter()
            out = await svc.snapshot()
            snap_ms.append((time.perf_counter() - t0) * 1000.0)
        assert out["timing"]["cache_hit"] is True
        assert out["mode_badge"] == "Manual Decision"
        assert statistics.median(snap_ms) < 1.0
        assert max(snap_ms) < 5.0  # allow rare GC spikes; median is the contract

        # Switch hot path — must flip data and stay sub-ms when warm
        sw_ms = []
        for u in ["ETH", "SOL", "BTC", "ETH", "SOL"]:
            t0 = time.perf_counter()
            sw = await svc.switch_underlying(u, actor="test")
            sw_ms.append((time.perf_counter() - t0) * 1000.0)
            assert sw["timing"]["cache_hit"] is True
            assert sw["timing"]["sub_ms"] is True
            assert sw["underlying"] == u
            assert sw.get("snapshot", {}).get("underlying") == u
            assert sw.get("snapshot", {}).get("gex", {}).get("underlying") == u
            if u == "ETH":
                assert 1_000 < sw["spot"] < 10_000
            elif u == "SOL":
                assert sw["spot"] < 1_000
            else:
                assert sw["spot"] > 10_000

        assert statistics.median(sw_ms) < 1.0
        # Manual Decision / no auto-place unchanged
        snap = await svc.snapshot()
        assert snap["flags"]["exec.live_place"] is False
        assert snap["flags"]["exec.auto"] is False
        assert snap["mode_badge"] == "Manual Decision"

    asyncio.run(_run())


def test_switch_returns_embedded_snapshot_without_second_build():
    svc = CockpitService()

    async def _run():
        await svc.prewarm_snapshots(["BTC", "ETH"])
        sw = await svc.switch_underlying("ETH", actor="test")
        assert sw["timing"]["compute_ms"] < 1.0
        assert sw["snapshot"]["spot"] == sw["spot"]
        assert sw["snapshot"]["signal"]["instrument"].startswith("ETH")

    asyncio.run(_run())
