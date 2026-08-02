"""Hand-checks for app/blast_ticks_analysis.py — parity crossover + OI/volume deltas + TRUE score on
synthetic captured ticks (put-rich → call-rich = spot up through K; post-cross the CALL writers cover and
the call premium expands = a TRUE-leaning ignition)."""
import pandas as pd

from app.blast_ticks import _COLS
import app.blast_ticks as bt
from app.blast_ticks_analysis import (load_pair, load_walls, align, crossovers, deltas, true_score,
                                      graded_crossovers, summary)


def _row(epoch, side, ltp, oi, vol, strike=24000.0, role="anchor"):
    return {"epoch": epoch, "time_ist": f"13:{45 + epoch % 15:02d}:00", "symbol": "NIFTY",
            "side": side, "strike": strike, "role": role, "expiry": "2026-08-04", "code": 8, "ltp": ltp,
            "ltq": 75, "atp": ltp, "volume": vol, "oi": oi, "bid": ltp - 0.5, "ask": ltp + 0.5}


def _write(tmp_path, monkeypatch, wall_up_oi=None):
    """Synthetic up-cross with covering + premium expansion (TRUE). If ``wall_up_oi`` is a (first,last)
    tuple, also write an up-wall CALL at 24050 with that OI path (an intact big wall should cap the score)."""
    monkeypatch.setattr(bt, "RAW_DIR", tmp_path)
    rows = []
    for i, e in enumerate(range(0, 6, 2)):
        rows.append(_row(e, "CE", 90.0, oi=1_000_000, vol=1000 + i * 100))
        rows.append(_row(e + 1, "PE", 110.0, oi=800_000, vol=500 + i * 50))
    for i, (e, cltp, coi, cvol) in enumerate([(6, 115.0, 1_000_000, 1400),
                                              (8, 124.0, 850_000, 1900),
                                              (10, 132.0, 700_000, 2600)]):
        rows.append(_row(e, "CE", cltp, oi=coi, vol=cvol))
        rows.append(_row(e + 1, "PE", 95.0, oi=800_000, vol=700 + i * 40))
    if wall_up_oi is not None:
        f, l = wall_up_oi
        for j, e in enumerate((6, 8, 10)):
            oi = f + (l - f) * j / 2
            rows.append(_row(e, "CE", 40.0, oi=oi, vol=100, strike=24050.0, role="wall_up"))
    p = tmp_path / "NIFTY" / "2026-08-04.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows)[_COLS].to_csv(p, index=False)


def test_align_carries_oi_and_volume(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    g = align(*load_pair("NIFTY", "2026-08-04"))
    assert {"call_vol", "put_vol", "call_oi", "put_oi", "diff", "implied_spot"} <= set(g.columns)
    assert g["implied_spot"].min() == 23980.0 and g["implied_spot"].max() == 24037.0  # 24000+(132-95)


def test_deltas_oi_and_volume(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    g = align(*load_pair("NIFTY", "2026-08-04"))
    d = deltas(g)
    assert d["call_oi"] == [1_000_000.0, 700_000.0, -300_000.0]     # calls covered 300k over the window
    assert d["call_vol"][2] == 2600 - 1000                          # cumulative call volume traded
    assert d["put_oi"][2] == 0.0                                    # put OI flat


def test_true_score_ignition_from_covering(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    g = align(*load_pair("NIFTY", "2026-08-04"))
    x = crossovers(g)[0]
    sc = true_score(g, x["direction"], from_epoch=x["epoch"])
    assert sc["itm_side"] == "call"                                 # up-cross → calls are the pain side
    assert sc["cover_frac"] < 0                                     # covering (fuel), not defending
    assert sc["score"] >= 60 and "TRUE" in sc["grade"]             # covering + premium expansion = TRUE
    assert sc["itm_doi"] < 0 and sc["itm_dvol"] > 0                # ITM ΔOI down, Δvol up


def test_graded_crossovers_and_summary(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    gx = graded_crossovers(align(*load_pair("NIFTY", "2026-08-04")))
    assert len(gx) == 1 and gx[0]["direction"] == "up" and gx[0]["score"] >= 60
    s = summary("NIFTY", "2026-08-04")
    assert s["strike"] == 24000.0 and s["n_crossovers"] == 1
    assert "TRUE" in s["blast_grade"] and s["blast_score"] >= 60
    assert s["deltas"]["call_oi"][2] == -300_000.0
    assert s["call_oi"] == [1_000_000.0, 700_000.0]                 # back-compat (first,last) shape


def test_load_pair_excludes_wall_rows(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, wall_up_oi=(2_100_000, 2_100_000))
    call, put = load_pair("NIFTY", "2026-08-04")
    assert set(call["strike"]) == {24000.0} and set(call["role"]) == {"anchor"}   # no 24050 wall CE
    wu, wd = load_walls("NIFTY", "2026-08-04")
    assert wu is not None and set(wu["strike"]) == {24050.0} and wd is None


def test_intact_wall_caps_the_score(tmp_path, monkeypatch):
    """A big, non-eroding up-wall (call OI 3× the crossed strike, flat) removes the clear-path component,
    lowering the TRUE score vs the no-wall read."""
    _write(tmp_path, monkeypatch, wall_up_oi=(2_100_000, 2_100_000))
    g = align(*load_pair("NIFTY", "2026-08-04"))
    wu, _ = load_walls("NIFTY", "2026-08-04")
    x = crossovers(g)[0]
    no_wall = true_score(g, x["direction"], from_epoch=x["epoch"])
    with_wall = true_score(g, x["direction"], from_epoch=x["epoch"], wall_up=wu)
    assert no_wall["has_wall"] is False and with_wall["has_wall"] is True
    assert with_wall["wall_ratio"] == 3.0                          # 2.1M / 0.7M crossed-strike OI
    assert with_wall["score"] < no_wall["score"]                   # intact wall caps the move
    s = summary("NIFTY", "2026-08-04")
    assert s["walls"]["up"] == 24050.0 and s["walls"]["dn"] is None
    assert s["crossovers"][0]["has_wall"] is True


def test_absent_day_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "RAW_DIR", tmp_path)
    assert summary("NIFTY", "2099-01-01") is None
    assert load_pair("NIFTY", "2099-01-01") == (None, None)
