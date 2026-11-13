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
    from data.holidays import is_trading_day
    from app.readiness import TOKEN_MIN_HOURS, build_readiness

    now = as_ist(now or now_ist())
    market_open = bool(config.is_market_open(now))
    readiness = build_readiness(market_open=market_open)
    ideas = _ideas_probe("NIFTY")

    after_ideas_window = now.time().replace(tzinfo=None) >= time(9, 16)
    pre_open = (
        is_trading_day(now.date())
        and now.time().replace(tzinfo=None) < time(9, 15)
    )
    # Token floor applies pre-open (blocking) and throughout the cash session.
    token_gate_active = pre_open or market_open
    checks: list[dict[str, Any]] = []

    checks.append({
        "id": "version",
        "label": "Process version reported",
        "ok": bool(version),
        "detail": version or "missing",
    })

    token = (readiness.get("probes") or {}).get("token") or {}
    hours = token.get("hours_left")
    token_ok = bool(token.get("ok"))
    if not token and token_gate_active:
        # Older readiness mocks without token probe — treat as unknown fail when gated.
        token_ok = False
    checks.append({
        "id": "token_hours",
        "label": f"Dhan token ≥ {TOKEN_MIN_HOURS:.0f}h (pre-open / session)",
        "ok": (not token_gate_active) or token_ok,
        "detail": (
            "off_gate"
            if not token_gate_active
            else (token.get("reason") or (f"hours_left={hours}" if hours is not None else "token_unknown"))
        ),
        "skipped": not token_gate_active,
    })

    checks.append({
        "id": "readiness",
        "label": "Readiness ready (or off-session)",
        "ok": bool(readiness.get("ready")),
        "detail": (
            f"mode={readiness.get('degrade_mode') or '—'}; "
            + (",".join(readiness.get("reasons") or []) or "ready")
        ),
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

    from app.slo_monitor import probe_tape_completeness, probe_vwap_blackout
    vwap = probe_vwap_blackout()
    checks.append({
        "id": "vwap_refuse_burn",
        "label": "No sustained Missing session VWAP refuses",
        "ok": (not market_open) or bool(vwap.get("ok")),
        "detail": (
            "off_session" if not market_open
            else f"refuses={vwap.get('count')} / {vwap.get('window_min')}m"
        ),
        "skipped": not market_open,
    })
    tape = probe_tape_completeness(now=now, market_open=market_open)
    checks.append({
        "id": "tape_completeness",
        "label": "Sentinel 1m tape vs expected",
        "ok": bool(tape.get("ok")),
        "detail": tape.get("reason") or f"worst_frac={tape.get('worst_frac')}",
        "skipped": tape.get("reason") in ("too_early", "off_session") and tape.get("ok"),
    })

    nse = (readiness.get("probes") or {}).get("nse_fallback") or {}
    checks.append({
        "id": "nse_fallback",
        "label": "Book from Dhan (not NSE scrape)",
        "ok": (not market_open) or not bool(nse.get("active")),
        "detail": (
            "off_session" if not market_open
            else (
                nse.get("reason")
                or (
                    f"ok lifetime_nse_fallback={nse.get('lifetime_count', 0)}"
                    if not nse.get("active")
                    else "book_from_nse_scrape"
                )
            )
        ),
        "skipped": not market_open,
    })

    failed = [c for c in checks if not c.get("ok") and not c.get("skipped")]
    return {
        "as_of": now.isoformat(timespec="seconds"),
        "market_open": market_open,
        "pre_open": pre_open,
        "pass": len(failed) == 0,
        "failed": [c["id"] for c in failed],
        "checks": checks,
        "readiness": readiness,
        "ideas": ideas,
        "vwap": vwap,
        "tape": tape,
        "degrade_mode": readiness.get("degrade_mode"),
        "slo_doc": "docs/sli-slo.md",
    }
