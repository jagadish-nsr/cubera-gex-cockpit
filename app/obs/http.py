"""HTTP timing. Polls update counters. Errors and slow calls become events."""

from __future__ import annotations

import time

from app.obs.context import bind, new_trace_id
from app.obs.events import event
from app.obs.metrics import note

_SLOW_MS = 1000.0


def _static(path: str) -> bool:
    return path.startswith("/static/") or path == "/favicon.ico"


def observe_request(method: str, path: str, status: int, duration_ms: float, trace_id: str) -> None:
    """Count every non-static call. Journal only failures and calls over 1 second."""
    if _static(path):
        return
    note(status, duration_ms)
    common = {
        "method": method,
        "route": path,
        "status": int(status),
        "duration_ms": round(float(duration_ms), 1),
        "trace_id": trace_id,
        "workflow": "http",
    }
    if status >= 400:
        level = "ERROR" if status >= 500 else "WARNING"
        event("http.error", level=level, ok=False, **common)
        return
    if duration_ms > _SLOW_MS:
        event("http.slow", level="WARNING", ok=True, **common)


def install(app) -> None:
    """Middleware: ``X-Request-Id`` plus :func:`observe_request`."""

    @app.middleware("http")
    async def _obs_http(request, call_next):
        path = request.url.path
        if _static(path):
            return await call_next(request)
        trace = new_trace_id()
        started = time.perf_counter()
        try:
            with bind(trace_id=trace):
                response = await call_next(request)
        except Exception:
            observe_request(request.method, path, 500, (time.perf_counter() - started) * 1000.0, trace)
            raise
        observe_request(
            request.method, path, response.status_code,
            (time.perf_counter() - started) * 1000.0, trace,
        )
        response.headers["X-Request-Id"] = trace
        return response
