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


@router.get("/engine/params")
async def engine_params() -> dict[str, Any]:
    """Expose configurable setup / veto parameters (starting values)."""
    cfg = load_config()
    eng = dict(cfg.get("engine") or {})
    return {
        "engine": eng,
        "flags": {
            "engine.gex_flow_model": list_flags().get("engine.gex_flow_model"),
            "engine.signal_rules": list_flags().get("engine.signal_rules"),
            "inference.feature_export": list_flags().get("inference.feature_export"),
            "inference.regime_classifier": list_flags().get("inference.regime_classifier"),
        },
        "stale_threshold_s": (cfg.get("feeds") or {}).get("stale_threshold_s"),
    }


@router.get("/inference/latest")
async def inference_latest() -> dict[str, Any]:
    snap = await get_service().snapshot()
    if snap.get("inference") is None:
        raise HTTPException(
            status_code=404,
            detail="inference flags off (inference.feature_export / inference.regime_classifier)",
        )
    return snap["inference"]


@router.get("/intel/feed")
async def intel_feed() -> dict[str, Any]:
    """Intelligence feed + blackout summary (flag-gated)."""
    from crypto.config import get_flag

    if not get_flag("intel.news", default=True):
        raise HTTPException(status_code=404, detail="intel.news flag is off")
    snap = await get_service().snapshot()
    return {
        "news": snap.get("news") or [],
        "intel": snap.get("intel") or {},
    }


@router.get("/intel/blackout")
async def intel_blackout() -> dict[str, Any]:
    """Current news/calendar blackout state for the veto path."""
    from crypto.config import get_flag

    if not get_flag("intel.news", default=True):
        return {"active": False, "note": "intel.news flag off"}
    snap = await get_service().snapshot()
    return (snap.get("intel") or {}).get("blackout") or {"active": False}


class DigestBody(BaseModel):
    """Desk digest request. Language only — never creates a GO."""

    kind: str = Field("morning", examples=["morning", "post_trade", "veto"])
    force_mock: bool = False


@router.post("/intel/digest")
async def intel_digest(body: DigestBody | None = Body(default=None)) -> dict[str, Any]:
    """Morning / post-trade / veto digest (Gemma when ``intel.gemma`` on)."""
    from crypto.config import get_flag
    from crypto.gemma.digests import build_digest

    body = body or DigestBody()
    snap = await get_service().snapshot()
    card = snap.get("signal") or {}
    events = snap.get("news") or []
    vetoes = list(card.get("veto_reasons") or [])
    digest = build_digest(
        body.kind,
        events=events,
        card=card,
        vetoes=vetoes,
        force_mock=body.force_mock,
    )
    digest["gemma_flag"] = get_flag("intel.gemma", default=False)
    return digest


@router.get("/intel/gemma")
async def intel_gemma_status() -> dict[str, Any]:
    """Gemma language-layer status (flag, config, no secrets)."""
    from crypto.gemma.client import gemma_available, gemma_config, gemma_enabled

    cfg = gemma_config()
    return {
        "enabled": gemma_enabled(),
        "available": gemma_available(probe=False),
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "timeout_s": cfg["timeout_s"],
        "mock_when_unavailable": cfg["mock_when_unavailable"],
        "note": "Language only — schema-validated; timeout→keywords; never creates GO.",
    }


class ConfirmBody(BaseModel):
    """Optional card override; default uses latest snapshot GO card."""

    card: dict[str, Any] | None = None
    actor: str = "operator"


class KillBody(BaseModel):
    confirmed: bool = False
    actor: str = "operator"


@router.get("/exec/status")
async def exec_status() -> dict[str, Any]:
    from crypto.exec.service import get_execution_service

    return await get_execution_service().status()


@router.post("/exec/confirm")
async def exec_confirm(body: ConfirmBody | None = Body(default=None)) -> dict[str, Any]:
    """One-tap Confirm: paper/sim or Live Confirm bracket from a GO card.

    Live path requires ``exec.live`` + ``exec.confirm`` and the fail-closed
    live gate (keys + ack + IP whitelist ack). Auto is never available.
    """
    from crypto.config import get_flag
    from crypto.exec.service import get_execution_service

    body = body or ConfirmBody()
    paper = get_flag("exec.paper", default=False)
    live = get_flag("exec.live", default=False)
    if not paper and not live:
        raise HTTPException(
            status_code=403,
            detail="exec.paper and exec.live are off — Advisory only",
        )
    if not get_flag("exec.confirm", default=False):
        raise HTTPException(
            status_code=403,
            detail="exec.confirm is off — Advisory default; enable Confirm to submit",
        )
    if get_flag("exec.auto", default=False):
        raise HTTPException(
            status_code=403,
            detail="exec.auto is refused — Auto mode is not supported",
        )

    card = body.card
    if card is None:
        snap = await get_service().snapshot()
        card = snap.get("signal")
    if not card:
        raise HTTPException(status_code=404, detail="No GO card available")

    result = await get_execution_service().confirm_from_card(card, actor=body.actor)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return result


@router.get("/exec/ledger")
async def exec_ledger(limit: int = 100, status: str | None = None) -> dict[str, Any]:
    from crypto.exec.service import get_execution_service

    ledger = get_execution_service().ledger
    return {"trades": ledger.list_trades(status=status, limit=min(limit, 500))}


@router.get("/exec/ledger/export")
async def exec_ledger_export(format: str = "json") -> Any:
    """Export paper ledger as JSON or CSV (tax/reporting hygiene)."""
    from fastapi.responses import PlainTextResponse, Response

    from crypto.exec.service import get_execution_service

    ledger = get_execution_service().ledger
    fmt = (format or "json").lower()
    if fmt == "csv":
        return Response(
            content=ledger.export_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=paper_trades.csv"},
        )
    if fmt == "json":
        return PlainTextResponse(
            content=ledger.export_json(),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=paper_trades.json"},
        )
    raise HTTPException(status_code=400, detail="format must be json or csv")


@router.get("/exec/live-gate")
async def exec_live_gate() -> dict[str, Any]:
    """Inspect Live Confirm safety gate (fail-closed checks)."""
    from crypto.exec.live_gate import evaluate_live_gate

    return evaluate_live_gate(require_flags=True).as_dict()


@router.post("/exec/kill")
async def exec_kill(body: KillBody | None = Body(default=None)) -> dict[str, Any]:
    """Two-tap kill switch: first arms, second with confirmed=true cancels open intents."""
    from crypto.exec.service import get_execution_service

    body = body or KillBody()
    return await get_execution_service().kill(confirmed=body.confirmed, actor=body.actor)
