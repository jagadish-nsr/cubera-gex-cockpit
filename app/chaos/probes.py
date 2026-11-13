"""Synthetic readiness + checklist for chaos drills (no live DBs, no invent)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.chaos.state import get_faults


SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX")
CHAOS_VERSION = "chaos-8660"


def build_chaos_readiness(*, market_open: bool | None = None) -> dict[str, Any]:
    f = get_faults()
    if market_open is None:
        market_open = bool(f.get("force_market_open"))

    chains = {}
    spots = {}
    circuits = {}
    for sym in SYMBOLS:
        if f.get("empty_chain"):
            chains[sym] = {
                "symbol": sym, "has_snapshot": False, "age_s": None,
                "degraded": False, "ok": False, "reason": "no_published_chain",
            }
        else:
            chains[sym] = {
                "symbol": sym, "has_snapshot": True, "age_s": 5.0,
                "degraded": False, "ok": True, "reason": "",
            }
        if f.get("stale_spot"):
            spots[sym] = {
                "symbol": sym, "age_s": 120.0, "spot": 24500.0,
                "ok": False, "reason": "spot_age_120s",
            }
        else:
            spots[sym] = {
                "symbol": sym, "age_s": 2.0, "spot": 24500.0,
                "ok": True, "reason": "",
            }
        if f.get("cb_open") and sym == "NIFTY":
            circuits[sym] = {
                "symbol": sym, "state": "OPEN", "consecutive_failures": 5,
                "cooldown_remaining": 30.0, "ok": False, "reason": "circuit_open",
            }
        else:
            circuits[sym] = {
                "symbol": sym, "state": "CLOSED", "consecutive_failures": 0,
                "cooldown_remaining": 0.0, "ok": True, "reason": "",
            }

    if f.get("feed_silence"):
        feed = {
            "ws_connected": False, "is_live": False, "last_tick_age_s": 90.0,
            "ok": False, "reason": "ws_disconnected", "ws_error": "chaos_feed_silence",
        }
    else:
        feed = {
            "ws_connected": True, "is_live": True, "last_tick_age_s": 1.0,
            "ok": True, "reason": "", "ws_error": None,
        }

    token = {
        "present": True, "hours_left": 8.0, "expired": False,
        "ok": True, "reason": "", "min_hours": 2.0, "source": "chaos",
    }
    nse = {
        "active": False, "symbols": [], "sources": {s: "chaos-fixture" for s in SYMBOLS},
        "lifetime_count": 0, "ok": True, "reason": "",
    }
    ingest = {
        "ok": True, "age_s": 1.0, "pid": 0, "role": "chaos",
        "reason": "", "required": False,
    }
    spot_src = {
        "primary": "none" if f.get("feed_silence") else "ws",
        "per_symbol": {s: {"source": "none" if f.get("feed_silence") else "ws",
                           "age_s": None if f.get("feed_silence") else 1.0,
                           "ws_age_s": None if f.get("feed_silence") else 1.0}
                       for s in SYMBOLS},
    }

    probes = {
        "chains": chains,
        "spots": spots,
        "feed": feed,
        "circuits": circuits,
        "ingest": ingest,
        "token": token,
        "nse_fallback": nse,
        "spot_source": spot_src,
    }

    if not market_open:
        return {
            "ready": True,
            "market_open": False,
            "reasons": [],
            "degrade_mode": "OFF_SESSION",
            "probes": probes,
            "thresholds": {},
            "chaos": True,
        }

    reasons: list[str] = []
    if f.get("empty_chain") or not any(c.get("ok") for c in chains.values()):
        reasons.append("no_fresh_chain")
    if f.get("stale_spot") or not any(s.get("ok") for s in spots.values()):
        reasons.append("no_fresh_spot")
    if f.get("feed_silence") or not feed.get("ok"):
        reasons.append(feed.get("reason") or "feed_not_live")
    open_cbs = [s for s, c in circuits.items() if not c.get("ok")]
    if open_cbs:
        reasons.append("circuit_open:" + ",".join(open_cbs))

    if f.get("empty_chain"):
        mode = "CHAIN_DEGRADED"
    elif f.get("feed_silence"):
        mode = "FEED_DOWN"
    elif reasons:
        mode = "NOT_READY"
    else:
        mode = "OK"

    return {
        "ready": len(reasons) == 0,
        "market_open": True,
        "reasons": reasons,
        "degrade_mode": mode,
        "probes": probes,
        "thresholds": {},
        "chaos": True,
    }


def build_chaos_checklist(
    *,
    now: datetime | None = None,
    version: str | None = CHAOS_VERSION,
) -> dict[str, Any]:
    """Mirror session-checklist FAIL semantics against injected readiness."""
    f = get_faults()
    market_open = bool(f.get("force_market_open"))
    readiness = build_chaos_readiness(market_open=market_open)
    now_s = (now or datetime.now()).isoformat(timespec="seconds")

    checks: list[dict[str, Any]] = []
    checks.append({
        "id": "version", "label": "Process version reported",
        "ok": bool(version), "detail": version or "missing",
    })
    checks.append({
        "id": "readiness", "label": "Readiness ready (or off-session)",
        "ok": bool(readiness.get("ready")),
        "detail": f"mode={readiness.get('degrade_mode')}; "
                  + (",".join(readiness.get("reasons") or []) or "ready"),
    })
    chains = (readiness.get("probes") or {}).get("chains") or {}
    any_chain = any(c.get("ok") for c in chains.values())
    checks.append({
        "id": "chain", "label": "At least one fresh chain publish",
        "ok": (not market_open) or any_chain,
        "detail": "off_session" if not market_open else ("ok" if any_chain else "no_fresh_chain"),
    })
    spots = (readiness.get("probes") or {}).get("spots") or {}
    any_spot = any(s.get("ok") for s in spots.values())
    checks.append({
        "id": "spot", "label": "At least one fresh spot quote",
        "ok": (not market_open) or any_spot,
        "detail": "off_session" if not market_open else ("ok" if any_spot else "no_fresh_spot"),
    })
    feed = (readiness.get("probes") or {}).get("feed") or {}
    checks.append({
        "id": "feed", "label": "Spot WS feed live in session",
        "ok": (not market_open) or bool(feed.get("ok")),
        "detail": feed.get("reason") or ("live" if feed.get("ok") else "not_live"),
    })
    circuits = (readiness.get("probes") or {}).get("circuits") or {}
    open_cbs = [s for s, c in circuits.items() if not c.get("ok")]
    checks.append({
        "id": "circuits", "label": "No OPEN circuit breakers",
        "ok": len(open_cbs) == 0,
        "detail": ",".join(open_cbs) if open_cbs else "all_closed",
    })

    vwap_ok = not (market_open and f.get("missing_vwap"))
    checks.append({
        "id": "ideas_vwap",
        "label": "Ideas after 09:16 — not VWAP-blackout",
        "ok": vwap_ok,
        "detail": "vwap_blackout" if not vwap_ok else ("off_session" if not market_open else "ok"),
        "skipped": not market_open,
    })

    failed = [c["id"] for c in checks if not c.get("ok") and not c.get("skipped")]
    return {
        "as_of": now_s,
        "market_open": market_open,
        "pass": len(failed) == 0,
        "failed": failed,
        "checks": checks,
        "readiness": readiness,
        "degrade_mode": readiness.get("degrade_mode"),
        "chaos": True,
        "faults": f,
        "slo_doc": "docs/chaos-drill.md",
    }
