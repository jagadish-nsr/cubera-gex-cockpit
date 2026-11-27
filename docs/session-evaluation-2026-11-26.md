# Session Evaluation — Thursday, 26-Nov-2026 (Sensex expiry)

Read-only review of prod journals (`cubera-gex-cockpit-prod/data/journal`,
`sentinel.db`, `admin_ops.db`, `paper_trades.db`, `watch_episodes/`) and prod
logs (`logs/ingest.jsonl*`, `log_timeseries.jsonl`, `blast_ticks.jsonl`,
`big_money.jsonl`). Extractors: `scratch/session_eval_2026_11_26.py` and
`scratch/session_eval_2026_11_26_chain.py` (outputs next to them).

Code fixes and the new session-start contract tests are in the **dev tree,
uncommitted**. Nothing was promoted. Prod still runs the code that produced
the problems below.

**Calendar.** Sensex weekly expiry (Thursday). Nifty's next weekly is Tue
01-Dec. Bank Nifty's November monthly went on Mon 23-Nov (24-Nov holiday), so
its book today is the December monthly. This was the first Sensex blast since
the 23-Nov WebSocket auth failure.

## Headline

| Index | Prev close | Open | High | Low | Close (15:40 snap) | Change |
|---|---:|---:|---:|---:|---:|---|
| Nifty | 25,819.25 | 25,873.2 | 25,974.9 | 25,733.5 | 25,884.5 | +65 (+0.25%) |
| Bank Nifty | 63,703.25 | 63,883.1 | 64,104.8 | 63,332.3 | 63,535.3 | −168 (−0.26%) |
| Sensex | (not stored) | 84,301.4 | 84,551.7 | 83,754.7 | 84,328.6 | ≈ +390 vs 25-Nov settle 83,938 |

A two-way, range-bound day. Nifty stayed POSITIVE (call-heavy) on every
timeseries row. Sensex was NEGATIVE (put-heavy) on 12 of 32 rows before the
journal went dark at 13:04.

**Verdict in one line.** The market data was fine, but the plumbing failed
it. Every chain fetch spent about 30 s waiting at a rate gate for a request
that takes about 0.25 s. The breaker and the last-known-good copy then served
**576 degraded snapshots**, and readiness reported "ready" throughout. On top
of that, five separate code faults hit the same afternoon:

1. The timeseries lock stopped every index's journal at 13:04.
2. The rec publisher crashed 412 times.
3. The narrator crashed 107 times.
4. Blast and big-money tick times were shifted by 5:30.
5. GO auto-stage bought ₹0.1 options after 15:30.

All five are fixed in the dev tree, and each is pinned by a test that fails
on the committed code.

---

## Reliability scorecard

| Metric | Value | Notes |
|---|---|---|
| Chain publishes | **2,257** (NF 749 · BN 753 · SX 755), 09:07–15:40 | Continuous, no dark gap |
| Rate-gate wait (`chain.timing`) | p50 **27–34 s in every 30-min bucket**; max 176.6 s (09:00) | HTTP p50 **0.2–0.3 s**. Zero HTTP 429 |
| Published `network_ms` | p50 17–21 s, max 35.0 s | Budget is 35 s |
| "Chain still running after 35 s" | **480** (NF 168 · SX 159 · BN 153) | Each one left a worker queued at the gate |
| LKGS degraded serves | **576**, in every bucket | Derivatives frozen to 0 on each (`DerivativeFreezeRule`) |
| Breaker open messages | 62 in the 09:00 bucket, then 1–20 per half hour | Opening bucket was the worst |
| Spot held (`spot_stale`) | 159 snapshots (NF 57 · BN 52 · SX 50), max held ≈ 880 s | 105 of them after 15:00 |
| Readiness SLO | **375 / 375 minutes "ready"**, 216 of them `CHAIN_DEGRADED` | The SLO overstated health by 58% |
| Admin alerts | Chain p95 > 30 s for **all three** indices, all day | Alert fired; nothing acted on it |
| Timeseries | Rows stop at **13:02 / 13:03 / 13:04** (NF / BN / SX) | 19-Nov (also a Sensex expiry) ran to 15:17 / 15:40 |
| Rec publisher | **412** `sigma_90` crashes (09h 90 · 13h 91 · 14h 104 · 15h 119) | No rec triggered after 14:47 |
| Narrator | **107** "datetime is not JSON serializable" | 1 genai card out of 113 |
| Off-market snapshot | **~2,600** false "snapshot 26-Nov is before the last session 25-Nov" warnings | Log noise, also withheld today's row |
| Slow HTTP | `/api/wall-chase-watch` **1,542** · `/api/cockpit-data` 536 | Not investigated |

### What burned: one Dhan quota, three spenders

Dhan allows one `/optionchain` call per 3 s (the gate uses 3.1 s, so 19.4
calls/min). Prod ingest alone needs 3 indices × 2 cycles/min = **6 calls/min**,
plus 7.5/min for the 8 s blast lane after 13:00. That fits. Three things made
it not fit:

1. **An expiry-list call on every fetch.** Each chain pull also asked Dhan for
   the expiry list (`calls=2` for a 1-expiry window), on the same 3.1 s
   bucket. That is about a third of the budget for data that changes once a
   day.
2. **Other processes on the same account.**
   - The dev server on :8650 ran in combined mode until about 09:28, with its
     own chain loops.
   - The dev-tree timeseries cron made **284** live fetches between 09:35 and
     15:39, and all of them timed out.
   - The prod cron's blast loop made **153** live fetches between 13:07 and
     15:39, and all of them timed out too.

   The cross-process gate spaced these calls out, but it does not prioritise.
   Prod ingest waited in the same queue as a dev experiment.
3. **Timed-out fetches stay in the queue.** A fetch that outlives the 35 s wait
   keeps its worker and its gate slot. The next 30 s cycle queued another fetch
   behind it, which made the backlog self-sustaining.

The breaker treated the gate wait as a feed failure, and LKGS served
snapshots up to 245 s old. Readiness counted a degraded book as `ok`, and on
the serve process the breaker states read `CLOSED` because they belong to
ingest. So `/api/health` said ready all day.

---

## Fixes made (dev tree, uncommitted)

| # | Fault | Fix | Where |
|---|---|---|---|
| 1 | Expiry list re-fetched every pull | Cached per IST day, refreshed every 30 min; on failure, falls back to the last list that still has a live expiry | `data/dhan_live.py` |
| 2 | Non-prod processes spend prod's quota | **Chain-quota owner lease.** Prod ingest (`CUBERA_DHAN_CHAIN_OWNER=1`) stamps `chain.owner`, and the heartbeat keeps it fresh. In session, any other process raises `DhanChainYield` before touching the network and serves the owner's published book. The yield neither trips the breaker nor falls back to NSE. A lease older than 60 s or a dead owner pid releases the quota. `CUBERA_DHAN_CHAIN_YIELD=0` overrides | `data/dhan_live.py`, `app/data_access.py`, `app/ingest_heartbeat.py`, `scripts/run_ingest.py`, `run_prod.sh` |
| 3 | Timed-out fetches re-queued | Single-flight per (symbol, expiry cap): later cycles join the running fetch | `app/data_access.py` |
| 4 | Timeseries dark from 13:04 | The `--once` fire releases its 5-min lock before entering the blast loop, which has its own lock | `scripts/log_timeseries.py` |
| 5 | Blast cron pulls Dhan every minute | Reads the published book (≤ 90 s old) first; pulls live only without one | `scripts/log_gamma_blast.py` |
| 6 | Readiness "ready" while degraded | In session, any degraded chain adds `chain_degraded:<SYM>`, so `ready=false` | `app/readiness.py` |
| 7 | Blast/big-money ticks shifted −5:30 | Shared `ltt_to_epoch` matches the true-epoch and IST-shifted forms against now (as the sentinel parser does). Replay keeps the IST form | `data/dhan_feed.py` + 3 collectors |
| 8 | Narrator datetime crash | One `_dumps(..., default=str)` for every prompt builder | `analytics/llm_prompts.py` |
| 9 | False stale-session warnings | A row from today is newer than the last session, not stale (`<`, not `!=`) | `data/market_snapshot.py` |
| 10 | GO bought ₹0.1 options at 15:30–15:32 | Auto-stage skips from 15:30 (`CASH_CLOSE_MIN`) and below ₹1 premium | `web_server.py` |
| — | `sigma_90` crash | Already fixed and tested in dev on 25-Nov, **never promoted**. Prod crashed 412 times today | `analytics/trade_ideas.py` |

### Tests

- **New: `tests/test_session_start_contract.py`**, 34 tests, about 1.5 s.
  - Each test names the date and the count it guards against. 29 of them fail
    on the committed code, so they catch these regressions.
  - It runs in `scripts/promote.sh` (`REQUIRED_RELIABILITY_TESTS`) and in CI.
  - It covers:
    - Lot sizes and the sign model.
    - Expiry-list caching.
    - Chain demand within the rate budget (hand-check: 6 + 7.5 = 13.5 calls/min, under 19.35).
    - Every owner-yield case: in session, off session, override, stale lease, dead pid, own pid, gate off.
    - No NSE fallback while yielding, and no breaker trip on a yield.
    - Reading the owner's book.
    - Single-flight.
    - The timeseries lock released before the blast loop.
    - The blast cron preferring the published book.
    - Readiness with degraded chains in session and off session.
    - `sigma_90` on a shared strike.
    - Datetimes in every prompt builder.
    - Snapshot staleness.
    - Both LTT forms and replay.
    - The GO entry cutoff and premium floor.
- **Suite guard:** an autouse fixture fails any test that tries a live Dhan
  HTTP call. Under pytest the cross-process gate is off, so a live call would
  spend prod's quota unseen.
- **Pre-existing failures fixed.** All 8 that weren't caused by the sandbox
  were test bugs:
  - 3 used hard-coded past expiries or the wall clock: `test_strike_ladder`,
    `test_sentinel_recommendations`, and `test_dashboard_data`, which also hit
    the live Dhan path.
  - 5 used a `MagicMock` bar with no VWAP: `test_pinned_recommendations_persistence`.
- **Full suite: 1,511 pass, 0 fail.** The two `test_blast_concurrency`
  multiprocess tests pass outside the sandbox.

---

## Premarket

| Item | Value | Notes |
|---|---|---|
| Discovery | Chain live at **09:07** for all three | Working |
| Digest | Sealed **13:13 IST** (genai, `gemma4:12b`, 20 events) | Later than 25-Nov (10:32). Not a pre-open digest at all |
| Catalyst | `HIGH_SHOCK`, overnight impact 38 | |
| Path score | BN `MAP_OK` 75 (MFE +90, MAE −547) · NF `HOLD` 78.2 · SX **`MAP_BREACH` 11.5** (MAE −442) | Sensex map was wrong-footed |
| Sensex prev close | **Not stored** in `session_snapshots` | Change vs prior day is hand-computed |

---

## Sentinel tape / order flow

| Symbol | 1-min bars | Span | Gaps > 3 min | Signals | Targets | Stopped | Hit rate |
|---|---:|---|---|---:|---:|---:|---:|
| Sensex | 384 | 09:15–15:38 | none | 43 | 18 | 19 | **49%** |
| Bank Nifty | 384 | 09:15–15:38 | none | 26 | 8 | 18 | 31% |
| Nifty | 384 | 09:15–15:38 | none | 42 | 9 | 32 | 22% |

- **The tape held all session**, unlike the 25-Nov stall at 14:51. There were
  20 silence resyncs, all of which finished `ok=True`.
- **Nifty bull signals failed.** `CONTINUATION_BULL` hit 5 of 22 targets and
  `CVD_DIVERGENCE_BULL` hit 0 of 7. Bank Nifty `CONTINUATION_BULL` hit 0 of 7.
- The best reads were Sensex bear signals: `CVD_DIVERGENCE_BEAR` 5 of 10,
  `CONTINUATION_BEAR` 3 of 5, and `ABSORPTION_BEAR` 2 of 2.
- One day, 111 signals. Treat these as a reliability check, not a verdict.

---

## GO desk

| Item | Value |
|---|---|
| Paper trades | **63** (27 wins), net **≈ +₹6,422** after costs |
| Sensex 14:42–15:32 | **21 entries** on a fading 0-DTE book. Most hit HARD_STOP or TRAIL_STOP within minutes |
| After 15:30 | **3 Sensex ABSORPTION_BEAR entries at ₹0.1 premium** (15:30, 15:31, 15:32), −₹50 each |
| Session end | 6 SESSION_END closes at 15:40. **One Sentinel trade (14:46, SX 84400CE) still OPEN** |
| Biggest losers | Sentinel `SPOT_INVAL` exits: NF EXHAUSTION_DOWN −₹2,295 · BN EXHAUSTION_DOWN −₹2,042 · NF ABSORPTION_BEAR −₹1,883 |

**Orphan paper trades.** `paper_trades.db` holds **13 OPEN rows**: 12 from
13-Nov (5 `SENTINEL_ENGINE`, 7 auto-stage) and the 14:46 Sentinel trade
from today. None has an OPEN `go_open` row, so
`close_all_open_at_session_end` (which walks `go_open`) never sees them. The
14:46 contract expired today. I left these rows alone, because the
exit price for a settlement with no mark has to be your decision (see
follow-ups).

`go_skips` is empty again, so the FIRE-to-entry funnel still can't be
explained from the journal.

---

## Recommendations

| Item | Value |
|---|---|
| Triggered today | **310**: SL_HIT 91 · EXPIRED 83 · INVALIDATED 80 · TGT1 40 · TGT2 16 |
| Target hit rate | 56 of 147 resolved by target or stop (**38%**) |
| By hour | 09h 153 · 10h 88 · 11h 4 · 12h 6 · 13h 39 · 14h 20 · **none after 14:47** |
| Stale ACTIVE from earlier days | **0** (the 25-Nov cleanup held) |
| Refusals | 29 Sensex "0DTE near book rolled off" · 14 "Missing ATM IV" · 15 sentinel score/type gate |

The afternoon stop is the `sigma_90` crash: the publisher raised on 91–119
cycles per hour from 13:00. Half the morning's triggers came in the first
hour.

---

## Session commentary

| Item | Value |
|---|---|
| Entries | **113** (SX 68 · NF 23 · BN 22) |
| Text source | genai **1** · heuristic 100 · unlabelled 12 |
| Narrator errors | **107**, all datetime serialization (fixed, item 8) |

The narrator wrote genai text once all day. Each crash fell back to the
template, so the cards are readable but none were rewritten. A line-by-line
audit like the one for 25-Nov was not repeated today.

---

## Sensex expiry blast

| Item | Value |
|---|---|
| 1-min blast tape (`gamma_blast_dhan`) | 106 minutes, 13:04–15:39 |
| Blast score | 183 rows: NO_TRIGGER 113 · COILING 57 · **TRIGGERED 13** (13:04–15:19), max **96** |
| Blast ticks (WS) | **134,563** ticks. The WS connected at 13:00 and reconnected 5 times (13:11, 13:13, 13:33, 13:53, 14:45) |
| Tick timestamps | Stored **07:30:55–10:09:58**; the real span is 13:00:55–15:39:58 (shifted −5:30) |
| Big money | Connected 09:15, disconnected 15:40; **25-Nov and 26-Nov events shifted −5:30** (19-Nov correct) |
| Timeseries | Dark 13:04 → close for **all three** indices (the lock bug, item 4) |
| Near book after expiry | Walls 0 and flip = spot at 15:40, by design (`near_book_expired`), but `session_snapshots` stores flip = spot as if it were a level |

The WebSocket auth problem from 23-Nov is gone: the socket connected and
recorded the whole window. The tick content is good, but the timestamps
aren't. Dhan sent a true epoch this week where the collectors assumed IST
wall time. **The stored 25/26-Nov tick and big-money rows need +19,800 s.** I
did not rewrite them. That repair is in the follow-ups.

---

## CALL / PUT watch

| Episode | Trigger | Source / zone | Target / kill | Result | MFE / MAE |
|---|---|---|---|---|---|
| SX CALL | 10:45:01 | WALL_CHASE / TEST_CALL | 84,500 / 84,347.6 | **TARGET_HIT** 10:51 | +76.7 / −23.0 |
| SX PUT | 12:51:07 @ 84,292.5 | WALL_CHASE / TEST_PUT | 83,892.5 / 84,443 | EXPIRED | +298.4 / −141.3 |
| SX CALL | 14:42:15 | WALL_CHASE / TEST_CALL | 84,600 / 84,122 | EXPIRED | +131.4 / −44.7 |

- `side ≠ stance` occurred **0** times; the 23-Nov fix still holds.
- All three opened in a chase zone, not `PINNED`. That is better than 25-Nov.
- The PUT's target was **400 pts** away. Its best excursion was 298, and it
  came within 9 pts of the kill. That is still the reachability problem
  from 25-Nov.
- **Both expiries were stamped at 20:19:29, not at the close.** On an expiry
  day, the watch should resolve at 15:40 against the closing print.

---

## Scorecard

| Dimension | Rating | Notes |
|---|:---:|---|
| Reliability | **Poor** | Gate wait ~30 s all day, 576 degraded serves, readiness lied, timeseries dark from 13:04 |
| Premarket | **Weak** | Discovery fine; digest at 13:13; Sensex map breach |
| Order flow / tape | **Good** | 384 bars, no gaps, Sensex 49% |
| GO desk | **Mixed** | +₹6.4k, but post-15:30 lottery entries and an orphan OPEN |
| Recommendations | **Poor** | Publisher crashed 412 times; nothing after 14:47 |
| Commentary | **Poor** | 1 genai card; 107 narrator crashes |
| Sensex blast | **Mixed** | WS works and the tape was recorded; timestamps shifted 5:30 |
| CALL/PUT watch | **Fair** | 1 of 3 hit, clean sides, targets still too far |

---

## Follow-ups (prioritised)

**Before the next session (needs your go-ahead)**

1. **Commit and promote** the dev-tree fixes. Promote now runs the
   session-start contract. Two complications:
   - The dev tree also holds your own uncommitted work: `pre_open_watch`,
     `premarket_path`, `trade_ideas`, the cockpit UI, `market_snapshot`,
     `pre_post_market`, and `web_server`.
   - The prod tree has its own uncommitted edits.

   Decide what ships together.
2. **Restart prod after promote**, so ingest starts with
   `CUBERA_DHAN_CHAIN_OWNER=1`. Check that
   `/tmp/cubera_dhan_gate/chain.owner` refreshes every ~10 s and that
   `chain.timing` gate p50 falls well under 10 s.
3. **Dev server in session.** A restarted dev :8650 now yields the chain and
   mirrors prod's book automatically. `COCKPIT_INGEST_EXTERNAL=1` is still the
   cleaner mode. The dev cron also yields now.

**High**

4. **Repair 25/26-Nov tick times.** Add +19,800 s to blast-tick and big-money
   rows for those dates. This is a one-off script with a backup, run with your
   go-ahead.
5. **Orphan paper trades.** There are 13 OPEN rows that no session-end sweep
   reaches. Pick the settlement rule (last mark, intrinsic at expiry, or
   "unsettled") and extend the sweep to `paper_trades`.
6. **Watch expiry at the close.** Resolve open watches at 15:40 on the
   closing print, not on the next process start.
7. **Pre-open digest timing.** It sealed at 13:13 today. Find out what blocks
   the seal.

**Medium**

8. **Session snapshot after expiry.** Do not store flip = spot as a level
   when the near book has rolled off. Store the Sensex prev close.
9. **`/api/wall-chase-watch` slow 1,542 times.** Profile it.
10. **Timeseries `stale` column** is still never true (from 25-Nov). Write it
    from `is_degraded` and `spot_stale`.
11. **Bank Nifty walls.** Put wall 65,000 and call wall 66,000 were both above
    spot (63,300–64,100) all day, with the flip near 61,900, on the December
    monthly book. Check by hand against the chain whether that is real OI
    concentration or a strike-window issue.
12. **`go_skips` empty**, so the FIRE-to-entry funnel is still unexplained.

---

## Follow-up status (27-Nov, v5.3.172)

Items 1–3 shipped in v5.3.171. Items 4–12 are fixed on `dev` and promoted.
The prod data repairs below were each run after a dry run, with a backup
taken first.

| # | Root cause | Fix | Prod data repaired |
|---|---|---|---|
| 4 | Old LTT decode subtracted 5:30 from true-epoch ticks | `scripts/repair_ltt_shift.py` (refuses a day with any row ≥ 10:10) | big_money 20/25/26-Nov (1,009 / 1,504 / 1,185 rows) now span 09:15–15:39; SENSEX blast 26-Nov (134,563 ticks) 13:00:55–15:39:58. Backups `big_money.db.bak-ltt-20261126-231617`, `blast_ticks/SENSEX/2026-11-26.csv.bak-ltt-20261126-231617` |
| 5 | Sentinel and auto-stage `paper_trades` rows had no `go_open` row, so no sweep closed them; 13-Nov Sentinel opened twins in the same second | `data/journal/paper_settlement.py`, run by the 15:30–16:10 post-market loop: mirror the linked `go_open` close, void same-second twins, settle 0DTE at intrinsic on the expiry close, else mark at the rec's last same-day LTP, else void. `_open_or_skip_paper` reuses an open same-strike row | 7 Nifty rows mirrored to `go_open`, 5 twins voided at ₹0, 84400CE settled at 0 (−₹2,108 net). 0 OPEN rows left. Backup `paper_trades.db.bak-settle-20261126-235318` |
| 6 | Watch expiry ran 3h from trigger, then on the next process start | Deadline is the earlier of 3h or 15:30 that day, stamped at the deadline | Two 26-Nov Sensex watches restamped to 15:30 (`restamped_from` kept; `*.bak-expiry-stamp`) |
| 7 | The digest sealed only when `/api/market-intel` was requested | Ingest runs a sealer thread in pre-open; a late build drops headlines after the open and is marked `sealed_late` | — |
| 8 | Session-close snapshot stored flip/GEX from an expired near book; first write of a session had no prior close | Flip and net GEX stored as NULL with `:near-book-expired`; first write of a new day carries the stored spot as prev close | SENSEX row: prev close 83,938.28 (from the 25-Nov 22:08 backup, cross-checked), flip/GEX NULL. Backup `market_snapshot.db.bak-sx-repair-*` |
| 9 | Five thread-pool hops per poll queued behind other work (offline the build takes ~2 ms per index) | One hop, 3 s shared response, single-flight; `timing_ms` in the payload | — |
| 10 | `stale` read only `oi_stale` | `stale` = OI stale, or chain degraded, or spot frozen while ATM premium moved vs the previous strike snapshot | — |
| 11 | **Not a bug.** Independent Black-Scholes gamma on the 15:40 book (one expiry, 29-Dec): 66,000 has the largest call gamma, 8% ahead of 65,000; 65,000 has the largest put gamma, 24% ahead of 66,000. Real round-strike concentration on the December monthly. **The check found a different bug:** the Dhan 1-min backfill wrote OI in shares next to live rows in lots, so the footprint saw 20–65x OI jumps at every backfill/live boundary | Backfill divides by the config lot and refuses without one; `scripts/repair_backfill_oi_units.py` | 28 `oi_strikes_dhan` files (30-Oct to 26-Nov, all three indices). Backups `<date>.csv.bak-oiunits-20261127-005418`. A re-run refuses every file |
| 12 | Auto-stage returned its skip reason to the poller and dropped it; only the manual Skip button wrote `go_skips` | Every declined FIRE is written once per IST day per (fingerprint, reason), with `source='auto'` and a `reason`. Manual skips are tagged `manual` | — |

**Found along the way, fixed:** the test suite wrote into the real
`market_snapshot.db`. A spot-tick test's background save stored NIFTY 25,250
as a ws-tick, and a Dhan-sync test's fake 27-Oct book was persisted by a
running fetcher loop, replacing the 26-Nov row. Promote runs these suites. The
bogus NIFTY 25,250 in the 25-Nov 22:07 snapshot backup fits the same test,
though I can't prove how it got there. Tests now read a session copy of the
file, and `save_snapshot` refuses any write older than the stored session.
Prod's three rows were checked and are clean.

## Second pass (27-Nov morning, v5.3.173)

| Finding | Root cause | Fix |
|---|---|---|
| Three 13-Nov Nifty GO rows closed 17-Nov 09:04 (pre-open) at TRAIL_STOP | They were never closed on 13-Nov (one was staged at 17:50, before the stage gate existed). Monday's pre-open mark refresh priced them off the pre-open book and trail-stopped them | `mark_open` refuses outside the continuous session and on rows opened on an earlier day. `close_stale_open` closes earlier-day rows at their last in-session mark, stamped at that day's 15:30 close |
| Paper settlement and GO session-end ran only 15:40–16:10 on a session day | No catch-up when ingest was down then, and none on weekends | Ingest runs a housekeeper at boot and every 5 min, on any day: stale GO close, then paper settlement |
| `/api/cockpit-data` cold build 4–10 s | Not thread-pool queueing. 9.4 of 9.8 s were three synchronous Ollama calls (90 s timeout each): positioning card, chop card, alert voice | A cache miss returns the deterministic text and queues the rewrite on one background worker. Cold build 0.23 s |
| Bank Nifty put/call IV gap | Measured, not what I guessed. Dhan's IVs match neither a zero-carry nor a parity-carry solve. Re-implied from mids under the parity forward (+305 pts), call and put agree within 0.5 vol pts (~17.5%); Dhan has calls 2–3 pts low and puts ~2 pts high. Nifty's gap is the other way (−1.5) | **Engine unchanged.** On the 26-Nov books, switching to mid-implied IV flips Bank Nifty's regime from POSITIVE to NEGATIVE and removes the Nifty and Bank Nifty flips, so it needs intraday evidence first. Now measured on every compute: `Levels.iv_parity_gap`, a flag and log line at ≥2 pts, and `kpis.regime.iv_parity_gap` in cockpit-data |

**Sentinel had the same hole (v5.3.174).** The lifecycle evaluated every
ACTIVE recommendation, whatever its trigger day, and a target exit pays
`max(LTP, target)`. When the 15:40 expiry was missed, the next session's first
tick "hit" the target at the gap price. On 25-Nov at 09:16:01 the 20-Nov Bank
Nifty 64500 CE *and* 64600 PE both did (+₹18,002 and +₹41,847). Now the
lifecycle skips recommendations triggered before today and idles outside the
session. The housekeeper expires earlier-day recommendations at any hour,
leaving `updated_at` alone so paper settlement still uses the entry-day mark.

**Still open:**

- **Cross-day P&L in the books.** The audit found 11 GO rows held overnight,
  not 3 (13-Nov to 20-Nov, about +₹74,600 net together). The largest are
  Bank Nifty 16→20-Nov at +₹71,160, and Nifty and Sensex 18→20-Nov at
  −₹10,516 and −₹7,316. There are also 10 Sentinel paper rows closed on a
  later day (9-Nov to 25-Nov, including the two gap "targets" above). The
  minute journal (`oi_strikes_dhan`) tracks the nearest expiry, while these
  trades priced a different one (entry 269.4 against a journaled 160.8), so
  no truthful same-day exit can be rebuilt. Voiding them, or excluding them
  from stats, is your call. Two GO/paper pairs also disagree: Bank Nifty
  ₹71,160 against ₹74,933, and the 19-Nov Nifty trade ₹962 against ₹3,854.
- **IV source for gamma.** Collect intraday vendor-vs-mid IV (the
  uncommitted quote-audit work journals `call_iv`/`put_iv` and bid/ask per
  strike), then decide whether gamma should use mid-implied IV under the
  parity forward.
---

## Disclaimer

Decision-support review, not financial advice. The human confirms all
decisions. Sample sizes are one session. Read the signal and watch findings
as hypotheses to test, not as tuning instructions.
