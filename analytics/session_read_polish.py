"""Optional LLM polish for cockpit session-read cards (positioning + chop).

Deterministic headlines stay the source of truth for numbers/labels. Prose may
be rephrased for a trader scanning the desk. Fail-soft → heuristic text.
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_S = 180.0


def _cache_get(key: str) -> dict | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    if time.time() - hit[0] > _CACHE_TTL_S:
        return None
    return dict(hit[1])


def _cache_put(key: str, value: dict) -> None:
    _CACHE[key] = (time.time(), dict(value))
    if len(_CACHE) > 64:
        for old_key, _ in sorted(_CACHE.items(), key=lambda kv: kv[1][0])[:16]:
            _CACHE.pop(old_key, None)


def _mark_heuristic(d: dict[str, Any]) -> dict[str, Any]:
    out = dict(d)
    out.setdefault("text_source", "heuristic")
    out.setdefault("llm_model", None)
    return out


def polish_positioning_flow(flow: dict[str, Any] | None) -> dict[str, Any] | None:
    """Polish positioning headline / overnight / live lines for the cockpit card."""
    if not isinstance(flow, dict) or not flow:
        return flow
    out = _mark_heuristic(flow)
    try:
        from analytics.llm_client import complete_chat, llm_enabled, model_name
        from analytics.llm_prompts import (
            parse_positioning_rewrite,
            positioning_card_messages,
        )

        if not llm_enabled("session_reads"):
            return out
        key = "|".join([
            str(out.get("headline") or ""),
            str(out.get("overnight_line") or ""),
            str(out.get("live_line") or ""),
            str(out.get("overnight_state") or ""),
            str(out.get("live_label") or ""),
        ])
        cached = _cache_get(f"pos:{key}")
        if cached is not None:
            return cached
        raw = complete_chat(
            positioning_card_messages(out),
            purpose="positioning_card",
            temperature=0.2,
            max_tokens=360,
            timeout_s=90.0,
        )
        parsed = parse_positioning_rewrite(raw or "")
        if not parsed:
            return out
        headline, overnight, live = parsed
        if len(headline) < 8:
            return out
        out["headline"] = headline
        if overnight:
            out["overnight_line"] = overnight
        if live:
            out["live_line"] = live
        out["text_source"] = "genai"
        out["llm_model"] = model_name()
        _cache_put(f"pos:{key}", out)
        return out
    except Exception as ex:  # noqa: BLE001
        log.debug("positioning polish: %s", ex)
        return _mark_heuristic(flow)


def polish_choppiness(chop: dict[str, Any] | None) -> dict[str, Any] | None:
    """Polish 'How price moved' headline/line for the cockpit card."""
    if not isinstance(chop, dict) or not chop:
        return chop
    out = _mark_heuristic(chop)
    try:
        from analytics.llm_client import complete_chat, llm_enabled, model_name
        from analytics.llm_prompts import choppiness_card_messages, parse_choppiness_rewrite

        if not llm_enabled("session_reads"):
            return out
        key = "|".join([
            str(out.get("state") or ""),
            str(out.get("headline") or ""),
            str(out.get("line") or ""),
            str(out.get("chop") or ""),
            str(out.get("efficiency") or ""),
        ])
        cached = _cache_get(f"chop:{key}")
        if cached is not None:
            return cached
        raw = complete_chat(
            choppiness_card_messages(out),
            purpose="choppiness_card",
            temperature=0.2,
            max_tokens=280,
            timeout_s=90.0,
        )
        parsed = parse_choppiness_rewrite(raw or "")
        if not parsed:
            return out
        headline, line = parsed
        if len(headline) < 6:
            return out
        out["headline"] = headline
        if line:
            out["line"] = line
        out["text_source"] = "genai"
        out["llm_model"] = model_name()
        _cache_put(f"chop:{key}", out)
        return out
    except Exception as ex:  # noqa: BLE001
        log.debug("choppiness polish: %s", ex)
        return _mark_heuristic(chop)
