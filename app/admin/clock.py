"""IST clock helpers shared by the audit, the alert loop, and the task runner."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from data.holidays import is_trading_day

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)  # first minute that is no longer part of the session


def as_ist(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def now_ist() -> datetime:
    return datetime.now(IST)


def is_regular_session(now: datetime) -> bool:
    """True during 09:15–15:30 IST on an NSE trading day."""
    now = as_ist(now)
    if not is_trading_day(now.date()):
        return False
    current = now.time().replace(tzinfo=None)
    return SESSION_OPEN <= current < SESSION_CLOSE
