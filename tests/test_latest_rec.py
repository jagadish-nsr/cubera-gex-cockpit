"""Shared Ideas latch pickle for EXTERNAL serve (GO must not see empty latch)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import web_server
from app.latest_rec import (
    clear_latest_rec,
    read_latest_rec,
    write_latest_rec,
)


@dataclass
class _FakeBatch:
    symbol: str
    spot: float
    executive_summary: str = "test"
    higher_ce_recommendations: list = field(default_factory=list)
    ce_recommendations: list = field(default_factory=list)
    pe_recommendations: list = field(default_factory=list)
    higher_pe_recommendations: list = field(default_factory=list)
    lower_ce_recommendations: list = field(default_factory=list)
    lower_pe_recommendations: list = field(default_factory=list)
    tactical_ce_recommendations: list = field(default_factory=list)
    tactical_pe_recommendations: list = field(default_factory=list)
    bookmarked_recommendations: list = field(default_factory=list)


def test_write_read_roundtrip(tmp_path: Path):
    batch = _FakeBatch(symbol="NIFTY", spot=25000.0)
    write_latest_rec("NIFTY", batch, window="current_week", directory=tmp_path)
    loaded = read_latest_rec("NIFTY", window="current_week", directory=tmp_path)
    assert loaded is not None
    assert loaded.symbol == "NIFTY"
    assert loaded.spot == 25000.0


def test_stale_rec_rejected(tmp_path: Path):
    batch = _FakeBatch(symbol="NIFTY", spot=25000.0)
    path = write_latest_rec("NIFTY", batch, directory=tmp_path)
    # Backdate written_at by rewriting payload age via read after monkeypatch is heavy —
    # instead write then sleep past max_age with tiny threshold.
    loaded = read_latest_rec("NIFTY", directory=tmp_path, max_age_s=0.01)
    time.sleep(0.02)
    assert read_latest_rec("NIFTY", directory=tmp_path, max_age_s=0.01) is None
    assert path.exists()


def test_wrong_window_rejected(tmp_path: Path):
    write_latest_rec("NIFTY", _FakeBatch("NIFTY", 1.0), window="next_week", directory=tmp_path)
    assert read_latest_rec("NIFTY", window="current_week", directory=tmp_path) is None


def test_latest_rec_batch_falls_back_to_disk(tmp_path: Path, monkeypatch):
    web_server._REC_BATCH.clear()
    batch = _FakeBatch(symbol="BANKNIFTY", spot=52000.0)
    write_latest_rec("BANKNIFTY", batch, window="current_week", directory=tmp_path)
    monkeypatch.setattr("app.latest_rec.DEFAULT_DIR", tmp_path)
    monkeypatch.setattr(
        "app.latest_rec.rec_path_for",
        lambda symbol, directory=tmp_path: tmp_path / f"latest_rec_{symbol}.pkl",
    )
    # Force read_latest_rec to use tmp_path
    monkeypatch.setattr(
        "app.latest_rec.read_latest_rec",
        lambda symbol, window=None, directory=tmp_path, max_age_s=120.0: read_latest_rec(
            symbol, window=window, directory=tmp_path, max_age_s=max_age_s,
        ),
    )
    got = web_server._latest_rec_batch("BANKNIFTY", "current_week")
    assert got is not None
    assert got.spot == 52000.0
    # Second call hits memory
    assert ("BANKNIFTY", "current_week") in web_server._REC_BATCH
    web_server._REC_BATCH.clear()
    clear_latest_rec("BANKNIFTY", directory=tmp_path)


def test_ensure_rec_publisher_noop_external(monkeypatch):
    monkeypatch.setenv("COCKPIT_INGEST_EXTERNAL", "1")
    web_server._REC_THREAD = None
    web_server._ensure_rec_publisher()
    assert web_server._REC_THREAD is None
    monkeypatch.delenv("COCKPIT_INGEST_EXTERNAL", raising=False)
