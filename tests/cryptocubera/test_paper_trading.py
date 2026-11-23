"""Delta paper trading + Confirm mode — mocked venue, never live capital."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

os.environ["CRYPTO_DB_URL"] = "sqlite:///:memory:"
os.environ.pop("DELTA_PAPER_API_KEY", None)
os.environ.pop("DELTA_PAPER_API_SECRET", None)
os.environ.pop("CRYPTO_LIVE_FEEDS", None)

from crypto.config import get_flag, list_flags, load_config, set_flag
from crypto.exec.ledger import TradeLedger
from crypto.exec.risk import (
    evaluate_daily_loss,
    resolve_exec_mode,
    size_from_stop,
)
from crypto.exec.service import (
    ExecutionService,
    get_execution_service,
    reset_execution_service,
)
from crypto.providers.delta.execution import (
    DELTA_TESTNET_REST,
    DeltaPaperExecutionVenue,
    _base_url,
)
from crypto.providers.mock.execution import MockExecutionVenue
from crypto.providers.registry import get_registry
from crypto.service import CockpitService
from crypto.store.db import get_engine, init_db


NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'paper.db'}")
    monkeypatch.delenv("DELTA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("DELTA_PAPER_API_SECRET", raising=False)
    monkeypatch.delenv("DELTA_PAPER_REST_URL", raising=False)
    monkeypatch.delenv("DELTA_LIVE_API_KEY", raising=False)
    monkeypatch.delenv("DELTA_LIVE_API_SECRET", raising=False)
    monkeypatch.delenv("CRYPTO_LIVE_CONFIRM_ACK", raising=False)
    monkeypatch.delenv("DELTA_LIVE_IP_WHITELIST_ACK", raising=False)
    load_config(reload=True)
    set_flag("exec.paper", False, actor="test")
    set_flag("exec.confirm", False, actor="test")
    set_flag("exec.live", False, actor="test")
    set_flag("exec.auto", False, actor="test")
    get_engine(reload=True)
    init_db(reload=True)
    reset_execution_service()
    get_registry(reload=True)
    yield
    load_config(reload=True)
    reset_execution_service()


def test_flags_include_exec():
    flags = list_flags()
    assert "exec.paper" in flags
    assert "exec.confirm" in flags
    assert flags["exec.paper"] is False
    assert flags["exec.confirm"] is False


def test_size_from_stop_respects_cap():
    # entry 100k, stop 1% away → large notional, capped
    sized = size_from_stop(
        entry=100_000,
        stop=99_000,
        size_cap_usd=2500,
        equity_usd=10_000,
        risk_frac=0.005,
    )
    assert sized.size_usd == 2500
    assert sized.capped is True
    assert sized.risk_budget_usd == 50.0


def test_size_from_stop_uncapped_small_risk():
    sized = size_from_stop(
        entry=100_000,
        stop=90_000,
        size_cap_usd=50_000,
        equity_usd=10_000,
        risk_frac=0.005,
    )
    # risk 50; stop 10%; notional = 50 / 0.1 = 500
    assert sized.size_usd == 500.0
    assert sized.capped is False


def test_resolve_mode_advisory_default():
    mode = resolve_exec_mode()
    assert mode["mode"] == "advisory"
    assert mode["badge"] == "Advisory"
    assert mode["can_confirm"] is False


def test_resolve_mode_paper_confirm():
    set_flag("exec.paper", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    mode = resolve_exec_mode()
    assert mode["mode"] == "paper_confirm"
    assert mode["badge"] == "Paper"
    assert mode["can_confirm"] is True
    assert mode["live"] is False


def test_local_paper_never_live_badge_when_live_flag_off():
    """Preferred path: paper + confirm with exec.live off → never Live Confirm."""
    set_flag("exec.paper", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    set_flag("exec.live", False, actor="test")
    mode = resolve_exec_mode(live_gate_ok=True)
    assert mode["mode"] == "paper_confirm"
    assert mode["badge"] == "Paper"
    assert "Live" not in mode["badge"]
    assert mode["live"] is False


def test_daily_loss_flips_confirm_off():
    set_flag("exec.paper", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    state = evaluate_daily_loss(-300.0, limit_usd=250.0, auto_flip=True)
    assert state.hit is True
    assert state.flipped_to_advisory is True
    assert get_flag("exec.confirm") is False
    mode = resolve_exec_mode(daily_loss_hit=True)
    assert mode["can_confirm"] is False
    assert mode["badge"] == "Advisory"


def test_delta_paper_refuses_live_host(monkeypatch):
    monkeypatch.setenv("DELTA_PAPER_REST_URL", "https://api.india.delta.exchange")
    with pytest.raises(RuntimeError, match="Refusing live Delta host"):
        _base_url()


def test_delta_paper_default_testnet():
    assert _base_url() == DELTA_TESTNET_REST.rstrip("/")


def test_delta_sim_fill_without_keys():
    venue = DeltaPaperExecutionVenue()

    async def _run():
        st = await venue.status()
        assert st["adapter"] == "sim"
        assert st["can_trade"] is True
        out = await venue.place_bracket(
            {
                "instrument": "BTCUSD",
                "side": "buy",
                "entry": 95000,
                "target": 96000,
                "stop": 94000,
                "size_usd": 500,
            }
        )
        assert out["ok"] is True
        assert out["adapter"] == "sim"
        assert out["status"] == "sim_filled"
        assert out["fill_price"] == 95000
        killed = await venue.cancel_all()
        assert killed["cancelled"] == 1

    asyncio.run(_run())


def test_mock_venue_bracket_and_cancel():
    venue = MockExecutionVenue()

    async def _run():
        out = await venue.place_bracket(
            {"instrument": "BTCUSD", "side": "buy", "entry": 1.0, "size_usd": 100}
        )
        assert out["status"] == "sim_filled"
        cid = out["client_order_id"]
        c = await venue.cancel_intent(cid)
        assert c["status"] == "cancelled"

    asyncio.run(_run())


def _go_card(**overrides):
    card = {
        "status": "GO",
        "setup": "positive_gamma_fade",
        "instrument": "BTCUSD",
        "side": "sell",
        "entry_zone": [95000, 95200],
        "target": 94000,
        "stop": 96000,
        "size_cap_usd": 2500,
        "score": 70,
        "reasons": ["a", "b", "c"],
        "spot": 95100,
    }
    card.update(overrides)
    return card


def test_confirm_requires_flags():
    svc = ExecutionService()

    async def _run():
        blocked = await svc.confirm_from_card(_go_card())
        assert blocked["ok"] is False
        assert blocked["code"] == "exec_off"

        set_flag("exec.paper", True, actor="test")
        blocked2 = await svc.confirm_from_card(_go_card())
        assert blocked2["ok"] is False
        assert blocked2["code"] == "confirm_blocked"

    asyncio.run(_run())


def test_confirm_submits_sim_and_ledger():
    set_flag("exec.paper", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    get_registry(reload=True)
    svc = ExecutionService()

    async def _run():
        result = await svc.confirm_from_card(_go_card(), actor="pytest")
        assert result["ok"] is True
        trade = result["trade"]
        assert trade["status"] == "sim_filled"
        assert trade["adapter"] in {"sim", "demo"}
        assert trade["size_usd"] > 0
        assert trade["size_usd"] <= 2500
        rows = svc.ledger.list_trades()
        assert len(rows) == 1
        assert rows[0]["client_order_id"] == trade["client_order_id"]
        csv_blob = svc.ledger.export_csv()
        assert "client_order_id" in csv_blob
        assert trade["client_order_id"] in csv_blob
        js = svc.ledger.export_json()
        assert "trades" in js

    asyncio.run(_run())


def test_confirm_rejects_nogo():
    set_flag("exec.paper", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    svc = ExecutionService()

    async def _run():
        out = await svc.confirm_from_card(_go_card(status="NO-GO"))
        assert out["ok"] is False
        assert out["code"] == "not_go"

    asyncio.run(_run())


def test_kill_switch_two_tap():
    set_flag("exec.paper", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    get_registry(reload=True)
    svc = ExecutionService()

    async def _run():
        await svc.confirm_from_card(_go_card())
        assert len(svc.ledger.open_intents()) == 1

        arm = await svc.kill(confirmed=False, actor="pytest")
        assert arm["armed"] is True
        assert arm["cancelled"] == 0
        assert len(svc.ledger.open_intents()) == 1

        done = await svc.kill(confirmed=True, actor="pytest")
        assert done["ok"] is True
        assert done["cancelled"] == 1
        assert done["armed"] is False
        assert len(svc.ledger.open_intents()) == 0
        assert get_flag("exec.confirm") is False

    asyncio.run(_run())


def test_registry_picks_delta_when_paper_on():
    set_flag("exec.paper", True, actor="test")
    reg = get_registry(reload=True)
    venue = reg.execution()
    assert venue.name in {"delta_paper", "mock"}
    # priority prefers delta paper adapter
    assert reg.active_summary()["execution"] == "delta"


def test_snapshot_exposes_mode_badge_and_paper_status():
    set_flag("exec.paper", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    set_flag("exec.live", False, actor="test")
    set_flag("ui.go_cards", True, actor="test")
    get_registry(reload=True)

    async def _run():
        snap = await CockpitService().snapshot()
        assert snap["mode"] == "paper_confirm"
        assert snap["mode_badge"] == "Paper"
        assert "Live" not in snap["mode_badge"]
        assert snap["flags"]["exec.live"] is False
        assert snap["execution"]["can_confirm"] is True
        assert snap["execution"]["live_flag"] is False
        assert snap["execution"]["live_gate"]["ok"] is False
        assert snap["signal"] is not None
        assert "paper_status" in snap["signal"]
        assert "can_confirm" in snap["signal"]

    asyncio.run(_run())


def test_env_paper_flags_map_without_live(monkeypatch, tmp_path):
    """CRYPTO_FLAG_EXEC_PAPER/CONFIRM enable paper; EXEC_LIVE=0 keeps live off."""
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'env_paper.db'}")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_PAPER", "1")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_CONFIRM", "1")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_LIVE", "0")
    load_config(reload=True)
    assert get_flag("exec.paper") is True
    assert get_flag("exec.confirm") is True
    assert get_flag("exec.live") is False
    mode = resolve_exec_mode()
    assert mode["badge"] == "Paper"
    assert mode["mode"] == "paper_confirm"


def test_env_paper_path_enables_eth_sol_keeps_live_off(monkeypatch, tmp_path):
    """Paper helper env: ETH/SOL on, BTC default underlying, exec.live forced off."""
    from crypto.config import get_active_underlying, is_asset_enabled, list_assets

    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'env_paper_assets.db'}")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_PAPER", "1")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_CONFIRM", "1")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_LIVE", "0")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_AUTO", "0")
    monkeypatch.setenv("CRYPTO_FLAG_ASSETS_ETH", "1")
    monkeypatch.setenv("CRYPTO_FLAG_ASSETS_SOL", "1")
    monkeypatch.delenv("CRYPTO_UNDERLYING", raising=False)
    load_config(reload=True)

    assert get_flag("exec.paper") is True
    assert get_flag("exec.confirm") is True
    assert get_flag("exec.live") is False
    assert get_flag("exec.auto") is False
    assert get_flag("assets.eth") is True
    assert get_flag("assets.sol") is True
    assert is_asset_enabled("BTC")
    assert is_asset_enabled("ETH")
    assert is_asset_enabled("SOL")
    assert get_active_underlying() == "BTC"
    enabled = {a["symbol"] for a in list_assets() if a.get("enabled")}
    assert enabled >= {"BTC", "ETH", "SOL"}
    mode = resolve_exec_mode()
    assert mode["badge"] == "Paper"
    assert "Live" not in mode["badge"]


def test_run_crypto_paper_sh_manual_decision_default_and_eth_sol():
    """Helper defaults to Manual Decision; paper sim optional; live_place/auto off."""
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "run_crypto_paper.sh"
    text = script.read_text(encoding="utf-8")
    assert "CRYPTO_FLAG_EXEC_CONFIRM=1" in text
    assert "CRYPTO_FLAG_EXEC_LIVE_PLACE=0" in text
    assert "CRYPTO_FLAG_EXEC_AUTO=0" in text
    # Default Manual Decision path (copy/export)
    assert "CRYPTO_FLAG_EXEC_LIVE=1" in text
    assert "Manual Decision" in text
    assert "copy/export" in text
    # Paper sim optional via CRYPTO_PAPER_SIM
    assert "CRYPTO_PAPER_SIM" in text
    assert "CRYPTO_FLAG_EXEC_LIVE=0" in text  # only inside PAPER_SIM branch
    assert "CRYPTO_FLAG_ASSETS_ETH" in text
    assert "CRYPTO_FLAG_ASSETS_SOL" in text
    assert 'CRYPTO_FLAG_ASSETS_ETH="${CRYPTO_FLAG_ASSETS_ETH:-1}"' in text
    assert 'CRYPTO_FLAG_ASSETS_SOL="${CRYPTO_FLAG_ASSETS_SOL:-1}"' in text
    assert 'CRYPTO_LIVE_FEEDS="${CRYPTO_LIVE_FEEDS:-1}"' in text
    assert 'CRYPTO_FLAG_FEEDS_DERIBIT="${CRYPTO_FLAG_FEEDS_DERIBIT:-1}"' in text
    assert 'CRYPTO_FLAG_FEEDS_DELTA="${CRYPTO_FLAG_FEEDS_DELTA:-1}"' in text
    assert "soft-fallback to mock if WS fails" in text
    assert 'CRYPTO_FLAG_INTEL_GEMMA="${CRYPTO_FLAG_INTEL_GEMMA:-1}"' in text
    assert "mock/keywords" in text


def test_env_manual_decision_paper_ops_default(monkeypatch, tmp_path):
    """Default paper-ops env: Manual Decision + feeds/ETH/SOL/Gemma; live_place off."""
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'env_md_ops.db'}")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_LIVE", "1")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_CONFIRM", "1")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_LIVE_PLACE", "0")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_AUTO", "0")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_PAPER", "0")
    monkeypatch.setenv("CRYPTO_LIVE_FEEDS", "1")
    monkeypatch.setenv("CRYPTO_FLAG_FEEDS_DERIBIT", "1")
    monkeypatch.setenv("CRYPTO_FLAG_FEEDS_DELTA", "1")
    monkeypatch.setenv("CRYPTO_FLAG_ASSETS_ETH", "1")
    monkeypatch.setenv("CRYPTO_FLAG_ASSETS_SOL", "1")
    monkeypatch.setenv("CRYPTO_FLAG_INTEL_GEMMA", "1")
    load_config(reload=True)

    assert get_flag("exec.live") is True
    assert get_flag("exec.confirm") is True
    assert get_flag("exec.live_place") is False
    assert get_flag("exec.auto") is False
    assert get_flag("exec.paper") is False
    assert get_flag("feeds.deribit") is True
    assert get_flag("feeds.delta") is True
    assert get_flag("intel.gemma") is True
    assert get_flag("assets.eth") is True
    assert get_flag("assets.sol") is True
    mode = resolve_exec_mode()
    assert mode["badge"] == "Manual Decision"
    assert mode["mode"] == "manual_decision"
    assert mode["can_confirm"] is True
    assert mode["live_place"] is False


def test_env_paper_sim_optional_keeps_live_off(monkeypatch, tmp_path):
    """CRYPTO_PAPER_SIM path: paper + confirm, exec.live off, feeds/gemma on."""
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'env_paper_sim.db'}")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_PAPER", "1")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_CONFIRM", "1")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_LIVE", "0")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_LIVE_PLACE", "0")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_AUTO", "0")
    monkeypatch.setenv("CRYPTO_LIVE_FEEDS", "1")
    monkeypatch.setenv("CRYPTO_FLAG_FEEDS_DERIBIT", "1")
    monkeypatch.setenv("CRYPTO_FLAG_FEEDS_DELTA", "1")
    monkeypatch.setenv("CRYPTO_FLAG_ASSETS_ETH", "1")
    monkeypatch.setenv("CRYPTO_FLAG_ASSETS_SOL", "1")
    monkeypatch.setenv("CRYPTO_FLAG_INTEL_GEMMA", "1")
    load_config(reload=True)

    assert get_flag("feeds.deribit") is True
    assert get_flag("feeds.delta") is True
    assert get_flag("exec.live") is False
    assert get_flag("exec.live_place") is False
    assert get_flag("exec.paper") is True
    assert get_flag("exec.confirm") is True
    assert get_flag("intel.gemma") is True
    mode = resolve_exec_mode()
    assert mode["badge"] == "Paper"
    assert "Live" not in mode["badge"]


def test_env_paper_path_enables_gemma_manual_decision(monkeypatch, tmp_path):
    """Paper-ops default: intel.gemma on with Manual Decision; live_place off."""
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'env_paper_gemma.db'}")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_LIVE", "1")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_CONFIRM", "1")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_LIVE_PLACE", "0")
    monkeypatch.setenv("CRYPTO_FLAG_EXEC_AUTO", "0")
    monkeypatch.setenv("CRYPTO_FLAG_ASSETS_ETH", "1")
    monkeypatch.setenv("CRYPTO_FLAG_ASSETS_SOL", "1")
    monkeypatch.setenv("CRYPTO_FLAG_INTEL_GEMMA", "1")
    load_config(reload=True)

    assert get_flag("intel.gemma") is True
    assert get_flag("exec.live") is True
    assert get_flag("exec.live_place") is False
    mode = resolve_exec_mode()
    assert mode["badge"] == "Manual Decision"


def test_api_confirm_and_export(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'api_paper.db'}")
    load_config(reload=True)
    get_engine(reload=True)
    init_db(reload=True)
    reset_execution_service()
    set_flag("exec.paper", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    get_registry(reload=True)

    from crypto.server import app

    client = TestClient(app)
    st = client.get("/api/exec/status")
    assert st.status_code == 200
    assert st.json()["can_confirm"] is True

    # Force a GO card via confirm body
    body = {
        "actor": "api-test",
        "card": _go_card(),
    }
    conf = client.post("/api/exec/confirm", json=body)
    assert conf.status_code == 200, conf.text
    trade = conf.json()["trade"]
    assert trade["status"] == "sim_filled"

    ledger = client.get("/api/exec/ledger")
    assert ledger.status_code == 200
    assert len(ledger.json()["trades"]) >= 1

    csv_resp = client.get("/api/exec/ledger/export?format=csv")
    assert csv_resp.status_code == 200
    assert "client_order_id" in csv_resp.text

    js_resp = client.get("/api/exec/ledger/export?format=json")
    assert js_resp.status_code == 200

    arm = client.post("/api/exec/kill", json={"confirmed": False, "actor": "api-test"})
    assert arm.json()["armed"] is True
    kill = client.post("/api/exec/kill", json={"confirmed": True, "actor": "api-test"})
    assert kill.json()["cancelled"] >= 1
