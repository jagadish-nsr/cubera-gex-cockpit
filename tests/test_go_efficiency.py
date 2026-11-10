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
