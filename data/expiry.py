"""NSE/BSE expiry calendar + window filtering.

Replaces the US "third Friday" logic with Indian exchange rules.

NSE (Nifty, Bank Nifty):
  - Weekly expiry: TUESDAY (weekday=1) — per SEBI's exchange-day split.
  - Monthly expiry: last TUESDAY of the month.
  ⚠️ VERIFY LIVE: SEBI/NSE may revise the rule again. Confirmed from a live NSE
  endpoint capture: option-chain-v3 served expiry=30-Jun-2026, which is a Tuesday.

BSE (Sensex):
  - Weekly expiry: THURSDAY (weekday=3) — BSE SENSEX weekly options.
  - Monthly expiry: last THURSDAY of the month.
  ⚠️ VERIFY LIVE with BSE circulars.

Holiday adjustment: when the natural expiry falls on an exchange holiday, the
exchange moves expiry to the PRECEDING trading day. This module now resolves that
using the live NSE F&O holiday calendar (data/holidays.py). The calendar is fetched
once per day and cached locally, so no network hit on most reruns.
"""

from __future__ import annotations

from datetime import date, timedelta

VALID_WINDOWS = {"0dte", "weekly", "monthly", "all", "0dte_to_next_monthly"}

# Monday=0 .. Sunday=6.
NSE_WEEKLY_EXPIRY_WEEKDAY = 1   # Tuesday  — NSE Nifty / Bank Nifty
BSE_WEEKLY_EXPIRY_WEEKDAY = 3   # Thursday — BSE Sensex

# Back-compat alias (existing code uses WEEKLY_EXPIRY_WEEKDAY without a prefix)
WEEKLY_EXPIRY_WEEKDAY = NSE_WEEKLY_EXPIRY_WEEKDAY


def _expiry_weekday(exchange: str = "NSE") -> int:
    """Return the weekly-expiry weekday for the given exchange."""
    return BSE_WEEKLY_EXPIRY_WEEKDAY if exchange.upper() == "BSE" else NSE_WEEKLY_EXPIRY_WEEKDAY


def _holiday_roll(d: date) -> date:
    """Roll d backward to the nearest trading day if it falls on a holiday.

    Uses the NSE F&O holiday calendar (data/holidays.py). Gracefully falls back
    to the original date if the holiday module is unavailable.
    """
    try:
        from data.holidays import roll_back_to_trading_day
        return roll_back_to_trading_day(d)
    except Exception:
        return d   # degrade gracefully — no roll if calendar unavailable


def last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Last <weekday> of the given month (e.g. last Thursday)."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)  # last day of month
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def next_monthly_expiry(ref: date, exchange: str = "NSE") -> date:
    """Next monthly (last-weekday) expiry on or after ref, holiday-adjusted.

    For NSE (Nifty/BankNifty): last Tuesday of the month.
    For BSE (Sensex): last Thursday of the month.

    If the natural last-weekday falls on an exchange holiday, rolls back to the
    preceding trading day (NSE/BSE practice).
    """
    wd = _expiry_weekday(exchange)
    this_month = _holiday_roll(last_weekday_of_month(ref.year, ref.month, wd))
    if this_month >= ref:
        return this_month
    ny, nm = (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)
    return _holiday_roll(last_weekday_of_month(ny, nm, wd))


def next_weekly_expiry(ref: date, exchange: str = "NSE") -> date:
    """Next weekly expiry weekday on or after ref, holiday-adjusted.

    When the natural weekly expiry date is an exchange holiday, the exchange
    moves it to the preceding trading day (same as monthly). This can occasionally
    shift a Tuesday (NIFTY) or Thursday (SENSEX) expiry to Monday/Wednesday.
    """
    wd = _expiry_weekday(exchange)
    raw = ref + timedelta(days=(wd - ref.weekday()) % 7)
    adjusted = _holiday_roll(raw)
    # If holiday roll pushed the adjusted date BEFORE ref, advance by one week
    if adjusted < ref:
        raw2 = raw + timedelta(weeks=1)
        adjusted = _holiday_roll(raw2)
    return adjusted


def describe_window(window: str, ref: date, exchange: str = "NSE",
                    has_weekly: bool = True) -> str:
    """Return a human-readable string describing the active expiry window.

    Example: 'weekly → Tue 14-Jul-2026' or 'monthly → Tue 28-Jul-2026'
    Used by the dashboard to prominently show which expiry date is being analyzed.
    """
    if window == "0dte":
        return f"0DTE → {ref.strftime('%a %d-%b-%Y')}"
    elif window == "weekly":
        exp = (next_weekly_expiry(ref, exchange) if has_weekly
               else next_monthly_expiry(ref, exchange))
        tag = "(collapsed to monthly — no weeklies)" if not has_weekly else ""
        return f"weekly → {exp.strftime('%a %d-%b-%Y')} {tag}".strip()
    elif window in ("monthly", "0dte_to_next_monthly"):
        exp = next_monthly_expiry(ref, exchange)
        return f"monthly → {exp.strftime('%a %d-%b-%Y')}"
    elif window == "all":
        return "all expiries"
    return window


def filter_expiry_window(contracts, window: str, ref: date,
                         has_weekly: bool = True, exchange: str = "NSE"):
    """Return only contracts whose expiry falls inside the named window.

    Mirrors the SPX adapter's interface so the rest of the engine is unchanged.
    `contracts` is a DataFrame with an 'expiry' (datetime.date) column.

    ``has_weekly=False`` (e.g. Bank Nifty, monthly-only since Nov-2024): the
    "weekly" window collapses to the next MONTHLY expiry, since there is no weekly
    Tuesday contract to bound it — without this the weekly window would silently
    return an empty chain (the next weekly Tuesday has no Bank Nifty series).

    ``exchange`` selects the expiry weekday convention (\"NSE\" = Tuesday,
    \"BSE\" = Thursday). Pass the Index.exchange value here.
    """
    if window not in VALID_WINDOWS:
        raise ValueError(f"unknown expiry window {window!r}; valid: {sorted(VALID_WINDOWS)}")
    exp = contracts["expiry"]
    if window == "all":
        mask = exp >= ref
    elif window == "0dte":
        mask = exp == ref
    elif window == "weekly":
        end = (next_weekly_expiry(ref, exchange) if has_weekly
               else next_monthly_expiry(ref, exchange))
        mask = (exp >= ref) & (exp <= end)
    elif window in ("monthly", "0dte_to_next_monthly"):
        mask = (exp >= ref) & (exp <= next_monthly_expiry(ref, exchange))
    else:  # pragma: no cover
        raise AssertionError(window)
    return contracts[mask].reset_index(drop=True)
