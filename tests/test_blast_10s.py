"""The gamma-blast CROSS is now timed off the ~10s spot lane instead of the option-chain series.

Put-call parity: call − put = S − K·e^(−rT), so spot crossing K is the same event as the premium
crossover — but spot has its own 10s lane, while the chain moves every 1-5 min. The §7d read hangs
on "did the score reach TRUE within 5 min OF THE CROSS", so a cross timed to ±3 min was blurring
the classification the whole feature depends on.
"""

from app.gammablast import detect_crossovers, detect_crossovers_spot
from app.simple_dashboard import _secs


def _spot(path):
    return [{"time_ist": t, "spot": s} for t, s in path]


def test_spot_cross_matches_the_premium_cross():
    """Same event, both ways round — parity, so the two detectors must agree on direction."""
    K = 24200.0
    up = _spot([("13:45:00", 24180.0), ("13:45:10", 24190.0), ("13:45:20", 24210.0)])
    ev = detect_crossovers_spot(up, K)
    assert len(ev) == 1 and ev[0]["to"] == "call_rich" and ev[0]["time_ist"] == "13:45:20"

    prem = [{"time_ist": "13:45", "call": 20.0, "put": 40.0},
            {"time_ist": "13:46", "call": 45.0, "put": 15.0}]
    assert detect_crossovers(prem)[0]["to"] == "call_rich"


def test_spot_cross_downward():
    ev = detect_crossovers_spot(
        _spot([("14:00:00", 24230.0), ("14:00:10", 24215.0), ("14:00:20", 24185.0)]), 24200.0)
    assert len(ev) == 1 and ev[0]["to"] == "put_rich"


def test_ten_second_resolution_beats_the_chain_cadence():
    """The point of the change: the chain would place this cross at 13:48, the spot lane at
    13:45:20 — a 2m40s error in the input to the 5-minute Fast-TRUE test."""
    K = 24200.0
    fine = detect_crossovers_spot(
        _spot([("13:45:10", 24190.0), ("13:45:20", 24210.0), ("13:47:00", 24240.0)]), K)
    coarse = detect_crossovers([{"time_ist": "13:45", "call": 10.0, "put": 30.0},
                                {"time_ist": "13:48", "call": 55.0, "put": 8.0}])
    err_s = _secs(coarse[0]["time_ist"]) - _secs(fine[0]["time_ist"])
    assert fine[0]["time_ist"] == "13:45:20"
    assert err_s == 160                      # 2m40s of timing error removed


def test_no_cross_when_spot_stays_one_side():
    assert detect_crossovers_spot(
        _spot([("13:45:00", 24210.0), ("13:45:10", 24230.0)]), 24200.0) == []


def test_exactly_at_the_strike_is_not_a_cross():
    """diff == 0 is undefined, not a side — it must not manufacture an event."""
    assert detect_crossovers_spot(
        _spot([("13:45:00", 24200.0), ("13:45:10", 24200.0)]), 24200.0) == []


def test_missing_spot_points_are_skipped():
    assert detect_crossovers_spot(
        [{"time_ist": "13:45:00", "spot": None}, {"time_ist": "13:45:10", "spot": 24210.0}],
        24200.0) == []


def test_secs_handles_both_resolutions():
    assert _secs("13:45") == 13 * 3600 + 45 * 60          # chain-cadence points
    assert _secs("13:45:20") == 13 * 3600 + 45 * 60 + 20  # spot-lane points
    assert _secs("13:45") < _secs("13:45:20")             # ordering across the two must be sane
    assert _secs("bad") is None and _secs(None) is None
