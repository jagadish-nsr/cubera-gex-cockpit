"""Special-session clock (Muhurat) beside the regular NSE day.

Diwali Laxmi Pujan stays on the holiday list for expiry roll-back. Muhurat is a
separate live window on that calendar day — any day of the week the exchange
names (weekday or weekend; 2026 falls on a Sunday). Hours default to the usual
afternoon template and are overridden from ``data/muhurat_sessions.json`` once
the NSE circular is verified.

Year switch: update ``data/holidays.py`` / NSE holiday cache, then either rely on
auto-detect from Laxmi Pujan holiday names or pin the date + hours in
``muhurat_sessions.json``. VERIFY hours against the circular before Diwali.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[1]
MUHURAT_CONFIG_PATH = ROOT / "data" / "muhurat_sessions.json"


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

# Usual Muhurat afternoon template (NSE historically 14:05 pre-open / 14:20 open).
# VERIFY each year against the CMTR circular — pin overrides in muhurat_sessions.json.
DEFAULT_MUHURAT_HOURS = {
    "briefing": "13:00",
    "preopen": "14:05",
    "discovery": "14:12",
    "open": "14:20",
    "cash_close": "15:30",
    "fno_close": "15:40",
    "post_close": "16:00",
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


def _hm_to_min(text: str) -> int:
    h, m = text.strip().split(":")
    return int(h) * 60 + int(m)


def _clock_from_hours(label: str, hours: dict[str, str]) -> SessionClock:
    merged = {**DEFAULT_MUHURAT_HOURS, **{k: v for k, v in hours.items() if v}}
    return SessionClock(
        kind="muhurat",
        label=label or "Diwali Muhurat Trading",
        briefing_min=_hm_to_min(merged["briefing"]),
        preopen_min=_hm_to_min(merged["preopen"]),
        discovery_min=_hm_to_min(merged["discovery"]),
        open_min=_hm_to_min(merged["open"]),
        cash_close_min=_hm_to_min(merged["cash_close"]),
        fno_close_min=_hm_to_min(merged["fno_close"]),
        post_close_min=_hm_to_min(merged["post_close"]),
    )


def _load_muhurat_config() -> dict:
    from data.holidays import MUHURAT_HOLIDAY_NAME_MARKERS

    if not MUHURAT_CONFIG_PATH.exists():
        return {
            "default_hours": DEFAULT_MUHURAT_HOURS,
            "auto_from_holiday_name_markers": list(MUHURAT_HOLIDAY_NAME_MARKERS),
            "sessions": [],
        }
    try:
        return json.loads(MUHURAT_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("muhurat_sessions.json unreadable: %s — using defaults", exc)
        return {
            "default_hours": DEFAULT_MUHURAT_HOURS,
            "auto_from_holiday_name_markers": list(MUHURAT_HOLIDAY_NAME_MARKERS),
            "sessions": [],
        }


@lru_cache(maxsize=8)
def muhurat_sessions_for_year(year: int) -> dict[date, SessionClock]:
    """Resolved Muhurat clocks for one calendar year.

    Explicit ``sessions`` rows win. Any Laxmi Pujan / Muhurat holiday name not
    listed still arms the default afternoon clock so a new-year holiday update
    turns the mode on without another code change.
    """
    from data.holidays import MUHURAT_HOLIDAY_NAME_MARKERS, muhurat_holiday_candidates

    cfg = _load_muhurat_config()
    default_hours = {**DEFAULT_MUHURAT_HOURS, **(cfg.get("default_hours") or {})}
    markers = tuple(cfg.get("auto_from_holiday_name_markers") or MUHURAT_HOLIDAY_NAME_MARKERS)
    sessions: dict[date, SessionClock] = {}

    verified_dates = {
        str((row or {}).get("date"))
        for row in (cfg.get("sessions") or [])
        if isinstance(row, dict) and (row or {}).get("verified")
    }

    for d, name in muhurat_holiday_candidates(year, markers).items():
        sessions[d] = _clock_from_hours("Diwali Muhurat Trading", default_hours)
        if d.isoformat() not in verified_dates:
            log.info(
                "Muhurat auto-armed %s from holiday %r — VERIFY hours vs NSE circular",
                d.isoformat(),
                name,
            )

    for row in cfg.get("sessions") or []:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        try:
            d = date.fromisoformat(str(row["date"]))
        except ValueError:
            continue
        if d.year != year:
            continue
        hours = {**default_hours}
        for key in DEFAULT_MUHURAT_HOURS:
            if row.get(key):
                hours[key] = str(row[key])
        label = str(row.get("label") or "Diwali Muhurat Trading")
        sessions[d] = _clock_from_hours(label, hours)

    return sessions


def clear_muhurat_session_cache() -> None:
    """Test helper after rewriting muhurat_sessions.json."""
    muhurat_sessions_for_year.cache_clear()


def all_muhurat_sessions() -> dict[date, SessionClock]:
    """Union of resolved sessions for nearby years (tests / ops introspection)."""
    today = datetime.now(IST).date()
    out: dict[date, SessionClock] = {}
    for year in sorted({today.year - 1, today.year, today.year + 1}):
        out.update(muhurat_sessions_for_year(year))
    return out


def muhurat_clock(d: date | datetime | None = None) -> SessionClock | None:
    day = _as_date(d)
    return muhurat_sessions_for_year(day.year).get(day)


def is_muhurat_session(d: date | datetime | None = None) -> bool:
    day = _as_date(d)
    return day in muhurat_sessions_for_year(day.year)


def clock_for(d: date | datetime | None = None) -> SessionClock:
    special = muhurat_clock(d)
    return special if special is not None else REGULAR_CLOCK


def is_live_session_day(d: date | datetime | None = None) -> bool:
    """True when the exchange runs a continuous session (regular or Muhurat).

    Muhurat dates remain holidays for expiry roll-back via ``is_holiday``.
    """
    day = _as_date(d)
    if is_muhurat_session(day):
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
