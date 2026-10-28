# WebSocket Simulator, Market Replay, and Stress Engine

Status, 2026-10-29: the code for this spec was removed from the tree. This document is the design to implement later. The live cockpit has no replay badge, and the live sentinel feed does not append a replay day file.

Canonical spec for replaying a stored session into the cockpit, measuring how the application behaves under that replay, and later practicing a paper ticket on the same clock. This document replaces the earlier simulator blueprint and the shorter replay plan. Where those two disagreed, this file is the decision.

When this is built, the live cockpit on ports 8640 and 8650 stays unchanged except one badge label. Replay is a separate process on port 8660.

---

## 1. Purpose

A trader can only watch the live book between 09:15 and 15:40 IST. This engine lets the same cockpit be driven, off hours, from a chosen trading day, at a pace the trader sets.

1. **Replay.** Load one symbol and one session. Step one minute, step five minutes, play a named part of the day, or play a custom window. Pause and seek.
2. **Learn the book.** At each minute the call wall, put wall, gamma flip, max pain, and net GEX are the same function the live page uses, on that minute’s chain. The sign is call-heavy or put-heavy. It is not a forecast that price will fade or run.
3. **Practice an entry and an exit.** Pause, read the book, then step forward. A later phase records a confirmed paper ticket against the chain last at the playhead and marks it only as the clock moves forward.
4. **Find latency gaps.** Time the book rebuild, the socket send, and the browser paint. A stress run walks the day as fast as the compute allows, and can repeat one stored minute at the live socket cadence. The report names the slow minutes. It does not invent a tick tape in order to quote a tick rate.

No order is sent to a broker. The trader confirms every paper ticket.

---

## 2. Isolation contract

From `docs/UI_MIGRATION_LESSONS_LEARNED.md` (rules 6 and 19) and the zero-fabrication rule:

- The replay process does not run inside `web_server.py`. There is no `simulation_mode`, no `reseed_sim`, and no clock branch in the live server.
- It never writes `data/sentinel.db`, `data/journal/market_snapshot.db`, or `data/journal/paper_trades.db`.
- It never opens a Dhan socket and never calls a Dhan quote or order endpoint.
- It never invents a price path inside a minute, a premium, an IV, a VIX print, a bid, or an ask. A recommendation, an alert, or a sentinel signal on this port is the output of the same generator the live page uses, fed only with state at or before the playhead (section 19). It is not a card copied out of the live database, and it is not emitted when that generator would substitute a default spot, a default wall, or a default IV.
- A missing value is omitted or shown as an em dash.
- Code lives in a new top-level package `replay/`. It is not imported by the live feed. `sentinel/simulator/` is not created. Feeding `spot_ticker`, `BarAggregator`, or `push_live_tick_to_sentinel` from this process is rejected: those paths write the live ledger.

The one live-process change besides a badge string is the minute-close append in section 5. That append writes a new parquet file. It does not rewrite `sentinel.db`.

Own files, gitignored:

- `data/replay/replay.db` — playhead timings and the derived level cache.
- `data/replay/practice.db` — paper tickets for this process only.

---

## 3. What a stored day actually contains

Playback unit is the **closed minute**. A regular session is **375 minutes, 09:15 through 15:29 IST inclusive**, the same grid as `app/admin/audit_engine.py`. The option and futures journals already hold that minute. The replay loads the day into memory once.

| Series | Path | Columns used | Role |
| :--- | :--- | :--- | :--- |
| Option chain | `data/journal/dhan_intraday/<SYMBOL>/<YYYY-MM-DD>.parquet` via `data.dhan.load_intraday_day` | `time_ist`, `off`, `side`, `strike`, `spot`, `open`, `high`, `low`, `close`, `volume`, `oi`, `iv` | Spot, the 21 offsets × call and put, and the chain last (`close`) used for a paper fill. About 15,750 rows on a full Nifty day. |
| Futures | `data/journal/dhan_futures/<SYMBOL>/<YYYY-MM-DD>.parquet` via `data.dhan.load_futures_intraday_day` | `time_ist`, `open`, `high`, `low`, `close`, `volume`, `oi` | The only OHLC candle. Header futures price is the minute close. Basis is that close minus the option-row spot. |
| Sentinel day file | `data/journal/sentinel/<SYMBOL>/<YYYY-MM-DD>.parquet` | The completed `Bar` plus any signals for that minute | CVD, delta, VWAP bands, depth imbalance, signals. Written by section 5. Absent until that append exists. |
| Sentinel cache | `data/sentinel.db` opened `file:...?mode=ro` | `sentinel_bars`, `sentinel_signals` for `<SYMBOL>-FUT` | Fallback only when the day file is missing and this date is still cached. |

Symbol match is exact: `NIFTY`, `BANKNIFTY`, `SENSEX`. Lot size and strike step come from `config.INDICES` (`strike_step_for`). Nifty 65 and 50 points, Bank Nifty 30 and 100, Sensex 20 and 100. `"NIFTY" in "BANKNIFTY"` must not choose either.

These stores are not a tape, and the replay does not read them as one:

- `data/journal/market_snapshot.db` keeps **one row per symbol** (`session_snapshots.symbol` is the primary key). It is the last book, not a minute of walls. Using it would pin every replay minute to the latest snapshot.
- `sentinel_daily_cvd` is a derived close. `ensure_historical_daily_cvd` can invent rows. Do not call it.
- `signal_outcomes` hold forward returns. They stay hidden until the playhead has passed that outcome’s horizon (`ret_30m` is not shown at the signal minute).
- India VIX is a live quote (`data.dhan.fetch_india_vix_dhan`). It is not a column on the option parquet. Replay sends `india_vix.last = null`. The book-balance row uses near-ATM IV from the chain, which the parquet has. The replay script writes an em dash into the VIX cell so a client fallback cannot show 14.04.
- Bid, ask, greeks, last-traded quantity, and depth are not in the option parquet. Gamma is recomputed. A paper fill uses the chain `close` for that strike and side. There is no bid or ask to cross.
- Recommendation cards, commentary rows, and big-money prints are not a stored tape. Section 19 generates the ones whose inputs exist at the playhead. A tab whose input is absent says so. It does not borrow today’s live row.

A day is armable when both parquet journals exist for that symbol and date. The catalog is built from those files, not from `sentinel.db`. Each date is annotated `sentinel: day_file | cached | absent`. A holiday or weekend with no file is refused with `data.holidays.get_holiday_name`. A short file (muhurat or a partial capture) plays the minutes present and is labeled partial. It is not scored against 375.

---

## 4. What the live socket does not start storing

No raw Dhan frame, no bid/ask archive, and no precomputed wall series on the live feed.

- A 1-minute step cannot display an intra-bar path the bar does not contain. Recording every frame would add a writer on the live socket and still would not be the playback unit.
- Walls are recomputed in the replay process by `gex.levels.compute_levels`. That cost is one of the gaps the stress run exists to measure. Writing the levels during the live session would create a second source that can drift from the function. After the first walk, replay may cache that minute’s levels inside `data/replay/replay.db` so the next seek is cheap. The live process does not write that cache.
- Exchange-time versus receive-time lag is not in a minute bar. This spec does not add a live latency probe. The report is application time under replay, not wire time to Dhan.

The live browser socket is already coalesced. `web_server.websocket_telemetry` sleeps `_WS_LIVE_SLEEP_S` (0.12s) and suppresses an unchanged signature until `_WS_HEARTBEAT_S` (1s). A harness that invents 1,000 ticks per second is not the production path. Section 12 stresses the path the cockpit actually runs.

---

## 5. The one live append

`sync_live_session` in `sentinel/feed/live.py` calls `clear_sentinel_data` before it rewrites the instrument. The next successful sync drops the prior day. The completed minute already exists in memory when `BarAggregator` finalizes it: price, volume, estimated delta, CVD, VWAP, depth imbalance, and any signal.

On that finalize, append the bar and its signals to `data/journal/sentinel/<SYMBOL>/<YYYY-MM-DD>.parquet`. One row per completed minute. No tick. No rewrite of `sentinel.db`. If the process restarts and finalizes the same minute again, the append replaces that minute’s row. It does not duplicate it.

Replay reads the day file first. If the file is absent and `sentinel.db` still has `1m` rows for `<SYMBOL>-FUT` on that date, those rows are read with `mode=ro`, and only rows with `start_ts` or `bar_start_ts` at or before the playhead. If both are missing, the order-flow lane says the ledger was not kept. Delta is not invented from the candle body.

Walls, spot, the futures candle, and a paper fill do not wait on this file.

---

## 6. Clock and transport

One playhead per process: symbol, day, minute index, state `paused | playing | stress`.

| Control | Behavior |
| :--- | :--- |
| Step 1m | Advance one stored minute and publish it. Then pause. |
| Step 5m | Five 1-minute publishes. This is not the rolled-up `5m` sentinel row. |
| Step back 1m | Move the playhead to the previous stored minute. Later walls, signals, and chart bars disappear. Refused while a practice ticket is open (section 13). |
| Session | 09:15–15:29, or the minutes actually present. |
| Opening | 09:15–10:15. |
| Midday | 11:30–13:00. |
| Late session | 14:00–15:29. |
| Custom | `HH:MM`–`HH:MM`, clipped to the minutes on disk. A start after the end is refused. Sub-minute seek is refused. The archive has no sub-minute timestamp. |

Speed is a multiplier of market time. The wait between published minutes is `60 / speed` seconds.

| Speed | Wait between minutes | A 375-minute day |
| :--- | :--- | :--- |
| 0.5× | 120s | About 12.5 hours |
| 1× | 60s | 6 hours 15 minutes, one market minute per wall minute |
| 2×, 5×, 10×, 30× | 30s, 12s, 6s, 2s | Compressed review |
| 60× | 1s | About 6 minutes 15 seconds |

`stress` does not sleep between minutes. It walks every minute in the armed window as fast as `compute_levels` and the send complete. It is not a speed multiplier on invented ticks.

Seek jumps to a stored `HH:MM` inside the armed window. The same forward-only rule as step-back applies when a ticket is open: seek is refused until the ticket is flat, so a stop sitting on a skipped minute cannot be jumped over.

Ten 1-minute steps and ten minutes of play at any speed must land on the same frame. Speed changes the wait, not the numbers.

---

## 7. The frame published each minute

One cockpit message per stored minute, on the socket the page already opens: `/ws/telemetry` and `/ws/live`. The client may send `{symbol}` to switch index. Switching reloads that symbol’s day. It does not keep the previous symbol’s playhead.

The JSON is the production `TICK` from `web_server.websocket_telemetry`, plus a `replay` object the toolbar reads. Unknown fields are ignored by today’s `handleLiveTick`. A new event type such as `SIM_TICK` is rejected because the page only paints `type === "TICK"`.

Required differences from the live payload:

- `feed_mode` is `REPLAY`. Never `LIVE_WEBSOCKET`.
- `timestamp` is the Unix time of that IST minute. `cockpit.js` buckets the candle with `tick.timestamp + 19800`. A wall-clock timestamp would draw the whole day into the current minute.
- `ist_time` is the playhead, `%H:%M:%S IST`, and the toolbar also shows the calendar date.
- `is_market_open` is true so the existing paint path runs. The banner says this is a stored session, not the live book.
- `spot` is the option parquet spot for that `time_ist`.
- `future_price` is the futures close. `basis` is `future_price - spot`.
- `latest_bar` is the futures OHLC. Its `time` equals the client bucket `floor((timestamp + 19800) / 60) * 60`, so the candle is the stored bar and not a one-tick doji. When a sentinel bar exists for that minute, `vwap`, `delta`, and `cvd` are copied onto this object. The chart badge says `REPLAY`, not `DHAN TICK`.
- `call_wall`, `put_wall`, `gamma_flip` come from `compute_levels` on this minute’s chain. They are null when the chain cannot be priced (section 8). They are never taken from `market_snapshot.db`.
- `india_vix.last` is null.
- `rec_quotes` is empty until a practice ticket exists. Then it lists only tickets opened in this replay, marked from the chain last. No default premium.

`replay` on the same message: `status`, `speed`, `minute_index`, `minute_count`, `day`, `sentinel` (`day_file`, `cached`, or `absent`), and the last `gex_ms`.

Inside one minute, stress may repeat that same payload every 0.12s for a configured dwell (default 5s) to load the socket and the paint path the way the live loop does. The price does not change. `replay.repeat` is true on those copies. The report counts them as repeats.

---

## 8. Book at the playhead

Each minute’s option rows become an `OptionsSnapshot` for that minute only. `gex.levels.compute_levels` is called with `contract_size` from `config.INDICES`. The sign model is `short_call_short_put`. Both legs stay negative. Walls are the largest call-gamma and put-gamma magnitudes. The flip and the net total are call magnitude minus put magnitude. Positive means call-heavy. Negative means put-heavy.

Spot passed into the snapshot is that minute’s `spot`. Open interest, IV, and the chain last are that minute’s rows. The 15:29 chain is not used at 10:15.

`analytics.market_alert.classify_zone` runs on that spot, those walls, and `strike_step_for(symbol)`. The zone label is the one the function already returns (`Testing call-wall concentration`, `Between wall concentrations`, and the rest). Replay does not add a second geometry language.

If a side is missing, IV is missing, or `compute_levels` raises, that minute is published with null walls and a `replay.book` reason. It is counted as skipped in the report. The spot and futures candle still move.

The book-balance line uses the same inputs as live, with near-ATM IV when VIX is null. Breadth, participant OI, and news are not on this tape. Those fields are omitted, not filled with zero.

A hand-check ships with the book: a two-strike fixture, the index lot, and walls and flip equal to `gex.core` on that fixture. A second test arms three minutes and asserts minute 1 cannot see minute 2’s open interest.

---

## 9. Process and routes

`replay/server.py`, port **8660**. It mounts `app/static` and serves `index.html` with one extra script, `replay/controls.js`, injected only on this response. Production `index.html` is not edited. The only edit in `app/static/js/cockpit.js` is the badge: when `tick.feed_mode === "REPLAY"`, the chart badge reads `REPLAY`. That branch does not read a simulator flag and does not advance a clock.

Startup does not start chain ingest.

Routes the page already polls, answered as of the playhead:

- `GET /api/health` — `app: "Cubera Replay"`, port, day, playhead.
- `GET /api/status` — badge `REPLAY <day> <HH:MM>`. Expiry is the expiry that was in force on that session date. No token.
- `GET /api/cockpit-data` — the live dashboard shape, filled from the playhead book (section 19). Breadth, same-day participants, and news are omitted with a reason.
- `GET /api/sentinel-tape` — futures candles and, when the day file exists, sentinel bars from the armed start through the playhead. Signals are the classifier walk, not a dump of `sentinel_signals`. Outcomes only after their horizon.
- `WS /ws/telemetry`, `WS /ws/live` — section 7.

Replay’s own routes:

- `GET /api/replay/days?symbol=` — dates present in both journals. Incomplete days are listed and cannot be armed. Sentinel coverage is an annotation.
- `POST /api/replay/session` — `{symbol, day, start, end}` or `{symbol, day, preset}`. Loads the window into memory. State becomes `paused` on the first minute.
- `POST /api/replay/transport` — `play`, `pause`, `step` (`1m`, `5m`, `back_1m`), `seek` (`HH:MM`), `speed` (0.5, 1, 2, 5, 10, 30, 60), `stress`.
- `GET /api/replay/state` — playhead, index, speed, sentinel coverage, last timings.
- `GET /api/replay/bookmarks?symbol=&day=` — section 11. Empty until that phase is built.
- `GET /api/replay/report` — section 12.

The remaining cockpit routes are answered in the live response shape from the playhead walk in section 19. A route whose required input is absent returns `{replay: true, available: false, reason}` naming that input. It does not return a live-looking empty book.

---

## 10. Trader controls

The toolbar exists only on 8660. It always shows the calendar date, so a replay cannot be mistaken for the live header on 8640.

```
Rewind 1m | Pause | Play | +1m | +5m | Speed 0.5x…60x | Stress
09:15  —— opening —— midday —— late ——  15:29
<day>  <HH:MM:SS IST>   minute i/N   book <gex_ms>ms   sentinel <coverage>
```

The scrubber seeks to a stored minute. Preset chips arm opening, midday, or late session. Custom start and end sit beside them. The metrics strip shows speed, the last book time, and, once a ticket exists, the practice mark. It does not show a tick rate from a fabricated tape.

Workflows this supports before paper trading exists:

- Play at 60× to watch the walls and the futures candle move together, then drop to 1× around a minute of interest.
- Pause on a minute, read the zone label and the book-balance line, then step one minute.
- Step 5 minutes to scan, then step 1 minute where the zone changes.
- Seek to a bookmark two minutes before the event and play through it (section 11).

---

## 11. Bookmarks

Built after the book is trustworthy. A bookmark is a minute already on the tape, not a new signal.

| Kind | Rule | Seek lands |
| :--- | :--- | :--- |
| Zone change | `classify_zone` label changes from the previous stored minute | Two minutes before the change, clipped to the armed start |
| Flip cross | Spot and the recomputed flip change which side of the other they are on | Two minutes before |
| Session extreme | Futures high or low equals the high or low of the armed window so far | That minute |
| Classifier signal | The session walk emits a sentinel signal on this minute | Two minutes before |

Copy uses the zone label and the words call-heavy or put-heavy. It does not say pin, fade, dealer defense, squeeze, or iceberg. A 15-second micro-burst is not a bookmark. The classifier walks completed minutes, and that series is not on the minute. Delta quality is not a filter. The ledger does not record one.

Clicking a bookmark seeks. It does not auto-arm a ticket.

---

## 12. Latency and stress

Each published minute (not each 0.12s repeat) writes one row to `data/replay/replay.db`:

| Field | What it times |
| :--- | :--- |
| `load_ms` | Slice the minute from the in-memory day |
| `gex_ms` | `compute_levels` |
| `signal_ms` | Sentinel classifier walk, when the page asked for signals |
| `rec_ms` | Recommendation and target-engine generation, when the page asked for cards |
| `send_ms` | `websocket.send_json` completing |
| `paint_ms` | `controls.js` posts back when the header clock equals this playhead. Null if the browser never acknowledged |
| `repeat_count` | Extra identical payloads during a stress dwell |
| `skipped` | Chain could not be priced |

The report lists count, p50, p95, p99, the slowest ten minutes, skipped minutes, sends that exceeded the live handler’s 1 second timeout, peak process memory, and the largest paint gap. Dropped acks are the minutes with a null `paint_ms` while a browser was connected.

Two stress suites, both on a real day:

1. **Compute walk.** `stress` with no dwell. Finds minutes where the book rebuild dominates. This is the gap that matters: live chain refresh is on the order of 30 seconds, and replay prices every stored minute. Card generation is off in this suite unless `--with-cards` is passed, so a slow playbook does not hide inside `gex_ms`.
2. **Paint load.** On a chosen minute, repeat the same payload every 0.12 seconds. Finds socket backpressure and chart stutter. The report labels the copies as repeats of one stored close.

There is no pass line of 2 milliseconds or 1,000 ticks per second. Those numbers assume a tick synthesizer and a calculation the book function does not do. The first saved report for a fixture day is the baseline. A later run on the same fixture records the delta against that baseline.

A CLI runs suite 1 without a browser:

```bash
python -m replay.stress --symbol NIFTY --day YYYY-MM-DD --mode compute
```

Suite 2 needs the page on 8660 so `paint_ms` exists.

---

## 13. Paper practice

Designed into the frame now. Built after the tape and the report are trustworthy.

Ledger is `data/replay/practice.db` only. Do not call `data.journal.store.open_trade`. That function writes `data/journal/paper_trades.db`. Copy the cost rule: ₹20 per order and 0.5 point of slippage per leg. The entry row stores the playhead time and the walls, flip, net GEX, and zone label of that minute. Later minutes do not rewrite that snapshot.

- Open and close require an explicit confirm. Nothing in the engine submits a ticket because a signal fired.
- The fill is the chain `close` for that strike and side at the playhead, plus the slippage. A missing close refuses the ticket. There is no default premium and no bid or ask.
- Quantity uses `config.INDICES[symbol].lot_size`.
- Mark-to-market, and a stop or target check, run only when the playhead advances through each minute by step or play. The mark uses that minute’s chain close.
- Seek and step-back are refused while a ticket is open. The trader flattens first. That is what keeps a stop on a skipped minute from being ignored.
- A limit or a stop that shares a minute with the other side of the bar is not guessed. The stored bar does not order the high and the low. The ticket is flagged `bar_path_unknown` for that minute and left to the trader. It is not auto-closed.
- The strip shows points, costs, and the best and worst mark seen while the clock was moving forward. It does not show a Sharpe ratio. One practice day is not a research result.
- A finished session can be exported later by an explicit action. It is not written into the live journal by itself.
- The ticket copy says paper replay. It does not say the structure would have filled on the exchange.

`commentary/qa/replay.py` and the target-engine harness remain the research walkers. This ledger is practice.

---

## 14. Point-in-time rules

- Levels, zone, the chain last, and every generated card at minute T use minutes at or before T only.
- The sentinel classifier, the recommendation latch, and the opening-range box are walked from 09:15 (or the first stored minute) through T, even when the armed window starts later. A midday preset does not pretend the morning did not happen.
- Seeking backward uses the memo for that earlier minute. It does not keep a card, a signal, or a chart bar that was born after the new playhead.
- Signal outcomes stay hidden until the playhead has passed the horizon they measure.
- `now`, `config.is_market_open()`, and `Date.now()` are not inputs. The playhead timestamp is.
- The replay process makes no Dhan call, including off hours. It does not read or write the live recommendation latch, commentary table, target-engine store, efficiency database, or `paper_trades.db`.

---

## 15. Tests

- Fixture book: two strikes, lot from `config.INDICES`, walls and flip match `gex.core`.
- No lookahead: three parquet minutes, the frame at minute 1 does not contain minute 2’s open interest or a later signal.
- Transport: step 1m, step 5m, each preset, a reversed custom range refused, a sub-minute seek refused, a missing journal refused, a holiday name returned.
- Determinism: ten steps and ten minutes of play produce the same frame.
- Socket: the payload has `type: TICK`, `feed_mode: REPLAY`, and `timestamp` equal to the playhead’s Unix time. `latest_bar.time` equals the client bucket.
- Isolation: hashes of `data/sentinel.db` and `data/journal/paper_trades.db` are unchanged after a compute walk. The test opens the sentinel file with `mode=ro` and asserts the replay package never calls `save_bar`, `save_snapshot`, or `open_trade`.
- Practice, when that phase lands: a missing chain close is refused, seek is refused while a ticket is open, brokerage and slippage match the journal constants, and the entry snapshot does not change after the next minute.
- Session surface: `/api/cockpit-data` at a playhead includes `kpis` and `ladder` built from that minute’s chain. A recommendation at minute 1 does not contain minute 2’s open interest. The latch at 11:00 matches a walk from 09:15 and does not match a walk that started at 11:00. A missing chain does not produce the generator’s default spot or a wall placed two strikes away. `run_target_engine` is called with `persist=False`. With no sentinel day file, delta stays null and the iceberg playbook is absent.

---

## 16. Build order

1. **Tape and clock.** Load one day from the two parquet journals. Step, presets, custom range, speed wait. Tests only.
2. **Book.** Minute rows to `OptionsSnapshot`, `compute_levels`, `classify_zone`, hand-check, no-lookahead test.
3. **Server.** Port 8660, `TICK` contract, `/api/status`, `/api/cockpit-data`, `/api/sentinel-tape` through the playhead, transport API, injected toolbar. Badge label in `cockpit.js`.
4. **Latency.** `replay.db`, paint ack, `/api/replay/report`, compute-walk CLI, repeat dwell labeled as repeats. Hash and determinism tests.
5. **Sentinel day file.** Append on minute finalize in the live aggregator. Replay prefers that file, then a read-only cache, then an explicit gap.
6. **Bookmarks.** Zone change, flip cross, session extreme, stored signal. Seek lands two minutes earlier where section 11 says so.
7. **Practice book.** `practice.db`, confirm to open and close, seek locked while a ticket is open, `bar_path_unknown` when a minute contains both a limit and a stop.
8. **Session surface.** Section 19. The live JSON shapes, the session walk for signals and recommendations, and a repaint on every published minute.

---

## 17. Rejected from the earlier blueprint

These were considered and left out because they contradict the stores or the isolation rule.

- A micro-tick synthesizer that walks open → low → high → close and assigns delta to the trending leg. The bar does not contain that path. Paper fills on it would record a trade that did not happen. Stress numbers from it would not be this cockpit’s workload.
- Reading walls from `market_snapshot.db` as if it were a historical series.
- Listing sessions from `sentinel.db` as the catalog of replayable days.
- `type: SIM_TICK` and `virtual_timestamp`. The page paints `TICK` and buckets on `timestamp`.
- An internal feeder into the live spot store and the live sentinel writer.
- Placing the package under `sentinel/simulator/`.
- Pass criteria of `T_calc < 2ms` and 1,000 ticks per second.
- Filling paper orders at a bid or ask the parquet does not have, or opening a ticket without a confirm.
- Bookmark copy that calls the book a pin, a fade, an iceberg, or a squeeze.
- Showing `signal_outcomes` at the signal minute.

---

## 18. Out of scope

- Repair, backfill, or a second Dhan socket from the replay toolbar.
- Treating the practice book as evidence that a wall will hold, or as a substitute for the research backtest.
- Inventing India VIX, sector breadth, a same-day participant file, a bid, an ask, or a path inside the minute so that a panel can look filled. Those panels stay empty and say the input is absent.

---

## 19. What the replay has to show

The cockpit on 8660 has to move the way the live cockpit moves when a minute closes. The numbers come from the stored minute and from the generators below. They are not a recording of what the live page happened to print, and they are not filled in where the minute has no input.

### 19.1 One walk, then a memo

Arming a day clears this process’s recommendation latch and sentinel classifier. Both are module globals inside the replay process only. The live server is a different process and is not touched.

For playhead minute T the walk is every stored minute from 09:15, or from the first minute on file, through T, in order:

1. Price the chain with `compute_levels`. Open interest, IV, and the chain last are that minute’s rows. Change in open interest is that strike’s open interest minus the previous stored minute on this day. PCR fifteen minutes earlier is the chain fifteen stored minutes earlier. Both are null on the first minute that cannot see them.
2. Update `MarketContext` with that minute’s futures bar and call `set_gex_levels` with that minute’s walls, flip, and magnet. The opening-range box is the futures high and low from 09:15 until 09:30. Session high and low are the futures extremes from the first stored minute through T.
3. If a sentinel bar exists for that minute, run `SignalClassifierEngine.evaluate_bar` with that context. If it does not, skip the classifier. Delta is not built from the candle body.
4. Build the directional-bias result from this minute. India VIX is null. Near-ATM IV is the chain’s IV. Skew is computed from the chain when both wings have an IV. Sector breadth and FII positioning are not passed.
5. Call `generate_trade_recommendations` with that dashboard, that bias, the signals emitted so far, and the latest sentinel bar if one exists. Then call `run_target_engine(..., persist=False)`.

The frame for T is memoized for the armed session. Ten steps and a seek back to the same minute return that memo. Seeking backward throws away memos after the new playhead so a later card cannot survive. A restart recomputes. The stress compute walk does not run steps 3 through 5 unless `--with-cards` is set. Play, step, and an open page always do.

`generate_trade_recommendations` substitutes a default spot (23000, 55000, 74000) and a wall two strikes from spot when the book is empty. Replay does not publish that result. A minute that fails `compute_levels` has null walls, no new card, and the previous cards are marked with the skip reason rather than re-scored on a fake book.

The latch is path-dependent. A window armed at 11:30 still walks from 09:15 so a card that was born at 10:02 is the same card a trader would have been watching. The chart and the scrubber still start at the armed start. The morning prefix is not drawn.

### 19.2 Cockpit payload

`GET /api/cockpit-data` returns the shape `loadCockpit` already paints. Flat fields are not enough. `loadCockpit` returns immediately when `kpis` is missing, which is why a replay today moves the spot and leaves the book on the screen.

| Field | Source at minute T |
| :--- | :--- |
| `kpis.call_wall`, `kpis.put_wall`, `kpis.gamma_flip`, `kpis.max_pain` | `compute_levels`. Each value is `{val, sub}`. The flip also has `raw`. `sub` is the distance from this minute’s spot, in the words the live payload already uses. |
| `kpis.directional_bias` | The bias result. `{val, sub, raw}`. |
| `kpis.regime`, `kpis.net_gex`, `kpis.pcr_oi` | The same minute’s book. Net GEX uses the live formatter. Positive stays call-heavy. |
| `ladder` | ATM ± 6 strikes at `strike_step_for`, plus the call wall, put wall, flip, max pain, and magnet if they fall outside that band. Each row has `strike`, `call_gex`, `put_gex`, `net_gex`, `call_oi`, `put_oi`, and the boolean badges the ladder already renders. GEX comes from `per_strike`. Open interest comes from that minute’s chain. |
| `standing_oi` | Call and put open-interest centres on that chain. |
| `structures` | `_concentration_structures` on this minute’s book and the signals emitted so far. |
| `orderflow_alerts` | Signals the classifier emitted on minutes at or before T. Each alert’s `ts` is that bar’s start. |
| `market_alert` | `evaluate_market_alert(payload, now_ts=playhead_unix)`. |
| `cockpit_recommendations` | The recommendation batch at T, in the card shape the cockpit quadrant already renders. |
| `as_of_time`, `strike_step`, `exec_session_status` | The playhead clock, the index step, and the regime line with this spot. |
| `session_schedule`, `next_phase_countdown` | Derived from the playhead clock, not the wall clock. |
| `value_area`, `prior_marks` | The previous session’s futures file when that file exists. Labeled with that date. |
| `participant_holdings` | A participant file whose date is strictly before the replay day. A same-day file is lookahead and is not read. |
| `futures_state`, `strike_doi` | Futures open-interest change and per-strike OI change against the previous stored minute. |
| `gamma_blast`, `crossover_watch`, `magnet_path` | Only the pure functions of this chain and prior sessions on disk. |

These stay null, with an explicit reason, and are never replaced with a constant:

- `india_vix`
- `market_breadth` and any sector snap
- news
- `pre_market` except while the playhead is before 09:15 and a prior-session book exists to show as the setup

`GET /api/status` adds the fields the header already writes: `label`, `expiry_target`, `dte_badge`, `mode_desc`, and `day_ohlc`. The day high and low are the futures high and low from the first stored minute through T. Expiry is the expiry in force on the session date (`next_weekly_expiry` or `next_monthly_expiry`, exact symbol match). `feed_title` and `feed_subtext` say this is the stored session for that date. `session_label` is `REPLAY`. The chart badge stays `REPLAY`. The banner does not say the Dhan socket is live.

### 19.3 Chart

`GET /api/sentinel-tape` returns futures candles from the armed start through T under `bars`, with `time`, `open`, `high`, `low`, `close`. `time` is `floor((playhead_unix + 19800) / interval) * interval` for the timeframe the page asked for. One-minute steps inside a five-minute bucket update that bucket’s high, low, and close. They do not invent prints inside the minute.

When the sentinel day file covers the minute, `vwap`, `delta`, and `cvd` are copied onto those bars, and the tape KPIs (`cvd_regime`, `net_cvd`, `buyer_ratio`, `institutional_blocks`, `absorption_status`) are computed from the bars through T. When it does not, those fields are null and the tape says the ledger was not kept. The futures candle still draws.

Call-wall, put-wall, and flip price lines on the chart are this minute’s levels.

### 19.4 Signals

A sentinel signal is `evaluate_bar` on the completed bar, in order, with the walls of that same minute. The stored `sentinel_signals` table is not the source. It can disagree with the current classifier, and it disappears on the next live sync.

The classifier’s relative-volume lookup may use earlier days’ volume at the same time of day. It must not see a later minute of the replay day.

A signal’s forward return stays off the tape until the playhead has passed that horizon. A 15-second micro-burst is not generated. The bar does not contain it.

### 19.5 Recommendations and the target engine

`generate_trade_recommendations` and `run_target_engine` run on the dashboard built in section 19.1. The batch is what `/api/recommendations` and the cockpit recommendation quadrant return.

Rules that keep the card honest:

- Spot, walls, flip, and the chain last are the minute’s own. The generator’s default spot and default walls are discarded.
- Premiums and deltas come from that minute’s chain. A missing close omits that leg. There is no model premium.
- Playbooks that require a sentinel signal, including the absorption fade, are absent when the sentinel ledger is absent. Chain-only playbooks still run, and the card says the tape input was absent.
- `compute_sector_breadth_context` and `compute_fii_positioning_context` are not called. They read the network or a file that is not the minute tape. Their contribution stays zero and the reason says the input was absent.
- `is_lunch_hour_chop` and any other clock check use the playhead.
- Mark-to-market on a card uses the chain close at T. A target, stop, or invalidation is decided only by minutes the walk has already visited.
- `/api/recommendations/alerts` lists those lifecycle events from this walk. It does not call `get_unacknowledged_alerts` on the live ledger.
- Effectiveness and the scorecard count only signals and practice tickets whose horizon or exit is at or before T. The live efficiency database is not read.
- `run_target_engine` is called with `persist=False`. Nothing is written to the target-engine store.

`rec_quotes` on the tick stays limited to practice tickets in `practice.db`. A recommendation card is not marked as a paper fill unless the trader confirms one.

### 19.6 The other tabs

Each route runs the same pure function on the playhead frame.

| Route | What it shows |
| :--- | :--- |
| `/api/target-engine` | The `persist=False` result at T. |
| `/api/options-analytics` | Expected move, the buildup matrix, and the IV term structure from this minute’s chain. An expiry that is not in the parquet is not added. |
| `/api/session-commentary` | Text composed in memory from the book and the signals through T, with the story phase taken from the playhead clock. Commentary tables are not read and not written. |
| `/api/sentinel-tape` | Section 19.3 and 19.4. |
| `/api/journal` | `practice.db` only, marked at T. |
| `/api/scorecard`, `/api/signal-efficiency` | Section 19.5. Empty until a generated signal’s horizon has passed. |
| `/api/ops-calendar` | The holiday calendar and the session phase of the playhead. |
| `/api/market-intel` | Only what the chain and prior completed sessions support. A sector or news fetch is not performed. The response says that input is absent. |

### 19.7 When the screen updates

The cockpit poll is 20 seconds and the status poll is 5 seconds. At 60× that is many market minutes late. After each published `TICK`, `controls.js` calls `loadCockpit` and the loader for the tab that is open. Those requests return the memo for the current playhead.

The tape’s fresh-signal check uses `Date.now()`. On a `REPLAY` tick the age is the playhead Unix time minus the signal’s `ts`. A signal from the session morning stays fresh at the playhead and is not judged against the wall clock.

The header clock, the spot, the futures price, the basis, and the day high and low follow the same minute. India VIX stays an em dash.
