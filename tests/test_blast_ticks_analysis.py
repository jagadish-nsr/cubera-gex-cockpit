"""Hand-checks for app/blast_ticks_analysis.py — parity-reconstructed crossover on synthetic captured
ticks (put-rich → call-rich = spot crossing K upward)."""
import pandas as pd

from app.blast_ticks import _COLS
import app.blast_ticks as bt
from app.blast_ticks_analysis import load_pair, align, crossovers, summary


def _row(epoch, side, ltp, oi=1_000_000, bid=None, ask=None, strike=24000.0):
    return {"epoch": epoch, "time_ist": f"13:{45 + epoch % 15:02d}:00", "symbol": "NIFTY",
            "side": side, "strike": strike, "expiry": "2026-08-04", "code": 8, "ltp": ltp,
            "ltq": 75, "atp": ltp, "volume": 1000, "oi": oi,
            "bid": bid if bid is not None else ltp - 0.5, "ask": ask if ask is not None else ltp + 0.5}


def _write(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "RAW_DIR", tmp_path)
    rows = []
    # put richer first (spot below K): call 90 / put 110 → diff −20 ; async: CE even epochs, PE odd
    for e in range(0, 6, 2):
        rows.append(_row(e, "CE", 90.0)); rows.append(_row(e + 1, "PE", 110.0))
    # then call richer (spot above K): call 115 / put 95 → diff +20  → one 'up' crossover between
    for e in range(6, 12, 2):
        rows.append(_row(e, "CE", 115.0)); rows.append(_row(e + 1, "PE", 95.0))
    p = tmp_path / "NIFTY" / "2026-08-04.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows)[_COLS].to_csv(p, index=False)


def test_load_and_align(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    call, put = load_pair("NIFTY", "2026-08-04")
    assert call is not None and put is not None and len(call) == 6 and len(put) == 6
    g = align(call, put)
    assert g is not None and {"diff", "implied_spot", "call_ltp", "put_ltp"} <= set(g.columns)
    # parity: implied_spot = K + (call − put); early ≈ 23980, late ≈ 24020
    assert g["implied_spot"].min() == 24000.0 + (90.0 - 110.0)      # 23980
    assert g["implied_spot"].max() == 24000.0 + (115.0 - 95.0)      # 24020


def test_crossover_detected_upward(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    g = align(*load_pair("NIFTY", "2026-08-04"))
    xs = crossovers(g)
    assert len(xs) == 1 and xs[0]["direction"] == "up"             # put-rich → call-rich = spot up through K
    # flagged at the FIRST bar diff turns positive (call updated to 115, put still ffilled 110 → +5)
    assert xs[0]["implied_spot"] == 24005.0


def test_summary(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    s = summary("NIFTY", "2026-08-04")
    assert s["strike"] == 24000.0 and s["expiry"] == "2026-08-04"
    assert s["n_call"] == 6 and s["n_put"] == 6 and s["n_crossovers"] == 1
    assert s["call_ltp_range"] == [90.0, 115.0] and s["put_ltp_range"] == [95.0, 110.0]
    assert s["call_spread_med"] == 1.0                             # ask−bid = (ltp+.5)−(ltp−.5)
    assert s["implied_spot_range"] == [23980.0, 24020.0]


def test_absent_day_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "RAW_DIR", tmp_path)
    assert summary("NIFTY", "2099-01-01") is None
    assert load_pair("NIFTY", "2099-01-01") == (None, None)
