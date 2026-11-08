"""Muhurat special session: live gates open 14:20–15:40 on 2026-11-08."""

from datetime import date, datetime, timedelta, timezone

import config
from data.holidays import is_holiday, is_trading_day
from data.pre_post_market import get_current_session_phase
from data.session_calendar import (
    clock_for,
    is_live_session_day,
    is_muhurat_session,
)
from web_server import compute_session_schedule_info

IST = timezone(timedelta(hours=5, minutes=30))
MUHURAT = date(2026, 11, 8)


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 8, hour, minute, tzinfo=IST)


def test_muhurat_stays_holiday_for_expiry_but_is_live_session_day():
    assert is_holiday(MUHURAT) is True
    assert is_trading_day(MUHURAT) is False
    assert is_muhurat_session(MUHURAT) is True
    assert is_live_session_day(MUHURAT) is True
    clock = clock_for(MUHURAT)
    assert clock.kind == "muhurat"
    assert clock.open_min == 14 * 60 + 20
    assert clock.fno_close_min == 15 * 60 + 40


def test_is_market_open_on_muhurat_window():
    assert config.is_market_open(_dt(11, 30)) is False
    assert config.is_market_open(_dt(14, 5)) is False
    assert config.is_market_open(_dt(14, 19)) is False
    assert config.is_market_open(_dt(14, 20)) is True
    assert config.is_market_open(_dt(15, 0)) is True
    assert config.is_market_open(_dt(15, 35)) is True
    assert config.is_market_open(_dt(15, 35), include_fno_extended=False) is False
    assert config.is_market_open(_dt(15, 40)) is False


def test_ordinary_sunday_stays_closed():
    sun = datetime(2026, 11, 15, 14, 30, tzinfo=IST)  # not Muhurat
    assert is_muhurat_session(sun.date()) is False
    assert config.is_market_open(sun) is False


def test_session_phase_tracks_muhurat_preopen_and_live():
    code, label, _ = get_current_session_phase(_dt(13, 30))
    assert code == "PRE_MARKET_SETUP"
    assert "Muhurat" in label or "Prior-session" in label

    code, label, _ = get_current_session_phase(_dt(14, 8))
    assert code == "PRE_MARKET_ENTRY"

    code, label, _ = get_current_session_phase(_dt(14, 15))
    assert code == "PRE_MARKET_DISCOVERY"

    code, label, _ = get_current_session_phase(_dt(14, 45))
    assert code == "REGULAR_SESSION"
    assert "Muhurat" in label

    code, label, _ = get_current_session_phase(_dt(15, 35))
    assert code == "CAS_AND_FNO_EXTENDED"


def test_schedule_badge_names_muhurat_not_weekend_closed():
    info = compute_session_schedule_info(_dt(14, 30))
    assert info["is_market_open"] is True
    assert info["session_kind"] == "muhurat"
    assert "MUHURAT" in info["phase_badge"]

    morning = compute_session_schedule_info(_dt(10, 0))
    assert morning["is_market_open"] is False
    assert "MUHURAT" in morning["phase_badge"] or "Muhurat" in morning["exec_phase_countdown"]
