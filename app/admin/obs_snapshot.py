"""Assemble Admin Observability payload slices (keeps routes thin)."""

from __future__ import annotations

import os
import resource
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.admin import db
from app.admin.clock import IST, as_ist, now_ist
from app.obs import metrics as obs_metrics

ROOT = Path(__file__).resolve().parents[2]

FAILURE_EVENTS = [
    "http.error", "http.slow", "lane.failed", "chain.failed", "chain.refused",
    "chain.persist_failed", "book.stale", "commentary.failed", "watchdog.restart",
    "blast_ticks.disconnected", "big_money.disconnected", "gex_daemon.failed",
    "gex_daemon.backoff", "timeseries.failed", "participant_oi.failed",
]

VOLUME_EVENTS = [
    "chain.published", "rec.generated", "rec.lifecycle", "rec.refused", "rec.withheld",
    "commentary.evaluated", "timeseries.appended", "timeseries.blast",
    "blast_ticks.count", "big_money.count", "gex_daemon.fetched",
    "dhan_sync.finished", "gamma_blast.appended", "target_snapshot.stored",
    "blast_ticks.connected", "blast_ticks.reconnected", "blast_ticks.disconnected",
    "blast_ticks.stopped", "big_money.connected", "big_money.reconnected",
    "big_money.disconnected", "big_money.stopped",
]

_JOURNAL_DIRS = ("timeseries", "gamma_blast", "blast_ticks", "gamma_blast_oi_tape")
_dir_cache: dict[str, tuple[float, float]] = {}
_DIR_TTL_S = 30.0


def window_since(window: str = "15m", now: datetime | None = None) -> tuple[str, str]:
    """Return (label, since_iso) for rollups."""
    now = as_ist(now or now_ist())
    label = (window or "15m").strip().lower()
    if label in ("session", "today"):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return "session", start.isoformat(timespec="seconds")
    minutes = 15
    if label.endswith("m"):
        try:
            minutes = max(1, int(label[:-1]))
        except ValueError:
            minutes = 15
    since = now - timedelta(minutes=minutes)
    return f"{minutes}m", since.isoformat(timespec="seconds")


def _wal_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return round(path.stat().st_size / (1024 * 1024), 2)


def _dir_mb(path: Path) -> float:
    key = str(path)
    now = time.time()
    cached = _dir_cache.get(key)
    if cached and now - cached[0] < _DIR_TTL_S:
        return cached[1]
    total = 0
    if path.is_dir():
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    mb = round(total / (1024 * 1024), 2)
    _dir_cache[key] = (now, mb)
    return mb


def _rss_mb() -> float:
    try:
        # ru_maxrss is bytes on macOS, kilobytes on Linux
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys_platform_is_linux():
            return round(usage / 1024.0, 1)
        return round(usage / (1024.0 * 1024.0), 1)
    except Exception:
        return 0.0


def sys_platform_is_linux() -> bool:
    return os.name == "posix" and "linux" in (os.uname().sysname.lower() if hasattr(os, "uname") else "")


def _token() -> dict:
    try:
        from data.dhan import token_status
        raw = token_status()
        return {
            "present": bool(raw.get("present")),
            "hours_left": raw.get("hours_left"),
            "source": raw.get("source"),
            "expired": bool(raw.get("expired")),
        }
    except Exception:
        return {"present": False, "hours_left": None, "source": None, "expired": True}


def _rate_gate() -> dict:
    try:
        from data.dhan_live import rate_report
        report = rate_report(60.0)
    except Exception:
        report = {}
    waits = obs_metrics.gate_wait_snapshot()
    out = {}
    for bucket, info in report.items():
        slot = dict(info)
        wait = waits.get(bucket) or {}
        slot["wait_p95_ms"] = wait.get("p95_ms", 0.0)
        slot["wait_max_ms"] = wait.get("max_ms", 0.0)
        out[bucket] = slot
    return out


def _spot_ws() -> dict:
    try:
        from app.ws_spot_feed import get_status
        return get_status()
    except Exception:
        return {"ws_connected": False, "ws_error": "unavailable", "last_tick_age_s": None, "is_live": False}


def _sync() -> dict:
    try:
        from app.dhan_sync import status
        return status()
    except Exception:
        return {"status": "unavailable"}


def _resilience_symbol(symbol: str) -> dict:
    from resilience import get_adaptive_poller, get_circuit_breaker, get_lkgs_manager

    cb = get_circuit_breaker(symbol)
    poller = get_adaptive_poller(symbol)
    lkgs = get_lkgs_manager()
    entry = lkgs.get_entry(symbol)
    data = entry.data if entry else None
    stale_s = 0.0
    degraded = bool(getattr(data, "is_degraded", False)) if data is not None else False
    reason = str(getattr(data, "degraded_reason", "") or "") if data is not None else ""
    if entry is not None:
        from datetime import timezone
        stored = entry.stored_at
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        stale_s = max(0.0, (datetime.now(timezone.utc) - stored).total_seconds())
    return {
        "circuit": {
            "state": cb.state.value if hasattr(cb.state, "value") else str(cb.state),
            "consecutive_failures": cb.consecutive_failures,
            "cooldown_remaining": round(float(cb.cooldown_remaining), 1),
        },
        "adaptive": {
            "rolling_rtt_ms": round(float(poller.rolling_rtt) * 1000.0, 1),
            "current_interval_s": round(float(poller.current_interval), 2),
        },
        "lkgs": {
            "has_snapshot": lkgs.has_snapshot(symbol),
            "is_degraded": degraded,
            "staleness_seconds": round(stale_s, 1),
            "degraded_reason": reason,
        },
    }


def _lanes(status_rows: list[dict]) -> list[dict]:
    lane_names = [
        "admin.monitor", "spot.warmup", "premarket", "dhan.sync", "rec.publisher",
        "blast.lane", "chain.NIFTY", "chain.BANKNIFTY", "chain.SENSEX", "chain.arm",
        "watchdog",
    ]
    by_name = {row["name"]: row for row in status_rows}
    out = []
    for name in lane_names:
        row = by_name.get(name) or {}
        out.append({
            "name": name,
            "last_ok_at": row.get("last_ok_at"),
            "last_error_at": row.get("last_error_at"),
            "ok_count": row.get("ok_count", 0),
            "error_count": row.get("error_count", 0),
            "last_duration_ms": row.get("last_duration_ms"),
        })
    return out


def _chain_threads() -> dict[str, bool]:
    try:
        from app import live_fetcher
        alive = {}
        for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
            # live_fetcher keeps threads in module private map; probe via ensure idempotency
            threads = getattr(live_fetcher, "_THREADS", {})
            found = False
            for key, thr in list(threads.items()):
                sym_key = key[0] if isinstance(key, tuple) else str(key)
                if str(sym_key).upper() == sym and thr is not None and thr.is_alive():
                    found = True
                    break
            alive[sym] = found
        return alive
    except Exception:
        return {"NIFTY": False, "BANKNIFTY": False, "SENSEX": False}


def build_ops_payload(
    *,
    window: str = "15m",
    process: str | None = None,
    event: str | None = None,
    level: str | None = None,
    limit: int = 80,
) -> dict:
    label, since_iso = window_since(window)
    http = obs_metrics.snapshot(window_s=300.0 if label != "session" else 6 * 3600.0)
    status_rows = db.list_ops_status(process=process)
    heartbeats = [row for row in status_rows if row["name"] == "heartbeat"]
    workflows = [row for row in status_rows if row["name"] != "heartbeat"]
    events_tail = db.list_ops_events(process=process, event=event, level=level, limit=limit)

    failure_names = list(FAILURE_EVENTS)
    volume_names = list(VOLUME_EVENTS)
    counts = db.count_events_by_name(since_iso, failure_names + volume_names)
    # ok=false subsets approximated by total for finished events that carry both
    failures = {name: counts.get(name, 0) for name in failure_names}
    volume = {name: counts.get(name, 0) for name in volume_names}

    chain = db.summarize_chain_latency(since_iso)
    last_ok = db.latest_workflow_ok("chain.")
    for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
        slot = chain.setdefault(sym, {
            "n": 0, "avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0,
            "network_avg_ms": 0.0, "compute_avg_ms": 0.0,
        })
        slot["last_ok_at"] = last_ok.get(f"chain.{sym}")

    journal_root = ROOT / "data" / "journal"
    journal_mb = {name: _dir_mb(journal_root / name) for name in _JOURNAL_DIRS}

    try:
        from app.event_loop_watchdog import is_armed, snapshot as wd_snap
        wd = wd_snap()
        armed = is_armed()
    except Exception:
        wd = {"armed": False, "lag_ms": 0.0}
        armed = False

    cb_open = 0
    resilience = {}
    for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
        try:
            resilience[sym] = _resilience_symbol(sym)
            if resilience[sym]["circuit"]["state"] == "OPEN":
                cb_open += 1
        except Exception:
            resilience[sym] = {
                "circuit": {"state": "UNKNOWN", "consecutive_failures": 0, "cooldown_remaining": 0.0},
                "adaptive": {"rolling_rtt_ms": 0.0, "current_interval_s": 0.0},
                "lkgs": {"has_snapshot": False, "is_degraded": False, "staleness_seconds": 0.0, "degraded_reason": ""},
            }

    egress = obs_metrics.egress_snapshot()
    http_journal = db.summarize_http_journal(since_iso)

    return {
        "window": label,
        "since": since_iso,
        "http": http,
        "http_journal": http_journal,
        "external": {
            "token": _token(),
            "rate_gate": _rate_gate(),
            "spot_ws": _spot_ws(),
            "sync": _sync(),
            "egress_misses": egress,
        },
        "infra": {
            "rss_mb": _rss_mb(),
            "threads": threading.active_count(),
            "wal_mb": {
                "sentinel": _wal_mb(ROOT / "data" / "sentinel.db-wal"),
                "market_snapshot": _wal_mb(journal_root / "market_snapshot.db-wal"),
                "admin_ops": _wal_mb(db.db_path().with_name(db.db_path().name + "-wal")),
            },
            "journal_mb": journal_mb,
            "loop_lag_p50_ms": http.get("loop_lag_p50_ms", 0.0),
            "loop_lag_p95_ms": http.get("loop_lag_p95_ms", 0.0),
            "loop_lag_max_ms": http.get("loop_lag_max_ms", 0.0),
            "watchdog_armed": armed,
            "watchdog_lag_ms": wd.get("lag_ms", 0.0),
            "watchdog_restarts": failures.get("watchdog.restart", 0),
            "lanes": _lanes(status_rows),
            "http_in_flight": http.get("in_flight", 0),
            "chain_threads": _chain_threads(),
            "cb_open": cb_open,
        },
        "resilience": resilience,
        "events_summary": {"failures": failures, "volume": volume},
        "chain": chain,
        "heartbeats": heartbeats,
        "workflows": workflows,
        "events": events_tail,
    }
