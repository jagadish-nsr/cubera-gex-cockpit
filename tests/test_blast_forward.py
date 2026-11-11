"""Hand-checks for ACTIVE-episode → forward spot/option joins."""

from app.blast_forward import (
    active_episodes,
    join_active_forward,
    option_forward_from_ticks,
    spot_forward,
)


def test_active_episodes_collapse_consecutive():
    rows = [
        {"time_ist": "13:10", "state": "COILING"},
        {"time_ist": "13:11", "state": "TRIGGERED", "score": "70", "spot": "100", "primary_strike": "99"},
        {"time_ist": "13:12", "state": "TRIGGERED", "score": "80", "spot": "101", "primary_strike": "99"},
        {"time_ist": "13:13", "state": "NO_TRIGGER"},
        {"time_ist": "13:20", "state": "TRIGGERED", "score": "65", "spot": "102", "primary_strike": "100"},
    ]
    eps = active_episodes(rows)
    assert len(eps) == 2
    assert eps[0]["start"] == "13:11" and eps[0]["end"] == "13:12" and eps[0]["n_minutes"] == 2
    assert eps[1]["start"] == "13:20" and eps[1]["n_minutes"] == 1


def test_spot_forward_mfe_mae():
    by = {
        "13:00": {"spot": "100", "primary_strike": "100"},
        "13:01": {"spot": "102"},
        "13:02": {"spot": "99"},
        "13:05": {"spot": "101"},
    }
    out = spot_forward(by, "13:00", horizons_min=(5,))
    assert out["spot0"] == 100.0
    assert out["d5"] == 1.0
    assert out["mfe5"] == 2.0
    assert out["mae5"] == -1.0
    assert out["vs_primary"] == "above"


def test_option_forward_from_ticks():
    ticks = [
        {"time_ist": "13:00:05", "strike": "100", "side": "CE", "ltp": "10"},
        {"time_ist": "13:00:05", "strike": "100", "side": "PE", "ltp": "8"},
        {"time_ist": "13:15:05", "strike": "100", "side": "CE", "ltp": "15"},
        {"time_ist": "13:15:05", "strike": "100", "side": "PE", "ltp": "4"},
    ]
    out = option_forward_from_ticks(ticks, strike=100.0, start_hhmm="13:00", horizon_min=15)
    assert out["ce_ret"] == 0.5
    assert out["pe_ret"] == -0.5


def test_join_active_forward_end_to_end():
    rows = [
        {"time_ist": "13:00", "state": "TRIGGERED", "score": "70", "spot": "100", "primary_strike": "100"},
        {"time_ist": "13:05", "state": "TRIGGERED", "score": "72", "spot": "103", "primary_strike": "100"},
        {"time_ist": "13:06", "state": "NO_TRIGGER", "score": "20", "spot": "104", "primary_strike": "100"},
        {"time_ist": "13:15", "state": "NO_TRIGGER", "score": "20", "spot": "105", "primary_strike": "100"},
    ]
    joined = join_active_forward(rows, None, horizons_min=(5, 15))
    assert len(joined) == 1
    assert joined[0]["n_minutes"] == 2
    assert joined[0]["d5"] == 3.0
