"""Muhurat special session: live gates follow Laxmi Pujan (any calendar day)."""

from datetime import date, datetime, timedelta, timezone

import config
from data.holidays import is_holiday, is_trading_day, laxmi_pujan_dates
from data.pre_post_market import (
    get_current_session_phase,
    get_post_market_state,
    post_market_poller_asleep,
)
from data.session_calendar import (
    clear_muhurat_session_cache,
    clock_for,
    is_live_session_day,
    is_muhurat_session,
)
from web_server import compute_session_schedule_info

IST = timezone(timedelta(hours=5, minutes=30))
MUHURAT = date(2026, 11, 8)  # Sunday 2026 (weekend Muhurat)
MUHURAT_THU = date(2027, 10, 28)  # Thursday 2027 (weekday Muhurat)


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


def test_muhurat_arms_on_weekend_and_weekday_laxmi_pujan():
    """Muhurat follows the holiday date — Sunday this year, Thursday in 2027."""
    assert MUHURAT.weekday() == 6  # Sunday
    assert is_muhurat_session(MUHURAT) is True
    assert is_live_session_day(MUHURAT) is True

    assert MUHURAT_THU.weekday() == 3  # Thursday
    assert is_holiday(MUHURAT_THU) is True
    assert is_trading_day(MUHURAT_THU) is False
    assert is_muhurat_session(MUHURAT_THU) is True
    assert is_live_session_day(MUHURAT_THU) is True
    open_dt = datetime(2027, 10, 28, 14, 20, tzinfo=IST)
    assert config.is_market_open(open_dt) is True
    morning = datetime(2027, 10, 28, 11, 0, tzinfo=IST)
    assert config.is_market_open(morning) is False


def test_laxmi_pujan_dates_come_from_holiday_calendar():
    assert MUHURAT in laxmi_pujan_dates(2026)
    assert MUHURAT_THU in laxmi_pujan_dates(2027)
    # Balipratipada is a holiday but not Muhurat.
    assert date(2026, 11, 10) not in laxmi_pujan_dates(2026)
    assert is_muhurat_session(date(2026, 11, 10)) is False


def test_new_year_holiday_name_auto_arms_without_sessions_row(tmp_path, monkeypatch):
    """Pre-configuring Laxmi Pujan in the holiday dictionary is enough for next year."""
    import data.holidays as hol
    import data.session_calendar as sc

    cfg = tmp_path / "muhurat_sessions.json"
    cfg.write_text(
        '{"version":1,"default_hours":{"briefing":"13:00","preopen":"14:05",'
        '"discovery":"14:12","open":"14:20","cash_close":"15:30","fno_close":"15:40",'
        '"post_close":"16:00"},"auto_from_holiday_name_markers":["laxmi pujan"],'
        '"sessions":[]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(sc, "MUHURAT_CONFIG_PATH", cfg)
    clear_muhurat_session_cache()
    # Invent a future weekend Laxmi Pujan only in the static holiday dictionary.
    future = date(2028, 11, 4)  # Saturday
    monkeypatch.setitem(hol.NSE_HOLIDAY_NAMES, future.isoformat(), "Diwali (Laxmi Pujan)")
    hol._calendar_cache.pop(2028, None)
    hol._calendar_names_cache.pop(2028, None)
    try:
        assert future.weekday() == 5  # Saturday — weekend still arms
        assert is_muhurat_session(future) is True
        assert clock_for(future).open_min == 14 * 60 + 20
    finally:
        clear_muhurat_session_cache()
        hol._calendar_cache.pop(2028, None)
        hol._calendar_names_cache.pop(2028, None)


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
    assert info.get("holiday_name") and "Muhurat" in info["holiday_name"]

    morning = compute_session_schedule_info(_dt(10, 0))
    assert morning["is_market_open"] is False
    assert "MUHURAT" in morning["phase_badge"] or "Muhurat" in morning["exec_phase_countdown"]
    assert "HOLIDAY ·" not in morning["phase_badge"]
    assert "WEEKEND ·" not in morning["phase_badge"]


def test_post_market_not_forced_during_muhurat_live():
    """Laxmi Pujan is_holiday must not flip the post-market card on during live."""
    from data import pre_post_market as ppm

    ppm._POST_MARKET_CACHE.clear()
    live = get_post_market_state("NIFTY", force_refresh=True, now=_dt(14, 30))
    assert live["is_post_market"] is False
    assert post_market_poller_asleep(_dt(14, 30)) is True
    assert post_market_poller_asleep(_dt(15, 45)) is False
    assert post_market_poller_asleep(_dt(16, 15)) is True


def test_ops_timeline_uses_muhurat_clock_not_regular_09xx():
    from web_server import build_ops_timeline_windows

    title, kind, windows = build_ops_timeline_windows(_dt(14, 30))
    assert kind == "muhurat"
    assert "Muhurat" in title
    assert all("09:" not in w["start"] for w in windows)
    active = [w for w in windows if w["status"] == "ACTIVE"]
    assert len(active) == 1
    assert active[0]["start"] == "14:20" or active[0]["start"] == "14:40"

    # Contiguous cover of pre-open through F&O close — no dead zone at 15:35.
    by_id = {w["id"]: w for w in windows}
    assert by_id["w7"]["start"] == "15:30" and by_id["w7"]["end"] == "15:40"
    _, _, at_cas = build_ops_timeline_windows(_dt(15, 35))
    assert sum(1 for w in at_cas if w["status"] == "ACTIVE") == 1
    assert next(w for w in at_cas if w["status"] == "ACTIVE")["id"] == "w7"

    # Morning is still the overnight/night window — never the regular 09:xx Power Hour.
    _, _, morning = build_ops_timeline_windows(_dt(10, 0))
    assert all(not w["start"].startswith("09") for w in morning)
    assert not any("Gamma Scalp" in w["name"] or "Power Hour" in w["name"] for w in morning)
    active_am = [w for w in morning if w["status"] == "ACTIVE"]
    assert len(active_am) == 1 and active_am[0]["id"] == "w9"
