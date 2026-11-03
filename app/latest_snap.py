"""Atomic write of ``data/journal/latest_snap_{SYMBOL}.pkl``.

Two publishers (the live fetcher and the GEX daemon) can finish a chain in the
same second. A shared ``.pkl.tmp`` name means the second ``os.replace`` finds
an empty path. Each write gets its own temp file in the same directory.
"""

from __future__ import annotations

import os
import pickle
import tempfile
from pathlib import Path
from typing import Any


def snap_path_for(symbol: str, directory: str | Path = "data/journal") -> Path:
    return Path(directory) / f"latest_snap_{symbol.upper()}.pkl"


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
