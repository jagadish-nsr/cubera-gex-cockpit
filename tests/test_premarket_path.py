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
    reseal_discovery,
    sample_due_checkpoints,
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
    # Filled, but spot is back above the prior close: the gap faded and recovered.
    recovered = thesis_state_at(
        "GAP_UP_INTO_POS_GAMMA",
        open_px=24500.0, spot=24400.0, cw=24700.0, pw=24300.0,
        flip=24400.0, prev=24380.0, gap_fill="FILLED",
    )
    # Filled and through the prior close against the open: the no-fade map failed.
    breach = thesis_state_at(
        "GAP_UP_INTO_POS_GAMMA",
        open_px=24500.0, spot=24370.0, cw=24700.0, pw=24300.0,
        flip=24400.0, prev=24380.0, gap_fill="FILLED",
    )
    assert ok == "MAP_OK"
    assert recovered == "MAP_OK"
    assert breach == "MAP_BREACH"


def test_thesis_no_fade_23_nov_nifty_fade_is_a_breach():
    """23 Nov Nifty: open 25,929, prior close 25,800.6, settled 25,349.65 (-580 from open).

    The check was inverted: it called this MAP_OK and a still-intact gap MAP_BREACH.
    """
    kw = dict(open_px=25929.15, cw=26000.0, pw=26000.0, flip=25806.5, prev=25800.6)
    assert thesis_state_at("GAP_UP_INTO_POS_GAMMA", spot=25349.65, gap_fill="FILLED", **kw) == "MAP_BREACH"
    assert thesis_state_at("GAP_UP_INTO_POS_GAMMA", spot=25971.0, gap_fill="FILLED", **kw) == "MAP_OK"
    assert thesis_state_at("GAP_UP_INTO_POS_GAMMA", spot=25971.0, gap_fill="UNFILLED", **kw) == "MAP_OK"
    # Mirror: gap down, through the prior close upward is the breach.
    kd = dict(open_px=24500.0, cw=24800.0, pw=24300.0, flip=24600.0, prev=24650.0)
    assert thesis_state_at("GAP_DOWN_INTO_POS_GAMMA", spot=24660.0, gap_fill="FILLED", **kd) == "MAP_BREACH"
    assert thesis_state_at("GAP_DOWN_INTO_POS_GAMMA", spot=24600.0, gap_fill="FILLED", **kd) == "MAP_OK"


def test_thesis_book_unavailable_is_no_book():
    assert thesis_state_at(
        "BOOK_UNAVAILABLE", open_px=25658.35, spot=25700.0, cw=0.0, pw=0.0,
        flip=0.0, prev=25349.65, gap_fill="UNFILLED",
    ) == "NO_BOOK"


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


def test_reseal_discovery_after_ltp_to_dhan_open(path_db):
    """Provisional LTP lock → official open must rewrite DISCOVERY spot/regime note."""
    date = "2026-11-19"
    # BN-shaped: shared wall, LTP print inflated gap into SHORT_GAMMA_WALL_TEST
    ltp = _seed_auction(
        "BANKNIFTY", date,
        spot=62204.1, prev=62038.65, gap_pts=165.45, gap_pct=0.27,
        regime="SHORT_GAMMA_WALL_TEST", favored="WATCH THE SHARED GAMMA STRIKE",
        cw=62000.0, pw=62000.0, flip=61662.0,
    )
    first = seal_discovery("BANKNIFTY", ltp)
    assert first is not None
    assert first["spot"] == 62204.1
    assert "SHORT_GAMMA" in (first.get("note") or "")

    # Official day-open is flat → AUCTION_BALANCED (same as today's upgrade)
    official = dict(ltp)
    official.update({
        "discovered_spot": 62081.3,
        "gap_pts": 42.65,
        "gap_pct": 0.07,
        "gap_regime": "AUCTION_BALANCED",
        "favored_play": "NO BOOK TRADE",
        "open_source": "dhan-open",
        "gap_fill_status": "FILLED",
    })
    ppm.save_pre_market_state("BANKNIFTY", official)
    rewritten = reseal_discovery("BANKNIFTY", official)
    assert rewritten is not None
    assert rewritten.get("resealed") is True
    assert rewritten["spot"] == 62081.3
    assert rewritten["note"] == "regime=AUCTION_BALANCED"
    assert rewritten["spot_vs_open_pts"] == 0.0

    # Idempotent when nothing changed
    assert reseal_discovery("BANKNIFTY", official) is None
    # First-seal path still no-ops
    assert seal_discovery("BANKNIFTY", official) is None

    evs = list_path_events("BANKNIFTY", date)
    assert len(evs) == 1
    assert evs[0]["checkpoint"] == "DISCOVERY"
    assert evs[0]["spot"] == 62081.3
    assert evs[0]["note"] == "regime=AUCTION_BALANCED"


def test_persist_opening_watch_reseals_on_upgrade(path_db):
    date = "2026-11-19"
    ltp = _seed_auction(
        "SENSEX", date,
        spot=82376.56, prev=82031.81, gap_pts=344.75, gap_pct=0.42,
        regime="GAP_UP_INTO_POS_GAMMA", favored="MAP ONLY — NO FADE",
        cw=82500.0, pw=82000.0, flip=81887.0,
    )
    assert seal_discovery("SENSEX", ltp) is not None
    official = dict(ltp)
    official.update({
        "discovered_spot": 82067.75,
        "gap_pts": 35.94,
        "gap_pct": 0.04,
        "gap_regime": "AUCTION_BALANCED",
        "favored_play": "NO BOOK TRADE",
        "open_source": "dhan-open",
    })
    ppm.save_pre_market_state("SENSEX", official)
    ppm.persist_opening_watch_from_state("SENSEX", official)
    disc = [e for e in list_path_events("SENSEX", date) if e["checkpoint"] == "DISCOVERY"]
    assert len(disc) == 1
    assert disc[0]["spot"] == 82067.75
    assert disc[0]["note"] == "regime=AUCTION_BALANCED"


def test_reseal_refreshes_walls_when_spot_unchanged(path_db):
    """23-Nov BN: same open/regime but map_strip CW corrected 64500→65000."""
    date = "2026-11-23"
    stale = _seed_auction(
        "BANKNIFTY", date,
        spot=65012.05, prev=64635.2, gap_pts=376.85, gap_pct=0.58,
        regime="CALL_WALL_BREACH_SQUEEZE", favored="WATCH HOLD ABOVE CALL STRIKE",
        cw=64500.0, pw=63000.0, flip=64324.7,
    )
    first = seal_discovery("BANKNIFTY", stale)
    assert first is not None
    assert abs(float(first["spot_vs_cw_pts"]) - (65012.05 - 64500.0)) < 0.1

    fixed = dict(stale)
    fixed.update({"call_wall": 65000.0, "put_wall": 64500.0})
    # Rebuild watch map_strip so auction row matches, then reseal from state walls.
    ppm.save_opening_watch(
        "BANKNIFTY",
        {
            "status": "STAGED",
            "regime": fixed["gap_regime"],
            "map_strip": {
                "open": fixed["discovered_spot"],
                "call_wall": 65000.0,
                "put_wall": 64500.0,
                "gamma_flip": fixed["gamma_flip"],
                "prev_close": fixed["prev_close"],
                "gap_pts": fixed["gap_pts"],
                "gap_pct": fixed["gap_pct"],
            },
        },
        trade_date=date,
    )
    rewritten = reseal_discovery("BANKNIFTY", fixed)
    assert rewritten is not None
    assert abs(float(rewritten["spot_vs_cw_pts"]) - 12.05) < 0.1
    assert abs(float(rewritten["spot_vs_pw_pts"]) - (65012.05 - 64500.0)) < 0.1


def test_checkpoints_wait_for_discovery(path_db, monkeypatch):
    date = "2026-11-23"
    # Auction row exists (morning briefing) but DISCOVERY not sealed yet.
    _seed_auction("NIFTY", date, spot=25929.0, cw=25500.0, pw=25500.0)
    monkeypatch.setattr(
        "analytics.premarket_path._spot_now", lambda _sym: 25971.0,
    )
    now = datetime(2026, 11, 23, 9, 16, tzinfo=IST)
    assert sample_due_checkpoints("NIFTY", now) == []
    assert list_path_events("NIFTY", date) == []

    state = {
        "date": date,
        "is_discovered": True,
        "discovered_spot": 25929.15,
        "prev_close": 25800.6,
        "gap_regime": "GAP_UP_INTO_POS_GAMMA",
        "gap_fill_status": "UNFILLED",
        "call_wall": 26000.0,
        "put_wall": 26000.0,
        "gamma_flip": 25806.5,
    }
    ppm.save_pre_market_state("NIFTY", state)
    ppm.save_opening_watch(
        "NIFTY",
        {
            "status": "STAGED",
            "regime": "GAP_UP_INTO_POS_GAMMA",
            "map_strip": {
                "open": 25929.15,
                "call_wall": 26000.0,
                "put_wall": 26000.0,
                "gamma_flip": 25806.5,
                "prev_close": 25800.6,
            },
        },
        trade_date=date,
    )
    assert seal_discovery("NIFTY", state) is not None
    fired = sample_due_checkpoints("NIFTY", now)
    assert "T0915" in fired
    evs = list_path_events("NIFTY", date)
    assert [e["checkpoint"] for e in evs] == ["DISCOVERY", "T0915"]
    t0915 = evs[1]
    assert abs(float(t0915["spot_vs_cw_pts"]) - (25971.0 - 26000.0)) < 0.1


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


def test_reseal_recomputes_checkpoints_sampled_on_the_provisional_open(path_db):
    """23 Nov Nifty: T0915 was sampled on the 25,368 print before the official 25,929 open."""
    date = "2026-11-23"
    prov = _seed_auction(
        "NIFTY", date,
        spot=25368.0, prev=25800.6, gap_pts=-432.6, gap_pct=-1.68,
        regime="PUT_WALL_BREAKDOWN_TEST", favored="WATCH THE SHARED GAMMA STRIKE",
        cw=25500.0, pw=25500.0, flip=25816.0,
    )
    assert seal_discovery("NIFTY", prov) is not None
    t0915 = append_path_event("NIFTY", "T0915", 25971.0, trade_date=date)
    assert t0915["thesis_state"] == "INVALIDATED"  # read against the provisional put wall
    assert t0915["spot_vs_open_pts"] == pytest.approx(603.0)

    official = dict(prov)
    official.update({
        "discovered_spot": 25929.15, "gap_pts": 128.55, "gap_pct": 0.50,
        "gap_regime": "GAP_UP_INTO_POS_GAMMA", "favored_play": "MAP ONLY — NO FADE",
        "open_source": "dhan-open", "gap_fill_status": "UNFILLED",
        "call_wall": 26000.0, "put_wall": 25800.0, "gamma_flip": 25806.5,
        "book_source": "live-chain",
    })
    ppm.save_pre_market_state("NIFTY", official)
    ppm.save_opening_watch("NIFTY", {
        "status": "STAGED", "regime": official["gap_regime"],
        "map_strip": {
            "open": 25929.15, "call_wall": 26000.0, "put_wall": 25800.0,
            "gamma_flip": 25806.5, "prev_close": 25800.6,
            "gap_pts": 128.55, "gap_pct": 0.50,
        },
    }, trade_date=date)
    assert reseal_discovery("NIFTY", official) is not None

    evs = {e["checkpoint"]: e for e in list_path_events("NIFTY", date)}
    t = evs["T0915"]
    assert t["thesis_state"] == "MAP_OK"
    assert t["spot_vs_open_pts"] == pytest.approx(41.85)
    assert t["spot_vs_cw_pts"] == pytest.approx(-29.0)
    assert t["spot_vs_pw_pts"] == pytest.approx(171.0)
    assert t["gap_fill"] == "UNFILLED"
    assert "recomputed after reseal" in t["note"]
    assert evs["DISCOVERY"]["note"] == "regime=GAP_UP_INTO_POS_GAMMA book=live-chain"
    # Second call has nothing left to change.
    assert reseal_discovery("NIFTY", official) is None


def test_reseal_with_no_book_zeroes_walls_instead_of_keeping_the_old_ones(path_db):
    date = "2026-11-25"
    stale = _seed_auction(
        "NIFTY", date, spot=25658.35, prev=25349.65, gap_pts=308.7, gap_pct=1.22,
        regime="SHORT_GAMMA_WALL_TEST", cw=25350.0, pw=25350.0, flip=25349.65,
    )
    assert seal_discovery("NIFTY", stale) is not None
    nobook = dict(stale)
    nobook.update({
        "gap_regime": "BOOK_UNAVAILABLE", "favored_play": "MAP ONLY — NO BOOK",
        "call_wall": 0.0, "put_wall": 0.0, "gamma_flip": 0.0, "book_source": "unavailable",
    })
    ppm.save_pre_market_state("NIFTY", nobook)
    ppm.save_opening_watch("NIFTY", {
        "status": "STAGED", "regime": "BOOK_UNAVAILABLE",
        "map_strip": {"open": 25658.35, "call_wall": 0.0, "put_wall": 0.0,
                      "gamma_flip": 0.0, "prev_close": 25349.65},
    }, trade_date=date)
    out = reseal_discovery("NIFTY", nobook)
    assert out is not None
    assert out["thesis_state"] == "NO_BOOK"
    assert out["spot_vs_cw_pts"] is None and out["spot_vs_pw_pts"] is None
    assert out["note"] == "regime=BOOK_UNAVAILABLE book=unavailable"


def test_no_book_session_is_not_scored_and_stays_out_of_the_base_rate(path_db):
    date = "2026-11-25"
    state = _seed_auction(
        "NIFTY", date, spot=25658.35, prev=25349.65, gap_pts=308.7, gap_pct=1.22,
        regime="BOOK_UNAVAILABLE", favored="MAP ONLY — NO BOOK", cw=0.0, pw=0.0, flip=0.0,
    )
    seal_discovery("NIFTY", state)
    append_path_event("NIFTY", "T1000", 25774.0, trade_date=date)
    scored = score_path("NIFTY", date)
    assert scored["thesis_outcome"] == "NO_BOOK"
    assert scored["path_efficiency_pct"] is None
    assert "no usable book" in scored["detail"]["unscored"]
    rec = ppm.get_persisted_auction_record("NIFTY", date)
    assert rec["path_efficiency_pct"] is None
    assert regime_path_stats("NIFTY", "BOOK_UNAVAILABLE")["n"] == 0
