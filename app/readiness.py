"""Deep readiness probes for /api/health — beyond liveness.

Liveness answers "is the process up?". Readiness answers "is the decision
screen safe to trade from right now?" during the F&O session:

- chain publish age per index (live_fetcher)
- spot quote age (spot_ticker)
- Dhan WS feed age (ws_spot_feed)
- circuit-breaker state per index
- LKGS degraded flags

Off-session, readiness is True (book comes from pickle/LKGS; Ideas stay closed).
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

# Soft thresholds — flag, do not invent data.
CHAIN_STALE_S = 120.0          # >2× the 60s ingest grid
SPOT_STALE_S = 45.0            # matches spot_ticker.STALE_AFTER_S
FEED_STALE_S = 30.0            # health daemon CRITICAL threshold
CHAIN_DEGRADED_S = 7200.0      # matches cockpit is_degraded wall
INGEST_HEARTBEAT_STALE_S = 90.0  # process-isolation design Phase C
TOKEN_MIN_HOURS = 2.0          # pre-open / session checklist floor

# Explicit degrade modes (Pillar D) — UI banner shows the enum, not free text only.
MODE_OK = "OK"
MODE_OFF_SESSION = "OFF_SESSION"
MODE_TOKEN_DEAD = "TOKEN_DEAD"
MODE_FEED_DOWN = "FEED_DOWN"
MODE_CHAIN_DEGRADED = "CHAIN_DEGRADED"
MODE_NOT_READY = "NOT_READY"


def _chain_probe(symbol: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "symbol": symbol,
        "has_snapshot": False,
        "age_s": None,
        "degraded": False,
        "ok": False,
        "reason": "",
    }
    try:
        from app import live_fetcher
        rec = live_fetcher.latest(symbol)
        if not rec or not rec.get("data"):
            out["reason"] = "no_published_chain"
            return out
        fetched = float(rec.get("fetched") or 0.0)
        age = (time.time() - fetched) if fetched > 0 else None
        out["has_snapshot"] = True
        out["age_s"] = round(age, 1) if age is not None else None
        data = rec["data"]
        degraded = bool(getattr(data, "is_degraded", False))
        out["degraded"] = degraded
        if age is not None and age > CHAIN_STALE_S:
            out["reason"] = f"chain_age_{int(age)}s"
            return out
        if degraded:
            out["reason"] = str(getattr(data, "degraded_reason", "") or "lkgs_degraded")
            # Still usable as degraded book — ok=False only when too old or missing.
            out["ok"] = age is None or age <= CHAIN_DEGRADED_S
            return out
        out["ok"] = True
        return out
    except Exception as exc:
        out["reason"] = f"chain_probe_error:{exc}"
        return out


def _spot_probe(symbol: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "symbol": symbol,
        "age_s": None,
        "spot": None,
        "ok": False,
        "reason": "",
    }
    try:
        from app.spot_ticker import _STORE, STALE_AFTER_S
        rec = _STORE.get(symbol)
        if not rec:
            out["reason"] = "no_spot_tick"
            return out
        age = time.time() - float(rec.get("at") or 0.0)
        out["age_s"] = round(age, 1)
        quote = rec.get("quote") or {}
        spot = float(quote.get("spot") or (rec.get("ohlc") or {}).get("ltp") or 0.0)
        out["spot"] = spot if spot > 0 else None
        if age > STALE_AFTER_S:
            out["reason"] = f"spot_age_{int(age)}s"
            return out
        if spot <= 0:
            out["reason"] = "spot_missing"
            return out
        out["ok"] = True
        return out
    except Exception as exc:
        out["reason"] = f"spot_probe_error:{exc}"
        return out


def _feed_probe() -> dict[str, Any]:
    try:
        from app.ws_spot_feed import get_status
        st = get_status()
        age = st.get("last_tick_age_s")
        ok = bool(st.get("is_live"))
        reason = ""
        if not st.get("ws_connected"):
            reason = "ws_disconnected"
        elif age is None:
            reason = "no_ticks"
        elif age > FEED_STALE_S:
            reason = f"feed_age_{int(age)}s"
            ok = False
        return {
            "ws_connected": bool(st.get("ws_connected")),
            "is_live": bool(st.get("is_live")),
            "last_tick_age_s": age,
            "ok": ok,
            "reason": reason if not ok else "",
            "ws_error": st.get("ws_error"),
        }
    except Exception as exc:
        return {
            "ws_connected": False,
            "is_live": False,
            "last_tick_age_s": None,
            "ok": False,
            "reason": f"feed_probe_error:{exc}",
            "ws_error": str(exc),
        }


def _circuit_probe(symbol: str) -> dict[str, Any]:
    try:
        from resilience import get_circuit_breaker
        cb = get_circuit_breaker(symbol)
        state = cb.state.value if hasattr(cb.state, "value") else str(cb.state)
        return {
            "symbol": symbol,
            "state": state,
            "consecutive_failures": int(cb.consecutive_failures),
            "cooldown_remaining": round(float(cb.cooldown_remaining), 1),
            "ok": state != "OPEN",
            "reason": "circuit_open" if state == "OPEN" else "",
        }
    except Exception as exc:
        return {
            "symbol": symbol,
            "state": "UNKNOWN",
            "consecutive_failures": 0,
            "cooldown_remaining": 0.0,
            "ok": False,
            "reason": f"cb_probe_error:{exc}",
        }


def _ingest_probe() -> dict[str, Any]:
    """When ingest is external, require a fresh heartbeat. Combined mode always ok if file fresh or absent-with-local-owner."""
    import os
    from app.ingest_heartbeat import probe as beat_probe

    external = os.environ.get("COCKPIT_INGEST_EXTERNAL", "").strip() == "1"
    beat = beat_probe(stale_after_s=INGEST_HEARTBEAT_STALE_S)
    beat["required"] = external
    if not external:
        # Combined process owns lanes; missing file is OK until writer starts.
        # If a file exists and is stale, still flag — something wrote then died.
        if beat.get("age_s") is None:
            beat["ok"] = True
            beat["reason"] = "combined_no_file_yet"
        return beat
    return beat


def _token_probe() -> dict[str, Any]:
    """Dhan token freshness. hours_left < TOKEN_MIN_HOURS → not ok (pre-open FAIL)."""
    out: dict[str, Any] = {
        "present": False,
        "hours_left": None,
        "expired": None,
        "ok": False,
        "reason": "token_missing",
        "min_hours": TOKEN_MIN_HOURS,
    }
    try:
        from data.dhan import token_status
        st = token_status()
        hours = st.get("hours_left")
        expired = st.get("expired")
        present = bool(st.get("present"))
        out["present"] = present
        out["hours_left"] = round(float(hours), 2) if hours is not None else None
        out["expired"] = expired
        out["source"] = st.get("source")
        if not present or expired is True:
            out["reason"] = "token_dead"
            return out
        if hours is None:
            out["reason"] = "token_unknown"
            return out
        if float(hours) < TOKEN_MIN_HOURS:
            out["reason"] = f"token_hours_{float(hours):.1f}"
            return out
        out["ok"] = True
        out["reason"] = ""
        return out
    except Exception as exc:
        out["reason"] = f"token_probe_error:{exc}"
        return out


def _nse_fallback_probe(symbols: list[str]) -> dict[str, Any]:
    """Surface active NSE scrape books + lifetime egress counter."""
    active: list[str] = []
    sources: dict[str, str] = {}
    try:
        from app import live_fetcher
        for sym in symbols:
            rec = live_fetcher.latest(sym)
            data = (rec or {}).get("data")
            src = str(getattr(data, "source", "") or "")
            sources[sym] = src
            if data is not None and "nse" in src.lower():
                active.append(sym)
    except Exception:
        pass
    count = 0
    try:
        from app.obs.metrics import egress_snapshot
        count = int((egress_snapshot() or {}).get("nse_fallback") or 0)
    except Exception:
        count = 0
    return {
        "active": bool(active),
        "symbols": active,
        "sources": sources,
        "lifetime_count": count,
        "ok": not bool(active),
        "reason": ("book_from_nse_scrape:" + ",".join(active)) if active else "",
    }


def _resolve_degrade_mode(
    *,
    market_open: bool,
    reasons: list[str],
    token: dict[str, Any],
    feed: dict[str, Any],
    chains: dict[str, Any],
    nse: dict[str, Any],
) -> str:
    if not market_open:
        return MODE_OFF_SESSION
    if not token.get("ok"):
        return MODE_TOKEN_DEAD
    if not feed.get("ok"):
        return MODE_FEED_DOWN
    any_degraded = any(c.get("degraded") for c in chains.values())
    if any_degraded or nse.get("active") or "no_fresh_chain" in reasons:
        return MODE_CHAIN_DEGRADED
    if reasons:
        return MODE_NOT_READY
    return MODE_OK


def build_readiness(*, market_open: bool | None = None) -> dict[str, Any]:
    """Assemble readiness payload. Safe to call on every /api/health hit."""
    import config

    if market_open is None:
        market_open = bool(config.is_market_open())

    symbols = ["NIFTY", "BANKNIFTY", "SENSEX"]
    chains = {s: _chain_probe(s) for s in symbols}
    spots = {s: _spot_probe(s) for s in symbols}
    feed = _feed_probe()
    circuits = {s: _circuit_probe(s) for s in symbols}
    ingest = _ingest_probe()
    token = _token_probe()
    nse = _nse_fallback_probe(symbols)
    try:
        from app.spot_ticker import spot_source_status
        spot_src = spot_source_status(tuple(symbols))
    except Exception as exc:
        spot_src = {"primary": "none", "per_symbol": {}, "error": str(exc)}

    thresholds = {
        "chain_stale_s": CHAIN_STALE_S,
        "spot_stale_s": SPOT_STALE_S,
        "feed_stale_s": FEED_STALE_S,
        "ingest_heartbeat_stale_s": INGEST_HEARTBEAT_STALE_S,
        "token_min_hours": TOKEN_MIN_HOURS,
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

    reasons: list[str] = []
    if not market_open:
        mode = _resolve_degrade_mode(
            market_open=False, reasons=[], token=token, feed=feed, chains=chains, nse=nse,
        )
        return {
            "ready": True,
            "market_open": False,
            "reasons": [],
            "degrade_mode": mode,
            "probes": probes,
            "thresholds": thresholds,
        }

    # Session open: at least one index must have a fresh chain + spot; feed should be live;
    # no OPEN circuit; external ingest must be heartbeating.
    any_chain_ok = any(c.get("ok") for c in chains.values())
    any_spot_ok = any(s.get("ok") for s in spots.values())
    if not any_chain_ok:
        reasons.append("no_fresh_chain")
    if not any_spot_ok:
        reasons.append("no_fresh_spot")
    if not feed.get("ok"):
        reasons.append(feed.get("reason") or "feed_not_live")
    open_cbs = [s for s, c in circuits.items() if not c.get("ok")]
    if open_cbs:
        reasons.append("circuit_open:" + ",".join(open_cbs))
    if ingest.get("required") and not ingest.get("ok"):
        reasons.append(ingest.get("reason") or "ingest_heartbeat_stale")
    if not token.get("ok"):
        reasons.append(token.get("reason") or "token_dead")
    if nse.get("active"):
        reasons.append(nse.get("reason") or "nse_fallback")

    mode = _resolve_degrade_mode(
        market_open=True, reasons=reasons, token=token, feed=feed, chains=chains, nse=nse,
    )
    return {
        "ready": len(reasons) == 0,
        "market_open": True,
        "reasons": reasons,
        "degrade_mode": mode,
        "probes": probes,
        "thresholds": thresholds,
    }
