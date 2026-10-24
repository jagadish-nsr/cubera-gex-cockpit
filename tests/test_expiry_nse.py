"""Index expiry calendar.

Nifty: every Tuesday. Bank Nifty: last Tuesday of the month.
Sensex: every Thursday. A holiday moves expiry to the previous trading day.
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


def test_last_tuesday_of_month_is_the_last_tuesday():
    d = last_weekday_of_month(2026, 6, WEEKLY_EXPIRY_WEEKDAY)
    assert d == date(2026, 6, 30)
    assert d.weekday() == 1  # Tuesday
    assert (d + timedelta(days=7)).month != 6


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


def test_current_and_next_week_filter():
    from data.expiry import second_weekly_expiry
    ref = date(2026, 6, 1)  # Monday
    w1 = next_weekly_expiry(ref)
    w2 = second_weekly_expiry(ref)
    m = next_monthly_expiry(ref)
    df = pd.DataFrame({"expiry": [w1, w2, m]})

    curr = filter_expiry_window(df, "current_week", ref)
    assert len(curr) == 1
    assert curr["expiry"].iloc[0] == w1

    nxt = filter_expiry_window(df, "next_week", ref)
    assert len(nxt) == 1
    assert nxt["expiry"].iloc[0] == w2

    # ISO date string filter
    iso_w1 = filter_expiry_window(df, str(w1), ref)
    assert len(iso_w1) == 1
    assert iso_w1["expiry"].iloc[0] == w1


def test_nifty_weekly_is_tuesday_and_sensex_weekly_is_thursday():
    ref = date(2026, 10, 26)  # Monday
    nifty = next_weekly_expiry(ref, "NSE")
    sensex = next_weekly_expiry(ref, "BSE")
    assert nifty == date(2026, 10, 27) and nifty.weekday() == 1
    assert sensex == date(2026, 10, 29) and sensex.weekday() == 3


def test_banknifty_window_is_the_last_tuesday_not_the_weekly():
    ref = date(2026, 10, 1)
    weekly_tuesday = next_weekly_expiry(ref, "NSE")
    month_end = next_monthly_expiry(ref, "NSE")
    assert weekly_tuesday.weekday() == 1
    assert month_end.weekday() == 1
    assert month_end > weekly_tuesday
    df = pd.DataFrame({"expiry": [weekly_tuesday, month_end]})
    kept = filter_expiry_window(df, "current_week", ref, has_weekly=False, exchange="NSE")
    assert list(kept["expiry"]) == [month_end]


def test_holiday_expiry_moves_to_the_previous_trading_day(monkeypatch):
    holiday = date(2026, 10, 27)  # a Tuesday
    monkeypatch.setattr("data.holidays.is_holiday", lambda d: d == holiday)
    rolled = next_weekly_expiry(date(2026, 10, 26), "NSE")
    assert rolled == date(2026, 10, 26)  # Monday, the trading day before
    assert rolled.weekday() == 0

