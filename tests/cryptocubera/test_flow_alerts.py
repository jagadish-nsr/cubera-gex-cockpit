"""CRY-22: CryptoCubera orderflow tape alerts (BTC/ETH/SOL only)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ["CRYPTO_DB_URL"] = "sqlite:///:memory:"
os.environ.pop("CRYPTOPANIC_TOKEN", None)
os.environ.pop("CRYPTO_LIVE_FEEDS", None)

from crypto.config import list_flags, load_config, set_flag
from crypto.engine.flow_alerts import (
    CRYPTO_UNDERLYINGS,
    TYPE_CVD_DIVERGENCE_LONG,
    TYPE_CVD_IMPULSE_LONG,
    evaluate_flow_alerts,
)
from crypto.intel.further_reading import further_reading_links
from crypto.service import CockpitService
from crypto.store.db import get_engine, init_db


NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'flow.db'}")
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)
    load_config(reload=True)
    set_flag("ui.flow_alerts", True, actor="test")
    set_flag("intel.news", True, actor="test")
    get_engine(reload=True)
    init_db(reload=True)
    yield
    load_config(reload=True)


def test_crypto_underlyings_only():
    assert CRYPTO_UNDERLYINGS == frozenset({"BTC", "ETH", "SOL"})
    assert "NIFTY" not in CRYPTO_UNDERLYINGS
    assert "BANKNIFTY" not in CRYPTO_UNDERLYINGS
    assert "SENSEX" not in CRYPTO_UNDERLYINGS


def test_rejects_equity_index_underlying():
    gex = {"spot": 24000, "call_wall": 24500, "put_wall": 23500, "flip": 24000}
    of = {"mark": 24000, "cvd_1m": 100, "cvd_5m": 200, "cvd_15m": -50, "book_imbalance": 0.2}
    assert evaluate_flow_alerts(gex, of, underlying="NIFTY", apply_cooldown=False) == []
    assert evaluate_flow_alerts(gex, of, underlying="BANKNIFTY", apply_cooldown=False) == []
    assert evaluate_flow_alerts(gex, of, underlying="SENSEX", apply_cooldown=False) == []


def test_cvd_impulse_and_divergence_on_fixture_shape():
    # Mirrors BTC fixture: strong 5m CVD, opposite 15m → impulse + divergence.
    gex = {
        "spot": 97240,
        "call_wall": 100000,
        "put_wall": 94000,
        "flip": 96500,
        "regime": "positive_gamma",
        "models_agree": True,
    }
    of = {
        "mark": 97255,
        "cvd_1m": 42.5,
        "cvd_5m": 186.0,
        "cvd_15m": -95.2,
        "book_imbalance": 0.18,
        "oi_delta": 10.0,
    }
    alerts = evaluate_flow_alerts(
        gex, of, underlying="BTC", now=NOW, apply_cooldown=False
    )
    types = {a["type"] for a in alerts}
    assert TYPE_CVD_IMPULSE_LONG in types
    assert TYPE_CVD_DIVERGENCE_LONG in types
    for a in alerts:
        assert a["underlying"] == "BTC"
        assert a["desk"] == "CryptoCubera"
        assert a["scope"] == "crypto"
        assert a["score"] >= 60


def test_no_equity_tape_package_import():
    import crypto.engine.flow_alerts as fa
    import inspect

    src = inspect.getsource(fa)
    assert "from sentinel" not in src
    assert "import sentinel" not in src
    assert CRYPTO_UNDERLYINGS == frozenset({"BTC", "ETH", "SOL"})


def test_snapshot_includes_flow_alerts_and_further_reading():
    import asyncio

    svc = CockpitService()
    snap = asyncio.run(svc._build_snapshot("BTC", persist=False))
    assert isinstance(snap.get("flow_alerts"), list)
    assert len(snap["flow_alerts"]) >= 1
    intel = snap.get("intel") or {}
    assert intel.get("scope") == "crypto"
    reading = intel.get("further_reading") or []
    assert len(reading) >= 3
    assert all(r["url"].startswith("https://") for r in reading)
    # Event URLs present on fixture news
    news = snap.get("news") or []
    assert any(isinstance(n.get("url"), str) and n["url"].startswith("http") for n in news)


def test_further_reading_is_crypto_only():
    links = further_reading_links()
    blob = " ".join(f"{x['title']} {x['url']} {x.get('note','')}" for x in links).lower()
    assert "nifty" not in blob
    assert "sensex" not in blob
    assert "banknifty" not in blob
    assert any("deribit" in x["url"].lower() for x in links)


def test_flow_alerts_flag_present():
    flags = list_flags()
    assert "ui.flow_alerts" in flags
