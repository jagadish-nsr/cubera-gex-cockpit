"""Gated repair. Dry-run does not spawn. Confirm is an allowlisted argv."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path

import pytest

import data.holidays as holidays
from app.admin import db
from app.admin.clock import IST
from app.admin.task_runner import (
    AdminRequestError,
    _procs,
    _reap,
    cancel,
    handle,
    mark_orphaned_tasks,
)


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
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
    monkeypatch.setattr("app.admin.task_runner.ROOT", tmp_path)
    db.set_db_path(tmp_path / "admin_ops.db")
    _procs.clear()
    yield
    _procs.clear()
    db.set_db_path(None)


def _opts(day: str, **extra) -> dict:
    body = {
        "task_type": "backfill_options",
        "symbol": "NIFTY",
        "start_date": day,
        "end_date": day,
    }
    body.update(extra)
    return body


def test_dry_run_does_not_spawn():
    spawned = []
    result = handle(
        _opts("2026-10-21", dry_run=True),
        now=datetime(2026, 10, 24, 20, 0, tzinfo=IST),
        spawn_fn=lambda *args: spawned.append(args),
    )
    assert spawned == []
    assert result["dry_run"] is True
    assert result["argv"][-3:] == ["2026-10-21", "2026-10-21", "NIFTY"]
    assert result["argv"][1].endswith("dhan_backfill.py")
    assert db.running_tasks() == []


def test_confirm_uses_the_validated_argv_and_rejects_shell_text():
    spawned = []

    def _spawn(argv, log_path, task_id):
        spawned.append(list(argv))

    now = datetime(2026, 10, 24, 20, 0, tzinfo=IST)
    result = handle(_opts("2026-10-21", confirm=True), now=now, spawn_fn=_spawn)
    assert result["status"] == "ok"
    assert spawned[0][-3:] == ["2026-10-21", "2026-10-21", "NIFTY"]
    assert all(";" not in part for part in spawned[0])

    with pytest.raises(AdminRequestError) as exc:
        handle(_opts("2026-10-21;rm", dry_run=True), now=now)
    assert exc.value.status_code == 400


def test_options_confirm_refuses_a_day_already_on_disk(tmp_path):
    day = "2026-10-22"
    folder = tmp_path / "data" / "journal" / "dhan_intraday" / "NIFTY"
    folder.mkdir(parents=True)
    (folder / f"{day}.parquet").write_bytes(b"present")
    spawned = []
    now = datetime(2026, 10, 24, 20, 0, tzinfo=IST)
    with pytest.raises(AdminRequestError, match="does not rewrite") as exc:
        handle(_opts(day, confirm=True), now=now, spawn_fn=lambda *args: spawned.append(args))
    assert exc.value.status_code == 400
    assert spawned == []
    assert db.running_tasks() == []


def test_futures_outside_the_window_and_during_the_open_session_are_refused():
    now = datetime(2026, 10, 24, 20, 0, tzinfo=IST)
    with pytest.raises(AdminRequestError) as old:
        handle({
            "task_type": "backfill_futures",
            "symbol": "NIFTY",
            "start_date": "2026-07-20",
            "end_date": "2026-07-20",
            "confirm": True,
        }, now=now)
    assert old.value.status_code == 400

    session = datetime(2026, 10, 23, 12, 0, tzinfo=IST)
    with pytest.raises(AdminRequestError) as live:
        handle({
            "task_type": "backfill_futures",
            "symbol": "NIFTY",
            "start_date": "2026-10-23",
            "end_date": "2026-10-23",
            "confirm": True,
            "confirm_live_session": True,
        }, now=session)
    assert "open session" in live.value.detail


def test_options_open_session_needs_the_second_confirm():
    session = datetime(2026, 10, 23, 12, 0, tzinfo=IST)
    with pytest.raises(AdminRequestError):
        handle(_opts("2026-10-23", confirm=True), now=session)
    spawned = []
    handle(
        _opts("2026-10-23", confirm=True, confirm_live_session=True),
        now=session,
        spawn_fn=lambda argv, log_path, task_id: spawned.append(argv),
    )
    assert spawned


def test_second_task_for_the_same_output_is_rejected():
    db.insert_task({
        "task_id": "task_existing",
        "task_type": "backfill_options",
        "argv_json": "[]",
        "status": "RUNNING",
        "params_json": json.dumps({
            "symbol": "NIFTY", "start_date": "2026-10-21", "end_date": "2026-10-21", "store": "NIFTY",
        }),
        "started_at": "2026-10-24T20:00:00+05:30",
        "log_path": "unused",
    })
    spawned = []
    with pytest.raises(AdminRequestError) as exc:
        handle(
            _opts("2026-10-21", confirm=True),
            now=datetime(2026, 10, 24, 20, 0, tzinfo=IST),
            spawn_fn=lambda *args: spawned.append(args),
        )
    assert exc.value.status_code == 409
    assert spawned == []


def test_startup_marks_a_running_task_interrupted():
    db.insert_task({
        "task_id": "task_orphan",
        "task_type": "backfill_futures",
        "argv_json": "[]",
        "status": "RUNNING",
        "params_json": "{}",
        "started_at": "2026-10-24T20:00:00+05:30",
        "log_path": "unused",
    })
    assert mark_orphaned_tasks(datetime(2026, 10, 24, 21, 0, tzinfo=IST)) == 1
    assert db.get_task("task_orphan")["status"] == "INTERRUPTED"


def test_cancel_terminates_only_that_child():
    db.insert_task({
        "task_id": "task_live",
        "task_type": "backfill_options",
        "argv_json": "[]",
        "status": "RUNNING",
        "params_json": "{}",
        "started_at": "2026-10-24T20:00:00+05:30",
        "log_path": "unused",
    })

    class _Proc:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    proc = _Proc()
    _procs["task_live"] = proc
    cancel("task_live", now=datetime(2026, 10, 24, 21, 0, tzinfo=IST))
    assert proc.terminated is True
    assert db.get_task("task_live")["status"] == "INTERRUPTED"


def test_interrupted_reap_drops_the_process():
    db.insert_task({
        "task_id": "task_live",
        "task_type": "backfill_options",
        "argv_json": "[]",
        "status": "INTERRUPTED",
        "params_json": "{}",
        "started_at": "2026-10-24T20:00:00+05:30",
        "log_path": "unused",
    })

    class _Handle:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class _Proc:
        def wait(self):
            return -15

    handle = _Handle()
    _procs["task_live"] = _Proc()
    _reap("task_live", _procs["task_live"], handle, Path("unused"))
    assert "task_live" not in _procs
    assert handle.closed is True
    assert db.get_task("task_live")["exit_code"] == -15


def test_options_confirm_rejects_a_future_date():
    now = datetime(2026, 10, 24, 20, 0, tzinfo=IST)
    with pytest.raises(AdminRequestError, match="future") as exc:
        handle(_opts("2026-10-30", confirm=True), now=now, spawn_fn=lambda *a: None)
    assert exc.value.status_code == 400


def test_admin_package_does_not_call_the_cleanse_script():
    root = Path(__file__).resolve().parents[1] / "app" / "admin"
    for path in root.glob("*.py"):
        assert "db_cleanse_and_sync" not in path.read_text(encoding="utf-8")


def test_routes_are_registered_and_reject_a_bad_symbol():
    from web_server import app
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/admin/audit" in paths
    assert "/api/admin/status" in paths
    assert "/api/admin/tasks/run" in paths
    assert "/api/admin/tasks/{task_id}/cancel" in paths

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/api/admin/audit",
        "raw_path": b"/api/admin/audit",
        "query_string": b"start_date=2026-10-18&end_date=2026-10-24&symbol=NIF",
        "headers": [],
    }
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    status = []

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])

    asyncio.run(app(scope, receive, send))
    assert status == [400]
