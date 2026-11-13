"""Atomic write of ``data/journal/latest_snap_{SYMBOL}.pkl``.

Two publishers (the live fetcher and the GEX daemon) can finish a chain in the
same second. A shared ``.pkl.tmp`` name means the second ``os.replace`` finds
an empty path. Each write gets its own temp file in the same directory.

A sibling ``.meta.json`` carries ``fetched_at_epoch`` so EXTERNAL serve readiness
can age the book without unpickling the dashboard on ``/api/health``.
"""

from __future__ import annotations

import json
import os
import pickle
import tempfile
import time
from pathlib import Path
from typing import Any


def snap_path_for(symbol: str, directory: str | Path = "data/journal") -> Path:
    return Path(directory) / f"latest_snap_{symbol.upper()}.pkl"


def meta_path_for(symbol: str, directory: str | Path = "data/journal") -> Path:
    return Path(directory) / f"latest_snap_{symbol.upper()}.meta.json"


def write_latest_snap(path: str | Path, payload: dict[str, Any]) -> None:
    """Pickle ``payload`` to ``path`` via a unique temp file, then replace."""
    snap_path = Path(path)
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{snap_path.name}.",
        suffix=".tmp",
        dir=str(snap_path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, snap_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_snap_meta(
    symbol: str,
    *,
    fetched_at_epoch: float,
    window: str | None = None,
    degraded: bool = False,
    degraded_reason: str = "",
    directory: str | Path = "data/journal",
) -> Path:
    """Atomic JSON sidecar for readiness probes (no unpickle)."""
    path = meta_path_for(symbol, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": (symbol or "").upper().strip(),
        "fetched_at_epoch": float(fetched_at_epoch),
        "window": window,
        "degraded": bool(degraded),
        "degraded_reason": str(degraded_reason or ""),
        "written_at_epoch": time.time(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_snap_meta(
    symbol: str,
    *,
    directory: str | Path = "data/journal",
) -> dict[str, Any] | None:
    """Read snap meta JSON. Returns None when missing or malformed."""
    path = meta_path_for(symbol, directory)
    if not path.exists():
        # Fall back to pickle mtime only — age signal without loading the book.
        snap = snap_path_for(symbol, directory)
        if not snap.exists():
            return None
        try:
            return {
                "symbol": (symbol or "").upper().strip(),
                "fetched_at_epoch": float(snap.stat().st_mtime),
                "window": None,
                "degraded": False,
                "degraded_reason": "",
                "source": "pickle_mtime",
            }
        except OSError:
            return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "fetched_at_epoch" not in data:
            return None
        return data
    except Exception:
        return None
