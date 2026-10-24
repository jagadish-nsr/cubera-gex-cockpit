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

## Futures rollover — watch near + next month (Option A)

Index futures are monthly, and in the last days before expiry OI/volume migrate to next-month — so a
single-contract tape would stare at a thinning book while the big prints move to the contract it isn't
watching. So we watch the **two nearest contracts per symbol** (`resolve_futures(rolls=2)` →
`nearest_futures`), tagged `roll` = near/next, and **aggregate by symbol**:

- **PRINTS** stay per-contract (a trade is on one contract) but are tagged with the contract `expiry`
  and roll up by symbol. So a big print migrating to next-month during the roll is still caught.
- **BURSTS** aggregate to symbol level in real time: `_SymbolBurstTracker` keeps each contract's own
  cumulative-volume delta but sums both into ONE window, so a burst reflects total futures flow across
  the roll (tagged `expiry='AGG'`).
- **Flow-bias / rollup** already `GROUP BY symbol`, so both contracts fold in automatically.
- **Rollover gauge:** the collector upserts each contract's OI/price to a `futures_state` table every
  ~15s; the tape shows `rolled N% → Aug` where `%rolled = next_OI/(near_OI+next_OI)`, plus near/next
  prices. Descriptive context, not a signal.
- The collector **re-resolves near/next daily** (on IST date change), so a long-running dashboard server
  advances across an expiry without a restart.

Dedupe keys include the contract (`expiry`) so a near+next tick at the same epoch isn't collapsed — raw
compaction on `(epoch,code,symbol,expiry)`, events `UNIQUE(epoch,symbol,kind,qty,price,expiry)`.
*Not yet:* explicit calendar-spread detection (simultaneous near-sell + next-buy) — a clean follow-up.

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
  `load_raw_ticks`. One process-wide collector; also usable standalone.
- **`scripts/run_big_money_collector.py`** — a headless one-shot runner of the same pipeline
  (reconnects; token 24h-capped → repaste `data/.dhan_token` and it resumes). `--dry-run` prints
  without storing; `--print-floor`/`--burst-floor` override floors for calibration.
- **`web_server.py`** — the tape card on the web cockpit reads the same event store.

**Write ownership (avoid double-writing raw).** Two concurrent sockets on one token are fine (verified
live), but two collectors both saving raw would double-append the same CSV. So the **raw archive is owned
by the scheduled headless collector** (`run_big_money_collector.py`, `save_raw=True`); the **dashboard's
auto-collector runs events-only** (`Collector(save_raw=False)`) — it still fills the event DB for the live
tape, but never touches the raw file. Events are safe either way (SQLite WAL + UNIQUE dedupe).

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
  crash-safe for a live writer — then compact to **Parquet** (columnar, ~5–10× smaller, fast scans).
  `scripts/compact_raw_ticks.py` compacts every finished day (`--market-hours` collector also compacts
  today on exit); `load_raw_ticks` prefers the Parquet automatically. Not a row-store DB — per-tick
  `INSERT` is slower than an append and worse for full-column scans.
- **Big-money events** (sparse — dozens/day) are the **directional history**, now in **SQLite**
  (`data/journal/big_money.db`, `app/big_money_db.py`) — tiny, indexed, queryable across days. The
  daily rollup is the "which way big money leaned" ledger:

```
python scripts/big_money_rollup.py                 # every day: buy vs sell ₹, net, lean, biggest
python scripts/big_money_rollup.py 2026-07-23
python scripts/big_money_rollup.py --migrate         # import any legacy JSONL first
```

## Day readout on the dashboard

The tape card also carries a **"Day so far"** readout for the selected symbol — the same aggregate the
standalone analysis page shows, folded in: event/print/burst counts, day OI trend (build/unwind),
**build-up ₹ split**, **net conviction + positioning share**, and the heaviest prices. It's computed by
`app/big_money_analysis.py::analyse` (shared with `scripts/big_money_analysis.py`).

The sidebar clock that scrubbed a captured day was removed on 24 Oct 2026. This app shows the live tape.
`scripts/rederive_events.py` can still walk a raw archive from the command line for a detector check.
That script is not a screen, and a future replay application does not write this process’s databases.

## Mid-day flow-bias on the dashboard

The tape card opens with a **"Big-money flow bias · today"** block: per symbol, buy- vs sell-initiated ₹
(Lee-Ready) across today's big prints/bursts, with a net lean (▲ BUY / ▼ SELL). Open the dashboard any
time and you get the running read of which way big money leaned so far. It is **descriptive order flow,
explicitly NOT a validated forecast** — a first cut we'll sharpen as data + study accrue (sign-free;
de-size on conflict; you confirm). It derives from `big_money_db.daily_rollup`, so it's a cheap indexed
query even on every repaint.

**Validation harness:** `scripts/big_money_flow_ic.py` pre-registers the test — daily net-flow lean vs
the next session's return, Spearman IC vs a shuffled-return mirror placebo, per symbol. It reports
INSUFFICIENT until ~15+ sessions are captured (by design). The flow-bias block stays *descriptive*
until this shows a real, placebo-beating IC; only then does it earn directional wording.

## Run recipe (manual)

```
python scripts/dhan_ws_probe.py 20                                  # verify entitlement / tiers
python scripts/run_big_money_collector.py                          # run until Ctrl-C (raw + events)
python scripts/run_big_money_collector.py --dry-run --print-floor 2000000   # calibration, no writes
```

The dashboard shows the tape automatically once the collector is writing today's store.
