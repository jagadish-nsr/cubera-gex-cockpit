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
# 1 SENSEX ≈ ₹16L, so ₹1cr single print ≈ 6–7 lots. Burst floor is over a short window, so set higher.
BIG_PRINT_FLOOR = {"NIFTY": 1_00_00_000.0, "BANKNIFTY": 1_00_00_000.0, "SENSEX": 1_00_00_000.0}
BURST_FLOOR = {"NIFTY": 3_00_00_000.0, "BANKNIFTY": 3_00_00_000.0, "SENSEX": 3_00_00_000.0}
BURST_WINDOW_S = 10.0
# ΔOI / conviction use a LONGER window than the 10s volume burst: futures OI updates only ~every 60s
# (measured), so a 10s window rarely spans an update → ΔOI≈0. 180s (~3 updates) makes it meaningful.
POS_WINDOW_S = 180.0
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


_QUAD = {(True, True): "long build-up", (False, True): "short covering",
         (True, False): "short build-up", (False, False): "long unwinding"}


def buildup(direction, doi, tol: float = 0.0) -> str | None:
    """The four-quadrant futures build-up from a price/flow DIRECTION (>0 = up / buy-initiated,
    <0 = down / sell-initiated) and ΔOI (>0 = new positions). None if either is missing or ~flat
    (churn — volume without net positioning). price↑OI↑=long build-up, price↓OI↑=short build-up,
    price↑OI↓=short covering, price↓OI↓=long unwinding."""
    if doi is None or direction is None or direction == 0 or abs(doi) <= tol:
        return None
    return _QUAD[(doi > 0, direction > 0)]


def conviction(doi, qty) -> float | None:
    """ΔOI / Δvolume over the window — the share of the traded volume that became NET new positioning
    rather than churn. ≈ +1 = heavy position-building, ≈ −1 = heavy unwinding, ≈ 0 = churn / day-trading
    (lots traded, no net OI change). None if either input is missing/zero. Most meaningful on BURSTS,
    where ``qty`` is the window volume and ``doi`` the window ΔOI (same scope)."""
    if doi is None or not qty:
        return None
    return doi / qty


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
    expiry: str | None = None           # contract expiry for a print; 'AGG' for a symbol-level burst
    oi: float | None = None             # symbol total open interest (near+next) at event time
    doi: float | None = None            # ΔOI over the positioning window — new positioning (+) vs unwind (−)
    conv: float | None = None           # conviction = ΔOI / Δvolume over the window (positioning vs churn)
    dpx: float | None = None            # price move over the positioning window (pairs w/ doi → build-up quadrant)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


def _ts(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=IST).strftime("%Y-%m-%d %H:%M:%S")


class _SymbolBurstTracker:
    """Per-SYMBOL trailing-window traded-notional accumulator with rising-edge firing. Fed each of the
    symbol's contracts' cumulative ``volume`` (near + next month); it keeps per-contract ``last_vol`` to
    compute each contract's own delta, then sums both into ONE symbol window — so a burst reflects total
    futures flow across the roll, not a single contract. Sustained bursts fire ONCE."""

    def __init__(self, window_s: float, floor: float, pos_window_s: float = POS_WINDOW_S):
        self.window_s, self.floor, self.pos_window_s = window_s, floor, pos_window_s
        self.buf: deque[tuple[float, float]] = deque()      # (epoch, dvol*price) — 10s burst-firing window
        self.last_vol: dict = {}                            # security_id -> last cumulative volume
        self.pos: dict = {}                                 # security_id -> deque[(epoch, volume, oi)] over pos_window
        self.active = False

    def update(self, sec_id, epoch: float, volume: int, price: float,
               oi: float | None = None) -> tuple:
        """Returns (fired, window_notional, window_qty, total_oi, doi, conv, dpx). ``doi`` = ΔOI over the
        POSITIONING window (pos_window_s, longer than the 10s burst window because OI updates slowly);
        ``conv`` = ΔOI / Δvolume over that same window (share of volume that became net positioning);
        ``dpx`` = this contract's price move over that same window (pairs with ``doi`` for the four-quadrant
        build-up: price↑OI↑ long build-up, price↓OI↑ writing, price↑OI↓ short covering, price↓OI↓ unwinding).
        total_oi = sum of the latest OI across the symbol's contracts."""
        prev = self.last_vol.get(sec_id)
        dvol = (volume - prev) if (prev is not None and volume >= prev) else 0
        self.last_vol[sec_id] = volume                      # (volume < prev ⇒ day reset/reconnect → 0)
        if dvol > 0 and price:
            self.buf.append((epoch, dvol * price))
        cutoff = epoch - self.window_s
        while self.buf and self.buf[0][0] < cutoff:
            self.buf.popleft()
        # positioning window: track (volume, oi, price) per contract over the longer window
        pb = self.pos.setdefault(sec_id, deque())
        pb.append((epoch, float(volume), oi, price))
        pcut = epoch - self.pos_window_s
        while pb and pb[0][0] < pcut:
            pb.popleft()
        oi_now = [p[-1][2] for p in self.pos.values() if p and p[-1][2] is not None]
        tot_oi = sum(oi_now) if oi_now else None
        doi_acc, dvol_acc, have_oi = 0.0, 0.0, False
        for p in self.pos.values():
            ois = [o for (_, _, o, _px) in p if o is not None]
            if len(ois) >= 2:
                doi_acc += ois[-1] - ois[0]
                have_oi = True
            if len(p) >= 2:
                dvol_acc += p[-1][1] - p[0][1]
        doi = doi_acc if have_oi else None
        conv = (doi / dvol_acc) if (doi is not None and dvol_acc) else None
        pxs = [px for (_, _, _o, px) in pb if px]           # this contract's price track over the window
        dpx = (pxs[-1] - pxs[0]) if len(pxs) >= 2 else None
        wnot = sum(v for _, v in self.buf)
        wqty = int(round(wnot / price)) if price else 0
        fired = wnot >= self.floor and not self.active      # rising edge only
        self.active = wnot >= self.floor
        return fired, wnot, wqty, tot_oi, doi, conv, dpx


class BigMoneyDetector:
    """Stateful detector over a set of contracts (id → {symbol, roll, lot, expiry, …} from
    `data.dhan_feed.resolve_futures`; near + next month per symbol). `on_tick` returns 0+ Events for one
    decoded Tick. PRINTS are per-contract (tagged with the contract expiry); BURSTS aggregate the
    symbol's contracts into one window (tagged 'AGG')."""

    def __init__(self, contracts: dict[int, dict], print_floor=None, burst_floor=None,
                 burst_window_s: float = BURST_WINDOW_S):
        self.contracts = contracts
        self.print_floor = print_floor or BIG_PRINT_FLOOR
        self.burst_floor = burst_floor or BURST_FLOOR
        syms = {c["symbol"] for c in contracts.values()}
        self._burst = {sym: _SymbolBurstTracker(burst_window_s, self.burst_floor.get(sym, _DEFAULT_FLOOR))
                       for sym in syms}

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

        # update the symbol's window tracker first (volume + OI), so BOTH prints and bursts can carry
        # the current total OI and the window ΔOI (new positioning vs churn/unwind).
        fired = False
        tot_oi = doi = conv = dpx = None
        wnot = wqty = 0
        if tick.volume is not None:
            fired, wnot, wqty, tot_oi, doi, conv, dpx = self._burst[sym].update(
                c["security_id"], epoch, int(tick.volume), price, getattr(tick, "oi", None))

        # PRINT — a single large trade on THIS contract (tagged with its expiry)
        if tick.ltq and tick.ltq > 0:
            nden = notional(price, tick.ltq)
            if nden >= self._floor(self.print_floor, sym):
                out.append(Event(epoch=epoch, ts_ist=_ts(epoch), symbol=sym, kind="print",
                                 side=classify_aggressor(price, tick.bid, tick.ask),
                                 qty=int(tick.ltq), lots=lots(tick.ltq, lot), notional=nden,
                                 price=price, bid=tick.bid, ask=tick.ask, expiry=c.get("expiry"),
                                 oi=tot_oi, doi=doi, conv=conv, dpx=dpx))

        # BURST — sliced flow over the trailing window, aggregated across the symbol's contracts
        if fired:
            out.append(Event(epoch=epoch, ts_ist=_ts(epoch), symbol=sym, kind="burst",
                             side=None, qty=wqty, lots=lots(wqty, lot), notional=wnot,
                             price=price, bid=tick.bid, ask=tick.ask, expiry="AGG",
                             oi=tot_oi, doi=doi, conv=conv, dpx=dpx))
        return out


# --- event store (SQLite; see app/big_money_db.py) ------------------------------------------------
# STORE_DIR / event_path name the LEGACY per-day JSONL layout, kept only so big_money_db.migrate_jsonl
# can import old files. New reads/writes go to the SQLite DB (sparse, queryable directional history).

def event_path(symbol: str, day: str) -> Path:
    return STORE_DIR / symbol / f"{day}.jsonl"


def append_event(ev: Event) -> None:
    from app import big_money_db
    big_money_db.insert_event(ev)


def load_events(symbol: str, day: str, limit: int | None = None) -> list[dict]:
    """Recent events for a symbol/day, newest first (empty if none). ``limit`` caps the tail."""
    from app import big_money_db
    return big_money_db.load_events(symbol, day, limit)
