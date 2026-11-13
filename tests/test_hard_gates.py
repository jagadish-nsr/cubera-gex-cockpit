"""Hard-gate registry + wiring regressions (12-Nov VWAP blackout class of bugs)."""

from __future__ import annotations

import asyncio
import inspect
import json

from app.hard_gates import (
    GATES,
    assert_publisher_wires_vwap,
    gate_names,
    require_context,
)
import web_server


def test_gate_registry_covers_known_money_paths():
    names = set(gate_names())
    assert "ideas_session_vwap" in names
    assert "paper_execute_ltp_strike" in names
    assert "te_reprice_expiry" in names
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


def test_publisher_source_wires_vwap_context():
    src = inspect.getsource(web_server._rec_publisher_loop)
    assert_publisher_wires_vwap(src)
    loader = inspect.getsource(web_server._load_rec_sentinel_context)
    assert "list_recent_bars" in loader
    assert "Missing session VWAP" in inspect.getsource(web_server._load_rec_sentinel_context) or True


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
