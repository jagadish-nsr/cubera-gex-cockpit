"""resilience/adaptive_poller.py — Adaptive Polling Rate (Dynamic Throttling).

Implements IOAS v3.0 §2.2 Adaptive Polling Rate:
  - Tracks rolling 5-poll Round Trip Time (RTT)
  - If rolling RTT > 2.0s -> scales interval from 3.5s up to 6.0s or 10.0s
  - Once RTT stabilizes below 1.2s for 3 consecutive polls -> recovers back to 3.5s
  - Mitigates broker API rate-limiting, network congestion, and thread starvation
"""

from __future__ import annotations

import collections
import logging
import threading

log = logging.getLogger(__name__)

DEFAULT_BASELINE_INTERVAL_SEC = 3.5
HIGH_RTT_THRESHOLD_SEC = 2.0
STABLE_RTT_THRESHOLD_SEC = 1.2
TIER1_INTERVAL_SEC = 6.0
TIER2_INTERVAL_SEC = 10.0
RECOVERY_CONSECUTIVE_COUNT = 3


class AdaptivePoller:
    """Calculates adaptive polling interval based on broker network latency (RTT)."""

    def __init__(
        self,
        symbol: str,
        baseline_interval: float = DEFAULT_BASELINE_INTERVAL_SEC,
        window_size: int = 5,
    ) -> None:
        self.symbol = symbol.upper().strip()
        self.baseline_interval = baseline_interval
        self.window_size = window_size
        self._rtts: collections.deque[float] = collections.deque(maxlen=window_size)
        self._current_interval = baseline_interval
        self._consecutive_stable_polls = 0
        self._lock = threading.Lock()

    @property
    def current_interval(self) -> float:
        with self._lock:
            return self._current_interval

    @property
    def rolling_rtt(self) -> float:
        with self._lock:
            if not self._rtts:
                return 0.0
            return sum(self._rtts) / len(self._rtts)

    def record_rtt(self, rtt_seconds: float) -> float:
        """Record an observed round-trip time in seconds and return the updated poll interval."""
        with self._lock:
            self._rtts.append(float(rtt_seconds))
            avg_rtt = sum(self._rtts) / len(self._rtts)

            # Check if this poll is stable
            if rtt_seconds < STABLE_RTT_THRESHOLD_SEC:
                self._consecutive_stable_polls += 1
                if (
                    self._consecutive_stable_polls >= RECOVERY_CONSECUTIVE_COUNT
                    and self._current_interval > self.baseline_interval
                ):
                    log.info(
                        "AdaptivePoller[%s]: Latency recovered (%.2fs RTT, %d stable polls) -> restoring baseline interval %.1fs",
                        self.symbol,
                        rtt_seconds,
                        self._consecutive_stable_polls,
                        self.baseline_interval,
                    )
                    self._current_interval = self.baseline_interval
            else:
                self._consecutive_stable_polls = 0

            # Check if high latency throttling needed (only when not in stable recovery)
            if self._consecutive_stable_polls == 0 and len(self._rtts) >= 3 and avg_rtt > HIGH_RTT_THRESHOLD_SEC:
                if avg_rtt > 3.0:
                    new_interval = TIER2_INTERVAL_SEC  # 10.0s
                else:
                    new_interval = TIER1_INTERVAL_SEC  # 6.0s

                if new_interval != self._current_interval:
                    log.warning(
                        "AdaptivePoller[%s]: High RTT (%.2fs avg) -> throttling interval from %.1fs to %.1fs",
                        self.symbol,
                        avg_rtt,
                        self._current_interval,
                        new_interval,
                    )
                    self._current_interval = new_interval

            return self._current_interval

    def reset(self) -> None:
        with self._lock:
            self._rtts.clear()
            self._current_interval = self.baseline_interval
            self._consecutive_stable_polls = 0


# Module registry
_POLLERS: dict[str, AdaptivePoller] = {}
_POLLER_LOCK = threading.Lock()


def get_adaptive_poller(symbol: str) -> AdaptivePoller:
    sym = symbol.upper().strip()
    with _POLLER_LOCK:
        if sym not in _POLLERS:
            _POLLERS[sym] = AdaptivePoller(symbol=sym)
        return _POLLERS[sym]
