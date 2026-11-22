"""GO card three-line explanations from structured fields.

Code owns every price and number. Gemma only words the lines.
Never creates a GO. Invalid / timeout → keep engine reasons.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from crypto.gemma.client import complete_json, gemma_config, gemma_enabled
from crypto.gemma.prompts import go_prose_messages
from crypto.gemma.schemas import (
    allowed_numbers_from_fields,
    extract_json_object,
    validate_go_prose,
)

log = logging.getLogger(__name__)


def explain_go_card(
    card: dict[str, Any],
    *,
    force_mock: bool | None = None,
) -> dict[str, Any]:
    """Return card with optional rewritten ``reasons`` (exactly 3 lines).

    Adds ``prose_source``: ``gemma`` | ``template`` | ``mock`` | ``engine``.
    Does not change status, scores, levels, or create a GO from NO-GO.
    """
    out = dict(card)
    engine_reasons = list(card.get("reasons") or [])
    while len(engine_reasons) < 3:
        engine_reasons.append("Structured setup fields matched rules.")
    engine_reasons = engine_reasons[:3]
    out["reasons"] = engine_reasons
    out["prose_source"] = "engine"

    if not gemma_enabled():
        return out

    fields = _structured_fields(card)
    allowed = allowed_numbers_from_fields(fields)
    cfg = gemma_config()
    use_mock = force_mock if force_mock is not None else False

    if not use_mock:
        raw = complete_json(go_prose_messages(fields), purpose="crypto_go_prose")
        if raw:
            parsed = _try_validate(raw, allowed)
            if parsed is not None:
                out["reasons"] = parsed["lines"]
                out["prose_source"] = "gemma"
                return out
            log.info("gemma go_prose schema reject — keep engine reasons")
        else:
            log.info("gemma go_prose timeout/unavailable — template/mock")
            use_mock = bool(cfg.get("mock_when_unavailable"))

    if use_mock:
        out["reasons"] = _template_lines(card)
        out["prose_source"] = "mock"
        return out

    out["prose_source"] = "template"
    out["reasons"] = _template_lines(card)
    return out


def _structured_fields(card: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "status",
        "setup",
        "instrument",
        "side",
        "entry_zone",
        "target",
        "stop",
        "size_cap_usd",
        "score",
        "invalidation",
        "gex_regime",
        "spot",
        "flip",
        "call_wall",
        "put_wall",
        "flow_mode",
        "model_version",
        "rules_version",
        "veto_reasons",
    )
    return {k: card.get(k) for k in keys if k in card}


def _template_lines(card: dict[str, Any]) -> list[str]:
    status = str(card.get("status") or "NO-GO")
    setup = str(card.get("setup") or "none")
    spot = card.get("spot")
    flip = card.get("flip")
    regime = card.get("gex_regime") or "uncertain"
    if status == "GO":
        target = card.get("target")
        stop = card.get("stop")
        return [
            f"{setup}: regime {regime} with spot {spot} vs flip {flip}.",
            f"Target {target}; stop {stop}; score {card.get('score')}.",
            f"Invalidation: {card.get('invalidation') or 'rules breach'}.",
        ]
    vetoes = list(card.get("veto_reasons") or card.get("reasons") or [])[:2]
    veto_txt = "; ".join(str(v) for v in vetoes) or "no setup"
    return [
        f"NO-GO under {regime}; spot {spot}, flip {flip}.",
        f"Blocked: {veto_txt}.",
        "Advisory only — Gemma never creates a GO.",
    ]


def _try_validate(raw: str, allowed: list[float]) -> Optional[dict[str, Any]]:
    obj = extract_json_object(raw)
    if obj is None:
        return None
    try:
        return validate_go_prose(obj, allowed_numbers=allowed)
    except ValueError as exc:
        log.debug("go_prose validate: %s", exc)
        return None
