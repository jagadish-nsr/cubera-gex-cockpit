"""Gamma-blast crossover logic — pure, hand-checkable (parity-based, sign-free)."""

from app.gammablast import (atm_strike, crossover_state, detect_crossovers,
                            gamma_concentration, pick_anchors, strike_premiums)


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
