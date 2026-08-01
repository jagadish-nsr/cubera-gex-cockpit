"""Hand-checks for the pure parts of app/blast_ticks.py — contract keying, row assembly, archive round
trip. The WS loop itself needs a live socket (not unit-tested here)."""
from types import SimpleNamespace as NS

from app.blast_ticks import pair_contracts, tick_row, BlastTickCollector, load_blast_ticks


def _pair():
    return {"symbol": "NIFTY", "strike": 24000.0, "expiry": "2026-08-04",
            "call": {"security_id": 65693, "side": "CE", "segment": "NSE_FNO", "strike": 24000.0},
            "put": {"security_id": 65694, "side": "PE", "segment": "NSE_FNO", "strike": 24000.0}}


def test_pair_contracts_keys_both_legs():
    c = pair_contracts(_pair())
    assert set(c) == {65693, 65694}
    assert c[65693]["side"] == "CE" and c[65694]["side"] == "PE"
    assert c[65693]["symbol"] == "NIFTY" and c[65693]["strike"] == 24000.0
    assert c[65693]["expiry"] == "2026-08-04" and c[65693]["segment"] == "NSE_FNO"
    assert pair_contracts(None) == {}


def test_tick_row_shape_and_ist():
    c = pair_contracts(_pair())[65693]
    tick = NS(code=8, ltp=142.5, ltq=75, atp=140.0, volume=120000, oi=5_000_000, bid=142.0, ask=143.0)
    epoch = 1785200000                                        # a fixed epoch → deterministic HH:MM:SS (IST)
    r = tick_row(tick, c, epoch)
    assert r["side"] == "CE" and r["strike"] == 24000.0 and r["ltp"] == 142.5
    assert r["bid"] == 142.0 and r["ask"] == 143.0 and r["oi"] == 5_000_000 and r["code"] == 8
    assert len(r["time_ist"]) == 8 and r["time_ist"][2] == ":"    # HH:MM:SS
    assert r["epoch"] == epoch


def test_archive_round_trip(tmp_path, monkeypatch):
    import app.blast_ticks as bt
    monkeypatch.setattr(bt, "RAW_DIR", tmp_path)
    col = BlastTickCollector(pair_contracts(_pair()))
    assert col.status.strike == 24000.0 and col.status.expiry == "2026-08-04"
    c = col.contracts[65693]
    col._add("NIFTY", "2026-08-04", tick_row(NS(code=8, ltp=100.0, ltq=75, atp=99.0, volume=10,
             oi=1, bid=99.5, ask=100.5), c, 1785200000))
    col._flush(force=True)
    df = load_blast_ticks("NIFTY", "2026-08-04")
    assert df is not None and len(df) == 1
    assert df.iloc[0]["side"] == "CE" and df.iloc[0]["ltp"] == 100.0
    assert load_blast_ticks("NIFTY", "2099-01-01") is None    # absent day → None


def test_empty_collector_is_safe():
    col = BlastTickCollector({})
    col.run()                                                  # no contracts → returns cleanly, no throw
    assert col.status.running is False and "no blast pair" in (col.status.error or "")
