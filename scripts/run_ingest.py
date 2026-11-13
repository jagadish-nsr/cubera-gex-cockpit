#!/usr/bin/env python3
"""Cubera ingest-only process — broker lanes, no public :8640 bind.

Owns: spot WS, chain live_fetcher ×3, rec.publisher, blast, pre/post, dhan.sync,
and ``data/journal/ingest.heartbeat``.

Serve with ``COCKPIT_INGEST_EXTERNAL=1`` reads snaps/SQLite and watches the
heartbeat. Do not run two ingest owners against the same journal (double-write).
"""

from __future__ import annotations

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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("cubera.ingest")

    from app.obs import boot, shutdown

    boot("ingest", loop=True)

    log.info("Arming broker ingest lanes (no public HTTP)…")
    from web_server import boot_broker_ingest_lanes

    boot_broker_ingest_lanes()

    from app.ingest_heartbeat import beat_forever

    log.info("Ingest armed — writing heartbeat; Ctrl-C to stop")
    try:
        beat_forever(role="ingest")
    except KeyboardInterrupt:
        log.info("Ingest shutdown requested")
        shutdown(ok=True, message="ingest stopped")


if __name__ == "__main__":
    main()
