"""NSE/BSE trading holiday calendar + holiday-aware expiry resolution.

Extends data/expiry.py with:
  - HolidayCalendar: fetches + caches the NSE holiday list, auto-refreshes on year change.
  - roll_back(): adjusts an expiry date backward to the nearest trading day when it falls
    on a holiday.
  - near_month_futures_expiry(): resolves the live near-month futures contract expiry,
    fully holiday-aware.

Holiday source: NSE's bhavcopy API (trade_holidays endpoint), same as live exchange.
For BSE we use the NSE list (BSE holidays substantially overlap).  This is explicitly
flagged in the data so any consumer knows the provenance.

Data is cached to: data/journal/holidays/<YEAR>.json
TTL: refreshed at most once per day during the year it covers; the prior year's file
is kept forever (historical, stable) and the next year's file is fetched eagerly when
December arrives.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
HOLIDAY_DIR = ROOT / "data" / "journal" / "holidays"

# Configurable - NSE publishes the holiday list as a JSON endpoint
NSE_HOLIDAY_URL = (
    "https://www.nseindia.com/api/holiday-master?type=trading"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

_calendar_cache: dict[int, set[date]] = {}   # year -> set of holiday dates


def _cache_path(year: int) -> Path:
    return HOLIDAY_DIR / f"{year}.json"


def _fetch_nse_holidays(year: int) -> set[date]:
    """Fetch NSE trading holidays for the given year via the NSE API.
    Returns a set of holiday dates. Falls back to an empty set on any error so
    expiry calculation degrades gracefully to naive (unadjusted) dates.
    """
    import urllib.request
    holidays: set[date] = set()
    try:
        req = urllib.request.Request(NSE_HOLIDAY_URL, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
        # NSE returns {"CM": [...], "FO": [...], ...}  – FO is the derivatives calendar
        fo = payload.get("FO") or payload.get("CM") or []
        for h in fo:
            # Prefer "tradingDate" field (YYYY-MM-DD or DD-Mon-YYYY)
            raw = h.get("tradingDate") or h.get("tradeDate") or ""
            for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    d = date.fromisoformat(raw) if fmt == "%Y-%m-%d" else __import__("datetime").datetime.strptime(raw, fmt).date()
                    if d.year == year:
                        holidays.add(d)
                    break
                except (ValueError, AttributeError):
                    continue
        log.info("Holiday calendar: fetched %d holidays for %d from NSE", len(holidays), year)
    except Exception as exc:  # noqa: BLE001
        log.warning("Holiday calendar: NSE fetch failed for %d: %s — using no-holiday fallback", year, exc)
    return holidays


def _load_or_fetch(year: int) -> set[date]:
    """Load holidays from disk cache; re-fetch if stale or missing."""
    if year in _calendar_cache:
        return _calendar_cache[year]

    path = _cache_path(year)
    HOLIDAY_DIR.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            data = json.loads(path.read_text())
            holidays = {date.fromisoformat(d) for d in data.get("holidays", [])}
            # Treat current-year cache as valid for today (re-fetch at most daily)
            if year < date.today().year:
                _calendar_cache[year] = holidays
                return holidays
            # Current/future year: refresh if fetched_date is not today
            if data.get("fetched_date") == str(date.today()):
                _calendar_cache[year] = holidays
                return holidays
        except Exception:  # noqa: BLE001
            pass

    # Fetch from NSE
    holidays = _fetch_nse_holidays(year)
    _save_to_disk(year, holidays)
    _calendar_cache[year] = holidays
    return holidays


def _save_to_disk(year: int, holidays: set[date]) -> None:
    path = _cache_path(year)
    try:
        path.write_text(json.dumps({
            "year": year,
            "fetched_date": str(date.today()),
            "source": "NSE API (FO holiday calendar)",
            "holidays": sorted(str(d) for d in holidays),
        }, indent=2))
    except Exception as exc:  # noqa: BLE001
        log.warning("Holiday calendar: could not write cache for %d: %s", year, exc)


def is_holiday(d: date) -> bool:
    """Return True if ``d`` is in the NSE F&O holiday calendar."""
    holidays = _load_or_fetch(d.year)
    return d in holidays


def is_trading_day(d: date) -> bool:
    """Return True if ``d`` is a weekday and not a holiday."""
    return d.weekday() < 5 and not is_holiday(d)


def roll_back_to_trading_day(d: date) -> date:
    """Roll ``d`` backward to the nearest trading day (Mon-Fri, non-holiday).

    Used to adjust an expiry date when the last-weekday-of-month falls on a
    holiday — the exchange moves the expiry to the preceding trading day.
    """
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def near_month_futures_expiry(ref: date = None, exchange: str = "NSE") -> date:
    """Resolve the near-month futures contract expiry, holiday-aware.

    NSE: last Thursday of the month (index futures expire on the last Thursday,
         unlike options which expire on the last Tuesday).
    BSE: last Thursday of the month (Sensex futures also expire on the last Thursday).

    If the natural expiry is a holiday, roll back to the preceding trading day.
    Ensures the file is populated for next year as December arrives.

    Args:
        ref: reference date (today if None)
        exchange: "NSE" or "BSE"

    Returns:
        The holiday-adjusted near-month futures expiry date.
    """
    from data.expiry import last_weekday_of_month

    if ref is None:
        ref = date.today()

    # Futures for NSE/BSE both expire on the LAST THURSDAY of the month
    # (weekday=3 = Thursday).  This is DIFFERENT from NSE options (Tuesday).
    FUTURES_WEEKDAY = 3  # Thursday

    # Eager-fetch next year's holidays when we reach December
    if ref.month == 12:
        _load_or_fetch(ref.year + 1)

    # Try this month, then next month
    for year, month in [(ref.year, ref.month), (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)]:
        raw = last_weekday_of_month(year, month, FUTURES_WEEKDAY)
        adjusted = roll_back_to_trading_day(raw)
        if adjusted >= ref:
            return adjusted

    # Fallback (should never reach here)
    raw = last_weekday_of_month(ref.year, ref.month, FUTURES_WEEKDAY)
    return roll_back_to_trading_day(raw)
