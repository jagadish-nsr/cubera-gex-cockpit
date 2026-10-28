"""Real-time tick capture for the gamma-blast anchor strikes (P0 of the blast-realtime effort).

On an expiry day the blast fixes an ATM strike near 13:45 and watches its CALL vs PUT. This module
subscribes those two option contracts on the Dhan WS Market-Feed (the same live feed that drives the
big-money futures tape) and archives their ticks — LTP, bid/ask, volume, OI — to a per-day CSV. That
gives the blast a tick-resolution premium + fuel lane instead of the 1–5 min option-chain REST cadence,
and a captured record to backtest the WS-timed crossover against the old 10s-spot cross.

Sign-free; decision-support; the token/clientId are never logged (see data.dhan_feed). No auto-exec.
Mirrors app/big_money_live.py's collector, minus the detector — here we only capture the two strikes.
"""
from __future__ import annotations

import csv
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.dhan_feed import SUB_FULL, connect, decode_packet, resolve_blast_pair, resolve_blast_set

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "journal" / "blast_ticks"          # gitignored (under data/journal/)
IST = timezone(timedelta(hours=5, minutes=30))
_IST_OFFSET_S = int(timedelta(hours=5, minutes=30).total_seconds())
_FLUSH_ROWS, _FLUSH_SECS = 200, 5.0
_COLS = ["epoch", "time_ist", "symbol", "side", "strike", "role", "expiry", "code",
         "ltp", "ltq", "atp", "volume", "oi", "bid", "ask"]


def pair_contracts(pair: dict) -> dict[int, dict]:
    """resolve_blast_pair(...) → {security_id: contract} keyed for connect() + tick lookup. Each contract
    carries symbol/side/strike/role/expiry/segment so an archived tick is self-describing. {} if None."""
    if not pair:
        return {}
    out = {}
    for leg in ("call", "put"):
        c = pair.get(leg)
        if c:
            out[int(c["security_id"])] = {
                "symbol": pair["symbol"], "side": c["side"], "strike": pair["strike"],
                "role": c.get("role", "anchor"), "expiry": pair["expiry"], "segment": c["segment"],
                "security_id": int(c["security_id"])}
    return out


def set_contracts(bset: dict) -> dict[int, dict]:
    """resolve_blast_set(...) → {security_id: contract} for the anchor pair + the two walls (each at its
    OWN strike). Skips a wall that's None (grid edge). {} if the set is None."""
    if not bset:
        return {}
    out = {}
    for leg in ("call", "put", "wall_up", "wall_dn"):
        c = bset.get(leg)
        if c:
            out[int(c["security_id"])] = {
                "symbol": bset["symbol"], "side": c["side"], "strike": c["strike"],
                "role": c.get("role", "anchor"), "expiry": bset["expiry"], "segment": c["segment"],
                "security_id": int(c["security_id"])}
    return out


_LOCK_TTL = 90.0            # a lock older than this (no flush) = the writer is dead → takeable


def _lock_path(symbol: str, day: str):
    return RAW_DIR / symbol / f"{day}.lock"


def archive_locked_fresh(symbol: str, day: str) -> bool:
    """Is another process currently writing today's archive? (lock file touched within _LOCK_TTL.) The
    dashboard checks this before auto-starting, so it defers to the scheduled driver instead of double-
    writing the same CSV."""
    import os
    p = _lock_path(symbol, day)
    try:
        return (time.time() - os.path.getmtime(p)) < _LOCK_TTL
    except OSError:
        return False


def _acquire_lock(symbol: str, day: str) -> bool:
    """Claim single-writer ownership of today's archive. Atomic O_EXCL create; takes over a STALE lock
    (dead writer). False if a fresh lock is held by someone else."""
    import os
    p = _lock_path(symbol, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(p) > _LOCK_TTL:     # stale → the old writer is gone
                os.unlink(p)
                return _acquire_lock(symbol, day)
        except OSError:
            pass
        return False


def tick_row(tick, c: dict, epoch: int) -> dict:
    """One archive row from a decoded Tick + its contract meta."""
    dt = datetime.fromtimestamp(epoch, IST)
    return {"epoch": epoch, "time_ist": dt.strftime("%H:%M:%S"), "symbol": c["symbol"],
            "side": c["side"], "strike": c["strike"], "role": c.get("role", "anchor"),
            "expiry": c["expiry"], "code": tick.code, "ltp": tick.ltp, "ltq": tick.ltq,
            "atp": tick.atp, "volume": tick.volume, "oi": tick.oi, "bid": tick.bid, "ask": tick.ask}


@dataclass
class Status:
    running: bool = False
    connected: bool = False
    error: str | None = None
    n_ticks: int = 0
    last_tick_epoch: int | None = None
    per_side: dict = field(default_factory=dict)             # 'CE'/'PE' → tick count
    symbol: str | None = None
    strike: float | None = None
    expiry: str | None = None
    read_only: bool = False                                  # couldn't get the write-lock → not archiving
    started_at: float = field(default_factory=time.time)


class BlastTickCollector:
    """Background WS capture of a fixed CALL+PUT pair → per-day CSV. Reconnects on drop (token is 24h-
    capped). Give it a ``contracts`` dict from ``pair_contracts`` — the pair is fixed for the session
    (the blast freezes its ATM strike), so no re-resolve loop; a new day = a new collector."""

    def __init__(self, contracts: dict[int, dict], own_archive: bool = True):
        self.contracts = contracts
        self.own_archive = own_archive                       # False ⇒ never write (a second, read-only lane)
        self.status = Status()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._buf: list[tuple[str, str, dict]] = []          # (symbol, day, row)
        self._last_flush = time.time()
        self._lock_key: tuple[str, str] | None = None        # (symbol, day) we hold the write-lock for
        if contracts:
            any_c = next(iter(contracts.values()))
            self.status.symbol = any_c["symbol"]
            self.status.strike, self.status.expiry = any_c["strike"], any_c["expiry"]

    def start(self) -> "BlastTickCollector":
        self._thread = threading.Thread(target=self.run, name="blast-ticks", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _add(self, symbol: str, day: str, row: dict) -> None:
        self._buf.append((symbol, day, row))

    def _flush(self, force: bool = False) -> None:
        if self.status.read_only:                            # not the archive owner → drop the buffer
            self._buf.clear(); return
        if not force and len(self._buf) < _FLUSH_ROWS and (time.time() - self._last_flush) < _FLUSH_SECS:
            return
        by: dict[tuple[str, str], list[dict]] = {}
        for sym, day, row in self._buf:
            by.setdefault((sym, day), []).append(row)
        for (sym, day), rows in by.items():
            p = RAW_DIR / sym / f"{day}.csv"
            p.parent.mkdir(parents=True, exist_ok=True)
            new = not p.exists()
            with open(p, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=_COLS)
                if new:
                    w.writeheader()
                w.writerows(rows)
            if self._lock_key == (sym, day):                 # refresh the write-lock so it stays fresh
                import os
                try:
                    os.utime(_lock_path(sym, day), None)
                except OSError:
                    pass
        self._buf.clear()
        self._last_flush = time.time()

    def _feed(self, name: str, **fields) -> None:
        try:
            from app.obs import event
            event(name, workflow="blast_ticks", symbol=self.status.symbol, **fields)
        except Exception:
            return

    def run(self) -> None:
        import websocket
        self.status.running = True
        ever_up = False
        linked = False
        fail_note = 0.0
        count_at = time.monotonic() + 60.0
        if not self.contracts:
            self.status.error = "no blast pair to subscribe"
            self._feed("blast_ticks.stopped", ok=False, error=self.status.error)
            self.status.running = False
            return
        if self.own_archive:                                 # claim single-writer ownership of the archive
            c = next(iter(self.contracts.values()))
            day = datetime.now(IST).strftime("%Y-%m-%d")
            if _acquire_lock(c["symbol"], day):
                self._lock_key = (c["symbol"], day)
            else:                                            # another writer owns today's archive → no-op
                self.status.read_only = True
                self.status.error = "archive owned by another writer (scheduled driver?) — not capturing"
                self._feed("blast_ticks.stopped", ok=False, error=self.status.error)
                self.status.running = False
                return
        while not self._stop.is_set():
            try:
                ws = connect(self.contracts, tiers=(SUB_FULL,))
            except RuntimeError as e:                        # missing token/clientId
                self.status.error = str(e); self.status.connected = False
                if time.monotonic() - fail_note > 60:
                    self._feed("blast_ticks.disconnected", level="WARNING", ok=False, error=type(e).__name__)
                    fail_note = time.monotonic()
                self._stop.wait(30); continue
            except Exception as e:                           # noqa: BLE001 - handshake/network → retry
                self.status.error = f"connect: {type(e).__name__}"; self.status.connected = False
                if time.monotonic() - fail_note > 60:
                    self._feed("blast_ticks.disconnected", level="WARNING", ok=False, error=type(e).__name__)
                    fail_note = time.monotonic()
                self._stop.wait(5); continue
            self.status.connected = True
            self.status.error = None
            self._feed("blast_ticks.reconnected" if ever_up else "blast_ticks.connected")
            ever_up = True
            linked = True
            ws.settimeout(2.0)
            try:
                while not self._stop.is_set():
                    if time.monotonic() >= count_at:
                        count_at = time.monotonic() + 60.0
                        self._feed("blast_ticks.count", ticks=self.status.n_ticks)
                    try:
                        opcode, frame = ws.recv_data()
                    except websocket.WebSocketTimeoutException:
                        self._flush(); continue
                    if opcode == websocket.ABNF.OPCODE_CLOSE:
                        break
                    if not isinstance(frame, (bytes, bytearray)) or len(frame) < 8:
                        continue
                    tick = decode_packet(bytes(frame))
                    if tick is None or tick.code not in (4, 8) or tick.ltp is None:
                        continue
                    c = self.contracts.get(tick.security_id)
                    if c is None:
                        continue
                    epoch = (int(tick.ltt) - _IST_OFFSET_S) if tick.ltt else int(time.time())
                    self.status.last_tick_epoch = epoch
                    self.status.n_ticks += 1
                    self.status.per_side[c["side"]] = self.status.per_side.get(c["side"], 0) + 1
                    dt = datetime.fromtimestamp(epoch, IST)
                    self._add(c["symbol"], dt.strftime("%Y-%m-%d"), tick_row(tick, c, epoch))
                    self._flush()
            except Exception as e:  # noqa: BLE001 - never let one bad frame kill the capture
                self.status.error = f"recv: {type(e).__name__}"
            finally:
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass
                self._flush(force=True)
                if linked:
                    self._feed("blast_ticks.disconnected", level="WARNING", ok=False, error=self.status.error or "socket closed")
                    linked = False
            self.status.connected = False
        self._flush(force=True)
        if self._lock_key:                                   # release our write-lock so tomorrow can claim
            import os
            try:
                os.unlink(_lock_path(*self._lock_key))
            except OSError:
                pass
            self._lock_key = None
        self._feed("blast_ticks.stopped", ticks=self.status.n_ticks)
        self.status.running = False


def resolve_and_capture(symbol: str, spot: float, expiry: str | None = None,
                        walls: bool = True) -> BlastTickCollector | None:
    """Convenience: resolve the ATM anchor (+ the two continuation walls when ``walls``) at ``spot`` and
    start capturing. None if it can't resolve."""
    if walls:
        contracts = set_contracts(resolve_blast_set(symbol, spot, expiry))
    else:
        contracts = pair_contracts(resolve_blast_pair(symbol, spot, expiry))
    if not contracts:
        return None
    return BlastTickCollector(contracts).start()


def available_blast_days(symbol: str) -> list[str]:
    d = RAW_DIR / symbol
    return sorted(p.stem for p in d.glob("*.csv")) if d.exists() else []


def load_blast_ticks(symbol: str, day: str):
    """The captured CALL+PUT tick stream for a symbol/day (DataFrame), or None."""
    import pandas as pd
    p = RAW_DIR / symbol / f"{day}.csv"
    return pd.read_csv(p) if p.exists() else None
