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
BODY = re.compile(r'<rect x="176.0" y="([\d.]+)" width="28" height="([\d.]+)"')


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
    assert any("prior VA above range" in t for t in re.findall(r">([^<>]+)<", withva))
