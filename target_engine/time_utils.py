"""Time handling utilities for Target Engine (T0 spec).

Handles fractional T (time-to-expiry in annual units), Indian Standard Time (IST)
wall-clock conversions, horizon clipping, and trading session calculations.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Union

# Indian Standard Time (UTC+05:30)
IST_OFFSET = timezone(timedelta(hours=5, minutes=30))
EXPIRY_TIME_IST = time(15, 30, 0)
SECONDS_PER_YEAR = 365.0 * 86400.0
MIN_ANNUAL_T = 1e-6  # Minimum positive T to prevent division by zero near expiry


def to_ist(dt: Optional[datetime] = None) -> datetime:
    """Ensure datetime is timezone-aware and set to IST."""
    if dt is None:
        return datetime.now(IST_OFFSET)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST_OFFSET)
    return dt.astimezone(IST_OFFSET)


def parse_expiry_datetime(expiry: Union[str, date, datetime]) -> datetime:
    """Parse an expiry date or datetime string into a 15:30:00 IST datetime.

    Supported formats:
    - '2026-10-15' (YYYY-MM-DD)
    - '15-Oct-2026' or '15-OCT-2026' (DD-Mon-YYYY)
    - '2026-10-15 15:30:00'
    - date or datetime instances
    """
    if isinstance(expiry, datetime):
        return to_ist(expiry)
    if isinstance(expiry, date):
        return to_ist(datetime.combine(expiry, EXPIRY_TIME_IST))

    cleaned = expiry.strip()
    # Try ISO YYYY-MM-DD
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d%b%Y"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            if fmt == "%Y-%m-%d %H:%M:%S":
                return to_ist(parsed)
            return to_ist(datetime.combine(parsed.date(), EXPIRY_TIME_IST))
        except ValueError:
            continue

    raise ValueError(f"Unrecognized expiry format: '{expiry}'")


def calculate_fractional_t(
    now: Union[datetime, str],
    expiry: Union[datetime, date, str],
    day_count: float = 365.0,
) -> float:
    """Calculate time to expiry T in annualized calendar years.

    Uses exact fractional seconds between now and 15:30 IST on the expiry date.
    Guarantees result is at least MIN_ANNUAL_T.
    """
    now_dt = to_ist(datetime.fromisoformat(now)) if isinstance(now, str) else to_ist(now)
    exp_dt = parse_expiry_datetime(expiry)

    diff_seconds = (exp_dt - now_dt).total_seconds()
    if diff_seconds <= 0.0:
        return MIN_ANNUAL_T

    return max(diff_seconds / (day_count * 86400.0), MIN_ANNUAL_T)


def calculate_horizon_t(
    now: datetime,
    horizon_minutes: int,
    expiry: Union[datetime, date, str],
    day_count: float = 365.0,
) -> tuple[datetime, float]:
    """Calculate the target datetime and remaining annualized T at the end of the horizon.

    Horizon end is strictly clipped to min(now + horizon, expiry_datetime).
    Returns (horizon_end_datetime, remaining_annual_t).
    """
    now_dt = to_ist(now)
    exp_dt = parse_expiry_datetime(expiry)

    unclipped_end = now_dt + timedelta(minutes=horizon_minutes)
    horizon_end = min(unclipped_end, exp_dt)

    remaining_seconds = (exp_dt - horizon_end).total_seconds()
    remaining_t = max(remaining_seconds / (day_count * 86400.0), MIN_ANNUAL_T)

    return horizon_end, remaining_t


def get_horizon_annual_duration(horizon_minutes: int, day_count: float = 365.0) -> float:
    """Convert horizon duration in minutes to fractional calendar years."""
    return max((horizon_minutes * 60.0) / (day_count * 86400.0), 1e-8)
