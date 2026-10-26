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

STALL_S = 45.0
TICK_S = 1.0

_heartbeat = 0.0
_started = False


def stalled(now: float, beat: float, armed_at: float, stall_s: float) -> bool:
    """True once the loop has gone ``stall_s`` without a tick.

    ``beat`` is 0 until the first tick. The clock then starts at ``armed_at``
    so a loop that never starts is treated the same as one that stops.
    """
    if stall_s <= 0:
        return False
    anchor = beat if beat > 0 else armed_at
    return (now - anchor) > stall_s


def _default_restart() -> None:
    try:
        os.write(2, b"event loop stalled; restarting process\n")
    except Exception:
        pass
    os.execv(sys.executable, [sys.executable, *sys.argv])


def start(loop: asyncio.AbstractEventLoop, *, stall_s: float = STALL_S, restart=None) -> None:
    """Arm the watchdog on ``loop``. Further calls in this process do nothing."""
    global _started
    if _started:
        return
    _started = True
    restart_fn = restart or _default_restart
    armed_at = time.monotonic()

    async def _beat() -> None:
        global _heartbeat
        while True:
            _heartbeat = time.monotonic()
            await asyncio.sleep(TICK_S)

    loop.create_task(_beat())

    def _watch() -> None:
        while True:
            time.sleep(TICK_S)
            if stalled(time.monotonic(), _heartbeat, armed_at, stall_s):
                restart_fn()
                return

    threading.Thread(target=_watch, name="event-loop-watchdog", daemon=True).start()
