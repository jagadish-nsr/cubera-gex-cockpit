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
_FAIL_THROTTLE_S = 60.0      # one disconnect event per minute while the socket stays down
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

# Live parity map for the cockpit / telemetry (updated on every Full tick).
# Keyed by symbol → {strike, call_ltp, put_ltp, implied, side, last_cross, epoch}.
_LIVE: dict[str, dict] = {}
_LIVE_LOCK = threading.Lock()


def live_state(symbol: str) -> dict | None:
    """Latest in-memory anchor parity for ``symbol``, or None until both legs print."""
    with _LIVE_LOCK:
        row = _LIVE.get((symbol or "").upper())
        return dict(row) if row else None


def _publish_live(symbol: str, strike: float, call_ltp: float, put_ltp: float, epoch: int) -> None:
    """Update the live map and stamp a crossover the moment (call−put) flips sign."""
    implied = float(strike) + float(call_ltp) - float(put_ltp)
    diff = float(call_ltp) - float(put_ltp)
    side = "call_rich" if diff > 0 else ("put_rich" if diff < 0 else "at_cross")
    sym = (symbol or "").upper()
    with _LIVE_LOCK:
        prev = _LIVE.get(sym) or {}
        last_cross = prev.get("last_cross")
        if prev.get("side") in ("call_rich", "put_rich") and side in ("call_rich", "put_rich") and side != prev["side"]:
            last_cross = {
                "epoch": epoch,
                "from": prev["side"],
                "to": side,
                "strike": float(strike),
                "implied": implied,
            }
        _LIVE[sym] = {
            "symbol": sym,
            "strike": float(strike),
            "call_ltp": float(call_ltp),
            "put_ltp": float(put_ltp),
            "implied": implied,
            "side": side,
            "last_cross": last_cross,
            "epoch": epoch,
        }


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


def next_roll(calls: list[float], puts: list[float], implied: float | None,
              step: float) -> tuple[str, float] | None:
    """The next strike to subscribe once spot is through an outer contract.

    ``step`` is that index's own strike step (Nifty 50, Bank Nifty 100, Sensex
    100). Returns ``("CE", strike)`` or ``("PE", strike)``, or None while spot
    is still between the subscribed strikes. The anchor pair is not moved.
    """
    if implied is None or not calls or not puts or step <= 0:
        return None
    hi = max(float(k) for k in calls)
    lo = min(float(k) for k in puts)
    if float(implied) >= hi:
        return ("CE", hi + float(step))
    if float(implied) <= lo:
        return ("PE", lo - float(step))
    return None


class BlastTickCollector:
    """Background WS capture of the blast contracts → per-day CSV.

    The anchor pair stays the strike captured at the start. When spot trades
    through the outer call or put, ``extend`` adds the next strike and the
    socket resubscribes. Reconnects on drop. A new day is a new collector.
    """

    def __init__(self, contracts: dict[int, dict], own_archive: bool = True):
        self.contracts = dict(contracts)
        self.own_archive = own_archive                       # False ⇒ never write (a second, read-only lane)
        self.status = Status()
        self.last_ltp: dict[int, float] = {}
        self.rolled: set[tuple[str, float]] = set()
        self._stop = threading.Event()
        self._reconnect = threading.Event()
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

    def extend(self, extra: dict[int, dict]) -> None:
        """Add contracts and resubscribe. The archive lock stays held."""
        if not extra:
            return
        self.contracts.update(extra)
        self._reconnect.set()

    def implied_from_anchor(self) -> float | None:
        """K + call − put from the latest anchor ticks. None until both have printed."""
        anchors = [c for c in self.contracts.values() if c.get("role") == "anchor"]
        call = next((c for c in anchors if c.get("side") == "CE"), None)
        put = next((c for c in anchors if c.get("side") == "PE"), None)
        if call is None or put is None:
            return None
        call_px = self.last_ltp.get(int(call["security_id"]))
        put_px = self.last_ltp.get(int(put["security_id"]))
        if call_px is None or put_px is None:
            return None
        return float(call["strike"]) + float(call_px) - float(put_px)

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
                if time.monotonic() - fail_note > _FAIL_THROTTLE_S:
                    self._feed("blast_ticks.disconnected", level="WARNING", ok=False, error=type(e).__name__)
                    fail_note = time.monotonic()
                self._stop.wait(30); continue
            except Exception as e:                           # noqa: BLE001 - handshake/network → retry
                self.status.error = f"connect: {type(e).__name__}"; self.status.connected = False
                if time.monotonic() - fail_note > _FAIL_THROTTLE_S:
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
                    if self._reconnect.is_set():
                        self._flush(force=True)
                        break
                    if time.monotonic() >= count_at:
                        count_at = time.monotonic() + 60.0
                        self._feed("blast_ticks.count", ticks=self.status.n_ticks)
                    try:
                        opcode, frame = ws.recv_data()
                    except websocket.WebSocketTimeoutException:
                        self._flush()
                        if self._reconnect.is_set():
                            break
                        continue
                    if opcode == websocket.ABNF.OPCODE_CLOSE:
                        break
                    if not isinstance(frame, (bytes, bytearray)) or len(frame) < 8:
                        continue
                    tick = decode_packet(bytes(frame))
                    if tick is None or tick.code not in (4, 8) or tick.ltp is None:
                        continue
                    c = self.contracts.get(tick.security_id)
                    if c is not None:
                        self.last_ltp[int(tick.security_id)] = float(tick.ltp)
                    if c is None:
                        continue
                    epoch = (int(tick.ltt) - _IST_OFFSET_S) if tick.ltt else int(time.time())
                    self.status.last_tick_epoch = epoch
                    self.status.n_ticks += 1
                    self.status.per_side[c["side"]] = self.status.per_side.get(c["side"], 0) + 1
                    # Live parity / crossover for the cockpit (anchor CE+PE only).
                    if c.get("role", "anchor") == "anchor":
                        implied = self.implied_from_anchor()
                        if implied is not None:
                            anchors = [x for x in self.contracts.values() if x.get("role") == "anchor"]
                            call = next((x for x in anchors if x.get("side") == "CE"), None)
                            put = next((x for x in anchors if x.get("side") == "PE"), None)
                            if call and put:
                                cl = self.last_ltp.get(int(call["security_id"]))
                                pl = self.last_ltp.get(int(put["security_id"]))
                                if cl is not None and pl is not None:
                                    prev = live_state(c["symbol"])
                                    _publish_live(c["symbol"], float(call["strike"]), float(cl), float(pl), epoch)
                                    now_live = live_state(c["symbol"]) or {}
                                    if (now_live.get("last_cross") and (
                                        not prev or prev.get("last_cross") != now_live.get("last_cross")
                                    )):
                                        try:
                                            from app.obs import event
                                            event(
                                                "blast_ticks.crossover",
                                                workflow="blast_ticks",
                                                symbol=c["symbol"],
                                                **(now_live.get("last_cross") or {}),
                                            )
                                        except Exception:
                                            pass
                    dt = datetime.fromtimestamp(epoch, IST)
                    self._add(c["symbol"], dt.strftime("%Y-%m-%d"), tick_row(tick, c, epoch))
                    self._flush()
            except Exception as e:  # noqa: BLE001 - never let one bad frame kill the capture
                self.status.error = f"recv: {type(e).__name__}"
            finally:
                rolling = self._reconnect.is_set()
                if rolling:
                    self._reconnect.clear()
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass
                self._flush(force=True)
                if linked and not rolling:
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
