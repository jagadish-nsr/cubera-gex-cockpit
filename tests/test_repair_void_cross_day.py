"""repair_void_cross_day: zero only the intraday rows that closed on a later day; leave the rest."""

from __future__ import annotations

import json
import sqlite3

import pytest

from data.journal import go_efficiency as go_eff
from data.journal import recommendations_store as rec_store
from data.journal import store as journal_store
from scripts import repair_void_cross_day as r


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(go_eff, "_DB_PATH", tmp_path / "go_efficiency.db")
    monkeypatch.setattr(journal_store, "_DB_PATH", tmp_path / "paper_trades.db")
    monkeypatch.setattr(rec_store, "_DB_PATH", tmp_path / "recommendations.db")
    go_eff._conn().close()
    journal_store.ensure_schema()
    rec_store._SCHEMA_READY_PATHS.clear()
    rec_store.ensure_schema()

    with sqlite3.connect(tmp_path / "go_efficiency.db") as c:
        for tid, opened, closed, net in [
            ("pt_cross", "2026-11-13T15:23:33+05:30", "2026-11-17T09:04:17+05:30", 5340.7),
            ("pt_same", "2026-11-19T10:00:00+05:30", "2026-11-19T15:30:00+05:30", -812.5),
        ]:
            c.execute(
                "INSERT INTO go_open (decision_id, trade_id, symbol, mode, status, opened_at, updated_at, "
                "decision_json, entry_premium, exit_premium, exit_reason, gross_pnl_inr, brokerage_cost, "
                "slippage_cost, net_pnl_inr, closed_at) VALUES (?, ?, 'NIFTY', 'directional', 'TRAIL_STOP', "
                "?, ?, '{\"id\": \"d\"}', -139.1, -222.38, 'TRAIL_STOP', ?, 40, 32.5, ?, ?)",
                (f"d_{tid}", tid, opened, closed, net + 72.5, net, closed))

    def paper(tid, exit_by, entry, exit_, reason, net):
        c.execute(
            "INSERT INTO paper_trades (trade_id, symbol, strategy_name, status, entry_ts, exit_ts, legs_json, "
            "entry_spot, entry_levels_json, entry_regime_json, entry_net_premium, exit_net_premium, gross_pnl, "
            "brokerage_cost, slippage_cost, net_realized_pnl, exit_reason, lot_size, exit_by) "
            "VALUES (?, 'NIFTY', 's', 'CLOSED', ?, ?, '[]', 25000, '{}', '{}', -139.1, -222.38, ?, 40, 32.5, ?, ?, 65, ?)",
            (tid, entry, exit_, net + 72.5, net, reason, exit_by))

    with sqlite3.connect(tmp_path / "paper_trades.db") as c:
        paper("pt_cross", "", "2026-11-13T15:23:33+05:30", "2026-11-17T09:04:17+05:30", "TRAIL_STOP", 5340.7)
        paper("pt_same", "", "2026-11-19T10:00:00+05:30", "2026-11-19T15:30:00+05:30", "SESSION_END", -812.5)
        paper("pt_sent_gap", "SENTINEL_ENGINE", "2026-11-20T15:07:07+05:30", "2026-11-25T09:16:01+05:30", "TARGET", 41846.6)
        paper("pt_sent_same", "SENTINEL_ENGINE", "2026-11-20T10:00:00+05:30", "2026-11-20T10:30:00+05:30", "TARGET", 900.0)
        paper("pt_structure", "", "2026-11-18T10:00:00+05:30", "2026-11-25T15:30:00+05:30", "EXPIRY_SETTLE", 2500.0)
        paper("pt_dup", "SENTINEL_ENGINE", "2026-11-13T09:17:14+05:30", "2026-11-26T23:53:18+05:30", "VOID_DUPLICATE", 0.0)

    with sqlite3.connect(tmp_path / "recommendations.db") as c:
        for rid, status, created, outcome, pt in [
            ("srec_gap", "TARGET_HIT", "2026-11-20 15:07:07", "2026-11-25 09:16:01", "pt_sent_gap"),
            ("srec_same", "TARGET_HIT", "2026-11-20 10:00:00", "2026-11-20 10:30:00", "pt_sent_same"),
            ("srec_nopaper", "SPOT_INVAL", "2026-11-19 15:13:00", "2026-11-20 11:31:03", None),
        ]:
            c.execute(
                "INSERT INTO sentinel_trade_recommendations (rec_id, symbol, strike, side, option_symbol, "
                "triggered_at, spot_entry, spot_target, spot_invalidation, option_entry, option_target, option_sl, "
                "contributing_signals_json, status, created_at, updated_at, outcome_ts, paper_trade_id) "
                "VALUES (?, 'BANKNIFTY', 64600, 'PE', 'x', ?, 1, 1, 1, 1, 1, 1, '[]', ?, ?, ?, ?, ?)",
                (rid, created, status, created, outcome, outcome, pt))
    return tmp_path


def _one(db, sql, *args):
    with sqlite3.connect(db) as c:
        return c.execute(sql, args).fetchone()


def test_plan_picks_only_cross_day_intraday_rows(journal):
    plan = r.build_plan(journal)
    assert [x["trade_id"] for x in plan.go] == ["pt_cross"]
    assert sorted(x["trade_id"] for x in plan.paper) == ["pt_cross", "pt_sent_gap"]
    assert sorted(x["rec_id"] for x in plan.recs) == ["srec_gap", "srec_nopaper"]
    assert sum(x["net_pnl_inr"] for x in plan.go) == pytest.approx(5340.7)


def test_apply_zeroes_and_keeps_the_original(journal):
    r.apply_plan(journal, r.build_plan(journal), "t")

    status, reason, net, gross, exit_p, dj = _one(
        journal / "go_efficiency.db",
        "SELECT status, exit_reason, net_pnl_inr, gross_pnl_inr, exit_premium, decision_json "
        "FROM go_open WHERE trade_id = 'pt_cross'")
    assert (status, reason, net, gross, exit_p) == ("VOID", "VOID_CROSS_DAY", 0, 0, -139.1)
    kept = json.loads(dj)
    assert kept["id"] == "d"
    assert kept["void_cross_day"]["net_pnl_inr"] == pytest.approx(5340.7)
    assert kept["void_cross_day"]["closed_at"] == "2026-11-17T09:04:17+05:30"
    assert _one(journal / "go_efficiency.db",
                "SELECT status, net_pnl_inr FROM go_open WHERE trade_id = 'pt_same'") == ("TRAIL_STOP", -812.5)

    pdb = journal / "paper_trades.db"
    reason, net, costs, exit_p, notes = _one(
        pdb, "SELECT exit_reason, net_realized_pnl, brokerage_cost + slippage_cost, exit_net_premium, notes "
             "FROM paper_trades WHERE trade_id = 'pt_sent_gap'")
    assert (reason, net, costs, exit_p) == ("VOID_CROSS_DAY", 0, 0, -139.1)
    assert "was TARGET at 2026-11-25T09:16:01+05:30" in notes and "41846.6" in notes
    for tid, want in [("pt_same", "SESSION_END"), ("pt_sent_same", "TARGET"),
                      ("pt_structure", "EXPIRY_SETTLE"), ("pt_dup", "VOID_DUPLICATE")]:
        assert _one(pdb, "SELECT exit_reason FROM paper_trades WHERE trade_id = ?", tid) == (want,)

    rdb = journal / "recommendations.db"
    assert _one(rdb, "SELECT status FROM sentinel_trade_recommendations WHERE rec_id = 'srec_gap'") == ("VOID",)
    assert _one(rdb, "SELECT status FROM sentinel_trade_recommendations WHERE rec_id = 'srec_nopaper'") == ("VOID",)
    assert _one(rdb, "SELECT status FROM sentinel_trade_recommendations WHERE rec_id = 'srec_same'") == ("TARGET_HIT",)

    for name in ("go_efficiency.db", "paper_trades.db", "recommendations.db"):
        assert (journal / f"{name}.bak-voidxday-t").exists()

    again = r.build_plan(journal)
    assert (again.go, again.paper, again.recs) == ([], [], [])


def test_scorecard_skips_voids(journal):
    """Left: pt_same −812.5, pt_sent_same +900, pt_structure +2500 → 3 trades, +₹2,588, 66.7%."""
    import asyncio

    import web_server

    r.apply_plan(journal, r.build_plan(journal), "t")
    kpis = asyncio.run(web_server.get_scorecard(symbol="NIFTY"))["kpis"]
    assert kpis["total_trades"] == 3
    assert kpis["realized_pnl"] == "+₹2,588"
    assert kpis["win_rate"] == "66.7%"


def test_voided_go_row_drops_out_of_go_stats(journal):
    before = go_eff.summarize("NIFTY")["pnl"]
    assert (before["closed_count"], before["net_pnl_inr"]) == (2, pytest.approx(5340.7 - 812.5))
    r.apply_plan(journal, r.build_plan(journal), "t")
    after = go_eff.summarize("NIFTY")["pnl"]
    assert (after["closed_count"], after["net_pnl_inr"]) == (1, pytest.approx(-812.5))
