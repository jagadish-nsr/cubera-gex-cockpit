"""Prompt builders for grounded LLM prose (digest + alert tape).

Inputs are already-ranked / already-evaluated facts. The model must not invent
levels, tickers, or catalysts. No trade advice / no auto-execution language.
"""

from __future__ import annotations

import json
from typing import Any


_DESK_RULES = (
    "You write for an Indian index-options desk (Nifty / Bank Nifty / Sensex). "
    "Use only facts in the user payload. Do not invent tickers, strikes, "
    "percent moves, catalysts, or levels. Do not give trade advice, order "
    "instructions, or auto-execution language. Plain English. No markdown."
)


def digest_narrative_messages(
    events: list[dict[str, Any]],
    *,
    catalyst_risk: str,
    overnight_impact: int,
) -> list[dict[str, str]]:
    slim = []
    for e in events or []:
        if not isinstance(e, dict):
            continue
        slim.append({
            "title": e.get("title"),
            "bucket": e.get("bucket"),
            "category": e.get("category"),
            "sentiment": e.get("sentiment"),
            "impact": e.get("impact"),
        })
    system = (
        _DESK_RULES
        + " Write ONE overnight pre-open paragraph covering US/global, Asia, "
        "geopolitics/trade, and India where those buckets appear in the payload. "
        "End by noting that full linked headlines are in Real-Time News. "
        "Mention catalyst intensity only if overnight_impact >= 4."
    )
    user = json.dumps(
        {
            "catalyst_risk": catalyst_risk,
            "overnight_impact": int(overnight_impact or 0),
            "events": slim,
        },
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def alert_tape_messages(alert: dict[str, Any]) -> list[dict[str, str]]:
    """Polish headline + drivers only; stance/trigger/kill stay deterministic."""
    payload = {
        "stance": alert.get("stance"),
        "confidence": alert.get("confidence"),
        "zone": alert.get("zone"),
        "headline": alert.get("headline"),
        "where": alert.get("where") or "",
        "drivers": alert.get("drivers"),
        "trigger": alert.get("trigger"),
        "kill": alert.get("kill"),
    }
    system = (
        _DESK_RULES
        + " Rewrite ONLY the desk prose for 'headline' and 'drivers'. "
        "Return exactly two lines: "
        "HEADLINE: <one short line> "
        "DRIVERS: <one or two short sentences>. "
        "You must not change stance, confidence, trigger, or kill. "
        "Do not invent wall levels or scores."
    )
    user = json.dumps(payload, ensure_ascii=False)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


_QA_DESK_RULES = (
    "You write for an Indian index-options desk (Nifty / Bank Nifty / Sensex). "
    "Use only facts in the user payload. Do not invent tickers, strikes, "
    "percent moves, catalysts, lot sizes, expiries, or levels. "
    "You may discuss trade suggestions that are already in the payload "
    "(market_alert stance/trigger/kill, GO legs/thesis, walls/flip/regime). "
    "Do not invent a new structure or strike absent from the payload. "
    "Do not use order-execution language (place order, market order, buy now, "
    "sell now, auto-exec, enter long/short). "
    "End every answer with exactly: "
    "Decision support only — you confirm; chat never stages orders. "
    "Plain English. Prefer short paragraphs. Cite OI/as-of stamps when stating levels. "
    "POSITIVE/NEGATIVE regime is call-heavy vs put-heavy book balance under "
    "short_call_short_put — not a fade or momentum forecast."
)


def session_qa_messages(
    context: dict[str, Any],
    question: str,
    *,
    history: list[dict[str, str]] | None = None,
    faq_id: str | None = None,
    heuristic_brief: str | None = None,
) -> list[dict[str, str]]:
    """Messages for free-form ask or optional FAQ polish."""
    focus = ""
    if faq_id:
        focus = (
            f" This is FAQ id '{faq_id}'. Stay on that topic. "
            "If a heuristic_brief is supplied, rewrite it for clarity only — "
            "keep every number, stance, kill, trigger, and leg exactly as given."
        )
    system = _QA_DESK_RULES + focus
    payload: dict[str, Any] = {
        "question": question,
        "context": context,
    }
    if heuristic_brief:
        payload["heuristic_brief"] = heuristic_brief
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for h in history or []:
        if not isinstance(h, dict):
            continue
        role = str(h.get("role") or "user").lower()
        content = str(h.get("content") or "").strip()
        if not content:
            continue
        if role not in ("user", "assistant"):
            role = "user"
        messages.append({"role": role, "content": content[:1200]})
    messages.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False)})
    return messages


def parse_alert_tape_rewrite(text: str) -> tuple[str, str] | None:
    """Parse HEADLINE:/DRIVERS: lines from model output."""
    if not text or not str(text).strip():
        return None
    headline = ""
    drivers = ""
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("headline:"):
            headline = line.split(":", 1)[1].strip()
        elif low.startswith("drivers:"):
            drivers = line.split(":", 1)[1].strip()
    if headline and drivers:
        return headline, drivers
    # Fallback: first paragraph headline, rest drivers
    parts = [p.strip() for p in str(text).strip().split("\n") if p.strip()]
    if len(parts) >= 2:
        return parts[0][:160], " ".join(parts[1:])[:500]
    return None
