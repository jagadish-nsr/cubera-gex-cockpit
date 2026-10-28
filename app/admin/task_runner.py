"""Allowlisted backfill spawn. Argv only — nothing is passed through a shell.

``backfill_options`` runs ``scripts/dhan_backfill.py``. ``backfill_futures`` runs
``scripts/dhan_backfill_futures.py`` with an explicit start and end. A dry-run
returns the plan and does not spawn. Confirm is a second request with the same
parameters.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from data.holidays import is_trading_day

from app.admin import db
from app.admin.clock import as_ist, is_regular_session, now_ist

log = logging.getLogger("cubera.admin.tasks")


def _task_event(name: str, **fields) -> None:
    from app.obs import event
    level = fields.pop("level", "INFO" if fields.get("ok", True) else "ERROR")
    event(name, workflow="admin.task", level=level, **fields)

ROOT = Path(__file__).resolve().parents[2]
OPTIONS_SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX")
FUTURES_SYMBOLS = ("NIFTY", "BANKNIFTY")
MAX_CALENDAR_DAYS = 31
FUT_LOOKBACK_DAYS = 89
PAGE_DAYS = 42

_procs: dict[str, subprocess.Popen] = {}
_proc_lock = threading.Lock()
_claim_lock = threading.Lock()


class AdminRequestError(Exception):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _parse_day(value: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise AdminRequestError(f"date must be YYYY-MM-DD, got {value!r}")
    # Reject anything that is not exactly four-two-two digits. fromisoformat
    # accepts some alternate forms that must not reach an argv list.
    parts = value.split("-")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise AdminRequestError(f"date must be YYYY-MM-DD, got {value!r}")
    if not (len(parts[0]) == 4 and len(parts[1]) == 2 and len(parts[2]) == 2):
        raise AdminRequestError(f"date must be YYYY-MM-DD, got {value!r}")
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as exc:
        raise AdminRequestError(f"date must be YYYY-MM-DD, got {value!r}") from exc


def _span_ok(start: date, end: date) -> None:
    if end < start:
        raise AdminRequestError("end_date is before start_date")
    if (end - start).days + 1 > MAX_CALENDAR_DAYS:
        raise AdminRequestError(f"range is longer than {MAX_CALENDAR_DAYS} calendar days")


def _trading_days(start: date, end: date) -> list[date]:
    out = []
    cursor = start
    while cursor <= end:
        if is_trading_day(cursor):
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _page_count(start: date, end: date) -> int:
    pages = 0
    hi = end
    while hi >= start:
        lo = max(start, hi - timedelta(days=PAGE_DAYS - 1))
        pages += 1
        hi = lo - timedelta(days=1)
    return pages


def _argv(task_type: str, symbol: str, start: date, end: date) -> list[str]:
    if task_type == "backfill_options":
        script = ROOT / "scripts" / "dhan_backfill.py"
        return [sys.executable, str(script), start.isoformat(), end.isoformat(), symbol]
    if task_type == "backfill_futures":
        script = ROOT / "scripts" / "dhan_backfill_futures.py"
        return [sys.executable, str(script), symbol, start.isoformat(), end.isoformat()]
    raise AdminRequestError("task_type must be backfill_options or backfill_futures")


def _validate(body: dict, now: datetime) -> tuple[str, str, date, date]:
    task_type = body.get("task_type")
    if task_type not in ("backfill_options", "backfill_futures"):
        raise AdminRequestError("task_type must be backfill_options or backfill_futures")
    symbol = str(body.get("symbol") or "").strip().upper()
    allowed = OPTIONS_SYMBOLS if task_type == "backfill_options" else FUTURES_SYMBOLS
    if symbol not in allowed:
        if task_type == "backfill_futures" and symbol == "SENSEX":
            raise AdminRequestError(
                "Sensex futures backfill is not available; the script covers NIFTY and BANKNIFTY"
            )
        raise AdminRequestError(f"symbol must be one of {', '.join(allowed)}")
    start = _parse_day(body.get("start_date"))
    end = _parse_day(body.get("end_date"))
    _span_ok(start, end)
    today = as_ist(now).date()
    if end > today:
        raise AdminRequestError("end_date cannot be in the future")
    if task_type == "backfill_futures":
        floor = today - timedelta(days=FUT_LOOKBACK_DAYS)
        if start < floor or end > today:
            raise AdminRequestError(
                "futures backfill must stay inside the Dhan 90-day near-month window"
            )
        if is_regular_session(now) and start <= today <= end:
            raise AdminRequestError(
                "futures rewrite refused during the open session"
            )
    if task_type == "backfill_options" and is_regular_session(now) and start <= today <= end:
        if not body.get("confirm_live_session"):
            raise AdminRequestError(
                "options backfill includes the open session; resend with confirm_live_session true"
            )
    return task_type, symbol, start, end


def _dry_plan(task_type: str, symbol: str, start: date, end: date, argv: list[str]) -> dict:
    days = _trading_days(start, end)
    if task_type == "backfill_options":
        folder = ROOT / "data" / "journal" / "dhan_intraday" / symbol
        on_disk = [d for d in days if (folder / f"{d.isoformat()}.parquet").exists()]
        return {
            "dry_run": True,
            "task_type": task_type,
            "symbol": symbol,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "trading_days": len(days),
            "days_on_disk": len(on_disk),
            "days_to_fetch": len(days) - len(on_disk),
            "pages": _page_count(start, end),
            "rewrites_existing": False,
            "argv": argv,
        }
    folder = ROOT / "data" / "journal" / "dhan_futures" / symbol
    on_disk = [d for d in days if (folder / f"{d.isoformat()}.parquet").exists()]
    return {
        "dry_run": True,
        "task_type": task_type,
        "symbol": symbol,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "trading_days": len(days),
        "days_on_disk": len(on_disk),
        "days_to_fetch": len(days),
        "pages": 1,
        "rewrites_existing": True,
        "note": "Rewrites sessions the API returns. Dhan serves only the active near-month contract.",
        "argv": argv,
    }


def _ranges_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start <= b_end and b_start <= a_end


def _lock_conflict(task_type: str, symbol: str, start: date, end: date) -> dict | None:
    for row in db.active_tasks():
        if row["task_type"] != task_type:
            continue
        try:
            params = json.loads(row["params_json"])
        except json.JSONDecodeError:
            continue
        if params.get("symbol") != symbol:
            continue
        if _ranges_overlap(start.isoformat(), end.isoformat(), params.get("start_date", ""), params.get("end_date", "")):
            return row
    return None


def _task_id(now: datetime) -> str:
    stamp = as_ist(now).strftime("%Y%m%d_%H%M%S")
    return f"task_{stamp}_{uuid.uuid4().hex[:4]}"


def _reap(task_id: str, proc, handle, log_path: Path) -> None:
    """Wait for ``proc``, then drop it. An interrupted task must not stay in ``_procs``."""
    code = None
    try:
        code = proc.wait()
    finally:
        try:
            handle.close()
        finally:
            with _proc_lock:
                _procs.pop(task_id, None)
    if code is None:
        return
    current = db.get_task(task_id)
    if current and current["status"] == "INTERRUPTED":
        db.update_task(task_id, exit_code=code, tail_snippet=_tail_text(log_path))
        _task_event("admin.task.finished", ok=False, task_id=task_id, exit_code=code, message="interrupted")
        return
    status = "COMPLETED" if code == 0 else "FAILED"
    db.update_task(
        task_id,
        status=status,
        ended_at=as_ist(now_ist()).isoformat(timespec="seconds"),
        exit_code=code,
        tail_snippet=_tail_text(log_path),
    )
    _task_event(
        "admin.task.finished",
        ok=code == 0,
        task_id=task_id,
        exit_code=code,
        level="INFO" if code == 0 else "ERROR",
    )


def spawn(argv: list[str], log_path: Path, task_id: str) -> subprocess.Popen:
    """Start ``argv`` with stdout appended to ``log_path``. Tests replace this."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "w", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(
        argv,
        stdout=handle,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
    )
    with _proc_lock:
        _procs[task_id] = proc

    threading.Thread(
        target=_reap,
        args=(task_id, proc, handle, log_path),
        name=f"admin-task-{task_id}",
        daemon=True,
    ).start()
    return proc


def _tail_text(path: Path, limit: int = 500) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-limit:])[:2048]


def tail_lines(path: Path, limit: int = 500) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-limit:]


def handle(body: dict, now: datetime | None = None, spawn_fn=None) -> dict:
    now = as_ist(now or now_ist())
    if not isinstance(body, dict):
        raise AdminRequestError("body must be a JSON object")
    if not body.get("dry_run") and not body.get("confirm"):
        raise AdminRequestError("set dry_run or confirm")
    task_type, symbol, start, end = _validate(body, now)
    argv = _argv(task_type, symbol, start, end)
    plan = _dry_plan(task_type, symbol, start, end, argv)
    if body.get("dry_run"):
        return plan
    if task_type == "backfill_options" and plan["days_to_fetch"] == 0:
        raise AdminRequestError("options backfill does not rewrite a day already on disk")
    task_id = _task_id(now)
    log_path = ROOT / "logs" / "admin_tasks" / f"{task_id}.log"
    params = {
        "symbol": symbol,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "store": symbol,
    }
    # Claim the row before spawn. Two confirms otherwise both see no RUNNING
    # row and start two subprocesses.
    with _claim_lock:
        conflict = _lock_conflict(task_type, symbol, start, end)
        if conflict is not None:
            raise AdminRequestError(
                f"{task_type} is already running for {symbol} ({conflict['task_id']})",
                status_code=409,
            )
        db.insert_task({
            "task_id": task_id,
            "task_type": task_type,
            "argv_json": json.dumps(argv),
            "status": "RUNNING",
            "params_json": json.dumps(params),
            "started_at": now.isoformat(timespec="seconds"),
            "log_path": str(log_path),
        })
    try:
        (spawn_fn or spawn)(argv, log_path, task_id)
    except Exception as exc:
        db.update_task(task_id, status="FAILED", ended_at=now.isoformat(timespec="seconds"), exit_code=-1,
                       tail_snippet=str(exc)[:2048])
        _task_event("admin.task.finished", ok=False, task_id=task_id, task_type=task_type, symbol=symbol, error=str(exc))
        raise AdminRequestError(f"failed to start task: {exc}") from exc
    _task_event("admin.task.started", task_id=task_id, task_type=task_type, symbol=symbol)
    return {"status": "ok", "task_id": task_id, "message": "Task queued in background"}


def task_view(task_id: str) -> dict:
    row = db.get_task(task_id)
    if row is None:
        raise AdminRequestError("unknown task", status_code=404)
    log_path = Path(row["log_path"])
    try:
        params = json.loads(row["params_json"])
    except json.JSONDecodeError:
        params = {}
    return {
        "task_id": row["task_id"],
        "task_type": row["task_type"],
        "status": row["status"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "exit_code": row["exit_code"],
        "params": params,
        "log_tail": tail_lines(log_path),
    }


def cancel(task_id: str, now: datetime | None = None) -> dict:
    row = db.get_task(task_id)
    if row is None:
        raise AdminRequestError("unknown task", status_code=404)
    if row["status"] != "RUNNING":
        raise AdminRequestError("task is not running", status_code=409)
    with _proc_lock:
        proc = _procs.get(task_id)
    if proc is not None and proc.poll() is None:
        proc.terminate()
    now = as_ist(now or now_ist())
    db.update_task(task_id, status="INTERRUPTED", ended_at=now.isoformat(timespec="seconds"))
    return {"status": "ok", "task_id": task_id}


def mark_orphaned_tasks(now: datetime | None = None) -> int:
    now = as_ist(now or now_ist())
    return db.mark_orphaned_running(now.isoformat(timespec="seconds"))
