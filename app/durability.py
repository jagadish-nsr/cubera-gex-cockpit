"""Data-plane durability helpers (Phase D / Pillar C).

- Nightly backups of key SQLite DBs + latest_snap pickles
- Restore into a *scratch* directory only (never live journal)
- Post-close WAL TRUNCATE when safe
- Cold-start: seed LKGS from on-disk pickles before first HTTP paint
"""

from __future__ import annotations

import logging
import pickle
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

# Relative to repo root. Missing files are skipped (not errors).
BACKUP_DB_RELS = (
    "data/sentinel.db",
    "data/journal/recommendations.db",
    "data/journal/paper_trades.db",
    "data/journal/admin_ops.db",
    "data/journal/market_snapshot.db",
    "data/journal/go_efficiency.db",
    "data/journal/commentary.db",
)

SNAP_GLOB = "latest_snap_*.pkl"
DEFAULT_BACKUP_ROOT = ROOT / "data" / "journal" / "backups"
DEFAULT_RETENTION_DAYS = 14


@dataclass
class BackupReport:
    stamp: str
    dest: Path
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.copied)


@dataclass
class RestoreReport:
    source: Path
    dest: Path
    copied: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.copied)


def _stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sqlite_safe_copy(src: Path, dest: Path) -> None:
    """Online backup via sqlite3 backup API (safe with WAL writers)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30.0)
    try:
        dst_con = sqlite3.connect(str(dest), timeout=30.0)
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()


def backup_paths(
    root: Path | None = None,
    *,
    backup_root: Path | None = None,
    stamp: str | None = None,
    include_snaps: bool = True,
) -> BackupReport:
    """Copy key DBs + latest snaps into ``backup_root/<stamp>/``."""
    root = (root or ROOT).resolve()
    backup_root = (backup_root or DEFAULT_BACKUP_ROOT).resolve()
    stamp = stamp or _stamp_now()
    dest = backup_root / stamp
    dest.mkdir(parents=True, exist_ok=True)
    report = BackupReport(stamp=stamp, dest=dest)

    for rel in BACKUP_DB_RELS:
        src = root / rel
        if not src.exists():
            report.skipped.append(rel)
            continue
        target = dest / Path(rel).name
        try:
            sqlite_safe_copy(src, target)
            report.copied.append(rel)
        except Exception as exc:
            report.errors.append(f"{rel}: {exc}")
            log.warning("backup failed for %s: %s", rel, exc)

    if include_snaps:
        journal = root / "data" / "journal"
        if journal.is_dir():
            for snap in sorted(journal.glob(SNAP_GLOB)):
                rel = f"data/journal/{snap.name}"
                try:
                    shutil.copy2(snap, dest / snap.name)
                    report.copied.append(rel)
                except Exception as exc:
                    report.errors.append(f"{rel}: {exc}")
                    log.warning("snap backup failed for %s: %s", snap, exc)

    manifest = dest / "MANIFEST.txt"
    lines = [
        f"stamp={stamp}",
        f"root={root}",
        f"copied={len(report.copied)}",
        f"skipped={len(report.skipped)}",
        f"errors={len(report.errors)}",
        "",
        "# copied",
        *[f"+ {p}" for p in report.copied],
        "",
        "# skipped",
        *[f"- {p}" for p in report.skipped],
        "",
        "# errors",
        *[f"! {e}" for e in report.errors],
    ]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def prune_backups(
    backup_root: Path | None = None,
    *,
    keep_days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
) -> list[str]:
    """Delete backup stamp dirs older than keep_days. Returns removed names."""
    backup_root = (backup_root or DEFAULT_BACKUP_ROOT).resolve()
    if not backup_root.is_dir():
        return []
    now = now or datetime.now(timezone.utc)
    removed: list[str] = []
    for child in sorted(backup_root.iterdir()):
        if not child.is_dir():
            continue
        # stamp dirs look like 20260327T120000Z
        try:
            created = datetime.strptime(child.name[:15], "%Y%m%dT%H%M%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        age_days = (now - created).total_seconds() / 86400.0
        if age_days > keep_days:
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    return removed


def assert_scratch_dest(dest: Path, root: Path | None = None) -> Path:
    """Refuse restore into the live data tree.

    Dest must resolve under a path whose name contains ``scratch`` (case-insensitive),
    and must not be the live ``data/`` or ``data/journal/`` directories.
    """
    root = (root or ROOT).resolve()
    dest = dest.expanduser().resolve()
    live_data = (root / "data").resolve()
    live_journal = (root / "data" / "journal").resolve()
    if dest == live_data or dest == live_journal:
        raise ValueError(f"refuse restore into live path: {dest}")
    if dest == root or dest == (root / "data" / "journal" / "backups").resolve():
        raise ValueError(f"refuse restore into {dest}; use a scratch subdirectory")
    # Block writing directly over live journal children unless under .../scratch...
    name_ok = "scratch" in dest.name.lower() or any(
        "scratch" in p.name.lower() for p in dest.parents
    )
    if not name_ok:
        raise ValueError(
            f"restore dest must include 'scratch' in the path name: {dest}"
        )
    if live_journal in dest.parents or dest == live_journal:
        # Allow only under data/journal/backups/scratch_* 
        backups = (root / "data" / "journal" / "backups").resolve()
        if backups not in dest.parents and dest != backups:
            raise ValueError(
                f"refuse restore into live journal tree outside backups/: {dest}"
            )
    return dest


def restore_dry_run(
    source: Path,
    dest: Path,
    *,
    root: Path | None = None,
) -> RestoreReport:
    """Copy a backup stamp directory into a scratch path. Never touches live DBs."""
    root = (root or ROOT).resolve()
    source = source.expanduser().resolve()
    dest = assert_scratch_dest(dest, root)
    if not source.is_dir():
        raise FileNotFoundError(f"backup source not a directory: {source}")
    dest.mkdir(parents=True, exist_ok=True)
    report = RestoreReport(source=source, dest=dest)
    for item in sorted(source.iterdir()):
        if item.name == "MANIFEST.txt":
            shutil.copy2(item, dest / item.name)
            report.copied.append(item.name)
            continue
        if not item.is_file():
            continue
        try:
            shutil.copy2(item, dest / item.name)
            report.copied.append(item.name)
        except Exception as exc:
            report.errors.append(f"{item.name}: {exc}")
    return report


def wal_checkpoint(
    db_path: Path,
    mode: str = "PASSIVE",
) -> tuple[int, int, int]:
    """Run PRAGMA wal_checkpoint(mode). Returns (busy, log, checkpointed)."""
    mode = mode.upper().strip()
    if mode not in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
        raise ValueError(f"unsupported checkpoint mode: {mode}")
    con = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        con.execute("PRAGMA busy_timeout=10000")
        row = con.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return int(row[0]), int(row[1]), int(row[2])
    finally:
        con.close()


def warm_lkgs_from_pickles(
    symbols: Iterable[str] = ("NIFTY", "BANKNIFTY", "SENSEX"),
    *,
    journal: Path | None = None,
) -> dict[str, str]:
    """Seed empty LKGS slots from ``latest_snap_*.pkl`` (no invent, no HTTP).

    Does not overwrite an existing in-memory LKGS entry. Does not write
    session_snapshots (persist=False seed path).
    """
    from app.latest_snap import snap_path_for
    from resilience.lkgs import get_lkgs_manager

    mgr = get_lkgs_manager()
    journal = journal or (ROOT / "data" / "journal")
    out: dict[str, str] = {}
    for sym in symbols:
        key = sym.upper().strip()
        if mgr.has_snapshot(key):
            out[key] = "already"
            continue
        path = snap_path_for(key, journal)
        if not path.exists():
            out[key] = "missing"
            continue
        try:
            with path.open("rb") as handle:
                raw = pickle.load(handle)
            bundle, fetched = _extract_bundle(raw)
            if bundle is None:
                out[key] = "invalid"
                continue
            mgr.seed(key, bundle, stored_at=fetched)
            out[key] = "warmed"
            log.info("LKGS cold-start warmed %s from %s", key, path.name)
        except Exception as exc:
            out[key] = f"error:{exc}"
            log.warning("LKGS warm failed for %s: %s", key, exc)
    return out


def _extract_bundle(raw: Any) -> tuple[Any | None, datetime | None]:
    bundle = raw
    fetched = None
    if isinstance(raw, dict) and "data" in raw:
        bundle = raw.get("data")
        fetched = raw.get("fetched_at")
    if bundle is None or not hasattr(bundle, "levels"):
        return None, None
    src = str(getattr(bundle, "source", "") or "").lower()
    if any(k in src for k in ("demo", "synthetic", "mock", "test")):
        return None, None
    spot = getattr(getattr(bundle, "levels", None), "spot", None)
    try:
        if spot is None or float(spot) <= 0:
            return None, None
    except (TypeError, ValueError):
        return None, None
    if isinstance(fetched, datetime):
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
    else:
        fetched = datetime.now(timezone.utc)
    return bundle, fetched
