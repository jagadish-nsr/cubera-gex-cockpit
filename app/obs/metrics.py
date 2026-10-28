"""In-process HTTP counters. They are not written on every poll."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_count = 0
_by_class = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0}
_slow = 0
_last_duration_ms = 0.0


def reset() -> None:
    global _count, _slow, _last_duration_ms
    with _lock:
        _count = 0
        _slow = 0
        _last_duration_ms = 0.0
        for key in _by_class:
            _by_class[key] = 0


def note(status: int, duration_ms: float) -> None:
    global _count, _slow, _last_duration_ms
    klass = f"{int(status) // 100}xx"
    if klass not in _by_class:
        klass = "other"
    with _lock:
        _count += 1
        _by_class[klass] += 1
        _last_duration_ms = float(duration_ms)
        if duration_ms > 1000:
            _slow += 1


def snapshot() -> dict:
    with _lock:
        return {
            "count": _count,
            "by_class": dict(_by_class),
            "slow": _slow,
            "last_duration_ms": round(_last_duration_ms, 1),
        }
