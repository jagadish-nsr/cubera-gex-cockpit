"""Minimal FastAPI chaos host on port 8660.

Does not import ``web_server``. Does not open live SQLite for write.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.chaos.probes import CHAOS_VERSION, build_chaos_checklist, build_chaos_readiness
from app.chaos.state import get_faults, reset_faults, set_faults

ROOT = Path(__file__).resolve().parents[2]

# Absolute paths the chaos host must never open for write.
_LIVE_DB_FORBIDDEN = (
    ROOT / "data" / "sentinel.db",
    ROOT / "data" / "journal" / "paper_trades.db",
    ROOT / "data" / "journal" / "recommendations.db",
    ROOT / "data" / "journal" / "admin_ops.db",
    ROOT / "data" / "journal" / "market_snapshot.db",
)


def assert_no_live_db_writes() -> list[str]:
    """Return open warnings; raise if a forbidden live DB was truncated/created by us.

    Chaos host never opens these files. This is a static path check used by drills.
    """
    return [str(p) for p in _LIVE_DB_FORBIDDEN]


app = FastAPI(
    title="Cubera Chaos Host",
    version=CHAOS_VERSION,
    docs_url="/docs",
)


class InjectBody(BaseModel):
    empty_chain: Optional[bool] = None
    cb_open: Optional[bool] = None
    stale_spot: Optional[bool] = None
    feed_silence: Optional[bool] = None
    missing_vwap: Optional[bool] = None
    force_market_open: Optional[bool] = Field(
        default=None,
        description="Treat session as open so checklist chain/feed gates apply",
    )


@app.on_event("startup")
def _boot() -> None:
    os.environ.setdefault("COCKPIT_ROLE", "chaos")
    os.environ.setdefault("COCKPIT_CHAOS", "1")
    reset_faults()


@app.get("/api/health")
def health():
    readiness = build_chaos_readiness()
    return {
        "status": "ok",
        "app": "Cubera Chaos Host",
        "version": CHAOS_VERSION,
        "env": "chaos",
        "port": int(os.environ.get("COCKPIT_CHAOS_PORT", "8660")),
        "ready": bool(readiness.get("ready")),
        "readiness": readiness,
        "forbidden_live_dbs": assert_no_live_db_writes(),
    }


@app.get("/api/admin/session-checklist")
def session_checklist():
    return build_chaos_checklist(version=CHAOS_VERSION)


@app.get("/api/chaos/faults")
def chaos_faults():
    return {"faults": get_faults()}


@app.post("/api/chaos/inject")
def chaos_inject(body: InjectBody):
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        raise HTTPException(status_code=400, detail="no faults provided")
    try:
        faults = set_faults(**payload)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "faults": faults, "checklist": build_chaos_checklist(version=CHAOS_VERSION)}


@app.post("/api/chaos/reset")
def chaos_reset():
    reset_faults()
    return {"ok": True, "faults": get_faults()}


@app.get("/")
def root():
    return JSONResponse({
        "app": "Cubera Chaos Host",
        "port": int(os.environ.get("COCKPIT_CHAOS_PORT", "8660")),
        "docs": "/docs",
        "health": "/api/health",
        "inject": "POST /api/chaos/inject",
        "note": "Never writes live journal DBs. See docs/chaos-drill.md",
    })
