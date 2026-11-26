"""Dhan 1-min backfill must store OI in the same unit (lots) as the live capture it sits beside."""

import pandas as pd

import app.timeseries as ts
from app.oi_backfill import backfill_oi_strikes_dhan


def _matrix():
    # 26-Nov Bank Nifty 64000, 09:18: rollingoption said 686,250 call / 459,450 put SHARES.
    return pd.DataFrame([
        {"time_ist": "09:18", "strike": 64000.0, "side": "CE", "oi": 686250.0, "close": 980.0, "spot": 63810.0},
        {"time_ist": "09:18", "strike": 64000.0, "side": "PE", "oi": 459450.0, "close": 1010.0, "spot": 63810.0},
    ])


def test_backfill_writes_lots_not_shares(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "OISTRIKES_DHAN_DIR", tmp_path)
    monkeypatch.setattr("data.dhan.fetch_option_matrix", lambda *a, **k: (_matrix(), []))
    out = backfill_oi_strikes_dhan("BANKNIFTY", "2026-11-26", log=lambda *_: None)
    assert out["rows"] == 1
    row = ts.load_strikes_day("2026-11-26", "BANKNIFTY", directory=tmp_path).iloc[0]
    assert row["call_oi"] == 22875.0          # 686,250 / 30
    assert row["put_oi"] == 15315.0           # 459,450 / 30
    assert row["call_oi_near"] == 22875.0
    assert row["put_oi_near"] == 15315.0


def test_backfill_refuses_without_a_lot_size(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "OISTRIKES_DHAN_DIR", tmp_path)
    monkeypatch.setattr("data.dhan.fetch_option_matrix", lambda *a, **k: (_matrix(), []))
    monkeypatch.setattr("data.dhan._lot_size_for_symbol", lambda symbol: 1)
    out = backfill_oi_strikes_dhan("BANKNIFTY", "2026-11-26", log=lambda *_: None)
    assert out["rows"] == 0
    assert not (tmp_path / "BANKNIFTY").exists()
