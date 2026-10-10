"""resilience — IOAS v3.0 Resiliency & Failure Protection Subsystem.

Components:
  - circuit_breaker: Three-State Circuit Breaker (CLOSED / OPEN / HALF_OPEN)
  - validator: Pre-Analytics Validation Gatekeeper
  - lkgs: Last Known Good Snapshot Manager
  - freeze: Derivative Freeze Rule
  - adaptive_poller: Adaptive Polling Rate & Dynamic Throttling
"""

from __future__ import annotations

from resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenException,
    CircuitState,
    get_circuit_breaker,
    reset_all_circuits,
)
from resilience.validator import (
    ValidationResult,
    validate_snapshot,
)
from resilience.lkgs import (
    LKGSManager,
    get_lkgs_manager,
)
from resilience.freeze import (
    apply_derivative_freeze,
)
from resilience.adaptive_poller import (
    AdaptivePoller,
    get_adaptive_poller,
)

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerOpenException",
    "CircuitState",
    "get_circuit_breaker",
    "reset_all_circuits",
    "ValidationResult",
    "validate_snapshot",
    "LKGSManager",
    "get_lkgs_manager",
    "apply_derivative_freeze",
    "AdaptivePoller",
    "get_adaptive_poller",
]
