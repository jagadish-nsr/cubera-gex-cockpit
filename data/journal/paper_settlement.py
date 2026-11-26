"""Settle paper trades that no other path will close.

The GO sweep walks ``go_open``; the Sentinel lifecycle closes on TARGET / SPOT_INVAL. Rows
outside both stay OPEN forever (26-Nov audit: 13 rows, some from 13-Nov). After the F&O
close this runs over every OPEN paper trade and applies the first rule that fits:

1. Linked go_open row already closed → mirror its exit premium, reason and time.
   Linked go_open still OPEN → leave it to the GO sweep.
2. Sentinel twin: no recommendation links this row, and another row on the same strike/side
   opened in the same second is closed or linked → VOID_DUPLICATE (settling both would
   count the trade twice).
3. Sentinel, every leg expiring on the entry day → intrinsic on the expiry-day close.
4. Sentinel, recommendation marked on the entry day → SESSION_END at that mark.
5. Any other row whose legs have all expired → intrinsic on the expiry-day close.
6. Sentinel from an earlier day with no price at all → VOID_NO_MARK (zero P&L, no costs).

Anything else stays OPEN and is logged as a warning. Index options are cash-settled at
intrinsic on the exchange closing value; the closing value here is the first stored spot at
or after 15:30 IST on the expiry day (session-close snapshot, else the timeseries).
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from data.journal import store as journal_store

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[2]
JOURNAL_DIR = ROOT / "data" / "journal"
FNO_CLOSE_MIN = 15 * 60 + 40
CLOSE_PRINT_FROM = "15:30"
SENTINEL = "SENTINEL_ENGINE"
_WARNED: set[str] = set()


def parse_expiry(raw: Any) -> str | None:
    """'26NOV2026' / '2026-11-26' / '26-Nov-2026' → '2026-11-26'. None when absent or unreadable."""
    s = str(raw or "").strip()
    if not s:
        return None
    for fmt in ("%d%b%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(s.title() if "%b" in fmt else s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def expiry_close(symbol: str, day: str, journal_dir: Path | None = None) -> tuple[float, str] | None:
    """(closing spot, source) for ``symbol`` on ``day``, or None when no print at/after 15:30 exists."""
    jd = journal_dir or JOURNAL_DIR
    sym = symbol.upper()
    db = jd / "market_snapshot.db"
    if db.exists():
        try:
            con = sqlite3.connect(db, timeout=5.0)
            try:
                row = con.execute(
                    "SELECT spot, as_of FROM session_snapshots WHERE symbol = ?", (sym,)
                ).fetchone()
            finally:
                con.close()
            if row and row[0] and row[1]:
                at = datetime.fromisoformat(str(row[1])).astimezone(IST)
                if at.date().isoformat() == day and at.strftime("%H:%M") >= CLOSE_PRINT_FROM:
                    return float(row[0]), f"session_snapshots {at:%H:%M:%S}"
        except (sqlite3.Error, ValueError) as exc:
            log.warning("expiry_close: session_snapshots unreadable for %s: %s", sym, exc)
    ts = jd / "timeseries" / sym / f"{day}.csv"
    if ts.exists():
        with ts.open(newline="") as f:
            late = [r for r in csv.DictReader(f)
                    if str(r.get("time_ist") or "") >= CLOSE_PRINT_FROM and r.get("spot")]
        if late:
            return float(late[0]["spot"]), f"timeseries {late[0]['time_ist']}"
    return None


def _intrinsic_exit(trade, close: float) -> float:
    """Signed exit premium at expiry: BUY legs are worth −intrinsic, SELL legs +intrinsic
    (same sign as entry_net_premium, where a debit is negative)."""
    total = 0.0
    for leg in trade.legs:
        k = float(leg["strike"])
        kind = str(leg.get("type") or "").upper()
        value = max(0.0, close - k) if kind in ("CE", "C", "CALL") else max(0.0, k - close)
        total += value if str(leg.get("action") or "").upper() == "SELL" else -value
    return round(total, 2)


def _leg_expiries(trade) -> list[str | None]:
    return [parse_expiry(leg.get("expiry")) for leg in (trade.legs or [])]


def _same_leg(a, b) -> bool:
    la, lb = (a.legs or [{}])[0], (b.legs or [{}])[0]
    return (float(la.get("strike") or 0) == float(lb.get("strike") or 0)
            and str(la.get("type") or "").upper() == str(lb.get("type") or "").upper())


def _settle_at_expiry(trade, day: str, journal_dir: Path | None) -> dict | None:
    got = expiry_close(trade.symbol, day, journal_dir)
    if got is None:
        return None
    close, source = got
    exit_px = _intrinsic_exit(trade, close)
    journal_store.close_trade(
        trade.trade_id, exit_px, exit_reason="EXPIRY_SETTLE", exit_ts=f"{day}T15:30:00+05:30",
        notes=f"Settled at intrinsic on {trade.symbol} close {close:.2f} ({source}).",
    )
    return {"trade_id": trade.trade_id, "rule": "EXPIRY_SETTLE", "exit": exit_px, "close": close}


def _settle_one(trade, today: str, after_close: bool, journal_dir: Path | None,
                sentinel_rows: list) -> dict | None:
    from data.journal import go_efficiency as go_eff
    from data.journal import recommendations_store as rec_store

    entry_day = str(trade.entry_ts)[:10]
    go = go_eff.linked_go_row(trade.trade_id)
    if go is not None:
        if go["status"] == "OPEN":
            return None
        if go.get("exit_premium") is not None:
            journal_store.close_trade(
                trade.trade_id, float(go["exit_premium"]),
                exit_reason=str(go.get("exit_reason") or go["status"]),
                exit_ts=go.get("closed_at") or None,
                notes="Settled from go_open; the linked paper close was missed.",
            )
            return {"trade_id": trade.trade_id, "rule": "GO_MIRROR", "exit": go["exit_premium"]}

    expiries = _leg_expiries(trade)
    all_dated = bool(expiries) and all(expiries)
    last_expiry = max(expiries) if all_dated else None
    expired = all_dated and (last_expiry < today or (last_expiry == today and after_close))

    if trade.exit_by == SENTINEL:
        rec = rec_store.get_sentinel_recommendation_for_paper(trade.trade_id)
        if rec is None:
            for other in sentinel_rows:
                if (other.trade_id != trade.trade_id
                        and str(other.entry_ts)[:19] == str(trade.entry_ts)[:19]
                        and _same_leg(trade, other)
                        and (other.status == "CLOSED"
                             or rec_store.get_sentinel_recommendation_for_paper(other.trade_id))):
                    journal_store.void_trade(
                        trade.trade_id, "VOID_DUPLICATE",
                        notes=f"Duplicate open of {other.trade_id}; that row carries the trade.",
                    )
                    return {"trade_id": trade.trade_id, "rule": "VOID_DUPLICATE", "twin": other.trade_id}
        if expired and last_expiry == entry_day:
            done = _settle_at_expiry(trade, last_expiry, journal_dir)
            if done:
                return done
        if rec is not None and float(rec.get("current_ltp") or 0) > 0 \
                and str(rec.get("updated_at") or "")[:10] == entry_day:
            mark = float(rec["current_ltp"])
            journal_store.close_trade(
                trade.trade_id, -mark, exit_reason="SESSION_END",
                exit_ts=str(rec["updated_at"]).replace(" ", "T") + "+05:30",
                notes=f"Session-end mark {mark:.2f} from {rec['rec_id']}.",
            )
            return {"trade_id": trade.trade_id, "rule": "SESSION_END", "exit": -mark}
        if entry_day < today:
            journal_store.void_trade(
                trade.trade_id, "VOID_NO_MARK",
                notes="No entry-day mark and no same-day expiry close; no honest exit price.",
            )
            return {"trade_id": trade.trade_id, "rule": "VOID_NO_MARK"}
        return None

    if expired:
        return _settle_at_expiry(trade, last_expiry, journal_dir)
    return None


def settle_open_paper(now: datetime | None = None, journal_dir: Path | None = None) -> list[dict]:
    """Apply the rules above to every OPEN paper trade that is past its entry session."""
    when = now or datetime.now(IST)
    when = when.replace(tzinfo=IST) if when.tzinfo is None else when.astimezone(IST)
    today = when.date().isoformat()
    after_close = when.hour * 60 + when.minute >= FNO_CLOSE_MIN
    due = [t for t in journal_store.list_open_trades()
           if str(t.entry_ts)[:10] < today or (str(t.entry_ts)[:10] == today and after_close)]
    if not due:
        return []
    sentinel_rows = [t for t in due if t.exit_by == SENTINEL]
    if sentinel_rows:
        sentinel_rows += [t for t in journal_store.list_closed_trades(limit=2000) if t.exit_by == SENTINEL]
    out: list[dict] = []
    for trade in due:
        try:
            done = _settle_one(trade, today, after_close, journal_dir, sentinel_rows)
        except Exception:
            log.warning("paper settlement failed for %s", trade.trade_id, exc_info=True)
            continue
        if done:
            out.append(done)
        elif not all(_leg_expiries(trade)) and trade.trade_id not in _WARNED:
            _WARNED.add(trade.trade_id)
            log.warning("paper trade %s (%s %s) is OPEN with an undated leg; it can never settle at expiry",
                        trade.trade_id, trade.symbol, trade.strategy_name)
    if out:
        log.info("paper settlement closed %d row(s): %s", len(out), out)
    return out
