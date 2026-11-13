"""Cross-process ingest heartbeat for serve readiness.

Ingest (or a combined single process that owns broker lanes) writes
``data/journal/ingest.heartbeat`` every few seconds. Serve with
``COCKPIT_INGEST_EXTERNAL=1`` refuses readiness when that file is older than
``STALE_AFTER_S`` during the F&O session.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "data" / "journal" / "ingest.heartbeat"
STALE_AFTER_S = 90.0
WRITE_INTERVAL_S = 5.0

_writer_thread: threading.Thread | None = None
_stop = threading.Event()
_path_override: Path | None = None


def heartbeat_path() -> Path:
    raw = os.environ.get("COCKPIT_INGEST_HEARTBEAT")
    if raw:
        return Path(raw)
    if _path_override is not None:
        return _path_override
    return DEFAULT_PATH


def set_path_for_tests(path: Path | None) -> None:
    global _path_override
    _path_override = path


def _local_feed_detail() -> dict[str, Any]:
    """WS feed fields for EXTERNAL serve to read via heartbeat (no local WS there)."""
    try:
        from app.ws_spot_feed import get_status
        st = get_status() or {}
        return {
            "ws_connected": bool(st.get("ws_connected")),
            "is_live": bool(st.get("is_live")),
            "last_tick_age_s": st.get("last_tick_age_s"),
            "ws_error": st.get("ws_error"),
        }
    except Exception as exc:
        return {
            "ws_connected": False,
            "is_live": False,
            "last_tick_age_s": None,
            "ws_error": str(exc),
        }


def write_beat(*, role: str | None = None, detail: dict[str, Any] | None = None) -> Path:
    """Atomically write a heartbeat JSON file. Returns the path written.

    Always merges local WS feed status into ``detail`` so EXTERNAL serve readiness
    can judge feed health without owning the Dhan socket.
    """
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**_local_feed_detail(), **(detail or {})}
    payload = {
        "ts": time.time(),
        "pid": os.getpid(),
        "role": role or os.environ.get("COCKPIT_ROLE", "combined"),
        "detail": merged,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_beat() -> dict[str, Any] | None:
    path = heartbeat_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "ts" not in data:
            return None
        return data
    except Exception as exc:
        log.warning("ingest heartbeat unreadable: %s", exc)
        return None


def probe(*, stale_after_s: float = STALE_AFTER_S) -> dict[str, Any]:
    """Readiness probe for the ingest heartbeat."""
    beat = read_beat()
    if beat is None:
        return {
            "ok": False,
            "age_s": None,
            "pid": None,
            "role": None,
            "reason": "ingest_heartbeat_missing",
        }
    try:
        age = max(0.0, time.time() - float(beat["ts"]))
    except (TypeError, ValueError):
        return {
            "ok": False,
            "age_s": None,
            "pid": beat.get("pid"),
            "role": beat.get("role"),
            "reason": "ingest_heartbeat_bad_ts",
        }
    ok = age <= stale_after_s
    return {
        "ok": ok,
        "age_s": round(age, 1),
        "pid": beat.get("pid"),
        "role": beat.get("role"),
        "reason": "" if ok else "ingest_heartbeat_stale",
    }


def start_writer(*, role: str | None = None, interval_s: float = WRITE_INTERVAL_S) -> None:
    """Daemon thread: write beats forever. Idempotent."""
    global _writer_thread
    if _writer_thread is not None and _writer_thread.is_alive():
        return
    _stop.clear()
    resolved_role = role or os.environ.get("COCKPIT_ROLE", "combined")

    def _loop() -> None:
        while not _stop.is_set():
            try:
                write_beat(role=resolved_role)
            except Exception:
                log.warning("ingest heartbeat write failed", exc_info=True)
            _stop.wait(interval_s)

    _writer_thread = threading.Thread(target=_loop, name="ingest-heartbeat", daemon=True)
    _writer_thread.start()
    try:
        write_beat(role=resolved_role)
    except Exception:
        log.warning("ingest heartbeat initial write failed", exc_info=True)


def stop_writer() -> None:
    _stop.set()


def beat_forever(*, role: str | None = None, interval_s: float = WRITE_INTERVAL_S) -> None:
    """Blocking main for the ingest-only process (after lanes are armed)."""
    resolved_role = role or os.environ.get("COCKPIT_ROLE", "ingest")
    log.info("ingest process holding (role=%s, heartbeat every %.1fs)", resolved_role, interval_s)
    while True:
        try:
            write_beat(role=resolved_role)
        except Exception:
            log.warning("ingest heartbeat write failed", exc_info=True)
        time.sleep(interval_s)
