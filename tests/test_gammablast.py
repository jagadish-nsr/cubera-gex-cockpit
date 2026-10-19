"""Gamma-blast crossover logic — pure, hand-checkable (parity-based, sign-free)."""

from app.gammablast import (atm_strike, blast_score, crossover_state,
                            detect_crossovers, evaluate_dashboard_gamma_blast,
                            gamma_concentration, grade_blast, next_strike,
                            pick_anchors, strike_oi, strike_premiums)


def _strikes(spot=24000):
    # call_ltp - put_ltp tracks spot - strike (put-call parity), by construction.
    return [
        {"strike": 23900.0, "call_ltp": 130.0, "put_ltp": 30.0, "net_gex": 5e8,
         "call_doi": 100, "put_doi": 900},
        {"strike": 24000.0, "call_ltp": 70.0, "put_ltp": 70.0, "net_gex": 9e9,
         "call_doi": 400, "put_doi": 420},
        {"strike": 24100.0, "call_ltp": 30.0, "put_ltp": 130.0, "net_gex": -2e9,
         "call_doi": 700, "put_doi": 120},
    ]


def test_atm_and_anchors():
    assert atm_strike([23900, 24000, 24100], 23980) == 24000.0
    # magnet snaps to the listed strike nearest the (possibly off-grid) magnet.
    a = pick_anchors(_strikes(), spot=23980, magnet=24010)
    assert a == {"magnet": 24000.0, "atm": 24000.0}


def test_strike_premiums_lookup():
    assert strike_premiums(_strikes(), 24100.0) == (30.0, 130.0)
    assert strike_premiums(_strikes(), 99999.0) == (None, None)
    assert strike_premiums(_strikes(), None) == (None, None)


def test_crossover_state_by_parity():
    # call richer => spot above strike; put richer => below; equal => at the cross.
    assert crossover_state(130, 30)["side"] == "call_rich"
    assert crossover_state(30, 130)["side"] == "put_rich"
    assert crossover_state(71, 70)["side"] == "at_cross"      # within tol
    assert crossover_state(None, 70) is None                  # missing premium


def test_detect_crossovers_sign_flip():
    # put_rich -> ... -> call_rich is ONE crossover (spot crossed up through K).
    series = [
        {"time_ist": "14:00", "call": 20, "put": 120},   # put_rich
        {"time_ist": "14:15", "call": 40, "put": 100},   # put_rich
        {"time_ist": "14:30", "call": 90, "put": 50},    # call_rich  <- cross
        {"time_ist": "14:45", "call": 130, "put": 20},   # call_rich
    ]
    evs = detect_crossovers(series)
    assert len(evs) == 1
    assert evs[0]["time_ist"] == "14:30"
    assert evs[0]["from"] == "put_rich" and evs[0]["to"] == "call_rich"


def test_gamma_concentration_ranks_by_exposure():
    out = gamma_concentration(_strikes(), spot=24000, n=2, band_pts=600)
    assert [r["strike"] for r in out] == [24000.0, 24100.0]   # |9e9| > |2e9| > |5e8|
    assert out[0]["net_gex"] == 9e9 and out[0]["dist"] == 0.0


def test_strike_oi_and_next_strike():
    rows = [{"strike": 23900.0, "call_oi": 11, "put_oi": 12},
            {"strike": 24000.0, "call_oi": 400, "put_oi": 420},
            {"strike": 24100.0, "call_oi": 33, "put_oi": 34}]
    assert strike_oi(rows, 24000.0) == (400, 420)
    assert strike_oi(rows, 99999.0) == (None, None)
    assert next_strike(rows, 24000.0, "up") == 24100.0
    assert next_strike(rows, 24000.0, "down") == 23900.0
    assert next_strike(rows, 24100.0, "up") is None            # no strike above


def test_blast_score_true_break_covering_and_clear_path():
    # ITM (call) writers COVER hard (−36%), premium expands, wall ahead eroding
    # (07-03 down-cross of 24350, read on the call side by symmetry): TRUE-leaning.
    crossed = [{"call_oi": 189000, "call_ltp": 109.0, "put_oi": 0, "put_ltp": 0},
               {"call_oi": 121000, "call_ltp": 129.0, "put_oi": 0, "put_ltp": 0}]
    ahead = [{"call_oi": 321000, "call_ltp": None, "put_oi": 0, "put_ltp": None},
             {"call_oi": 259000, "call_ltp": None, "put_oi": 0, "put_ltp": None}]
    sc = blast_score("up", crossed, ahead)
    assert sc["cover_frac"] < -0.3 and sc["prem_exp"] > 0
    assert sc["score"] >= 60 and sc["grade"].startswith("TRUE")


def test_blast_score_weak_when_intact_wall_ahead_caps():
    # ITM writers cover only mildly (−12%), premium up, but a BIG non-eroding wall
    # ahead (07-02 up-cross of 24150): capped → WEAK, not TRUE.
    crossed = [{"call_oi": 85000, "call_ltp": 122.0, "put_oi": 0, "put_ltp": 0},
               {"call_oi": 74000, "call_ltp": 148.0, "put_oi": 0, "put_ltp": 0}]
    ahead = [{"call_oi": 188000, "call_ltp": None, "put_oi": 0, "put_ltp": None},
             {"call_oi": 178000, "call_ltp": None, "put_oi": 0, "put_ltp": None}]
    sc = blast_score("up", crossed, ahead)
    assert 35 <= sc["score"] < 60 and "WEAK" in sc["grade"]


def test_blast_score_fake_when_writers_defend():
    # ITM-side OI RISES (writers adding = active defence) → forced FAKE regardless.
    crossed = [{"call_oi": 100000, "call_ltp": 120.0, "put_oi": 0, "put_ltp": 0},
               {"call_oi": 118000, "call_ltp": 95.0, "put_oi": 0, "put_ltp": 0}]
    sc = blast_score("up", crossed)
    assert sc["cover_frac"] > 0 and "FAKE" in sc["grade"] and "defending" in sc["grade"]


def test_grade_blast_components_reproduce_the_buckets():
    # clear path, strong cover + thrust => TRUE
    assert grade_blast(-0.2, 0.1)["grade"].startswith("TRUE")
    # same ignition/thrust but a BIG non-eroding wall ahead caps it => WEAK
    assert "WEAK" in grade_blast(-0.2, 0.1, wall_ratio=9.0, wall_frac=0.0)["grade"]
    # mild cover, no thrust => WEAK; barely any cover => FAKE (pin/cap)
    assert "WEAK" in grade_blast(-0.05, 0.0)["grade"]
    assert grade_blast(-0.01, 0.0)["grade"] == "FAKE-leaning (pin/cap)"
    # writers ADDING OI (cover>+0.05) => forced FAKE (defending) regardless of score
    assert "defending" in grade_blast(0.1, -0.1)["grade"]


def test_grade_blast_matches_blast_score():
    # the live blast_score must delegate to grade_blast → identical score/grade
    crossed = [{"call_oi": 189000, "call_ltp": 109.0}, {"call_oi": 121000, "call_ltp": 129.0}]
    sc = blast_score("up", crossed)                      # no wall ahead
    g = grade_blast(sc["cover_frac"], sc["prem_exp"])
    assert g["score"] == sc["score"] and g["grade"] == sc["grade"]


def test_blast_score_defensive_none():
    assert blast_score("sideways", [{"call_oi": 1}]) is None    # bad direction
    assert blast_score("up", []) is None                        # no data
    assert blast_score("up", [{"call_oi": 0, "call_ltp": 0}]) is None  # zero start OI


def test_evaluate_dashboard_gamma_blast_non_expiry():
    from unittest.mock import MagicMock
    dash = MagicMock()
    dash.levels.spot = 22800.0
    dash.levels.call_wall = 23000.0
    dash.levels.put_wall = 22600.0
    dash.levels.flip = 22800.0
    dash.levels.magnet = 22800.0
    dash.levels.total_net_gex = 50_000_000.0
    dash.view.regime = "POSITIVE"
    dash.dte_now = 3.5
    dash.expiries = []
    dash.oi_strikes = []
    dash.fetched_at = None

    res = evaluate_dashboard_gamma_blast(dash, "NIFTY")
    assert res["is_expiry"] is False
    assert res["state"] == "NO_TRIGGER"
    assert "INACTIVE (NON-EXPIRY)" in res["badge"]
    assert res["score"] <= 32


def test_evaluate_dashboard_gamma_blast_0dte_breakout():
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    dash = MagicMock()
    dash.levels.spot = 23020.0  # breached Call Wall
    dash.levels.call_wall = 23000.0
    dash.levels.put_wall = 22600.0
    dash.levels.flip = 22950.0
    dash.levels.magnet = 22900.0
    dash.levels.total_net_gex = -250_000_000.0
    dash.view.regime = "NEGATIVE"
    dash.dte_now = 0.0  # 0-DTE
    dash.expiries = []
    # Call writers covering 25% OI
    dash.oi_strikes = [{"strike": 23000.0, "call_oi": 100000.0, "call_doi": -25000.0}]
    # 14:45 IST (09:15 UTC)
    dash.fetched_at = datetime(2026, 10, 19, 9, 15, tzinfo=timezone.utc)

    res = evaluate_dashboard_gamma_blast(dash, "NIFTY")
    assert res["is_expiry"] is True
    assert res["in_blast_window"] is True
    assert res["state"] == "TRIGGERED"
    assert "ACTIVE (CALL SQUEEZE)" in res["badge"]
    assert res["score"] >= 60


def test_evaluate_dashboard_gamma_blast_writers_defending_caps_score():
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    dash = MagicMock()
    dash.levels.spot = 23020.0  # breached Call Wall
    dash.levels.call_wall = 23000.0
    dash.levels.put_wall = 22600.0
    dash.levels.flip = 22950.0
    dash.levels.magnet = 22900.0
    dash.levels.total_net_gex = -50_000_000.0
    dash.view.regime = "NEGATIVE"
    dash.dte_now = 0.0  # 0-DTE
    # Call writers adding +20% OI (defending)
    dash.oi_strikes = [{"strike": 23000.0, "call_oi": 100000.0, "call_doi": 20000.0}]
    dash.fetched_at = datetime(2026, 10, 19, 9, 15, tzinfo=timezone.utc)

    res = evaluate_dashboard_gamma_blast(dash, "NIFTY")
    assert res["is_expiry"] is True
    assert res["state"] == "NO_TRIGGER"
    assert res["score"] < 35

