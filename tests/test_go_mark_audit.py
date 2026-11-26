"""26-Nov: every GO mark leaves a trace of the chain row it was priced from.

Logging only. These tests also pin that the mark value itself is unchanged.
"""

from __future__ import annotations

import asyncio
import json

import pandas as pd
import pytest


@pytest.fixture
def ge(tmp_path, monkeypatch):
    import data.journal.go_efficiency as go_eff
    import data.journal.store as paper

    monkeypatch.setattr(go_eff, "_DB_PATH", tmp_path / "go_efficiency.db")
    monkeypatch.setattr(paper, "_DB_PATH", tmp_path / "paper.db")
    go_eff._SCHEMA_READY_PATHS.clear()
    return go_eff


def _chain():
    # Two expiries share NIFTY CE 25500. Front (Nov 17) is 28.5; next week (Nov 24) is 191.7.
    return pd.DataFrame(
        [
            {"expiry": "2026-11-17", "strike": 25500.0, "type": "C", "mid": 28.5},
            {"expiry": "2026-11-24", "strike": 25500.0, "type": "C", "mid": 191.7},
            {"expiry": "2026-11-17", "strike": 25150.0, "type": "P", "mid": 1.45},
        ]
    )


def test_chain_leg_row_returns_front_expiry_and_price():
    import web_server as ws

    px, used = ws._chain_leg_row(_chain(), 25500.0, "CE", expiry="")
    assert (px, used) == (28.5, "2026-11-17")


def test_chain_leg_row_named_expiry_wins_over_front():
    import web_server as ws

    px, used = ws._chain_leg_row(_chain(), 25500.0, "CE", expiry="2026-11-24")
    assert (px, used) == (191.7, "2026-11-24")


def test_chain_leg_row_missing_strike_is_none():
    import web_server as ws

    assert ws._chain_leg_row(_chain(), 26000.0, "CE") == (None, None)


def test_chain_leg_ltp_value_unchanged_by_refactor():
    import web_server as ws

    assert ws._chain_leg_ltp(_chain(), 25500.0, "CE", expiry="") == 28.5
    assert ws._chain_leg_ltp(_chain(), 25150.0, "PE") == 1.45  # PE asked, only PE row exists
    assert ws._chain_leg_ltp(_chain(), 26000.0, "CE") is None


def test_record_mark_audit_writes_row_with_legs(ge):
    legs = [{"action": "BUY", "opt": "CE", "strike": 25500.0, "expiry_used": "2026-11-17", "price": 28.5}]
    ge.record_mark_audit("dec-1", source="chain_refresh", mark_premium=-28.5, mark_spot=25413.0, legs=legs)
    conn = ge._conn()
    row = conn.execute("SELECT decision_id, source, mark_premium, mark_spot, legs_json FROM go_mark_audit").fetchone()
    conn.close()
    assert row[0] == "dec-1" and row[1] == "chain_refresh"
    assert row[2] == -28.5 and row[3] == 25413.0
    assert json.loads(row[4]) == legs


def test_refresh_records_expiry_used_and_keeps_mark(ge, monkeypatch):
    import config
    import web_server as ws

    decision = {
        "contracts": [{"action": "BUY", "opt_type": "CE", "strike": 25500.0, "expiry": ""}],
    }
    monkeypatch.setattr(config, "is_market_open", lambda: True)
    monkeypatch.setattr(ge, "list_open", lambda sym, include_decision=True: [
        {"decision_id": "dec-9", "decision": decision},
    ])
    marks = []

    def spy_mark_open(decision_id, **kw):
        # Stand in for the real update: record what would have been written.
        marks.append((decision_id, kw["mark_premium"]))
        return {"decision_id": decision_id}

    monkeypatch.setattr(ge, "mark_open", spy_mark_open)

    class _Dash:
        contracts = _chain()
        levels = type("L", (), {"spot": 25413.0})()

    updated = asyncio.run(ws._go_refresh_open_marks("NIFTY", _Dash()))
    assert updated == 1
    assert marks == [("dec-9", -28.5)]  # BUY priced at front-expiry LTP, sign unchanged

    conn = ge._conn()
    row = conn.execute("SELECT source, legs_json FROM go_mark_audit WHERE decision_id='dec-9'").fetchone()
    conn.close()
    assert row[0] == "chain_refresh"
    legs = json.loads(row[1])
    assert legs[0]["expiry_used"] == "2026-11-17"
    assert legs[0]["price"] == 28.5
    assert legs[0]["strike"] == 25500.0
