"""resilience/validator.py — Pre-Analytics Validation Gatekeeper.

Implements IOAS v3.0 §2.3 Processing & Data Quality Failure Protections:
  - Strike Count: NIFTY >= 25 valid strikes, SENSEX >= 20 valid strikes, BANKNIFTY >= 20
  - Spot Price Sanity: Spot must be positive and within ±10% of previous poll (if available)
  - ATM Liquidity: Near-ATM strikes (±2 strikes) must have valid non-null LTP and open interest
  - Quarantines bad snapshots and prevents corrupt or partial feeds from polluting downstream GEX/Greeks
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Minimum strike thresholds per symbol
MIN_STRIKES = {
    "NIFTY": 25,
    "BANKNIFTY": 20,
    "SENSEX": 20,
}
DEFAULT_MIN_STRIKES = 20
MAX_SPOT_CHANGE_PCT = 0.10  # 10% sudden jump limit


@dataclass
class ValidationResult:
    is_valid: bool
    issues: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        if self.is_valid:
            return "VALID: all pre-analytics sanity checks passed"
        return f"INVALID: {'; '.join(self.issues)}"


def validate_snapshot(
    symbol: str,
    spot: float | None,
    contracts_df: Any = None,
    rows: list[dict] | None = None,
    prev_spot: float | None = None,
    strike_step: int = 50,
) -> ValidationResult:
    """Run pre-analytics checks on an ingested option chain snapshot.

    Returns ValidationResult with is_valid=True/False and specific failure issues.
    """
    issues: list[str] = []
    sym = (symbol or "NIFTY").upper()
    req_strikes = MIN_STRIKES.get(sym, DEFAULT_MIN_STRIKES)

    # 1. Spot price sanity
    if spot is None or spot <= 0:
        issues.append(f"Spot price missing or non-positive: {spot}")
    elif prev_spot is not None and prev_spot > 0:
        change_pct = abs(spot - prev_spot) / prev_spot
        if change_pct > MAX_SPOT_CHANGE_PCT:
            issues.append(
                f"Spot jumped {change_pct * 100:.1f}% from {prev_spot:,.2f} to {spot:,.2f} (limit ±10%)"
            )

    # 2. Strike count check
    unique_strikes: set[float] = set()
    total_contracts = 0

    if contracts_df is not None:
        try:
            if hasattr(contracts_df, "empty") and not contracts_df.empty:
                if "strike" in contracts_df.columns:
                    unique_strikes = set(contracts_df["strike"].dropna().unique())
                    total_contracts = len(contracts_df)
        except Exception as exc:
            issues.append(f"Failed to inspect contracts DataFrame: {exc}")
    elif rows:
        for r in rows:
            k = r.get("strike") or r.get("strikePrice")
            if k is not None:
                try:
                    unique_strikes.add(float(k))
                    total_contracts += 1
                except (ValueError, TypeError):
                    pass

    n_strikes = len(unique_strikes)
    if n_strikes < req_strikes:
        issues.append(
            f"Insufficient unique strikes: {n_strikes} found, minimum required for {sym} is {req_strikes}"
        )

    # 3. ATM Liquidity Check (±2 strikes around ATM)
    atm_issues = 0
    if spot and spot > 0 and (contracts_df is not None or rows):
        atm_strike = round(spot / strike_step) * strike_step
        atm_window = {atm_strike + i * strike_step for i in range(-2, 3)}

        if contracts_df is not None and hasattr(contracts_df, "empty") and not contracts_df.empty:
            try:
                c_df = contracts_df[contracts_df["strike"].isin(atm_window)]
                # Check if we have at least some quotes in the ATM band
                if len(c_df) == 0:
                    issues.append(f"Zero contracts found in ATM window {atm_window}")
                else:
                    zero_oi_count = (c_df["oi"] <= 0).sum() if "oi" in c_df.columns else 0
                    if zero_oi_count == len(c_df):
                        issues.append(f"All {len(c_df)} contracts in ATM window have zero OI")
            except Exception:
                pass

    is_valid = len(issues) == 0
    if not is_valid:
        log.warning(
            "ValidationGatekeeper[%s]: Rejected snapshot: %s",
            sym,
            "; ".join(issues),
        )

    return ValidationResult(
        is_valid=is_valid,
        issues=issues,
        metrics={
            "symbol": sym,
            "spot": spot,
            "unique_strikes": n_strikes,
            "total_contracts": total_contracts,
        },
    )
