"""Golden fixtures for the sign-free vol-regime -> structure mapping.

CALM (HIGH concentration) + range-bound -> SELL premium, short call ABOVE the call wall (iron
condor), NOT a long put. WILD (LOW concentration) -> BUY convexity, a non-directional long strangle
(direction isn't forecastable), small size. MID / None -> wait.
"""

from structures.suggest import suggest_structure


class _Levels:
    """Minimal stand-in for gex.levels.Levels (suggest_structure only reads these)."""
    def __init__(self, spot, call_wall, put_wall, flip=None):
        self.spot = spot
        self.call_wall = call_wall
        self.put_wall = put_wall
        self.flip = flip


def test_calm_range_iron_condor():
    L = _Levels(spot=23800, call_wall=24000, put_wall=23600)
    cs = suggest_structure(L, "calm", lot_size=65)
    assert "condor" in cs.name.lower()
    # short call sits AT/ABOVE the call wall, and it is a SELL (not a long put)
    sell_calls = [leg for leg in cs.legs if leg[:2] == ("SELL", "C")]
    assert sell_calls and sell_calls[0][2] >= 24000
    assert cs.exit_by  # forced exit-by present


def test_wild_long_strangle_buy_premium():
    L = _Levels(spot=23000, call_wall=23200, put_wall=22800)
    cs = suggest_structure(L, "wild", lot_size=65)
    assert "strangle" in cs.name.lower()
    assert any(leg[:2] == ("BUY", "P") for leg in cs.legs)   # buying premium
    assert any(leg[:2] == ("BUY", "C") for leg in cs.legs)   # both sides (non-directional)
    assert "small" in cs.risk_note.lower()


def test_mid_and_none_wait_no_legs():
    L = _Levels(spot=23000, call_wall=23200, put_wall=22800)
    assert suggest_structure(L, "mid", lot_size=65).legs == []
    cs = suggest_structure(L, None, lot_size=65)
    assert cs.legs == [] and "wait" in cs.name.lower()


def test_expiry_day_notes():
    L = _Levels(spot=23800, call_wall=24000, put_wall=23600)
    cs = suggest_structure(L, "calm", lot_size=65, is_expiry_day=True)
    assert "PIN" in cs.expiry_note and "BLAST" in cs.expiry_note
    csn = suggest_structure(L, "wild", lot_size=65, is_expiry_day=True)
    assert "BLAST" in csn.expiry_note
    # Not expiry day -> no note.
    assert suggest_structure(L, "calm", lot_size=65, is_expiry_day=False).expiry_note == ""


def test_calm_outside_band_waits():
    # calm but spot below the put wall -> no clean condor -> wait.
    L = _Levels(spot=23547, call_wall=24000, put_wall=23700)
    cs = suggest_structure(L, "calm", lot_size=65)
    assert cs.legs == [] and "wait" in cs.name.lower()
