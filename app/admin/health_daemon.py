"""Deduplicated token, feed, WAL, gap, and stale-process alerts.

The loop reads ``token_status()`` and ``app.ws_spot_feed.get_status()``. It does
not open a second WebSocket. When ``sentinel.db-wal`` exceeds 25 MB it runs a
PASSIVE ``wal_checkpoint`` (non-blocking if writers are active) and alerts if
the file stays large. After 15:45 IST on a trading day it attempts one
TRUNCATE checkpoint so the WAL does not carry overnight. A closed session with
a gap is rechecked until the journals are complete. A clean session is not
scanned again until the process restarts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

from data.holidays import is_trading_day

from app.admin import db
from app.admin.audit_engine import audit_range
from app.admin.clock import IST, as_ist, is_regular_session, now_ist

log = logging.getLogger("cubera.admin.health")

ROOT = Path(__file__).resolve().parents[2]
WAL_WARN_BYTES = 25 * 1024 * 1024
PROCESS_SILENCE = timedelta(minutes=3)
EVENT_KEEP = timedelta(days=7)
OBS_ERROR_RPM_WARN = 5
OBS_CHAIN_P95_MS = 30_000.0
OBS_CHAIN_SILENT_MULT = 3.0
OBS_CHAIN_INTERVAL_S = 30.0
OBS_LOOP_LAG_P95_MS = 5_000.0
OBS_GATE_HEADROOM_WARN = 0.05
_gap_checked: set[str] = set()
_gap_count = 0
_gap_lock = threading.Lock()
_wal_truncate_done: set[str] = set()
_wal_truncate_lock = threading.Lock()
_rvol_refit_done: set[str] = set()
_rvol_refit_lock = threading.Lock()
_task: asyncio.Task | None = None
_wal_thread: threading.Thread | None = None
_wal_thread_stop = threading.Event()


def owns_sentinel_wal() -> bool:
    """True when this process may PASSIVE/TRUNCATE sentinel.db WAL (Pillar A5).

    Dual mode: ingest owns checkpoints. EXTERNAL serve only observes size.
    Combined / ingest role: own WAL. Chaos never checkpoints live DBs.
    """
    if os.environ.get("COCKPIT_INGEST_EXTERNAL", "").strip() == "1":
        return False
    role = os.environ.get("COCKPIT_ROLE", "").strip().lower()
    if role in ("serve", "chaos"):
        return False
    return True


def gap_count() -> int:
    with _gap_lock:
        return _gap_count


def reset_gap_cache() -> None:
    global _gap_count
    with _gap_lock:
        _gap_checked.clear()
        _gap_count = 0
    with _wal_truncate_lock:
        _wal_truncate_done.clear()
    with _rvol_refit_lock:
        _rvol_refit_done.clear()


def _iso(now: datetime) -> str:
    return as_ist(now).isoformat(timespec="seconds")


def _alert_id() -> str:
    return "alert_" + uuid.uuid4().hex[:12]


def _previous_trading_day(day):
    cursor = day - timedelta(days=1)
    for _ in range(14):
        if is_trading_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return None


def evaluate_alerts(
    root: Path | None = None,
    now: datetime | None = None,
    token_fn=None,
    feed_fn=None,
    include_gaps: bool = True,
    include_slo: bool = True,
) -> dict:
    """Upsert or resolve alerts for the current conditions. Safe to call every minute."""
    now = as_ist(now or now_ist())
    root = Path(root) if root is not None else ROOT
    stamp = _iso(now)
    if token_fn is None:
        from data.dhan import token_status as token_fn
    if feed_fn is None:
        from app.ws_spot_feed import get_status as feed_fn

    _token_alert(token_fn(), stamp)
    _feed_alert(feed_fn(), now, stamp)
    _wal_alert(root, stamp)
    if owns_sentinel_wal():
        _wal_truncate_post_close(root, now, stamp)
        _rvol_baseline_post_close(root, now)
    if include_gaps:
        _gap_alert(root, now, stamp)
        _reclassify_backfill_only_gap_alerts(stamp)
    _process_alert(now, stamp)
    _obs_perf_alerts(now, stamp)
    if include_slo:
        _slo_alerts(now, stamp)
    _prune_events(now)
    return {"badge_count": db.badge_count()}


def _token_alert(status: dict, stamp: str) -> None:
    present = bool(status.get("present"))
    expired = bool(status.get("expired"))
    hours = status.get("hours_left")
    fp = "TOKEN|expiry"
    if (not present) or expired or (hours is not None and hours < 2):
        why = "missing" if not present else ("expired" if expired else f"{hours:.1f}h left")
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=fp,
            severity="CRITICAL",
            category="TOKEN",
            title="Dhan token is expired or inside 2 hours",
            description=f"Token status: {why}. Source: {status.get('source') or 'unknown'}.",
            session_date=None,
            now_iso=stamp,
        )
        return
    if hours is not None and hours < 6:
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=fp,
            severity="WARNING",
            category="TOKEN",
            title="Dhan token expires inside 6 hours",
            description=f"Token has {hours:.1f}h left. Source: {status.get('source') or 'unknown'}.",
            session_date=None,
            now_iso=stamp,
        )
        return
    db.resolve_alert(fp, stamp)


def _feed_alert(status: dict, now: datetime, stamp: str) -> None:
    session_date = now.date().isoformat()
    silence_fp = "FEED|silence"
    age = status.get("last_tick_age_s")
    started = bool(status.get("ws_connected")) or age is not None
    if is_regular_session(now) and started and age is not None and age > 30:
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=silence_fp,
            severity="CRITICAL",
            category="FEED",
            title="Spot feed silent during the session",
            description=f"Last tick age is {age}s. Threshold is 30s during 09:15–15:30 IST.",
            session_date=session_date,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(silence_fp, stamp)
        for row in db.list_alerts():
            old = str(row.get("fingerprint") or "")
            if old.startswith("FEED|silence|"):
                db.resolve_alert(old, stamp)

    idle_fp = "FEED|not-started"
    if not status.get("ws_connected") and age is None:
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=idle_fp,
            severity="INFO",
            category="FEED",
            title="Spot feed is not running",
            description="No WebSocket has connected in this process.",
            session_date=None,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(idle_fp, stamp)


def _checkpoint_sentinel_wal(db_path: Path) -> str:
    """PASSIVE checkpoint — returns a short result note. Never raises to caller."""
    try:
        import sqlite3
        con = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            con.execute("PRAGMA busy_timeout=5000")
            row = con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            # row: (busy, log, checkpointed)
            return f"passive busy={row[0]} log={row[1]} ckpt={row[2]}"
        finally:
            con.close()
    except Exception as exc:
        log.warning("sentinel WAL checkpoint failed: %s", exc)
        return f"checkpoint_failed:{exc}"


def _wal_truncate_post_close(root: Path, now: datetime, stamp: str) -> None:
    """Once per trading day after 15:45 IST, TRUNCATE sentinel WAL if present.

    Skipped while the regular session is open so we never block the open print.
    Busy writers leave busy!=0; we retry next minute until success or process exit.
    """
    now = as_ist(now)
    if not is_trading_day(now.date()):
        return
    if now.time().replace(tzinfo=None) < time(15, 45):
        return
    key = now.date().isoformat()
    with _wal_truncate_lock:
        if key in _wal_truncate_done:
            return
    db_path = root / "data" / "sentinel.db"
    if not db_path.exists():
        with _wal_truncate_lock:
            _wal_truncate_done.add(key)
        return
    wal_path = root / "data" / "sentinel.db-wal"
    if not wal_path.exists() or wal_path.stat().st_size == 0:
        with _wal_truncate_lock:
            _wal_truncate_done.add(key)
        return
    try:
        from app.durability import wal_checkpoint

        busy, log_frames, ckpt = wal_checkpoint(db_path, mode="TRUNCATE")
        if busy:
            log.info(
                "sentinel WAL TRUNCATE deferred (busy=%s log=%s ckpt=%s)",
                busy,
                log_frames,
                ckpt,
            )
            return
        with _wal_truncate_lock:
            _wal_truncate_done.add(key)
        log.info(
            "sentinel WAL TRUNCATE ok after close (%s): busy=%s log=%s ckpt=%s",
            key,
            busy,
            log_frames,
            ckpt,
        )
        db.resolve_alert("DB|sentinel-wal", stamp)
    except Exception as exc:
        log.warning("sentinel WAL TRUNCATE failed: %s", exc)


def _rvol_baseline_post_close(root: Path, now: datetime) -> None:
    """Once per trading day after 15:45 IST, refit TOD RVol U-curves from sentinel bars.

    Rejected fits leave the prior ``data/journal/rvol_baseline.json`` untouched;
    ``compute_tod_rvol`` keeps frozen curves until a validated write lands.
    CONT / ABS / CVD gates are never modified here.
    """
    now = as_ist(now)
    if not is_trading_day(now.date()):
        return
    if now.time().replace(tzinfo=None) < time(15, 45):
        return
    key = now.date().isoformat()
    with _rvol_refit_lock:
        if key in _rvol_refit_done:
            return
    db_path = root / "data" / "sentinel.db"
    profile_path = root / "data" / "journal" / "rvol_baseline.json"
    if not db_path.exists():
        with _rvol_refit_lock:
            _rvol_refit_done.add(key)
        return
    try:
        from sentinel.engine.rvol_fit import refit_and_persist

        report = refit_and_persist(db_path, profile_path, as_of=now.date())
        with _rvol_refit_lock:
            _rvol_refit_done.add(key)
        if report.ok:
            log.info(
                "rvol baseline post-close refit ok (%s) → %s prevalence=%s",
                key,
                report.path,
                {k: round(v, 3) for k, v in report.prevalence.items()},
            )
        else:
            log.warning(
                "rvol baseline post-close refit refused (%s): %s",
                key,
                report.reason,
            )
    except Exception as exc:
        log.warning("rvol baseline post-close refit failed: %s", exc)
        with _rvol_refit_lock:
            _rvol_refit_done.add(key)


def _wal_alert(root: Path, stamp: str) -> None:
    path = root / "data" / "sentinel.db-wal"
    db_path = root / "data" / "sentinel.db"
    fp = "DB|sentinel-wal"
    size = path.stat().st_size if path.exists() else 0
    if size > WAL_WARN_BYTES:
        mb = size / (1024 * 1024)
        note = ""
        if db_path.exists() and owns_sentinel_wal():
            note = _checkpoint_sentinel_wal(db_path)
            size_after = path.stat().st_size if path.exists() else 0
            mb_after = size_after / (1024 * 1024)
            if size_after <= WAL_WARN_BYTES:
                log.info("sentinel WAL checkpointed: %.1f MB → %.1f MB (%s)", mb, mb_after, note)
                db.resolve_alert(fp, stamp)
                return
            note = f"{note}; still {mb_after:.1f} MB after PASSIVE"
        elif not owns_sentinel_wal():
            note = "EXTERNAL serve observes only — ingest owns WAL checkpoint"
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=fp,
            severity="WARNING",
            category="DB",
            title="sentinel.db WAL is over 25 MB",
            description=f"sentinel.db-wal is {mb:.1f} MB. {note}".strip(),
            session_date=None,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(fp, stamp)


def run_wal_maintenance(root: Path | None = None, now: datetime | None = None) -> None:
    """PASSIVE size check + post-close TRUNCATE + RVol baseline refit. Safe for ingest (A5)."""
    if not owns_sentinel_wal():
        return
    now = as_ist(now or now_ist())
    root = Path(root) if root is not None else ROOT
    stamp = _iso(now)
    _wal_alert(root, stamp)
    _wal_truncate_post_close(root, now, stamp)
    _rvol_baseline_post_close(root, now)


def start_wal_maintenance_thread(*, interval_s: float = 60.0) -> None:
    """Daemon thread for ingest (no asyncio loop). No-op if this role is read-only."""
    global _wal_thread
    if not owns_sentinel_wal():
        log.info("WAL maintenance thread skipped — this role is Sentinel read-only")
        return
    if _wal_thread is not None and _wal_thread.is_alive():
        return
    _wal_thread_stop.clear()

    def _loop() -> None:
        log.info("Sentinel WAL maintenance thread started (interval=%.0fs)", interval_s)
        while not _wal_thread_stop.wait(interval_s):
            try:
                run_wal_maintenance()
            except Exception:
                log.exception("ingest WAL maintenance")

    _wal_thread = threading.Thread(
        target=_loop, name="sentinel-wal-maintenance", daemon=True
    )
    _wal_thread.start()


def _is_backfill_only_gap_description(description: str) -> bool:
    """True when every clause is futures/options MISSING (no sentinel / PARTIAL)."""
    parts = [p.strip() for p in (description or "").split(";") if p.strip()]
    if not parts:
        return False
    for part in parts:
        low = part.lower()
        if "sentinel" in low:
            return False
        if "partial" in low:
            return False
        if not (low.endswith("futures missing") or low.endswith("options missing")):
            return False
    return True


def _reclassify_backfill_only_gap_alerts(stamp: str) -> None:
    """Convert legacy GAP WARNINGs that were only missing Dhan parquet into REPAIR INFO.

    Pre-v5.3.135 every session without backfill fired GAP. Those rows stay open
    until reclassified; do not invent new session holes.
    """
    try:
        rows = db.list_alerts(unread_only=False)
    except Exception:
        log.exception("reclassify gap alerts: list failed")
        return
    for row in rows or []:
        fp = str(row.get("fingerprint") or "")
        if not fp.startswith("GAP|"):
            continue
        if row.get("resolved_at"):
            continue
        desc = str(row.get("description") or "")
        if not _is_backfill_only_gap_description(desc):
            continue
        session_date = str(row.get("session_date") or fp.split("|", 1)[-1])
        repair_fp = f"REPAIR|{session_date}"
        db.resolve_alert(fp, stamp)
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=repair_fp,
            severity="INFO",
            category="REPAIR",
            title=f"Session {session_date} Dhan backfill not on disk",
            description=desc,
            session_date=session_date,
            now_iso=stamp,
        )


def _gap_alert(root: Path, now: datetime, stamp: str) -> None:
    """Post-15:45 session completeness vs Dhan backfill backlog.

    **GAP (WARNING):** live desk tape holes — Sentinel MISSING/PARTIAL, or
    futures/options parquet that exist but are PARTIAL (corrupt/unreadable).

    **REPAIR (INFO):** ``dhan_futures`` / ``dhan_intraday`` MISSING. Those paths
    are filled by backfill scripts, not by the live dual ingest. A complete
    Sentinel day with no parquet backfill is not an incomplete session
    (session-eval 2026-11-13).
    """
    global _gap_count
    if is_trading_day(now.date()) and now.time().replace(tzinfo=None) < time(15, 45):
        return
    prev = now.date() if is_trading_day(now.date()) and now.time().replace(tzinfo=None) >= time(15, 45) else _previous_trading_day(now.date())
    if prev is None:
        return
    key = prev.isoformat()
    with _gap_lock:
        if key in _gap_checked:
            return
    session_gaps: list[str] = []
    repair_missing: list[str] = []
    for symbol in ("NIFTY", "BANKNIFTY", "SENSEX"):
        try:
            report = audit_range(root, symbol, key, key, now=now)
        except Exception:
            log.exception("gap audit failed for %s %s", symbol, key)
            return
        day = report["days"][0]
        for label, status in (
            ("futures", day["futures_status"]),
            ("options", day["options_matrix_status"]),
            ("sentinel", day["sentinel_status"]),
        ):
            # Sensex futures are not in the Dhan near-month backfill. A missing
            # file is the steady state, not a session gap.
            if symbol == "SENSEX" and label == "futures":
                continue
            if status not in ("PARTIAL", "MISSING"):
                continue
            if label in ("futures", "options") and status == "MISSING":
                repair_missing.append(f"{symbol} {label} {status}")
                continue
            session_gaps.append(f"{symbol} {label} {status}")
    with _gap_lock:
        if key in _gap_checked:
            return
        _gap_count = len(session_gaps)
        if not session_gaps and not repair_missing:
            _gap_checked.add(key)
    fp = f"GAP|{key}"
    repair_fp = f"REPAIR|{key}"
    if session_gaps:
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=fp,
            severity="WARNING",
            category="GAP",
            title=f"Session {key} has incomplete journals",
            description="; ".join(session_gaps),
            session_date=key,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(fp, stamp)
    if repair_missing and not session_gaps:
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=repair_fp,
            severity="INFO",
            category="REPAIR",
            title=f"Session {key} Dhan backfill not on disk",
            description="; ".join(repair_missing),
            session_date=key,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(repair_fp, stamp)


def _parse_beat(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return as_ist(datetime.fromisoformat(value))
    except ValueError:
        return None


def _process_alert(now: datetime, stamp: str) -> None:
    """A process that beat during this session and then went quiet.

    A process with no heartbeat row is not running, so it does not alert.
    Outside the regular session every process alert is resolved.
    """
    now = as_ist(now)
    stale: set[str] = set()
    if is_regular_session(now):
        for row in db.list_ops_status(name="heartbeat"):
            beat = _parse_beat(row.get("beat_at"))
            if beat is None or beat.date() != now.date():
                continue
            fingerprint = f"PROCESS|{row['process']}"
            if now - beat > PROCESS_SILENCE:
                stale.add(fingerprint)
                age_s = int((now - beat).total_seconds())
                db.upsert_alert(
                    alert_id=_alert_id(),
                    fingerprint=fingerprint,
                    severity="WARNING",
                    category="PROCESS",
                    title=f"Process {row['process']} heartbeat is stale",
                    description=(
                        f"Last beat {row.get('beat_at')} is {age_s}s old "
                        f"(quiet for more than {int(PROCESS_SILENCE.total_seconds())}s)."
                    ),
                    session_date=now.date().isoformat(),
                    now_iso=stamp,
                )
    for row in db.list_alerts():
        fingerprint = str(row.get("fingerprint") or "")
        if fingerprint.startswith("PROCESS|") and fingerprint not in stale:
            db.resolve_alert(fingerprint, stamp)


def _obs_perf_alerts(now: datetime, stamp: str) -> None:
    """Latency / concurrency / vendor alerts from the Observability snapshot."""
    try:
        from app.admin.obs_snapshot import build_ops_payload
        import config
        snap = build_ops_payload(window="15m", limit=1)
    except Exception:
        log.exception("obs perf alert snapshot failed")
        return

    http = snap.get("http") or {}
    infra = snap.get("infra") or {}
    failures = ((snap.get("events_summary") or {}).get("failures") or {})
    gate = ((snap.get("external") or {}).get("rate_gate") or {}).get("chain") or {}
    resilience = snap.get("resilience") or {}
    chain = snap.get("chain") or {}
    session_date = as_ist(now).date().isoformat()

    err_fp = "OBS|http-error-rpm"
    err_rpm = float(http.get("error_rpm") or 0)
    if err_rpm >= OBS_ERROR_RPM_WARN:
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=err_fp,
            severity="CRITICAL" if err_rpm >= OBS_ERROR_RPM_WARN * 2 else "WARNING",
            category="OBS",
            title="HTTP error RPM elevated",
            description=f"error_rpm={err_rpm:.0f} over the last 60s (threshold {OBS_ERROR_RPM_WARN}).",
            session_date=session_date,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(err_fp, stamp)

    lag_fp = "OBS|loop-lag"
    lag = float(infra.get("loop_lag_p95_ms") or http.get("loop_lag_p95_ms") or 0.0)
    if lag >= OBS_LOOP_LAG_P95_MS:
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=lag_fp,
            severity="WARNING",
            category="OBS",
            title="Event-loop lag p95 elevated",
            description=f"loop_lag_p95={lag:.0f}ms (threshold {OBS_LOOP_LAG_P95_MS:.0f}ms).",
            session_date=session_date,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(lag_fp, stamp)

    wd_fp = "OBS|watchdog-restart"
    if int(failures.get("watchdog.restart") or 0) > 0:
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=wd_fp,
            severity="CRITICAL",
            category="OBS",
            title="Event-loop watchdog restarted the process",
            description=f"watchdog.restart count in window: {failures.get('watchdog.restart')}.",
            session_date=session_date,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(wd_fp, stamp)

    gate_fp = "OBS|rate-gate-starve"
    headroom = gate.get("headroom")
    if headroom is not None and float(headroom) <= OBS_GATE_HEADROOM_WARN:
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=gate_fp,
            severity="WARNING",
            category="OBS",
            title="Dhan chain rate-gate headroom nearly exhausted",
            description=f"chain headroom={headroom} (threshold {OBS_GATE_HEADROOM_WARN}).",
            session_date=session_date,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(gate_fp, stamp)

    for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
        cb_fp = f"OBS|circuit-open|{sym}"
        state = ((resilience.get(sym) or {}).get("circuit") or {}).get("state")
        if state == "OPEN":
            db.upsert_alert(
                alert_id=_alert_id(),
                fingerprint=cb_fp,
                severity="WARNING",
                category="OBS",
                title=f"{sym} circuit breaker is OPEN",
                description=f"{sym} CB state OPEN with cooldown "
                f"{((resilience.get(sym) or {}).get('circuit') or {}).get('cooldown_remaining')}s.",
                session_date=session_date,
                now_iso=stamp,
            )
        else:
            db.resolve_alert(cb_fp, stamp)

        row = chain.get(sym) or {}
        p95_fp = f"OBS|chain-p95|{sym}"
        if int(row.get("n") or 0) >= 10 and float(row.get("p95_ms") or 0) >= OBS_CHAIN_P95_MS:
            db.upsert_alert(
                alert_id=_alert_id(),
                fingerprint=p95_fp,
                severity="WARNING",
                category="OBS",
                title=f"{sym} chain p95 above 30s",
                description=f"{sym} chain p95={row.get('p95_ms')}ms over n={row.get('n')}.",
                session_date=session_date,
                now_iso=stamp,
            )
        else:
            db.resolve_alert(p95_fp, stamp)

        silent_fp = f"OBS|chain-silent|{sym}"
        last_ok = row.get("last_ok_at")
        silent = False
        try:
            ingest_open = config.is_chain_ingest_open(now)
        except Exception:
            ingest_open = is_regular_session(now)
        # Only after a publish has been seen — cold start before first chain
        # row must not CRITICAL-spam empty ledgers.
        if ingest_open and last_ok:
            try:
                beat = as_ist(datetime.fromisoformat(str(last_ok)))
                age = (as_ist(now) - beat).total_seconds()
                silent = age > (OBS_CHAIN_INTERVAL_S * OBS_CHAIN_SILENT_MULT)
            except Exception:
                silent = False
        if silent:
            db.upsert_alert(
                alert_id=_alert_id(),
                fingerprint=silent_fp,
                severity="CRITICAL",
                category="OBS",
                title=f"{sym} chain publish is silent",
                description=f"{sym} last_ok_at={last_ok or 'never'} during ingest window.",
                session_date=session_date,
                now_iso=stamp,
            )
        else:
            db.resolve_alert(silent_fp, stamp)


def _slo_alerts(now: datetime, stamp: str) -> None:
    """Minute readiness rollup + consecutive not-ready / VWAP / tape burns."""
    import config
    from app.readiness import build_readiness
    from app.slo_monitor import (
        NOT_READY_STREAK_CRIT,
        probe_tape_completeness,
        probe_vwap_blackout,
        record_readiness_minute,
    )

    session_date = as_ist(now).date().isoformat()
    try:
        market_open = bool(config.is_market_open(now))
        readiness = build_readiness(market_open=market_open)
        roll = record_readiness_minute(readiness, now=as_ist(now))
    except Exception:
        log.exception("slo readiness rollup failed")
        return

    streak_fp = "SLO|readiness-streak"
    streak = int(roll.get("streak") or 0)
    if market_open and streak >= NOT_READY_STREAK_CRIT:
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=streak_fp,
            severity="CRITICAL",
            category="SLO",
            title=f"Readiness false for {streak} consecutive minutes",
            description=(
                f"reasons={','.join(roll.get('reasons') or []) or 'unknown'}. "
                f"Threshold={NOT_READY_STREAK_CRIT}m (docs/sli-slo.md)."
            ),
            session_date=session_date,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(streak_fp, stamp)

    vwap_fp = "SLO|vwap-blackout"
    try:
        vwap = probe_vwap_blackout()
    except Exception:
        log.exception("slo vwap probe failed")
        vwap = {"blackout": False, "count": 0}
    if market_open and vwap.get("blackout"):
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=vwap_fp,
            severity="CRITICAL",
            category="SLO",
            title="Ideas VWAP blackout (sustained Missing session VWAP refuses)",
            description=(
                f"{vwap.get('count')} rec.refused VWAP misses in "
                f"{vwap.get('window_min')}m (floor {vwap.get('floor')}). "
                "Fix publisher Sentinel context — do not lower CONT gates."
            ),
            session_date=session_date,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(vwap_fp, stamp)

    tape_fp = "SLO|tape-completeness"
    try:
        tape = probe_tape_completeness(now=as_ist(now), market_open=market_open)
    except Exception:
        log.exception("slo tape probe failed")
        tape = {"ok": True, "severity": "", "reason": ""}
    sev = str(tape.get("severity") or "")
    if sev and not tape.get("ok"):
        detail = ", ".join(
            f"{sym} {row.get('bars')}/{row.get('expected')}"
            for sym, row in (tape.get("per_symbol") or {}).items()
        )
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=tape_fp,
            severity=sev,
            category="SLO",
            title="Sentinel 1m tape completeness burn",
            description=f"{tape.get('reason')}: {detail} (worst_frac={tape.get('worst_frac')})",
            session_date=session_date,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(tape_fp, stamp)


def _prune_events(now: datetime) -> None:
    cutoff = (as_ist(now) - EVENT_KEEP).isoformat(timespec="seconds")
    try:
        db.prune_ops_events(cutoff)
    except Exception:
        log.exception("ops event prune failed")


async def _loop() -> None:
    await asyncio.sleep(5)
    while True:
        try:
            await asyncio.to_thread(evaluate_alerts, ROOT)
        except Exception:
            log.exception("admin health loop")
        await asyncio.sleep(60)


def start_background() -> None:
    global _task
    loop = asyncio.get_running_loop()
    if _task is not None and not _task.done():
        return
    _task = loop.create_task(_loop())
