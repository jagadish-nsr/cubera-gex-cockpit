"""Shared Trade Ideas latch for dual ingest/serve.

Ingest owns ``rec.publisher`` and writes ``data/journal/latest_rec_{SYMBOL}.pkl``.
EXTERNAL serve has an empty in-process ``_REC_BATCH``; it must read this file so
GO / Ideas UI can latch without starting a second publisher (process-isolation).
"""

from __future__ import annotations

import logging
import os
import pickle
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_DIR = Path("data") / "journal"
# Ideas refresh ~20s; allow a short grace so serve does not flap empty.
DEFAULT_MAX_AGE_S = 120.0


def rec_path_for(symbol: str, directory: str | Path = DEFAULT_DIR) -> Path:
    return Path(directory) / f"latest_rec_{(symbol or '').upper().strip()}.pkl"


def write_latest_rec(
    symbol: str,
    batch: Any,
    *,
    window: str | None = None,
    directory: str | Path = DEFAULT_DIR,
) -> Path:
    """Atomic pickle of the live RecommendationBatch for serve latch reads."""
    path = rec_path_for(symbol, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": (symbol or "").upper().strip(),
        "window": window or "current_week",
        "written_at_epoch": time.time(),
        "batch": batch,
    }
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def clear_latest_rec(symbol: str, *, directory: str | Path = DEFAULT_DIR) -> None:
    path = rec_path_for(symbol, directory)
    try:
        path.unlink(missing_ok=True)
    except TypeError:
        # py3.9 Path.unlink(missing_ok=...) exists; keep defensive
        if path.exists():
            path.unlink()
    except OSError as exc:
        log.debug("clear_latest_rec %s: %s", symbol, exc)


def read_latest_rec(
    symbol: str,
    *,
    window: str | None = None,
    directory: str | Path = DEFAULT_DIR,
    max_age_s: float = DEFAULT_MAX_AGE_S,
) -> Any | None:
    """Return the shared RecommendationBatch, or None if missing/stale/wrong window."""
    path = rec_path_for(symbol, directory)
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            payload = pickle.load(handle)
    except Exception as exc:
        log.warning("latest_rec unreadable %s: %s", path, exc)
        return None
    if not isinstance(payload, dict) or payload.get("batch") is None:
        return None
    want = (window or "current_week")
    got = str(payload.get("window") or "current_week")
    if got != want:
        return None
    try:
        age = time.time() - float(payload.get("written_at_epoch") or 0.0)
    except (TypeError, ValueError):
        return None
    if max_age_s > 0 and age > max_age_s:
        return None
    return payload["batch"]
