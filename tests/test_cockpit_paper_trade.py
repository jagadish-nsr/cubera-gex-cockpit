"""Test 1-Click Paper Trading from Cockpit tab."""

import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from dataclasses import dataclass
from datetime import date

from app.cockpit import _execute_1click_paper_trade, _prefill_journal
import data.journal.store as journal_store
from data.journal.pnl import mark_to_market
from structures.suggest import CandidateStructure


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Redirect journal store to a fresh temp DB."""
    tmp_db_path = tmp_path / "test_cockpit_trades.db"
    monkeypatch.setattr(journal_store, "_DB_PATH", tmp_db_path)
    journal_store.ensure_schema()
    yield tmp_db_path


@dataclass
class DummyLevels:
    spot: float = 24500.0
    call_wall: float = 24800.0
    put_wall: float = 24200.0
    gamma_flip: float = 24450.0
    max_pain: float = 24500.0


@dataclass
class DummyView:
    regime: str = "calm"
    vol_regime: str = "calm"
    headline: str = "Positive gamma regime"


@dataclass
class DummyData:
    levels: DummyLevels = DummyLevels()
    view: DummyView = DummyView()
    contracts: pd.DataFrame = None
    expiries: list = None


def test_1click_paper_trade_execution_without_chain():
    """Verify 1-click execution when contracts df is missing/empty (fallback pricing)."""
    cs = CandidateStructure(
        name="Bear Call Spread",
        legs=[("SELL", "C", 24800.0), ("BUY", "C", 24900.0)],
        exit_by="Day before 15:15 IST (2026-10-08)",
    )
    data = DummyData()

    session_state = {}
    with patch("streamlit.session_state", session_state), \
         patch("streamlit.toast") as mock_toast:

        _execute_1click_paper_trade(cs, "NIFTY", data)

        mock_toast.assert_called_once()
        assert "just_opened_trade" in session_state
        trade_id = session_state["just_opened_trade"]["trade_id"]
        assert trade_id.startswith("pt_")
        assert session_state["just_opened_trade"]["strategy"] == "Bear Call Spread"
        assert session_state["just_opened_trade"]["net_premium"] > 0

        # Verify persisted in SQLite
        open_trades = journal_store.list_open_trades(symbol="NIFTY")
        assert len(open_trades) == 1
        t = open_trades[0]
        assert t.trade_id == trade_id
        assert t.strategy_name == "Bear Call Spread"
        assert len(t.legs) == 2
        assert t.legs[0]["strike"] == 24800.0
        assert t.legs[0]["action"] == "SELL"
        assert t.legs[0]["ltp"] > 0.0
        assert t.legs[1]["strike"] == 24900.0
        assert t.legs[1]["action"] == "BUY"
        assert t.legs[1]["ltp"] > 0.0

        # Mark-to-market works smoothly on this trade
        mtm = mark_to_market(t, {})
        assert "unrealized_net_inr" in mtm


def test_1click_paper_trade_execution_with_chain():
    """Verify 1-click execution with live option chain LTP extraction."""
    cs = CandidateStructure(
        name="Bull Put Spread",
        legs=[("SELL", "P", 24200.0), ("BUY", "P", 24100.0)],
        exit_by="Day before 15:15 IST",
    )
    df = pd.DataFrame([
        {"strike": 24200.0, "type": "PE", "ltp": 45.5},
        {"strike": 24100.0, "type": "PE", "ltp": 18.0},
    ])
    data = DummyData(contracts=df, expiries=[date(2026, 10, 8)])

    session_state = {}
    with patch("streamlit.session_state", session_state), \
         patch("streamlit.toast") as mock_toast:

        _execute_1click_paper_trade(cs, "NIFTY", data)

        open_trades = journal_store.list_open_trades(symbol="NIFTY")
        assert len(open_trades) == 1
        t = open_trades[0]
        assert t.strategy_name == "Bull Put Spread"
        # 45.5 - 18.0 = 27.5 net credit
        assert abs(t.entry_net_premium - 27.5) < 0.01
        assert t.legs[0]["ltp"] == 45.5
        assert t.legs[1]["ltp"] == 18.0
        assert t.legs[0]["expiry"] == "2026-10-08"


def test_prefill_journal_stages_state():
    """Verify secondary Customize button correctly stages session_state."""
    cs = CandidateStructure(
        name="Iron Condor",
        legs=[
            ("SELL", "C", 24800.0), ("BUY", "C", 24900.0),
            ("SELL", "P", 24200.0), ("BUY", "P", 24100.0),
        ],
        exit_by="Day before 15:15 IST",
    )
    data = DummyData()

    session_state = {}
    with patch("streamlit.session_state", session_state), \
         patch("streamlit.toast") as mock_toast:

        _prefill_journal(cs, "NIFTY", data)

        mock_toast.assert_called_once()
        assert session_state["prefill_strategy"] == "Iron Condor"
        assert session_state["prefill_symbol"] == "NIFTY"
        assert len(session_state["prefill_legs"]) == 4
        assert session_state["active_tab"] == "📓 Journal"


def test_record_to_trade_idea_live_option_pricing():
    """Verify that _record_to_trade_idea looks up real option chain prices instead of decaying to 0.5."""
    from app.cockpit import _record_to_trade_idea
    rec_dict = {
        "rec_id": "NIFTY_CE_22600_VOL DISPERSION",
        "symbol": "NIFTY",
        "side": "CE",
        "strike": 22600.0,
        "option_symbol": "NIFTY 22600 CE",
        "initial_spot": 22593.2,
        "initial_ltp": 18.9,
    }
    contracts_df = pd.DataFrame([
        {"strike": 22600.0, "type": "C", "mid": 23.2, "delta": 0.36, "oi": 1000, "doi": 100},
        {"strike": 22600.0, "type": "P", "mid": 115.0, "delta": -0.64, "oi": 1000, "doi": 100},
    ])
    data = DummyData(contracts=contracts_df)

    idea = _record_to_trade_idea(rec_dict, current_spot=22462.85, data=data)
    # The real price in the chain is 23.2, not clamped to 0.5
    assert idea.current_ltp == 23.2
    assert idea.delta_approx == 0.36

