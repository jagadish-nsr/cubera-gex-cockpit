"""Prompt builders for Gemma language tasks. No tool descriptions."""

from __future__ import annotations

import json
from typing import Any

from crypto.intel.schema import EVENT_TYPES


def classify_messages(title: str, *, source: str = "") -> list[dict[str, str]]:
    types = ", ".join(sorted(EVENT_TYPES))
    system = (
        "You classify crypto market headlines. Reply with JSON only. "
        "No tools. No trading advice. Schema: "
        '{"event_type":"<one of listed>","assets":["BTC"],"venues":["binance"],'
        '"confidence":0.0} where confidence is 0..1. '
        f"event_type must be one of: {types}."
    )
    user = f"Source: {source or 'unknown'}\nHeadline: {title}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def go_prose_messages(fields: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "You write a three-line GO/NO-GO card explanation for a crypto desk. "
        "Reply with JSON only: {\"lines\":[\"...\",\"...\",\"...\"]}. "
        "Use ONLY the numbers present in the structured fields. "
        "Do not invent prices, walls, stops, or scores. No tools. No order instructions."
    )
    # Strip free-form reasons so the model rewords from structure, not old prose
    safe = {
        k: v
        for k, v in fields.items()
        if k
        not in {
            "reasons",
            "veto_reasons",
            "prose_source",
            "gemma",
        }
    }
    user = "Structured fields (authoritative numbers):\n" + json.dumps(safe, default=str)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def digest_messages(
    kind: str,
    *,
    events: list[dict[str, Any]] | None = None,
    card: dict[str, Any] | None = None,
    vetoes: list[str] | None = None,
) -> list[dict[str, str]]:
    system = (
        "You write a short desk digest for CryptoCubera. JSON only: "
        '{"kind":"<morning|post_trade|veto>","summary":"...","bullets":["..."]}. '
        "Advisory only. No tools. Do not create a GO or set prices. "
        "Do not invent numeric levels — omit numbers you were not given."
    )
    payload = {
        "kind": kind,
        "events": events or [],
        "card": card,
        "vetoes": vetoes or [],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, default=str)},
    ]
