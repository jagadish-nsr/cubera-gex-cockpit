# Big-money futures tape ([W2])

A live **large-trade / unusual-activity tape** for the NIFTY + BankNifty **current-expiry futures**,
off the Dhan v2 Market-Feed websocket. When a trade (or a short burst of trades) is unusually large,
it's stamped with the exchange time and shown on the dashboard.

## Read this first — what it can and cannot be

Retail feeds carry **aggregated exchange broadcast**, so:

- **No orders, no identity.** You cannot see "an FII bought 5,000 lots." You see the **last traded
  quantity (LTQ)** per print, running **volume**, **OI**, and the **best bid/ask** — nothing about who
  traded or their resting orders. So this is an **inference** of big activity from large *prints* and
  *bursts*, **not** literal order tracking.
- **Direction is ambiguous.** A ₹cr sell could be a hedge, not a bearish view. The only honest
  direction is the **aggressor side** (Lee-Ready: a trade at/above the ask is buy-initiated, at/below
  the bid is sell-initiated; inside the spread is indeterminate). We report **flow, never a
  prediction** — the same sign-free discipline as the dealer-sign landmine (see CLAUDE.md).
- **Futures only, current expiry.** Options are deferred (you can't tell a directional print from a
  hedge/spread leg in the chain). "Current expiry only" thins in the last 1–2 days as liquidity rolls
  to next month — late-cycle big prints migrate to the contract we're not watching (auto-roll is a
  future add).

Decision-support; **never auto-executes**.

## The two filters

`notional = price × qty` — `qty` (LTQ / volume) is already shares-equivalent (verified live: LTQ 65 =
one NIFTY lot), so lot size does **not** multiply in again; it's only for the "N lots" label.

| Filter | Fires when | Catches |
|---|---|---|
| **PRINT** | one trade's notional ≥ a per-symbol ₹ floor | a single fat block crossing the book |
| **BURST** | cumulative traded notional over a trailing `BURST_WINDOW_S` (10s) window ≥ a floor, rising-edge (fires once per crossing) | orders sliced into many small clips |

Each event records the raw fields that tripped it (price, qty, bid/ask, notional) — make-state-visible.

### Floors are placeholders — CALIBRATE on real tape
Defaults in `app/big_money.py` (`BIG_PRINT_FLOOR` ₹1cr, `BURST_FLOOR` ₹3cr) are **starting guesses**.
Calibrate with a dry-run (`--print-floor`/`--burst-floor` override all symbols):

```
python scripts/run_big_money_collector.py --dry-run --seconds 300 --print-floor 2000000
```

**Calibration insight (day-1, 2026-07-22):** on the futures feed the LTQ of a single print is almost
always **1–2 lots** (algos work large orders as many small clips), so a high single-**PRINT** floor
rarely fires — the **BURST** filter is the workhorse for futures. Set the print floor to catch the
genuine occasional block; lean on burst for sliced institutional flow.

## Architecture (decoupled — a collector process + a dashboard reader)

```
Dhan WS ─► collector (always-on) ─► JSONL event store ─► dashboard tape card (tails, newest first)
           decode → filter → aggressor tag    data/journal/big_money/<SYMBOL>/<day>.jsonl (gitignored)
```

- **`data/dhan_feed.py`** — the v2 websocket client + binary decoder. Offsets are LOCKED against real
  packets by `tests/test_dhan_feed.py` (hex fixtures). Little-endian.
- **`app/big_money.py`** — the pure filter (`notional`, `classify_aggressor`, `_BurstTracker`,
  `BigMoneyDetector`) + the `Event` model + the JSONL store (`append_event` / `load_events`).
- **`scripts/run_big_money_collector.py`** — the always-on process (reconnects; token 24h-capped →
  repaste `data/.dhan_token` and it resumes). `--dry-run` prints without storing.
- **`app/simple_dashboard.py::_big_money_tape_html`** — the full-width tape card (reads the store).

## Feed format reference (v2, verified 2026-07-21/22)

Connect: `wss://api-feed.dhan.co?version=2&token=<tok>&clientId=<cid>&authType=2` (both secrets required,
never logged). Subscribe JSON: `{"RequestCode": 15|17|21, "InstrumentCount": n, "InstrumentList":[…]}`
(15 Ticker / 17 Quote / 21 Full). Binary response, **little-endian**; header = code(u8)@0 · msgLen(u16)@1
· seg(u8)@3 · securityId(i32)@4. Payload offsets:

| Field | Quote (code 4) | Full (code 8) |
|---|---|---|
| LTP f32 | 8 | 8 |
| LTQ i16 | 12 | 12 |
| LTT i32 (**IST-wall-as-epoch** — subtract 5:30 for true epoch) | 14 | 14 |
| ATP f32 / volume i32 | 18 / 22 | 18 / 22 |
| tot-sell / tot-buy i32 | 26 / 30 | 26 / 30 |
| OI i32 | — | 34 |
| open/close/high/low f32 | 34/38/42/46 | 46/50/54/58 |
| depth ×5 (bidQty i32·askQty i32·bidOrd i16·askOrd i16·bidPx f32·askPx f32, 20B) | — | 62… (best bid px @74, ask px @78) |

Feed codes: 2 Ticker · 4 Quote · 5 OI · 6 PrevClose · 7 MktStatus · 8 Full · 50 Disconnect.

## Run recipe

```
# verify entitlement / see the tiers (any time; snapshot packets arrive even when closed)
python scripts/dhan_ws_probe.py 20

# run the tape (writes the store the dashboard reads); Ctrl-C to stop
python scripts/run_big_money_collector.py

# calibration dry-run (print only)
python scripts/run_big_money_collector.py --dry-run --seconds 300 --print-floor 2000000
```

The dashboard shows the tape automatically once the collector is writing today's store.
