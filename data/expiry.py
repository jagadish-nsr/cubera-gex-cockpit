"""NSE expiry calendar + window filtering.

Replaces the US "third Friday" logic with NSE rules: weekly expiries and a
monthly expiry historically on the **last Thursday** of the month.

⚠️ VERIFY LIVE: SEBI/NSE revised weekly/monthly expiry rules in 2024–2025
(expiry day has shifted). This module implements the last-Thursday convention as
a documented STARTING POINT — confirm the current rules before trusting it, and
update WEEKLY_EXPIRY_WEEKDAY if NSE has moved the weekly. Holiday adjustment
(roll to previous trading day when the expiry is an NSE holiday) is a TODO and
needs the NSE trading-holiday calendar.
"""

from __future__ import annotations

from datetime import date, timedelta

VALID_WINDOWS = {"0dte", "weekly", "monthly", "all", "0dte_to_next_monthly"}

# Monday=0 .. Sunday=6. Thursday=3. VERIFY against current NSE rules.
WEEKLY_EXPIRY_WEEKDAY = 3  # Thursday


def last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Last <weekday> of the given month (e.g. last Thursday)."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)  # last day of month
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def next_monthly_expiry(ref: date) -> date:
    """Next monthly (last-Thursday) expiry on or after ref.

    NOTE: does NOT yet apply NSE holiday roll-back — TODO once the holiday
    calendar is wired.
    """
    this_month = last_weekday_of_month(ref.year, ref.month, WEEKLY_EXPIRY_WEEKDAY)
    if this_month >= ref:
        return this_month
    ny, nm = (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)
    return last_weekday_of_month(ny, nm, WEEKLY_EXPIRY_WEEKDAY)


def next_weekly_expiry(ref: date) -> date:
    """Next weekly expiry weekday on or after ref."""
    return ref + timedelta(days=(WEEKLY_EXPIRY_WEEKDAY - ref.weekday()) % 7)


def filter_expiry_window(contracts, window: str, ref: date):
    """Return only contracts whose expiry falls inside the named window.

    Mirrors the SPX adapter's interface so the rest of the engine is unchanged.
    `contracts` is a DataFrame with an 'expiry' (datetime.date) column.
    """
    if window not in VALID_WINDOWS:
        raise ValueError(f"unknown expiry window {window!r}; valid: {sorted(VALID_WINDOWS)}")
    exp = contracts["expiry"]
    if window == "all":
        mask = exp >= ref
    elif window == "0dte":
        mask = exp == ref
    elif window == "weekly":
        mask = (exp >= ref) & (exp <= next_weekly_expiry(ref))
    elif window in ("monthly", "0dte_to_next_monthly"):
        mask = (exp >= ref) & (exp <= next_monthly_expiry(ref))
    else:  # pragma: no cover
        raise AssertionError(window)
    return contracts[mask].reset_index(drop=True)
