#!/usr/bin/env python3
"""Cubera ingest-only process — broker lanes, no public :8640 bind.

Owns: spot WS, chain live_fetcher ×3, rec.publisher, blast, pre/post, dhan.sync,
and ``data/journal/ingest.heartbeat``.

Serve with ``COCKPIT_INGEST_EXTERNAL=1`` reads snaps/SQLite and watches the
heartbeat. Do not run two ingest owners against the same journal (double-write).
"""

from __future__ import annotations

import fcntl
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("COCKPIT_ROLE", "ingest")
# Ingest must never set EXTERNAL=1 — that would skip its own lanes.
os.environ.pop("COCKPIT_INGEST_EXTERNAL", None)
os.environ.setdefault("ENV_MODE", "production")
os.environ.setdefault("COCKPIT_ENV", "prod")

_SINGLETON_FH = None


def acquire_ingest_singleton(lock_path: Path | None = None):
    """Acquire an exclusive OS flock so two ingest writers can never run on one checkout."""
    lp = Path(lock_path) if lock_path is not None else (ROOT / "logs" / "ingest.lock")
    lp.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lp, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        holder = fh.read().strip() or "unknown"
        fh.close()
        raise RuntimeError(f"Another ingest process already holds {lp} (PID {holder})")
    fh.seek(0)
    fh.truncate()
    fh.write(f"{os.getpid()}\n")
    fh.flush()
    return fh


def main() -> None:
    global _SINGLETON_FH
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("cubera.ingest")

    try:
        _SINGLETON_FH = acquire_ingest_singleton()
    except RuntimeError as exc:
        log.error("%s — refusing dual ingest startup", exc)
        sys.exit(2)

    from app.obs import boot, shutdown

    boot("ingest", loop=True)

    log.info("Arming broker ingest lanes (no public HTTP)…")
    from web_server import boot_broker_ingest_lanes

    boot_broker_ingest_lanes()

    # Pillar A5: ingest owns sentinel.db WAL PASSIVE/TRUNCATE (serve is read-only).
    from app.admin.health_daemon import start_wal_maintenance_thread

    start_wal_maintenance_thread()

    # boot_broker_ingest_lanes already started the heartbeat writer thread.
    # Hold the process with a second writer only if that thread died.
    from app.ingest_heartbeat import beat_forever, start_writer

    log.info("Ingest armed — writing heartbeat; Ctrl-C to stop")
    start_writer(role="ingest")  # idempotent if already alive
    try:
        beat_forever(role="ingest")
    except KeyboardInterrupt:
        log.info("Ingest shutdown requested")
        shutdown(ok=True, message="ingest stopped")


if __name__ == "__main__":
    main()
