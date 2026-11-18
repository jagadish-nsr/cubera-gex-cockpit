"""Hand-checkable premarket path ledger + efficiency score."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

import pytest

from analytics.premarket_path import (
    IST,
    append_path_event,
    ensure_path_schema,
    list_path_events,
    regime_path_stats,
    score_path,
    seal_discovery,
    thesis_state_at,
)
from data import pre_post_market as ppm


@pytest.fixture()
def path_db(tmp_path, monkeypatch):
    db = tmp_path / "market_snapshot.db"
    monkeypatch.setattr(ppm, "DB_PATH", db)
    ppm._PRE_MARKET_CACHE.clear()
    ensure_path_schema()
    ppm.init_pre_post_db()
    yield db
    ppm._PRE_MARKET_CACHE.clear()


def _seed_auction(sym: str, date: str, **kw):
    state = {
        "date": date,
        "is_discovered": True,
        "discovered_spot": kw.get("spot", 25210.0),
        "prev_close": kw.get("prev", 25299.0),
        "gap_pts": kw.get("gap_pts", -89.0),
        "gap_pct": kw.get("gap_pct", -0.35),
        "gap_regime": kw.get("regime", "MODERATE_DRIFT"),
        "favored_play": kw.get("favored", "MAP ONLY"),
        "gap_fill_status": kw.get("gap_fill", "UNFILLED"),
        "call_wall": kw.get("cw", 25500.0),
        "put_wall": kw.get("pw", 25000.0),
        "gamma_flip": kw.get("flip", 25140.0),
        "regime_badge": "test",
        "regime_briefing": "test",
        "discovered_at": "09:12:00",
        "open_source": "spot-open",
    }
    watch = {
        "status": "STAGED",
        "regime": state["gap_regime"],
        "map_strip": {
            "open": state["discovered_spot"],
            "call_wall": state["call_wall"],
            "put_wall": state["put_wall"],
            "gamma_flip": state["gamma_flip"],
            "prev_close": state["prev_close"],
            "gap_pts": state["gap_pts"],
            "gap_pct": state["gap_pct"],
        },
        "structure": None,
    }
    ppm.save_pre_market_state(sym, state)
    ppm.save_opening_watch(sym, watch, trade_date=date)
    return state


def test_thesis_moderate_drift_map_ok():
    assert thesis_state_at(
        "MODERATE_DRIFT",
        open_px=25210.0, spot=25144.0, cw=25500.0, pw=25000.0,
        flip=25140.0, prev=25299.0, gap_fill="FILLED",
    ) == "MAP_OK"


def test_thesis_call_wall_hold_vs_fail():
    hold = thesis_state_at(
        "CALL_WALL_BREACH_SQUEEZE",
        open_px=22720.0, spot=22750.0, cw=22700.0, pw=22400.0,
        flip=22500.0, prev=22570.0, gap_fill="UNFILLED",
    )
    fail = thesis_state_at(
        "CALL_WALL_BREACH_SQUEEZE",
        open_px=22720.0, spot=22650.0, cw=22700.0, pw=22400.0,
        flip=22500.0, prev=22570.0, gap_fill="UNFILLED",
    )
    assert hold == "HOLDING"
    assert fail == "INVALIDATED"


def test_thesis_no_fade_breach_when_gap_fills_through_prior():
    ok = thesis_state_at(
        "GAP_UP_INTO_POS_GAMMA",
        open_px=24500.0, spot=24480.0, cw=24700.0, pw=24300.0,
        flip=24400.0, prev=24380.0, gap_fill="UNFILLED",
    )
    breach = thesis_state_at(
        "GAP_UP_INTO_POS_GAMMA",
        open_px=24500.0, spot=24400.0, cw=24700.0, pw=24300.0,
        flip=24400.0, prev=24380.0, gap_fill="FILLED",
    )
    assert ok == "MAP_OK"
    assert breach == "MAP_BREACH"


def test_seal_and_idempotent_checkpoints(path_db):
    date = "2026-11-13"
    state = _seed_auction("NIFTY", date)
    e1 = seal_discovery("NIFTY", state)
    assert e1 is not None
    assert e1["checkpoint"] == "DISCOVERY"
    e2 = seal_discovery("NIFTY", state)
    assert e2 is None  # idempotent
    a = append_path_event("NIFTY", "T0915", 25200.0, trade_date=date)
    b = append_path_event("NIFTY", "T0915", 25205.0, trade_date=date)
    assert a is not None
    assert b is None
    evs = list_path_events("NIFTY", date)
    assert [e["checkpoint"] for e in evs] == ["DISCOVERY", "T0915"]


def test_score_path_moderate_drift(path_db):
    date = "2026-11-13"
    state = _seed_auction("NIFTY", date, regime="MODERATE_DRIFT", favored="MAP ONLY")
    seal_discovery("NIFTY", state)
    append_path_event("NIFTY", "T0915", 25200.0, trade_date=date)
    append_path_event("NIFTY", "T1000", 25150.0, trade_date=date, gap_fill="FILLED")
    # settle print on auction row
    ppm.save_post_market_state("NIFTY", {
        "date": date,
        "settlement_spot": 25144.0,
        "last_continuous_spot": 25144.0,
        "settlement_divergence_pts": 0.0,
        "pin_efficiency_pct": 10.0,
        "pin_target_wall": "25,000 put-gamma",
        "settled_at": "15:40:00",
        "status_text": "settled",
        "gap_fill_status": "FILLED",
    })
    # save_post_market_state already calls score_path; re-read
    rec = ppm.get_persisted_auction_record("NIFTY", date)
    assert rec is not None
    assert rec.get("thesis_outcome") == "MAP_OK"
    assert rec.get("path_efficiency_pct") is not None
    assert float(rec["path_efficiency_pct"]) >= 50.0
    detail = json.loads(rec["path_score_detail_json"])
    assert detail["thesis_component"] == 100.0


def test_score_call_wall_fail_low_eff(path_db):
    date = "2026-11-10"
    state = _seed_auction(
        "NIFTY", date,
        spot=22720.0, prev=22570.0, gap_pts=150.0, gap_pct=0.66,
        regime="CALL_WALL_BREACH_SQUEEZE", favored="WATCH HOLD ABOVE CALL STRIKE",
        cw=22700.0, pw=22400.0, flip=22550.0,
    )
    seal_discovery("NIFTY", state)
    append_path_event("NIFTY", "T0915", 22740.0, trade_date=date)
    append_path_event("NIFTY", "T1000", 22640.0, trade_date=date)  # below CW
    ppm.save_post_market_state("NIFTY", {
        "date": date,
        "settlement_spot": 22620.0,
        "last_continuous_spot": 22620.0,
        "settlement_divergence_pts": 0.0,
        "pin_efficiency_pct": 0.0,
        "pin_target_wall": "x",
        "settled_at": "15:40:00",
        "status_text": "settled",
    })
    rec = ppm.get_persisted_auction_record("NIFTY", date)
    assert rec["thesis_outcome"] == "FAIL"
    assert float(rec["path_efficiency_pct"]) < 50.0


def test_regime_path_stats_and_watch_demotion(path_db, monkeypatch):
    # Seed 8 failed CALL_WALL paths
    for i in range(8):
        d = f"2026-10-{10 + i:02d}"
        st = _seed_auction(
            "NIFTY", d,
            spot=22720.0, prev=22570.0, gap_pct=0.66,
            regime="CALL_WALL_BREACH_SQUEEZE", favored="WATCH HOLD ABOVE CALL STRIKE",
            cw=22700.0, pw=22400.0,
        )
        seal_discovery("NIFTY", st)
        append_path_event("NIFTY", "T0915", 22650.0, trade_date=d)
        ppm.save_post_market_state("NIFTY", {
            "date": d,
            "settlement_spot": 22640.0,
            "last_continuous_spot": 22640.0,
            "settlement_divergence_pts": 0.0,
            "pin_efficiency_pct": 0.0,
            "pin_target_wall": "x",
            "settled_at": "15:40:00",
            "status_text": "settled",
        })
    stats = regime_path_stats("NIFTY", "CALL_WALL_BREACH_SQUEEZE")
    assert stats["n"] == 8
    assert stats["hold_rate"] < 0.40

    from analytics.pre_open_watch import build_pre_open_watch
    w = build_pre_open_watch(
        {
            "is_discovered": True,
            "discovered_spot": 22720.0,
            "prev_close": 22570.0,
            "gap_pts": 150.0,
            "gap_pct": 0.66,
            "call_wall": 22700.0,
            "put_wall": 22400.0,
            "gamma_flip": 22550.0,
            "gap_regime": "CALL_WALL_BREACH_SQUEEZE",
            "favored_play": "WATCH HOLD ABOVE CALL STRIKE",
        },
        symbol="NIFTY",
        market_open=False,
        atm_iv=0.14,
        dte_days=2.0,
    )
    assert w["side_hint"] == "NONE"
    assert w["structure"] is None
    assert w["history"]["n"] == 8
    assert "demoted" in w["title"].lower() or "withheld" in w["caution"].lower()


def test_low_n_does_not_demote(path_db):
    from analytics.pre_open_watch import build_pre_open_watch
    w = build_pre_open_watch(
        {
            "is_discovered": True,
            "discovered_spot": 22720.0,
            "prev_close": 22570.0,
            "gap_pts": 150.0,
            "gap_pct": 0.66,
            "call_wall": 22700.0,
            "put_wall": 22400.0,
            "gamma_flip": 22550.0,
            "gap_regime": "CALL_WALL_BREACH_SQUEEZE",
            "favored_play": "WATCH HOLD ABOVE CALL STRIKE",
        },
        symbol="NIFTY",
        market_open=False,
        atm_iv=0.14,
        dte_days=2.0,
    )
    assert w["side_hint"] == "CE"
    assert w["structure"] is not None
    assert (w.get("history") or {}).get("n", 0) == 0
