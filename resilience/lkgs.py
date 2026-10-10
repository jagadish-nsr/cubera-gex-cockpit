"""resilience/lkgs.py — Last Known Good Snapshot (LKGS) Manager.

Implements IOAS v3.0 §2.2 Last Known Good Snapshot & Graceful Degradation:
  - Preserves the last validated, complete snapshot per symbol
  - When broker call fails or circuit is OPEN, immediately serves the LKGS
  - Tags snapshot with status="DEGRADED", is_degraded=True, staleness_seconds=N, degraded_reason="..."
  - Protects against total UI blackout during intermittent API drops
"""

from __future__ import annotations

import copy
import logging
import threading
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


class SnapshotEntry:
    def __init__(self, data: Any, stored_at: datetime) -> None:
        self.data = data
        self.stored_at = stored_at


class LKGSManager:
    """In-memory thread-safe registry of Last Known Good Snapshots."""

    def __init__(self, max_allowed_staleness_sec: float = 7200.0) -> None:
        self._registry: dict[str, SnapshotEntry] = {}
        self._prev_spot: dict[str, float] = {}
        self._lock = threading.Lock()
        self.max_allowed_staleness_sec = max_allowed_staleness_sec

    def store(self, symbol: str, data: Any, fetched_at: datetime | None = None) -> None:
        """Store a valid snapshot as the latest LKGS for symbol."""
        if data is None:
            return
        sym = symbol.upper().strip()
        ts = fetched_at or datetime.now(timezone.utc)
        with self._lock:
            self._registry[sym] = SnapshotEntry(data=data, stored_at=ts)
            # Track spot for jump validation
            spot = getattr(getattr(data, "levels", None), "spot", None)
            if spot and spot > 0:
                self._prev_spot[sym] = float(spot)
            log.debug("LKGS[%s]: Stored new valid snapshot as of %s", sym, ts.isoformat())

    def get_prev_spot(self, symbol: str) -> float | None:
        """Get previous known spot for validation checks."""
        sym = symbol.upper().strip()
        with self._lock:
            return self._prev_spot.get(sym)

    def get_degraded_snapshot(
        self,
        symbol: str,
        reason: str = "Broker API unavailable",
    ) -> tuple[Any | None, bool, float, str]:
        """Retrieve LKGS for symbol and tag it with DEGRADED metadata.

        Returns:
            (snapshot, is_degraded, staleness_seconds, reason)
        """
        sym = symbol.upper().strip()
        with self._lock:
            entry = self._registry.get(sym)

        if entry is None or entry.data is None:
            return None, False, 0.0, "No previous snapshot available"

        now = datetime.now(timezone.utc)
        stored_ts = entry.stored_at
        if stored_ts.tzinfo is None:
            stored_ts = stored_ts.replace(tzinfo=timezone.utc)

        staleness = (now - stored_ts).total_seconds()
        # Shallow copy or wrapper to avoid mutating the pristine stored reference
        try:
            degraded_data = copy.copy(entry.data)
        except Exception:
            degraded_data = entry.data

        # Tag degraded attributes
        setattr(degraded_data, "is_degraded", True)
        setattr(degraded_data, "staleness_seconds", staleness)
        setattr(degraded_data, "degraded_reason", reason)
        setattr(degraded_data, "status", "DEGRADED")

        log.warning(
            "LKGS[%s]: Serving DEGRADED snapshot (%.1fs stale, reason: %s)",
            sym,
            staleness,
            reason,
        )
        return degraded_data, True, staleness, reason

    def get(self, symbol: str) -> Any | None:
        """Get the stored snapshot data for symbol if available, else None."""
        sym = symbol.upper().strip()
        with self._lock:
            entry = self._registry.get(sym)
            if entry is not None:
                return entry.data
            return None

    def get_entry(self, symbol: str) -> SnapshotEntry | None:
        """Get the SnapshotEntry wrapper with stored_at timestamp."""
        sym = symbol.upper().strip()
        with self._lock:
            return self._registry.get(sym)

    def has_snapshot(self, symbol: str) -> bool:
        sym = symbol.upper().strip()
        with self._lock:
            return sym in self._registry

    def clear(self, symbol: str | None = None) -> None:
        with self._lock:
            if symbol is not None:
                sym = symbol.upper().strip()
                self._registry.pop(sym, None)
                self._prev_spot.pop(sym, None)
            else:
                self._registry.clear()
                self._prev_spot.clear()



# Module singleton
_GLOBAL_LKGS = LKGSManager()


def get_lkgs_manager() -> LKGSManager:
    return _GLOBAL_LKGS
