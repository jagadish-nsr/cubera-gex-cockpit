"""Hand checks for Phase D durability helpers."""

from __future__ import annotations

import pickle
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.durability import (
    assert_scratch_dest,
    backup_paths,
    prune_backups,
    restore_dry_run,
    warm_lkgs_from_pickles,
)
from app.latest_snap import write_latest_snap
from resilience.lkgs import LKGSManager


def _make_db(path: Path, table: str = "t") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, v TEXT)")
    con.execute(f"INSERT INTO {table}(v) VALUES ('ok')")
    con.commit()
    con.close()


def test_backup_and_scratch_restore(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "data" / "journal").mkdir(parents=True)
    _make_db(root / "data" / "sentinel.db")
    _make_db(root / "data" / "journal" / "recommendations.db")
    snap = root / "data" / "journal" / "latest_snap_NIFTY.pkl"
    write_latest_snap(snap, {"data": "book", "symbol": "NIFTY", "fetched_at": "t"})

    backup_root = root / "data" / "journal" / "backups"
    report = backup_paths(root=root, backup_root=backup_root, stamp="20260327T120000Z")
    assert report.ok
    assert (report.dest / "sentinel.db").exists()
    assert (report.dest / "recommendations.db").exists()
    assert (report.dest / "latest_snap_NIFTY.pkl").exists()
    assert (report.dest / "MANIFEST.txt").exists()

    scratch = backup_root / "scratch_restore_test"
    restored = restore_dry_run(report.dest, scratch, root=root)
    assert restored.ok
    assert (scratch / "sentinel.db").exists()
    con = sqlite3.connect(str(scratch / "sentinel.db"))
    assert con.execute("SELECT v FROM t").fetchone()[0] == "ok"
    con.close()


def test_restore_refuses_live_journal(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    live = root / "data" / "journal"
    live.mkdir(parents=True)
    src = tmp_path / "backup_stamp"
    src.mkdir()
    (src / "sentinel.db").write_bytes(b"x")

    with pytest.raises(ValueError, match="live"):
        restore_dry_run(src, live, root=root)

    with pytest.raises(ValueError, match="scratch"):
        restore_dry_run(src, tmp_path / "restore_out", root=root)


def test_assert_scratch_allows_backups_scratch(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    dest = root / "data" / "journal" / "backups" / "scratch_x"
    dest.mkdir(parents=True)
    assert assert_scratch_dest(dest, root) == dest.resolve()


def test_prune_backups_by_age(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    old = backup_root / "20200101T000000Z"
    new = backup_root / "20990101T000000Z"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "x").write_text("old")
    (new / "x").write_text("new")
    removed = prune_backups(
        backup_root,
        keep_days=14,
        now=datetime(2026, 3, 27, tzinfo=timezone.utc),
    )
    assert "20200101T000000Z" in removed
    assert not old.exists()
    assert new.exists()


def test_warm_lkgs_from_pickles_seeds_empty_only(tmp_path: Path, monkeypatch) -> None:
    journal = tmp_path / "journal"
    journal.mkdir()
    levels = SimpleNamespace(spot=24500.0, call_wall=24600, put_wall=24400)
    bundle = SimpleNamespace(levels=levels, source="dhan-live")
    payload = {
        "data": bundle,
        "symbol": "NIFTY",
        "fetched_at": datetime(2026, 3, 26, 10, 0, tzinfo=timezone.utc),
    }
    path = journal / "latest_snap_NIFTY.pkl"
    with path.open("wb") as handle:
        pickle.dump(payload, handle)

    mgr = LKGSManager()
    monkeypatch.setattr("resilience.lkgs.get_lkgs_manager", lambda: mgr)

    out = warm_lkgs_from_pickles(("NIFTY", "BANKNIFTY"), journal=journal)
    assert out["NIFTY"] == "warmed"
    assert out["BANKNIFTY"] == "missing"
    assert mgr.has_snapshot("NIFTY")
    assert float(mgr.get("NIFTY").levels.spot) == 24500.0

    out2 = warm_lkgs_from_pickles(("NIFTY",), journal=journal)
    assert out2["NIFTY"] == "already"


def test_seed_does_not_overwrite(tmp_path: Path) -> None:
    mgr = LKGSManager()
    first = SimpleNamespace(levels=SimpleNamespace(spot=1.0), source="a")
    second = SimpleNamespace(levels=SimpleNamespace(spot=2.0), source="b")
    assert mgr.seed("NIFTY", first) is True
    assert mgr.seed("NIFTY", second) is False
    assert mgr.get("NIFTY").levels.spot == 1.0
