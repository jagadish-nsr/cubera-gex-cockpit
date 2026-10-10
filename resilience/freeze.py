"""resilience/freeze.py — Derivative Freeze Rule.

Implements IOAS v3.0 §2.2 Derivative Freeze Rule:
  - During degraded or stale cycles, all flow derivatives:
      * OI Velocity -> frozen to 0.0
      * OI Acceleration -> frozen to 0.0
      * IV Rate-of-Change -> frozen to 0.0
  - Prevents stale timestamps from generating artificial volatility spikes or false divergence alerts
  - Clearly flags derivatives_frozen = True on output objects
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Staleness threshold beyond which derivatives MUST be frozen (seconds)
DERIVATIVE_FREEZE_STALENESS_SEC = 60.0


def apply_derivative_freeze(
    data: Any,
    force_freeze: bool = False,
    staleness_seconds: float = 0.0,
) -> Any:
    """Check if snapshot is degraded or stale, and freeze velocity/acceleration metrics."""
    if data is None:
        return data

    is_degraded = getattr(data, "is_degraded", False)
    stale_sec = getattr(data, "staleness_seconds", staleness_seconds)

    should_freeze = force_freeze or is_degraded or (stale_sec >= DERIVATIVE_FREEZE_STALENESS_SEC)

    if should_freeze:
        setattr(data, "derivatives_frozen", True)
        # Freeze OI strikes velocity if present
        oi_strikes = getattr(data, "oi_strikes", None)
        if oi_strikes and isinstance(oi_strikes, list):
            for s in oi_strikes:
                if isinstance(s, dict):
                    s["call_doi_velocity"] = 0.0
                    s["put_doi_velocity"] = 0.0
                    s["net_velocity"] = 0.0

        # Freeze raw aggregate velocities
        raw_agg = getattr(data, "raw_agg", None)
        if raw_agg and isinstance(raw_agg, dict):
            raw_agg["oi_velocity"] = 0.0
            raw_agg["oi_acceleration"] = 0.0
            raw_agg["iv_roc"] = 0.0

        log.info(
            "DerivativeFreezeRule: Derivatives frozen to 0.0 (degraded=%s, staleness=%.1fs)",
            is_degraded,
            stale_sec,
        )
    else:
        setattr(data, "derivatives_frozen", False)

    return data
