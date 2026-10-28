"""``/api/admin/*`` routes. Registered onto the existing FastAPI app."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from typing import Optional

from app.admin import db
from app.admin.audit_engine import audit_range
from app.admin.clock import now_ist
from app.admin.health_daemon import evaluate_alerts, gap_count
from app.admin.task_runner import AdminRequestError, cancel, handle, task_view
from app.obs.metrics import snapshot as http_snapshot

ROOT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    return ROOT


def _env_name() -> str:
    explicit = os.environ.get("COCKPIT_ENV")
    if explicit:
        return explicit
    return "prod" if _port() == 8640 else "dev"


def _port() -> int:
    raw = os.environ.get("COCKPIT_PORT", "8640")
    try:
        return int(raw)
    except ValueError:
        return 8640


def _wal_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return round(path.stat().st_size / (1024 * 1024), 2)


def _token_public() -> dict:
    from data.dhan import token_status
    raw = token_status()
    return {
        "present": bool(raw.get("present")),
        "hours_left": raw.get("hours_left"),
        "source": raw.get("source"),
        "expired": raw.get("expired"),
    }


def _feed_public() -> dict:
    from app.ws_spot_feed import get_status
    raw = get_status()
    return {
        "ws_connected": bool(raw.get("ws_connected")),
        "last_tick_age_s": raw.get("last_tick_age_s"),
        "is_live": bool(raw.get("is_live")),
    }


def register_admin_routes(app: FastAPI) -> None:
    @app.get("/api/admin/status")
    def admin_status():
        root = data_root()
        return {
            "environment": _env_name(),
            "port": _port(),
            "token_status": _token_public(),
            "feed_status": _feed_public(),
            "wal_sizes_mb": {
                "sentinel": _wal_mb(root / "data" / "sentinel.db-wal"),
                "market_snapshot": _wal_mb(root / "data" / "journal" / "market_snapshot.db-wal"),
                "admin_ops": _wal_mb(db.db_path().with_name(db.db_path().name + "-wal")),
            },
            "active_gaps_count": gap_count(),
            "active_alerts_count": db.badge_count(),
        }

    @app.get("/api/admin/ops")
    def admin_ops(
        process: Optional[str] = Query(None),
        event: Optional[str] = Query(None),
        level: Optional[str] = Query(None),
        limit: int = Query(80, ge=1, le=500),
    ):
        """Heartbeats, workflow gauges, and the latest workflow events."""
        rows = db.list_ops_status(process=process)
        return {
            "heartbeats": [row for row in rows if row["name"] == "heartbeat"],
            "workflows": [row for row in rows if row["name"] != "heartbeat"],
            "events": db.list_ops_events(process=process, event=event, level=level, limit=limit),
            "http": http_snapshot(),
        }

    @app.get("/api/admin/audit")
    def admin_audit(
        start_date: str = Query(...),
        end_date: str = Query(...),
        symbol: str = Query("NIFTY"),
    ):
        try:
            return audit_range(data_root(), symbol, start_date, end_date, now=now_ist())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/admin/alerts")
    def admin_alerts(
        unread_only: bool = Query(False),
        severity: Optional[str] = Query(None),
    ):
        rows = db.list_alerts(unread_only=unread_only, severity=severity)
        return {"badge_count": db.badge_count(), "alerts": rows}

    @app.post("/api/admin/alerts/ack")
    def admin_ack(payload: dict):
        alert_id = (payload or {}).get("alert_id")
        action = (payload or {}).get("action")
        if not alert_id or action not in ("read", "dismiss"):
            raise HTTPException(status_code=400, detail="alert_id and action read|dismiss are required")
        found = db.ack_alert(str(alert_id), action, now_ist().isoformat(timespec="seconds"))
        if not found:
            raise HTTPException(status_code=404, detail="unknown alert")
        return {"status": "ok", "badge_count": db.badge_count()}

    @app.post("/api/admin/alerts/refresh")
    def admin_refresh_alerts():
        """Run one alert pass. The background loop also calls this every minute."""
        try:
            return evaluate_alerts(data_root(), now=now_ist())
        except Exception as exc:
            log_detail = str(exc)
            return JSONResponse(status_code=500, content={"detail": log_detail})

    @app.post("/api/admin/tasks/run")
    def admin_run_task(payload: dict):
        try:
            return handle(payload or {}, now=now_ist())
        except AdminRequestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @app.get("/api/admin/tasks/{task_id}")
    def admin_task(task_id: str):
        try:
            return task_view(task_id)
        except AdminRequestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @app.post("/api/admin/tasks/{task_id}/cancel")
    def admin_cancel(task_id: str):
        try:
            return cancel(task_id, now=now_ist())
        except AdminRequestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
