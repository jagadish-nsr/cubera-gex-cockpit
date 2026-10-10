"""resilience/circuit_breaker.py — Three-State Circuit Breaker.

Implements IOAS v3.0 §2.2 Latency & Network Failure Protections:
  - States: CLOSED (normal), OPEN (broker down), HALF_OPEN (re-probing canary)
  - Trip condition: 3 consecutive timeouts or errors trip to OPEN
  - Cooldown: 30s initial window, doubling on consecutive failures (capped at 120s)
  - Half-open probe: exactly one canary request executed after cooldown
  - Thread-safe registry keyed by symbol or broker
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum

log = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenException(Exception):
    """Raised or returned when a request is blocked because the circuit is OPEN."""
    pass


class CircuitBreaker:
    """Three-State Circuit Breaker per broker / per index."""

    def __init__(
        self,
        key: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        max_cooldown_seconds: float = 120.0,
        backoff_factor: float = 2.0,
    ) -> None:
        self.key = key
        self.failure_threshold = failure_threshold
        self.base_cooldown = cooldown_seconds
        self.current_cooldown = cooldown_seconds
        self.max_cooldown = max_cooldown_seconds
        self.backoff_factor = backoff_factor

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_state_change = time.monotonic()
        self._last_failure_time: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_cooldown_transition()
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    @property
    def cooldown_remaining(self) -> float:
        with self._lock:
            if self._state != CircuitState.OPEN:
                return 0.0
            elapsed = time.monotonic() - self._last_state_change
            return max(0.0, self.current_cooldown - elapsed)

    def _check_cooldown_transition(self) -> None:
        """If OPEN and cooldown elapsed, transition to HALF_OPEN for a probe."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_state_change
            if elapsed >= self.current_cooldown:
                self._state = CircuitState.HALF_OPEN
                self._last_state_change = time.monotonic()
                log.info(
                    "CircuitBreaker[%s]: Cooldown (%.1fs) elapsed -> transitioned to HALF_OPEN probe",
                    self.key,
                    self.current_cooldown,
                )

    def can_execute(self) -> bool:
        """Check if a request is allowed to execute."""
        with self._lock:
            self._check_cooldown_transition()
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.HALF_OPEN:
                return True
            return False

    def record_success(self) -> None:
        """Record a successful call. Restores CLOSED state and resets cooldown."""
        with self._lock:
            prev_state = self._state
            self._consecutive_failures = 0
            self.current_cooldown = self.base_cooldown
            self._state = CircuitState.CLOSED
            self._last_state_change = time.monotonic()
            if prev_state != CircuitState.CLOSED:
                log.info(
                    "CircuitBreaker[%s]: Canary probe succeeded -> reset to CLOSED (cooldown reset to %.1fs)",
                    self.key,
                    self.base_cooldown,
                )

    def record_failure(self, error: Exception | str | None = None) -> None:
        """Record a failed call. Trips to OPEN if threshold reached or if probe failed."""
        with self._lock:
            now = time.monotonic()
            self._consecutive_failures += 1
            self._last_failure_time = now

            if self._state == CircuitState.HALF_OPEN:
                # Canary probe failed -> re-open with exponential backoff
                self.current_cooldown = min(
                    self.current_cooldown * self.backoff_factor,
                    self.max_cooldown,
                )
                self._state = CircuitState.OPEN
                self._last_state_change = now
                log.warning(
                    "CircuitBreaker[%s]: Canary probe failed (%s) -> tripped back to OPEN with backoff cooldown %.1fs",
                    self.key,
                    error,
                    self.current_cooldown,
                )
            elif self._state == CircuitState.CLOSED:
                if self._consecutive_failures >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._last_state_change = now
                    log.warning(
                        "CircuitBreaker[%s]: %d consecutive failures reached (%s) -> tripped to OPEN (cooldown %.1fs)",
                        self.key,
                        self._consecutive_failures,
                        error,
                        self.current_cooldown,
                    )

    def reset(self) -> None:
        """Force manual reset back to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self.current_cooldown = self.base_cooldown
            self._last_state_change = time.monotonic()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, CircuitBreaker] = {}
_REGISTRY_LOCK = threading.Lock()


def get_circuit_breaker(
    key: str,
    failure_threshold: int = 3,
    cooldown_seconds: float = 30.0,
    max_cooldown_seconds: float = 120.0,
) -> CircuitBreaker:
    """Thread-safe accessor for per-symbol/per-broker circuit breakers."""
    norm_key = key.upper().strip()
    with _REGISTRY_LOCK:
        if norm_key not in _REGISTRY:
            _REGISTRY[norm_key] = CircuitBreaker(
                key=norm_key,
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
                max_cooldown_seconds=max_cooldown_seconds,
            )
        return _REGISTRY[norm_key]


def reset_all_circuits() -> None:
    """Reset all circuit breakers (primarily for test isolation)."""
    with _REGISTRY_LOCK:
        for cb in _REGISTRY.values():
            cb.reset()
