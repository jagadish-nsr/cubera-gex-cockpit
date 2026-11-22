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
def _startup() -> None:
    init_db()
    log.info("CryptoCubera %s ready — store initialized", __version__)


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
