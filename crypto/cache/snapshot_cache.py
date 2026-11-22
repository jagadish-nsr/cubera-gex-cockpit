"""Per-underlying in-memory snapshot cache (no I/O on read)."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional


class SnapshotCache:
    """Hot-path store for fully built cockpit payloads keyed by underlying."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._payloads: dict[str, dict[str, Any]] = {}
        self._built_at: dict[str, float] = {}
        self._build_ms: dict[str, float] = {}

    def get(self, underlying: str) -> Optional[dict[str, Any]]:
        key = (underlying or "").strip().upper()
        with self._lock:
            payload = self._payloads.get(key)
            if payload is None:
                return None
            # Shallow top-level copy so callers can attach timing without mutating cache.
            return dict(payload)

    def put(self, underlying: str, payload: dict[str, Any], *, build_ms: float) -> None:
        key = (underlying or "").strip().upper()
        with self._lock:
            # Store a shallow copy of the top level; nested structures treated immutable.
            self._payloads[key] = dict(payload)
            self._built_at[key] = time.time()
            self._build_ms[key] = float(build_ms)

    def invalidate(self, underlying: str | None = None) -> None:
        with self._lock:
            if underlying is None:
                self._payloads.clear()
                self._built_at.clear()
                self._build_ms.clear()
                return
            key = underlying.strip().upper()
            self._payloads.pop(key, None)
            self._built_at.pop(key, None)
            self._build_ms.pop(key, None)

    def keys(self) -> list[str]:
        with self._lock:
            return sorted(self._payloads.keys())

    def meta(self, underlying: str) -> dict[str, Any]:
        key = (underlying or "").strip().upper()
        with self._lock:
            return {
                "underlying": key,
                "present": key in self._payloads,
                "built_at": self._built_at.get(key),
                "build_ms": self._build_ms.get(key),
                "age_s": (time.time() - self._built_at[key]) if key in self._built_at else None,
            }
