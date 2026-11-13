"""Replace this process when the asyncio loop stops making progress.

A blocking lock on the event-loop thread stops uvicorn from accepting
connections. SIGTERM is not handled while that thread sits in
``pthread_cond_wait``, so a separate thread replaces the process image.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import traceback

STALL_S = 45.0
TICK_S = 1.0

_heartbeat = 0.0
_started = False
_armed_at = 0.0
_expected_beat = 0.0


def stalled(now: float, beat: float, armed_at: float, stall_s: float) -> bool:
    """True once the loop has gone ``stall_s`` without a tick.

    ``beat`` is 0 until the first tick. The clock then starts at ``armed_at``
    so a loop that never starts is treated the same as one that stops.
    """
    if stall_s <= 0:
        return False
    anchor = beat if beat > 0 else armed_at
    return (now - anchor) > stall_s


def is_armed() -> bool:
    return _started


def snapshot() -> dict:
    """Lag vs expected 1s beat — used by Admin Observability."""
    now = time.monotonic()
    beat = _heartbeat
    expected = _expected_beat if _expected_beat > 0 else (beat + TICK_S if beat > 0 else now)
    lag_ms = max(0.0, (now - expected) * 1000.0) if _started else 0.0
    return {
        "armed": _started,
        "lag_ms": round(lag_ms, 1),
        "stall_s": STALL_S,
        "last_beat_age_s": round(now - beat, 1) if beat > 0 else None,
    }


def main_thread_stack(limit: int = 40) -> str:
    """Innermost frames of the main thread.

    The watchdog thread reads this while the loop thread is blocked. The
    blocked call is the last frame.
    """
    ident = threading.main_thread().ident
    frame = sys._current_frames().get(ident) if ident is not None else None
    if frame is None:
        return "main thread stack unavailable"
    lines = traceback.format_stack(frame)
    return "".join(lines[-limit:]).rstrip()


def _default_restart() -> None:
    stack = main_thread_stack()
    try:
        from app.obs import event
        event(
            "watchdog.restart",
            level="ERROR",
            ok=False,
            workflow="watchdog",
            message="event loop stalled; restarting process",
            stack=stack,
        )
    except Exception:
        pass
    try:
        os.write(2, b"event loop stalled; restarting process\n")
        os.write(2, stack.encode("utf-8", errors="replace") + b"\n")
    except Exception:
        pass
    os.execv(sys.executable, [sys.executable, *sys.argv])


def start(loop: asyncio.AbstractEventLoop, *, stall_s: float = STALL_S, restart=None) -> None:
    """Arm the watchdog on ``loop``. Further calls in this process do nothing."""
    global _started, _armed_at, _expected_beat
    if _started:
        return
    _started = True
    restart_fn = restart or _default_restart
    _armed_at = time.monotonic()
    _expected_beat = _armed_at + TICK_S

    async def _beat() -> None:
        global _heartbeat, _expected_beat
        while True:
            now = time.monotonic()
            lag_ms = max(0.0, (now - _expected_beat) * 1000.0) if _expected_beat > 0 else 0.0
            _heartbeat = now
            _expected_beat = now + TICK_S
            try:
                from app.obs import metrics as obs_metrics
                obs_metrics.note_loop_lag(lag_ms)
            except Exception:
                pass
            await asyncio.sleep(TICK_S)

    loop.create_task(_beat())

    def _watch() -> None:
        while True:
            time.sleep(TICK_S)
            if stalled(time.monotonic(), _heartbeat, _armed_at, stall_s):
                restart_fn()
                return

    threading.Thread(target=_watch, name="event-loop-watchdog", daemon=True).start()
