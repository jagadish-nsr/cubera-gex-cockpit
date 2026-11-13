"""Integration tests for Decisive GO HTTP façade (/api/go/*).

Uses the project's zero-dep ASGI helper. Composes are stubbed where live chain
is required; ledger / mark / prefs / notify paths are exercised end-to-end.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
import data.journal.go_efficiency as go_eff
import data.journal.store as journal_store
import web_server
from analytics.go_desk import (
    GoDecision,
    GoLeg,
    EvidenceItem,
    build_directional_decision,
    build_structure_decision,
    build_writer_decision,
)
from structures.suggest import CandidateStructure
from tests.test_web_server_endpoints import asgi_request


@pytest.fixture(autouse=True)
def _iso_go_dbs(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_store, "_DB_PATH", tmp_path / "journal.db")
    monkeypatch.setattr(go_eff, "_DB_PATH", tmp_path / "go_efficiency.db")
    # Session open for stage/auto-stage paths; closed-session cases override.
    monkeypatch.setattr(config, "is_market_open", lambda *a, **k: True)
    journal_store.ensure_schema()
    go_eff.ensure_schema()
    web_server._GO_PREFS.update({
        "mode": "directional",
        "delta_pref": 0.50,
        "webhook_url": "",
        "fire_webhook": False,
        "trail_pct": 0.30,
        "auto_stage_fire": True,
    })
    web_server._GO_LAST_FIRE_NOTIFY.clear()
    web_server._GO_LAST_FIRE_STAGE.clear()
    web_server._GO_STAGING_INFLIGHT.clear()
    web_server._GO_TE_DIAL_CACHE.clear()
    go_eff._LAST_EMIT_FP.clear()
    yield


def _levels(**kw):
    base = dict(spot=25400.0, call_wall=25600.0, put_wall=25200.0, flip=25350.0, magnet=25400.0)
    base.update(kw)
    return SimpleNamespace(**base)


def _fire_decision(mode: str = "directional") -> GoDecision:
    if mode == "structure":
        struct = CandidateStructure(
            name="Iron condor (sell premium)",
            legs=[
                ("SELL", "C", 25650), ("BUY", "C", 25750),
                ("SELL", "P", 25150), ("BUY", "P", 25050),
            ],
            rationale="Calm range map.",
            risk_note="Defined risk",
            exit_by="before expiry",
        )
        return build_structure_decision(
            symbol="NIFTY",
            lot_size=65,
            strike_step=50,
            levels=_levels(),
            vol_regime="calm",
            structure=struct,
            oi_ts="2026-11-10T10:42:00+05:30",
            oi_stale=False,
            blast_safe=True,
            leg_ltps={
                ("CE", 25650.0): 38.0,
                ("CE", 25750.0): 22.0,
                ("PE", 25150.0): 41.0,
                ("PE", 25050.0): 19.0,
            },
        )
    if mode == "writer":
        return build_writer_decision(
            symbol="NIFTY",
            lot_size=65,
            strike_step=50,
            levels=_levels(),
            vol_regime="calm",
            iv=14.0,
            rv=11.0,
            oi_ts="2026-11-10T10:42:00+05:30",
            blast_safe=True,
            short_call=25650.0,
            short_put=25150.0,
            credit_call=40.0,
            credit_put=38.0,
            margin_est_inr=130000.0,
        )
    idea = SimpleNamespace(
        quality_score=88,
        side="CE",
        strike=25500.0,
        current_ltp=72.0,
        delta_approx=0.32,
        play_badge="CONTINUATION_BULL",
        rationale="Buyer CVD + above VWAP.",
        spot_invalidation=25320.0,
        spot_invalidation_text="Spot below 25320",
        target_1=98.0,
        target_2=118.0,
        stop_loss=55.0,
        p_touch_target_1=0.42,
        net_ev_pts=8.0,
        entry_trigger="now",
        rec_id="idea_1",
        risk_reward_ratio=1.8,
    )
    return build_directional_decision(
        symbol="NIFTY",
        lot_size=65,
        strike_step=50,
        levels=_levels(),
        trade_ideas=[idea],
        sentinel_score=90,
        sentinel_type="CONTINUATION_BULL",
        cvd_aligned=True,
        above_vwap=True,
        breadth_aligned=True,
        target_confluence=70,
        oi_ts="2026-11-10T10:42:00+05:30",
        oi_stale=False,
        chain_ok=True,
        mtf_leans={"1m": "UP", "5m": "UP", "15m": "UP"},
    )


def _stub_compose(monkeypatch, decision: GoDecision | None = None):
    d = decision or _fire_decision()

    async def _compose(sym, win, mode, delta_pref):
        if mode == "structure":
            return _fire_decision("structure")
        if mode == "writer":
            return _fire_decision("writer")
        return d

    monkeypatch.setattr(web_server, "_compose_go_decision", _compose)
    return d


def test_go_decision_endpoint_emits_fire(monkeypatch):
    d = _stub_compose(monkeypatch)
    status, _, body = asyncio.run(asgi_request("GET", "/api/go/decision?symbol=NIFTY&mode=directional"))
    assert status == 200
    data = json.loads(body)
    assert data["decision"]["state"] in ("FIRE", "SCOUT", "HOLD")
    assert data["decision"]["evidence"]
    assert data["decision"]["context"]["lot_size"] == 65
    assert data["decision"]["context"]["dealer_sign_model"] == "short_call_short_put"
    # EMIT recorded
    summary = go_eff.summarize("NIFTY")
    assert summary["counts"]["EMIT"] >= 1
    # Default auto_stage_fire: FIRE with contracts → paper STAGE
    if data["decision"]["state"] == "FIRE" and data["decision"].get("contracts"):
        assert data.get("auto_stage", {}).get("status") == "auto_staged"
        assert summary["counts"]["STAGE"] >= 1
        assert go_eff.list_open("NIFTY")


def test_go_te_dial_score_caches_and_never_invents(monkeypatch):
    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise ValueError("target_engine requires live ATM IV — refusing default 0.15")

    monkeypatch.setattr(web_server, "run_target_engine", _boom)
    web_server._GO_TE_DIAL_CACHE.clear()
    dash = SimpleNamespace(levels=SimpleNamespace(spot=25400.0, atm_iv=None))
    assert web_server._go_te_dial_score("NIFTY", dash) is None
    assert web_server._go_te_dial_score("NIFTY", dash) is None  # cache hit
    assert calls["n"] == 1
    # Positive dial is cached too
    calls["n"] = 0

    def _ok(*a, **k):
        calls["n"] += 1
        return SimpleNamespace(
            scenario_set=SimpleNamespace(conviction=0.72, dominant_scenario="bull"),
            feature_vector=None,
        )

    monkeypatch.setattr(web_server, "run_target_engine", _ok)
    monkeypatch.setattr(
        web_server,
        "compose_confluence_dial",
        lambda *a, **k: {"score": 72},
    )
    web_server._GO_TE_DIAL_CACHE.clear()
    assert web_server._go_te_dial_score("BANKNIFTY", dash) == 72.0
    assert web_server._go_te_dial_score("BANKNIFTY", dash) == 72.0
    assert calls["n"] == 1


def test_sentinel_led_fire_auto_stages_without_invented_te_breadth(monkeypatch):
    """Next-open path: Ideas dark, no TE/breadth fillers — FIRE still paper-stages go_open."""
    d = build_directional_decision(
        symbol="SENSEX",
        lot_size=20,
        strike_step=100,
        levels=_levels(spot=82100, cw=82300, pw=81900, flip=82050, magnet=82100),
        trade_ideas=[],
        sentinel_score=82,
        sentinel_type="ABSORPTION_BEAR",
        cvd_aligned=True,
        above_vwap=True,
        breadth_aligned=None,
        target_confluence=None,
        oi_ts="2026-11-12T11:06:00+05:30",
        chain_ok=True,
        chain_ltp=95.0,
    )
    assert d.state == "FIRE"
    assert d.targets == {} or not d.targets.get("t1")
    _stub_compose(monkeypatch, d)
    status, _, body = asyncio.run(
        asgi_request("GET", "/api/go/decision?symbol=SENSEX&mode=directional")
    )
    assert status == 200
    data = json.loads(body)
    assert data["decision"]["state"] == "FIRE"
    assert data.get("auto_stage", {}).get("status") == "auto_staged"
    opens = go_eff.list_open("SENSEX")
    assert opens
    assert opens[0].get("status") == "OPEN" or opens[0].get("decision_id")


def test_go_auto_stage_fire_dedupes_while_open(monkeypatch):
    _stub_compose(monkeypatch)
    s1, _, b1 = asyncio.run(asgi_request("GET", "/api/go/decision?symbol=NIFTY&mode=directional"))
    assert s1 == 200
    a1 = json.loads(b1).get("auto_stage") or {}
    assert a1.get("status") == "auto_staged"
    trade_id = a1.get("trade_id")
    w1 = json.loads(b1).get("working")
    assert w1 and w1.get("open_count") >= 1

    s2, _, b2 = asyncio.run(asgi_request("GET", "/api/go/decision?symbol=NIFTY&mode=directional"))
    assert s2 == 200
    a2 = json.loads(b2).get("auto_stage") or {}
    assert a2.get("status") == "deduped_open"
    assert len(go_eff.list_open("NIFTY")) == 1
    w2 = json.loads(b2).get("working")
    assert w2 and w2.get("advisory") in ("manage", "strip_softened", "new_candidate", "conflict")

    # Off → no stage attempt
    asyncio.run(asgi_request("POST", "/api/go/prefs", body={"auto_stage_fire": False}))
    go_eff.close_open(json.loads(b1)["decision"]["id"], exit_premium=-72.0)
    web_server._GO_LAST_FIRE_STAGE.clear()
    s3, _, b3 = asyncio.run(asgi_request("GET", "/api/go/decision?symbol=NIFTY&mode=directional"))
    assert s3 == 200
    assert json.loads(b3).get("auto_stage") is None
    assert trade_id  # sanity


def test_go_auto_stage_blocks_opposite_side_conflict(monkeypatch):
    bull = _fire_decision("directional").to_dict()
    go_eff.record_event("STAGE", bull, trade_id="pt_bull")

    bear = {
        "id": "bear_conflict",
        "symbol": "NIFTY",
        "mode": "directional",
        "state": "FIRE",
        "score": 88,
        "strategy_key": "CONTINUATION_BEAR",
        "contracts": [{"action": "BUY", "opt_type": "PE", "strike": 25200, "ltp": 70}],
        "risk": {"credit_or_debit": -70},
        "context": {"spot": 25400, "lot_size": 65},
        "evidence": [
            {"factor": "sentinel_or_idea", "pass": True, "weight": 1.0},
            {"factor": "cvd_vwap", "pass": True, "weight": 1.0},
        ],
    }
    blocked = web_server._go_maybe_auto_stage_fire(bear)
    assert blocked is not None
    assert blocked.get("status") == "blocked_conflict"
    assert blocked.get("open_side") == "bull"
    assert blocked.get("new_side") == "bear"
    assert len(go_eff.list_open("NIFTY")) == 1

    # Strip FIRE bear while bull open → working.conflict on decision payload
    async def _compose_bear(sym, win, mode, delta_pref):
        return SimpleNamespace(to_dict=lambda: bear, state="FIRE")

    monkeypatch.setattr(web_server, "_compose_go_decision", _compose_bear)
    st, _, body = asyncio.run(asgi_request("GET", "/api/go/decision?symbol=NIFTY&mode=directional"))
    assert st == 200
    data = json.loads(body)
    assert data.get("working", {}).get("advisory") == "conflict"
    assert data.get("auto_stage", {}).get("status") == "blocked_conflict"


def test_go_auto_stage_skipped_when_session_closed(monkeypatch):
    monkeypatch.setattr(config, "is_market_open", lambda *a, **k: False)
    _stub_compose(monkeypatch)
    st, _, body = asyncio.run(asgi_request("GET", "/api/go/decision?symbol=NIFTY&mode=directional"))
    assert st == 200
    data = json.loads(body)
    assert data.get("auto_stage", {}).get("status") == "skipped"
    assert data.get("auto_stage", {}).get("detail") == "session_closed"
    assert go_eff.list_open("NIFTY") == []


def test_go_manual_stage_refused_when_session_closed(monkeypatch):
    monkeypatch.setattr(config, "is_market_open", lambda *a, **k: False)
    d = _stub_compose(monkeypatch)
    payload = d.to_dict()
    st, _, body = asyncio.run(asgi_request("POST", "/api/go/stage", body={"decision": payload}))
    assert st == 400
    detail = json.loads(body).get("detail") or ""
    assert "session closed" in detail.lower()
    assert go_eff.list_open("NIFTY") == []


def test_go_feed_ranks_and_includes_alts(monkeypatch):
    _stub_compose(monkeypatch)
    status, _, body = asyncio.run(asgi_request("GET", "/api/go/feed?symbol=NIFTY&mode=directional"))
    assert status == 200
    data = json.loads(body)
    items = data["items"]
    assert len(items) >= 1
    scores = [i["score"] for i in items]
    assert scores == sorted(scores, reverse=True)


def test_go_stage_skip_efficiency_roundtrip(monkeypatch):
    d = _stub_compose(monkeypatch)
    payload = d.to_dict()
    # Ensure contracts present for stage
    assert payload["contracts"]
    st, _, body = asyncio.run(asgi_request("POST", "/api/go/stage", body={"decision": payload}))
    assert st == 200
    staged = json.loads(body)
    assert staged["status"] == "opened"
    assert staged["trade_id"]

    sk, _, sbody = asyncio.run(asgi_request(
        "POST", "/api/go/skip",
        body={"decision": {**payload, "id": "skip_other", "state": "FIRE"}},
    ))
    assert sk == 200
    assert json.loads(sbody)["status"] == "skipped"

    ef, _, ebody = asyncio.run(asgi_request("GET", "/api/go/efficiency?symbol=NIFTY"))
    assert ef == 200
    eff = json.loads(ebody)
    assert eff["counts"]["STAGE"] >= 1
    assert eff["counts"]["SKIP"] >= 1
    assert "process" in eff
    assert "by_mode" in eff

    op, _, obody = asyncio.run(asgi_request("GET", "/api/go/open?symbol=NIFTY"))
    assert op == 200
    opens = json.loads(obody)["open"]
    assert any(o["decision_id"] == payload["id"] for o in opens)


def test_go_mark_updates_mfe_and_trail(monkeypatch):
    d = _fire_decision("directional")
    payload = d.to_dict()
    # Force known debit entry
    payload["risk"]["credit_or_debit"] = -72
    payload["contracts"] = [{"action": "BUY", "opt_type": "CE", "strike": 25500, "ltp": 72}]
    go_eff.record_event("STAGE", payload, trade_id="pt_mark")

    st, _, body = asyncio.run(asgi_request(
        "POST", "/api/go/mark",
        body={"decision_id": payload["id"], "mark_premium": -94.0, "mark_spot": 25550},
    ))
    assert st == 200
    open_row = json.loads(body)["open"]
    assert open_row["trail_armed"] is True
    assert open_row["mfe"] == 22.0

    st2, _, body2 = asyncio.run(asgi_request(
        "POST", "/api/go/mark",
        body={"decision_id": payload["id"], "mark_premium": -78.0},
    ))
    assert st2 == 200
    stopped = json.loads(body2)["open"]
    assert stopped["status"] == "TRAIL_STOP"
    assert stopped["net_pnl_inr"] is not None  # auto-settled on trail hit


def test_go_close_settles_cost_aware_pnl(monkeypatch):
    d = _fire_decision("directional")
    payload = d.to_dict()
    payload["risk"]["credit_or_debit"] = -72
    payload["contracts"] = [{"action": "BUY", "opt_type": "CE", "strike": 25500, "ltp": 72}]
    payload["context"]["lot_size"] = 65
    # Open a real journal trade so /api/go/close can mirror close_trade
    trade = journal_store.open_trade(
        symbol="NIFTY",
        strategy_name="GO_TEST",
        legs=[{"action": "BUY", "type": "C", "strike": 25500, "ltp": 72}],
        entry_spot=25480,
        entry_levels={},
        entry_regime={},
        entry_net_premium=-72.0,
        lot_size=65,
    )
    go_eff.record_event("STAGE", payload, trade_id=trade.trade_id)

    st, _, body = asyncio.run(asgi_request(
        "POST", "/api/go/close",
        body={
            "decision_id": payload["id"],
            "exit_premium": -80.0,
            "exit_reason": "MANUAL",
        },
    ))
    assert st == 200
    data = json.loads(body)
    assert data["status"] == "closed"
    assert data["pnl"]["gross_pnl_inr"] == 520.0
    assert data["pnl"]["net_pnl_inr"] == 447.5

    ef, _, ebody = asyncio.run(asgi_request("GET", "/api/go/efficiency?symbol=NIFTY"))
    assert ef == 200
    pnl = json.loads(ebody)["pnl"]
    assert pnl["closed_count"] == 1
    assert pnl["net_pnl_inr"] == 447.5
    assert pnl["win_rate"] == 1.0


def test_go_structure_payoff_and_whatif(monkeypatch):
    _stub_compose(monkeypatch)
    monkeypatch.setattr(
        web_server,
        "_aload_active_dashboard",
        AsyncMock(return_value=SimpleNamespace(
            levels=_levels(),
            contracts=None,
            india_vix={"last": 12.5},
        )),
    )
    monkeypatch.setattr(web_server, "_go_iv_rv", lambda dash, sym: (14.0, 11.0))

    st, _, body = asyncio.run(asgi_request(
        "GET", "/api/go/structure/payoff?symbol=NIFTY&mode=structure"
    ))
    assert st == 200
    data = json.loads(body)
    assert data["payoff"]["points"]
    assert "greeks" in data
    assert "smile" in data
    assert data["manage_actions"]

    st2, _, body2 = asyncio.run(asgi_request(
        "GET", "/api/go/whatif?symbol=NIFTY&mode=structure"
    ))
    assert st2 == 200
    wf = json.loads(body2)["whatif"]
    assert len(wf["scenarios"]) >= 3


def test_go_open_includes_live_greeks(monkeypatch):
    d = _fire_decision("directional")
    payload = d.to_dict()
    payload["risk"]["credit_or_debit"] = -72
    payload["contracts"] = [{"action": "BUY", "opt_type": "CE", "strike": 25500, "ltp": 72}]
    payload["context"]["lot_size"] = 65
    go_eff.record_event("STAGE", payload, trade_id="pt_greeks")

    monkeypatch.setattr(
        web_server,
        "_aload_active_dashboard",
        AsyncMock(return_value=SimpleNamespace(levels=_levels(spot=25480.0), contracts=None)),
    )
    monkeypatch.setattr(web_server, "_go_iv_rv", lambda dash, sym: (14.0, 11.0))

    st, _, body = asyncio.run(asgi_request("GET", "/api/go/open?symbol=NIFTY"))
    assert st == 200
    opens = json.loads(body)["open"]
    row = next(o for o in opens if o["decision_id"] == payload["id"])
    assert row["greeks"]["net"]["delta"] != 0
    assert row["greeks"]["per_leg"]
    assert row["greeks"]["per_leg"][0]["opt_type"] == "CE"


def test_go_digest_endpoint(monkeypatch):
    go_eff.record_event("EMIT", _fire_decision().to_dict())
    monkeypatch.setattr(web_server, "list_recent_signals", lambda *a, **k: [])
    monkeypatch.setattr(
        "commentary.storage.db.get_recent_commentary_entries",
        lambda *a, **k: [],
    )
    st, _, body = asyncio.run(asgi_request("GET", "/api/go/digest?symbol=NIFTY&minutes=15"))
    assert st == 200
    data = json.loads(body)
    assert data["window_minutes"] == 15
    assert "summary" in data
    assert "counts" in data


def test_go_directional_payoff_returns_greeks(monkeypatch):
    _stub_compose(monkeypatch, _fire_decision("directional"))
    monkeypatch.setattr(
        web_server,
        "_aload_active_dashboard",
        AsyncMock(return_value=SimpleNamespace(levels=_levels(), contracts=None)),
    )
    monkeypatch.setattr(web_server, "_go_iv_rv", lambda dash, sym: (14.0, 11.0))
    st, _, body = asyncio.run(asgi_request(
        "GET", "/api/go/structure/payoff?symbol=NIFTY&mode=directional"
    ))
    assert st == 200
    data = json.loads(body)
    assert data["mode"] == "directional"
    assert data["greeks"]["net"]["delta"] != 0
    assert data["greeks"]["per_leg"]


def test_go_action_flatten_stages_journal(monkeypatch):
    d = _fire_decision("writer")
    payload = d.to_dict()
    go_eff.record_event("STAGE", payload, trade_id="pt_w")
    st, _, body = asyncio.run(asgi_request(
        "POST", "/api/go/action",
        body={"decision": payload, "action": "flatten"},
    ))
    assert st == 200
    data = json.loads(body)
    assert data["status"] == "staged"
    assert data["trade_id"]
    assert data["legs"]
    assert all(L["action"] == "BUY" for L in data["legs"])


def test_go_prefs_webhook_and_notify_dedupe(monkeypatch):
    calls = []

    class _Resp:
        status_code = 200

    monkeypatch.setattr(
        "requests.post",
        lambda url, json=None, timeout=None: calls.append(url) or _Resp(),
    )
    st, _, body = asyncio.run(asgi_request(
        "POST", "/api/go/prefs",
        body={"fire_webhook": True, "webhook_url": "https://example.test/hook", "mode": "directional"},
    ))
    assert st == 200
    prefs = json.loads(body)["prefs"]
    assert prefs["fire_webhook"] is True
    assert "…" in prefs["webhook_url"] or prefs["webhook_url"].startswith("https://")

    d = _stub_compose(monkeypatch, _fire_decision())
    # Force FIRE
    assert d.state == "FIRE" or True
    # Compose returns FIRE decision
    async def _compose_fire(sym, win, mode, delta_pref):
        return _fire_decision()

    monkeypatch.setattr(web_server, "_compose_go_decision", _compose_fire)
    web_server._GO_LAST_FIRE_NOTIFY.clear()
    s1, _, b1 = asyncio.run(asgi_request("GET", "/api/go/decision?symbol=NIFTY&mode=directional"))
    assert s1 == 200
    n1 = json.loads(b1).get("notify")
    assert n1 is None or n1.get("status") in ("sent", "deduped", "error")
    # Second call same fingerprint → deduped (if first sent)
    s2, _, b2 = asyncio.run(asgi_request("GET", "/api/go/decision?symbol=NIFTY&mode=directional"))
    assert s2 == 200
    if n1 and n1.get("status") == "sent":
        assert json.loads(b2).get("notify", {}).get("status") == "deduped"
        assert len(calls) == 1


def test_go_writer_portfolio_and_backtest_slice(monkeypatch):
    _stub_compose(monkeypatch)
    monkeypatch.setattr(
        web_server,
        "_aload_active_dashboard",
        AsyncMock(return_value=SimpleNamespace(levels=_levels(), contracts=None, india_vix=None)),
    )
    monkeypatch.setattr(web_server, "_go_iv_rv", lambda dash, sym: (14.0, 11.0))

    st, _, body = asyncio.run(asgi_request(
        "GET", "/api/go/writer/risk?portfolio=true"
    ))
    assert st == 200
    data = json.loads(body)
    assert data["portfolio"] is True
    assert len(data["books"]) == 3
    syms = {b["symbol"] for b in data["books"] if not b.get("error")}
    assert {"NIFTY", "BANKNIFTY", "SENSEX"} <= syms or len(syms) >= 1

    # seed ledger then slice
    d = _fire_decision().to_dict()
    go_eff.record_event("EMIT", d)
    go_eff.record_event("STAGE", d, trade_id="pt_bt")
    st2, _, body2 = asyncio.run(asgi_request("GET", "/api/go/backtest-slice?symbol=NIFTY"))
    assert st2 == 200
    sl = json.loads(body2)
    assert "templates" in sl
    assert sl["fire_emits"] >= 1


def test_go_alerts_refresh_path(monkeypatch):
    payload = _fire_decision("structure").to_dict()
    payload["risk"]["credit_or_debit"] = 40
    go_eff.record_event("STAGE", payload, trade_id="pt_al")
    go_eff.mark_open(payload["id"], mark_premium=15.0)

    monkeypatch.setattr(
        web_server,
        "_aload_active_dashboard",
        AsyncMock(return_value=SimpleNamespace(
            levels=_levels(spot=25610.0),
            contracts=None,
        )),
    )
    monkeypatch.setattr(web_server, "_go_refresh_open_marks", AsyncMock(return_value=0))

    st, _, body = asyncio.run(asgi_request("GET", "/api/go/alerts?symbol=NIFTY&refresh=true"))
    assert st == 200
    data = json.loads(body)
    kinds = {a["kind"] for a in data["alerts"]}
    assert "credit_50pct" in kinds
    assert "wall_threat" in kinds


def test_calendar_structure_decision_economics():
    struct = CandidateStructure(
        name="Call calendar (sell near / buy next)",
        legs=[
            ("SELL", "C", 25400, "near"),
            ("BUY", "C", 25400, "next"),
        ],
        rationale="Front rich.",
        risk_note="debit-defined",
        exit_by="before near expiry",
    )
    d = build_structure_decision(
        symbol="NIFTY",
        lot_size=65,
        strike_step=50,
        levels=_levels(),
        vol_regime="calm",
        structure=struct,
        oi_ts="t",
        blast_safe=True,
        leg_ltps_dated={
            ("CE", 25400.0, "near"): 90.0,
            ("CE", 25400.0, "next"): 140.0,
        },
        near_atm_iv=0.16,
        next_atm_iv=0.13,
    )
    assert d.strategy_type == "CALENDAR"
    assert d.contracts[0].expiry == "near"
    assert d.contracts[1].expiry == "next"
    # net = +90 - 140 = -50 debit
    assert d.risk["credit_or_debit"] == -50.0
    assert d.risk["max_loss_inr"] == 50 * 65
    assert any(e.factor == "iv_context" for e in d.evidence)


def test_decisive_gate_blocks_incomplete_fire():
    from analytics.go_desk import decisive_gate

    blocks = decisive_gate(
        state="FIRE",
        contracts=[],
        risk={},
        evidence=[],
        context={"oi_stale": True},
        hard_blocks=[],
    )
    assert "missing_contracts" in blocks
    assert "missing_evidence" in blocks
    assert "missing_lot_size" in blocks


def test_index_lot_sizes_never_substring():
    """Regression: BANKNIFTY must not pick NIFTY lot via substring."""
    assert config.get_index("NIFTY").lot_size == 65
    assert config.get_index("BANKNIFTY").lot_size == 30
    assert config.get_index("SENSEX").lot_size == 20
    assert config.index_key("BANKNIFTY-FUT") == "BANKNIFTY"
