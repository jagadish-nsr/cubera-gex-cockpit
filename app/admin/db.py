"""SQLite ledger for admin alerts, backfill tasks, and operational events.

Market databases are not reused here. The file is ``data/journal/admin_ops.db``
unless a test points ``set_db_path`` at a temporary file.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_lock = threading.Lock()
_db_path: Path | None = None
_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_alerts (
    alert_id     TEXT PRIMARY KEY,
    fingerprint  TEXT NOT NULL UNIQUE,
    severity     TEXT NOT NULL,
    category     TEXT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL,
    session_date TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    resolved_at  TEXT,
    is_read      INTEGER NOT NULL DEFAULT 0,
    is_dismissed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admin_tasks (
    task_id      TEXT PRIMARY KEY,
    task_type    TEXT NOT NULL,
    argv_json    TEXT NOT NULL,
    status       TEXT NOT NULL,
    params_json  TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    exit_code    INTEGER,
    log_path     TEXT NOT NULL,
    tail_snippet TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON admin_tasks(status, started_at DESC);

CREATE TABLE IF NOT EXISTS ops_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    process     TEXT NOT NULL,
    event       TEXT NOT NULL,
    level       TEXT NOT NULL,
    trace_id    TEXT,
    symbol      TEXT,
    duration_ms REAL,
    message     TEXT NOT NULL,
    fields_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ops_events_ts ON ops_events(ts);
CREATE INDEX IF NOT EXISTS idx_ops_events_proc ON ops_events(process, event, ts);

CREATE TABLE IF NOT EXISTS ops_status (
    process          TEXT NOT NULL,
    name             TEXT NOT NULL,
    last_ok_at       TEXT,
    last_error_at    TEXT,
    ok_count         INTEGER NOT NULL DEFAULT 0,
    error_count      INTEGER NOT NULL DEFAULT 0,
    last_duration_ms REAL,
    detail_json      TEXT NOT NULL DEFAULT '{}',
    pid              INTEGER,
    beat_at          TEXT,
    PRIMARY KEY (process, name)
);
"""


def set_db_path(path: Path | None) -> None:
    """Point the ledger at ``path``. ``None`` restores the repo default."""
    global _db_path, _ready
    with _lock:
        _db_path = path
        _ready = False


def path_is_override() -> bool:
    """True when a test pointed the ledger away from the repo file."""
    return _db_path is not None


def db_path() -> Path:
    if _db_path is not None:
        return _db_path
    return ROOT / "data" / "journal" / "admin_ops.db"


def _connect() -> sqlite3.Connection:
    global _ready
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    if not _ready:
        conn.executescript(_SCHEMA)
        _ready = True
    return conn


def _row_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def upsert_alert(
    *,
    alert_id: str,
    fingerprint: str,
    severity: str,
    category: str,
    title: str,
    description: str,
    session_date: str | None,
    now_iso: str,
) -> None:
    with _lock:
        conn = _connect()
        try:
            existing = conn.execute(
                "SELECT * FROM admin_alerts WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO admin_alerts (
                        alert_id, fingerprint, severity, category, title, description,
                        session_date, created_at, updated_at, resolved_at, is_read, is_dismissed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 0)
                    """,
                    (
                        alert_id, fingerprint, severity, category, title, description,
                        session_date, now_iso, now_iso,
                    ),
                )
            else:
                reopened = existing["resolved_at"] is not None
                conn.execute(
                    """
                    UPDATE admin_alerts
                       SET severity = ?, category = ?, title = ?, description = ?,
                           session_date = ?, updated_at = ?, resolved_at = NULL,
                           is_read = ?, is_dismissed = ?
                     WHERE fingerprint = ?
                    """,
                    (
                        severity, category, title, description, session_date, now_iso,
                        0 if reopened else existing["is_read"],
                        0 if reopened else existing["is_dismissed"],
                        fingerprint,
                    ),
                )
            conn.commit()
        finally:
            conn.close()


def resolve_alert(fingerprint: str, now_iso: str) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE admin_alerts
                   SET resolved_at = ?, updated_at = ?
                 WHERE fingerprint = ? AND resolved_at IS NULL
                """,
                (now_iso, now_iso, fingerprint),
            )
            conn.commit()
        finally:
            conn.close()


def list_alerts(*, unread_only: bool = False, severity: str | None = None,
                include_resolved: bool = False) -> list[dict]:
    clauses = []
    params: list = []
    if not include_resolved:
        clauses.append("resolved_at IS NULL")
        clauses.append("is_dismissed = 0")
    if unread_only:
        clauses.append("is_read = 0")
    if severity:
        clauses.append("severity = ?")
        params.append(severity)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM admin_alerts{where} ORDER BY updated_at DESC",
                params,
            ).fetchall()
            return [_row_dict(r) for r in rows]
        finally:
            conn.close()


def badge_count() -> int:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM admin_alerts
                 WHERE resolved_at IS NULL AND is_dismissed = 0
                """
            ).fetchone()
            return int(row["n"])
        finally:
            conn.close()


def ack_alert(alert_id: str, action: str, now_iso: str) -> bool:
    if action not in ("read", "dismiss"):
        raise ValueError("action must be read or dismiss")
    with _lock:
        conn = _connect()
        try:
            if action == "read":
                cur = conn.execute(
                    "UPDATE admin_alerts SET is_read = 1, updated_at = ? WHERE alert_id = ?",
                    (now_iso, alert_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE admin_alerts
                       SET is_dismissed = 1, is_read = 1, updated_at = ?
                     WHERE alert_id = ?
                    """,
                    (now_iso, alert_id),
                )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def insert_task(row: dict) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO admin_tasks (
                    task_id, task_type, argv_json, status, params_json,
                    started_at, ended_at, exit_code, log_path, tail_snippet
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["task_id"], row["task_type"], row["argv_json"], row["status"],
                    row["params_json"], row["started_at"], row.get("ended_at"),
                    row.get("exit_code"), row["log_path"], row.get("tail_snippet") or "",
                ),
            )
            conn.commit()
        finally:
            conn.close()


def update_task(task_id: str, **fields) -> None:
    if not fields:
        return
    allowed = {"status", "ended_at", "exit_code", "tail_snippet", "argv_json"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown task fields: {sorted(unknown)}")
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                f"UPDATE admin_tasks SET {sets} WHERE task_id = ?",
                (*fields.values(), task_id),
            )
            conn.commit()
        finally:
            conn.close()


def get_task(task_id: str) -> dict | None:
    with _lock:
        conn = _connect()
        try:
            return _row_dict(conn.execute(
                "SELECT * FROM admin_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone())
        finally:
            conn.close()


def running_tasks() -> list[dict]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM admin_tasks WHERE status = 'RUNNING'"
            ).fetchall()
            return [_row_dict(r) for r in rows]
        finally:
            conn.close()


def _loads(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def insert_ops_event(row: dict) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO ops_events (
                    ts, process, event, level, trace_id, symbol,
                    duration_ms, message, fields_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["ts"], row["process"], row["event"], row["level"],
                    row.get("trace_id"), row.get("symbol"), row.get("duration_ms"),
                    row.get("message") or row["event"], row.get("fields_json") or "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()


def list_ops_events(
    *,
    process: str | None = None,
    event: str | None = None,
    level: str | None = None,
    limit: int = 100,
) -> list[dict]:
    clauses = []
    params: list = []
    if process:
        clauses.append("process = ?")
        params.append(process)
    if event:
        clauses.append("event = ?")
        params.append(event)
    if level:
        clauses.append("level = ?")
        params.append(level)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM ops_events{where} ORDER BY ts DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
    out = []
    for row in rows:
        item = _row_dict(row)
        item["fields"] = _loads(item.pop("fields_json", None))
        out.append(item)
    return out


def prune_ops_events(before_iso: str) -> int:
    """Delete workflow events older than ``before_iso``. Status rows stay."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM ops_events WHERE ts < ?", (before_iso,))
            conn.commit()
            return int(cur.rowcount)
        finally:
            conn.close()


def upsert_ops_status(
    *,
    process: str,
    name: str,
    now_iso: str,
    ok: bool = True,
    duration_ms: float | None = None,
    detail: dict | None = None,
    pid: int | None = None,
    beat: bool = False,
) -> None:
    """Overwrite one process/workflow row. Heartbeats refresh ``beat_at`` only."""
    detail_json = json.dumps(detail or {}, default=str)[:2000]
    with _lock:
        conn = _connect()
        try:
            existing = conn.execute(
                "SELECT * FROM ops_status WHERE process = ? AND name = ?",
                (process, name),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO ops_status (
                        process, name, last_ok_at, last_error_at, ok_count, error_count,
                        last_duration_ms, detail_json, pid, beat_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        process, name,
                        now_iso if ok and not beat else (now_iso if ok else None),
                        None if ok else now_iso,
                        0 if beat else (1 if ok else 0),
                        0 if beat or ok else 1,
                        duration_ms,
                        detail_json,
                        pid,
                        now_iso if beat else None,
                    ),
                )
            elif beat:
                conn.execute(
                    """
                    UPDATE ops_status
                       SET beat_at = ?, pid = COALESCE(?, pid),
                           last_ok_at = ?, detail_json = ?
                     WHERE process = ? AND name = ?
                    """,
                    (now_iso, pid, now_iso, detail_json, process, name),
                )
            else:
                conn.execute(
                    """
                    UPDATE ops_status
                       SET last_ok_at = ?,
                           last_error_at = ?,
                           ok_count = ?,
                           error_count = ?,
                           last_duration_ms = COALESCE(?, last_duration_ms),
                           detail_json = ?,
                           pid = COALESCE(?, pid)
                     WHERE process = ? AND name = ?
                    """,
                    (
                        now_iso if ok else existing["last_ok_at"],
                        existing["last_error_at"] if ok else now_iso,
                        int(existing["ok_count"]) + (1 if ok else 0),
                        int(existing["error_count"]) + (0 if ok else 1),
                        duration_ms,
                        detail_json,
                        pid,
                        process, name,
                    ),
                )
            if not ok and name != "heartbeat":
                conn.execute(
                    """
                    UPDATE ops_status
                       SET last_error_at = ?
                     WHERE process = ? AND name = 'heartbeat'
                    """,
                    (now_iso, process),
                )
            conn.commit()
        finally:
            conn.close()


def list_ops_status(*, process: str | None = None, name: str | None = None) -> list[dict]:
    clauses = []
    params: list = []
    if process:
        clauses.append("process = ?")
        params.append(process)
    if name:
        clauses.append("name = ?")
        params.append(name)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM ops_status{where} ORDER BY process, name",
                params,
            ).fetchall()
        finally:
            conn.close()
    out = []
    for row in rows:
        item = _row_dict(row)
        item["detail"] = _loads(item.pop("detail_json", None))
        out.append(item)
    return out


def mark_orphaned_running(now_iso: str) -> int:
    """Any RUNNING row left from a previous process becomes INTERRUPTED."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                UPDATE admin_tasks
                   SET status = 'INTERRUPTED', ended_at = ?
                 WHERE status = 'RUNNING'
                """,
                (now_iso,),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
