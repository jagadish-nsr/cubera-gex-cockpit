"""SQLite file rotation for CryptoCubera local paper recordings.

When the live DB exceeds ``rotate_max_bytes``, rename it to a timestamped
sidecar and keep the newest ``rotate_keep`` archives. Call **before**
opening the engine (or after disposing it) — never mid-write.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 512 * 1024 * 1024  # 500 MiB
DEFAULT_KEEP = 5


def _archive_glob(path: Path) -> list[Path]:
    # cubera.db → cubera.20261122T071900Z.db (+ optional -wal/-shm left behind)
    stem = path.stem  # cubera
    parent = path.parent
    return sorted(
        (
            p
            for p in parent.glob(f"{stem}.*.db")
            if p.is_file() and p.name != path.name
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def maybe_rotate_sqlite(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    keep: int = DEFAULT_KEEP,
    force: bool = False,
) -> dict[str, Any]:
    """Rotate ``path`` when oversized (or ``force``). Returns a status dict."""
    result: dict[str, Any] = {
        "path": str(path),
        "rotated": False,
        "archive": None,
        "size_bytes": 0,
        "max_bytes": int(max_bytes),
        "keep": int(keep),
        "pruned": [],
    }
    if max_bytes <= 0:
        result["note"] = "rotation disabled (max_bytes <= 0)"
        return result
    if not path.exists():
        result["note"] = "no file yet"
        return result

    size = path.stat().st_size
    result["size_bytes"] = size
    if not force and size < max_bytes:
        result["note"] = "under threshold"
        return result

    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = path.with_name(f"{path.stem}.{stamp}{path.suffix}")
    # Avoid collision if called twice in the same second
    n = 1
    while archive.exists():
        archive = path.with_name(f"{path.stem}.{stamp}-{n}{path.suffix}")
        n += 1

    # Move main file; orphan WAL/SHM if present (safe after dispose / cold start)
    shutil.move(str(path), str(archive))
    for suffix in ("-wal", "-shm"):
        side = Path(str(path) + suffix)
        if side.exists():
            try:
                side.unlink()
            except OSError as exc:
                log.warning("could not remove sqlite sidecar %s: %s", side, exc)

    result["rotated"] = True
    result["archive"] = str(archive)
    log.info(
        "Rotated CryptoCubera SQLite %s (%d bytes) → %s",
        path,
        size,
        archive,
    )

    # Prune oldest beyond keep
    archives = _archive_glob(path)
    # Newly created archive is included
    pruned: list[str] = []
    for old in archives[keep:]:
        try:
            old.unlink()
            pruned.append(str(old))
        except OSError as exc:
            log.warning("could not prune archive %s: %s", old, exc)
    result["pruned"] = pruned
    result["note"] = "rotated"
    return result


def rotation_settings(cfg: Optional[dict[str, Any]] = None) -> tuple[int, int]:
    """Read rotate_max_bytes / rotate_keep from config database block."""
    db = (cfg or {}).get("database") or {}
    max_bytes = int(db.get("rotate_max_bytes") or DEFAULT_MAX_BYTES)
    keep = int(db.get("rotate_keep") or DEFAULT_KEEP)
    return max_bytes, keep
