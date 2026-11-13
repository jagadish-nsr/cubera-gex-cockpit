"""Automated first-30-min session-open checklist (Admin + /api/health companion).

Mirrors the 12-Nov operator checklist without inventing market data.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from app.admin.clock import IST, as_ist, now_ist


def _ideas_probe(symbol: str = "NIFTY") -> dict[str, Any]:
    """Peek at the in-memory Ideas batch — never invents a summary."""
    try:
        import web_server
        with web_server._REC_LOCK:
            batch = web_server._REC_BATCH.get((symbol, web_server._ACTIVE_STATE.get("window") or "current_week"))
            if batch is None:
                # try any window for this symbol
                for (sym, _win), b in web_server._REC_BATCH.items():
                    if sym == symbol:
                        batch = b
                        break
        if batch is None:
            return {"ok": False, "count": 0, "summary": "", "reason": "no_batch_yet"}
        ideas = []
        for attr in (
            "ce_recommendations",
            "pe_recommendations",
            "tactical_ce_recommendations",
            "tactical_pe_recommendations",
            "bookmarked_recommendations",
        ):
            ideas.extend(list(getattr(batch, attr, None) or []))
        summary = str(getattr(batch, "executive_summary", "") or "")
        vwap_blackout = "Missing session VWAP" in summary
        return {
            "ok": (len(ideas) > 0) or (not vwap_blackout),
            "count": len(ideas),
            "summary": summary[:200],
            "vwap_blackout": vwap_blackout,
            "reason": "vwap_blackout" if vwap_blackout else ("empty_ok" if not ideas else ""),
        }
    except Exception as exc:
        return {"ok": False, "count": 0, "summary": "", "reason": f"ideas_probe_error:{exc}"}


def build_session_checklist(
    *,
    now: datetime | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    import config
    from app.readiness import build_readiness

    now = as_ist(now or now_ist())
    market_open = bool(config.is_market_open(now))
    readiness = build_readiness(market_open=market_open)
    ideas = _ideas_probe("NIFTY")

    after_ideas_window = now.time().replace(tzinfo=None) >= time(9, 16)
    checks: list[dict[str, Any]] = []

    checks.append({
        "id": "version",
        "label": "Process version reported",
        "ok": bool(version),
        "detail": version or "missing",
    })
    checks.append({
        "id": "readiness",
        "label": "Readiness ready (or off-session)",
        "ok": bool(readiness.get("ready")),
        "detail": ",".join(readiness.get("reasons") or []) or "ready",
    })
    chains = (readiness.get("probes") or {}).get("chains") or {}
    any_chain = any(c.get("ok") for c in chains.values())
    checks.append({
        "id": "chain",
        "label": "At least one fresh chain publish",
        "ok": (not market_open) or any_chain,
        "detail": "off_session" if not market_open else (
            "ok" if any_chain else "no_fresh_chain"
        ),
    })
    spots = (readiness.get("probes") or {}).get("spots") or {}
    any_spot = any(s.get("ok") for s in spots.values())
    checks.append({
        "id": "spot",
        "label": "At least one fresh spot quote",
        "ok": (not market_open) or any_spot,
        "detail": "off_session" if not market_open else (
            "ok" if any_spot else "no_fresh_spot"
        ),
    })
    feed = (readiness.get("probes") or {}).get("feed") or {}
    checks.append({
        "id": "feed",
        "label": "Spot WS feed live in session",
        "ok": (not market_open) or bool(feed.get("ok")),
        "detail": feed.get("reason") or ("live" if feed.get("ok") else "not_live"),
    })
    circuits = (readiness.get("probes") or {}).get("circuits") or {}
    open_cbs = [s for s, c in circuits.items() if not c.get("ok")]
    checks.append({
        "id": "circuits",
        "label": "No OPEN circuit breakers",
        "ok": len(open_cbs) == 0,
        "detail": ",".join(open_cbs) if open_cbs else "all_closed",
    })

    ingest = (readiness.get("probes") or {}).get("ingest") or {}
    ingest_required = bool(ingest.get("required"))
    checks.append({
        "id": "ingest_heartbeat",
        "label": "Ingest heartbeat (dual-process)",
        "ok": (not ingest_required) or bool(ingest.get("ok")),
        "detail": (
            "combined_mode" if not ingest_required
            else (ingest.get("reason") or f"age={ingest.get('age_s')}s")
        ),
        "skipped": not ingest_required,
    })

    ideas_applicable = market_open and after_ideas_window
    checks.append({
        "id": "ideas_vwap",
        "label": "Ideas after 09:16 — not VWAP-blackout",
        "ok": (not ideas_applicable) or bool(ideas.get("ok")),
        "detail": (
            "before_09:16" if market_open and not after_ideas_window
            else ("off_session" if not market_open
                  else f"count={ideas.get('count')} {ideas.get('reason') or ideas.get('summary') or ''}")
        ),
        "skipped": not ideas_applicable,
    })

    failed = [c for c in checks if not c.get("ok") and not c.get("skipped")]
    return {
        "as_of": now.isoformat(timespec="seconds"),
        "market_open": market_open,
        "pass": len(failed) == 0,
        "failed": [c["id"] for c in failed],
        "checks": checks,
        "readiness": readiness,
        "ideas": ideas,
        "slo_doc": "docs/sli-slo.md",
    }
