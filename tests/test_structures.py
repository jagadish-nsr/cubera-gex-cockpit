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
    cs = suggest_structure(L, "calm", lot_size=65, strike_step=50)
    assert "condor" in cs.name.lower()
    sell_calls = [leg for leg in cs.legs if leg[:2] == ("SELL", "C")]
    sell_puts = [leg for leg in cs.legs if leg[:2] == ("SELL", "P")]
    assert sell_calls[0][2] == 24050  # one step above the call-gamma strike
    assert sell_puts[0][2] == 23550   # one step below the put-gamma strike
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


def test_suggest_structures_multiple_options():
    from structures.suggest import suggest_structures

    # In calm regime with spot inside range: returns primary (condor) + alternate (credit spread)
    L = _Levels(spot=23900, call_wall=24000, put_wall=23600)
    candidates = suggest_structures(L, "calm", lot_size=65)
    assert len(candidates) == 2
    assert "condor" in candidates[0].name.lower()
    assert "call spread" in candidates[1].name.lower()  # spot closer to call wall

    # In wild regime: returns primary (strangle) + alternate (debit spread)
    L_wild = _Levels(spot=23000, call_wall=23200, put_wall=22800)
    wild_candidates = suggest_structures(L_wild, "wild", lot_size=65, bias_score=2.5)
    assert len(wild_candidates) == 2
    assert "strangle" in wild_candidates[0].name.lower()
    assert "bull call" in wild_candidates[1].name.lower()  # positive bias score
    sell_calls = [leg for leg in wild_candidates[1].legs if leg[:2] == ("SELL", "C")]
    assert sell_calls[0][2] == 23250  # short sits one step above the call-gamma strike


class _Bundle:
    def __init__(self, **kw):
        self.symbol = kw.get("symbol", "NIFTY")
        self.vanna_now = kw.get("vanna_now", 1.0)
        self.charm_now = kw.get("charm_now")
        self.dte_now = kw.get("dte_now", 3.0)
        self.proxy_symbol = kw.get("proxy_symbol")
        self.proxy_vanna = kw.get("proxy_vanna")
        self.view = type("V", (), {"mode_label": "CALL-HEAVY"})()


def test_structure_uses_absolute_gex_bucket_not_the_sign(monkeypatch):
    from app.intraday_features import structure_vol_regime
    monkeypatch.setattr(
        "app.intraday_features.regime_now", lambda *a, **k: {"tag": "wild"},
    )
    assert structure_vol_regime(_Bundle()) == "wild"


def test_structure_waits_when_the_gauge_is_missing(monkeypatch):
    from app.intraday_features import structure_vol_regime
    monkeypatch.setattr("app.intraday_features.regime_now", lambda *a, **k: None)
    assert structure_vol_regime(_Bundle()) == "mid"
    assert structure_vol_regime(None) == "mid"


def test_sensex_structure_borrows_nifty_concentration(monkeypatch):
    from app.intraday_features import structure_vol_regime
    seen = {}

    def fake(store, value, **k):
        seen["store"] = store
        seen["value"] = value
        return {"tag": "calm"}

    monkeypatch.setattr("app.intraday_features.intraday_vol_regime", fake)
    tag = structure_vol_regime(_Bundle(symbol="SENSEX", proxy_symbol="NIFTY", proxy_vanna=12.0))
    assert tag == "calm"
    assert seen == {"store": "NIFTY", "value": 12.0}

