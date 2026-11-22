"""Manual Decision payload helpers for GO cards.

Decision support only: format levels for clipboard / JSON export so the
operator can place by hand on Delta. Never submits exchange orders.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def _entry_mid(card: dict[str, Any]) -> Optional[float]:
    zone = card.get("entry_zone")
    if isinstance(zone, (list, tuple)) and zone:
        nums = [float(x) for x in zone if x is not None]
        if nums:
            return sum(nums) / len(nums)
    spot = card.get("spot")
    if spot is not None:
        return float(spot)
    return None


def _format_entry(zone: Any) -> str:
    if isinstance(zone, (list, tuple)) and zone:
        parts = [str(int(float(x))) if float(x) == int(float(x)) else str(x) for x in zone if x is not None]
        if len(parts) >= 2:
            return f"{parts[0]} – {parts[1]}"
        if parts:
            return parts[0]
    if zone is None:
        return "—"
    return str(zone)


def build_decision_payload(
    card: dict[str, Any],
    *,
    size_usd: Optional[float] = None,
    notes: str = "",
) -> dict[str, Any]:
    """Structured Manual Decision export from a GO card."""
    reasons = list(card.get("reasons") or [])[:3]
    size = size_usd
    if size is None:
        cap = card.get("size_cap_usd")
        size = float(cap) if cap is not None else None
    return {
        "mode": "manual_decision",
        "status": str(card.get("status") or ""),
        "setup": card.get("setup") or "",
        "instrument": card.get("instrument") or "",
        "side": (card.get("side") or "").lower(),
        "entry_zone": card.get("entry_zone"),
        "entry": _entry_mid(card),
        "target": card.get("target"),
        "stop": card.get("stop"),
        "size_usd": size,
        "size_cap_usd": card.get("size_cap_usd"),
        "score": card.get("score"),
        "reasons": reasons,
        "invalidation": card.get("invalidation") or "",
        "valid_until": card.get("valid_until"),
        "model_version": card.get("model_version"),
        "rules_version": card.get("rules_version"),
        "notes": notes or "",
        "note": (
            "Manual Decision — place by hand on Delta. "
            "CryptoCubera does not auto-execute exchange orders."
        ),
    }


def format_decision_levels(card: dict[str, Any], *, size_usd: Optional[float] = None) -> str:
    """One-click clipboard text: instrument, side, entry/target/stop, size, score, reasons."""
    payload = build_decision_payload(card, size_usd=size_usd)
    side = (payload["side"] or "—").upper()
    instrument = payload["instrument"] or "—"
    lines = [
        f"{instrument} {side}",
        f"Entry: {_format_entry(payload.get('entry_zone'))}",
        f"Target: {payload.get('target') if payload.get('target') is not None else '—'}",
        f"Stop: {payload.get('stop') if payload.get('stop') is not None else '—'}",
    ]
    size = payload.get("size_usd")
    if size is not None:
        lines.append(f"Size: ${float(size):.0f}")
    score = payload.get("score")
    if score is not None:
        lines.append(f"Score: {score}")
    if payload.get("valid_until"):
        lines.append(f"Valid until: {payload['valid_until']}")
    if payload.get("invalidation"):
        lines.append(f"Invalidation: {payload['invalidation']}")
    reasons = payload.get("reasons") or []
    if reasons:
        lines.append("Reasons:")
        for i, r in enumerate(reasons, 1):
            lines.append(f"  {i}. {r}")
    lines.append("")
    lines.append(payload["note"])
    return "\n".join(lines)


def decision_json(card: dict[str, Any], *, size_usd: Optional[float] = None, notes: str = "") -> str:
    """Pretty JSON export for placing by hand / journaling."""
    return json.dumps(
        build_decision_payload(card, size_usd=size_usd, notes=notes),
        indent=2,
        default=str,
    )
