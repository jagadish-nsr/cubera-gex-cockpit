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
        + " Rewrite ONLY the desk prose for 'headline' and 'drivers' so a trader "
        "scanning the cockpit instantly knows: where spot sits vs the book, "
        "why this stance, and what would invalidate it. "
        "Speak in plain desk English (not jargon soup). "
        "Return exactly two lines: "
        "HEADLINE: <one short line naming location + stance implication> "
        "DRIVERS: <one or two short sentences tying zone/drivers to trigger/kill "
        "without repeating the trigger/kill strings verbatim unless needed>. "
        "You must not change stance, confidence, trigger, or kill. "
        "Do not invent wall levels or scores. Do not say fade/momentum from regime alone."
    )
    user = json.dumps(payload, ensure_ascii=False)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def positioning_card_messages(flow: dict[str, Any]) -> list[dict[str, str]]:
    """Trader-facing rewrite of the Index futures positioning card."""
    payload = {
        "headline": flow.get("headline"),
        "overnight_line": flow.get("overnight_line"),
        "live_line": flow.get("live_line"),
        "overnight_state": flow.get("overnight_state"),
        "live_label": flow.get("live_label"),
        "alert": flow.get("alert"),
        "file_date": flow.get("file_date"),
    }
    system = (
        _DESK_RULES
        + " Rewrite the Index futures positioning card for a trader scanning the cockpit. "
        "Explain overnight participant book vs live futures price/OI in plain English. "
        "Do not invent a direction call or fade/momentum. "
        "Keep state labels and any numbers exactly as given. "
        "Return exactly three lines: "
        "HEADLINE: <one short line> "
        "OVERNIGHT: <one sentence> "
        "LIVE: <one sentence>."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def parse_positioning_rewrite(text: str) -> tuple[str, str, str] | None:
    if not text or not str(text).strip():
        return None
    headline = overnight = live = ""
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("headline:"):
            headline = line.split(":", 1)[1].strip()
        elif low.startswith("overnight:"):
            overnight = line.split(":", 1)[1].strip()
        elif low.startswith("live:"):
            live = line.split(":", 1)[1].strip()
    if headline and (overnight or live):
        return headline, overnight, live
    parts = [p.strip() for p in str(text).strip().split("\n") if p.strip()]
    if len(parts) >= 3:
        return parts[0][:160], parts[1][:400], parts[2][:400]
    return None


def choppiness_card_messages(chop: dict[str, Any]) -> list[dict[str, str]]:
    """Trader-facing rewrite of the How price moved card."""
    payload = {
        "state": chop.get("state"),
        "headline": chop.get("headline"),
        "line": chop.get("line"),
        "chop": chop.get("chop"),
        "efficiency": chop.get("efficiency"),
        "vwap_crosses": chop.get("vwap_crosses"),
        "n_5m": chop.get("n_5m"),
        "n_1m": chop.get("n_1m"),
    }
    system = (
        _DESK_RULES
        + " Rewrite the 'How price moved' card for a trader. "
        "Say whether the path is boxed, directional, or mixed, and what that means "
        "for watching the next stretch — not a trade instruction. "
        "Keep every chop/efficiency/VWAP number exactly as given. "
        "Return exactly two lines: "
        "HEADLINE: <one short line> "
        "LINE: <one or two short sentences>."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def parse_choppiness_rewrite(text: str) -> tuple[str, str] | None:
    if not text or not str(text).strip():
        return None
    headline = line = ""
    for raw in str(text).splitlines():
        row = raw.strip()
        if not row:
            continue
        low = row.lower()
        if low.startswith("headline:"):
            headline = row.split(":", 1)[1].strip()
        elif low.startswith("line:"):
            line = row.split(":", 1)[1].strip()
    if headline and line:
        return headline, line
    parts = [p.strip() for p in str(text).strip().split("\n") if p.strip()]
    if len(parts) >= 2:
        return parts[0][:160], " ".join(parts[1:])[:500]
    return None


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


_FAQ_TRADER_FOCUS: dict[str, str] = {
    "futures_positioning": (
        " Explain index futures positioning for a trader: overnight participant "
        "read vs live futures price/OI pair (long build-up, short build-up, "
        "short covering, long unwinding). Say what it means for inventory pressure "
        "without calling a direction. Note Sensex has no NSE participant file."
    ),
    "price_move": (
        " Explain how price moved this session vs prior close and the day range. "
        "Locate spot versus call/put walls and flip when those numbers are present. "
        "Describe path (up/down/chop), not a forecast."
    ),
    "alert_tape": (
        " Rephrase the cockpit alert tape for a trader: stance, zone, why it "
        "matters now, trigger and kill as the watchlist. Keep stance/confidence/"
        "trigger/kill exactly as in the payload."
    ),
}


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
            "If a heuristic_brief is supplied, rephrase it into clear trader desk prose — "
            "what it means on the tape right now — while keeping every number, stance, "
            "kill, trigger, futures state label, and leg exactly as given. "
            "No markdown. Two to four short sentences unless the brief is a list."
        )
        extra = _FAQ_TRADER_FOCUS.get(str(faq_id))
        if extra:
            focus += extra
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


def commentary_story_messages(
    *,
    event_type: str,
    event_facts: dict[str, Any],
    template_headline: str,
    template_body: str,
    session_context: dict[str, Any] | None = None,
    max_words: int = 80,
) -> list[dict[str, str]]:
    """Rewrite a detector event into trader-meaningful session commentary."""
    system = (
        _DESK_RULES
        + " You write Session Commentary story entries for an Indian index-options desk. "
        "Rewrite the template HEADLINE and BODY into clearer trader prose. "
        "Use session_context when it helps: spot/walls/flip/magnet/regime/net_gex, "
        "OI as-of, price_move vs prior close, positioning/futures_state, market_alert "
        "stance/trigger/kill, digest catalyst note, running_thesis, and GO summary. "
        "Only cite values present in the payload. "
        "Keep every event_facts number exactly (strikes, pts, pct, OI). "
        "Do not invent catalysts, dealer defense, fade/momentum from regime alone, or new levels. "
        "Walls are concentration, not floors/ceilings. "
        "POSITIVE/NEGATIVE regime is call-heavy vs put-heavy under short_call_short_put. "
        f"Keep the body under about {int(max_words)} words. Plain English. No markdown. "
        "Return exactly two lines: "
        "HEADLINE: <one short line> "
        "BODY: <one or two short paragraphs as a single line>."
    )
    payload = {
        "event_type": event_type,
        "event_facts": event_facts or {},
        "template_headline": template_headline,
        "template_body": template_body,
        "session_context": session_context or {},
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def parse_commentary_story_rewrite(text: str) -> tuple[str, str] | None:
    """Parse HEADLINE:/BODY: lines from commentary model output."""
    if not text or not str(text).strip():
        return None
    headline = ""
    body = ""
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("headline:"):
            headline = line.split(":", 1)[1].strip()
        elif low.startswith("body:"):
            body = line.split(":", 1)[1].strip()
    if headline and body:
        return headline[:180], body[:900]
    parts = [p.strip() for p in str(text).strip().split("\n") if p.strip()]
    if len(parts) >= 2:
        return parts[0][:180], " ".join(parts[1:])[:900]
    return None
