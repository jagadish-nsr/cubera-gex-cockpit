"""Live Confirm on Delta — fail-closed gate, no Auto, mocked venue."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

os.environ["CRYPTO_DB_URL"] = "sqlite:///:memory:"
os.environ.pop("DELTA_LIVE_API_KEY", None)
os.environ.pop("DELTA_LIVE_API_SECRET", None)
os.environ.pop("CRYPTO_LIVE_CONFIRM_ACK", None)
os.environ.pop("DELTA_LIVE_IP_WHITELIST_ACK", None)
os.environ.pop("DELTA_LIVE_REST_URL", None)
os.environ.pop("CRYPTO_LIVE_FEEDS", None)

from crypto.config import get_flag, list_flags, load_config, set_flag
from crypto.exec.live_gate import (
    LIVE_ACK_ENV,
    LIVE_ACK_VALUE,
    evaluate_live_gate,
    validate_live_base_url,
)
from crypto.exec.risk import resolve_exec_mode, size_from_stop, live_equity_usd
from crypto.exec.service import (
    ExecutionService,
    get_execution_service,
    reset_execution_service,
)
from crypto.providers.delta.live_execution import DeltaLiveExecutionVenue
from crypto.providers.registry import get_registry
from crypto.service import CockpitService
from crypto.store.db import get_engine, init_db


NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)


def _clear_live_env(monkeypatch):
    for k in (
        "DELTA_LIVE_API_KEY",
        "DELTA_LIVE_API_SECRET",
        "CRYPTO_LIVE_CONFIRM_ACK",
        "DELTA_LIVE_IP_WHITELIST_ACK",
        "DELTA_LIVE_REST_URL",
        "DELTA_PAPER_API_KEY",
        "DELTA_PAPER_API_SECRET",
    ):
        monkeypatch.delenv(k, raising=False)


def _arm_live_env(monkeypatch):
    monkeypatch.setenv("DELTA_LIVE_API_KEY", "live-key-test")
    monkeypatch.setenv("DELTA_LIVE_API_SECRET", "live-secret-test")
    monkeypatch.setenv(LIVE_ACK_ENV, LIVE_ACK_VALUE)
    monkeypatch.setenv("DELTA_LIVE_IP_WHITELIST_ACK", "1")
    monkeypatch.setenv("DELTA_LIVE_REST_URL", "https://api.india.delta.exchange")


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'live.db'}")
    _clear_live_env(monkeypatch)
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


def test_flags_include_live_and_auto_off():
    flags = list_flags()
    assert "exec.live" in flags
    assert flags["exec.live"] is False
    assert flags.get("exec.auto") is False


def test_live_gate_fails_closed_without_keys():
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    gate = evaluate_live_gate(require_flags=True)
    assert gate.ok is False
    assert gate.code == "live_gate_failed"
    assert any("DELTA_LIVE_API_KEY" in r for r in gate.reasons)


def test_live_gate_fails_without_ack(monkeypatch):
    monkeypatch.setenv("DELTA_LIVE_API_KEY", "k")
    monkeypatch.setenv("DELTA_LIVE_API_SECRET", "s")
    monkeypatch.setenv("DELTA_LIVE_IP_WHITELIST_ACK", "1")
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    gate = evaluate_live_gate(require_flags=True)
    assert gate.ok is False
    assert any(LIVE_ACK_ENV in r for r in gate.reasons)


def test_live_gate_refuses_testnet_url(monkeypatch):
    _arm_live_env(monkeypatch)
    monkeypatch.setenv("DELTA_LIVE_REST_URL", "https://cdn-ind.testnet.deltaex.org")
    ok, _, reasons = validate_live_base_url()
    assert ok is False
    assert reasons


def test_live_gate_ok_when_fully_armed(monkeypatch):
    _arm_live_env(monkeypatch)
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    gate = evaluate_live_gate(require_flags=True)
    assert gate.ok is True
    assert gate.base_url.endswith("api.india.delta.exchange")


def test_paper_keys_do_not_unlock_live(monkeypatch):
    monkeypatch.setenv("DELTA_PAPER_API_KEY", "paper-only")
    monkeypatch.setenv("DELTA_PAPER_API_SECRET", "paper-secret")
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    gate = evaluate_live_gate(require_flags=True)
    assert gate.ok is False
    assert any("paper" in r.lower() for r in gate.reasons)


def test_resolve_mode_live_confirm(monkeypatch):
    _arm_live_env(monkeypatch)
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    mode = resolve_exec_mode(live_gate_ok=True)
    assert mode["mode"] == "live_confirm"
    assert mode["badge"] == "Live Confirm"
    assert mode["can_confirm"] is True


def test_resolve_mode_live_falls_back_to_paper_when_gate_fails():
    set_flag("exec.live", True, actor="test")
    set_flag("exec.paper", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    mode = resolve_exec_mode(live_gate_ok=False)
    assert mode["mode"] == "paper_confirm"
    assert mode["can_confirm"] is True


def test_resolve_mode_refuses_auto():
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    set_flag("exec.auto", True, actor="test")
    mode = resolve_exec_mode(live_gate_ok=True)
    assert mode["can_confirm"] is False
    assert "auto" in mode["reason"].lower()


def test_live_confirm_refused_without_gate():
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    svc = ExecutionService()

    async def _run():
        out = await svc.confirm_from_card(_go_card())
        assert out["ok"] is False
        assert out["code"] in {"confirm_blocked", "live_gate_failed"}

    asyncio.run(_run())


def test_live_confirm_submits_when_gate_ok(monkeypatch):
    _arm_live_env(monkeypatch)
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    get_registry(reload=True)
    svc = ExecutionService()

    async def _fake_place(self, order):
        return {
            "ok": True,
            "adapter": "live",
            "status": "submitted",
            "client_order_id": order["client_order_id"],
            "fill_price": None,
        }

    async def _run():
        with patch.object(DeltaLiveExecutionVenue, "place_bracket", _fake_place):
            result = await svc.confirm_from_card(_go_card(), actor="pytest")
        assert result["ok"] is True
        trade = result["trade"]
        assert trade["mode"] == "live_confirm"
        assert trade["adapter"] == "live"
        assert trade["status"] == "submitted"
        assert trade["size_usd"] > 0
        assert trade["size_usd"] <= 2500

    asyncio.run(_run())


def test_live_venue_never_sim_fills_on_gate_fail():
    venue = DeltaLiveExecutionVenue()

    async def _run():
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
        assert out["ok"] is False
        assert out["adapter"] == "live"
        assert out["status"] == "refused"
        assert out.get("fill_price") is None

    asyncio.run(_run())


def test_kill_switch_works_with_live_flag(monkeypatch):
    _arm_live_env(monkeypatch)
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    get_registry(reload=True)
    svc = ExecutionService()

    async def _fake_place(self, order):
        return {
            "ok": True,
            "adapter": "live",
            "status": "submitted",
            "client_order_id": order["client_order_id"],
            "fill_price": None,
        }

    async def _fake_cancel(self):
        return {"ok": True, "cancelled": 1, "client_order_ids": []}

    async def _run():
        with patch.object(DeltaLiveExecutionVenue, "place_bracket", _fake_place):
            await svc.confirm_from_card(_go_card())
        assert len(svc.ledger.open_intents()) == 1
        with patch.object(DeltaLiveExecutionVenue, "cancel_all", _fake_cancel):
            arm = await svc.kill(confirmed=False, actor="pytest")
            assert arm["armed"] is True
            done = await svc.kill(confirmed=True, actor="pytest")
        assert done["cancelled"] == 1
        assert get_flag("exec.confirm") is False

    asyncio.run(_run())


def test_snapshot_live_confirm_badge(monkeypatch):
    _arm_live_env(monkeypatch)
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    set_flag("ui.go_cards", True, actor="test")
    get_registry(reload=True)

    async def _run():
        snap = await CockpitService().snapshot()
        assert snap["mode"] == "live_confirm"
        assert snap["mode_badge"] == "Live Confirm"
        assert snap["execution"]["can_confirm"] is True
        assert snap["execution"]["live_gate"]["ok"] is True

    asyncio.run(_run())


def test_api_live_gate_and_confirm_blocked(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'api_live.db'}")
    _clear_live_env(monkeypatch)
    load_config(reload=True)
    get_engine(reload=True)
    init_db(reload=True)
    reset_execution_service()
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    get_registry(reload=True)

    from crypto.server import app

    client = TestClient(app)
    gate = client.get("/api/exec/live-gate")
    assert gate.status_code == 200
    assert gate.json()["ok"] is False

    conf = client.post(
        "/api/exec/confirm",
        json={"actor": "api-test", "card": _go_card()},
    )
    assert conf.status_code == 409

    # Advisory default when live off
    set_flag("exec.live", False, actor="test")
    set_flag("exec.paper", False, actor="test")
    denied = client.post(
        "/api/exec/confirm",
        json={"actor": "api-test", "card": _go_card()},
    )
    assert denied.status_code == 403


def test_live_sizing_uses_live_equity(monkeypatch):
    # Ensure live equity helper is wired; size still capped
    assert live_equity_usd() > 0
    sized = size_from_stop(
        entry=100_000,
        stop=99_000,
        size_cap_usd=2500,
        equity_usd=live_equity_usd(),
        risk_frac=0.005,
    )
    assert sized.size_usd == 2500
    assert sized.capped is True
