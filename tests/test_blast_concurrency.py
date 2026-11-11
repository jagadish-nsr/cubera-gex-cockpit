"""Stress the blast score + OI-tape locks for stalls / lost updates / deadlocks.

These are wall-clock bounded: if a flock or lock order deadlocks, pytest hits
the timeout (or the join timeout below) rather than hanging the suite forever.
"""
from __future__ import annotations

import csv
import json
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _worker_score(args):
    """Process worker: many journal_blast_read calls for the same IST minute."""
    tmp, n, proc_id = args
    import app.gammablast as gb

    gb.BLAST_SCORE_DIR = Path(tmp)
    gb._score_written.clear()
    fetched = datetime(2026, 11, 9, 8, 15, tzinfo=timezone.utc)  # 13:45 IST
    wrote = 0
    for i in range(n):
        ok = gb.journal_blast_read(
            "NIFTY",
            25000.0 + proc_id + i * 0.01,
            {
                "score": 50 + (proc_id % 40),
                "state": "TRIGGERED" if i % 2 == 0 else "NO_TRIGGER",
                "primary_strike": 25000,
                "badge": "GAMMA BLAST: ACTIVE",
                "call_wall": 25000,
                "put_wall": 24900,
                "in_blast_window": True,
                "cover_frac": -0.1,
                "cover_known": True,
            },
            fetched,
        )
        if ok:
            wrote += 1
    return wrote


def _worker_oi(args):
    """Process worker: shared OI tape appends."""
    tmp, n, proc_id = args
    import app.gammablast as gb

    gb.OI_TAPE_DIR = Path(tmp)
    gb.reset_strike_oi_history()
    t0 = datetime(2026, 11, 9, 13, 0, 0)  # naive IST wall
    for i in range(n):
        ts = t0 + timedelta(seconds=30 * i + proc_id)  # stagger seconds across procs
        gb.note_strike_oi(
            "NIFTY",
            [{"strike": 25000.0, "call_oi": 100_000.0 - 100 * i - proc_id, "put_oi": 1.0}],
            ts,
        )
    # Cover frac after last write should see a multi-sample tape
    frac = gb.window_cover_frac("NIFTY", 25000.0, "call", t0 + timedelta(seconds=30 * (n - 1) + 5))
    return {"frac": frac, "tape_exists": (Path(tmp) / "NIFTY.json").exists()}


@pytest.mark.timeout(20)
def test_score_journal_multi_process_one_row_per_minute(tmp_path):
    """Several processes race the same minute — exactly one CSV data row."""
    n_procs, n_calls = 6, 40
    with ProcessPoolExecutor(max_workers=n_procs) as pool:
        futs = [
            pool.submit(_worker_score, (str(tmp_path), n_calls, p))
            for p in range(n_procs)
        ]
        done, not_done = wait(futs, timeout=15)
        assert not not_done, "score writers stalled / deadlock suspected"
        writes = [f.result(timeout=1) for f in done]
    assert sum(writes) == 1
    path = tmp_path / "NIFTY" / "2026-11-09.csv"
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == 1
    assert rows[0]["time_ist"] == "13:45"


@pytest.mark.timeout(20)
def test_oi_tape_multi_process_no_stall_and_readable(tmp_path):
    """Several processes append the shared OI tape — finishes and stays valid JSON."""
    n_procs, n_calls = 4, 25
    with ProcessPoolExecutor(max_workers=n_procs) as pool:
        futs = [
            pool.submit(_worker_oi, (str(tmp_path), n_calls, p))
            for p in range(n_procs)
        ]
        done, not_done = wait(futs, timeout=15)
        assert not not_done, "OI tape writers stalled / deadlock suspected"
        results = [f.result(timeout=1) for f in done]
    assert all(r["tape_exists"] for r in results)
    raw = json.loads((tmp_path / "NIFTY.json").read_text())
    assert isinstance(raw, list) and len(raw) >= 2
    # At least one worker should compute a cover fraction (history long enough)
    assert any(r["frac"] is not None for r in results)


@pytest.mark.timeout(15)
def test_interleaved_score_and_oi_threads_no_stall(tmp_path, monkeypatch):
    """Same process: threads hammer both lock domains (OI then score order)."""
    import app.gammablast as gb

    monkeypatch.setattr(gb, "BLAST_SCORE_DIR", tmp_path / "score")
    monkeypatch.setattr(gb, "OI_TAPE_DIR", tmp_path / "oi")
    gb.reset_strike_oi_history()
    gb._score_written.clear()

    errors: list[BaseException] = []

    def oi_loop():
        try:
            t0 = datetime(2026, 11, 9, 13, 0, 0)
            for i in range(30):
                gb.note_strike_oi(
                    "NIFTY",
                    [{"strike": 25000.0, "call_oi": 90_000.0 - i, "put_oi": 1.0}],
                    t0 + timedelta(seconds=i * 25),
                )
                gb.window_cover_frac("NIFTY", 25000.0, "call", t0 + timedelta(seconds=i * 25 + 5))
        except BaseException as exc:  # noqa: BLE001 — collect for main thread
            errors.append(exc)

    def score_loop(thread_id: int):
        try:
            for i in range(30):
                fetched = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
                gb.journal_blast_read(
                    "NIFTY",
                    25000.0,
                    {
                        "score": 40 + thread_id,
                        "state": "COILING",
                        "badge": "GAMMA BLAST: COILING / IMMINENT",
                        "in_blast_window": True,
                    },
                    fetched,
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(oi_loop) for _ in range(2)]
        futs += [pool.submit(score_loop, t) for t in range(4)]
        done, not_done = wait(futs, timeout=12)
        assert not not_done, "interleaved score/OI threads stalled"
        for f in done:
            f.result(timeout=1)
    assert not errors
    # One row per distinct minute from the score loops
    day = tmp_path / "score" / "NIFTY" / "2026-11-09.csv"
    assert day.exists()
    rows = list(csv.DictReader(day.open()))
    minutes = [r["time_ist"] for r in rows]
    assert len(minutes) == len(set(minutes))
