"""Special-session clock (Muhurat) beside the regular NSE day.

Diwali Laxmi Pujan stays on the holiday list for expiry roll-back. Muhurat is a
separate live window on that calendar day (often a Sunday). Regular Mon–Fri
hours stay 09:15–15:40. VERIFY against the NSE circular before each Diwali.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class SessionClock:
    """Minutes-since-midnight IST for one continuous session day."""

    kind: str  # "regular" | "muhurat"
    label: str
    briefing_min: int
    preopen_min: int
    discovery_min: int
    open_min: int
    cash_close_min: int
    fno_close_min: int
    post_close_min: int


REGULAR_CLOCK = SessionClock(
    kind="regular",
    label="Regular session",
    briefing_min=6 * 60 + 30,
    preopen_min=9 * 60,
    discovery_min=9 * 60 + 7,  # discovery from 09:07:30; second handled by caller
    open_min=9 * 60 + 15,
    cash_close_min=15 * 60 + 30,
    fno_close_min=15 * 60 + 40,
    post_close_min=16 * 60,
)

# NSE CMTR circular 149/2026 (19 Oct 2026): Sunday 8 Nov 2026 Muhurat.
# Equity continuous 14:20–15:30; F&O 14:20–15:40; pre-open 14:05–14:20.
MUHURAT_SESSIONS: dict[date, SessionClock] = {
    date(2026, 11, 8): SessionClock(
        kind="muhurat",
        label="Diwali Muhurat Trading",
        briefing_min=13 * 60,
        preopen_min=14 * 60 + 5,
        discovery_min=14 * 60 + 12,
        open_min=14 * 60 + 20,
        cash_close_min=15 * 60 + 30,
        fno_close_min=15 * 60 + 40,
        post_close_min=16 * 60,
    ),
}


def _as_date(d: date | datetime | None) -> date:
    if d is None:
        return datetime.now(IST).date()
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=IST)
        else:
            d = d.astimezone(IST)
        return d.date()
    return d


def muhurat_clock(d: date | datetime | None = None) -> SessionClock | None:
    return MUHURAT_SESSIONS.get(_as_date(d))


def is_muhurat_session(d: date | datetime | None = None) -> bool:
    return _as_date(d) in MUHURAT_SESSIONS


def clock_for(d: date | datetime | None = None) -> SessionClock:
    special = muhurat_clock(d)
    return special if special is not None else REGULAR_CLOCK


def is_live_session_day(d: date | datetime | None = None) -> bool:
    """True when the exchange runs a continuous session (regular or Muhurat).

    Muhurat dates remain holidays for expiry roll-back via ``is_holiday``.
    """
    day = _as_date(d)
    if day in MUHURAT_SESSIONS:
        return True
    from data.holidays import is_trading_day
    return is_trading_day(day)


def minutes_now(now: datetime | None = None) -> int:
    now_ist = now or datetime.now(IST)
    if now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST)
    else:
        now_ist = now_ist.astimezone(IST)
    return now_ist.hour * 60 + now_ist.minute
