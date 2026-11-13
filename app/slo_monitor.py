"""SLO enforcement helpers — minute readiness rollups and burn detectors.

Called once per health-daemon minute. Does not invent market data; it only
records readiness probes and raises Admin alerts when burns persist.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, time, timedelta
from typing import Any

log = logging.getLogger("cubera.slo")

# Consecutive not-ready minutes before CRITICAL (session open only).
NOT_READY_STREAK_CRIT = 3
# VWAP blackout: rec.refused with Missing session VWAP in this window.
VWAP_WINDOW_MIN = 10
VWAP_REFUSE_FLOOR = 20  # sustained refuses after first bar → blackout class
# Tape: after close, bars below this fraction of expected → CRITICAL.
TAPE_COMPLETE_FRAC = 0.85
# During session, bars vs elapsed below this → WARNING.
TAPE_LIVE_FRAC = 0.80

_lock = threading.Lock()
_not_ready_streak = 0
_last_rollup_minute: str | None = None


def reset_for_tests() -> None:
    global _not_ready_streak, _last_rollup_minute
    with _lock:
        _not_ready_streak = 0
        _last_rollup_minute = None


def not_ready_streak() -> int:
    with _lock:
        return _not_ready_streak


def record_readiness_minute(
    readiness: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Write one ``slo.readiness.minute`` ops event per IST minute. Returns streak info."""
    global _not_ready_streak, _last_rollup_minute
    from app.obs import event

    minute_key = now.strftime("%Y-%m-%dT%H:%M")
    ready = bool(readiness.get("ready"))
    market_open = bool(readiness.get("market_open"))
    reasons = list(readiness.get("reasons") or [])

    with _lock:
        if _last_rollup_minute == minute_key:
            return {
                "recorded": False,
                "streak": _not_ready_streak,
                "ready": ready,
                "market_open": market_open,
            }
        _last_rollup_minute = minute_key
        if market_open and not ready:
            _not_ready_streak += 1
        else:
            _not_ready_streak = 0
        streak = _not_ready_streak

    probes = readiness.get("probes") or {}
    feed = probes.get("feed") or {}
    ingest = probes.get("ingest") or {}
    chains = probes.get("chains") or {}
    chain_ages = {
        sym: (row or {}).get("age_s")
        for sym, row in chains.items()
    }
    open_cbs = [
        sym for sym, row in (probes.get("circuits") or {}).items()
        if not (row or {}).get("ok")
    ]

    event(
        "slo.readiness.minute",
        workflow="slo",
        ok=ready,
        level="INFO" if ready or not market_open else "WARNING",
        message="ready" if ready else ("off_session" if not market_open else "not_ready"),
        market_open=market_open,
        ready=ready,
        reasons=",".join(reasons)[:200],
        streak=streak,
        feed_age_s=feed.get("last_tick_age_s"),
        ingest_age_s=ingest.get("age_s"),
        chain_ages=str(chain_ages)[:200],
        open_circuits=",".join(open_cbs),
    )
    return {
        "recorded": True,
        "streak": streak,
        "ready": ready,
        "market_open": market_open,
        "reasons": reasons,
    }


def probe_vwap_blackout(*, since_minutes: int = VWAP_WINDOW_MIN) -> dict[str, Any]:
    """Count recent rec.refused events whose reason mentions Missing session VWAP."""
    import json
    from app.admin import db
    from app.admin.clock import now_ist

    since = (now_ist() - timedelta(minutes=since_minutes)).isoformat(timespec="seconds")
    try:
        rows = db.list_ops_events(event="rec.refused", limit=400)
    except Exception as exc:
        return {"ok": True, "count": 0, "reason": f"probe_error:{exc}", "blackout": False}

    n = 0
    for row in rows:
        ts = str(row.get("ts") or "")
        if ts < since:
            continue
        parts = [str(row.get("message") or "")]
        raw = row.get("fields_json") or ""
        parts.append(str(raw))
        if raw:
            try:
                fields = json.loads(raw)
                parts.append(str(fields.get("reason") or ""))
                parts.append(str(fields.get("message") or ""))
            except Exception:
                pass
        blob = " ".join(parts)
        if "Missing session VWAP" in blob:
            n += 1

    blackout = n >= VWAP_REFUSE_FLOOR
    return {
        "ok": not blackout,
        "count": n,
        "window_min": since_minutes,
        "floor": VWAP_REFUSE_FLOOR,
        "blackout": blackout,
        "reason": "vwap_blackout" if blackout else "",
    }


def _count_session_1m_bars(symbol: str) -> int:
    try:
        from sentinel.storage.db import list_recent_bars
        bars = list_recent_bars(
            f"{symbol}-FUT",
            timeframe="1m",
            limit=500,
            session_only=True,
            _auto_populate=False,
        )
        return len(bars or [])
    except Exception:
        return 0


def _elapsed_expected_bars(now: datetime) -> int:
    """Minutes from 09:15 to min(now, 15:30) on a trading session clock."""
    t = now.timetz().replace(tzinfo=None) if hasattr(now, "timetz") else now.time()
    if hasattr(t, "tzinfo") and t.tzinfo is not None:
        t = t.replace(tzinfo=None)
    start = time(9, 15)
    end = time(15, 30)
    if t < start:
        return 0
    cutoff = min(t, end)
    return max(0, (cutoff.hour * 60 + cutoff.minute) - (start.hour * 60 + start.minute) + 1)


def probe_tape_completeness(*, now: datetime, market_open: bool) -> dict[str, Any]:
    """Per-index 1m bar counts vs expected elapsed / full-day grid."""
    symbols = ("NIFTY", "BANKNIFTY", "SENSEX")
    expected_live = _elapsed_expected_bars(now)
    # Full cash continuous grid ~09:15–15:30 inclusive ≈ 376; session evals ~380–384 with FNO.
    expected_full = 376
    closed = (not market_open) and now.time().replace(tzinfo=None) >= time(15, 45)

    per: dict[str, Any] = {}
    worst_frac = 1.0
    for sym in symbols:
        bars = _count_session_1m_bars(sym)
        exp = expected_full if closed else max(1, expected_live)
        frac = (bars / exp) if exp else 1.0
        worst_frac = min(worst_frac, frac)
        per[sym] = {
            "bars": bars,
            "expected": exp,
            "frac": round(frac, 3),
        }

    if closed:
        ok = worst_frac >= TAPE_COMPLETE_FRAC
        severity = "CRITICAL" if not ok else ""
        reason = "" if ok else "tape_incomplete"
    elif market_open and expected_live >= 30:
        ok = worst_frac >= TAPE_LIVE_FRAC
        severity = "WARNING" if not ok else ""
        reason = "" if ok else "tape_lagging"
    else:
        ok = True
        severity = ""
        reason = "too_early" if market_open else "off_session"

    return {
        "ok": ok,
        "closed": closed,
        "severity": severity,
        "reason": reason,
        "worst_frac": round(worst_frac, 3),
        "per_symbol": per,
        "expected_live": expected_live,
    }


def run_session_open_smoke(*, version: str | None = None) -> dict[str, Any]:
    """Post-open smoke: health readiness schema + checklist + Ideas VWAP gate."""
    import config
    from app.admin.clock import now_ist
    from app.readiness import build_readiness
    from app.session_checklist import build_session_checklist

    now = now_ist()
    market_open = bool(config.is_market_open(now))
    readiness = build_readiness(market_open=market_open)
    checklist = build_session_checklist(now=now, version=version)
    ideas = checklist.get("ideas") or {}
    vwap = probe_vwap_blackout()
    tape = probe_tape_completeness(now=now, market_open=market_open)

    checks = [
        {
            "id": "health_schema",
            "ok": "probes" in readiness and "ingest" in (readiness.get("probes") or {}),
            "detail": "readiness.probes.ingest",
        },
        {
            "id": "checklist_pass",
            "ok": bool(checklist.get("pass")),
            "detail": ",".join(checklist.get("failed") or []) or "pass",
        },
        {
            "id": "vwap_blackout",
            "ok": not vwap.get("blackout"),
            "detail": f"refuses={vwap.get('count')}",
        },
        {
            "id": "tape",
            "ok": bool(tape.get("ok")),
            "detail": tape.get("reason") or f"worst_frac={tape.get('worst_frac')}",
        },
    ]
    if version:
        checks.insert(0, {
            "id": "version",
            "ok": bool(version),
            "detail": version,
        })

    failed = [c["id"] for c in checks if not c["ok"]]
    return {
        "pass": len(failed) == 0,
        "failed": failed,
        "checks": checks,
        "readiness_ready": readiness.get("ready"),
        "market_open": market_open,
        "ideas": {"count": ideas.get("count"), "reason": ideas.get("reason")},
        "vwap": vwap,
        "tape": tape,
    }
