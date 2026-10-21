"""data/journal/store.py — Paper-trade SQLite ledger.

Design principles (from wife-dashboard-scope.md + CLAUDE.md):
  - Point-in-time honesty: every trade row stores an IMMUTABLE JSON snapshot of
    GEX levels and regime AT ENTRY. Re-labelling with today's GEX is forbidden.
  - SQLite WAL mode so the Streamlit reader never blocks the writer.
  - Single-writer via a module-level lock; readers open read-only connections.
  - Paper-only: no broker, no orders, no real money.
  - Costs are real: brokerage + slippage subtracted on close (no fantasy fills).

Schema (paper_trades):
  trade_id          TEXT  PRIMARY KEY   — "pt_{YYYYMMDD}_{HHMMSS}_{4-hex}"
  symbol            TEXT               — "NIFTY" | "BANKNIFTY" | "SENSEX"
  strategy_name     TEXT               — e.g. "Iron Condor", "Bear Call Spread"
  status            TEXT               — "OPEN" | "CLOSED"
  entry_ts          TEXT               — ISO-8601 IST
  exit_ts           TEXT               — ISO-8601 IST or NULL
  legs_json         TEXT               — JSON list of {action,type,strike,ltp,expiry}
  entry_spot        REAL
  entry_levels_json TEXT               — JSON snapshot of Levels at entry (IMMUTABLE)
  entry_regime_json TEXT               — JSON snapshot of RegimeView at entry (IMMUTABLE)
  entry_net_premium REAL               — positive = credit received, negative = debit paid
  exit_net_premium  REAL               — NULL until closed
  gross_pnl         REAL               — NULL until closed
  brokerage_cost    REAL               — configurable per-order fee (default 20 ₹/order)
  slippage_cost     REAL               — 0.5 pts per leg × n_legs × lot_size
  net_realized_pnl  REAL               — NULL until closed
  exit_reason       TEXT               — "MANUAL" | "TARGET" | "STOP" | "FORCED_DATE"
  notes             TEXT
  lot_size          INTEGER            — snapshot of lot size at entry
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
_DB_PATH = ROOT / "data" / "journal" / "paper_trades.db"

# ---------------------------------------------------------------------------
# Default cost assumptions (override via config or caller args)
# ---------------------------------------------------------------------------
DEFAULT_BROKERAGE_PER_ORDER_INR: float = 20.0   # flat per order entry/exit
DEFAULT_SLIPPAGE_PTS_PER_LEG: float = 0.50       # half a point each leg

# Module-level write lock: only one thread writes at a time.
_WRITE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PaperTrade:
    trade_id: str
    symbol: str
    strategy_name: str
    status: str                    # "OPEN" | "CLOSED"
    entry_ts: str                  # ISO-8601 IST string
    exit_ts: str | None
    legs: list[dict]               # [{action, type, strike, ltp, expiry}, ...]
    entry_spot: float
    entry_levels: dict             # immutable GEX snapshot
    entry_regime: dict             # immutable regime snapshot
    entry_net_premium: float       # positive = credit, negative = debit
    exit_net_premium: float | None
    gross_pnl: float | None
    brokerage_cost: float
    slippage_cost: float
    net_realized_pnl: float | None
    exit_reason: str | None
    notes: str
    lot_size: int
    exit_by: str = ""


def _now_ist() -> str:
    """Current time as ISO-8601 string in IST (UTC+5:30)."""
    from datetime import timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).isoformat(timespec="seconds")


def _slippage(legs: list[dict], lot_size: int, pts_per_leg: float) -> float:
    """Total slippage cost = pts_per_leg × n_legs × lot_size."""
    return pts_per_leg * len(legs) * lot_size


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

def _get_write_conn() -> sqlite3.Connection:
    """Open (or create) the DB and ensure WAL + schema exist. Caller holds _WRITE_LOCK."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            trade_id          TEXT PRIMARY KEY,
            symbol            TEXT NOT NULL,
            strategy_name     TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'OPEN',
            entry_ts          TEXT NOT NULL,
            exit_ts           TEXT,
            legs_json         TEXT NOT NULL,
            entry_spot        REAL NOT NULL,
            entry_levels_json TEXT NOT NULL,
            entry_regime_json TEXT NOT NULL,
            entry_net_premium REAL NOT NULL,
            exit_net_premium  REAL,
            gross_pnl         REAL,
            brokerage_cost    REAL NOT NULL,
            slippage_cost     REAL NOT NULL,
            net_realized_pnl  REAL,
            exit_reason       TEXT,
            notes             TEXT NOT NULL DEFAULT '',
            lot_size          INTEGER NOT NULL,
            exit_by           TEXT NOT NULL DEFAULT ''
        )
    """)
    try:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN exit_by TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # already present
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pt_status ON paper_trades (status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pt_symbol ON paper_trades (symbol, entry_ts DESC)
    """)
    conn.commit()
    return conn


def _get_read_conn() -> sqlite3.Connection:
    """Open a read-only connection. Never blocks the writer."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    uri = f"file:{_DB_PATH}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        # DB does not exist yet — return an in-memory empty DB so readers
        # don't crash on first launch before any trade is logged.
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Bootstrap minimal schema so queries return empty results instead of crashing
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                trade_id          TEXT PRIMARY KEY,
                symbol            TEXT NOT NULL,
                strategy_name     TEXT NOT NULL,
                status            TEXT NOT NULL DEFAULT 'OPEN',
                entry_ts          TEXT NOT NULL,
                exit_ts           TEXT,
                legs_json         TEXT NOT NULL,
                entry_spot        REAL NOT NULL,
                entry_levels_json TEXT NOT NULL,
                entry_regime_json TEXT NOT NULL,
                entry_net_premium REAL NOT NULL,
                exit_net_premium  REAL,
                gross_pnl         REAL,
                brokerage_cost    REAL NOT NULL,
                slippage_cost     REAL NOT NULL,
                net_realized_pnl  REAL,
                exit_reason       TEXT,
                notes             TEXT NOT NULL DEFAULT '',
                lot_size          INTEGER NOT NULL,
                exit_by           TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.commit()
        return conn


def ensure_schema() -> None:
    """Create the DB and schema if they don't exist. Idempotent."""
    with _WRITE_LOCK:
        conn = _get_write_conn()
        conn.close()


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def open_trade(
    symbol: str,
    strategy_name: str,
    legs: list[dict],
    entry_spot: float,
    entry_levels: dict,
    entry_regime: dict,
    entry_net_premium: float,
    lot_size: int,
    brokerage_per_order: float = DEFAULT_BROKERAGE_PER_ORDER_INR,
    slippage_pts: float = DEFAULT_SLIPPAGE_PTS_PER_LEG,
    notes: str = "",
    exit_by: str = "",
) -> PaperTrade:
    """Log a new paper trade as OPEN. Returns the PaperTrade with its assigned trade_id.

    entry_net_premium: positive = credit (short spread), negative = debit (long spread).
    entry_levels / entry_regime: pass dataclass.__dict__ or asdict() — stored immutably.
    """
    uid = uuid.uuid4().hex[:4].upper()
    ts = _now_ist()
    trade_id = f"pt_{ts[:10].replace('-', '')}_{ts[11:19].replace(':', '')}_{uid}"

    slip = _slippage(legs, lot_size, slippage_pts)
    # Entry-side slippage charged at open (half the round-trip cost)
    entry_slip = slip / 2.0
    brok = brokerage_per_order  # one order to open

    trade = PaperTrade(
        trade_id=trade_id,
        symbol=symbol.upper(),
        strategy_name=strategy_name,
        status="OPEN",
        entry_ts=ts,
        exit_ts=None,
        legs=legs,
        entry_spot=entry_spot,
        entry_levels=entry_levels,
        entry_regime=entry_regime,
        entry_net_premium=entry_net_premium,
        exit_net_premium=None,
        gross_pnl=None,
        brokerage_cost=brok,
        slippage_cost=entry_slip,
        net_realized_pnl=None,
        exit_reason=None,
        notes=notes,
        lot_size=lot_size,
        exit_by=exit_by,
    )

    _write_trade(trade)
    log.info("Opened paper trade %s: %s %s net_premium=%.2f",
             trade_id, symbol, strategy_name, entry_net_premium)
    return trade


def close_trade(
    trade_id: str,
    exit_net_premium: float,
    exit_reason: str = "MANUAL",
    brokerage_per_order: float = DEFAULT_BROKERAGE_PER_ORDER_INR,
    slippage_pts: float = DEFAULT_SLIPPAGE_PTS_PER_LEG,
    notes: str = "",
) -> PaperTrade | None:
    """Close an OPEN paper trade and calculate realised P&L net of costs.

    For a credit spread: gross_pnl = entry_premium - exit_premium
                         (profit when exit_premium < entry_premium = buyback cheaper)
    For a debit spread:  gross_pnl = exit_premium - entry_premium
                         (profit when exit_premium > entry_premium = sell for more)
    The sign is unified: gross_pnl > 0 means profit in all cases.
    """
    trade = get_trade(trade_id)
    if trade is None:
        log.warning("close_trade: trade_id %s not found", trade_id)
        return None
    if trade.status == "CLOSED":
        log.warning("close_trade: trade_id %s is already CLOSED", trade_id)
        return trade

    # gross P&L: entry premium - exit premium (works for both credit & debit)
    gross = trade.entry_net_premium - exit_net_premium

    exit_slip = _slippage(trade.legs, trade.lot_size, slippage_pts) / 2.0
    total_slip = trade.slippage_cost + exit_slip   # accumulate both legs
    total_brok = trade.brokerage_cost + brokerage_per_order  # open + close

    # Multiply by lot_size to get rupee P&L
    gross_inr = gross * trade.lot_size
    net_inr = gross_inr - total_brok - total_slip

    exit_ts = _now_ist()
    with _WRITE_LOCK:
        conn = _get_write_conn()
        try:
            conn.execute("""
                UPDATE paper_trades SET
                    status = 'CLOSED',
                    exit_ts = ?,
                    exit_net_premium = ?,
                    gross_pnl = ?,
                    brokerage_cost = ?,
                    slippage_cost = ?,
                    net_realized_pnl = ?,
                    exit_reason = ?,
                    notes = CASE WHEN ? != '' THEN ? ELSE notes END
                WHERE trade_id = ?
            """, (exit_ts, exit_net_premium, gross_inr,
                  total_brok, total_slip, net_inr,
                  exit_reason, notes, notes, trade_id))
            conn.commit()
        finally:
            conn.close()

    log.info("Closed paper trade %s: gross=₹%.0f net=₹%.0f (after costs)",
             trade_id, gross_inr, net_inr)
    return get_trade(trade_id)


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def _row_to_trade(row: sqlite3.Row) -> PaperTrade:
    return PaperTrade(
        trade_id=row["trade_id"],
        symbol=row["symbol"],
        strategy_name=row["strategy_name"],
        status=row["status"],
        entry_ts=row["entry_ts"],
        exit_ts=row["exit_ts"],
        legs=json.loads(row["legs_json"]),
        entry_spot=row["entry_spot"],
        entry_levels=json.loads(row["entry_levels_json"]),
        entry_regime=json.loads(row["entry_regime_json"]),
        entry_net_premium=row["entry_net_premium"],
        exit_net_premium=row["exit_net_premium"],
        gross_pnl=row["gross_pnl"],
        brokerage_cost=row["brokerage_cost"],
        slippage_cost=row["slippage_cost"],
        net_realized_pnl=row["net_realized_pnl"],
        exit_reason=row["exit_reason"],
        notes=row["notes"] or "",
        lot_size=row["lot_size"],
        exit_by=dict(row).get("exit_by") or "",
    )


def get_trade(trade_id: str) -> PaperTrade | None:
    conn = _get_read_conn()
    try:
        row = conn.execute(
            "SELECT * FROM paper_trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()
        return _row_to_trade(row) if row else None
    finally:
        conn.close()


def list_open_trades(symbol: str | None = None) -> list[PaperTrade]:
    """Return all OPEN trades, optionally filtered by symbol."""
    conn = _get_read_conn()
    try:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE status = 'OPEN' AND symbol = ? "
                "ORDER BY entry_ts DESC", (symbol.upper(),)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE status = 'OPEN' ORDER BY entry_ts DESC"
            ).fetchall()
        return [_row_to_trade(r) for r in rows]
    finally:
        conn.close()


def list_closed_trades(symbol: str | None = None, limit: int = 200) -> list[PaperTrade]:
    """Return CLOSED trades, most-recent first, up to `limit`."""
    conn = _get_read_conn()
    try:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE status = 'CLOSED' AND symbol = ? "
                "ORDER BY exit_ts DESC LIMIT ?", (symbol.upper(), limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE status = 'CLOSED' "
                "ORDER BY exit_ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_trade(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_json_dict(obj: Any) -> Any:
    """Recursively convert objects to JSON-serializable types, converting all keys to str."""
    if isinstance(obj, dict):
        return {str(k): _safe_json_dict(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)):
        return [_safe_json_dict(x) for x in obj]
    elif hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def _write_trade(trade: PaperTrade) -> None:
    """Insert a new trade row. Caller must NOT hold _WRITE_LOCK already."""
    with _WRITE_LOCK:
        conn = _get_write_conn()
        try:
            conn.execute("""
                INSERT INTO paper_trades (
                    trade_id, symbol, strategy_name, status, entry_ts, exit_ts,
                    legs_json, entry_spot, entry_levels_json, entry_regime_json,
                    entry_net_premium, exit_net_premium, gross_pnl,
                    brokerage_cost, slippage_cost, net_realized_pnl,
                    exit_reason, notes, lot_size, exit_by
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                trade.trade_id, trade.symbol, trade.strategy_name, trade.status,
                trade.entry_ts, trade.exit_ts,
                json.dumps(_safe_json_dict(trade.legs), default=str),
                trade.entry_spot,
                json.dumps(_safe_json_dict(trade.entry_levels), default=str),
                json.dumps(_safe_json_dict(trade.entry_regime), default=str),
                trade.entry_net_premium, trade.exit_net_premium, trade.gross_pnl,
                trade.brokerage_cost, trade.slippage_cost, trade.net_realized_pnl,
                trade.exit_reason, trade.notes, trade.lot_size, trade.exit_by,
            ))
            conn.commit()
        finally:
            conn.close()
