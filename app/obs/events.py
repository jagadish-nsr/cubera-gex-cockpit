"""Workflow events, heartbeats, and workflow gauges."""

from __future__ import annotations

import logging
import os
import threading
import time

from app.admin import db
from app.admin.clock import now_ist
from app.obs.configure import process_name
from app.obs.context import current, new_trace_id
from app.obs.redact import redact_mapping, redact_text

log = logging.getLogger("cubera.obs")

_hb_started = False
_hb_lock = threading.Lock()


def _stamp() -> str:
    return now_ist().isoformat(timespec="seconds")


def _persist() -> bool:
    """Keep pytest off the operator ledger unless the test pointed at a temp file."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not db.path_is_override():
        return False
    return True


def _clean_fields(fields: dict) -> dict:
    cleaned = {}
    for key, value in redact_mapping(fields).items():
        if value is None:
            continue
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        cleaned[key] = value
    return cleaned


def event(
    name: str,
    *,
    level: str = "INFO",
    message: str | None = None,
    symbol: str | None = None,
    duration_ms: float | None = None,
    error: str | None = None,
    workflow: str | None = None,
    ok: bool | None = None,
    trace_id: str | None = None,
    **fields,
) -> str | None:
    """Write one JSON log line and one ``ops_events`` row. Failures stay local."""
    try:
        return _event(
            name,
            level=level,
            message=message,
            symbol=symbol,
            duration_ms=duration_ms,
            error=error,
            workflow=workflow,
            ok=ok,
            trace_id=trace_id,
            **fields,
        )
    except Exception:
        log.warning("obs event %s could not be recorded", name, exc_info=True)
        return None


def _event(
    name: str,
    *,
    level: str,
    message: str | None,
    symbol: str | None,
    duration_ms: float | None,
    error: str | None,
    workflow: str | None,
    ok: bool | None,
    trace_id: str | None,
    **fields,
) -> str:
    ctx = current()
    trace = trace_id or ctx.get("trace_id") or new_trace_id()
    sym = symbol if symbol is not None else ctx.get("symbol")
    if error:
        fields["error"] = redact_text(str(error))[:500]
    if ok is None:
        ok = level.upper() not in ("WARNING", "ERROR", "CRITICAL") and not error
    if duration_ms is None and fields.get("duration_ms") is not None:
        try:
            duration_ms = float(fields["duration_ms"])
        except (TypeError, ValueError):
            duration_ms = None
    text = redact_text(message or name)
    if error and message is None:
        text = redact_text(f"{name}: {error}")[:500]
    else:
        text = text[:500]
    payload = _clean_fields(fields)
    payload.update({
        "event": name,
        "trace_id": trace,
        "symbol": sym,
        "duration_ms": duration_ms,
        "ok": bool(ok),
    })
    level_name = level.upper()
    level_no = getattr(logging, level_name, logging.INFO)
    log.log(level_no, text, extra={"obs": payload})
    if not _persist():
        return trace
    import json
    stamp = _stamp()
    db.insert_ops_event({
        "ts": stamp,
        "process": process_name(),
        "event": name,
        "level": level_name,
        "trace_id": trace,
        "symbol": sym,
        "duration_ms": duration_ms,
        "message": text,
        "fields_json": json.dumps(payload, default=str)[:4000],
    })
    gauge(
        workflow or name,
        ok=bool(ok),
        duration_ms=duration_ms,
        symbol=sym,
        detail={"event": name, "message": text[:300]},
    )
    return trace


def gauge(
    name: str,
    *,
    ok: bool = True,
    duration_ms: float | None = None,
    detail: dict | None = None,
    symbol: str | None = None,
) -> None:
    """Upsert the latest figure for one workflow. This does not append an event."""
    if not _persist():
        return
    try:
        body = dict(detail or {})
        if symbol:
            body["symbol"] = symbol
        db.upsert_ops_status(
            process=process_name(),
            name=name,
            now_iso=_stamp(),
            ok=ok,
            duration_ms=duration_ms,
            detail=body,
            pid=os.getpid(),
        )
    except Exception:
        log.warning("obs gauge %s could not be stored", name, exc_info=True)


def heartbeat(detail: dict | None = None) -> None:
    """Refresh this process row. A quiet loop still counts as alive."""
    if not _persist():
        return
    try:
        db.upsert_ops_status(
            process=process_name(),
            name="heartbeat",
            now_iso=_stamp(),
            ok=True,
            detail=detail or {},
            pid=os.getpid(),
            beat=True,
        )
    except Exception:
        log.warning("obs heartbeat could not be stored", exc_info=True)


def start_heartbeat(interval_s: float = 30.0) -> None:
    """Daemon thread. Further calls in this process do nothing."""
    global _hb_started
    with _hb_lock:
        if _hb_started:
            return
        _hb_started = True

    def _loop() -> None:
        while True:
            heartbeat()
            time.sleep(interval_s)

    threading.Thread(target=_loop, name="obs-heartbeat", daemon=True).start()


def boot(process: str, *, loop: bool = False) -> None:
    """Configure logging, beat once, and record that the process started."""
    from app.obs.configure import configure
    configure(process)
    if loop:
        start_heartbeat()
    heartbeat()
    event("process.started", workflow="process", message=process)


def shutdown(*, ok: bool = True, message: str | None = None, **fields) -> None:
    """Terminal status for a one-shot script."""
    event(
        "process.finished",
        workflow="process",
        ok=ok,
        level="INFO" if ok else "ERROR",
        message=message or ("finished" if ok else "failed"),
        **fields,
    )
    heartbeat()
