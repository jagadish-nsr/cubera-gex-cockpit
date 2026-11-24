"""HTTP timing. Polls update counters. Errors and slow calls become events.

``_obs_http`` middleware enqueues slow/error journal writes onto a bounded
background worker so SQLite WAL contention on ``admin_ops.db`` never stalls
the uvicorn asyncio event loop.
"""

from __future__ import annotations

from collections import deque
import threading
import time

from app.obs.context import bind, new_trace_id
from app.obs.events import event
from app.obs.metrics import begin_request, end_request, note

_SLOW_MS = 1000.0
_QUEUE_MAX = 2048
_q_cond = threading.Condition()
_queue: deque[tuple[str, str, str, int, float, str]] = deque()
_inflight_writes = 0
_worker_started = False


def _static(path: str) -> bool:
    return path.startswith("/static/") or path == "/favicon.ico"


def _journal_http_event(
    name: str, level: str, ok: bool, method: str, route: str,
    status: int, duration_ms: float, trace_id: str,
) -> None:
    event(
        name,
        level=level,
        ok=ok,
        method=method,
        route=route,
        status=int(status),
        duration_ms=round(float(duration_ms), 1),
        trace_id=trace_id,
        workflow="http",
    )


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _q_cond:
        if _worker_started:
            return
        _worker_started = True
        threading.Thread(
            target=_drain_loop, name="obs-http-journal", daemon=True
        ).start()


def _drain_loop() -> None:
    global _inflight_writes
    while True:
        with _q_cond:
            while not _queue:
                _q_cond.wait()
            item = _queue.popleft()
            _inflight_writes += 1
        try:
            _journal_http_event(*item)
        except Exception:
            pass
        finally:
            with _q_cond:
                _inflight_writes -= 1
                if not _queue and _inflight_writes == 0:
                    _q_cond.notify_all()


def flush_http_obs(timeout: float = 1.5) -> None:
    """Block until queued async HTTP obs events are persisted (used by readers/tests)."""
    if not _queue and _inflight_writes == 0:
        return
    deadline = time.monotonic() + max(0.05, float(timeout))
    with _q_cond:
        while (_queue or _inflight_writes > 0):
            rem = deadline - time.monotonic()
            if rem <= 0:
                break
            _q_cond.wait(timeout=rem)


def observe_request(
    method: str, path: str, status: int, duration_ms: float, trace_id: str,
    *, async_journal: bool = False,
) -> None:
    """Count every non-static call. Journal only failures and calls over 1 second."""
    if _static(path):
        return
    route = path.split("?", 1)[0]
    note(status, duration_ms, route=route)
    if status >= 400:
        name = "http.error"
        level = "ERROR" if status >= 500 else "WARNING"
        ok = False
    elif duration_ms > _SLOW_MS:
        name = "http.slow"
        level = "WARNING"
        ok = True
    else:
        return

    if not async_journal:
        _journal_http_event(name, level, ok, method, route, status, duration_ms, trace_id)
        return

    _ensure_worker()
    item = (name, level, ok, method, route, int(status), float(duration_ms), trace_id)
    with _q_cond:
        if len(_queue) >= _QUEUE_MAX:
            _queue.popleft()
        _queue.append(item)
        _q_cond.notify()


def install(app) -> None:
    """Middleware: ``X-Request-Id`` plus non-blocking :func:`observe_request`."""

    @app.middleware("http")
    async def _obs_http(request, call_next):
        path = request.url.path
        if _static(path):
            return await call_next(request)
        trace = new_trace_id()
        begin_request()
        started = time.perf_counter()
        try:
            with bind(trace_id=trace):
                response = await call_next(request)
        except Exception:
            observe_request(
                request.method, path, 500,
                (time.perf_counter() - started) * 1000.0, trace,
                async_journal=True,
            )
            raise
        finally:
            end_request()
        observe_request(
            request.method, path, response.status_code,
            (time.perf_counter() - started) * 1000.0, trace,
            async_journal=True,
        )
        response.headers["X-Request-Id"] = trace
        return response
