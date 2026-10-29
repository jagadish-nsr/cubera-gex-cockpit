"""Phase A capture loop — append an intraday index GEX snapshot to the day's
time-series (``data/journal/timeseries/<SYMBOL>/YYYY-MM-DD.csv``, gitignored).

  python scripts/log_timeseries.py             # loop every 5 min during NSE hours
  python scripts/log_timeseries.py --once       # one snapshot, then exit (for schedulers)
  python scripts/log_timeseries.py --loop 15    # custom interval (minutes)
  python scripts/log_timeseries.py --once --ignore-session   # capture off-hours (testing)

Reuses the dashboard pipeline (ONE NSE fetch -> levels + OI/volume aggregates), so
the engine stays the single source of truth. A failed pull is logged and the loop
keeps going (fail loud per row, don't crash the day). Decision-support only.

BUNDLED GAMMA-BLAST: inside 13:00-15:30 IST, the same --once task stays up
at a 1-minute cadence until 15:30 when Nifty, Bank Nifty, or Sensex has its
nearest listed expiry today. Each index is judged on its own chain. A pid lock
stops the next 15-minute fire from starting a second loop.

NOTE: the session guard does NOT know NSE holidays — on a holiday it will idle
during 09:15-15:30 IST and record flat/stale data (the OI staleness flag covers
this). Add the holiday calendar later if it matters.
"""

from __future__ import annotations

import argparse
import os
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data_access import load_dashboard  # noqa: E402
from app.timeseries import (GAMMA_BLAST_DIR, append_if_due,  # noqa: E402
                            append_strike_rows, build_row, build_strike_rows)

_OPEN_IST = (9, 15)    # NSE session 09:15-15:30 IST
_CLOSE_IST = (15, 30)
_BLAST_START_IST = (13, 0)    # gamma-blast window 13:00-15:30 IST (expiry days)
_BLAST_END_IST = (15, 30)


_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now() -> datetime:
    """Aware IST.

    Adding 5:30 to ``datetime.now(timezone.utc)`` leaves the tzinfo as UTC.
    ``config.is_market_open`` then converts that value to IST again, so a
    real 10:00 IST is tested as 15:30 and the cash session is skipped.
    """
    return datetime.now(_IST)


def _mins(hm: tuple[int, int]) -> int:
    return hm[0] * 60 + hm[1]


def _in_session(ist: datetime) -> bool:
    import config
    return config.is_market_open(ist, include_fno_extended=False)


def _in_blast_window(ist: datetime) -> bool:
    if ist.weekday() >= 5:
        return False
    try:
        from data.holidays import is_holiday
        if is_holiday(ist.date()):
            return False
    except Exception:
        pass
    m = ist.hour * 60 + ist.minute
    return _mins(_BLAST_START_IST) <= m <= _mins(_BLAST_END_IST)


def _is_expiry_day(data) -> bool:
    """True if the nearest expiry in this pull is today (IST) — the live,
    file-free expiry check that drives the bundled gamma-blast capture."""
    ist_today = (data.fetched_at + timedelta(hours=5, minutes=30)).date()
    return bool(data.expiries) and data.expiries[0] == ist_today


def _save_blast(data) -> int:
    """Write this pull's per-strike rows to the 1-min gamma-blast journal, routed to the feed's own
    tree (Dhan -> gamma_blast_dhan/<SYMBOL>/). Returns the strike count (0 on demo)."""
    from app.timeseries import gamma_blast_dir_for
    rows = build_strike_rows(data)
    if not rows:
        return 0
    ist = data.fetched_at + timedelta(hours=5, minutes=30)
    append_strike_rows(rows, date_ist=ist.strftime("%Y-%m-%d"),
                       symbol=data.symbol, directory=gamma_blast_dir_for(getattr(data, "source", None)))
    return len(rows)


def capture_once(min_gap: float = 10.0, symbol: str | None = None) -> tuple[str, object]:
    """One live pull -> 5-min time-series row (+ per-strike OI, deduped). On an
    expiry day inside the blast window it ALSO appends a 1-min gamma-blast row from
    the SAME pull. Returns (log line, DashboardData) so the caller can decide
    whether to enter the bundled 1-min blast loop."""
    data = load_dashboard(symbol=symbol)  # live NSE, single fetch
    ist = data.fetched_at + timedelta(hours=5, minutes=30)
    row = build_row(data)
    path, written = append_if_due(row, min_gap_minutes=min_gap, symbol=data.symbol)

    # Bundled gamma-blast: expiry day + blast window -> 1-min per-strike stream.
    blast_n = _save_blast(data) if (_is_expiry_day(data) and _in_blast_window(ist)) else 0
    blast_tag = f"  [blast +{blast_n}]" if blast_n else ""

    head = (f"[{data.symbol}] {row['date_ist']} {row['time_ist']} IST  spot {row['spot']:,}  "
            f"{row['regime']}/{row['mode_label']}  "
            f"walls {row['put_wall']:,}-{row['call_wall']:,}  magnet {row['magnet']:,}  "
            f"PCR {row['pcr_oi']}  news {row['news_count']}(impact {row['news_impact']})")
    if not written:
        return f"{head}  -> skipped (a row < {min_gap:g} min old exists){blast_tag}", data
    # Per-strike OI/ΔOI rides the SAME fetch; write it in lock-step with the main
    # row so the two captures never drift (skip writes nothing on either side). Routed to the
    # feed's own journal (Dhan -> oi_strikes_dhan) so sources never mix.
    from app.timeseries import oistrikes_dir_for
    strike_rows = build_strike_rows(data)
    append_strike_rows(strike_rows, date_ist=row["date_ist"], symbol=data.symbol,
                       directory=oistrikes_dir_for(data.source))
    extra = f" +{len(strike_rows)} strikes" if strike_rows else ""
    # On the Dhan feed, top up any 1-min OI gap (late start / missed stretch) from Dhan history —
    # a no-op when a 1-min capture is already caught up (see maybe_backfill_oi's gap check).
    bf_tag = ""
    if str(data.source or "").lower().startswith("dhan"):
        try:
            from app.oi_backfill import maybe_backfill_oi
            r = maybe_backfill_oi(data.symbol, row["date_ist"], log=lambda *a: None)
            if r.get("stamps"):
                bf_tag = f" +bf{r['stamps']}"
        except Exception:  # noqa: BLE001 - backfill is best-effort context
            pass
    # Index-futures intraday snapshot (SEPARATE live call; best-effort, never fatal).
    fut_tag = ""
    try:
        from app.futures_buildup import capture_futures_from_ws, futures_intraday_dir_for
        fdir = futures_intraday_dir_for(data.source)
        if str(data.source or "").lower().startswith("dhan"):   # Dhan-native futures quote
            from data.dhan_live import fetch_futures_dhan
            got = capture_futures_from_ws(symbol=data.symbol, fetch_fn=fetch_futures_dhan,
                                          parse_fn=lambda p, s: p, min_gap_minutes=min_gap, directory=fdir)
        else:
            got = capture_futures_from_ws(symbol=data.symbol, min_gap_minutes=min_gap, directory=fdir)
        if got and got[1]:
            fut_tag = " +fut"
    except Exception:  # noqa: BLE001 - futures capture is optional context
        pass
    return f"{head}  -> {path.name}{extra}{bf_tag}{fut_tag}{blast_tag}", data


def _blast_lock_path() -> Path:
    return ROOT / "data" / "journal" / "gamma_blast.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _blast_already_running() -> bool:
    path = _blast_lock_path()
    if not path.is_file():
        return False
    try:
        pid = int(path.read_text().strip())
    except ValueError:
        return False
    return pid != os.getpid() and _pid_alive(pid)


def _acquire_blast_lock() -> bool:
    if _blast_already_running():
        return False
    path = _blast_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()))
    return True


def _release_blast_lock() -> None:
    path = _blast_lock_path()
    try:
        if path.is_file() and path.read_text().strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


def _capture_other_blasts(primary: str) -> bool:
    """Write Bank Nifty and Sensex when that index's own nearest expiry is today."""
    from scripts.log_gamma_blast import BLAST_SYMBOLS, capture_once as blast_once
    expiry = False
    for sym in BLAST_SYMBOLS:
        if sym == primary:
            continue
        try:
            msg = blast_once(symbol=sym)
        except Exception as e:  # noqa: BLE001 - one index must not drop the others
            _print(f"[{sym}] blast capture FAILED: {e}")
            continue
        _print(msg)
        if "not an expiry day" not in msg:
            expiry = True
    return expiry


def _run_blast_window(symbol: str | None) -> None:
    """Keep capturing at 1-min cadence until 15:30 IST.

    The default scheduled fire (no --symbol) covers Nifty, Bank Nifty, and
    Sensex. Nifty keeps the time-series row. The other two write only the
    gamma-blast file, and only on their own expiry day.
    """
    from scripts.log_gamma_blast import BLAST_SYMBOLS, capture_once as blast_once
    if not _acquire_blast_lock():
        _print("gamma-blast window already running")
        return
    symbols = (symbol,) if symbol else BLAST_SYMBOLS
    try:
        while True:
            _time.sleep(60)
            ist = _ist_now()
            if not _in_blast_window(ist):
                _print(f"{ist:%H:%M} IST  blast window over — bundled 1-min capture stopped")
                return
            still = False
            for sym in symbols:
                try:
                    if symbol is None and sym != "NIFTY":
                        msg = blast_once(symbol=sym)
                        _print(msg)
                        if "not an expiry day" not in msg:
                            still = True
                    else:
                        msg, data = capture_once(min_gap=4.0, symbol=sym if symbol else None)
                        _print(msg)
                        _note_capture(msg, data)
                        if _is_expiry_day(data):
                            still = True
                except Exception as e:  # noqa: BLE001 - one bad pull must not stop the window
                    _print(f"{ist:%H:%M} IST  blast capture FAILED (kept looping): {e}")
                    _note_capture("", error=e)
                    still = True
            if not still:
                _print("nearest expiry rolled — bundled capture stopped")
                return
    finally:
        _release_blast_lock()


def _note_capture(msg, data=None, error=None) -> None:
    from app.obs import book_fields, event
    if error is not None:
        event(
            "timeseries.failed",
            level="WARNING",
            ok=False,
            workflow="timeseries",
            symbol=getattr(data, "symbol", None) if data is not None else None,
            error=str(error),
        )
        return
    fields = book_fields(data) if data is not None else {}
    skipped = "skipped" in (msg or "")
    event(
        "timeseries.skipped" if skipped else "timeseries.appended",
        workflow="timeseries",
        message=(msg or "")[:400],
        **fields,
    )
    if msg and "[blast" in msg:
        event("timeseries.blast", workflow="timeseries.blast", **fields)


def _print(s: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Intraday index GEX time-series capture.")
    ap.add_argument("--once", action="store_true", help="single snapshot then exit")
    ap.add_argument("--symbol", default=None,
                    help="index to capture (NIFTY/BANKNIFTY); default NIFTY")
    ap.add_argument("--loop", type=float, default=5.0, help="interval in minutes (loop mode)")
    ap.add_argument("--min-gap", type=float, default=4.0,
                    help="dedup guard: skip if a row was saved < this many minutes ago "
                         "(auto-clamped below the loop interval; <=0 disables)")
    ap.add_argument("--ignore-session", action="store_true",
                    help="capture even outside NSE hours (testing)")
    args = ap.parse_args()
    from app.obs import boot, shutdown
    boot("log_timeseries", loop=True)

    if args.once:
        ist = _ist_now()
        if not args.ignore_session and not _in_session(ist):
            _print(f"{ist:%Y-%m-%d %H:%M} IST  outside NSE session - skipped")
            shutdown(ok=True, message="outside session")
            return                                  # clean exit: weekend / off-window fire
        try:
            msg, data = capture_once(min_gap=args.min_gap, symbol=args.symbol)
            _print(msg)
            _note_capture(msg, data)
        except Exception as e:  # noqa: BLE001 - surface and exit non-zero for schedulers
            _print(f"capture FAILED: {e}")
            _note_capture("", error=e)
            shutdown(ok=False, error=str(e))
            sys.exit(1)
        # Stay up through 15:30 when any of the three indices expires today.
        # The lock ignores the later 15-minute fires so they do not stack a second loop.
        if _in_blast_window(ist):
            if _blast_already_running():
                _print(f"{ist:%H:%M} IST  gamma-blast window already running")
            else:
                extra = _capture_other_blasts(data.symbol) if args.symbol is None else False
                if _is_expiry_day(data) or extra:
                    _print(f"{ist:%H:%M} IST  expiry blast — 1-min capture until 15:30 IST")
                    _run_blast_window(args.symbol)
        return

    # Never let the guard block the loop's own cadence: keep it below the interval.
    gap = min(args.min_gap, max(1.0, args.loop - 1.0)) if args.min_gap > 0 else 0.0
    base_interval = max(args.loop, 1.0) * 60.0
    _print(f"Time-series capture: every {args.loop:g} min during NSE hours "
           "(09:15-15:30 IST); auto 1-min in the gamma-blast window on expiry days. "
           "Ctrl+C to stop.")
    try:
        while True:
            ist = _ist_now()
            interval = base_interval
            if args.ignore_session or _in_session(ist):
                try:
                    msg, data = capture_once(min_gap=gap, symbol=args.symbol)
                    _print(msg)
                    _note_capture(msg, data)
                    # Tighten to 1-min cadence in the blast window on expiry days.
                    if _is_expiry_day(data) and _in_blast_window(ist):
                        interval = 60.0
                except Exception as e:  # noqa: BLE001 - one bad pull must not stop the day
                    _print(f"{ist:%H:%M} IST  capture FAILED (kept looping): {e}")
                    _note_capture("", error=e)
            else:
                _print(f"{ist:%Y-%m-%d %H:%M} IST  outside NSE session - idle")
            _time.sleep(interval)
    except KeyboardInterrupt:
        _print("stopped.")
        shutdown(ok=True, message="stopped")


if __name__ == "__main__":
    main()
