"""Headline classification via Gemma with keyword fallback.

Advisory only — never creates a GO. Timeout / invalid JSON → keywords.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from crypto.gemma.client import complete_json, gemma_config, gemma_enabled
from crypto.gemma.prompts import classify_messages
from crypto.gemma.schemas import extract_json_object, validate_classify
from crypto.intel.classify import PRIOR_MOVE_BY_TYPE, classify_headline

log = logging.getLogger(__name__)


def classify_with_gemma(
    title: str,
    *,
    source: str = "",
    assets_hint: Iterable[str] | None = None,
    venues_hint: Iterable[str] | None = None,
    event_type_hint: str | None = None,
    force_mock: bool | None = None,
) -> dict[str, Any]:
    """Classify a headline. Always returns a usable dict (never raises).

    Keys: event_type, assets, venues, prior_move_proxy, confidence, source
    (``gemma`` | ``keyword`` | ``mock``).
    """
    fallback = classify_headline(
        title,
        assets_hint=assets_hint,
        venues_hint=venues_hint,
        event_type_hint=event_type_hint,
    )
    fallback_out = {
        "event_type": str(fallback["event_type"]),
        "assets": list(fallback["assets"]),  # type: ignore[arg-type]
        "venues": list(fallback["venues"]),  # type: ignore[arg-type]
        "prior_move_proxy": float(fallback["prior_move_proxy"]),  # type: ignore[arg-type]
        "confidence": 0.55,
        "source": "keyword",
    }

    if not gemma_enabled():
        return fallback_out

    cfg = gemma_config()
    use_mock = force_mock if force_mock is not None else False

    if not use_mock:
        raw = complete_json(classify_messages(title, source=source), purpose="crypto_classify")
        if raw:
            parsed = _try_validate(raw)
            if parsed is not None:
                return {
                    "event_type": parsed["event_type"],
                    "assets": _merge_lists(parsed["assets"], assets_hint),
                    "venues": _merge_lists(parsed["venues"], venues_hint, upper=False),
                    "prior_move_proxy": float(
                        PRIOR_MOVE_BY_TYPE.get(parsed["event_type"], 0.3)
                    ),
                    "confidence": float(parsed["confidence"]),
                    "source": "gemma",
                }
            log.info("gemma classify schema reject — keyword fallback")
        else:
            log.info("gemma classify timeout/unavailable — keyword fallback")
            use_mock = bool(cfg.get("mock_when_unavailable"))

    if use_mock:
        mock = dict(fallback_out)
        mock["source"] = "mock"
        mock["confidence"] = min(0.5, float(mock["confidence"]))
        return mock

    return fallback_out


def _try_validate(raw: str) -> Optional[dict[str, Any]]:
    obj = extract_json_object(raw)
    if obj is None:
        return None
    try:
        return validate_classify(obj)
    except ValueError as exc:
        log.debug("classify validate: %s", exc)
        return None


def _merge_lists(
    primary: list[str],
    hint: Iterable[str] | None,
    *,
    upper: bool = True,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in list(primary) + list(hint or []):
        key = item.upper() if upper else item.lower()
        val = item.upper() if upper else item.lower()
        if key not in seen:
            seen.add(key)
            out.append(val)
    return out
