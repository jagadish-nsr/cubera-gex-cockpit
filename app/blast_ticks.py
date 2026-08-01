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

from data.dhan_feed import SUB_FULL, connect, decode_packet, resolve_blast_pair

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "journal" / "blast_ticks"          # gitignored (under data/journal/)
IST = timezone(timedelta(hours=5, minutes=30))
_IST_OFFSET_S = int(timedelta(hours=5, minutes=30).total_seconds())
_FLUSH_ROWS, _FLUSH_SECS = 200, 5.0
_COLS = ["epoch", "time_ist", "symbol", "side", "strike", "expiry", "code",
         "ltp", "ltq", "atp", "volume", "oi", "bid", "ask"]


def pair_contracts(pair: dict) -> dict[int, dict]:
    """resolve_blast_pair(...) → {security_id: contract} keyed for connect() + tick lookup. Each contract
    carries symbol/side/strike/expiry/segment so an archived tick is self-describing. {} if pair is None."""
    if not pair:
        return {}
    out = {}
    for leg in ("call", "put"):
        c = pair.get(leg)
        if c:
            out[int(c["security_id"])] = {
                "symbol": pair["symbol"], "side": c["side"], "strike": pair["strike"],
                "expiry": pair["expiry"], "segment": c["segment"], "security_id": int(c["security_id"])}
    return out


def tick_row(tick, c: dict, epoch: int) -> dict:
    """One archive row from a decoded Tick + its contract meta."""
    dt = datetime.fromtimestamp(epoch, IST)
    return {"epoch": epoch, "time_ist": dt.strftime("%H:%M:%S"), "symbol": c["symbol"],
            "side": c["side"], "strike": c["strike"], "expiry": c["expiry"], "code": tick.code,
            "ltp": tick.ltp, "ltq": tick.ltq, "atp": tick.atp, "volume": tick.volume,
            "oi": tick.oi, "bid": tick.bid, "ask": tick.ask}


@dataclass
class Status:
    running: bool = False
    connected: bool = False
    error: str | None = None
    n_ticks: int = 0
    last_tick_epoch: int | None = None
    per_side: dict = field(default_factory=dict)             # 'CE'/'PE' → tick count
    strike: float | None = None
    expiry: str | None = None
    started_at: float = field(default_factory=time.time)


class BlastTickCollector:
    """Background WS capture of a fixed CALL+PUT pair → per-day CSV. Reconnects on drop (token is 24h-
    capped). Give it a ``contracts`` dict from ``pair_contracts`` — the pair is fixed for the session
    (the blast freezes its ATM strike), so no re-resolve loop; a new day = a new collector."""

    def __init__(self, contracts: dict[int, dict]):
        self.contracts = contracts
        self.status = Status()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._buf: list[tuple[str, str, dict]] = []          # (symbol, day, row)
        self._last_flush = time.time()
        if contracts:
            any_c = next(iter(contracts.values()))
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
        self._buf.clear()
        self._last_flush = time.time()

    def run(self) -> None:
        import websocket
        self.status.running = True
        if not self.contracts:
            self.status.error = "no blast pair to subscribe"
            self.status.running = False
            return
        while not self._stop.is_set():
            try:
                ws = connect(self.contracts, tiers=(SUB_FULL,))
            except RuntimeError as e:                        # missing token/clientId
                self.status.error = str(e); self.status.connected = False
                self._stop.wait(30); continue
            except Exception as e:                           # noqa: BLE001 - handshake/network → retry
                self.status.error = f"connect: {type(e).__name__}"; self.status.connected = False
                self._stop.wait(5); continue
            self.status.connected = True
            self.status.error = None
            ws.settimeout(2.0)
            try:
                while not self._stop.is_set():
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
            self.status.connected = False
        self._flush(force=True)
        self.status.running = False


def resolve_and_capture(symbol: str, spot: float, expiry: str | None = None) -> BlastTickCollector | None:
    """Convenience: resolve the ATM blast pair at ``spot`` and start capturing. None if it can't resolve."""
    pair = resolve_blast_pair(symbol, spot, expiry)
    contracts = pair_contracts(pair)
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
