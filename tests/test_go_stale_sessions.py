"""17-Nov 09:04: three 13-Nov GO rows were trail-stopped off Monday's pre-open book.

One was staged at 17:50 on 13-Nov; none was closed by 13-Nov's session-end sweep. Marks are now
refused outside the continuous session and on rows from an earlier day, and earlier-day rows are
closed at their last in-session mark, stamped at that day's cash close.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(autouse=True)
def _dbs(tmp_path, monkeypatch):
    import data.journal.go_efficiency as ge
    import data.journal.store as paper

    monkeypatch.setattr(ge, "_DB_PATH", tmp_path / "go_efficiency.db")
    monkeypatch.setattr(paper, "_DB_PATH", tmp_path / "paper.db")
    monkeypatch.setattr("config.is_market_open", lambda *a, **k: True)
    ge._LAST_EMIT_FP.clear()
    ge.ensure_schema()
    paper.ensure_schema()
    yield ge


def _stage(ge, decision_id, trade_id=None, ltp=139.1):
    ge.record_event("STAGE", {
        "id": decision_id, "symbol": "NIFTY", "mode": "directional", "state": "FIRE",
        "score": 85, "strategy_key": "MOMENTUM",
        "contracts": [{"action": "BUY", "opt_type": "CE", "strike": 25200, "ltp": ltp}],
        "risk": {"credit_or_debit": -ltp},
        "context": {"spot": 25139.25, "lot_size": 65},
    }, trade_id=trade_id)


def _set(ge, decision_id, **cols):
    conn = sqlite3.connect(ge._DB_PATH)
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(f"UPDATE go_open SET {sets} WHERE decision_id = ?", (*cols.values(), decision_id))
    conn.commit()
    conn.close()


def _row(ge, decision_id):
    conn = sqlite3.connect(ge._DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM go_open WHERE decision_id = ?", (decision_id,)).fetchone())
    finally:
        conn.close()


def test_pre_open_mark_is_refused(_dbs, monkeypatch):
    ge = _dbs
    _stage(ge, "d_today")
    monkeypatch.setattr("config.is_market_open", lambda *a, **k: False)
    assert ge.mark_open("d_today", mark_premium=-222.38, mark_spot=25353.45) is None
    row = _row(ge, "d_today")
    assert row["status"] == "OPEN"
    assert row["mark_premium"] != -222.38


def test_earlier_session_row_is_not_marked(_dbs):
    ge = _dbs
    _stage(ge, "d_old")
    _set(ge, "d_old", opened_at="2026-11-13T15:23:33+05:30")
    assert ge.mark_open("d_old", mark_premium=-222.38) is None
    assert _row(ge, "d_old")["status"] == "OPEN"


def test_stale_rows_close_at_last_mark_stamped_at_their_close(_dbs):
    import data.journal.store as paper

    ge = _dbs
    trade = paper.open_trade(
        symbol="NIFTY", strategy_name="MOMENTUM",
        legs=[{"action": "BUY", "type": "C", "strike": 25200.0, "ltp": 139.1}],
        entry_spot=25139.25, entry_levels={}, entry_regime={},
        entry_net_premium=-139.1, lot_size=65,
    )
    _stage(ge, "d_old", trade_id=trade.trade_id)
    _set(ge, "d_old", opened_at="2026-11-13T15:23:33+05:30", mark_premium=-150.0)
    _stage(ge, "d_today", ltp=90.0)

    monday_preopen = datetime(2026, 11, 17, 9, 4, 17, tzinfo=IST)
    closed = ge.close_stale_open(now=monday_preopen)

    assert [c["decision_id"] for c in closed] == ["d_old"]
    old = _row(ge, "d_old")
    assert old["status"] == "CLOSED" and old["exit_reason"] == "SESSION_END"
    assert old["exit_premium"] == -150.0
    assert old["closed_at"] == "2026-11-13T15:30:00+05:30"
    # (entry − exit) × lot = (−139.1 − (−150.0)) × 65 = +708.50 gross
    assert old["gross_pnl_inr"] == pytest.approx(708.5)
    assert _row(ge, "d_today")["status"] == "OPEN"
    pt = paper.get_trade(trade.trade_id)
    assert pt.status != "OPEN"
    assert str(pt.exit_ts).startswith("2026-11-13T15:30")
    assert ge.close_stale_open(now=monday_preopen) == []          # idempotent


def test_housekeeping_tick_runs_go_then_recs_then_paper(monkeypatch):
    import data.pre_post_market as ppm

    calls = []
    monkeypatch.setattr("data.journal.go_efficiency.close_stale_open",
                        lambda now=None: calls.append("go") or [{"decision_id": "x"}])
    monkeypatch.setattr("data.journal.recommendations_store.expire_stale_active",
                        lambda now=None: calls.append("recs") or 2)
    monkeypatch.setattr("data.journal.paper_settlement.settle_open_paper",
                        lambda now=None: calls.append("paper") or [])
    out = ppm.session_housekeeping_tick(datetime(2026, 11, 22, 10, 0, tzinfo=IST))   # a Sunday
    assert calls == ["go", "recs", "paper"]
    assert out == {"go": 1, "recs": 2, "paper": 0}
