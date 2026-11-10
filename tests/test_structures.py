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


def test_expiry_blast_active_overrides_note_and_keeps_shorts_off_magnet():
    from structures.suggest import suggest_structures

    L = _Levels(spot=24060, call_wall=24000, put_wall=23600)
    L.magnet = 24050
    blast = {
        "is_expiry": True,
        "state": "TRIGGERED",
        "score": 70,
        "badge": "GAMMA BLAST: ACTIVE",
        "primary_strike": 24050,
    }
    cands = suggest_structures(L, "calm", lot_size=65, is_expiry_day=True, blast=blast)
    assert cands
    assert "ACTIVE" in cands[0].expiry_note
    assert "dealers" not in cands[0].expiry_note.lower()
    for cs in cands:
        for leg in (cs.legs or []):
            action, _right, strike = leg[0], leg[1], leg[2]
            if action == "SELL":
                assert int(strike) != 24050


def test_expiry_blast_coiling_note():
    L = _Levels(spot=23980, call_wall=24000, put_wall=23600)
    blast = {
        "is_expiry": True,
        "state": "COILING",
        "score": 45,
        "badge": "GAMMA BLAST: COILING / IMMINENT",
        "primary_strike": 24000,
    }
    cs = suggest_structure(L, "calm", lot_size=65, is_expiry_day=True, blast=blast)
    assert "COILING" in cs.expiry_note
    assert "map" in cs.expiry_note.lower() or "Nearness" in cs.expiry_note


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


def test_calendar_when_front_iv_rich():
    from structures.suggest import suggest_structures

    L = _Levels(spot=23800, call_wall=24000, put_wall=23600)
    cands = suggest_structures(
        L, "calm", lot_size=65, strike_step=50,
        near_atm_iv=0.16, next_atm_iv=0.13,
    )
    assert len(cands) >= 2
    term = next((c for c in cands if "calendar" in c.name.lower() or "diagonal" in c.name.lower()), None)
    assert term is not None
    assert any(len(leg) == 4 and leg[3] == "near" for leg in term.legs)
    assert any(len(leg) == 4 and leg[3] == "next" for leg in term.legs)
    sells = [leg for leg in term.legs if leg[0] == "SELL"]
    assert sells and sells[0][3] == "near"


def test_diagonal_with_bullish_bias():
    from structures.suggest import suggest_structures

    L = _Levels(spot=23800, call_wall=24000, put_wall=23600)
    cands = suggest_structures(
        L, "calm", lot_size=65, strike_step=50,
        bias_score=1.5, near_atm_iv=0.18, next_atm_iv=0.14,
    )
    term = next((c for c in cands if "diagonal" in c.name.lower()), None)
    assert term is not None
    assert term.legs[0][0] == "SELL" and term.legs[0][3] == "near"
    assert term.legs[1][0] == "BUY" and term.legs[1][3] == "next"


def test_no_calendar_when_term_flat():
    from structures.suggest import suggest_structures

    L = _Levels(spot=23800, call_wall=24000, put_wall=23600)
    cands = suggest_structures(
        L, "calm", lot_size=65, strike_step=50,
        near_atm_iv=0.14, next_atm_iv=0.139,
    )
    assert not any("calendar" in c.name.lower() or "diagonal" in c.name.lower() for c in cands)


def test_calendar_short_nudge_off_magnet_atm():
    from structures.suggest import suggest_structures

    L = _Levels(spot=25400, call_wall=25600, put_wall=25200)
    L.magnet = 25400  # ATM == magnet
    cands = suggest_structures(
        L, "calm", lot_size=65, strike_step=50,
        near_atm_iv=0.20, next_atm_iv=0.15,
    )
    term = next(c for c in cands if "calendar" in c.name.lower() or "diagonal" in c.name.lower())
    shorts = [leg for leg in term.legs if leg[0] == "SELL"]
    assert shorts
    assert int(shorts[0][2]) != 25400
    assert shorts[0][3] == "near"

