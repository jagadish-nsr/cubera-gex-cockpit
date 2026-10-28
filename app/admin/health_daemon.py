"""Deduplicated token, feed, WAL, gap, and stale-process alerts.

The loop reads ``token_status()`` and ``app.ws_spot_feed.get_status()``. It does
not open a second WebSocket, and it does not checkpoint or vacuum. A closed
session with a gap is rechecked until the journals are complete. A clean
session is not scanned again until the process restarts.
"""

from __future__ import annotations

import asyncio
import logging
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
_gap_checked: set[str] = set()
_gap_count = 0
_gap_lock = threading.Lock()
_task: asyncio.Task | None = None


def gap_count() -> int:
    with _gap_lock:
        return _gap_count


def reset_gap_cache() -> None:
    global _gap_count
    with _gap_lock:
        _gap_checked.clear()
        _gap_count = 0


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
    if include_gaps:
        _gap_alert(root, now, stamp)
    _process_alert(now, stamp)
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


def _wal_alert(root: Path, stamp: str) -> None:
    path = root / "data" / "sentinel.db-wal"
    fp = "DB|sentinel-wal"
    size = path.stat().st_size if path.exists() else 0
    if size > WAL_WARN_BYTES:
        mb = size / (1024 * 1024)
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=fp,
            severity="WARNING",
            category="DB",
            title="sentinel.db WAL is over 25 MB",
            description=f"sentinel.db-wal is {mb:.1f} MB. The admin monitor does not checkpoint it.",
            session_date=None,
            now_iso=stamp,
        )
    else:
        db.resolve_alert(fp, stamp)


def _gap_alert(root: Path, now: datetime, stamp: str) -> None:
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
    gaps = []
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
            if status in ("PARTIAL", "MISSING"):
                gaps.append(f"{symbol} {label} {status}")
    with _gap_lock:
        if key in _gap_checked:
            return
        _gap_count = len(gaps)
        if not gaps:
            _gap_checked.add(key)
    fp = f"GAP|{key}"
    if gaps:
        db.upsert_alert(
            alert_id=_alert_id(),
            fingerprint=fp,
            severity="WARNING",
            category="GAP",
            title=f"Session {key} has incomplete journals",
            description="; ".join(gaps),
            session_date=key,
            now_iso=stamp,
        )
        return
    db.resolve_alert(fp, stamp)


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
