"""Read-only completeness audit of the journals the cockpit actually reads.

A regular session is the 375 minutes from 09:15 through 15:29 IST. Row count is
not completeness: the set of ``time_ist`` minutes is. Callers pass ``now`` so
tests can freeze the clock. This module does not write market data.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

from data.holidays import get_holiday_name, is_trading_day

from app.admin.clock import IST, as_ist

SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX")
FUTURES_REPAIR_SYMBOLS = ("NIFTY", "BANKNIFTY")
MAX_SPAN_DAYS = 120
FUT_LOOKBACK_DAYS = 89  # today minus 89 is the oldest day Dhan will still serve
SESSION_LAST = time(15, 29)
OFFSETS = tuple(range(-10, 11))
SIDES = ("ce", "pe")
PAIRS_PER_MINUTE = len(OFFSETS) * len(SIDES)  # 42
OPTIONS_ON_DISK = "options backfill does not rewrite a day already on disk"


def session_grid() -> list[str]:
    """09:15 through 15:29 inclusive, zero-padded HH:MM."""
    cur = 9 * 60 + 15
    end = 15 * 60 + 29
    out = []
    while cur <= end:
        out.append(f"{cur // 60:02d}:{cur % 60:02d}")
        cur += 1
    return out


_GRID = session_grid()
_GRID_SET = set(_GRID)


def _norm_hhmm(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if " " in s:
        s = s.split()[-1]
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return None


def missing_segments(expected: list[str], present: set[str]) -> list[str]:
    missing = [m for m in expected if m not in present]
    if not missing:
        return []

    def to_min(hhmm: str) -> int:
        hour, minute = hhmm.split(":")
        return int(hour) * 60 + int(minute)

    ranges: list[str] = []
    start = prev = missing[0]
    for minute in missing[1:]:
        if to_min(minute) == to_min(prev) + 1:
            prev = minute
            continue
        ranges.append(start if start == prev else f"{start}-{prev}")
        start = prev = minute
    ranges.append(start if start == prev else f"{start}-{prev}")
    return ranges


def _check_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if sym not in SYMBOLS:
        raise ValueError(f"symbol must be one of {', '.join(SYMBOLS)}")
    return sym


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}") from exc


def _expected_minutes(day: date, now: datetime) -> tuple[list[str], bool]:
    """Minutes that should already be closed, and whether the session is still open."""
    now = as_ist(now)
    open_session = day == now.date() and is_trading_day(day) and (
        time(9, 15) <= now.time().replace(tzinfo=None) < time(15, 30)
    )
    if day > now.date():
        return [], False
    if not open_session:
        if day == now.date() and is_trading_day(day) and now.time().replace(tzinfo=None) < time(9, 15):
            return [], True
        return list(_GRID), False
    closed = now.hour * 60 + now.minute - 1
    # At 09:15 the previous minute is 09:14, before the open, so nothing is due yet.
    if closed < 9 * 60 + 15:
        return [], True
    last = min(closed, 15 * 60 + 29)
    last_hhmm = f"{last // 60:02d}:{last % 60:02d}"
    return [m for m in _GRID if m <= last_hhmm], True


def _eod_due(session_day: date, now: datetime) -> bool:
    nxt = session_day + timedelta(days=1)
    for _ in range(14):
        if is_trading_day(nxt):
            break
        nxt += timedelta(days=1)
    deadline = datetime(nxt.year, nxt.month, nxt.day, 9, 15, tzinfo=IST)
    return as_ist(now) >= deadline


def _read_parquet(path: Path, columns: list[str]):
    import pandas as pd
    return pd.read_parquet(path, columns=columns)


def _ohlc_ok(frame) -> bool:
    import numpy as np
    needed = ["open", "high", "low", "close"]
    if any(col not in frame.columns for col in needed):
        return False
    arr = frame.loc[:, needed].astype("float64").to_numpy()
    if arr.size == 0:
        return True
    if not np.isfinite(arr).all():
        return False
    op, hi, lo, cl = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    return bool(
        (hi >= op).all() and (hi >= cl).all() and (hi >= lo).all()
        and (lo <= op).all() and (lo <= cl).all()
    )


def _score_presence(times: list[str], expected: list[str], *, open_session: bool,
                    ohlc_ok: bool) -> tuple[str, int, list[str], list[str]]:
    """Return status, bar count on the expected grid, missing segments, issues."""
    on_grid = [t for t in times if t in _GRID_SET]
    if not on_grid and not expected:
        return "IN_PROGRESS", 0, [], []
    if not on_grid:
        return "MISSING", 0, [], []
    expected_set = set(expected)
    considered = [t for t in on_grid if t in expected_set]
    uniq = set(considered)
    issues: list[str] = []
    if len(considered) != len(uniq):
        issues.append("duplicate_minutes")
    if not ohlc_ok:
        issues.append("ohlc")
    segs = missing_segments(expected, uniq)
    bars = len(uniq)
    if open_session and not segs and not issues:
        return "IN_PROGRESS", bars, [], issues
    if not segs and not issues:
        return "COMPLETE", bars, [], issues
    return "PARTIAL", bars, segs, issues


def _futures_day(root: Path, symbol: str, day: date, now: datetime) -> dict:
    path = root / "data" / "journal" / "dhan_futures" / symbol / f"{day.isoformat()}.parquet"
    outside = day < as_ist(now).date() - timedelta(days=FUT_LOOKBACK_DAYS)
    expected, open_session = _expected_minutes(day, now)
    block = _futures_block(symbol, day, now, outside)
    if not path.exists():
        if outside:
            status = "UNFILLABLE"
        elif open_session and not expected:
            status = "IN_PROGRESS"
        else:
            status = "MISSING"
        return {
            "status": status,
            "bars": None if status == "UNFILLABLE" else 0,
            "expected_bars": len(_GRID) if not open_session else len(expected),
            "missing_segments": [],
            "issues": [],
            "repair_block": block,
        }
    try:
        frame = _read_parquet(path, ["time_ist", "open", "high", "low", "close"])
    except Exception:
        return {
            "status": "PARTIAL",
            "bars": None,
            "expected_bars": len(expected),
            "missing_segments": [],
            "issues": ["unreadable"],
            "repair_block": block,
        }
    times = [t for t in (_norm_hhmm(v) for v in frame["time_ist"]) if t]
    ok = _ohlc_ok(frame)
    status, bars, segs, issues = _score_presence(times, expected, open_session=open_session, ohlc_ok=ok)
    if outside and status == "MISSING":
        status = "UNFILLABLE"
    return {
        "status": status,
        "bars": bars,
        "expected_bars": len(_GRID) if not open_session else len(expected),
        "missing_segments": segs,
        "issues": issues,
        "repair_block": block if status in ("MISSING", "PARTIAL", "UNFILLABLE") else None,
    }


def _futures_block(symbol: str, day: date, now: datetime, outside: bool) -> str | None:
    if symbol not in FUTURES_REPAIR_SYMBOLS:
        return "Sensex futures backfill is not available; the script covers NIFTY and BANKNIFTY"
    if outside:
        return "outside Dhan 90-day intraday window"
    now = as_ist(now)
    if day == now.date() and is_trading_day(day) and time(9, 15) <= now.time().replace(tzinfo=None) < time(15, 30):
        return "futures rewrite refused during the open session"
    return None


def _options_day(root: Path, symbol: str, day: date, now: datetime) -> dict:
    path = root / "data" / "journal" / "dhan_intraday" / symbol / f"{day.isoformat()}.parquet"
    expected, open_session = _expected_minutes(day, now)
    live_block = None
    now_ist = as_ist(now)
    if day == now_ist.date() and is_trading_day(day) and time(9, 15) <= now_ist.time().replace(tzinfo=None) < time(15, 30):
        live_block = "confirm_live_session"
    if not path.exists():
        if open_session and not expected:
            status = "IN_PROGRESS"
        else:
            status = "MISSING"
        return {
            "status": status,
            "missing_segments": [],
            "issues": [],
            "needs_live_confirm": bool(live_block) and status in ("MISSING", "PARTIAL"),
            "repair_block": None,
        }
    try:
        frame = _read_parquet(path, ["time_ist", "off", "side"])
    except Exception:
        return {
            "status": "PARTIAL",
            "missing_segments": [],
            "issues": ["unreadable"],
            "needs_live_confirm": bool(live_block),
            "repair_block": OPTIONS_ON_DISK,
        }
    if frame.empty:
        return {
            "status": "MISSING",
            "missing_segments": [],
            "issues": [],
            "needs_live_confirm": bool(live_block),
            "repair_block": OPTIONS_ON_DISK,
        }
    present: dict[str, set[tuple[int, str]]] = {}
    dup = False
    seen: set[tuple[str, int, str]] = set()
    for raw_time, raw_off, raw_side in zip(frame["time_ist"], frame["off"], frame["side"]):
        hhmm = _norm_hhmm(raw_time)
        if hhmm is None or hhmm not in _GRID_SET:
            continue
        try:
            off = int(raw_off)
        except (TypeError, ValueError):
            continue
        side = str(raw_side).lower()
        key = (hhmm, off, side)
        if key in seen:
            dup = True
        seen.add(key)
        if off in OFFSETS and side in SIDES:
            present.setdefault(hhmm, set()).add((off, side))
    issues = ["duplicate_minutes"] if dup else []
    short = [m for m in expected if len(present.get(m, ())) < PAIRS_PER_MINUTE]
    segs = missing_segments(expected, set(expected) - set(short)) if short else []
    # missing_segments above treats short minutes as absent, which is what the operator needs.
    if open_session and not short and not issues:
        status = "IN_PROGRESS"
    elif not short and not issues:
        status = "COMPLETE"
    else:
        status = "PARTIAL"
    return {
        "status": status,
        "missing_segments": segs,
        "issues": issues,
        "needs_live_confirm": bool(live_block) and status in ("MISSING", "PARTIAL"),
        "repair_block": OPTIONS_ON_DISK if status == "PARTIAL" else None,
    }


def _csv_status(path: Path, due: bool) -> str:
    if not path.exists():
        return "MISSING" if due else "PENDING"
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        return "PARTIAL"
    if not rows:
        return "PARTIAL"
    return "COMPLETE"


def _sentinel_day(root: Path, symbol: str, day: date, now: datetime) -> dict:
    db_path = root / "data" / "sentinel.db"
    expected, open_session = _expected_minutes(day, now)
    inst = f"{symbol}-FUT"
    if not db_path.exists():
        status = "IN_PROGRESS" if open_session and not expected else "MISSING"
        return {
            "status": status,
            "bars": 0,
            "cvd_present": False,
            "missing_segments": [],
            "issues": [],
        }
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return {
            "status": "PARTIAL", "bars": None, "cvd_present": False,
            "missing_segments": [], "issues": ["unreadable"],
        }
    try:
        lo = (day - timedelta(days=1)).isoformat()
        hi = (day + timedelta(days=2)).isoformat()
        try:
            rows = conn.execute(
                """
                SELECT start_ts FROM sentinel_bars
                 WHERE instrument_id = ? AND timeframe = '1m'
                   AND start_ts >= ? AND start_ts < ?
                """,
                (inst, lo, hi),
            ).fetchall()
        except sqlite3.Error:
            return {
                "status": "PARTIAL", "bars": None, "cvd_present": False,
                "missing_segments": [], "issues": ["unreadable"],
            }
        times: list[str] = []
        for (raw,) in rows:
            times.extend(_ist_hhmm_on_day(raw, day))
        cvd = False
        try:
            hit = conn.execute(
                """
                SELECT 1 FROM sentinel_daily_cvd
                 WHERE trade_date = ? AND (instrument_id = ? OR instrument_id = ? OR symbol = ?)
                 LIMIT 1
                """,
                (day.isoformat(), inst, symbol, symbol),
            ).fetchone()
            cvd = hit is not None
        except sqlite3.Error:
            cvd = False
    finally:
        conn.close()
    status, bars, segs, issues = _score_presence(times, expected, open_session=open_session, ohlc_ok=True)
    return {
        "status": status,
        "bars": bars,
        "cvd_present": cvd,
        "missing_segments": segs,
        "issues": issues,
    }


def _ist_hhmm_on_day(raw, day: date) -> list[str]:
    if raw is None:
        return []
    text = str(raw).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        hhmm = _norm_hhmm(text)
        return [hhmm] if hhmm else []
    parsed = as_ist(parsed)
    if parsed.date() != day:
        return []
    return [parsed.strftime("%H:%M")]


def _blank(kind: str) -> dict:
    return {
        "futures_status": kind,
        "futures_bars": None,
        "futures_expected_bars": None,
        "options_matrix_status": kind,
        "sentinel_status": kind,
        "sentinel_bars": None,
        "sentinel_cvd_present": False,
        "bhavcopy_status": kind,
        "participant_status": kind,
        "missing_segments": [],
        "issues": [],
        "repairs": [],
        "repair_available": False,
        "repair_block_reason": None,
    }


def _any_file(root: Path, symbol: str, day: date) -> bool:
    iso = day.isoformat()
    candidates = (
        root / "data" / "journal" / "dhan_futures" / symbol / f"{iso}.parquet",
        root / "data" / "journal" / "dhan_intraday" / symbol / f"{iso}.parquet",
        root / "data" / "journal" / "options_eod" / symbol / f"{iso}.csv",
        root / "data" / "journal" / "participant_oi" / f"{iso}.csv",
    )
    return any(p.exists() for p in candidates)


def _repairs(fut: dict, opt: dict) -> list[dict]:
    out = []
    for task_type, block, status, live in (
        ("backfill_futures", fut.get("repair_block"), fut["status"], False),
        ("backfill_options", opt.get("repair_block"), opt["status"], opt.get("needs_live_confirm", False)),
    ):
        available = status in ("MISSING", "PARTIAL") and not block
        out.append({
            "task_type": task_type,
            "available": available,
            "block_reason": None if available else (block or _idle_reason(status)),
            "needs_live_confirm": bool(live) and available,
        })
    return out


def _idle_reason(status: str) -> str | None:
    if status in ("COMPLETE", "IN_PROGRESS", "PENDING", "HOLIDAY", "WEEKEND", "SPECIAL_SESSION"):
        return None
    if status == "UNFILLABLE":
        return "outside Dhan 90-day intraday window"
    return None


def _rollup(fut: str, opt: str, sent: str, bhav: str, part: str) -> str:
    series = (fut, opt, sent, bhav, part)
    if any(s == "PARTIAL" for s in series):
        return "gapped"
    if fut == "UNFILLABLE":
        return "unfillable"
    if any(s == "MISSING" for s in series):
        return "missing"
    if any(s == "IN_PROGRESS" for s in (fut, opt, sent)):
        return "in_progress"
    return "complete"


def audit_range(root: Path, symbol: str, start_date: str, end_date: str,
                now: datetime | None = None) -> dict:
    """Day-by-day audit. ``root`` is the repo (or a temp tree) that contains ``data/``."""
    sym = _check_symbol(symbol)
    start = _parse_day(start_date)
    end = _parse_day(end_date)
    if end < start:
        raise ValueError("end_date is before start_date")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise ValueError(f"range is longer than {MAX_SPAN_DAYS} calendar days")
    now = as_ist(now or datetime.now(IST))
    root = Path(root)

    days = []
    summary = {
        "trading_days": 0,
        "holidays": 0,
        "weekends": 0,
        "complete_days": 0,
        "gapped_days": 0,
        "missing_days": 0,
        "unfillable_days": 0,
        "participant_oi_missing_days": 0,
    }
    participant_missing: set[str] = set()
    day = start
    while day <= end:
        iso = day.isoformat()
        holiday_name = get_holiday_name(day)
        weekend = day.weekday() >= 5
        trading = is_trading_day(day)
        base = {
            "date": iso,
            "weekday": day.strftime("%A"),
            "holiday_name": holiday_name,
            "is_trading_day": trading,
        }
        if not trading:
            kind = "WEEKEND" if weekend else "HOLIDAY"
            if _any_file(root, sym, day):
                kind = "SPECIAL_SESSION"
                fut = _futures_day(root, sym, day, now)
                row = _blank("SPECIAL_SESSION")
                row["futures_bars"] = fut["bars"]
                row["futures_status"] = "SPECIAL_SESSION"
                row["session_kind"] = kind
                # Observed minutes only. Do not score a short session against 375.
            else:
                row = _blank(kind)
                row["session_kind"] = kind
            if kind == "WEEKEND":
                summary["weekends"] += 1
            elif kind == "HOLIDAY":
                summary["holidays"] += 1
            row.update(base)
            row["is_trading_day"] = False
            days.append(row)
            day += timedelta(days=1)
            continue

        if day > now.date():
            row = _blank("PENDING")
            row["session_kind"] = "PENDING"
            row.update(base)
            days.append(row)
            day += timedelta(days=1)
            continue

        summary["trading_days"] += 1
        fut = _futures_day(root, sym, day, now)
        opt = _options_day(root, sym, day, now)
        sent = _sentinel_day(root, sym, day, now)
        due = _eod_due(day, now)
        bhav = _csv_status(root / "data" / "journal" / "options_eod" / sym / f"{iso}.csv", due)
        part = _csv_status(root / "data" / "journal" / "participant_oi" / f"{iso}.csv", due)
        if part == "MISSING":
            participant_missing.add(iso)
        repairs = _repairs(fut, opt)
        segments = list(dict.fromkeys(fut["missing_segments"] + opt["missing_segments"] + sent["missing_segments"]))
        issues = list(dict.fromkeys(fut["issues"] + opt["issues"] + sent["issues"]))
        available = [r for r in repairs if r["available"]]
        blocked = [r["block_reason"] for r in repairs if r["block_reason"]]
        row = {
            **base,
            "session_kind": "REGULAR",
            "futures_status": fut["status"],
            "futures_bars": fut["bars"],
            "futures_expected_bars": fut["expected_bars"],
            "options_matrix_status": opt["status"],
            "sentinel_status": sent["status"],
            "sentinel_bars": sent["bars"],
            "sentinel_cvd_present": sent["cvd_present"],
            "bhavcopy_status": bhav,
            "participant_status": part,
            "missing_segments": segments,
            "issues": issues,
            "repairs": repairs,
            "repair_available": bool(available),
            "repair_block_reason": None if available else ("; ".join(b for b in blocked if b) or None),
        }
        bucket = _rollup(fut["status"], opt["status"], sent["status"], bhav, part)
        if bucket == "gapped":
            summary["gapped_days"] += 1
        elif bucket == "missing":
            summary["missing_days"] += 1
        elif bucket == "unfillable":
            summary["unfillable_days"] += 1
        elif bucket == "complete":
            summary["complete_days"] += 1
        days.append(row)
        day += timedelta(days=1)

    summary["participant_oi_missing_days"] = len(participant_missing)
    return {
        "symbol": sym,
        "calendar": "NSE",
        "calendar_note": "NSE holiday list applied to Sensex" if sym == "SENSEX" else None,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "summary": summary,
        "days": days,
    }
