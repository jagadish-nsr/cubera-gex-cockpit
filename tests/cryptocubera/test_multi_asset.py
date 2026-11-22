"""Multi-asset ETH/SOL support — fixtures, flags, switcher API."""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ["CRYPTO_DB_URL"] = "sqlite:///:memory:"
os.environ.pop("CRYPTO_LIVE_SPIKE_DERIBIT", None)
os.environ.pop("CRYPTO_UNDERLYING", None)

from crypto.config import (
    get_active_underlying,
    get_asset_config,
    get_flag,
    get_quote_currency,
    is_asset_enabled,
    list_assets,
    load_config,
    set_flag,
    set_underlying,
)
from crypto.engine.gex import compute_gex_features
from crypto.engine.signals import build_signal_card
from crypto.providers.mock.chain import MockOptionChainProvider
from crypto.providers.mock.fixtures import fixture_path_for, load_fixture
from crypto.providers.mock.perp import MockPerpProvider
from crypto.service import CockpitService
from crypto.store.db import get_engine, init_db


@pytest.fixture(autouse=True)
def _reset_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 't.db'}")
    monkeypatch.delenv("CRYPTO_UNDERLYING", raising=False)
    load_config(reload=True)
    get_engine(reload=True)
    init_db(reload=True)
    # Restore asset flags + underlying to defaults each test
    set_flag("assets.btc", True, actor="test")
    set_flag("assets.eth", False, actor="test")
    set_flag("assets.sol", False, actor="test")
    load_config()["underlying"] = "BTC"
    yield
    load_config(reload=True)


def test_quote_currency_is_delta_pair():
    assert get_quote_currency("BTC") == "BTCUSD"
    assert get_quote_currency("ETH") == "ETHUSD"
    assert get_quote_currency("SOL") == "SOLUSD"
    assert get_asset_config("BTC")["currency"] == "BTCUSD"
    assert get_asset_config("ETH")["currency"] == "ETHUSD"
    assert get_asset_config("SOL")["currency"] == "SOLUSD"


def test_go_card_instrument_uses_quote_currency():
    gex = compute_gex_features(
        asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    )
    perp = asyncio.run(MockPerpProvider().fetch_perp("BTC-PERPETUAL"))
    card = build_signal_card(gex, perp)
    assert card["instrument"] == "BTCUSD"


def test_btc_default_enabled():
    assert get_active_underlying() == "BTC"
    assert is_asset_enabled("BTC") is True
    assert is_asset_enabled("ETH") is False
    assert is_asset_enabled("SOL") is False


def test_asset_catalog_order():
    catalog = list_assets()
    symbols = [a["symbol"] for a in catalog]
    assert symbols[:3] == ["BTC", "ETH", "SOL"]
    btc = next(a for a in catalog if a["symbol"] == "BTC")
    assert btc["enabled"] is True
    assert btc["active"] is True
    assert btc["feature_flag"] == "assets.btc"


def test_enable_eth_and_switch():
    set_flag("assets.eth", True, actor="test")
    assert is_asset_enabled("ETH") is True
    result = set_underlying("ETH", actor="test")
    assert result["underlying"] == "ETH"
    assert result["previous"] == "BTC"
    assert get_active_underlying() == "ETH"
    meta = get_asset_config("ETH")
    assert meta["deribit_perp"] == "ETH-PERPETUAL"
    assert meta["delta_symbols"] == ["ETHUSD"]
    assert meta["fixture"] == "eth_chain.json"


def test_sol_requires_flag():
    with pytest.raises(ValueError, match="disabled"):
        set_underlying("SOL", actor="test")
    set_flag("assets.sol", True, actor="test")
    set_underlying("SOL", actor="test")
    assert get_active_underlying() == "SOL"


def test_disable_active_eth_falls_back_to_btc():
    set_flag("assets.eth", True, actor="test")
    set_underlying("ETH", actor="test")
    assert get_active_underlying() == "ETH"
    set_flag("assets.eth", False, actor="test")
    assert get_active_underlying() == "BTC"
    assert load_config().get("underlying") == "BTC"


def test_cannot_disable_btc_flag():
    result = set_flag("assets.btc", False, actor="test")
    assert result["value"] is True
    assert is_asset_enabled("BTC") is True


def test_fixtures_exist_per_asset():
    for sym in ("BTC", "ETH", "SOL"):
        path = fixture_path_for(sym)
        assert path.is_file(), path
        fx = load_fixture(sym)
        assert fx["underlying"] == sym
        assert float(fx["spot"]) > 0
        assert len(fx["contracts"]) >= 10
        assert fx["perp"]["symbol"].startswith(sym)


@pytest.mark.parametrize("sym", ["BTC", "ETH", "SOL"])
def test_gex_and_go_per_asset(sym):
    set_flag(f"assets.{sym.lower()}", True, actor="test")
    snap = asyncio.run(MockOptionChainProvider().fetch_chain(sym))
    assert snap.underlying == sym
    assert all(c.instrument.startswith(sym) for c in snap.contracts[:3])
    gex = compute_gex_features(snap)
    assert gex["underlying"] == sym
    assert gex["call_wall"] is not None
    assert gex["put_wall"] is not None
    assert gex["models_agree"] is True
    perp = asyncio.run(MockPerpProvider().fetch_perp(f"{sym}-PERPETUAL"))
    assert sym in perp.symbol
    card = build_signal_card(gex, perp)
    assert card["status"] == "GO"
    assert sym in card["instrument"]


def test_snapshot_switches_underlying():
    set_flag("assets.eth", True, actor="test")
    set_flag("assets.sol", True, actor="test")
    svc = CockpitService()

    async def _run():
        btc = await svc.snapshot()
        assert btc["underlying"] == "BTC"
        assert btc["spot"] > 10_000
        assert btc["signal"]["status"] == "GO"
        assert any(a["symbol"] == "ETH" and a["enabled"] for a in btc["assets"])

        await svc.switch_underlying("ETH", actor="test")
        eth = await svc.snapshot()
        assert eth["underlying"] == "ETH"
        assert 1_000 < eth["spot"] < 10_000
        assert eth["gex"]["underlying"] == "ETH"
        assert eth["signal"]["instrument"] == "ETHUSD"
        assert eth["currency"] == "ETHUSD"
        assert eth["asset"]["currency"] == "ETHUSD"
        assert eth["asset"]["deribit_perp"] == "ETH-PERPETUAL"

        await svc.switch_underlying("SOL", actor="test")
        sol = await svc.snapshot()
        assert sol["underlying"] == "SOL"
        assert sol["spot"] < 1_000
        assert sol["signal"]["instrument"] == "SOLUSD"
        assert sol["currency"] == "SOLUSD"
        # Intel feed should not be empty (fixture + calendar)
        assert isinstance(sol["news"], list)

    asyncio.run(_run())


def test_live_cache_does_not_leak_btc_spot_to_eth(monkeypatch):
    """Regression: Deribit live BTC cache must not stick after ETH switch.

    First-session paper ops showed ~59k BTC-like spot under ETH-PERPETUAL
    because fetch_chain reused get_deribit_chain() without an underlying check.
    """
    monkeypatch.delenv("CRYPTO_LIVE_FEEDS", raising=False)
    set_flag("assets.eth", True, actor="test")
    set_flag("feeds.deribit", True, actor="test")
    set_flag("feeds.mock", True, actor="test")

    from crypto.feeds.state import get_feed_state
    from crypto.providers.deribit.chain import DeribitOptionChainProvider

    async def _run():
        btc = await MockOptionChainProvider().fetch_chain("BTC")
        btc.source = "deribit-ws+rest"
        state = get_feed_state(reload=True)
        state.set_deribit_chain(btc)

        # Direct provider path: ETH must not return the BTC cache
        eth_snap = await DeribitOptionChainProvider().fetch_chain("ETH")
        assert eth_snap.underlying == "ETH"
        assert 1_000 < eth_snap.spot < 10_000
        assert eth_snap.spot != btc.spot

        # Full cockpit path after switch_underlying (snapshot cache / cold build)
        state.set_deribit_chain(btc)
        load_config()["underlying"] = "BTC"
        svc = CockpitService()
        sw = await svc.switch_underlying("ETH", actor="test")
        assert sw["underlying"] == "ETH"
        assert 1_000 < sw["spot"] < 10_000
        eth = await svc.snapshot()
        assert eth["underlying"] == "ETH"
        assert 1_000 < eth["spot"] < 10_000
        assert eth["signal"]["instrument"].startswith("ETH")
        assert eth["chain"]["underlying"] == "ETH"
        # Active underlying gate rejects BTC writes after switch
        assert get_feed_state().active_underlying == "ETH"
        state.set_deribit_chain(btc)
        assert get_feed_state().get_deribit_chain(for_underlying="BTC") is None

    asyncio.run(_run())


def test_feed_state_underlying_filter():
    from crypto.feeds.state import get_feed_state

    state = get_feed_state(reload=True)
    btc = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    btc.source = "deribit-ws+rest"
    state.set_deribit_chain(btc)
    assert state.get_deribit_chain(for_underlying="BTC") is not None
    assert state.get_deribit_chain(for_underlying="ETH") is None
    state.clear_for_underlying_switch()
    assert state.get_deribit_chain() is None
    assert state.recent_trades == []


def test_feed_state_rejects_stale_collector_write():
    from crypto.feeds.state import get_feed_state

    state = get_feed_state(reload=True)
    state.set_active_underlying("ETH")
    btc = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    btc.source = "deribit-ws+rest"
    state.set_deribit_chain(btc)  # dying BTC collector — must not stick
    assert state.get_deribit_chain() is None
    eth = asyncio.run(MockOptionChainProvider().fetch_chain("ETH"))
    state.set_deribit_chain(eth)
    assert state.get_deribit_chain(for_underlying="ETH") is not None
    assert 1_000 < state.get_deribit_chain().spot < 10_000


def test_switch_underlying_primes_spot_for_ui(monkeypatch):
    """PATCH / switch must return matching spot so the UI can update without reload."""
    monkeypatch.delenv("CRYPTO_LIVE_FEEDS", raising=False)
    set_flag("assets.eth", True, actor="test")
    set_flag("assets.sol", True, actor="test")
    svc = CockpitService()

    async def _run():
        eth = await svc.switch_underlying("ETH", actor="test")
        assert eth["underlying"] == "ETH"
        assert eth.get("spot") is not None
        assert 1_000 < eth["spot"] < 10_000
        # Feed realign is background; snapshot payload is embedded for UI.
        assert (eth.get("feeds") or {}).get("scheduled") is True
        assert eth.get("snapshot", {}).get("underlying") == "ETH"

        snap = await svc.snapshot()
        assert snap["underlying"] == "ETH"
        assert abs(snap["spot"] - eth["spot"]) < 1e-6
        assert snap["gex"]["underlying"] == "ETH"
        assert snap["signal"]["instrument"].startswith("ETH")

        sol = await svc.switch_underlying("SOL", actor="test")
        assert sol["underlying"] == "SOL"
        assert sol["spot"] < 1_000
        snap2 = await svc.snapshot()
        assert snap2["underlying"] == "SOL"
        assert snap2["spot"] < 1_000
        assert snap2["signal"]["instrument"].startswith("SOL")

    asyncio.run(_run())


def test_asset_flag_env_override(monkeypatch):
    monkeypatch.setenv("CRYPTO_FLAG_ASSETS_ETH", "1")
    load_config(reload=True)
    assert get_flag("assets.eth") is True
    set_underlying("ETH", actor="test")
    assert get_active_underlying() == "ETH"
