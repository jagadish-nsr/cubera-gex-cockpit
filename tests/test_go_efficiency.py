"""GO efficiency ledger spine tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def tmp_go_eff_db(tmp_path, monkeypatch):
    import data.journal.go_efficiency as ge
    monkeypatch.setattr(ge, "_DB_PATH", tmp_path / "go_efficiency.db")
    ge.ensure_schema()
    yield


def test_record_emit_stage_skip_and_summarize():
    from data.journal.go_efficiency import record_event, summarize

    decision = {
        "id": "d1",
        "symbol": "NIFTY",
        "mode": "directional",
        "state": "FIRE",
        "score": 82,
        "strategy_key": "CONTINUATION_BULL",
    }
    e1 = record_event("EMIT", decision)
    e2 = record_event("STAGE", decision, trade_id="pt_test")
    e3 = record_event("SKIP", {**decision, "id": "d2", "state": "FIRE"})
    assert e1.startswith("ge_")
    assert e2.startswith("ge_")
    assert e3.startswith("ge_")

    summary = summarize("NIFTY")
    assert summary["counts"]["EMIT"] == 1
    assert summary["counts"]["STAGE"] == 1
    assert summary["counts"]["SKIP"] == 1
    assert summary["emit_by_state"]["FIRE"] == 1
    assert summary["stage_rate_vs_fire_emit"] == 1.0


def test_invalid_event_type_raises():
    from data.journal.go_efficiency import record_event
    with pytest.raises(ValueError):
        record_event("BOGUS", {"id": "x", "symbol": "NIFTY", "mode": "directional", "state": "HOLD"})


def test_stage_opens_row_and_mark_updates_mfe_mae():
    from data.journal.go_efficiency import record_event, mark_open, list_open, summarize

    decision = {
        "id": "d_trail",
        "symbol": "NIFTY",
        "mode": "directional",
        "state": "FIRE",
        "score": 88,
        "strategy_key": "CONTINUATION_BULL",
        "contracts": [{"action": "BUY", "opt_type": "CE", "strike": 25500, "ltp": 72}],
        "context": {"spot": 25480},
        "risk": {"credit_or_debit": -72},
    }
    record_event("STAGE", decision, trade_id="pt_1")
    opens = list_open("NIFTY")
    assert len(opens) == 1
    assert opens[0]["entry_premium"] == -72
    assert opens[0]["mfe"] == 0

    # Favorable long below trail arm (+30%): LTP 72 → 80; pnl = 8
    upd = mark_open("d_trail", mark_premium=-80.0, mark_spot=25550)
    assert upd["mfe"] == 8.0
    assert upd["mae"] == 0.0
    assert upd["status"] == "OPEN"
    assert upd["trail_armed"] is False

    # Adverse: LTP 54 → mark −54; pnl = −18
    upd2 = mark_open("d_trail", mark_premium=-54.0)
    assert upd2["mae"] == 18.0
    assert upd2["mfe"] == 8.0
    assert upd2["status"] == "OPEN"

    summary = summarize("NIFTY")
    assert summary["open_positions"] == 1
    assert summary["avg_mfe"] == 8.0
    assert summary["avg_mae"] == 18.0


def test_sticky_trail_arms_and_stops():
    from data.journal.go_efficiency import record_event, mark_open

    decision = {
        "id": "d_arm",
        "symbol": "NIFTY",
        "mode": "directional",
        "state": "FIRE",
        "score": 90,
        "strategy_key": "CONTINUATION_BULL",
        "contracts": [{"action": "BUY", "opt_type": "CE", "strike": 25500, "ltp": 72}],
        "risk": {"credit_or_debit": -72},
        "context": {"spot": 25480},
    }
    record_event("STAGE", decision, trade_id="pt_2")

    # Arm at +30% LTP: abs 72 → 94
    armed = mark_open("d_arm", mark_premium=-94.0)
    assert armed["trail_armed"] is True
    assert armed["trail_stop"] == round(72 * 1.10, 2)
    assert armed["status"] == "OPEN"
    assert armed["mfe"] == 22.0

    # Sticky: drop below trail stop abs 78 < 79.2
    stopped = mark_open("d_arm", mark_premium=-78.0)
    assert stopped["trail_armed"] is True
    assert stopped["status"] == "TRAIL_STOP"


def test_summarize_process_and_mode_slices():
    from data.journal.go_efficiency import record_event, summarize

    decision = {
        "id": "d_proc",
        "symbol": "NIFTY",
        "mode": "structure",
        "state": "FIRE",
        "score": 80,
        "strategy_key": "IRON_CONDOR",
        "evidence": [{"factor": "corridor", "pass": True}],
        "soft_flags": ["lunch_chop"],
        "contracts": [],
        "risk": {"credit_or_debit": 40},
        "context": {"spot": 25400},
    }
    record_event("EMIT", decision)
    record_event("STAGE", decision, trade_id="pt_p")
    summary = summarize("NIFTY")
    assert summary["by_mode"]["structure"]["EMIT"] == 1
    assert summary["by_mode"]["structure"]["STAGE"] == 1
    assert summary["by_strategy_key"].get("IRON_CONDOR") == 1
    assert summary["lunch_emit_count"] == 1
    assert summary["lunch_stage_count"] == 1
    assert summary["process"]["median_emit_to_stage_sec"] is not None
    assert summary["process"]["evidence_complete_pct"] == 100.0
    assert summary["by_hour"]
    assert summary["by_dow"]
    # Hour bucket has FIRE count for the emit
    hour_vals = list(summary["by_hour"].values())
    assert any(v.get("FIRE", 0) >= 1 for v in hour_vals)
    assert any(v.get("STAGE", 0) >= 1 for v in hour_vals)


def test_settle_pnl_long_debit_hand_check():
    """Nifty lot 65, 1-leg long CE: entry −72 → exit −80.

    Gross pts = −72 − (−80) = 8
    Gross ₹  = 8 × 65 = 520
    Brokerage = 20 × 2 = 40
    Slippage  = 0.5 × 1 × 65 = 32.5  (full round-trip)
    Net ₹     = 520 − 40 − 32.5 = 447.5
    """
    from data.journal.go_efficiency import settle_pnl

    s = settle_pnl(
        entry_premium=-72.0,
        exit_premium=-80.0,
        lot_size=65,
        n_legs=1,
    )
    assert s["gross_pts"] == 8.0
    assert s["gross_pnl_inr"] == 520.0
    assert s["brokerage_cost"] == 40.0
    assert s["slippage_cost"] == 32.5
    assert s["net_pnl_inr"] == 447.5


def test_settle_pnl_short_credit_hand_check():
    """Bank Nifty lot 30, 2-leg credit: entry +40 → exit +25 (buyback cheaper).

    Gross pts = 40 − 25 = 15
    Gross ₹  = 15 × 30 = 450
    Brokerage = 40
    Slippage  = 0.5 × 2 × 30 = 30
    Net ₹     = 450 − 40 − 30 = 380
    """
    from data.journal.go_efficiency import settle_pnl

    s = settle_pnl(
        entry_premium=40.0,
        exit_premium=25.0,
        lot_size=30,
        n_legs=2,
    )
    assert s["gross_pts"] == 15.0
    assert s["gross_pnl_inr"] == 450.0
    assert s["brokerage_cost"] == 40.0
    assert s["slippage_cost"] == 30.0
    assert s["net_pnl_inr"] == 380.0


def test_close_open_settles_and_summarize_pnl():
    from data.journal.go_efficiency import (
        record_event,
        mark_open,
        close_open,
        list_open,
        summarize,
    )

    decision = {
        "id": "d_pnl",
        "symbol": "NIFTY",
        "mode": "directional",
        "state": "FIRE",
        "score": 85,
        "strategy_key": "CONTINUATION_BULL",
        "contracts": [{"action": "BUY", "opt_type": "CE", "strike": 25500, "ltp": 72}],
        "risk": {"credit_or_debit": -72},
        "context": {"spot": 25480, "lot_size": 65},
    }
    record_event("STAGE", decision, trade_id="pt_pnl")
    opens = list_open("NIFTY")
    assert opens[0]["lot_size"] == 65
    assert opens[0]["unrealized_pnl_inr"] == 0.0

    mark_open("d_pnl", mark_premium=-80.0, mark_spot=25520)
    opens = list_open("NIFTY")
    assert opens[0]["unrealized_pnl_inr"] == 520.0  # 8 pts × 65

    settled = close_open(
        "d_pnl",
        exit_premium=-80.0,
        exit_spot=25520,
        exit_reason="MANUAL",
    )
    assert settled is not None
    assert settled["gross_pnl_inr"] == 520.0
    assert settled["net_pnl_inr"] == 447.5
    assert settled["brokerage_cost"] == 40.0
    assert settled["slippage_cost"] == 32.5
    assert list_open("NIFTY") == []

    summary = summarize("NIFTY")
    assert summary["open_positions"] == 0
    assert summary["pnl"]["closed_count"] == 1
    assert summary["pnl"]["wins"] == 1
    assert summary["pnl"]["win_rate"] == 1.0
    assert summary["pnl"]["net_pnl_inr"] == 447.5
    assert summary["pnl"]["gross_pnl_inr"] == 520.0


def test_trail_stop_auto_settles_pnl():
    from data.journal.go_efficiency import record_event, mark_open, summarize

    decision = {
        "id": "d_trail_pnl",
        "symbol": "NIFTY",
        "mode": "directional",
        "state": "FIRE",
        "score": 90,
        "strategy_key": "CONTINUATION_BULL",
        "contracts": [{"action": "BUY", "opt_type": "CE", "strike": 25500, "ltp": 72}],
        "risk": {"credit_or_debit": -72},
        "context": {"spot": 25480, "lot_size": 65},
    }
    record_event("STAGE", decision, trade_id="pt_tp")
    mark_open("d_trail_pnl", mark_premium=-94.0)  # arm
    stopped = mark_open("d_trail_pnl", mark_premium=-78.0)
    assert stopped["status"] == "TRAIL_STOP"
    # entry −72 → exit −78 → gross 6 pts × 65 = 390; net 390 − 40 − 32.5 = 317.5
    assert stopped["gross_pnl_inr"] == 390.0
    assert stopped["net_pnl_inr"] == 317.5

    summary = summarize("NIFTY")
    assert summary["pnl"]["closed_count"] == 1
    assert summary["pnl"]["net_pnl_inr"] == 317.5
