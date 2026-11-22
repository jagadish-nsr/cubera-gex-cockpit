"""FastAPI routes for the CryptoCubera cockpit."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from crypto import __version__
from crypto.config import get_active_underlying, list_assets, list_flags, load_config
from crypto.service import get_service
from crypto.store.db import init_db

router = APIRouter()


class FlagPatch(BaseModel):
    name: str = Field(..., examples=["ui.gex_map"])
    value: bool
    actor: str = "operator"


class UnderlyingPatch(BaseModel):
    underlying: str = Field(..., examples=["ETH"])
    actor: str = "operator"


@router.get("/health")
async def health() -> dict[str, Any]:
    init_db()
    cfg = load_config()
    from crypto.store.db import store_status
    from crypto.store.session import current_session

    sess = current_session()
    store = store_status()
    return {
        "ok": True,
        "service": "cryptocubera",
        "version": __version__,
        "mode": cfg.get("mode"),
        "underlying": get_active_underlying(),
        "assets": list_assets(),
        "store": {
            "path": store.get("path"),
            "size_bytes": store.get("size_bytes"),
        },
        "session_id": (sess or {}).get("session_id"),
    }


@router.get("/snapshot")
async def snapshot() -> dict[str, Any]:
    """Cockpit payload. Hot path is an in-memory cache hit (sub-ms compute)."""
    return await get_service().snapshot()


@router.get("/cache/status")
async def cache_status() -> dict[str, Any]:
    """In-memory snapshot cache meta (CRY-21 latency diagnostics)."""
    svc = get_service()
    keys = svc.snapshot_cache.keys()
    return {
        "keys": keys,
        "prewarm_done": svc._prewarm_done,
        "entries": {k: svc.snapshot_cache.meta(k) for k in keys},
        "note": "timing.compute_ms on /snapshot and PATCH /underlying excludes HTTP RTT",
    }


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


@router.get("/assets")
async def assets() -> dict[str, Any]:
    """List configured underlyings and which asset flags are on."""
    return {
        "underlying": get_active_underlying(),
        "assets": list_assets(),
        "flags": {
            "assets.btc": list_flags().get("assets.btc"),
            "assets.eth": list_flags().get("assets.eth"),
            "assets.sol": list_flags().get("assets.sol"),
            "ui.asset_switcher": list_flags().get("ui.asset_switcher"),
        },
    }


@router.patch("/underlying")
async def patch_underlying(body: UnderlyingPatch) -> dict[str, Any]:
    """Switch active underlying (BTC default; ETH/SOL require assets.* flags).

    Hot path is in-memory: flips active asset and returns a cached snapshot
    (``timing.compute_ms`` target < 1ms when warm). Feed realign runs in the
    background. Response may include ``snapshot`` so the UI can render without
    a second round-trip.
    """
    try:
        return await get_service().switch_underlying(body.underlying, actor=body.actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/feeds")
async def feeds_status() -> dict[str, Any]:
    from crypto.feeds.manager import get_feed_manager

    return get_feed_manager().status()


@router.get("/store/status")
async def api_store_status() -> dict[str, Any]:
    """SQLite path, size, and last rotation (local recording hygiene)."""
    from crypto.store.db import store_status

    return store_status()


@router.get("/session")
async def api_session() -> dict[str, Any]:
    """Current paper/advisory recording session (started on server boot)."""
    from crypto.store.session import current_session, recording_enabled, start_session

    sess = current_session()
    if sess is None and recording_enabled():
        sess = start_session()
    return {"recording_enabled": recording_enabled(), "session": sess}


@router.get("/session/summary")
async def api_session_summary(session_id: str | None = None) -> dict[str, Any]:
    """GO / confirms / vetoes / PnL / tick counts for the active (or named) session."""
    from crypto.store.session import build_summary, current_session

    sid = session_id or ((current_session() or {}).get("session_id"))
    return build_summary(session_id=sid)


@router.get("/session/export")
async def api_session_export(
    format: str = "json",
    session_id: str | None = None,
) -> Any:
    """Export paper session summary as JSON or CSV."""
    from fastapi.responses import PlainTextResponse, Response

    from crypto.store.session import (
        current_session,
        export_summary_csv,
        export_summary_json,
    )

    sid = session_id or ((current_session() or {}).get("session_id"))
    fmt = (format or "json").lower()
    if fmt == "csv":
        return Response(
            content=export_summary_csv(session_id=sid),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=paper_session_summary.csv"
            },
        )
    if fmt == "json":
        return PlainTextResponse(
            content=export_summary_json(session_id=sid),
            media_type="application/json",
            headers={
                "Content-Disposition": "attachment; filename=paper_session_summary.json"
            },
        )
    raise HTTPException(status_code=400, detail="format must be json or csv")


@router.get("/session/list")
async def api_session_list(limit: int = 50) -> dict[str, Any]:
    from crypto.store.session import list_sessions

    return {"sessions": list_sessions(limit=min(limit, 200))}


class SessionEndBody(BaseModel):
    session_id: str | None = None
    write_summary: bool = True


@router.post("/session/end")
async def api_session_end(body: SessionEndBody | None = Body(default=None)) -> dict[str, Any]:
    """Close the active recording session and snapshot the summary into SQLite."""
    from crypto.store.session import end_session

    body = body or SessionEndBody()
    result = end_session(session_id=body.session_id, write_summary=body.write_summary)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result


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
    """Intelligence feed + blackout + further-reading (flag-gated)."""
    from crypto.config import get_flag

    if not get_flag("intel.news", default=True):
        raise HTTPException(status_code=404, detail="intel.news flag is off")
    snap = await get_service().snapshot()
    intel = snap.get("intel") or {}
    return {
        "news": snap.get("news") or [],
        "intel": intel,
        "further_reading": intel.get("further_reading") or [],
    }


@router.get("/flow/alerts")
async def flow_alerts() -> dict[str, Any]:
    """CryptoCubera orderflow tape alerts for the active underlying (BTC/ETH/SOL).

    Decision-support only — never places orders. Not the equity Sentinel tape.
    """
    from crypto.config import get_flag

    if not get_flag("ui.flow_alerts", default=True):
        raise HTTPException(status_code=404, detail="ui.flow_alerts flag is off")
    snap = await get_service().snapshot()
    return {
        "underlying": snap.get("underlying"),
        "desk": "CryptoCubera",
        "scope": "crypto",
        "alerts": snap.get("flow_alerts") or [],
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
    notes: str = ""


class KillBody(BaseModel):
    confirmed: bool = False
    actor: str = "operator"


@router.get("/exec/status")
async def exec_status() -> dict[str, Any]:
    from crypto.exec.service import get_execution_service

    return await get_execution_service().status()


@router.get("/exec/decision")
async def exec_decision() -> dict[str, Any]:
    """Export the latest GO card as a Manual Decision (levels + JSON).

    Decision support only — never places an exchange order.
    Includes calendar/news blackout status when the intel layer is active.
    """
    from crypto.exec.service import get_execution_service

    snap = await get_service().snapshot()
    card = snap.get("signal")
    if not card:
        raise HTTPException(status_code=404, detail="No GO card available")
    out = get_execution_service().decision_from_card(card)
    blackout = (snap.get("intel") or {}).get("blackout") or {}
    out["blackout"] = {
        "active": bool(blackout.get("active")),
        "reasons": list(blackout.get("reasons") or [])[:3],
        "blackout_until": blackout.get("blackout_until"),
        "note": blackout.get("note")
        or "Calendar/news blackout forces NO-GO; never places trades.",
    }
    return out


@router.post("/exec/confirm")
async def exec_confirm(body: ConfirmBody | None = Body(default=None)) -> dict[str, Any]:
    """Confirm a GO card: paper/sim practice, or Manual Decision log.

    ``exec.live`` + ``exec.confirm`` → Manual Decision (copy/export levels;
    ledger note). Confirm **never** POSTs to Delta. Paper Confirm may sim-fill.
    Auto is never available. ``exec.live_place`` cannot unlock exchange place.
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

    result = await get_execution_service().confirm_from_card(
        card, actor=body.actor, notes=body.notes or ""
    )
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
    """Export paper / Manual Decision ledger as JSON or CSV (includes notes)."""
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
    """Inspect legacy live-place safety gate (Confirm does not use this to trade).

    Manual Decision does not require the gate. Exchange POST remains refused
    from Confirm regardless of gate status.
    """
    from crypto.exec.live_gate import evaluate_live_gate

    out = evaluate_live_gate(require_flags=True).as_dict()
    out["confirm_places_orders"] = False
    out["note"] = (
        "Manual Decision path does not place live orders. "
        + str(out.get("note") or "")
    ).strip()
    return out


@router.post("/exec/kill")
async def exec_kill(body: KillBody | None = Body(default=None)) -> dict[str, Any]:
    """Two-tap kill switch: first arms, second with confirmed=true cancels open intents."""
    from crypto.exec.service import get_execution_service

    body = body or KillBody()
    return await get_execution_service().kill(confirmed=body.confirmed, actor=body.actor)
