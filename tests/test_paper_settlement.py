"""Paper settlement after the session: hand-checked against the 26-Nov orphan rows."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

import data.journal.go_efficiency as ge
import data.journal.recommendations_store as rs
import data.journal.store as paper
from data.journal import paper_settlement as ps

AFTER_CLOSE = datetime.fromisoformat("2026-11-26T15:45:00+05:30")


@pytest.fixture(autouse=True)
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(ge, "_DB_PATH", tmp_path / "go_efficiency.db")
    monkeypatch.setattr(paper, "_DB_PATH", tmp_path / "paper.db")
    ge.ensure_schema()
    paper.ensure_schema()
    ps._WARNED.clear()
    return tmp_path


def _trade(trade_id, entry_ts, legs, premium, lot, exit_by=""):
    paper._write_trade(paper.PaperTrade(
        trade_id=trade_id, symbol="SENSEX" if lot == 20 else "NIFTY", strategy_name="t", status="OPEN",
        entry_ts=entry_ts, exit_ts=None, legs=legs, entry_spot=0.0, entry_levels={}, entry_regime={},
        entry_net_premium=premium, exit_net_premium=None, gross_pnl=None, brokerage_cost=20.0,
        slippage_cost=0.0, net_realized_pnl=None, exit_reason=None, notes="", lot_size=lot,
        exit_by=exit_by,
    ))


def _go_row(trade_id, status, exit_premium=None, closed_at=None):
    con = sqlite3.connect(ge._DB_PATH)
    con.execute(
        "INSERT INTO go_open (decision_id, trade_id, symbol, mode, entry_premium, status, opened_at, "
        "updated_at, decision_json, exit_premium, exit_reason, closed_at) "
        "VALUES (?, ?, 'NIFTY', 'directional', -162.7, ?, '2026-11-13T10:13:49+05:30', 'x', '{}', ?, ?, ?)",
        (f"d_{trade_id}", trade_id, status, exit_premium, status if exit_premium else None, closed_at))
    con.commit()
    con.close()


def _rec(rec_id, paper_id, current_ltp, updated_at):
    con = sqlite3.connect(rs._DB_PATH)
    con.execute(
        "INSERT INTO sentinel_trade_recommendations (rec_id, symbol, strike, side, option_symbol, "
        "triggered_at, status, spot_entry, spot_target, spot_invalidation, option_entry, option_target, "
        "option_sl, current_ltp, paper_trade_id, contributing_signals_json, updated_at) "
        "VALUES (?, 'SENSEX', 84400, 'CE', '', ?, 'EXPIRED', 0, 0, 0, 102.9, 0, 0, ?, ?, '[]', ?)",
        (rec_id, updated_at, current_ltp, paper_id, updated_at))
    con.commit()
    con.close()


def _close_snapshot(journal, spot, as_of_utc):
    con = sqlite3.connect(journal / "market_snapshot.db")
    con.execute("CREATE TABLE session_snapshots (symbol TEXT PRIMARY KEY, spot REAL, as_of TEXT)")
    con.execute("INSERT INTO session_snapshots VALUES ('SENSEX', ?, ?)", (spot, as_of_utc))
    con.commit()
    con.close()


SX_CE = [{"action": "BUY", "type": "CE", "strike": 84400.0, "ltp": 102.9, "expiry": "26NOV2026"}]
NF_C = [{"action": "BUY", "type": "C", "strike": 25200.0, "ltp": 162.7}]


def test_go_mirror_settles_missed_linked_close():
    # 13-Nov auto-stage: go_open hit TRAIL_STOP at -178.97; the paper row was never closed.
    _trade("pt_nf", "2026-11-13T10:13:49+05:30", NF_C, -162.7, 65)
    _go_row("pt_nf", "TRAIL_STOP", -178.97, "2026-11-13T13:18:07+05:30")
    got = ps.settle_open_paper(now=AFTER_CLOSE)
    assert got[0]["rule"] == "GO_MIRROR"
    t = paper.get_trade("pt_nf")
    assert t.status == "CLOSED" and t.exit_reason == "TRAIL_STOP"
    assert t.exit_ts == "2026-11-13T13:18:07+05:30"
    assert t.gross_pnl == pytest.approx((178.97 - 162.7) * 65)       # 1,057.55


def test_go_still_open_is_left_to_go_sweep():
    _trade("pt_nf", "2026-11-13T10:13:49+05:30", NF_C, -162.7, 65)
    _go_row("pt_nf", "OPEN")
    assert ps.settle_open_paper(now=AFTER_CLOSE) == []
    assert paper.get_trade("pt_nf").status == "OPEN"


def test_sentinel_same_second_twin_is_voided():
    legs = [{"action": "BUY", "type": "CE", "strike": 81500.0, "ltp": 724.48, "expiry": "19NOV2026"}]
    _trade("pt_dup", "2026-11-13T09:17:14+05:30", legs, -724.48, 20, "SENTINEL_ENGINE")
    _trade("pt_real", "2026-11-13T09:17:14+05:30", legs, -724.48, 20, "SENTINEL_ENGINE")
    paper.close_trade("pt_real", -669.5, exit_reason="SPOT_INVAL")
    _rec("srec_20261113_SENSEX_81500_CE", "pt_real", 669.5, "2026-11-13 09:25:29")
    got = ps.settle_open_paper(now=AFTER_CLOSE)
    assert got == [{"trade_id": "pt_dup", "rule": "VOID_DUPLICATE", "twin": "pt_real"}]
    t = paper.get_trade("pt_dup")
    assert (t.status, t.exit_reason, t.net_realized_pnl, t.brokerage_cost) == ("CLOSED", "VOID_DUPLICATE", 0, 0)


def test_sentinel_zero_dte_settles_at_intrinsic_on_close(journal):
    # 26-Nov 14:46 SX 84400CE at 102.9, expiry same day. Close 84,328.59 → CE worth 0.
    _trade("pt_sx", "2026-11-26T14:46:14+05:30", SX_CE, -102.9, 20, "SENTINEL_ENGINE")
    _close_snapshot(journal, 84328.59, "2026-11-26T10:09:50+00:00")      # 15:39:50 IST
    got = ps.settle_open_paper(now=AFTER_CLOSE, journal_dir=journal)
    assert got[0]["rule"] == "EXPIRY_SETTLE" and got[0]["exit"] == 0.0
    t = paper.get_trade("pt_sx")
    assert t.gross_pnl == pytest.approx(-102.9 * 20)                       # -2,058
    assert t.exit_ts == "2026-11-26T15:30:00+05:30"


def test_intrinsic_put_and_short_leg_by_hand():
    put = SimpleNamespace(legs=[{"action": "BUY", "type": "PE", "strike": 84400.0}])
    assert ps._intrinsic_exit(put, 84328.59) == pytest.approx(-71.41)
    short_call = SimpleNamespace(legs=[{"action": "SELL", "type": "CE", "strike": 84000.0}])
    assert ps._intrinsic_exit(short_call, 84328.59) == pytest.approx(328.59)


def test_sentinel_without_close_uses_entry_day_mark():
    legs = [{"action": "BUY", "type": "CE", "strike": 84400.0, "ltp": 102.9, "expiry": "04DEC2026"}]
    _trade("pt_sx", "2026-11-26T14:46:14+05:30", legs, -102.9, 20, "SENTINEL_ENGINE")
    _rec("srec_20261126_SENSEX_84400_CE", "pt_sx", 61.25, "2026-11-26 15:40:14")
    got = ps.settle_open_paper(now=AFTER_CLOSE)
    assert got[0]["rule"] == "SESSION_END"
    t = paper.get_trade("pt_sx")
    assert t.exit_net_premium == -61.25
    assert t.gross_pnl == pytest.approx((61.25 - 102.9) * 20)


def test_twin_must_share_the_entry_second():
    # 09:46:13 BBFB/DBCA twins; 10:22:07 is a separate trade that later took the rec link.
    legs = [{"action": "BUY", "type": "CE", "strike": 81700.0, "ltp": 698.02, "expiry": "19NOV2026"}]
    _trade("pt_dbca", "2026-11-13T09:46:13+05:30", legs, -698.02, 20, "SENTINEL_ENGINE")
    _trade("pt_bbfb", "2026-11-13T09:46:13+05:30", legs, -698.02, 20, "SENTINEL_ENGINE")
    _trade("pt_3775", "2026-11-13T10:22:07+05:30", legs, -717.75, 20, "SENTINEL_ENGINE")
    paper.close_trade("pt_bbfb", -742.45, exit_reason="TARGET")
    paper.close_trade("pt_3775", -742.45, exit_reason="TARGET")
    _rec("srec_20261113_SENSEX_81700_CE", "pt_3775", 742.45, "2026-11-13 10:34:19")
    got = ps.settle_open_paper(now=AFTER_CLOSE)
    assert got == [{"trade_id": "pt_dbca", "rule": "VOID_DUPLICATE", "twin": "pt_bbfb"}]


def test_sentinel_from_earlier_day_without_any_price_is_voided():
    legs = [{"action": "BUY", "type": "CE", "strike": 81500.0, "ltp": 724.48, "expiry": "19NOV2026"}]
    _trade("pt_lone", "2026-11-13T09:17:14+05:30", legs, -724.48, 20, "SENTINEL_ENGINE")
    got = ps.settle_open_paper(now=AFTER_CLOSE)
    assert got == [{"trade_id": "pt_lone", "rule": "VOID_NO_MARK"}]


def test_nothing_settles_before_the_close():
    _trade("pt_sx", "2026-11-26T14:46:14+05:30", SX_CE, -102.9, 20, "SENTINEL_ENGINE")
    assert ps.settle_open_paper(now=datetime.fromisoformat("2026-11-26T15:20:00+05:30")) == []
    assert paper.get_trade("pt_sx").status == "OPEN"


def test_manual_undated_trade_stays_open():
    _trade("pt_manual", "2026-11-20T10:00:00+05:30", NF_C, -162.7, 65)
    assert ps.settle_open_paper(now=AFTER_CLOSE) == []
    assert paper.get_trade("pt_manual").status == "OPEN"


def test_manual_trade_settles_once_expired(journal):
    legs = [{"action": "BUY", "type": "PE", "strike": 84400.0, "ltp": 90.0, "expiry": "2026-11-26"}]
    _trade("pt_manual", "2026-11-24T10:00:00+05:30", legs, -90.0, 20)
    _close_snapshot(journal, 84328.59, "2026-11-26T10:09:50+00:00")
    got = ps.settle_open_paper(now=AFTER_CLOSE, journal_dir=journal)
    assert got[0]["exit"] == pytest.approx(-71.41)
    assert paper.get_trade("pt_manual").gross_pnl == pytest.approx((71.41 - 90.0) * 20)


def test_expiry_close_timeseries_fallback_takes_first_print_after_1530(journal):
    d = journal / "timeseries" / "SENSEX"
    d.mkdir(parents=True)
    (d / "2026-11-19.csv").write_text(
        "time_ist,spot\n15:17,82960.86\n15:40,83021.75\n15:45,83030.00\n")
    assert ps.expiry_close("SENSEX", "2026-11-19", journal) == (83021.75, "timeseries 15:40")
    assert ps.expiry_close("SENSEX", "2026-11-20", journal) is None


def test_stale_snapshot_from_another_day_is_not_a_close(journal):
    _close_snapshot(journal, 84328.59, "2026-11-25T10:09:50+00:00")
    assert ps.expiry_close("SENSEX", "2026-11-26", journal) is None


def test_parse_expiry_formats():
    assert ps.parse_expiry("26NOV2026") == "2026-11-26"
    assert ps.parse_expiry("2026-11-26") == "2026-11-26"
    assert ps.parse_expiry("26-Nov-2026") == "2026-11-26"
    assert ps.parse_expiry("") is None and ps.parse_expiry("soon") is None


def test_sentinel_open_does_not_duplicate_same_day():
    import analytics.sentinel_recommendations as sr
    sig = SimpleNamespace(type="CONTINUATION_BULL", bias="LONG", event_id="e1", score=80, rvol_tod=1.2)
    kw = dict(symbol="SENSEX", side="CE", strike=84400.0, entry_ltp=102.9, expiry_tag="26NOV2026",
              spot=84450.0, spot_target=84600.0, spot_inval=84300.0, opt_target=150.0, opt_sl=70.0,
              signal=sig, lot_size=20, force=True)
    first = sr._open_or_skip_paper(**kw)
    second = sr._open_or_skip_paper(**kw)
    assert first and second == first
    assert len(paper.list_open_trades("SENSEX")) == 1
