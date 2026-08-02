"""Hand-checks for app/blast_ticks_analysis.py — parity crossover + OI/volume deltas + TRUE score on
synthetic captured ticks (put-rich → call-rich = spot up through K; post-cross the CALL writers cover and
the call premium expands = a TRUE-leaning ignition)."""
import pandas as pd

from app.blast_ticks import _COLS
import app.blast_ticks as bt
from app.blast_ticks_analysis import (load_pair, align, crossovers, deltas, true_score,
                                      graded_crossovers, summary)


def _row(epoch, side, ltp, oi, vol, strike=24000.0):
    return {"epoch": epoch, "time_ist": f"13:{45 + epoch % 15:02d}:00", "symbol": "NIFTY",
            "side": side, "strike": strike, "expiry": "2026-08-04", "code": 8, "ltp": ltp,
            "ltq": 75, "atp": ltp, "volume": vol, "oi": oi, "bid": ltp - 0.5, "ask": ltp + 0.5}


def _write(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "RAW_DIR", tmp_path)
    rows = []
    # PRE-cross (put richer → spot below K): call 90 / put 110. CE even epochs, PE odd (async).
    # call OI loaded at 1.0M (writers short calls at K), volume climbing.
    for i, e in enumerate(range(0, 6, 2)):
        rows.append(_row(e, "CE", 90.0, oi=1_000_000, vol=1000 + i * 100))
        rows.append(_row(e + 1, "PE", 110.0, oi=800_000, vol=500 + i * 50))
    # POST-cross (call richer → spot above K): call 115→132 (premium EXPANDING), call OI 1.0M→0.7M
    # (writers COVERING = fuel), volume surging. put 95, put OI flat.
    for i, (e, cltp, coi, cvol) in enumerate([(6, 115.0, 1_000_000, 1400),
                                              (8, 124.0, 850_000, 1900),
                                              (10, 132.0, 700_000, 2600)]):
        rows.append(_row(e, "CE", cltp, oi=coi, vol=cvol))
        rows.append(_row(e + 1, "PE", 95.0, oi=800_000, vol=700 + i * 40))
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


def test_absent_day_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "RAW_DIR", tmp_path)
    assert summary("NIFTY", "2099-01-01") is None
    assert load_pair("NIFTY", "2099-01-01") == (None, None)
