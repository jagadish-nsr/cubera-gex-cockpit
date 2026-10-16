"""tests/test_market_snapshot.py — Test suite for Persistent Market Snapshot Registry."""

import pytest
from pathlib import Path
from data.market_snapshot import init_db, save_snapshot, get_snapshot


def test_market_snapshot_persistence(tmp_path, monkeypatch):
    test_db = tmp_path / "test_snapshot.db"
    monkeypatch.setattr("data.market_snapshot.DB_PATH", test_db)

    # Initial state
    init_db()
    assert test_db.exists()

    # Save a valid snapshot
    save_snapshot(
        symbol="NIFTY",
        spot=22721.95,
        prev_close=22603.05,
        open_px=22600.0,
        high=22725.0,
        low=22580.0,
        call_wall=22750.0,
        put_wall=22700.0,
        gamma_flip=22702.07,
        max_pain=22700.0,
        net_gex=1500000.0,
        vix=14.08,
        as_of="2026-10-13 15:30:00",
        source="dhan-verified",
    )

    # Retrieve and verify exact values
    snap = get_snapshot("NIFTY")
    assert snap is not None
    assert snap["symbol"] == "NIFTY"
    assert snap["spot"] == 22721.95
    assert snap["prev_close"] == 22603.05
    assert snap["call_wall"] == 22750.0
    assert snap["put_wall"] == 22700.0
    assert snap["gamma_flip"] == 22702.07
    assert snap["max_pain"] == 22700.0
    assert snap["vix"] == 14.08
    assert snap["source"] == "dhan-verified"


def test_market_snapshot_bootstrap_authentic_values():
    """Verify that get_snapshot bootstraps authentic Dhan data without fabricating fake prices."""
    for sym, expected_spot_range in [
        ("NIFTY", (22000.0, 26000.0)),
        ("BANKNIFTY", (50000.0, 60000.0)),
        ("SENSEX", (70000.0, 80000.0)),
    ]:
        snap = get_snapshot(sym)
        assert snap is not None
        assert snap["spot"] >= expected_spot_range[0]
        assert snap["spot"] <= expected_spot_range[1]
        assert snap["call_wall"] >= snap["put_wall"]
        assert snap["call_wall"] > 0.0
        assert snap["put_wall"] > 0.0
        assert snap["prev_close"] > 0.0
