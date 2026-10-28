"""Read-only audit. Dates are classified by data.holidays, not by a hardcoded week."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import data.holidays as holidays
from app.admin.audit_engine import audit_range, session_grid
from app.admin.clock import IST

WEEK_START = "2026-10-18"
WEEK_END = "2026-10-24"


@pytest.fixture(autouse=True)
def offline_holidays(monkeypatch):
    """Use the project's holiday dictionary so tests do not call NSE."""
    cache: dict[int, set[date]] = {}

    def load(year: int) -> set[date]:
        if year not in cache:
            found: set[date] = set()
            names: dict[date, str] = {}
            for iso, name in holidays.NSE_HOLIDAY_NAMES.items():
                day = date.fromisoformat(iso)
                if day.year == year:
                    found.add(day)
                    names[day] = name
            cache[year] = found
            holidays._calendar_names_cache[year] = names
        return cache[year]

    monkeypatch.setattr(holidays, "_load_or_fetch", load)


def _at(day: str, hour: int, minute: int) -> datetime:
    y, m, d = (int(p) for p in day.split("-"))
    return datetime(y, m, d, hour, minute, tzinfo=IST)


def _write_futures(root, symbol: str, day: str, minutes: list[str], *, bad_ohlc: bool = False,
                   duplicate_first: bool = False) -> None:
    seq = list(minutes)
    if duplicate_first and seq:
        seq = seq[:-1] + [seq[0]]
    rows = []
    for i, minute in enumerate(seq):
        high, low = (90.0, 110.0) if bad_ohlc and i == 0 else (110.0, 90.0)
        rows.append({
            "time_ist": minute, "open": 100.0, "high": high, "low": low,
            "close": 105.0, "volume": 1, "oi": 1,
        })
    path = root / "data" / "journal" / "dhan_futures" / symbol / f"{day}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def _write_options(root, symbol: str, day: str, minutes: list[str], *, drop_one: bool = False) -> None:
    rows = []
    dropped = False
    for minute in minutes:
        for off in range(-10, 11):
            for side in ("ce", "pe"):
                if drop_one and not dropped:
                    dropped = True
                    continue
                rows.append({"time_ist": minute, "off": off, "side": side})
    path = root / "data" / "journal" / "dhan_intraday" / symbol / f"{day}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def _day(report: dict, iso: str) -> dict:
    return next(row for row in report["days"] if row["date"] == iso)


def test_session_grid_is_375_minutes():
    grid = session_grid()
    assert grid[0] == "09:15"
    assert grid[-1] == "15:29"
    assert len(grid) == 375
    assert len(set(grid)) == 375


def test_complete_futures_session(tmp_path):
    day = "2026-10-22"
    _write_futures(tmp_path, "NIFTY", day, session_grid())
    report = audit_range(tmp_path, "NIFTY", day, day, now=_at("2026-10-24", 20, 0))
    row = _day(report, day)
    assert row["futures_status"] == "COMPLETE"
    assert row["futures_bars"] == 375
    assert row["futures_expected_bars"] == 375
    assert row["missing_segments"] == []


def test_duplicate_minute_with_a_hole_is_partial(tmp_path):
    day = "2026-10-22"
    _write_futures(tmp_path, "NIFTY", day, session_grid(), duplicate_first=True)
    report = audit_range(tmp_path, "NIFTY", day, day, now=_at("2026-10-24", 20, 0))
    row = _day(report, day)
    assert row["futures_status"] == "PARTIAL"
    assert "15:29" in row["missing_segments"]
    assert "duplicate_minutes" in row["issues"]
    assert row["futures_bars"] == 374


def test_bad_ohlc_is_partial_even_when_the_grid_is_full(tmp_path):
    day = "2026-10-22"
    _write_futures(tmp_path, "NIFTY", day, session_grid(), bad_ohlc=True)
    report = audit_range(tmp_path, "NIFTY", day, day, now=_at("2026-10-24", 20, 0))
    row = _day(report, day)
    assert row["futures_status"] == "PARTIAL"
    assert "ohlc" in row["issues"]
    assert row["missing_segments"] == []


def test_october_2026_week_follows_the_holiday_calendar(tmp_path):
    report = audit_range(tmp_path, "NIFTY", WEEK_START, WEEK_END, now=_at("2026-10-24", 23, 0))
    assert report["summary"]["trading_days"] == 4
    assert report["summary"]["holidays"] == 1
    assert report["summary"]["weekends"] == 2
    for row in report["days"]:
        day = date.fromisoformat(row["date"])
        if day.weekday() >= 5:
            assert row["session_kind"] == "WEEKEND"
            assert row["holiday_name"] is None
        elif not holidays.is_trading_day(day):
            assert row["session_kind"] == "HOLIDAY"
            assert row["holiday_name"] == holidays.get_holiday_name(day)
        else:
            assert row["session_kind"] == "REGULAR"
            assert row["is_trading_day"] is True
    dussehra = _day(report, "2026-10-20")
    assert dussehra["session_kind"] == "HOLIDAY"
    assert "Dussehra" in dussehra["holiday_name"]
    assert _day(report, "2026-10-24")["session_kind"] == "WEEKEND"
    assert _day(report, "2026-10-19")["session_kind"] == "REGULAR"


def test_file_on_a_holiday_is_a_special_session(tmp_path):
    day = "2026-10-20"
    _write_futures(tmp_path, "NIFTY", day, session_grid()[:60])
    report = audit_range(tmp_path, "NIFTY", day, day, now=_at("2026-10-24", 20, 0))
    row = _day(report, day)
    assert row["session_kind"] == "SPECIAL_SESSION"
    assert row["futures_status"] == "SPECIAL_SESSION"
    assert row["futures_bars"] == 60
    assert row["futures_expected_bars"] is None
    assert row["repair_available"] is False


def test_futures_outside_the_90_day_window_are_unfillable(tmp_path):
    now = _at("2026-10-24", 20, 0)
    old = (now.date() - timedelta(days=89 + 3)).isoformat()
    assert date.fromisoformat(old).weekday() < 5
    report = audit_range(tmp_path, "NIFTY", old, old, now=now)
    row = _day(report, old)
    assert row["futures_status"] == "UNFILLABLE"
    futures = next(item for item in row["repairs"] if item["task_type"] == "backfill_futures")
    assert futures["available"] is False
    assert "90-day" in futures["block_reason"]


def test_open_session_with_a_full_elapsed_grid_is_in_progress(tmp_path):
    day = "2026-10-23"
    elapsed = [m for m in session_grid() if m <= "11:59"]
    _write_futures(tmp_path, "NIFTY", day, elapsed)
    report = audit_range(tmp_path, "NIFTY", day, day, now=_at(day, 12, 0))
    row = _day(report, day)
    assert row["futures_status"] == "IN_PROGRESS"
    assert row["futures_bars"] == len(elapsed)
    assert row["missing_segments"] == []


def test_eod_files_stay_pending_until_the_next_session_open(tmp_path):
    day = "2026-10-23"
    same_evening = audit_range(tmp_path, "NIFTY", day, day, now=_at(day, 16, 0))
    evening = _day(same_evening, day)
    assert evening["bhavcopy_status"] == "PENDING"
    assert evening["participant_status"] == "PENDING"

    next_open = audit_range(tmp_path, "NIFTY", day, day, now=_at("2026-10-26", 9, 15))
    due = _day(next_open, day)
    assert due["bhavcopy_status"] == "MISSING"
    assert due["participant_status"] == "MISSING"
    assert due["participant_status"] == "MISSING"
    assert next_open["summary"]["participant_oi_missing_days"] == 1


def test_options_minute_missing_one_pair_is_partial(tmp_path):
    day = "2026-10-22"
    _write_options(tmp_path, "NIFTY", day, session_grid(), drop_one=True)
    report = audit_range(tmp_path, "NIFTY", day, day, now=_at("2026-10-24", 20, 0))
    row = _day(report, day)
    assert row["options_matrix_status"] == "PARTIAL"
    assert row["missing_segments"]
    options = next(item for item in row["repairs"] if item["task_type"] == "backfill_options")
    assert options["available"] is False
    assert "does not rewrite" in options["block_reason"]


def test_a_future_trading_day_is_pending(tmp_path):
    report = audit_range(tmp_path, "NIFTY", "2026-10-23", "2026-10-23", now=_at("2026-10-22", 20, 0))
    row = _day(report, "2026-10-23")
    assert row["session_kind"] == "PENDING"
    assert row["futures_status"] == "PENDING"
    assert row["options_matrix_status"] == "PENDING"
    assert row["repair_available"] is False
    assert report["summary"]["missing_days"] == 0
    assert report["summary"]["trading_days"] == 0


def test_banknifty_does_not_read_the_nifty_store(tmp_path):
    day = "2026-10-22"
    _write_futures(tmp_path, "NIFTY", day, session_grid())
    report = audit_range(tmp_path, "BANKNIFTY", day, day, now=_at("2026-10-24", 20, 0))
    assert _day(report, day)["futures_status"] == "MISSING"
    with pytest.raises(ValueError):
        audit_range(tmp_path, "NIF", day, day, now=_at("2026-10-24", 20, 0))
    sensex = audit_range(tmp_path, "SENSEX", day, day, now=_at("2026-10-24", 20, 0))
    assert sensex["calendar_note"] == "NSE holiday list applied to Sensex"
    futures = next(item for item in _day(sensex, day)["repairs"] if item["task_type"] == "backfill_futures")
    assert futures["available"] is False
