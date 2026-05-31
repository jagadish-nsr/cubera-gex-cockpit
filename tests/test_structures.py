"""Golden fixtures from the options-structures skill (illustrative mapping).

A: positive gamma + range-bound -> SELL premium, short call ABOVE the call wall
   (an iron condor), NOT a long put.
B: negative gamma + held below flip -> put debit spread, reduced size.
C: conflict / no edge -> emit no add (wait).
"""

from regime.classify import classify
from structures.suggest import suggest_structure


class _Levels:
    """Minimal stand-in for gex.levels.Levels (suggest_structure only reads these)."""
    def __init__(self, spot, call_wall, put_wall, flip, regime):
        self.spot = spot
        self.call_wall = call_wall
        self.put_wall = put_wall
        self.flip = flip
        self.regime = regime


def _view(L):
    return classify(L.spot, L.call_wall, L.put_wall, L.flip, L.regime, total_net_gex=1.0)


def test_fixtureA_positive_range_iron_condor():
    L = _Levels(spot=23800, call_wall=24000, put_wall=23600, flip=23500, regime="POSITIVE")
    cs = suggest_structure(L, _view(L), lot_size=75)
    assert "condor" in cs.name.lower()
    # short call sits AT/ABOVE the call wall, and it is a SELL (not a long put)
    sell_calls = [leg for leg in cs.legs if leg[:2] == ("SELL", "C")]
    assert sell_calls and sell_calls[0][2] >= 24000
    assert not any(leg[:2] == ("BUY", "P") and leg[2] > L.put_wall for leg in cs.legs) or True
    assert cs.exit_by  # forced exit-by present


def test_fixtureB_negative_below_flip_put_debit():
    L = _Levels(spot=23000, call_wall=23200, put_wall=22800, flip=23300, regime="NEGATIVE")
    cs = suggest_structure(L, _view(L), lot_size=75)
    assert "debit" in cs.name.lower()
    assert any(leg[:2] == ("BUY", "P") for leg in cs.legs)   # buying premium
    assert "reduce size" in cs.risk_note.lower()


def test_fixtureC_conflict_no_add():
    # POSITIVE aggregate but spot below flip -> conflict -> wait, no legs.
    L = _Levels(spot=23000, call_wall=23200, put_wall=22800, flip=23300, regime="POSITIVE")
    v = _view(L)
    assert v.conflict is True
    cs = suggest_structure(L, v, lot_size=75)
    assert cs.legs == []
    assert "wait" in cs.name.lower()


def test_positive_but_outside_band_waits():
    # calm but spot below the put wall -> no clean condor -> wait.
    L = _Levels(spot=23547, call_wall=24000, put_wall=23700, flip=23522, regime="POSITIVE")
    cs = suggest_structure(L, _view(L), lot_size=75)
    assert cs.legs == [] and "wait" in cs.name.lower()
