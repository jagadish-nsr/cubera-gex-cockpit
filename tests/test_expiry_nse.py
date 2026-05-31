"""NSE expiry calendar (last-Thursday monthly) + window filtering.

Structural checks (robust without hard-coding dates): the monthly expiry is a
Thursday, sits in the reference month, and is the LAST Thursday.
"""

from datetime import date, timedelta

import pandas as pd

from data.expiry import (
    WEEKLY_EXPIRY_WEEKDAY,
    filter_expiry_window,
    last_weekday_of_month,
    next_monthly_expiry,
    next_weekly_expiry,
)


def test_last_thursday_is_thursday_and_last():
    d = last_weekday_of_month(2026, 6, WEEKLY_EXPIRY_WEEKDAY)
    assert d.weekday() == WEEKLY_EXPIRY_WEEKDAY     # Thursday
    assert d.month == 6 and d.year == 2026
    assert (d + timedelta(days=7)).month != 6       # no later Thursday in June


def test_next_monthly_rolls_when_past():
    june_last_thu = last_weekday_of_month(2026, 6, WEEKLY_EXPIRY_WEEKDAY)
    # a day after this month's expiry -> next month's
    ref = june_last_thu + timedelta(days=1)
    nxt = next_monthly_expiry(ref)
    assert nxt > june_last_thu
    assert nxt.weekday() == WEEKLY_EXPIRY_WEEKDAY
    # on/before this month's expiry -> stays this month
    assert next_monthly_expiry(june_last_thu - timedelta(days=3)) == june_last_thu


def test_next_weekly_is_on_or_after_ref():
    ref = date(2026, 6, 1)
    w = next_weekly_expiry(ref)
    assert w >= ref and w.weekday() == WEEKLY_EXPIRY_WEEKDAY
    assert (w - ref).days < 7


def test_window_filter():
    ref = date(2026, 6, 1)
    monthly = next_monthly_expiry(ref)
    rows = [ref, next_weekly_expiry(ref), monthly, monthly + timedelta(days=40)]
    df = pd.DataFrame({"expiry": rows})
    kept = set(filter_expiry_window(df, "0dte_to_next_monthly", ref)["expiry"])
    assert monthly in kept
    assert (monthly + timedelta(days=40)) not in kept
    assert len(filter_expiry_window(df, "all", ref)) == 4
