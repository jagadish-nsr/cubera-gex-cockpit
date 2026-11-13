"""Hard-gate registry + wiring regressions (12-Nov VWAP blackout class of bugs)."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

from app.hard_gates import (
    GATES,
    TAB_BANNER_SOURCES,
    assert_blast_flags_equal_walls,
    assert_go_fire_premium_gated,
    assert_publisher_wires_vwap,
    assert_tab_banner_sources,
    assert_te_refuses_without_iv,
    gate_names,
    require_context,
)
import web_server


ROOT = Path(__file__).resolve().parents[1]


def test_gate_registry_covers_known_money_paths():
    names = set(gate_names())
    assert "ideas_session_vwap" in names
    assert "paper_execute_ltp_strike" in names
    assert "te_reprice_expiry" in names
    assert "te_reprice_ltp" in names
    assert "te_run_spot" in names
    assert "te_run_atm_iv" in names
    assert "te_run_expiry" in names
    assert "go_fire_premium" in names
    assert "te_strike_iv" in names
    assert "blast_equal_walls" in names
    assert all(g.required_context for g in GATES)


def test_require_context_flags_missing_vwap_bar():
    missing = require_context(
        "ideas_session_vwap",
        {"latest_bar": None, "sentinel_bars": []},
    )
    assert "latest_bar" in missing
    assert "sentinel_bars" in missing


def test_require_context_ok_when_bar_present():
    bar = object()
    missing = require_context(
        "ideas_session_vwap",
        {"latest_bar": bar, "sentinel_bars": [bar]},
    )
    assert missing == []


def test_require_context_go_fire_premium():
    assert "contracts_ltp" in require_context("go_fire_premium", {"contracts_ltp": None})
    assert "contracts_ltp" in require_context("go_fire_premium", {"contracts_ltp": 0})
    assert require_context("go_fire_premium", {"contracts_ltp": 42.5}) == []


def test_require_context_blast_equal_walls():
    missing = require_context("blast_equal_walls", {"call_wall": 0, "put_wall": 24500})
    assert "call_wall" in missing
    assert require_context(
        "blast_equal_walls", {"call_wall": 24500, "put_wall": 24500}
    ) == []


def test_publisher_source_wires_vwap_context():
    src = inspect.getsource(web_server._rec_publisher_loop)
    assert_publisher_wires_vwap(src)
    loader = inspect.getsource(web_server._load_rec_sentinel_context)
    assert "list_recent_bars" in loader


def test_go_fire_premium_source_inspection():
    from analytics import go_desk
    src = inspect.getsource(go_desk.decisive_gate)
    assert_go_fire_premium_gated(src)
    assert "missing_premium" in src


def test_te_iv_refuse_source_inspection():
    src = inspect.getsource(web_server.reprice_target_engine)
    assert_te_refuses_without_iv(src)


def test_blast_equal_walls_source_inspection():
    from app import gammablast
    src = inspect.getsource(gammablast)
    assert_blast_flags_equal_walls(src)


def test_tab_banner_sources_wired_in_cockpit_js():
    js = (ROOT / "app" / "static" / "js" / "cockpit.js").read_text(encoding="utf-8")
    assert_tab_banner_sources(js)
    for key in TAB_BANNER_SOURCES:
        assert f'noteFetchResult("{key}"' in js


def test_execute_refuses_missing_ltp_strike():
    from tests.test_web_server_endpoints import asgi_request

    status, _, content = asyncio.run(
        asgi_request(
            "POST",
            "/api/recommendations/execute",
            body={"symbol": "NIFTY", "side": "CE", "lots": 1},
        )
    )
    assert status == 400
    data = json.loads(content)
    assert "ltp" in str(data).lower() or "strike" in str(data).lower()


def test_reprice_source_has_no_hardcoded_past_expiry():
    src = inspect.getsource(web_server.reprice_target_engine)
    assert "2026-10-19" not in src
    assert "next_weekly_expiry" in src or "next_monthly_expiry" in src


def test_decisive_gate_blocks_fire_without_premium():
    from analytics.go_desk import GoLeg, decisive_gate

    blocks = decisive_gate(
        state="FIRE",
        contracts=[GoLeg(action="BUY", opt_type="CE", strike=24500.0, ltp=None)],
        risk={"max_loss_inr": 1000, "credit_or_debit": -10},
        evidence=[],
        context={"lot_size": 65, "oi_ts": "x"},
        hard_blocks=[],
        strategy_key="TEST",
    )
    assert "missing_premium" in blocks
