"""Big-money tape — a background thread that streams the Dhan feed, filters big
prints and bursts, and saves the raw tick stream. One process-wide collector.

Why save raw ticks: Dhan's HISTORICAL API bottoms out at 1-min candles (OHLC+vol+OI) with NO
tick/bid-ask history (verified in docs/big-money-tape.md), so the live websocket is the ONLY way to
build a tick+quote archive. We persist every Quote/Full tick to a per-day CSV so it can be replayed /
studied later; the filtered big-money Events go to the separate JSONL store the tape card reads.

Sign-free; decision-support; never auto-executes. Token/clientId never logged.
"""

from __future__ import annotations

import csv
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.big_money import IST, _IST_OFFSET_S, BigMoneyDetector, append_event
from data.dhan_feed import SUB_FULL, connect, decode_packet, resolve_futures

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "journal" / "big_money_raw"           # gitignored (under data/journal/)
DEFAULT_SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX")
_RAW_COLS = ["epoch", "time_ist", "symbol", "roll", "expiry", "code", "ltp", "ltq", "atp", "volume",
             "oi", "bid", "ask", "tot_buy_qty", "tot_sell_qty"]
_STATE_EVERY_S = 15.0        # throttle futures_state (OI/price) upserts per contract
_FLUSH_ROWS = 400            # flush the raw buffer past this many rows …
_FLUSH_SECS = 15.0           # … or this many seconds, whichever first


class _RawWriter:
    """Buffers decoded Quote/Full ticks and appends them to a per-day CSV (append-only, crash-safe,
    consistent with the oi_strikes journal). One file per symbol/day."""

    def __init__(self):
        self.buf: dict[tuple[str, str], list[dict]] = {}
        self.last_flush = time.time()

    def add(self, symbol: str, day: str, row: dict) -> None:
        self.buf.setdefault((symbol, day), []).append(row)

    def flush(self, force: bool = False) -> None:
        n = sum(len(v) for v in self.buf.values())
        if not force and n < _FLUSH_ROWS and (time.time() - self.last_flush) < _FLUSH_SECS:
            return
        for (symbol, day), rows in list(self.buf.items()):
            if not rows:
                continue
            p = RAW_DIR / symbol / f"{day}.csv"
            p.parent.mkdir(parents=True, exist_ok=True)
            new = not p.exists()
            with open(p, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=_RAW_COLS)
                if new:
                    w.writeheader()
                w.writerows(rows)
            self.buf[(symbol, day)] = []
        self.last_flush = time.time()


@dataclass
class Status:
    """Live health of the collector, read by the dashboard (make-state-visible)."""
    running: bool = False
    connected: bool = False
    error: str | None = None
    last_tick_epoch: int | None = None
    n_events: int = 0
    n_raw: int = 0
    per_symbol: dict = field(default_factory=dict)      # symbol → tick count (SENSEX silence is visible)
    started_at: float = field(default_factory=time.time)
    symbols: tuple = ()
    save_raw: bool = True


class Collector:
    """Streams the futures feed in a background thread: decode → save raw → filter → append Event.
    Reconnects on drop (token is 24h-capped — repaste data/.dhan_token and it resumes)."""

    def __init__(self, symbols=DEFAULT_SYMBOLS, save_raw: bool = True, persist_events: bool = True,
                 print_floor=None, burst_floor=None, on_event=None):
        self.symbols = tuple(symbols)
        self.save_raw = save_raw
        self.persist_events = persist_events          # False = don't write the JSONL (dry-run)
        self.print_floor = print_floor                # None → module defaults (per-symbol)
        self.burst_floor = burst_floor
        self.on_event = on_event                      # optional callback(Event) for live printing
        self.status = Status(symbols=self.symbols, save_raw=save_raw)
        self._stop = threading.Event()
        self._raw = _RawWriter()
        self._thread: threading.Thread | None = None

    def start(self) -> "Collector":
        self._thread = threading.Thread(target=self.run, name="big-money-collector", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        """True from the moment the thread is started until run() returns — unlike status.running,
        which is set inside the thread and races the caller's first check (GIL)."""
        return self._thread is not None and self._thread.is_alive()

    def _new_detector(self, contracts):
        self.status.per_symbol = {c["symbol"]: self.status.per_symbol.get(c["symbol"], 0)
                                  for c in contracts.values()}
        return BigMoneyDetector(contracts, print_floor=self.print_floor, burst_floor=self.burst_floor)

    def run(self) -> None:
        import websocket
        from app.big_money_db import upsert_futures_state
        self.status.running = True
        contracts = resolve_futures(self.symbols)
        while not contracts and not self._stop.is_set():    # transient network / scrip-master fail → retry,
            self.status.error = "no futures resolved (scrip master unreachable?) — retrying in 30s"
            self.status.connected = False                   # don't give up the whole session on a startup blip
            self._stop.wait(30)
            contracts = resolve_futures(self.symbols)
        if not contracts:
            self.status.running = False
            return
        detector = self._new_detector(contracts)
        resolve_day = datetime.now(IST).date()
        state_ts: dict = {}                                 # security_id -> last futures_state upsert
        while not self._stop.is_set():
            import config
            if not config.is_market_open():
                self.status.connected = False
                self.status.error = "Market closed (FnO hours: 09:15 AM – 03:40 PM IST)"
                self._stop.wait(30)
                continue

            today = datetime.now(IST).date()                # daily ROLL: re-resolve near/next contracts
            if today != resolve_day:
                nc = resolve_futures(self.symbols)
                if nc:
                    contracts, detector = nc, self._new_detector(nc)
                resolve_day = today
            next_day_check = time.time() + 60
            try:
                ws = connect(contracts, tiers=(SUB_FULL,))
            except RuntimeError as e:                       # missing token/clientId — wait, don't spin
                self.status.error = str(e)
                self.status.connected = False
                self._stop.wait(30)
                continue
            except Exception as e:                          # noqa: BLE001 - handshake/network → retry
                self.status.error = f"connect: {type(e).__name__}"
                self.status.connected = False
                self._stop.wait(5)
                continue
            self.status.connected = True
            self.status.error = None
            ws.settimeout(2.0)
            try:
                while not self._stop.is_set():
                    if not config.is_market_open():
                        self.status.connected = False
                        self.status.error = "Market closed (FnO hours: 09:15 AM – 03:40 PM IST)"
                        break
                    if time.time() > next_day_check:        # promptly reconnect+re-resolve on a new IST day
                        next_day_check = time.time() + 60
                        if datetime.now(IST).date() != resolve_day:
                            break
                    try:
                        opcode, frame = ws.recv_data()
                    except websocket.WebSocketTimeoutException:
                        self._raw.flush()
                        continue
                    if opcode == websocket.ABNF.OPCODE_CLOSE:
                        break
                    if not isinstance(frame, (bytes, bytearray)) or len(frame) < 8:
                        continue
                    tick = decode_packet(bytes(frame))
                    if tick is None or tick.code not in (4, 8) or tick.ltp is None:
                        continue
                    c = contracts.get(tick.security_id)
                    if c is None:
                        continue
                    epoch = (int(tick.ltt) - _IST_OFFSET_S) if tick.ltt else int(time.time())
                    self.status.last_tick_epoch = epoch
                    self.status.per_symbol[c["symbol"]] = self.status.per_symbol.get(c["symbol"], 0) + 1
                    if self.save_raw:
                        dt = datetime.fromtimestamp(epoch, IST)
                        self._raw.add(c["symbol"], dt.strftime("%Y-%m-%d"), {
                            "epoch": epoch, "time_ist": dt.strftime("%H:%M:%S"), "symbol": c["symbol"],
                            "roll": c.get("roll"), "expiry": c.get("expiry"),
                            "code": tick.code, "ltp": tick.ltp, "ltq": tick.ltq, "atp": tick.atp,
                            "volume": tick.volume, "oi": tick.oi, "bid": tick.bid, "ask": tick.ask,
                            "tot_buy_qty": tick.tot_buy_qty, "tot_sell_qty": tick.tot_sell_qty})
                        self.status.n_raw += 1
                    # rollover gauge: throttled per-contract OI/price snapshot → futures_state
                    if self.persist_events and tick.oi is not None:
                        sid = tick.security_id
                        if time.time() - state_ts.get(sid, 0) >= _STATE_EVERY_S:
                            state_ts[sid] = time.time()
                            try:
                                upsert_futures_state(c["symbol"], c.get("roll"), c.get("expiry"),
                                                     float(tick.oi), float(tick.ltp), epoch)
                            except Exception:  # noqa: BLE001
                                pass
                    for ev in detector.on_tick(tick):
                        if self.on_event:
                            self.on_event(ev)
                        if self.persist_events:
                            append_event(ev)
                        self.status.n_events += 1
                    self._raw.flush()
            except Exception as e:                          # noqa: BLE001 - stream error → reconnect
                self.status.error = f"stream: {type(e).__name__}"
            finally:
                self._raw.flush(force=True)
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass
                self.status.connected = False
        self.status.running = False


_LOT_FALLBACK = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}


def available_raw_days(symbol: str) -> list[str]:
    """Sorted days with a captured raw tick archive (CSV or Parquet) for a symbol."""
    d = RAW_DIR / symbol
    if not d.exists():
        return []
    return sorted({p.stem for p in d.glob("*.csv")} | {p.stem for p in d.glob("*.parquet")})


def rederive_events(symbol: str, day: str, lots: dict | None = None, upto_epoch: int | None = None):
    """Replay a captured RAW day through the CURRENT detector to regenerate events — so a detector
    improvement (e.g. the fixed conviction window) applies to already-captured days, and so REPLAY can
    reconstruct the tape as of any clock. Raw is the source of truth; this recomputes prints/bursts/
    ΔOI/conviction from it. ``upto_epoch`` replays only ticks ≤ that epoch (for replay). Returns Events."""
    import math
    from app.big_money import _IST_OFFSET_S, BigMoneyDetector
    from data.dhan_feed import Tick
    df = load_raw_ticks(symbol, day)
    if df is None or getattr(df, "empty", True):
        return []
    df = df.sort_values("epoch")
    if upto_epoch is not None:
        df = df[df["epoch"] <= upto_epoch]
    if df.empty:
        return []
    lot = (lots or {}).get(symbol) or _LOT_FALLBACK.get(symbol, 1)

    def _f(x):
        try:
            v = float(x)
            return v if v == v else None
        except (TypeError, ValueError):
            return None

    contracts, sidmap = {}, {}
    for roll, expiry in {(r.roll, r.expiry) for r in df.itertuples(index=False)}:
        if roll != roll:                                    # skip NaN roll (pre-roll raw)
            roll = "near"
        sid = abs(hash((symbol, str(roll), str(expiry)))) & 0x7FFFFFFF
        contracts[sid] = {"symbol": symbol, "roll": roll, "security_id": sid, "lot": lot,
                          "expiry": None if (isinstance(expiry, float) and math.isnan(expiry)) else expiry}
        sidmap[(roll, expiry)] = sid
    det = BigMoneyDetector(contracts)
    out = []
    for r in df.itertuples(index=False):
        roll = r.roll if r.roll == r.roll else "near"
        sid = sidmap.get((roll, r.expiry)) or sidmap.get(("near", r.expiry))
        if sid is None:
            continue
        vol = _f(r.volume)
        tick = Tick(code=int(r.code), name="", security_id=sid, ltp=_f(r.ltp), ltq=_f(r.ltq),
                    ltt=int(r.epoch) + _IST_OFFSET_S, volume=None if vol is None else int(vol),
                    oi=_f(r.oi), bid=_f(r.bid), ask=_f(r.ask))
        out.extend(det.on_tick(tick))
    return out


def load_raw_ticks(symbol: str, day: str):
    """The saved raw Quote/Full tick stream for a symbol/day (DataFrame), or None. Prefers the
    compacted Parquet (see compact_raw_day); falls back to the live append CSV."""
    import pandas as pd
    base = RAW_DIR / symbol
    pq, csv = base / f"{day}.parquet", base / f"{day}.csv"
    if pq.exists():
        return pd.read_parquet(pq)
    return pd.read_csv(csv) if csv.exists() else None


def compact_raw_day(symbol: str, day: str, keep_csv: bool = False):
    """Compact a finished day's append CSV into columnar Parquet (~5-10× smaller, faster scans) and
    remove the CSV. Idempotent + safe: if a Parquet already exists it MERGES (concat + dedupe on
    epoch/code/symbol) so a re-created CSV can't lose earlier rows. Returns the final row count, or
    None if there was nothing to compact. Do NOT compact TODAY while the collector is still appending."""
    import pandas as pd
    base = RAW_DIR / symbol
    csv, pq = base / f"{day}.csv", base / f"{day}.parquet"
    if not csv.exists():
        return None
    frames = [pd.read_csv(csv)]
    if pq.exists():
        frames.append(pd.read_parquet(pq))
    df = pd.concat(frames, ignore_index=True)
    key = [c for c in ("epoch", "code", "symbol", "expiry") if c in df.columns]   # expiry keeps near/next distinct
    df = df.drop_duplicates(subset=key).sort_values("epoch").reset_index(drop=True)
    df.to_parquet(pq, index=False)
    if not keep_csv:
        csv.unlink()
    return len(df)
