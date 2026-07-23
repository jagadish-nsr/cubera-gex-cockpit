"""SQLite store for big-money EVENTS ([W2]) — the sparse, queryable directional history.

Events are few (dozens/day) but the VALUE is querying them across days ("net buy vs sell flow per
day", "all bursts > ₹5cr this week"), so a small indexed DB beats per-day JSONL. Raw ticks stay as
CSV/Parquet — a row-store DB is the wrong home for that firehose (see docs/big-money-tape.md).

`app.big_money.append_event` / `load_events` delegate here, so all callers (collector, dashboard,
summary) use SQLite transparently. WAL + busy_timeout make concurrent writers (the dashboard's
in-process collector and a headless collector) + readers safe. A UNIQUE key dedupes the repeated
identical prints the snapshot cadence emits. Sign-free; decision-support.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "journal" / "big_money.db"          # gitignored (under data/journal/)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    epoch    INTEGER NOT NULL,
    ts_ist   TEXT    NOT NULL,
    day      TEXT    NOT NULL,
    symbol   TEXT    NOT NULL,
    kind     TEXT    NOT NULL,          -- 'print' | 'burst'
    side     TEXT,                      -- 'buy' | 'sell' | NULL (aggressor)
    qty      INTEGER,
    lots     REAL,
    notional REAL    NOT NULL,
    price    REAL    NOT NULL,
    bid      REAL,
    ask      REAL,
    expiry   TEXT,                      -- print: contract expiry; burst: 'AGG' (symbol-aggregate)
    oi       REAL,                      -- symbol total OI (near+next) at event time
    doi      REAL,                      -- ΔOI over the window: new positioning (+) vs unwind (−)
    UNIQUE(epoch, symbol, kind, qty, price, expiry)   -- dedupe repeats; expiry keeps near/next distinct
);
CREATE INDEX IF NOT EXISTS ix_events_day     ON events(day);
CREATE INDEX IF NOT EXISTS ix_events_sym_day ON events(symbol, day);

CREATE TABLE IF NOT EXISTS futures_state (
    symbol  TEXT NOT NULL,
    roll    TEXT NOT NULL,              -- 'near' | 'next'
    expiry  TEXT,
    oi      REAL,
    price   REAL,
    ts      INTEGER,
    PRIMARY KEY (symbol, roll)          -- one row per contract slot; upserted
);
"""

_COLS = ("epoch", "ts_ist", "symbol", "kind", "side", "qty", "lots", "notional", "price", "bid",
         "ask", "expiry", "oi", "doi")


def _connect(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=5.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(_SCHEMA)                    # idempotent (CREATE IF NOT EXISTS)
    # back-compat: a pre-existing events table may lack the expiry column — add it (the UNIQUE stays
    # the old shape on that legacy table, which only weakens dedupe on already-captured rows).
    have = {r[1] for r in con.execute("PRAGMA table_info(events)")}
    for col, typ in (("expiry", "TEXT"), ("oi", "REAL"), ("doi", "REAL")):
        if col not in have:
            con.execute(f"ALTER TABLE events ADD COLUMN {col} {typ}")
    return con


def insert_event(ev, path: Path | None = None) -> None:
    """Insert one Event (or an event-like object with the same attrs). Duplicate snapshot prints are
    IGNOREd via the UNIQUE key; near vs next-month prints stay distinct via ``expiry``."""
    row = (int(ev.epoch), ev.ts_ist, ev.ts_ist[:10], ev.symbol, ev.kind, ev.side,
           None if ev.qty is None else int(ev.qty), ev.lots, float(ev.notional), float(ev.price),
           ev.bid, ev.ask, getattr(ev, "expiry", None) or "",   # '' not NULL so the UNIQUE key dedupes
           getattr(ev, "oi", None), getattr(ev, "doi", None))
    with _connect(path) as con:
        con.execute("INSERT OR IGNORE INTO events"
                    "(epoch,ts_ist,day,symbol,kind,side,qty,lots,notional,price,bid,ask,expiry,oi,doi) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)


def upsert_futures_state(symbol, roll, expiry, oi, price, ts, path: Path | None = None) -> None:
    """Record the latest OI/price for a contract slot (symbol, roll) — feeds the rollover gauge."""
    with _connect(path) as con:
        con.execute("INSERT INTO futures_state(symbol,roll,expiry,oi,price,ts) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(symbol,roll) DO UPDATE SET expiry=excluded.expiry, oi=excluded.oi, "
                    "price=excluded.price, ts=excluded.ts",
                    (symbol, roll, expiry, oi, price, int(ts)))


def rollover(symbols: list[str] | None = None, path: Path | None = None) -> list[dict]:
    """Per-symbol rollover read from ``futures_state``: {symbol, near/next expiry+oi+price, pct_rolled}
    where pct_rolled = next_OI / (near_OI + next_OI). Rows only where a near contract is known."""
    with _connect(path) as con:
        rows = con.execute("SELECT symbol,roll,expiry,oi,price FROM futures_state").fetchall()
    by: dict[str, dict] = {}
    for sym, roll, expiry, oi, price in rows:
        if symbols and sym not in symbols:
            continue
        by.setdefault(sym, {})[roll] = {"expiry": expiry, "oi": oi, "price": price}
    out = []
    for sym, d in sorted(by.items()):
        near, nxt = d.get("near"), d.get("next")
        if not near:
            continue
        noi = (near.get("oi") or 0.0)
        xoi = (nxt.get("oi") or 0.0) if nxt else 0.0
        tot = noi + xoi
        out.append({"symbol": sym, "near": near, "next": nxt,
                    "pct_rolled": (100.0 * xoi / tot) if tot else 0.0})
    return out


def load_events(symbol: str, day: str, limit: int | None = None, path: Path | None = None) -> list[dict]:
    """Events for a symbol/day, newest first — same dict shape the old JSONL loader returned."""
    q = (f"SELECT {','.join(_COLS)} FROM events WHERE symbol=? AND day=? ORDER BY epoch DESC")
    if limit:
        q += f" LIMIT {int(limit)}"
    with _connect(path) as con:
        cur = con.execute(q, (symbol, day))
        return [dict(zip(_COLS, r)) for r in cur.fetchall()]


def daily_rollup(days: list[str] | None = None, symbols: list[str] | None = None,
                 path: Path | None = None) -> list[dict]:
    """Per (day, symbol) aggregate: event/print/burst counts, buy vs sell-initiated notional, net flow
    and lean (the directional history), and the biggest single event. Filter by ``days``/``symbols``."""
    where, params = [], []
    if days:
        where.append(f"day IN ({','.join('?' * len(days))})"); params += list(days)
    if symbols:
        where.append(f"symbol IN ({','.join('?' * len(symbols))})"); params += list(symbols)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    q = f"""
        SELECT day, symbol,
               COUNT(*)                                             AS n,
               SUM(kind='print')                                   AS n_print,
               SUM(kind='burst')                                   AS n_burst,
               SUM(CASE WHEN side='buy'  THEN notional ELSE 0 END)  AS buy_notional,
               SUM(CASE WHEN side='sell' THEN notional ELSE 0 END)  AS sell_notional,
               SUM(side='buy')                                     AS buy_n,
               SUM(side='sell')                                    AS sell_n,
               MAX(notional)                                       AS biggest
        FROM events{clause}
        GROUP BY day, symbol
        ORDER BY day, symbol
    """
    out = []
    with _connect(path) as con:
        for r in con.execute(q, params).fetchall():
            (day, sym, n, n_print, n_burst, buy, sell, buy_n, sell_n, biggest) = r
            buy, sell = buy or 0.0, sell or 0.0
            flow = buy + sell
            net = buy - sell
            out.append({"day": day, "symbol": sym, "n": n, "n_print": n_print or 0,
                        "n_burst": n_burst or 0, "buy_notional": buy, "sell_notional": sell,
                        "buy_n": buy_n or 0, "sell_n": sell_n or 0, "net_notional": net,
                        "net_pct": (100.0 * net / flow) if flow else 0.0,
                        "lean": ("BUY" if net > 0 else "SELL" if net < 0 else "flat"),
                        "biggest": biggest or 0.0})
    return out


def migrate_jsonl(path: Path | None = None) -> int:
    """One-off import of the legacy per-day JSONL event files (data/journal/big_money/<SYM>/<day>.jsonl)
    into the DB. Idempotent (INSERT OR IGNORE). Returns the number of lines processed."""
    import json
    from app.big_money import STORE_DIR
    n = 0
    if not STORE_DIR.exists():
        return 0
    for f in sorted(STORE_DIR.glob("*/*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row = (int(d["epoch"]), d["ts_ist"], d["ts_ist"][:10], d["symbol"], d["kind"],
                       d.get("side"), d.get("qty"), d.get("lots"), float(d["notional"]),
                       float(d["price"]), d.get("bid"), d.get("ask"))
                with _connect(path) as con:
                    con.execute("INSERT OR IGNORE INTO events"
                                "(epoch,ts_ist,day,symbol,kind,side,qty,lots,notional,price,bid,ask) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
                n += 1
    return n
