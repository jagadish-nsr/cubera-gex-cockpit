"""Big-money futures tape — the pure filter + event model for [W2] (docs/big-money-tape.md).

Consumes decoded `data.dhan_feed.Tick`s and emits an Event when activity is "big":
  • PRINT  — a single trade whose ₹ notional ≥ a per-symbol floor (one fat block crossing the book).
  • BURST  — cumulative traded notional over a trailing K-sec window ≥ a floor (orders sliced into
             many small clips that a single-print filter would miss).

Honest limits (the landmine): the feed is aggregated broadcast — no orders, no identity. A PRINT is
one exchange print; a BURST is flow, not one order. Direction is only ever the **aggressor side**
(Lee-Ready: traded at/above ask = buy-initiated, at/below bid = sell-initiated), and only on the Full
tier that carries bid/ask. Reported as flow, never a prediction. Sign-free; decision-support only.

notional = price × qty  (qty is already shares-equivalent, i.e. lots × lot_size, so lot_size does
NOT multiply in again — verified live: LTQ 65 = 1 NIFTY lot). lot_size is only for the "N lots" label.

Floors are NAMED, LOGGED constants to be CALIBRATED on the day-1 dry-run tape, not trusted as-is.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = ROOT / "data" / "journal" / "big_money"     # gitignored (under data/journal/)

# ₹ floors — PLACEHOLDER defaults, CALIBRATE on real tape. 1 NIFTY lot ≈ ₹15.7L, 1 BankNifty ≈ ₹17L,
# so ₹1cr single print ≈ 6–7 lots. Burst floor is over a short window, so set it higher.
BIG_PRINT_FLOOR = {"NIFTY": 1_00_00_000.0, "BANKNIFTY": 1_00_00_000.0}      # ₹1 crore single print
BURST_FLOOR = {"NIFTY": 3_00_00_000.0, "BANKNIFTY": 3_00_00_000.0}         # ₹3 crore per window
BURST_WINDOW_S = 10.0
_DEFAULT_FLOOR = 1_00_00_000.0
IST = timezone(timedelta(hours=5, minutes=30))
# Dhan's LTT is IST wall-clock expressed as a unix epoch (i.e. already +5:30), so we subtract the
# offset to recover a TRUE UTC epoch before rendering — otherwise the shown time is +5:30 too late.
_IST_OFFSET_S = 5 * 3600 + 30 * 60


def notional(price: float | None, qty: int | None) -> float:
    """₹ notional of a trade of ``qty`` shares-equivalent at ``price`` (0 if either is missing)."""
    if not price or not qty:
        return 0.0
    return float(price) * float(qty)


def lots(qty: int | None, lot_size: int | None) -> float | None:
    """``qty`` expressed in lots (None if lot_size unknown)."""
    if not qty or not lot_size:
        return None
    return float(qty) / float(lot_size)


def classify_aggressor(ltp: float | None, bid: float | None, ask: float | None) -> str | None:
    """Lee-Ready aggressor side from the trade price vs the touch: at/above ask ⇒ 'buy'-initiated,
    at/below bid ⇒ 'sell'-initiated, strictly inside the spread ⇒ None (indeterminate). None if the
    quote is missing (Ticker/Quote tiers carry no bid/ask). Sign-free flow read, never a prediction."""
    if ltp is None or bid is None or ask is None or ask <= 0 or bid <= 0:
        return None
    if ltp >= ask:
        return "buy"
    if ltp <= bid:
        return "sell"
    return None


@dataclass
class Event:
    """One qualifying big-money event, ready to store/render."""
    epoch: int
    ts_ist: str                         # 'YYYY-MM-DD HH:MM:SS' (exchange time when available)
    symbol: str
    kind: str                           # 'print' | 'burst'
    side: str | None                    # 'buy' | 'sell' | None (aggressor; None for burst/mid)
    qty: int
    lots: float | None
    notional: float                     # ₹
    price: float
    bid: float | None = None
    ask: float | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))


def _ts(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=IST).strftime("%Y-%m-%d %H:%M:%S")


class _BurstTracker:
    """Per-instrument trailing-window traded-notional accumulator with rising-edge firing (so a
    sustained burst fires ONCE, not every packet). Fed the cumulative day ``volume`` per packet;
    the positive delta between packets is the traded qty in that gap."""

    def __init__(self, window_s: float, floor: float):
        self.window_s, self.floor = window_s, floor
        self.buf: deque[tuple[float, float]] = deque()      # (epoch, dvol*price)
        self.last_vol: int | None = None
        self.active = False

    def update(self, epoch: float, volume: int, price: float) -> tuple[bool, float, int]:
        dvol = 0
        if self.last_vol is not None and volume >= self.last_vol:
            dvol = volume - self.last_vol
        self.last_vol = volume                              # (volume < last ⇒ day reset/reconnect → dvol 0)
        if dvol > 0 and price:
            self.buf.append((epoch, dvol * price))
        cutoff = epoch - self.window_s
        while self.buf and self.buf[0][0] < cutoff:
            self.buf.popleft()
        wnot = sum(v for _, v in self.buf)
        wqty = int(round(wnot / price)) if price else 0
        fired = wnot >= self.floor and not self.active      # rising edge only
        self.active = wnot >= self.floor
        return fired, wnot, wqty


class BigMoneyDetector:
    """Stateful detector over a set of contracts (id → {symbol, lot, …} from
    `data.dhan_feed.resolve_futures`). `on_tick` returns 0+ Events for one decoded Tick."""

    def __init__(self, contracts: dict[int, dict], print_floor=None, burst_floor=None,
                 burst_window_s: float = BURST_WINDOW_S):
        self.contracts = contracts
        self.print_floor = print_floor or BIG_PRINT_FLOOR
        self.burst_floor = burst_floor or BURST_FLOOR
        self._burst = {sid: _BurstTracker(burst_window_s, self.burst_floor.get(c["symbol"], _DEFAULT_FLOOR))
                       for sid, c in contracts.items()}

    def _floor(self, table, symbol):
        return table.get(symbol, _DEFAULT_FLOOR)

    def on_tick(self, tick) -> list[Event]:
        c = self.contracts.get(getattr(tick, "security_id", None))
        if c is None or tick.ltp is None:
            return []
        sym, lot, price = c["symbol"], c.get("lot"), float(tick.ltp)
        import time as _time
        # Dhan LTT is IST-wall-as-epoch → normalise to a true epoch (so _ts renders correct IST).
        epoch = (int(tick.ltt) - _IST_OFFSET_S) if getattr(tick, "ltt", None) else int(_time.time())
        out: list[Event] = []

        # PRINT — a single large trade
        if tick.ltq and tick.ltq > 0:
            nden = notional(price, tick.ltq)
            if nden >= self._floor(self.print_floor, sym):
                out.append(Event(epoch=epoch, ts_ist=_ts(epoch), symbol=sym, kind="print",
                                 side=classify_aggressor(price, tick.bid, tick.ask),
                                 qty=int(tick.ltq), lots=lots(tick.ltq, lot), notional=nden,
                                 price=price, bid=tick.bid, ask=tick.ask))

        # BURST — sliced flow over the trailing window
        if tick.volume is not None:
            fired, wnot, wqty = self._burst[c["security_id"]].update(epoch, int(tick.volume), price)
            if fired:
                out.append(Event(epoch=epoch, ts_ist=_ts(epoch), symbol=sym, kind="burst",
                                 side=None, qty=wqty, lots=lots(wqty, lot), notional=wnot,
                                 price=price, bid=tick.bid, ask=tick.ask))
        return out


# --- event store (append-only JSONL per symbol/day; gitignored under data/journal/) --------------

def event_path(symbol: str, day: str) -> Path:
    return STORE_DIR / symbol / f"{day}.jsonl"


def append_event(ev: Event) -> None:
    p = event_path(ev.symbol, ev.ts_ist[:10])
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(ev.to_json() + "\n")


def load_events(symbol: str, day: str, limit: int | None = None) -> list[dict]:
    """Recent events for a symbol/day, newest first (empty if none). ``limit`` caps the tail."""
    p = event_path(symbol, day)
    if not p.exists():
        return []
    rows = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    rows.reverse()
    return rows[:limit] if limit else rows
