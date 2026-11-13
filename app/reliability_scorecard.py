"""Reliability scorecard for session-evaluation notes (Pillar G4).

Pulls what is available from Admin ops / SLO events. Missing inputs stay
explicit — never invent ready-minutes or restart counts.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def rollup_readiness_minutes(
    *,
    session_date: str,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Unique IST minutes 09:20–15:20 classified ready vs not_ready.

    When ``events`` is None, loads from admin ``ops_events``. Empty result
    returns zeros with ``available=False`` so the UI does not invent coverage.
    """
    if events is None:
        try:
            from app.admin import db
            events = db.list_readiness_minutes(session_date)
        except Exception as exc:
            return {
                "ready_minutes": None,
                "not_ready_minutes": None,
                "total_minutes": None,
                "burn_pct": None,
                "available": False,
                "error": str(exc),
            }

    ready = 0
    not_ready = 0
    seen: set[str] = set()
    for row in events or []:
        ts = str(row.get("ts") or "")
        minute = ts[:16]  # YYYY-MM-DDTHH:MM
        if len(minute) < 16 or minute in seen:
            continue
        seen.add(minute)
        fields = row.get("fields") or {}
        if isinstance(fields, str):
            try:
                fields = json.loads(fields)
            except Exception:
                fields = {}
        # Prefer explicit ready flag; fall back to message.
        is_ready = bool(fields.get("ready")) if "ready" in fields else (
            str(row.get("message") or "") == "ready"
        )
        if fields.get("ready") is False:
            is_ready = False
        if is_ready:
            ready += 1
        else:
            not_ready += 1

    total = ready + not_ready
    if total == 0:
        return {
            "ready_minutes": None,
            "not_ready_minutes": None,
            "total_minutes": 0,
            "burn_pct": None,
            "available": False,
        }
    burn = round(100.0 * not_ready / total, 2)
    return {
        "ready_minutes": ready,
        "not_ready_minutes": not_ready,
        "total_minutes": total,
        "burn_pct": burn,
        "available": True,
    }


def build_reliability_scorecard(
    *,
    session_date: str | None = None,
    version: str | None = None,
    health: dict[str, Any] | None = None,
    ops: dict[str, Any] | None = None,
    checklist: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a scorecard dict suitable for pasting into session-evaluation.md."""
    health = health or {}
    ops = ops or {}
    checklist = checklist or {}
    readiness = health.get("readiness") or {}
    fails = ((ops.get("events_summary") or {}).get("failures") or {})
    ingest = ((readiness.get("probes") or {}).get("ingest") or {})

    day = session_date or datetime.now().date().isoformat()
    ready_minutes = None
    not_ready_minutes = None
    burn_note = "From slo.readiness.minute when present"
    # Prefer explicit rollup if present on ops payload.
    slo = ops.get("slo") or ops.get("readiness_rollup") or {}
    if isinstance(slo, dict) and (
        slo.get("ready_minutes") is not None or slo.get("not_ready_minutes") is not None
    ):
        ready_minutes = slo.get("ready_minutes")
        not_ready_minutes = slo.get("not_ready_minutes")
        if slo.get("burn_pct") is not None:
            burn_note = f"burn={slo.get('burn_pct')}% of 09:20–15:20"
    else:
        rolled = rollup_readiness_minutes(session_date=day)
        if rolled.get("available"):
            ready_minutes = rolled["ready_minutes"]
            not_ready_minutes = rolled["not_ready_minutes"]
            burn_note = (
                f"burn={rolled.get('burn_pct')}% · {rolled.get('total_minutes')} min sampled"
            )

    rows = [
        {
            "metric": "Process version",
            "value": version or health.get("version") or "—",
            "note": "Must match promoted tag",
        },
        {
            "metric": "Degrade mode (as-of)",
            "value": readiness.get("degrade_mode") or "—",
            "note": ",".join(readiness.get("reasons") or []) or "none",
        },
        {
            "metric": "Ready minutes (session)",
            "value": ready_minutes if ready_minutes is not None else "not rolled up",
            "note": burn_note,
        },
        {
            "metric": "Not-ready minutes",
            "value": not_ready_minutes if not_ready_minutes is not None else "not rolled up",
            "note": "Target burn ≤ 1% of 09:20–15:20",
        },
        {
            "metric": "Watchdog restarts",
            "value": int(fails.get("watchdog.restart") or 0),
            "note": "SLO: 0 mid-session on serve",
        },
        {
            "metric": "Ingest heartbeat age_s",
            "value": ingest.get("age_s"),
            "note": ingest.get("reason") or ("dual" if ingest.get("required") else "combined"),
        },
        {
            "metric": "Session checklist",
            "value": "PASS" if checklist.get("pass") else ("FAIL" if checklist else "—"),
            "note": ",".join(checklist.get("failed") or []) or "—",
        },
        {
            "metric": "VWAP blackout / Ideas refuses",
            "value": fails.get("rec.refused") or fails.get("ideas_vwap") or "see ops",
            "note": "Zero sustained Missing session VWAP",
        },
    ]

    return {
        "session_date": day,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
        "markdown": render_scorecard_markdown(rows, session_date=day),
        "doc": "docs/session-evaluation-TEMPLATE.md · docs/sli-slo.md",
    }


def render_scorecard_markdown(
    rows: list[dict[str, Any]],
    *,
    session_date: str | None = None,
) -> str:
    lines = [
        "## Reliability scorecard",
        "",
        f"Session: `{session_date or 'YYYY-MM-DD'}` · cite SLI burn from `docs/sli-slo.md`.",
        "",
        "| Metric | Value | Notes |",
        "|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('metric')} | {row.get('value')} | {row.get('note') or ''} |"
        )
    lines.append("")
    lines.append(
        "Ready/not-ready minutes roll up from `slo.readiness.minute` (09:20–15:20 IST). "
        "Do not invent counts when the rollup is unavailable."
    )
    lines.append("")
    return "\n".join(lines)
