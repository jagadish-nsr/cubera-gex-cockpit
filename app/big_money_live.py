"""Big-money tape — the always-on background SERVICE ([W2]): a daemon thread that streams the Dhan
feed, filters big prints/bursts, AND saves the raw tick stream. Auto-started from the dashboard via
`streamlit.cache_resource` (one singleton across reruns/sessions).

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
_RAW_COLS = ["epoch", "time_ist", "symbol", "code", "ltp", "ltq", "atp", "volume", "oi",
             "bid", "ask", "tot_buy_qty", "tot_sell_qty"]
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

    def run(self) -> None:
        import websocket
        self.status.running = True
        contracts = resolve_futures(self.symbols)
        if not contracts:
            self.status.error = "no futures resolved from the scrip master"
            self.status.running = False
            return
        self.status.per_symbol = {c["symbol"]: 0 for c in contracts.values()}
        detector = BigMoneyDetector(contracts, print_floor=self.print_floor,
                                    burst_floor=self.burst_floor)
        while not self._stop.is_set():
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
                            "code": tick.code, "ltp": tick.ltp, "ltq": tick.ltq, "atp": tick.atp,
                            "volume": tick.volume, "oi": tick.oi, "bid": tick.bid, "ask": tick.ask,
                            "tot_buy_qty": tick.tot_buy_qty, "tot_sell_qty": tick.tot_sell_qty})
                        self.status.n_raw += 1
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


def load_raw_ticks(symbol: str, day: str):
    """The saved raw Quote/Full tick stream for a symbol/day (DataFrame), or None."""
    import pandas as pd
    p = RAW_DIR / symbol / f"{day}.csv"
    return pd.read_csv(p) if p.exists() else None
