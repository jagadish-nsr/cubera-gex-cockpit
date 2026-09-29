"""Hand-checks for app/blast_compare.py — the WS-vs-REST per-expiry comparison journal (upsert + deltas)."""
import app.blast_compare as bc


def _reads():
    ws = {"strike": 24000.0, "cross": True, "score": 80, "grade": "TRUE-leaning",
          "cross_time": "13:52:00", "cover_frac": -0.3, "wall_ratio": 1.2}
    rest = {"K": 24000.0, "cross": True, "score": 65, "grade": "WEAK / unconfirmed",
            "cross_time": "13:53:20", "cover_frac": -0.1, "wall_ratio": 1.5}
    return ws, rest


def test_log_computes_deltas(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "DIR", tmp_path)
    ws, rest = _reads()
    bc.log("2026-08-04", "NIFTY", ws, rest, "13:55:00")
    df = bc.load("NIFTY")
    assert len(df) == 1
    r = df.iloc[0]
    assert r["d_score"] == 15                                  # 80 − 65 (WS scores higher)
    assert r["d_cross_s"] == -80                               # WS crossed 80s earlier (13:52:00 vs 13:53:20)
    assert r["k_ws"] == 24000.0 and r["k_rest"] == 24000.0


def test_upsert_same_day_then_append_new(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "DIR", tmp_path)
    ws, rest = _reads()
    bc.log("2026-08-04", "NIFTY", ws, rest, "13:55:00")
    bc.log("2026-08-04", "NIFTY", {**ws, "score": 90}, rest, "13:56:00")   # same day → overwrite
    df = bc.load("NIFTY")
    assert len(df) == 1 and df.iloc[0]["d_score"] == 25       # 90 − 65
    bc.log("2026-08-11", "NIFTY", ws, rest, "13:55:00")       # next expiry → append
    df = bc.load("NIFTY")
    assert len(df) == 2 and sorted(df["day"]) == ["2026-08-04", "2026-08-11"]


def test_one_lane_uncrossed_leaves_deltas_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "DIR", tmp_path)
    ws, _ = _reads()
    bc.log("2026-08-04", "NIFTY", ws, {"K": 24000.0, "cross": False}, "13:55:00")
    r = bc.load("NIFTY").iloc[0]
    import pandas as pd
    assert pd.isna(r["d_score"]) and pd.isna(r["d_cross_s"])  # no delta until BOTH lanes cross
    assert bool(r["ws_cross"]) is True and bool(r["rest_cross"]) is False


def test_load_absent_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "DIR", tmp_path)
    assert bc.load("NIFTY") is None
