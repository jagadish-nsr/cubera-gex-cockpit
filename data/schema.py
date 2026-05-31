"""Common options-snapshot schema shared by all data adapters.

Keeping one schema (rather than passing vendor-specific dicts around) is what
lets the engine stay vendor-agnostic and lets every output carry the same
provenance fields: OI timestamp, dealer-sign model, expiry window, source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

# Canonical per-contract columns every adapter must produce.
#   expiry : datetime.date  (contract expiration)
#   strike : float
#   type   : "C" | "P"
#   oi     : float          (open interest; EOD-lagging)
#   iv     : float          (implied vol, decimal; may be NaN -> derive/flag)
#   gamma  : float          (vendor gamma, decimal; cross-check only, may be NaN)
#   mid    : float          (contract mid price; used to back out IV if missing)
#   T      : float          (years to expiry at fetched_at; filled by adapter)
CONTRACT_COLUMNS = ["expiry", "strike", "type", "oi", "iv", "gamma", "mid", "T"]


@dataclass
class OptionsSnapshot:
    underlying: str               # e.g. "I:SPX"
    spot: float                   # underlying index level
    contracts: pd.DataFrame       # columns per CONTRACT_COLUMNS
    oi_timestamp: datetime        # when the OI was last updated (EOD-lagging)
    dealer_sign_model: str
    expiry_window: str
    source: str                   # e.g. "polygon", "fixture"
    fetched_at: datetime

    def validate(self) -> None:
        """Fail loud on a malformed snapshot (CLAUDE.md #6)."""
        missing = set(CONTRACT_COLUMNS) - set(self.contracts.columns)
        if missing:
            raise ValueError(f"snapshot contracts missing columns: {sorted(missing)}")
        if self.spot is None or self.spot <= 0:
            raise ValueError(f"invalid spot: {self.spot!r}")
        if self.contracts.empty:
            raise ValueError("snapshot has no contracts (empty chain)")

    def oi_age_hours(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        ts = self.oi_timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now - ts).total_seconds() / 3600.0

    def oi_is_stale(self, threshold_hours: float, now: datetime | None = None) -> bool:
        return self.oi_age_hours(now) > threshold_hours
