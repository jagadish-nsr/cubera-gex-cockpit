"""Manual Decision — decision-only Confirm; no exchange POST; no Auto."""

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
from crypto.exec.decision import build_decision_payload, format_decision_levels
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
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'manual.db'}")
    _clear_live_env(monkeypatch)
    load_config(reload=True)
    set_flag("exec.paper", False, actor="test")
    set_flag("exec.confirm", False, actor="test")
    set_flag("exec.live", False, actor="test")
    set_flag("exec.live_place", False, actor="test")
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
        "invalidation": "through call wall",
        "valid_until": "2026-11-22T13:00:00+00:00",
        "spot": 95100,
    }
    card.update(overrides)
    return card


def test_flags_include_live_place_off():
    flags = list_flags()
    assert "exec.live" in flags
    assert flags["exec.live"] is False
    assert flags.get("exec.live_place") is False
    assert flags.get("exec.auto") is False


def test_decision_payload_has_required_fields():
    payload = build_decision_payload(_go_card())
    for key in (
        "instrument",
        "side",
        "entry_zone",
        "target",
        "stop",
        "size_usd",
        "score",
        "reasons",
        "valid_until",
        "mode",
    ):
        assert key in payload
    assert payload["mode"] == "manual_decision"
    assert "auto-execute" in payload["note"].lower() or "hand" in payload["note"].lower()
    text = format_decision_levels(_go_card())
    assert "BTCUSD" in text
    assert "SELL" in text
    assert "Entry:" in text
    assert "Target:" in text
    assert "Stop:" in text


def test_live_gate_still_fails_closed_without_keys():
    """Legacy gate kept for diagnostics; Manual Decision does not need it."""
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    gate = evaluate_live_gate(require_flags=True)
    assert gate.ok is False


def test_live_gate_refuses_testnet_url(monkeypatch):
    _arm_live_env(monkeypatch)
    monkeypatch.setenv("DELTA_LIVE_REST_URL", "https://cdn-ind.testnet.deltaex.org")
    ok, _, reasons = validate_live_base_url()
    assert ok is False
    assert reasons


def test_resolve_mode_manual_decision_without_keys():
    """Manual Decision needs flags only — no live keys for decision support."""
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    mode = resolve_exec_mode(live_gate_ok=False)
    assert mode["mode"] == "manual_decision"
    assert mode["badge"] == "Manual Decision"
    assert mode["can_confirm"] is True
    assert "Live Auto" not in mode["badge"]
    assert "Live Confirm" not in mode["badge"]


def test_resolve_mode_manual_even_when_gate_ok(monkeypatch):
    _arm_live_env(monkeypatch)
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    mode = resolve_exec_mode(live_gate_ok=True)
    assert mode["mode"] == "manual_decision"
    assert mode["badge"] == "Manual Decision"


def test_resolve_mode_refuses_auto():
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    set_flag("exec.auto", True, actor="test")
    mode = resolve_exec_mode(live_gate_ok=True)
    assert mode["can_confirm"] is False
    assert "auto" in mode["reason"].lower()


def test_manual_confirm_logs_decision_no_exchange_post(monkeypatch):
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    set_flag("exec.live_place", True, actor="test")  # still must not place
    get_registry(reload=True)
    svc = ExecutionService()
    placed = {"n": 0}

    async def _fake_place(self, order):
        placed["n"] += 1
        return {"ok": True, "adapter": "live", "status": "submitted"}

    async def _run():
        with patch.object(DeltaLiveExecutionVenue, "place_bracket", _fake_place):
            result = await svc.confirm_from_card(
                _go_card(), actor="pytest", notes="hand entry at open"
            )
        assert result["ok"] is True
        assert result.get("exchange_post") is False
        assert placed["n"] == 0
        trade = result["trade"]
        assert trade["mode"] == "manual_decision"
        assert trade["adapter"] == "manual"
        assert trade["status"] == "manual_noted"
        assert trade["notes"] == "hand entry at open"
        assert "decision" in result
        assert result["decision"]["instrument"] == "BTCUSD"
        assert "Entry:" in result["levels_text"]
        csv_blob = svc.ledger.export_csv()
        assert "notes" in csv_blob
        assert "hand entry at open" in csv_blob
        assert len(svc.ledger.open_intents()) == 0

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


def test_snapshot_manual_decision_badge():
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    set_flag("ui.go_cards", True, actor="test")
    get_registry(reload=True)

    async def _run():
        snap = await CockpitService().snapshot()
        assert snap["mode"] == "manual_decision"
        assert snap["mode_badge"] == "Manual Decision"
        assert snap["execution"]["can_confirm"] is True
        assert snap["execution"].get("live_place_flag") is False
        # Kill switch is paper-intent only
        assert snap["execution"]["kill_switch_enabled"] is False

    asyncio.run(_run())


def test_api_decision_and_manual_confirm(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'api_manual.db'}")
    _clear_live_env(monkeypatch)
    load_config(reload=True)
    get_engine(reload=True)
    init_db(reload=True)
    reset_execution_service()
    set_flag("exec.live", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    set_flag("ui.go_cards", True, actor="test")
    get_registry(reload=True)

    from crypto.server import app

    client = TestClient(app)
    gate = client.get("/api/exec/live-gate")
    assert gate.status_code == 200
    assert gate.json().get("confirm_places_orders") is False

    decision = client.get("/api/exec/decision")
    assert decision.status_code == 200
    body = decision.json()
    assert body["ok"] is True
    assert "levels_text" in body
    assert body["decision"]["mode"] == "manual_decision"

    conf = client.post(
        "/api/exec/confirm",
        json={"actor": "api-test", "card": _go_card(), "notes": "manual note"},
    )
    assert conf.status_code == 200
    data = conf.json()
    assert data["ok"] is True
    assert data["exchange_post"] is False
    assert data["trade"]["status"] == "manual_noted"
    assert data["trade"]["notes"] == "manual note"

    exported = client.get("/api/exec/ledger/export?format=csv")
    assert exported.status_code == 200
    assert "notes" in exported.text
    assert "manual note" in exported.text


def test_paper_confirm_still_sims_when_live_off():
    set_flag("exec.paper", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    set_flag("exec.live", False, actor="test")
    get_registry(reload=True)
    svc = ExecutionService()

    async def _run():
        result = await svc.confirm_from_card(_go_card(), actor="pytest")
        assert result["ok"] is True
        assert result["trade"]["mode"] == "paper_confirm"
        assert result["trade"]["status"] == "sim_filled"
        assert result.get("exchange_post") is not False or "decision" not in result

    asyncio.run(_run())


def test_live_sizing_helper_still_works(monkeypatch):
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
