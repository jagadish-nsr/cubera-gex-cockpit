"""NSE/BSE expiry calendar + window filtering.

User-confirmed 2026-10-24:

  NIFTY      every Tuesday. Monthly is the last Tuesday of the month.
  BANKNIFTY  last Tuesday of the month only. No weeklies.
  SENSEX     every Thursday. Monthly is the last Thursday of the month.

If that date is an exchange holiday, expiry is the previous trading day
(data/holidays.py). It is not left on the holiday and it is not rolled forward.
Bank Nifty reaches the monthly date because Index.has_weekly is False, so the
weekly window collapses to next_monthly_expiry.
"""

from __future__ import annotations

from datetime import date, timedelta

VALID_WINDOWS = {
    "0dte",
    "weekly",
    "monthly",
    "all",
    "0dte_to_next_monthly",
    "current_week",
    "next_week",
}

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

    Uses the NSE F&O holiday calendar (data/holidays.py). Fail loud if the
    calendar cannot be loaded — returning an unrolled holiday date would put
    the wrong expiry on the decision screen.
    """
    from data.holidays import roll_back_to_trading_day
    return roll_back_to_trading_day(d)


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


def second_monthly_expiry(ref: date, exchange: str = "NSE") -> date:
    """Second upcoming monthly expiry after ref, holiday-adjusted."""
    m1 = next_monthly_expiry(ref, exchange)
    return next_monthly_expiry(m1 + timedelta(days=5), exchange)


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


def second_weekly_expiry(ref: date, exchange: str = "NSE") -> date:
    """Second upcoming weekly expiry after ref, holiday-adjusted."""
    w1 = next_weekly_expiry(ref, exchange)
    return next_weekly_expiry(w1 + timedelta(days=1), exchange)


def next_n_weekly_expiries(ref: date, n: int = 4, exchange: str = "NSE") -> list[date]:
    """Next ``n`` weekly expiries on or after ``ref``, holiday-adjusted.

    Used by the multi-expiry OI/volume study (Nifty Tuesday / Sensex Thursday).
    Does not invent weeklies for monthly-only underlyings — callers must gate on
    ``Index.has_weekly`` before calling.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    out: list[date] = []
    cursor = ref
    for _ in range(n):
        exp = next_weekly_expiry(cursor, exchange)
        out.append(exp)
        cursor = exp + timedelta(days=1)
    return out


def pick_next_weeklies_from_vendor(
    vendor_isos: list[str],
    ref: date,
    n: int = 4,
    *,
    exchange: str = "NSE",
) -> list[date]:
    """First ``n`` vendor expiries on/after ``ref``, cross-checked vs calendar weeklies.

    Vendor ``/expirylist`` is the listing truth near the front (weeklies). Far
    dates include monthlies/LEAPs — we only take the first ``n`` on/after ``ref``
    and warn (via return mismatch) if they disagree with ``next_n_weekly_expiries``.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    vendor_dates: list[date] = []
    for raw in vendor_isos:
        try:
            d = date.fromisoformat(str(raw)[:10])
        except (TypeError, ValueError):
            continue
        if d >= ref:
            vendor_dates.append(d)
    picked = vendor_dates[:n]
    calendar = next_n_weekly_expiries(ref, n, exchange)
    if picked and picked != calendar[: len(picked)]:
        # Prefer vendor listing when present; calendar is the expected shape check.
        return picked
    return calendar if not picked else picked


def describe_window(window: str, ref: date, exchange: str = "NSE",
                    has_weekly: bool = True) -> str:
    """Return a human-readable string describing the active expiry window.

    Example: 'weekly → Tue 14-Jul-2026' or 'monthly → Tue 28-Jul-2026'
    Used by the dashboard to prominently show which expiry date is being analyzed.
    """
    if window == "0dte":
        return f"0DTE → {ref.strftime('%a %d-%b-%Y')}"
    elif window in ("weekly", "current_week"):
        exp = (next_weekly_expiry(ref, exchange) if has_weekly
               else next_monthly_expiry(ref, exchange))
        tag = "(collapsed to monthly — no weeklies)" if not has_weekly else ""
        prefix = "current week" if window == "current_week" else "weekly"
        return f"{prefix} → {exp.strftime('%a %d-%b-%Y')} {tag}".strip()
    elif window == "next_week":
        exp = (second_weekly_expiry(ref, exchange) if has_weekly
               else second_monthly_expiry(ref, exchange))
        tag = "(next monthly — no weeklies)" if not has_weekly else ""
        return f"next week → {exp.strftime('%a %d-%b-%Y')} {tag}".strip()
    elif window in ("monthly", "0dte_to_next_monthly"):
        exp = next_monthly_expiry(ref, exchange)
        return f"monthly → {exp.strftime('%a %d-%b-%Y')}"
    elif window == "all":
        return "all expiries"
    try:
        d = date.fromisoformat(window)
        return d.strftime('%a %d-%b-%Y')
    except (ValueError, TypeError):
        return window


def filter_expiry_window(contracts, window: str, ref: date,
                         has_weekly: bool = True, exchange: str = "NSE"):
    """Return contracts whose expiry falls inside the named window.

    `contracts` is a DataFrame with an 'expiry' (datetime.date) column.

    ``has_weekly=False`` (e.g. Bank Nifty, monthly-only since Nov-2024): the
    "weekly" and "current_week" windows collapse to the next MONTHLY expiry,
    and "next_week" collapses to the subsequent monthly expiry.

    ``exchange`` selects the expiry weekday convention (\"NSE\" = Tuesday,
    \"BSE\" = Thursday). Pass the Index.exchange value here.
    """
    is_iso_date = False
    target_date = None
    if window not in VALID_WINDOWS:
        try:
            target_date = date.fromisoformat(window)
            is_iso_date = True
        except (ValueError, TypeError):
            raise ValueError(f"unknown expiry window {window!r}; valid: {sorted(VALID_WINDOWS)}")

    exp = contracts["expiry"]
    # If broker data has not yet rolled over after an expiry (e.g. pre-market next morning),
    # clamp ref to the newest available expiry so valid contracts are not dropped.
    if not exp.empty and exp.max() < ref:
        ref = exp.max()

    if is_iso_date:
        mask = exp == target_date
    elif window == "all":
        mask = exp >= ref
    elif window == "0dte":
        mask = exp == ref
    elif window == "current_week":
        target = (next_weekly_expiry(ref, exchange) if has_weekly
                  else next_monthly_expiry(ref, exchange))
        if not exp.empty and (target not in exp.values):
            future_exp = exp[exp >= ref]
            if not future_exp.empty:
                target = future_exp.min()
        mask = exp == target
    elif window == "next_week":
        target = (second_weekly_expiry(ref, exchange) if has_weekly
                  else second_monthly_expiry(ref, exchange))
        if not exp.empty and (target not in exp.values):
            future_exp = sorted(exp[exp >= ref].unique())
            if len(future_exp) > 1:
                target = future_exp[1]
            elif len(future_exp) == 1:
                target = future_exp[0]
        mask = exp == target
    elif window == "weekly":
        end = (next_weekly_expiry(ref, exchange) if has_weekly
               else next_monthly_expiry(ref, exchange))
        if not exp.empty and (exp <= end).sum() == 0:
            future_exp = exp[exp >= ref]
            if not future_exp.empty:
                end = future_exp.min()
        mask = (exp >= ref) & (exp <= end)
    elif window in ("monthly", "0dte_to_next_monthly"):
        mask = (exp >= ref) & (exp <= next_monthly_expiry(ref, exchange))
    else:  # pragma: no cover
        raise AssertionError(window)
    return contracts[mask].reset_index(drop=True)
