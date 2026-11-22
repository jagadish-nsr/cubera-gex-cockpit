"""Hand-checkable tests for CryptoCubera foundation."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

# Isolate DB + config for tests
os.environ["CRYPTO_DB_URL"] = "sqlite:///:memory:"
os.environ.pop("CRYPTO_LIVE_SPIKE_DERIBIT", None)

from crypto.config import get_flag, list_flags, load_config, set_flag
from crypto.engine.gex import compute_gex_features
from crypto.engine.signals import build_signal_card
from crypto.providers.mock.chain import MockOptionChainProvider
from crypto.providers.mock.perp import MockPerpProvider
from crypto.providers.registry import ProviderRegistry
from crypto.store.db import init_db, get_engine
from crypto.store.writer import StoreWriter
from crypto.service import CockpitService


@pytest.fixture(autouse=True)
def _reset_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 't.db'}")
    load_config(reload=True)
    get_engine(reload=True)
    init_db(reload=True)
    yield
    load_config(reload=True)


def test_flags_default_safe():
    flags = list_flags()
    assert flags["feeds.mock"] is True
    assert flags["feeds.deribit"] is False
    assert flags["exec.confirm"] is False
    assert flags["ui.gex_map"] is True


def test_set_flag_roundtrip():
    before = get_flag("ui.go_cards")
    result = set_flag("ui.go_cards", not before, actor="test")
    assert result["previous"] is before
    assert get_flag("ui.go_cards") is (not before)
    set_flag("ui.go_cards", before, actor="test")


def test_mock_chain_validates():
    snap = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    assert snap.spot > 0
    assert len(snap.contracts) >= 10
    snap.validate()


def test_gex_walls_and_agreement():
    snap = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    gex = compute_gex_features(snap)
    assert gex["call_wall"] is not None
    assert gex["put_wall"] is not None
    assert gex["flip"] is not None
    assert isinstance(gex["by_strike"], list)
    assert len(gex["by_strike"]) >= 5
    # Fixture tuned so convention + flow agree for polished GO demo
    assert gex["models_agree"] is True
    assert gex["regime"] in {"positive_gamma", "negative_gamma"}


def test_signal_go_from_fixture():
    snap = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    perp = asyncio.run(MockPerpProvider().fetch_perp())
    gex = compute_gex_features(snap)
    card = build_signal_card(gex, perp)
    assert card["status"] == "GO"
    assert card["instrument"]
    assert card["entry_zone"]
    assert card["target"]
    assert card["stop"]
    assert len(card["reasons"]) >= 1


def test_registry_picks_mock():
    reg = ProviderRegistry()
    assert reg.active_summary()["option_chain"] == "mock"
    assert reg.option_chain().name == "mock"


def test_store_write_path():
    writer = StoreWriter()
    snap = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    perp = asyncio.run(MockPerpProvider().fetch_perp())
    cid = writer.write_chain(snap)
    oid = writer.write_orderflow(perp)
    gex = compute_gex_features(snap)
    gid = writer.write_gex(gex)
    sid = writer.write_signal({"status": "GO", "setup": "t", "instrument": "BTC", "side": "sell", "score": 1, "veto_reasons": []})
    assert cid > 0 and oid > 0 and gid > 0 and sid > 0
    aid = writer.audit_flag("ui.gex_map", True, False, "test")
    assert aid > 0


def test_cockpit_snapshot():
    svc = CockpitService()
    data = asyncio.run(svc.snapshot())
    assert data["brand"] == "CryptoCubera"
    assert data["gex"]["by_strike"]
    assert data["signal"]["status"] in {"GO", "NO-GO"}
    assert data["flags"]["feeds.mock"] is True
