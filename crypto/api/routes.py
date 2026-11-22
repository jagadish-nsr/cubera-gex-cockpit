"""FastAPI routes for the CryptoCubera cockpit."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from crypto import __version__
from crypto.config import list_flags, load_config
from crypto.service import get_service
from crypto.store.db import init_db

router = APIRouter()


class FlagPatch(BaseModel):
    name: str = Field(..., examples=["ui.gex_map"])
    value: bool
    actor: str = "operator"


@router.get("/health")
async def health() -> dict[str, Any]:
    init_db()
    cfg = load_config()
    return {
        "ok": True,
        "service": "cryptocubera",
        "version": __version__,
        "mode": cfg.get("mode"),
        "underlying": cfg.get("underlying"),
    }


@router.get("/snapshot")
async def snapshot() -> dict[str, Any]:
    return await get_service().snapshot()


@router.get("/flags")
async def flags() -> dict[str, bool]:
    return list_flags()


@router.patch("/flags")
async def patch_flag(body: FlagPatch) -> dict[str, Any]:
    known = list_flags()
    if body.name not in known:
        # Allow new flags but warn
        pass
    return get_service().toggle_flag(body.name, body.value, actor=body.actor)


@router.get("/providers")
async def providers() -> dict[str, Any]:
    svc = get_service()
    return {
        "active": svc.registry.active_summary(),
        "catalog": svc.registry.list_providers(),
    }


@router.get("/feeds")
async def feeds_status() -> dict[str, Any]:
    from crypto.feeds.manager import get_feed_manager

    return get_feed_manager().status()


@router.get("/feeds/fields")
async def feed_field_findings() -> dict[str, Any]:
    """Documented aggressor-side and gamma-unit findings from Phase 0."""
    from crypto.providers.delta.parsers import FIELD_FINDINGS as DELTA_FIELDS
    from crypto.providers.deribit.parsers import FIELD_FINDINGS as DERIBIT_FIELDS

    return {"deribit": DERIBIT_FIELDS, "delta": DELTA_FIELDS}


@router.get("/gex")
async def gex_only() -> dict[str, Any]:
    snap = await get_service().snapshot()
    if snap.get("gex") is None:
        raise HTTPException(status_code=404, detail="ui.gex_map flag is off")
    return snap["gex"]


@router.get("/signals/latest")
async def latest_signal() -> dict[str, Any]:
    snap = await get_service().snapshot()
    if snap.get("signal") is None:
        raise HTTPException(status_code=404, detail="ui.go_cards flag is off")
    return snap["signal"]
