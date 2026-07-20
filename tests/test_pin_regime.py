"""Hand-checks for app.market_profile.pin_regime (Studies #26/#27 dashboard read)."""

from app.market_profile import pin_regime

PROF = {"poc": 24300.0, "vah": 24340.0, "val": 24260.0, "va_width": 80.0}


def test_pin_inside_value_is_pinned_fade():
    r = pin_regime(PROF, 24310.0)                 # inside [24260, 24340]
    assert r["pinned"] is True
    assert r["kind"] == "pinned" and r["lean"] == "fade"
    assert r["gap_pts"] == 10.0                    # |24300 − 24310|
    assert abs(r["gap_vaw"] - 10.0 / 80.0) < 1e-9


def test_pin_outside_value_is_unpinned_run():
    r = pin_regime(PROF, 24450.0)                 # above VAH → outside
    assert r["pinned"] is False
    assert r["kind"] == "unpinned" and r["lean"] == "run"
    assert r["gap_pts"] == 150.0


def test_boundary_pin_on_vah_counts_inside():
    assert pin_regime(PROF, 24340.0)["pinned"] is True     # inclusive band


def test_missing_inputs_return_none():
    assert pin_regime(None, 24300.0) is None
    assert pin_regime(PROF, None) is None
    assert pin_regime({"poc": float("nan"), "vah": 1, "val": 0, "va_width": 1}, 0.5) is None


def test_dashboard_chips_render_the_right_read():
    """The candle chip (de-hype + prior) and the blast context line, both driven off pin_regime."""
    from types import SimpleNamespace as NS

    from app.simple_dashboard import _pin_context_line, _pin_regime_chip
    today = {"today": PROF}
    pinned, unpinned = NS(max_pain=24310.0), NS(max_pain=24500.0)

    cp = _pin_regime_chip(today, pinned)
    assert "INSIDE" in cp and "map, not a magnet" in cp and "fades back" in cp
    cu = _pin_regime_chip(today, unpinned)
    assert "OUTSIDE" in cu and "directional" in cu and "run" in cu

    assert "PINNED" in _pin_context_line(today, pinned)
    assert "UN-PINNED" in _pin_context_line(today, unpinned)

    # guardrails: no profile / no pin → nothing rendered (never a stray chip)
    assert _pin_regime_chip(None, pinned) == ""
    assert _pin_regime_chip({"today": None}, pinned) == ""
    assert _pin_regime_chip(today, NS(max_pain=None)) == ""
    assert _pin_context_line({"today": None}, pinned) == ""
