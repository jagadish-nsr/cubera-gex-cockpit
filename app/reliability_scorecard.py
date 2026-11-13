"""Reliability scorecard for session-evaluation notes (Pillar G4).

Pulls what is available from Admin ops / SLO events. Missing inputs stay
explicit — never invent ready-minutes or restart counts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


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

    ready_minutes = None
    not_ready_minutes = None
    # Prefer explicit rollup if present on ops payload.
    slo = ops.get("slo") or ops.get("readiness_rollup") or {}
    if isinstance(slo, dict):
        ready_minutes = slo.get("ready_minutes")
        not_ready_minutes = slo.get("not_ready_minutes")

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
            "note": "From slo.readiness.minute when present",
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
        "session_date": session_date or datetime.now().date().isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
        "markdown": render_scorecard_markdown(rows, session_date=session_date),
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
        "Fill ready/not-ready minutes from Admin Observability / `slo.readiness.minute` "
        "events after the session. Do not invent counts."
    )
    lines.append("")
    return "\n".join(lines)
