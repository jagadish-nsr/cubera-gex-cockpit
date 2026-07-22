# Big-money futures tape ([W2])

A live **large-trade / unusual-activity tape** for the NIFTY + BankNifty + SENSEX **current-expiry
futures**, off the Dhan v2 Market-Feed websocket. When a trade (or a short burst of trades) is
unusually large, it's stamped with the exchange time and shown on the dashboard.

**Auto-starts with the dashboard.** Ticking *🐋 Big-money tape (live, auto)* in the sidebar (default on)
launches a single background collector thread (via `st.cache_resource`, one per server) that streams the
feed, flags big prints/bursts, **and archives every raw tick**. No separate process to run. The tape card
shows a live health strip (connected · last-tick age · event count · raw-ticks-saved · per-symbol tick
counts). You can also run it headless with `scripts/run_big_money_collector.py`.

## Why we archive raw ticks — Dhan has no historical tick data
Verified against Dhan's own docs: the **historical** API bottoms out at **1-minute candles**
(OHLC + volume + OI) and carries **no tick / trade / bid-ask history**. The live websocket is the ONLY
source of tick + quote data, so the collector saves every Quote/Full tick to a per-day CSV
(`data/journal/big_money_raw/<SYMBOL>/<day>.csv`, gitignored) — building the tick+quote archive Dhan
won't sell you. Load it with `app.big_money_live.load_raw_ticks(symbol, day)`.

## SENSEX needs the BSE live-data entitlement
SENSEX is wired in (floors, defaults, tape), and its future resolves from the scrip master (BSE_FNO,
lot 20). But **verify-live shows the websocket returns zero SENSEX packets** on the current token while a
NIFTY control on the same connection ticks fine — BSE derivatives live data is a separate Dhan
entitlement from NSE. So SENSEX shows **0 ticks** (flagged ⚠️ in the health strip) until BSE live-data is
enabled on the plan; it then lights up automatically with no code change.

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
  `BigMoneyDetector`) + the `Event` model; `append_event` / `load_events` delegate to the DB.
- **`app/big_money_db.py`** — the **SQLite event store** (`insert_event`, `load_events`,
  `daily_rollup`, `migrate_jsonl`). WAL + a UNIQUE key (dedupes snapshot repeats); the queryable
  directional history.
- **`app/big_money_live.py`** — the always-on background SERVICE: `Collector` (daemon thread:
  decode → save raw → filter → append Event, with reconnect + per-symbol tick counts) and
  `load_raw_ticks`. Auto-started from the dashboard via `cache_resource`; also usable standalone.
- **`scripts/run_big_money_collector.py`** — a headless one-shot runner of the same pipeline
  (reconnects; token 24h-capped → repaste `data/.dhan_token` and it resumes). `--dry-run` prints
  without storing; `--print-floor`/`--burst-floor` override floors for calibration.
- **`app/simple_dashboard.py`** — `_get_big_money_collector` (the cache_resource singleton),
  `_big_money_tape_html` (full-width tape card) + `_bm_status_html` (the live health strip).

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

## Scheduled auto-collection (headless, every trading day)

`scripts/run_big_money_collector.py --market-hours` is **IST-aware**: it waits for the 09:15 IST open
and stops at 15:30 IST, so a scheduled trigger only has to fire *before* open. Register the Windows task:

```
powershell -ExecutionPolicy Bypass -File scripts\install_collector_schedule.ps1     # register (weekdays)
powershell -File scripts\install_collector_schedule.ps1 -Remove                      # unregister
```

Fires weekdays at **03:15 local**. Note this machine runs on **UK time (BST/GMT)**, which DST-shifts vs
IST — so we trigger early and let the collector wait out the slack (robust across the DST change); it
maps to ~07:45–08:45 IST year-round. `--market-hours` also auto-logs to
`data/journal/big_money_logs/collector-<day>.log`. **Runs only when you are logged on**, and needs a
**fresh Dhan token** each morning (24h cap) — the collector retries until a token is present, so paste
it any time before/after open and it connects.

## Daily summary & floor calibration

```
python scripts/big_money_summary.py                 # today, all symbols
python scripts/big_money_summary.py 2026-07-23 NIFTY
```

Per symbol it reports tick count & span, the **single-print notional distribution** (→ a suggested
PRINT floor at p99), the **10s-window traded-notional distribution** (→ a suggested BURST floor), the
biggest prints, and **buy- vs sell-initiated traded notional** (Lee-Ready signed flow — the directional
lean). Use the p99 suggestions to set `BIG_PRINT_FLOOR` / `BURST_FLOOR` in `app/big_money.py` once you
have a full, representative session.

## Storage & scale (why not one big database?)

Two very different streams, so two stores:

- **Raw ticks** (~130k rows/symbol/day) live in append-only **CSV** per symbol/day — simple and
  crash-safe for a live writer. At scale, compact each finished day to **Parquet** (columnar, ~5–10×
  smaller, fast analytical scans) rather than a row-store DB; per-tick `INSERT` into SQLite is slower
  than an append and worse for full-column scans. *(Daily Parquet compaction is a clean future add.)*
- **Big-money events** (sparse — dozens/day) are the **directional history**, now in **SQLite**
  (`data/journal/big_money.db`, `app/big_money_db.py`) — tiny, indexed, queryable across days. The
  daily rollup is the "which way big money leaned" ledger:

```
python scripts/big_money_rollup.py                 # every day: buy vs sell ₹, net, lean, biggest
python scripts/big_money_rollup.py 2026-07-23
python scripts/big_money_rollup.py --migrate         # import any legacy JSONL first
```

## Mid-day flow-bias on the dashboard

The tape card opens with a **"Big-money flow bias · today"** block: per symbol, buy- vs sell-initiated ₹
(Lee-Ready) across today's big prints/bursts, with a net lean (▲ BUY / ▼ SELL). Open the dashboard any
time and you get the running read of which way big money leaned so far. It is **descriptive order flow,
explicitly NOT a validated forecast** — a first cut we'll sharpen as data + study accrue (sign-free;
de-size on conflict; you confirm). It derives from `big_money_db.daily_rollup`, so it's a cheap indexed
query even on every repaint.

## Run recipe (manual)

```
python scripts/dhan_ws_probe.py 20                                  # verify entitlement / tiers
python scripts/run_big_money_collector.py                          # run until Ctrl-C (raw + events)
python scripts/run_big_money_collector.py --dry-run --print-floor 2000000   # calibration, no writes
```

The dashboard shows the tape automatically once the collector is writing today's store.
