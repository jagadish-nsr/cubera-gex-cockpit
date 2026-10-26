"""Tests for the paper-trade journal (store + pnl + scorecard).

All tests run fully offline — no network, no broker, no real money.
The DB is always a temporary file, never the real paper_trades.db.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Patch the DB path to a temp file for every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Redirect the journal store to a fresh temp DB per test."""
    import data.journal.store as store_mod
    tmp_db_path = tmp_path / "test_paper_trades.db"
    monkeypatch.setattr(store_mod, "_DB_PATH", tmp_db_path)
    store_mod.ensure_schema()
    yield tmp_db_path


# ---------------------------------------------------------------------------
# Minimal fixtures
# ---------------------------------------------------------------------------

_LEGS_CREDIT = [
    {"action": "SELL", "type": "C", "strike": 24700.0, "ltp": 48.0, "expiry": "2026-10-03"},
    {"action": "BUY",  "type": "C", "strike": 24800.0, "ltp": 18.0, "expiry": "2026-10-03"},
]

_LEGS_DEBIT = [
    {"action": "BUY",  "type": "P", "strike": 24400.0, "ltp": 60.0, "expiry": "2026-10-03"},
    {"action": "SELL", "type": "P", "strike": 24300.0, "ltp": 25.0, "expiry": "2026-10-03"},
]

_LEVELS_SNAP = {
    "spot": 24550.0, "call_wall": 24700.0, "put_wall": 24400.0,
    "flip": 24450.0, "max_pain": 24500.0,
}

_REGIME_SNAP = {"regime": "calm", "vol_regime": "calm", "headline": "Range-bound"}

LOT = 65


# ---------------------------------------------------------------------------
# store.py — open_trade
# ---------------------------------------------------------------------------

def test_open_trade_creates_open_row():
    from data.journal.store import open_trade, list_open_trades

    entry_premium = 30.0  # SELL 48 - BUY 18 = 30 credit
    t = open_trade(
        symbol="NIFTY",
        strategy_name="Bear Call Spread",
        legs=_LEGS_CREDIT,
        entry_spot=24550.0,
        entry_levels=_LEVELS_SNAP,
        entry_regime=_REGIME_SNAP,
        entry_net_premium=entry_premium,
        lot_size=LOT,
    )

    assert t.status == "OPEN"
    assert t.trade_id.startswith("pt_")
    assert t.symbol == "NIFTY"
    assert t.entry_net_premium == 30.0
    assert t.net_realized_pnl is None

    open_trades = list_open_trades()
    assert len(open_trades) == 1
    assert open_trades[0].trade_id == t.trade_id


def test_open_trade_preserves_immutable_snapshot():
    """The entry_levels and entry_regime stored in the DB must NOT change
    if the dict is mutated by the caller after logging."""
    from data.journal.store import open_trade, get_trade

    levels = {"spot": 24550.0, "call_wall": 24700.0}
    t = open_trade("NIFTY", "Bear Call Spread", _LEGS_CREDIT,
                   24550.0, levels, _REGIME_SNAP, 30.0, LOT)

    # Mutate the original dict — stored value must be unaffected
    levels["spot"] = 99999.0

    retrieved = get_trade(t.trade_id)
    assert retrieved.entry_levels["spot"] == 24550.0, (
        "Point-in-time snapshot must be immutable in storage"
    )


# ---------------------------------------------------------------------------
# store.py — close_trade
# ---------------------------------------------------------------------------

def test_close_credit_spread_profit():
    """Close a credit spread at a lower premium = profit."""
    from data.journal.store import open_trade, close_trade

    t = open_trade("NIFTY", "Bear Call Spread", _LEGS_CREDIT,
                   24550.0, _LEVELS_SNAP, _REGIME_SNAP, 30.0, LOT)
    closed = close_trade(t.trade_id, exit_net_premium=8.0, exit_reason="TARGET")

    # gross P&L = (30 - 8) × 65 = 1,430
    assert closed.gross_pnl == pytest.approx(1430.0)
    assert closed.status == "CLOSED"
    assert closed.exit_reason == "TARGET"
    # Net must be less than gross (costs subtracted)
    assert closed.net_realized_pnl < closed.gross_pnl


def test_close_credit_spread_loss():
    """Close a credit spread at a higher premium = loss."""
    from data.journal.store import open_trade, close_trade

    t = open_trade("NIFTY", "Bear Call Spread", _LEGS_CREDIT,
                   24550.0, _LEVELS_SNAP, _REGIME_SNAP, 30.0, LOT)
    closed = close_trade(t.trade_id, exit_net_premium=55.0, exit_reason="STOP")

    # gross P&L = (30 - 55) × 65 = -1,625  (loss)
    assert closed.gross_pnl == pytest.approx(-1625.0)
    assert closed.net_realized_pnl < closed.gross_pnl  # costs make it worse


def test_close_debit_spread_profit():
    """Close a debit spread (BUY put spread) at a higher value = profit."""
    from data.journal.store import open_trade, close_trade

    entry_debit = -(60.0 - 25.0)  # paid 35 net → -35 in our convention
    t = open_trade("NIFTY", "Bull Put Spread", _LEGS_DEBIT,
                   24550.0, _LEVELS_SNAP, _REGIME_SNAP, entry_debit, LOT)
    # Exit when the spread is worth 60 (value doubled)
    exit_debit = -(60.0 - 25.0) * 2  # -70 exit (current net value)
    closed = close_trade(t.trade_id, exit_net_premium=exit_debit)

    # gross = (entry - exit) × lot = (-35 - (-70)) × 65 = 35 × 65 = 2,275
    assert closed.gross_pnl == pytest.approx(2275.0)


def test_close_already_closed_returns_trade_unchanged():
    from data.journal.store import open_trade, close_trade

    t = open_trade("NIFTY", "Bear Call Spread", _LEGS_CREDIT,
                   24550.0, _LEVELS_SNAP, _REGIME_SNAP, 30.0, LOT)
    close_trade(t.trade_id, exit_net_premium=8.0)
    result = close_trade(t.trade_id, exit_net_premium=5.0)  # second close attempt
    # Should return the already-closed trade with original values
    assert result.exit_net_premium == 8.0
    assert result.status == "CLOSED"


def test_list_open_and_closed_separation():
    from data.journal.store import open_trade, close_trade, list_open_trades, list_closed_trades

    t1 = open_trade("NIFTY", "Iron Condor", _LEGS_CREDIT,
                    24550.0, _LEVELS_SNAP, _REGIME_SNAP, 30.0, LOT)
    t2 = open_trade("NIFTY", "Bear Call Spread", _LEGS_CREDIT,
                    24550.0, _LEVELS_SNAP, _REGIME_SNAP, 30.0, LOT)
    close_trade(t1.trade_id, exit_net_premium=10.0)

    open_trades = list_open_trades()
    closed_trades = list_closed_trades()

    open_ids = {t.trade_id for t in open_trades}
    closed_ids = {t.trade_id for t in closed_trades}

    assert t1.trade_id not in open_ids
    assert t1.trade_id in closed_ids
    assert t2.trade_id in open_ids
    assert t2.trade_id not in closed_ids


def test_symbol_filter():
    from data.journal.store import open_trade, list_open_trades

    open_trade("NIFTY", "Bear Call Spread", _LEGS_CREDIT,
               24550.0, _LEVELS_SNAP, _REGIME_SNAP, 30.0, LOT)
    open_trade("SENSEX", "Bear Call Spread", _LEGS_CREDIT,
               80540.0, _LEVELS_SNAP, _REGIME_SNAP, 95.0, 20)

    nifty = list_open_trades(symbol="NIFTY")
    sensex = list_open_trades(symbol="SENSEX")

    assert all(t.symbol == "NIFTY" for t in nifty)
    assert all(t.symbol == "SENSEX" for t in sensex)
    assert len(nifty) == 1
    assert len(sensex) == 1


# ---------------------------------------------------------------------------
# pnl.py — mark_to_market
# ---------------------------------------------------------------------------

def test_mtm_credit_spread_winning():
    from data.journal.store import open_trade
    from data.journal.pnl import mark_to_market

    t = open_trade("NIFTY", "Bear Call Spread", _LEGS_CREDIT,
                   24550.0, _LEVELS_SNAP, _REGIME_SNAP, 30.0, LOT)

    # Current prices: SELL leg now worth 15, BUY leg now worth 5
    current_prices = {
        ("C", 24700.0, "2026-10-03"): 15.0,
        ("C", 24800.0, "2026-10-03"): 5.0,
    }
    mtm = mark_to_market(t, current_prices)

    # current_net_premium = 15 (sell side cost to close) - 5 (buy side worth) = 10
    # unrealized_gross_inr = (30 - 10) × 65 = 1,300
    assert mtm["current_net_premium"] == pytest.approx(10.0)
    assert mtm["unrealized_gross_inr"] == pytest.approx(1300.0)
    assert mtm["stale_legs"] == []


def test_mtm_falls_back_to_entry_ltp_for_missing_prices():
    from data.journal.store import open_trade
    from data.journal.pnl import mark_to_market

    t = open_trade("NIFTY", "Bear Call Spread", _LEGS_CREDIT,
                   24550.0, _LEVELS_SNAP, _REGIME_SNAP, 30.0, LOT)

    # No prices provided — should use entry LTP fallback and flag stale legs
    mtm = mark_to_market(t, {})
    assert len(mtm["stale_legs"]) == 2  # both legs missing


# ---------------------------------------------------------------------------
# scorecard.py
# ---------------------------------------------------------------------------

def test_scorecard_win_rate_and_pnl():
    from data.journal.store import open_trade, close_trade, list_closed_trades
    from data.journal.scorecard import compute_scorecard

    # 2 wins, 1 loss
    for _ in range(2):
        t = open_trade("NIFTY", "Bear Call Spread", _LEGS_CREDIT,
                       24550.0, _LEVELS_SNAP, _REGIME_SNAP, 30.0, LOT)
        close_trade(t.trade_id, exit_net_premium=8.0)   # profit

    t_loss = open_trade("NIFTY", "Bear Call Spread", _LEGS_CREDIT,
                        24550.0, _LEVELS_SNAP, _REGIME_SNAP, 30.0, LOT)
    close_trade(t_loss.trade_id, exit_net_premium=60.0)  # loss

    closed = list_closed_trades()
    sc = compute_scorecard(closed)

    assert sc["total_trades"] == 3
    assert sc["win_rate"] == pytest.approx(66.7, abs=0.1)
    assert sc["total_net_pnl"] < 0 or sc["total_net_pnl"] > 0  # just ensure it's computed
    assert "equity_curve" in sc
    assert len(sc["equity_curve"]) == 3


def test_scorecard_by_regime():
    from data.journal.store import open_trade, close_trade, list_closed_trades
    from data.journal.scorecard import compute_scorecard

    calm_regime = {"regime": "calm"}
    wild_regime = {"regime": "wild"}

    t1 = open_trade("NIFTY", "Iron Condor", _LEGS_CREDIT,
                    24550.0, _LEVELS_SNAP, calm_regime, 30.0, LOT)
    close_trade(t1.trade_id, exit_net_premium=5.0)  # calm win

    t2 = open_trade("NIFTY", "Long Strangle", _LEGS_DEBIT,
                    24550.0, _LEVELS_SNAP, wild_regime, -35.0, LOT)
    close_trade(t2.trade_id, exit_net_premium=-60.0)  # wild win

    sc = compute_scorecard(list_closed_trades())
    assert "calm" in sc["by_regime"]
    assert "wild" in sc["by_regime"]
    assert sc["by_regime"]["calm"]["n"] == 1
    assert sc["by_regime"]["wild"]["n"] == 1


def test_empty_scorecard():
    from data.journal.scorecard import compute_scorecard

    sc = compute_scorecard([])
    assert sc["total_trades"] == 0
    assert sc["win_rate"] == 0.0
    assert sc["equity_curve"] == []


def test_open_trade_exit_by_and_custom_costs():
    """Verify that exit_by and configurable costs are persisted and retrieved correctly."""
    from data.journal.store import open_trade, get_trade

    trade = open_trade(
        symbol="NIFTY",
        strategy_name="Iron Condor",
        legs=_LEGS_CREDIT,
        entry_spot=24550.0,
        entry_levels=_LEVELS_SNAP,
        entry_regime=_REGIME_SNAP,
        entry_net_premium=30.0,
        lot_size=LOT,
        brokerage_per_order=35.0,
        slippage_pts=1.0,
        exit_by="Thursday 15:00",
    )
    assert trade.exit_by == "Thursday 15:00"
    assert trade.brokerage_cost == 35.0
    # Entry-side slippage = 1.0 * 2 legs * 65 lot / 2 = 65.0
    assert trade.slippage_cost == 65.0

    retrieved = get_trade(trade.trade_id)
    assert retrieved is not None
    assert retrieved.exit_by == "Thursday 15:00"
    assert retrieved.brokerage_cost == 35.0
    assert retrieved.slippage_cost == 65.0
