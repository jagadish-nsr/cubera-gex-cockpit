"""JSON schema validation for Gemma outputs.

Reject anything that fails validation — callers fall back to keywords/templates.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from crypto.intel.schema import EVENT_TYPES

CLASSIFY_REQUIRED = ("event_type", "assets", "venues", "confidence")
GO_PROSE_REQUIRED = ("lines",)
DIGEST_KINDS = frozenset({"morning", "post_trade", "veto"})
DIGEST_REQUIRED = ("kind", "summary", "bullets")


def extract_json_object(text: str) -> Optional[dict[str, Any]]:
    """Pull the first JSON object from model text (fenced or raw)."""
    if not text or not str(text).strip():
        return None
    raw = str(text).strip()
    # Strip markdown fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.I)
    if fence:
        raw = fence.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        raw = raw[start : end + 1]
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def validate_classify(payload: Any) -> dict[str, Any]:
    """Return normalized classify dict or raise ValueError."""
    if not isinstance(payload, dict):
        raise ValueError("classify payload must be an object")
    for key in CLASSIFY_REQUIRED:
        if key not in payload:
            raise ValueError(f"classify missing {key}")
    event_type = str(payload["event_type"]).strip().lower()
    if event_type not in EVENT_TYPES:
        raise ValueError(f"bad event_type {event_type!r}")
    assets = _str_list(payload["assets"], upper=True)
    venues = _str_list(payload["venues"], upper=False)
    confidence = float(payload["confidence"])
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence out of range: {confidence}")
    return {
        "event_type": event_type,
        "assets": assets,
        "venues": venues,
        "confidence": confidence,
    }


def validate_go_prose(payload: Any, *, allowed_numbers: list[float]) -> dict[str, Any]:
    """Three explanation lines; every number must appear in ``allowed_numbers``."""
    if not isinstance(payload, dict):
        raise ValueError("go_prose payload must be an object")
    for key in GO_PROSE_REQUIRED:
        if key not in payload:
            raise ValueError(f"go_prose missing {key}")
    lines = payload["lines"]
    if not isinstance(lines, list) or len(lines) != 3:
        raise ValueError("go_prose.lines must be exactly 3 strings")
    cleaned: list[str] = []
    for i, line in enumerate(lines):
        s = str(line).strip()
        if not s:
            raise ValueError(f"go_prose line {i} empty")
        if len(s.split()) > 40:
            raise ValueError(f"go_prose line {i} too long")
        _assert_numbers_allowed(s, allowed_numbers)
        cleaned.append(s)
    return {"lines": cleaned}


def validate_digest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("digest payload must be an object")
    for key in DIGEST_REQUIRED:
        if key not in payload:
            raise ValueError(f"digest missing {key}")
    kind = str(payload["kind"]).strip().lower()
    if kind not in DIGEST_KINDS:
        raise ValueError(f"bad digest kind {kind!r}")
    summary = str(payload["summary"]).strip()
    if not summary or len(summary) > 600:
        raise ValueError("digest summary empty or too long")
    bullets = payload["bullets"]
    if not isinstance(bullets, list) or not bullets:
        raise ValueError("digest.bullets must be a non-empty list")
    cleaned = [str(b).strip() for b in bullets if str(b).strip()]
    if not cleaned:
        raise ValueError("digest.bullets empty after clean")
    if len(cleaned) > 8:
        cleaned = cleaned[:8]
    return {"kind": kind, "summary": summary, "bullets": cleaned}


def allowed_numbers_from_fields(fields: dict[str, Any]) -> list[float]:
    """Harvest numeric facts the model is allowed to mention."""
    out: list[float] = []
    for v in _walk_numbers(fields):
        out.append(float(v))
    # Harmless small integers often used in prose (counts, ranks)
    out.extend([0.0, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0])
    return out


def _assert_numbers_allowed(text: str, allowed: list[float]) -> None:
    for num in _extract_numbers(text):
        if abs(num) < 100.0:  # small counts / percents — ignore
            continue
        if 2020.0 <= abs(num) <= 2035.0:  # years
            continue
        if not _matches_allowed(num, allowed):
            raise ValueError(f"invented number in prose: {num}")


def _matches_allowed(extracted: float, allowed: list[float]) -> bool:
    for fn in allowed:
        if abs(extracted - fn) < 1e-4:
            return True
        if abs(extracted - (fn * 100.0)) < 1e-2:
            return True
        if abs((extracted * 100.0) - fn) < 1e-2:
            return True
        if abs(extracted - fn) <= 1.0:
            return True
        if fn != 0 and abs(extracted - fn) / abs(fn) < 0.005:
            return True
    return False


def _extract_numbers(text: str) -> list[float]:
    tokens = re.findall(r"(?:[+-]|\b)\d+(?:,\d{3})*(?:\.\d+)?", text or "")
    out: list[float] = []
    for t in tokens:
        try:
            out.append(float(t.replace(",", "")))
        except ValueError:
            continue
    return out


def _walk_numbers(obj: Any) -> list[float]:
    found: list[float] = []
    if isinstance(obj, dict):
        for v in obj.values():
            found.extend(_walk_numbers(v))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            found.extend(_walk_numbers(item))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        found.append(float(obj))
    return found


def _str_list(value: Any, *, upper: bool) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("expected list")
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        s = str(item).strip()
        if not s:
            continue
        key = s.upper() if upper else s.lower()
        val = s.upper() if upper else s.lower()
        if key not in seen:
            seen.add(key)
            out.append(val)
    return out
