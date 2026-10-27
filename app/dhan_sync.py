"""One Dhan pull, then the existing compute path refreshes the book.

Off-market, Dhan stays idle. The three exceptions are a manual sync, the
06:30 IST run, and saving a new session token. Those call ``load_dashboard``
with ``force_live`` so the chain file on disk is not reused. The live chain
loop, the spot poller, and ordinary page reads do not.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.admin.clock import IST, as_ist

log = logging.getLogger("cubera.dhan_sync")

SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX")
MORNING_SYNC = (6, 30)
STATE_PATH = Path("data/journal/dhan_sync_state.json")
OPEN_WAIT_S = 120.0

_lock = threading.Lock()
_running = False
_hooks: list = []
_scheduler_started = False
_stop = threading.Event()
_status: dict = {
    "running": False,
    "status": "idle",
    "trigger": None,
    "window": None,
    "started_at": None,
    "finished_at": None,
    "refreshed_at": None,
    "refreshed_at_display": None,
    "last_error": None,
    "symbols": {},
}


def on_published(fn) -> None:
    """Register a callback invoked with the symbol after a successful publish."""
    if fn not in _hooks:
        _hooks.append(fn)


def status() -> dict:
    with _lock:
        return json.loads(json.dumps(_status))


def reset_for_tests() -> None:
    global _running
    with _lock:
        _running = False
        _status.update({
            "running": False,
            "status": "idle",
            "trigger": None,
            "window": None,
            "started_at": None,
            "finished_at": None,
            "refreshed_at": None,
            "refreshed_at_display": None,
            "last_error": None,
            "symbols": {},
        })


def offmarket_refresh_line(stamp: str, *, morning: bool) -> str:
    """The single off-market freshness line. Empty stamp stays explicit."""
    when = (stamp or "").strip()
    if morning and when:
        return f"Prior-session book. Data refreshed {when}."
    if morning:
        return "Prior-session book. Refresh time unavailable."
    if when:
        return f"Data refreshed {when}."
    return "Refresh time unavailable."


def schedule_due(now: datetime, last_scheduled_date: str | None) -> bool:
    """True at or after 06:30 IST when today's morning sync has not been requested."""
    now = as_ist(now)
    minutes = now.hour * 60 + now.minute
    if minutes < MORNING_SYNC[0] * 60 + MORNING_SYNC[1]:
        return False
    return last_scheduled_date != now.date().isoformat()


def read_schedule_date(path: Path | None = None) -> str | None:
    path = path or STATE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("last_scheduled_date") if isinstance(payload, dict) else None
    return value if isinstance(value, str) and len(value) == 10 else None


def write_schedule_date(day: str, path: Path | None = None) -> None:
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"last_scheduled_date": day}), encoding="utf-8")
    tmp.replace(path)


def seconds_until_morning(now: datetime) -> float:
    """Seconds until the next 06:30 IST, at least one second."""
    now = as_ist(now)
    target = now.replace(hour=MORNING_SYNC[0], minute=MORNING_SYNC[1], second=5, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def scheduler_tick(now: datetime | None = None, path: Path | None = None) -> bool:
    """Request the morning sync once per IST date. Returns whether it did."""
    now = as_ist(now or datetime.now(IST))
    path = path or STATE_PATH
    if not schedule_due(now, read_schedule_date(path)):
        return False
    request_sync("schedule", window="current_week")
    write_schedule_date(now.date().isoformat(), path)
    return True


def start_scheduler() -> None:
    """Daemon that fires the 06:30 sync, including a catch-up if the process starts later."""
    global _scheduler_started
    with _lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    threading.Thread(target=_scheduler_loop, name="dhan-sync-schedule", daemon=True).start()


def _scheduler_loop() -> None:
    while not _stop.is_set():
        try:
            scheduler_tick()
        except Exception as exc:
            log.warning("dhan morning sync check failed: %s", exc)
        _stop.wait(min(seconds_until_morning(datetime.now(IST)), 60.0))


def _token_ok(token_fn) -> bool:
    if token_fn is None:
        from data.dhan import token_status as token_fn
    try:
        row = token_fn() or {}
    except Exception as exc:
        log.warning("dhan token status failed: %s", exc)
        return False
    return bool(row.get("present")) and not bool(row.get("expired"))


def _quote_ohlc(data) -> dict | None:
    levels = getattr(data, "levels", None)
    spot = float(getattr(levels, "spot", 0) or 0) if levels is not None else 0.0
    raw = getattr(data, "day_ohlc", None)
    ohlc = dict(raw) if isinstance(raw, dict) else {}
    if spot > 0 and not ohlc.get("ltp"):
        ohlc["ltp"] = spot
    vix = getattr(data, "india_vix", None)
    if isinstance(vix, dict) and vix.get("last") is not None:
        ohlc["vix"] = vix
    fetched = getattr(data, "fetched_at", None)
    if hasattr(fetched, "isoformat"):
        ohlc["as_of"] = fetched.isoformat()
    if not ohlc.get("ltp"):
        return None
    return ohlc


def _accept(data) -> None:
    if data is None or getattr(data, "is_degraded", False):
        reason = getattr(data, "degraded_reason", None) or "Dhan did not return a fresh book"
        raise RuntimeError(str(reason))
    src = str(getattr(data, "source", "") or "").lower()
    if any(k in src for k in ("demo", "synthetic", "mock", "test")):
        raise RuntimeError("sync received a synthetic book")
    if getattr(getattr(data, "levels", None), "spot", None) in (None, 0):
        raise RuntimeError("sync book has no spot")


def _stamp(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(IST).strftime("%d-%m-%Y %H:%M:%S IST")


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def request_sync(
    trigger: str,
    symbols: tuple[str, ...] | None = None,
    window: str = "current_week",
    *,
    loader=None,
    publisher=None,
    quote_fn=None,
    market_open=None,
    token_fn=None,
    poll_s: float = 0.5,
) -> dict:
    """Start a background sync. Returns immediately.

    During the open session the existing chain loop does the pull, so this
    only wakes it. Off-market, the worker calls ``load_dashboard`` itself.
    """
    global _running
    if trigger not in ("manual", "schedule", "token"):
        raise ValueError("trigger must be manual, schedule, or token")
    chosen = tuple((symbols or SYMBOLS))
    bad = [sym for sym in chosen if sym not in SYMBOLS]
    if bad:
        raise ValueError(f"symbol must be one of {', '.join(SYMBOLS)}")
    with _lock:
        if _running:
            return {"status": "already_running", "running": True, "trigger": _status.get("trigger")}
    if not _token_ok(token_fn):
        with _lock:
            if _running:
                return {"status": "already_running", "running": True, "trigger": _status.get("trigger")}
            _status["status"] = "skipped"
            _status["last_error"] = "No valid Dhan token"
            _status["trigger"] = trigger
        return {"status": "skipped", "reason": "no_token", "running": False}

    with _lock:
        if _running:
            return {"status": "already_running", "running": True, "trigger": _status.get("trigger")}
        _running = True
        _status.update({
            "running": True,
            "status": "started",
            "trigger": trigger,
            "window": window,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "last_error": None,
            "symbols": {},
        })
    threading.Thread(
        target=_worker,
        kwargs={
            "trigger": trigger,
            "symbols": chosen,
            "window": window,
            "loader": loader,
            "publisher": publisher,
            "quote_fn": quote_fn,
            "market_open": market_open,
            "poll_s": poll_s,
        },
        name="dhan-sync",
        daemon=True,
    ).start()
    return {"status": "started", "running": True, "trigger": trigger, "window": window, "symbols": list(chosen)}


def _worker(*, trigger, symbols, window, loader, publisher, quote_fn, market_open, poll_s) -> None:
    global _running
    started = datetime.now(timezone.utc)
    results: dict[str, dict] = {}
    newest: datetime | None = None
    try:
        if market_open is None:
            import config
            market_open = config.is_market_open
        if market_open():
            results, newest = _sync_open(symbols, window, started, poll_s)
        else:
            results, newest = _sync_closed(symbols, window, loader, publisher, quote_fn)
    except Exception as exc:
        log.warning("dhan sync failed: %s", exc)
        results = {sym: {"ok": False, "error": str(exc)} for sym in symbols}
    errors = [f"{sym}: {row['error']}" for sym, row in results.items() if not row.get("ok")]
    error_text = "; ".join(errors) if errors else None
    with _lock:
        _running = False
        _status["running"] = False
        _status["finished_at"] = datetime.now(timezone.utc).isoformat()
        _status["symbols"] = results
        _status["last_error"] = error_text
        _status["status"] = "ok" if not errors else "error"
        if newest is not None:
            _status["refreshed_at"] = newest.astimezone(timezone.utc).isoformat()
            _status["refreshed_at_display"] = _stamp(newest)
    log.info("dhan sync finished trigger=%s errors=%s", trigger, error_text)


def _sync_open(symbols, window, started, poll_s) -> tuple[dict, datetime | None]:
    from app.live_fetcher import ensure, force, latest
    for sym in symbols:
        ensure(sym, False, "dhan", 60, window=window)
        force(sym, window=window)
    pending = set(symbols)
    results: dict[str, dict] = {}
    newest: datetime | None = None
    deadline = time.time() + OPEN_WAIT_S
    while pending and time.time() < deadline:
        for sym in list(pending):
            pub = latest(sym, window) or {}
            fetched = pub.get("fetched")
            if fetched is None or _as_utc(fetched) < started:
                continue
            pending.discard(sym)
            err = pub.get("err")
            data = pub.get("data")
            if err or data is None:
                results[sym] = {"ok": False, "error": err or "chain pull failed"}
                continue
            results[sym] = {"ok": True, "error": None, "fetched_at": _as_utc(fetched).isoformat()}
            if newest is None or _as_utc(fetched) > newest:
                newest = _as_utc(fetched)
        if pending:
            time.sleep(poll_s)
    for sym in pending:
        results[sym] = {"ok": False, "error": "timed out waiting for the live chain pull"}
    return results, newest


def _sync_closed(symbols, window, loader, publisher, quote_fn) -> tuple[dict, datetime | None]:
    if loader is None:
        from app.data_access import load_dashboard as loader
    if publisher is None:
        from app.live_fetcher import publish_snapshot as publisher
    if quote_fn is None:
        from app.spot_ticker import note_synced_book as quote_fn
    results: dict[str, dict] = {}
    newest: datetime | None = None
    for sym in symbols:
        try:
            data = loader(
                window=window, symbol=sym, use_demo=False, live_source="dhan", force_live=True,
            )
            _accept(data)
            fetched = publisher(sym, window, data)
            ohlc = _quote_ohlc(data)
            if ohlc is not None:
                quote_fn(sym, ohlc)
            for fn in list(_hooks):
                try:
                    fn(sym)
                except Exception as exc:
                    log.debug("dhan sync hook failed for %s: %s", sym, exc)
            moment = fetched if isinstance(fetched, datetime) else getattr(data, "fetched_at", None)
            if isinstance(moment, datetime):
                moment = _as_utc(moment)
                if newest is None or moment > newest:
                    newest = moment
            results[sym] = {
                "ok": True,
                "error": None,
                "fetched_at": moment.isoformat() if isinstance(moment, datetime) else None,
            }
            log.info("dhan sync %s published", sym)
        except Exception as exc:
            log.warning("dhan sync %s failed: %s", sym, exc)
            results[sym] = {"ok": False, "error": str(exc)}
    return results, newest
