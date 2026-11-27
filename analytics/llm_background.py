"""Run optional LLM prose polish off the request path.

26-Nov: three synchronous Ollama calls (90 s timeout each) made a cold /api/cockpit-data take
4–10 s. Callers now return the deterministic text at once and submit the polish here; the
polisher's own cache serves the rewrite on a later poll. One worker, one job per key in flight,
and a bounded backlog so a slow model cannot queue work without limit.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

log = logging.getLogger(__name__)

_MAX_PENDING = 8
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-polish")
_LOCK = threading.Lock()
_PENDING: set[str] = set()
_IDLE = threading.Condition(_LOCK)


def submit_once(key: str, job: Callable[[], object]) -> bool:
    """Queue ``job`` unless the same key is already pending. Returns True when queued."""
    with _LOCK:
        if key in _PENDING or len(_PENDING) >= _MAX_PENDING:
            return False
        _PENDING.add(key)

    def _run() -> None:
        try:
            job()
        except Exception as exc:  # noqa: BLE001 — polish is optional, never fatal
            log.debug("background polish %s failed: %s", key[:60], exc)
        finally:
            with _LOCK:
                _PENDING.discard(key)
                _IDLE.notify_all()

    _POOL.submit(_run)
    return True


def drain(timeout_s: float = 5.0) -> bool:
    """Wait until no polish is pending (tests). True when idle."""
    with _LOCK:
        return _IDLE.wait_for(lambda: not _PENDING, timeout=timeout_s)
