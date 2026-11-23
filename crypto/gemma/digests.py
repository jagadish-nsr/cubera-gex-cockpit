"""Morning / post-trade / veto digests via Gemma with template offline path."""

from __future__ import annotations

import logging
from typing import Any, Optional

from crypto.gemma.client import complete_json, gemma_config, gemma_enabled
from crypto.gemma.prompts import digest_messages
from crypto.gemma.schemas import extract_json_object, validate_digest

log = logging.getLogger(__name__)


def build_digest(
    kind: str,
    *,
    events: list[dict[str, Any]] | None = None,
    card: dict[str, Any] | None = None,
    vetoes: list[str] | None = None,
    force_mock: bool | None = None,
) -> dict[str, Any]:
    """Build a digest dict. Never creates a GO. Fail-soft to templates."""
    kind_n = str(kind or "morning").strip().lower()
    if kind_n not in {"morning", "post_trade", "veto"}:
        kind_n = "morning"

    template = _template_digest(kind_n, events=events, card=card, vetoes=vetoes)
    template["source"] = "template"

    if not gemma_enabled():
        return template

    cfg = gemma_config()
    use_mock = force_mock if force_mock is not None else False

    if not use_mock:
        raw = complete_json(
            digest_messages(kind_n, events=events, card=card, vetoes=vetoes),
            purpose="crypto_digest",
        )
        if raw:
            parsed = _try_validate(raw)
            if parsed is not None:
                # Force requested kind — model cannot change task identity
                parsed["kind"] = kind_n
                parsed["source"] = "gemma"
                return parsed
            log.info("gemma digest schema reject — template")
        else:
            log.info("gemma digest timeout/unavailable — mock/template")
            use_mock = bool(cfg.get("mock_when_unavailable"))

    if use_mock:
        mock = dict(template)
        mock["source"] = "mock"
        return mock

    return template


def _template_digest(
    kind: str,
    *,
    events: list[dict[str, Any]] | None,
    card: dict[str, Any] | None,
    vetoes: list[str] | None,
) -> dict[str, Any]:
    events = events or []
    if kind == "morning":
        tiers = {}
        for e in events:
            t = str(e.get("tier") or "WATCH")
            tiers[t] = tiers.get(t, 0) + 1
        bullets = [
            f"{e.get('tier', 'WATCH')}: {e.get('title', '')[:120]}"
            for e in events[:5]
        ] or ["No scored headlines in the window."]
        summary = (
            f"Morning desk: {len(events)} intel items "
            f"(IMMINENT={tiers.get('IMMINENT', 0)}, "
            f"BREAKING={tiers.get('BREAKING', 0)}, "
            f"WATCH={tiers.get('WATCH', 0)}). Advisory only."
        )
        return {"kind": kind, "summary": summary, "bullets": bullets}

    if kind == "post_trade":
        c = card or {}
        bullets = [
            f"Status {c.get('status') or 'n/a'} setup {c.get('setup') or 'none'}",
            f"Instrument {c.get('instrument') or 'n/a'} side {c.get('side') or '-'}",
            f"Score {c.get('score')}; size cap {c.get('size_cap_usd')}",
        ]
        summary = (
            f"Post-trade note for {c.get('instrument') or 'position'}: "
            f"{c.get('status') or 'n/a'} under {c.get('gex_regime') or 'uncertain'}."
        )
        return {"kind": kind, "summary": summary, "bullets": bullets}

    # veto
    vlist = list(vetoes or (card or {}).get("veto_reasons") or [])
    bullets = [str(v) for v in vlist[:5]] or ["No veto detail provided."]
    summary = (
        "Signal vetoed — hard gates sit above language. "
        "Gemma explains only; it never creates a GO."
    )
    return {"kind": "veto", "summary": summary, "bullets": bullets}


def _try_validate(raw: str) -> Optional[dict[str, Any]]:
    obj = extract_json_object(raw)
    if obj is None:
        return None
    try:
        return validate_digest(obj)
    except ValueError as exc:
        log.debug("digest validate: %s", exc)
        return None
