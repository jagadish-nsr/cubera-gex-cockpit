"""tests/test_resilience.py — Unit tests for IOAS v3.0 Resiliency & Failure Protection Subsystem.

Covers:
  - resilience.circuit_breaker (CLOSED, OPEN, HALF_OPEN, backoff)
  - resilience.validator (pre-analytics validation gatekeeper)
  - resilience.lkgs (Last Known Good Snapshot + DEGRADED tagging)
  - resilience.freeze (Derivative Freeze Rule)
  - resilience.adaptive_poller (dynamic RTT-based throttling)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest import mock

import pandas as pd
import pytest

from resilience import (
    AdaptivePoller,
    CircuitBreaker,
    CircuitBreakerOpenException,
    CircuitState,
    LKGSManager,
    apply_derivative_freeze,
    get_circuit_breaker,
    reset_all_circuits,
    validate_snapshot,
)


# ---------------------------------------------------------------------------
# 1. Circuit Breaker Tests
# ---------------------------------------------------------------------------

def test_circuit_breaker_initial_state():
    cb = CircuitBreaker(key="TEST_CB_1", failure_threshold=3, cooldown_seconds=10.0)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True
    assert cb.consecutive_failures == 0


def test_circuit_breaker_trips_to_open_after_threshold():
    cb = CircuitBreaker(key="TEST_CB_2", failure_threshold=3, cooldown_seconds=10.0)

    cb.record_failure("error 1")
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    cb.record_failure("error 2")
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    cb.record_failure("error 3")
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False
    assert cb.cooldown_remaining > 0.0


def test_circuit_breaker_half_open_probe_success():
    cb = CircuitBreaker(key="TEST_CB_3", failure_threshold=2, cooldown_seconds=0.1)
    cb.record_failure("err 1")
    cb.record_failure("err 2")
    assert cb.state == CircuitState.OPEN

    # Sleep past cooldown
    time.sleep(0.12)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.can_execute() is True

    # Successful probe resets to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 0
    assert cb.can_execute() is True


def test_circuit_breaker_half_open_probe_failure_exponential_backoff():
    cb = CircuitBreaker(
        key="TEST_CB_4",
        failure_threshold=1,
        cooldown_seconds=0.1,
        backoff_factor=2.0,
        max_cooldown_seconds=1.0,
    )
    cb.record_failure("err 1")
    assert cb.state == CircuitState.OPEN

    # Wait for cooldown -> HALF_OPEN
    time.sleep(0.12)
    assert cb.state == CircuitState.HALF_OPEN

    # Failed probe trips back to OPEN and doubles cooldown
    cb.record_failure("probe failed")
    assert cb.state == CircuitState.OPEN
    assert cb.current_cooldown >= 0.2


# ---------------------------------------------------------------------------
# 2. Validation Gatekeeper Tests
# ---------------------------------------------------------------------------

def test_validator_accepts_clean_snapshot():
    # Build 30 dummy strikes for NIFTY
    strikes = [24000.0 + i * 50 for i in range(30)]
    df = pd.DataFrame({
        "strike": strikes,
        "oi": [10000.0] * 30,
        "iv": [0.15] * 30,
    })
    res = validate_snapshot("NIFTY", spot=24500.0, contracts_df=df, strike_step=50)
    assert res.is_valid is True
    assert len(res.issues) == 0


def test_validator_rejects_insufficient_strikes():
    # Only 5 strikes (NIFTY requires >= 25)
    df = pd.DataFrame({
        "strike": [24500.0, 24550.0, 24600.0, 24650.0, 24700.0],
        "oi": [10000.0] * 5,
    })
    res = validate_snapshot("NIFTY", spot=24550.0, contracts_df=df)
    assert res.is_valid is False
    assert any("Insufficient unique strikes" in issue for issue in res.issues)


def test_validator_rejects_invalid_spot_and_excessive_jump():
    # Zero spot
    res1 = validate_snapshot("NIFTY", spot=0.0)
    assert res1.is_valid is False
    assert any("Spot price missing or non-positive" in issue for issue in res1.issues)

    # 15% sudden spot jump (limit 10%)
    res2 = validate_snapshot("NIFTY", spot=28000.0, prev_spot=24000.0)
    assert res2.is_valid is False
    assert any("Spot jumped" in issue for issue in res2.issues)


# ---------------------------------------------------------------------------
# 3. LKGS Manager Tests
# ---------------------------------------------------------------------------

def test_lkgs_store_and_degraded_retrieval():
    lkgs = LKGSManager()

    class DummyData:
        def __init__(self, spot: float):
            self.levels = mock.Mock(spot=spot)

    d = DummyData(spot=24600.0)
    t0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    lkgs.store("NIFTY", d, fetched_at=t0)

    assert lkgs.has_snapshot("NIFTY") is True
    assert lkgs.get_prev_spot("NIFTY") == 24600.0

    # Retrieve degraded snapshot
    degraded, is_deg, staleness, reason = lkgs.get_degraded_snapshot("NIFTY", reason="Broker timeout")
    assert is_deg is True
    assert degraded is not None
    assert degraded.status == "DEGRADED"
    assert degraded.is_degraded is True
    assert degraded.degraded_reason == "Broker timeout"
    assert staleness > 0.0


def test_lkgs_empty_symbol_returns_none():
    lkgs = LKGSManager()
    data, is_deg, _, _ = lkgs.get_degraded_snapshot("NON_EXISTENT")
    assert data is None
    assert is_deg is False


# ---------------------------------------------------------------------------
# 4. Derivative Freeze Rule Tests
# ---------------------------------------------------------------------------

def test_derivative_freeze_when_degraded():
    class MockSnap:
        def __init__(self):
            self.is_degraded = True
            self.staleness_seconds = 120.0
            self.oi_strikes = [
                {"strike": 24500, "call_doi_velocity": 150.0, "net_velocity": 200.0}
            ]
            self.raw_agg = {"oi_velocity": 500.0, "oi_acceleration": 25.0}

    snap = MockSnap()
    frozen = apply_derivative_freeze(snap)
    assert frozen.derivatives_frozen is True
    assert frozen.oi_strikes[0]["call_doi_velocity"] == 0.0
    assert frozen.oi_strikes[0]["net_velocity"] == 0.0
    assert frozen.raw_agg["oi_velocity"] == 0.0
    assert frozen.raw_agg["oi_acceleration"] == 0.0


def test_derivative_freeze_normal_state_unfrozen():
    class MockSnap:
        def __init__(self):
            self.is_degraded = False
            self.staleness_seconds = 5.0
            self.oi_strikes = [
                {"strike": 24500, "call_doi_velocity": 150.0, "net_velocity": 200.0}
            ]

    snap = MockSnap()
    res = apply_derivative_freeze(snap)
    assert res.derivatives_frozen is False
    assert res.oi_strikes[0]["call_doi_velocity"] == 150.0


# ---------------------------------------------------------------------------
# 5. Adaptive Poller Tests
# ---------------------------------------------------------------------------

def test_adaptive_poller_high_rtt_throttling_and_recovery():
    poller = AdaptivePoller(symbol="NIFTY", baseline_interval=3.5)
    assert poller.current_interval == 3.5

    # Record 3 slow RTTs > 2.0s
    poller.record_rtt(2.4)
    poller.record_rtt(2.5)
    interval = poller.record_rtt(2.6)
    assert interval == 6.0
    assert poller.current_interval == 6.0

    # 3 fast RTTs < 1.2s -> recovers back to baseline
    poller.record_rtt(0.8)
    poller.record_rtt(0.9)
    recovered = poller.record_rtt(0.7)
    assert recovered == 3.5
    assert poller.current_interval == 3.5
