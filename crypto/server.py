#!/usr/bin/env python3
"""CryptoCubera foundation server — modular cockpit API + static UI.

Default: http://localhost:8760
Does not replace the NSE cockpit (web_server.py on 8640/8650).
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from crypto import __version__
from crypto.api.routes import router
from crypto.store.db import init_db

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_DIR = WEB_DIR / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("cryptocubera")

app = FastAPI(
    title="CryptoCubera",
    version=__version__,
    description="Modular crypto GEX decision-support cockpit (advisory).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.on_event("startup")
async def _startup() -> None:
    init_db()
    try:
        from crypto.store.db import store_status
        from crypto.store.session import recording_enabled, start_session

        st = store_status()
        log.info(
            "Store ready path=%s size_bytes=%s rotate_max=%s",
            st.get("path"),
            st.get("size_bytes"),
            st.get("rotate_max_bytes"),
        )
        if recording_enabled():
            sess = start_session()
            log.info(
                "Recording session %s mode=%s (exec.live stays off)",
                sess.get("session_id"),
                sess.get("mode"),
            )
    except Exception as exc:  # noqa: BLE001 — never block UI boot
        log.warning("Session recording failed to start: %s", exc)
    try:
        from crypto.feeds.manager import get_feed_manager

        summary = await get_feed_manager().start()
        log.info("CryptoCubera %s ready — feeds=%s", __version__, summary)
    except Exception as exc:  # noqa: BLE001 — never block UI boot
        log.warning("Live feed manager failed to start: %s", exc)
        log.info("CryptoCubera %s ready — store initialized (feeds idle)", __version__)
    try:
        from crypto.service import get_service

        warmed = await get_service().prewarm_snapshots()
        log.info("Snapshot cache prewarmed: %s", warmed)
    except Exception as exc:  # noqa: BLE001 — never block UI boot
        log.warning("Snapshot cache prewarm failed: %s", exc)


@app.on_event("shutdown")
async def _shutdown() -> None:
    try:
        from crypto.feeds.manager import get_feed_manager

        await get_feed_manager().stop()
    except Exception as exc:  # noqa: BLE001
        log.debug("feed shutdown: %s", exc)
    try:
        from crypto.store.session import current_session, end_session, recording_enabled

        if recording_enabled() and current_session() is not None:
            result = end_session(write_summary=True)
            summary = result.get("summary") or {}
            log.info(
                "Session closed id=%s go=%s confirms=%s vetoes=%s pnl=%s",
                summary.get("session_id"),
                summary.get("go_count"),
                summary.get("confirms"),
                summary.get("veto_signal_count"),
                summary.get("realized_pnl_usd"),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("Session end failed: %s", exc)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:
    parser = argparse.ArgumentParser(description="CryptoCubera cockpit server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8760)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "crypto.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
