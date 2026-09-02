"""Hand-checkable tests for the offline gamma-blast study (app/blast_study.py).

Synthetic anchor series with known geometry so every verdict is checkable by eye. Sign-free; these lock
the pin-chop gate, the run-commit, the realized-delta sign, and the exit disciplines."""
import pandas as pd
import pytest

from app import blast_study as bs


def _series(rows, K=100.0):
    """rows = list of (mins, spot, call_ltp, put_ltp). Build the anchor frame the study consumes."""
    df = pd.DataFrame(rows, columns=["mins", "spot", "call_ltp", "put_ltp"])
    df["time_ist"] = df["mins"].map(lambda m: f"{13 + (45 + int(m)) // 60:02d}:{(45 + int(m)) % 60:02d}")
    df["disp"] = df["spot"] - K
    df["call_oi"] = 1000.0
    df["put_oi"] = 1000.0
    return df


def test_persistence_gate_ignores_chop():
    # spot jitters across K by <margin and for <dwell → NOT a real break
    a = _series([(m, 100 + (2 if m % 2 else -2), 5, 5) for m in range(0, 30, 2)])
    assert bs.persistence_cross(a, margin=10, dwell_min=0.75) is None


def test_persistence_gate_fires_on_sustained_break():
    # clears K by >margin and stays up for >dwell → a real up-break, timed at the arm
    rows = [(0, 100, 5, 5), (2, 101, 5, 5)] + [(m, 130, 30, 1) for m in range(4, 40, 2)]
    br = bs.persistence_cross(_series(rows), margin=10, dwell_min=0.75)
    assert br is not None and br["direction"] == "up"


def test_commit_time_none_for_pin():
    # spot ends near K having never sustained >COMMIT_PTS → a pin
    a = _series([(m, 100 + (10 if m % 4 else -10), 5, 5) for m in range(0, 40, 2)])
    assert bs.commit_time(a, thresh=bs.COMMIT_PTS) is None


def test_commit_time_run_up():
    rows = [(m, 100 + min(m, 60), 5 + min(m, 60), max(1, 5 - m)) for m in range(0, 50, 2)]
    c = bs.commit_time(_series(rows), thresh=40)
    assert c is not None and c["direction"] == "up"


def test_realized_delta_sign_and_magnitude():
    # call premium tracks spot 1:1 (deep-ITM up-run) → realized call-delta ≈ 1
    rows = [(m, 100 + m, 5 + m, max(0.1, 5 - m)) for m in range(0, 20, 2)]
    d = bs.realized_delta(_series(rows), "call", win_min=20)
    assert d is not None and 0.8 <= d <= 1.2


def test_realized_delta_put_positive_on_downside():
    # spot falls, put premium rises 1:1 → reported |put-delta| ≈ 1 (positive commitment)
    rows = [(m, 100 - m, max(0.1, 5 - m), 5 + m) for m in range(0, 20, 2)]
    d = bs.realized_delta(_series(rows), "put", win_min=20)
    assert d is not None and 0.8 <= d <= 1.2


def test_exit_hard_time_stop_before_settlement():
    # a run that never retraces → exit is the hard pre-settlement time-stop, not the trail
    rows = [(m, 100 + min(m, 100), 5 + min(m, 100), 1) for m in range(0, 120, 2)]
    ex = bs.exit_signal(_series(rows), "call", entry_mins=0, trail_pts=30, flat_by="15:25")
    assert ex["reason"] == "settlement-time-stop"


def test_exit_spot_trailing_stop_on_retrace():
    # spot runs to +60 then gives back >trail before settlement → trailing stop fires
    up = [(m, 100 + m, 5 + m, 1) for m in range(0, 60, 2)]
    down = [(m, 160 - (m - 58), 60, 1) for m in range(60, 130, 2)]
    ex = bs.exit_signal(_series(up + down), "call", entry_mins=0, trail_pts=30, flat_by="15:55")
    assert ex["reason"] == "spot-trailing-stop"


def test_drop_remap_removes_oi_discontinuity():
    # a final row whose OI leaps >50% above the running median is dropped
    base = [{"time_ist": f"13:{45 + i:02d}", "strike": 100.0, "call_oi": 1000.0, "put_oi": 1000.0}
            for i in range(10)]
    base.append({"time_ist": "13:59", "strike": 100.0, "call_oi": 9000.0, "put_oi": 1000.0})
    out = bs._drop_remap(pd.DataFrame(base))
    assert (out["call_oi"] < 5000).all()
