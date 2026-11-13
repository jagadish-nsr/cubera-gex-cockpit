"""In-memory fault flags for the chaos host. Process-local only."""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_faults: dict[str, Any] = {
    "empty_chain": False,
    "cb_open": False,
    "stale_spot": False,
    "feed_silence": False,
    "missing_vwap": False,
    "force_market_open": False,
}


def reset_faults() -> None:
    with _lock:
        for key in list(_faults):
            if key == "force_market_open":
                _faults[key] = False
            else:
                _faults[key] = False


def set_faults(**kwargs: Any) -> dict[str, Any]:
    with _lock:
        for key, value in kwargs.items():
            if key not in _faults:
                raise KeyError(f"unknown chaos fault: {key}")
            _faults[key] = bool(value) if key != "force_market_open" else bool(value)
        return dict(_faults)


def get_faults() -> dict[str, Any]:
    with _lock:
        return dict(_faults)
