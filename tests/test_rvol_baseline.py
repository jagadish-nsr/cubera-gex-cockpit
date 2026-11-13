"""Hand-checkable TOD RVol baselines — empirical U-curve, gates unchanged.

Fit days 2026-11-05..12; holdout semantics documented in sentinel/engine/rvol.py.
CONT classifier thresholds (rvol≥1.5, Δ, VWAP, oi_fresh) are NOT relaxed here.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from sentinel.engine import rvol as rvol_mod
from sentinel.engine.rvol import (
    BASELINE_FIT_DAYS,
    BASELINE_HOLDOUT_DAY,
    _CURVES,
    _curve_at,
    _get_tod_baseline,
    _index_key,
    compute_tod_rvol,
    invalidate_live_curves,
)

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(autouse=True)
def _force_frozen_curves(tmp_path, monkeypatch):
    """Baseline arithmetic tests pin shipped frozen curves (ignore live journal)."""
    missing = tmp_path / "no_rvol_baseline.json"
    monkeypatch.setattr(rvol_mod, "_DEFAULT_PROFILE", missing)
    invalidate_live_curves()
    yield
    invalidate_live_curves()


def _ist(h: int, m: int) -> datetime:
    return datetime(2026, 11, 13, h, m, tzinfo=IST)


def test_index_key_banknifty_before_nifty_substring():
    assert _index_key("BANKNIFTY-FUT") == "BANKNIFTY"
    assert _index_key("NIFTY-FUT") == "NIFTY"
    assert _index_key("SENSEX") == "SENSEX"


def test_curve_anchors_match_named_constants():
    """t=0 → open_peak, t=180 → trough, t=375 → close_peak (before floor)."""
    for key, curve in _CURVES.items():
        assert _curve_at(0, curve) == pytest.approx(curve.open_peak)
        assert _curve_at(180, curve) == pytest.approx(max(curve.floor, curve.trough))
        assert _curve_at(375, curve) == pytest.approx(max(curve.floor, curve.close_peak))


def test_nifty_midday_baseline_near_empirical_trough():
    """Old analytical noon was ~12k; empirical trough ~6.6k — must not regress to 12k."""
    noon = _get_tod_baseline("12:15", "NIFTY-FUT")
    assert 5_000.0 <= noon <= 8_000.0
    assert noon == pytest.approx(_CURVES["NIFTY"].trough, rel=0.02)


def test_nifty_open_baseline_is_first_print_peak_not_midday():
    open_b = _get_tod_baseline("09:15", "NIFTY")
    mid_b = _get_tod_baseline("12:15", "NIFTY")
    assert open_b > 5 * mid_b


def test_hand_check_nifty_rvol_at_noon():
    """volume / trough at 12:15 — arithmetic must be reproducible by hand."""
    trough = _CURVES["NIFTY"].trough
    # Exactly 1.5× trough → RVol 1.50 (CONT volume gate edge).
    r = compute_tod_rvol(_ist(12, 15), volume=1.5 * trough, instrument="NIFTY-FUT")
    assert r == pytest.approx(1.5, abs=0.01)
    # Typical quiet noon print (~0.5× trough) must stay below CONT volume gate.
    quiet = compute_tod_rvol(_ist(12, 15), volume=0.5 * trough, instrument="NIFTY-FUT")
    assert quiet < 1.5
    # Old bug: 6890 units at noon looked like ~0.57 RVol under 12k baseline.
    # Under empirical trough it is elevated but still needs other CONT gates.
    replay_1219 = compute_tod_rvol(_ist(12, 19), volume=6890.0, instrument="NIFTY-FUT")
    assert replay_1219 >= 1.0
    assert replay_1219 < 3.0  # not climax from a normal sell bar alone


def test_same_absolute_volume_higher_rvol_at_midday_than_open():
    """U-shape property: identical units are more 'unusual' at midday."""
    vol = 20_000.0
    morning = compute_tod_rvol(_ist(9, 20), volume=vol, instrument="NIFTY")
    midday = compute_tod_rvol(_ist(12, 30), volume=vol, instrument="NIFTY")
    assert midday > morning


def test_sensex_baseline_not_flooding_midday():
    """Old Sensex midday ~60 made half the tape look like RVol≥1.5."""
    mid = _get_tod_baseline("12:15", "SENSEX-FUT")
    assert mid >= 100.0  # empirical trough ~160
    # A merely typical Sensex minute (≈ trough) is RVol ~1.0, not 2.5+.
    typical = compute_tod_rvol(_ist(12, 15), volume=mid, instrument="SENSEX-FUT")
    assert typical == pytest.approx(1.0, abs=0.02)


def test_banknifty_substring_uses_bn_curve_not_nifty():
    bn = _get_tod_baseline("12:15", "BANKNIFTY-FUT")
    nf = _get_tod_baseline("12:15", "NIFTY-FUT")
    assert bn == pytest.approx(_CURVES["BANKNIFTY"].trough, rel=0.02)
    assert bn < nf / 2.0  # BN units are smaller; must not borrow Nifty curve


def test_zero_volume_is_zero_rvol():
    assert compute_tod_rvol(_ist(12, 0), volume=0.0, instrument="NIFTY") == 0.0


def test_fit_provenance_documented():
    assert BASELINE_HOLDOUT_DAY == "2026-11-13"
    assert "2026-11-12" in BASELINE_FIT_DAYS
    assert BASELINE_HOLDOUT_DAY not in BASELINE_FIT_DAYS


def test_classifier_continuation_rvol_gate_unchanged():
    """Guardrail: recalibration must not silently lower CONT RVol in classifier."""
    from sentinel.engine import classifier as clf
    import inspect
    import re

    src = inspect.getsource(clf.SignalClassifierEngine.evaluate_bar)
    # CONTINUATION blocks still require rvol >= 1.5 (both bull and bear).
    cont_rvol = re.findall(
        r"rvol >= 1\.5\n\s+and bar\.delta_ratio",
        src,
    )
    assert len(cont_rvol) >= 2, "both CONTINUATION_BULL and _BEAR must keep rvol >= 1.5"
    # Must not introduce a softened CONT volume gate (CVD may use 1.2 — that is fine).
    assert "rvol >= 1.0" not in src
    assert "rvol >= 1.1" not in src
