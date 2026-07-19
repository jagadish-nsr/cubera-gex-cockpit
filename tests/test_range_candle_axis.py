"""The vertical candle's price axis must be anchored on PRICE ACTION, never on the levels.

Regression for a real defect seen on an expiry-day replay: the axis was min/max over today's OHLC
*plus every GEX level plus the prior-day value area*, so an expiry-day gamma flip swinging across
the flip grid (or a prior VA sitting entirely above today) rescaled the axis on every tick — the
candle shrank to a sliver and jumped around. A level that is far away must go OFF-SCALE (an
▲/▼ marker with the value) and leave the geometry untouched.
"""

import re
from types import SimpleNamespace as NS

from app.simple_dashboard import _range_candle

OHLC = {"o": 24180, "h": 24260, "l": 24150, "c": 24200}
# the candle body is the only width-28 rect; matched by shape, not by a pinned x, so tuning the
# layout coordinates doesn't silently disable these tests
BODY = re.compile(r'<rect x="[\d.]+" y="([\d.]+)" width="28" height="([\d.]+)"')


def _levels(**kw):
    base = dict(spot=24200, call_wall=24300, put_wall=24100, magnet=24200,
                max_pain=24250, flip=24200)
    return NS(**{**base, **kw})


def _body(svg):
    m = BODY.search(svg)
    assert m, "candle body not found in the SVG"
    return round(float(m.group(1)), 1), round(float(m.group(2)), 1)


def _offscale(svg):
    return {t for t in re.findall(r">([^<>]+)<", svg) if t.endswith((" above", " below"))}


def test_candle_geometry_is_invariant_to_a_runaway_flip():
    """A gamma flip anywhere from 23000 to 26000 leaves the candle in exactly the same place."""
    ref = _body(_range_candle(_levels(), OHLC))
    for flip in (23000, 24000, 24450, 26000):
        assert _body(_range_candle(_levels(flip=flip), OHLC)) == ref, f"axis moved for flip={flip}"


def test_far_level_is_reported_off_scale_not_plotted():
    svg = _range_candle(_levels(flip=23000), OHLC)
    assert "Gamma flip below" in _offscale(svg)
    assert "below today's range" in svg          # the hover title still gives the full read
    assert "23,000" in svg                       # and the value stays visible


def test_near_levels_stay_on_scale():
    """Walls close to the day's range are still drawn as rules, not demoted to markers."""
    assert _offscale(_range_candle(_levels(), OHLC)) == set()


def test_prior_value_area_above_the_day_does_not_stretch_the_axis():
    prior = {"hi": 24500, "lo": 24400, "poc": 24450, "vah": 24480, "val": 24420, "va_width": 60}
    plain = _range_candle(_levels(), OHLC)
    withva = _range_candle(_levels(), OHLC, prof={"today": None, "prior": prior, "weekly": None})
    assert _body(withva) == _body(plain)
    note = " ".join(t for t in re.findall(r">([^<>]+)<", withva) if "prior VA" in t)
    assert "prior VA above" in note
    assert "24,420" in note and "24,480" in note      # reported with its prices, not just a tag


# ── value-area PRICE labels ────────────────────────────────────────────────────────────────────
import numpy as np  # noqa: E402


def _today(lo=24150, hi=24260, step=5.0):
    cen = np.arange(lo, hi + step, step) + step / 2
    cnt = np.maximum(1.0, 30 - np.abs(np.arange(len(cen)) - 12) * 2)
    return {"counts": cnt, "centers": cen, "bin_pts": step, "poc_i": 12, "val_i": 8, "vah_i": 16,
            "hi": hi, "lo": lo, "poc": float(cen[12]), "vah": float(cen[16]), "val": float(cen[8])}


def _labels(svg):
    return [t for _x, _y, t in re.findall(r'class="vc-mp" x="(\d+)" y="([\d.]+)"[^>]*>([^<]+)<', svg)]


def test_todays_value_area_edges_are_labelled_with_prices():
    t = _today()
    svg = _range_candle(_levels(), OHLC, prof={"today": t, "prior": None, "weekly": None})
    labs = _labels(svg)
    assert any(x.startswith("VAH ") and "," in x for x in labs)
    assert any(x.startswith("VAL ") and "," in x for x in labs)
    assert any(x.startswith("POC ") for x in labs)


def test_prior_value_levels_carry_their_prices():
    prior = {"hi": 24240, "lo": 24120, "poc": 24190, "vah": 24225, "val": 24160}
    svg = _range_candle(_levels(), OHLC, prof={"today": _today(), "prior": prior, "weekly": None})
    labs = _labels(svg)
    assert "pVAH 24,225" in labs and "pPOC 24,190" in labs and "pVAL 24,160" in labs


def test_out_of_window_levels_are_labelled_not_dropped():
    """A level outside the fixed axis still has to be reported — with its price and a direction."""
    week = {"hi": 24500, "lo": 24400, "poc": 24450, "vah": 24480, "val": 24420}
    svg = _range_candle(_levels(), OHLC, prof={"today": _today(), "prior": None, "weekly": week},
                        show_weekly=True)
    off = " ".join(t for t in re.findall(r">([^<>]+)<", svg) if t.startswith(("▲", "▼")))
    assert "5D VA above" in off
    assert "24,420" in off and "24,450" in off and "24,480" in off   # val-poc-vah all reported


def test_labels_fit_inside_the_viewbox():
    """Left labels are anchored END at the gutter — too narrow a gutter clips them off-canvas."""
    week = {"hi": 24240, "lo": 24120, "poc": 24200, "vah": 24230, "val": 24170}
    svg = _range_candle(_levels(), OHLC,
                        prof={"today": _today(), "prior": week, "weekly": week}, show_weekly=True)
    gutter = int(re.search(r'class="vc-mp" x="(\d+)"', svg).group(1))
    widest = max(len(t) for t in _labels(svg))
    assert gutter >= widest * 6.2, f"gutter {gutter}px too narrow for a {widest}-char label"
