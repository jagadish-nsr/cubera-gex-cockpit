# Session Evaluation — Wednesday, 25-Nov-2026

Read-only review of prod journals (`cubera-gex-cockpit-prod/data/journal`,
`sentinel.db`, `admin_ops.db`) and prod logs (`logs/ingest.jsonl`). Nothing
was changed, committed, or promoted.

Prod is serving **v5.3.169** from the prod worktree (listener pid 87061,
cwd `cubera-gex-cockpit-prod`, promoted 08:26). Health at 19:01:
`ready=true`, `OFF_SESSION`.

**Calendar context.** Nifty's weekly expiry was Mon 23-Nov (Tue 24-Nov was a
holiday, Gurpurb). Today, Wed 25-Nov, is **not** an expiry for any index.
**Sensex expires tomorrow, Thu 26-Nov.** The blast capture therefore skipped
itself today ("not an expiry day"). Tomorrow's Sensex blast is the first real
test of the tick feed since its WebSocket auth failure on 23-Nov.

## Headline

| Index | Prev close | Discovery (09:08) | Day range (timeseries) | Last / settle | Gap |
|---|---:|---:|---|---:|---|
| Nifty | 25,349.65 | 25,658.35 | 25,664 – 25,939 | 25,791 / 25,819 | +309 (+1.2%) UNFILLED |
| Bank Nifty | 62,585.35 | 63,408.60 | 63,271 – 64,094 | 63,652 / 63,703 | +823 (+1.3%) UNFILLED |
| Sensex | 82,435.99 | 83,436.89 | 83,478 – 84,472 | 83,982 / 83,938 | +1,001 (+1.2%) UNFILLED |

All three gaps stayed unfilled all day. The premarket gap-up read was
directionally right and the index closed above discovery. The
"runaway" read on Sensex fits. Nifty's "shared gamma" test never touched its
26,000 call wall (max 25,939).

The day was a **trend-up with two-way chop** on Nifty and Bank Nifty, and a
**gap-up, fade, re-gap** day on Sensex. Sensex flipped to NEGATIVE regime at
10:45–11:32 and at 12:59. Nifty and Bank Nifty stayed POSITIVE all day.

**Verdict in one line.** Plumbing is much better than 23-Nov (chain publishes
all day, readiness journal present, commentary flowing, Sensex recorded, GO
closes at session end, side≠stance fix verified live). The live data is still
**degraded for much of the session** and the CALL/PUT chase alerts are
**noisy with poor hit rate**. The chase problems are in the design of the
alerts, not in the side logic that was just fixed.

---

## Reliability scorecard

| Metric | Value | Notes |
|---|---|---|
| Serving tree | **Prod** (`cubera-gex-cockpit-prod`, v5.3.169) | Split brain from 23-Nov is closed |
| Chain publishes | **2,168** (NF 723 · BN 729 · SX 716), 09:07–15:40 | Continuous. 23-Nov went silent after 09:16 |
| Chain fetch latency | `network_ms > 10 s` in **1,713 / 2,168 (79%)** snapshots · max 34.4 s | Fetch budget is 35 s |
| Circuit breaker | **86 trips** · 703 "chain still running after 35 s" · **1,040 LKGS DEGRADED** serves (NF 325 · BN 333 · SX 382) | Live chain was degraded most of the session |
| Spot held (`spot_stale=true`) | NF 92 · BN 99 · SX 109 snapshots · max held **912 s / 1,371 s / 1,021 s** | BN spot held up to ~23 min |
| OI age (snapshot) | Max NF 11 min · **BN 23 min** · SX 8 min · `oi_stale` **never true** | Gate is `OI_STALE_HOURS = 30` (built for NSE EOD OI) |
| Timeseries `stale` column | **False on every row** | Degradation not visible in the journal |
| `slo.readiness.minute` | **1,128** rows (1,080 INFO · 48 WARNING) | 23-Nov had 0. Fixed |
| Ingest heartbeat | Ok to ~19:01, then **"heartbeat write failed"** tracebacks at 19:04 | Not investigated |
| Ops DB noise | **28,776** `process.started` + 4 lane starts **every ~2 s** (~169k rows) | Journal bloat, buries real events |
| Slow HTTP | 193 `http.slow` (avg 1.7 s): `/api/cockpit-data` 60 · `/api/status` 28 · `/api/go/decision` 23 · `/api/liquidation` 21 · `/api/wall-chase-watch` 9 | Slow reads, no outages |
| Blast WS | Capture skipped (not expiry) | 0 ticks, but also **0 WS attempts**, so auth is still untested |

### What burned

Chain reliability is the root of most of the day's noise. The fetch regularly
took 8–34 s, so the breaker tripped (86 times) and LKGS served stale
snapshots (up to 810 s). The **spot was held** for long periods while the
chain kept publishing. Those snapshots still wrote timeseries rows with
`stale=False` and fed the alert engine. `oi_stale` cannot fire for intraday
OI because its threshold is 30 hours.

---

## Premarket

| Item | Value | Notes |
|---|---|---|
| Discovery | All three at **09:08–09:09** | Discovery is working |
| Regime | NF & BN `SHORT_GAMMA_WALL_TEST` · SX `GAP_UP_RUNAWAY` | Favored plays: "watch the shared gamma strike" / "watch the gap" |
| Gap fill | All **UNFILLED** all day | Correct: min prints never got back to prev close |
| Pre-discovery open (09:00–09:08) | **NF store_open 25,368** and **BN 62,656.6** | Both equal the **23-Nov openings**, carried over until discovery overwrote them. Flipped to `None` and back for several minutes |
| Sensex pre-open | `spot-open` 84,421.97 for ~10 s, then `dhan-open` 83,436.89 | Two different "open" references, last one wins |
| Digest | **Sealed 10:32:47**, after the open | Not a pre-open digest. Narrative is `heuristic` (no LLM) |
| Digest catalyst | `HIGH_SHOCK` (intensity 30) | Most headlines are impact 2–4. Two items are dated 24-Nov evening. The "Sensex rockets 1,400 pts" headline is 07:11 and unverified |
| Sensex prev close 82,435.99 | Consistent with the 23-Nov selloff (≈ −1.8% from the 20-Nov close 83,920) | **Unverifiable**: no 23-Nov Sensex journal exists |

**What went well.** Discovery, gap classification and the unfilled-gap call
all held. Sensex's "runaway" classification was the right read.

**What went wrong.** The digest arrived after the open. Pre-open store values
carried yesterday's openings. Sensex has two "open" definitions, so the
discovery gap (+1,001) and the 09:15 commentary gap (+1,372) disagree.

---

## Sentinel tape / order flow

Signal counts come from `sentinel.db`. Outcomes come from `signal_outcomes`.

| Symbol | 1-min bars | Last bar | Signals | Target hits | Stopped | Hit rate (tgt / tgt+stop) |
|---|---:|---|---:|---:|---:|---:|
| Bank Nifty | 337 | 14:51 | 48 | 22 | 25 | 47% |
| Nifty | 337 | 14:51 | 58 | 18 | 38 | 32% |
| Sensex | 334 | 14:48 | 43 | 8 | 34 | 19% |

- **Tape dark 14:51 → 15:30 on all three futures.** The WebSocket feed stalled
  while the REST futures CSV kept writing to 15:29. The gap watchdog logged
  resyncs at 14:31, 14:42, 14:46 and 14:49, and one at 14:52 with no completion
  line. The tape never came back.
- Outcome quality by type: **SENSEX `CVD_DIVERGENCE_BULL` 0 of 9** targets (avg
  15-min return −629 pts). **NIFTY `CONTINUATION_BULL` 9 of 23** targets. BN
  `CVD_DIVERGENCE_BEAR` 7 of 17 is the best bearish read.
- Sample size is small (149 signals, one day), so treat these as a
  reliability check, not a verdict.
- Daily CVD closed NF +704k, BN −10.5k, SX +6.3k.

**Gap:** the 14:51 stall means ~40 min of close-window tape is missing, which
is exactly where GO exits and session-end reads depend on it.

---

## GO / recommendations

### GO desk

| Item | Value |
|---|---|
| Positions closed | **10** (SX 8 · NF 2) · **0 open** at close |
| Net P&L (1 lot each, after costs) | **≈ +₹24,874** (SX +₹21,463 · NF +₹3,411) |
| Win rate | **8 of 10** |
| Session-end settlement | 3 rows closed at 15:40:01 as `SESSION_END` — fix from 19-Nov worked |

| Entered | Symbol / strategy | Exit | Net ₹ |
|---|---|---|---:|
| 09:20 | SX STRUCTURE | TRAIL_STOP 12:33 | +5,052 |
| 09:20 | SX MOMENTUM | TRAIL_STOP 12:33 | +5,543 |
| 09:22 | SX ICEBERG_FADE | TRAIL_STOP 12:33 | +5,587 |
| 09:41 | SX VOL_DISPERSION | TRAIL_STOP 12:33 | +4,446 |
| 10:02 | SX VANNA-CHARM | TRAIL_STOP 12:27 | +4,214 |
| 10:56 | NF STRUCTURE | SESSION_END 15:40 | +3,392 |
| 11:41 | NF ICEBERG_FADE | SESSION_END 15:40 | +19 |
| 12:33 | SX ICEBERG_FADE | TRAIL_STOP 14:02 | +3,257 |
| 13:11 | SX VOL_DISPERSION | SESSION_END 15:40 | **−5,801** |
| 14:02 | SX ICEBERG_FADE | SESSION_END 15:40 | −834 |

- **The day's P&L came from five early Sensex longs** that all trailed out at
  12:27–12:33. The two losers are both afternoon entries.
- **The biggest loser held to the close.** The 13:11 `VOL_DISPERSION` lost
  ~57% of premium (−505 → −218) and was never stopped. It exited only at
  `SESSION_END`. Check the stop policy for directional GO positions before
  the trail arms. This is the most important GO question from today.
- Sensex GO emitted **67 FIRE** rows and entered 8 positions. Most FIRE
  states never became trades. I did not confirm why (the `go_skips` query
  returned nothing for today), so this is an open question.

### Recommendations

| Item | Value |
|---|---|
| Rows with today's `triggered_at` | 384 (208 created today) |
| Closed today (outcome_ts) | 323 · TGT1 131 · TGT2 13 · SL 76 · INVALIDATED 103 |
| Hit rate (TGT / closed) | **45%** |
| Still **ACTIVE** at 19:01 | **61 from today** |
| Stale **ACTIVE** from earlier days | **752 rows** (back to 19-Oct) |

Recs have no end-of-day expiry. GO settles at `SESSION_END`, but
recommendations stay ACTIVE indefinitely. This inflates the open book and
leaves outcomes unresolved.

---

## Session commentary

| Item | Value |
|---|---|
| Entries | **125** (SX 81 · NF 25 · BN 19). 23-Nov had 2 |
| Text source | `heuristic` on 92 · unlabelled template on 33 · **no LLM narratives today** |
| Phases | pre_open, opening_15m, morning, midday, afternoon, closing_30m, MARKET_ALERT |
| Cards with a WALL CHASE chip | **12** cards for **5** WALL_CHASE episodes |

The cards are readable, and the 09:15 gap-up/opening range/wall migration
lines match the timeseries. The MARKET_ALERT stream is the noisy part (see
next section).

---

## Target engine

- Persisted `target_runs` for today: **0** (same as 23-Nov).
- `candidate_trades` 12 and `target_scenarios` 18 were persisted, so the engine
  ran on demand but did not log a run.
- Not evaluable from the journal. Re-run one live request to check whether the
  engine returns a conviction and no-edge verdict as the design says.

---

## Nifty expiry blast / Sensex expiry readiness

- Today: **skipped by design**. Nifty and BN capture only on expiry days.
- Tomorrow (26-Nov): **Sensex expiry**. Pre-warm is at 12:56 IST, freeze at
  13:00. The 23-Nov `WebSocketBadStatusException` has **not been re-tested**
  because today's run never attempted the socket.
- Before tomorrow: prove the blast WS auth works (a supervised dry-run),
  and confirm the Sensex chain is not in breaker-open at 12:50.

---

## CALL / PUT chase alerting — deep dive

This is the core of the request. Data: 13 watch episodes in
`data/journal/watch_episodes/` (12 SENSEX, 1 NIFTY), plus 33 `MARKET_ALERT`
cards in commentary.

### Scorecard

| Slice | N | Targets hit | Hit % | Net pts (approx.) | Avg MFE | Avg MAE | Median life |
|---|---:|---:|---:|---:|---:|---:|---:|
| All | 13 | 2 | 15% | −580 | +80 | −75 | 14 min |
| CALL WATCH | 7 | 1 | 14% | −253 | +77 | −62 | 12 min |
| PUT WATCH | 6 | 1 | 17% | −326 | +83 | −90 | 21 min |
| ALERT_TAPE source | 8 | 2 | 25% | **−32** | +83 | −53 | 12 min |
| WALL_CHASE source | 5 | **0** | 0% | **−548** | +74 | −110 | **69 min** |

Net pts: targets valued at target distance, invalidations valued at the
resolve spot. Ballpark only, from n = 13.

- **Alert funnel.** 33 cards → 13 episodes → 2 hits. That is about **6%
  alert-to-target** conversion, and about 1 in 6 episodes reaches target.
- **Side vs stance.** 0 of 13 episodes disagreed after the fix. Every
  `side` equals the stance side. The 23-Nov fix is verified live.
- **MFE is real, but kill hits first.** Average MFE was +80 pts, yet 11 of
  13 were killed. The kill sat closer to spot than the typical excursion.

### Problems found

1. **Kill distance is a fixed 60-pt fallback on tape episodes.** 5 of 8
   ALERT_TAPE episodes have kill exactly 60.0 pts from spot. This is
   `_DEFAULT_RISK["SENSEX"]=120 × 0.5`, not a wall or magnet. Two others sit
   at 15.7 and 37.1 pts, inside one-minute noise. CLAUDE.md says stops go
   beyond the gamma strikes, never at a fixed distance. The 10:30 CALL won
   despite a 15.7-pt kill, which is luck, not design.

2. **WALL_CHASE targets are unreachable intraday.** The 4 SENSEX chase targets
   sit **453–997 pts** away, well beyond the next wall. The NIFTY one sits
   399 pts away. Zero of five hit. Realised risk-to-reward is effectively
   1:10 on the chase family, and the 69-minute median life shows capital tied
   up while the kill is the only thing that resolves it.

3. **Episode stacking on one move.** The 11:06 CALL WALL_CHASE episode was
   ACTIVE from 11:06 to 12:26 (80 min). Four more CALL episodes opened on top
   of it at 11:23, 11:38, 12:04 and 12:21. Each was invalidated 5–27 min
   later. Reuse dedupe in `analytics/watch_episode.py` only checks trigger
   age (under 15 min), not whether a same-side episode is still ACTIVE. The
   result is five stacked CALL losses from one thesis.

4. **No cooldown after a kill (re-arm within the minute).** The 09:54 PUT
   was killed at 10:19:00. A new PUT (ALERT_TAPE) armed at 10:19:02 and
   was killed in 36 seconds. Another armed at 10:21:21, 1.7 min later.

5. **Most episodes open inside PINNED.** 10 of 13 episodes were opened with
   zone `PINNED` (spot between walls). The wall-chase rule needs a chase zone
   (`ABOVE_CALL_WALL`, `TEST_CALL`, etc.). The tape rule ("5-minute hold above
   ₹84,000 with tape agreement") needs no wall zone at all. Between walls, a
   60-pt kill sits inside the noise.

6. **Two invalidation levels on one card.** The 14:01 PUT WATCH says "clear
   ₹83,500" in its trigger text but has kill ₹83,985 (above spot). A reader
   cannot tell which level invalidates the idea.

7. **Duplicate cards.** Each WALL_CHASE episode produces a primary card and one
   or two follow-up "WALL CHASE ×2 · 📡 SIGNAL" cards with the same stance
   (09:54 ×2, 11:06 ×2, 14:01/14:02). Twelve chip cards for five episodes.

8. **The "WALL CHASE" chip shows on episodes whose source is ALERT_TAPE.**
   The 11:23 card reads "WALL CHASE ×2" but its episode is ALERT_TAPE. The
   chip describes the wall state, not the episode, so the label can mislead.

9. **Stale spot and OI feed the chase engine.** Spot was held up to 23 min
   and OI up to 23 min old at snapshots that generated alerts. Neither flag
   fired. A WATCH on a held spot is not a live read.

### What worked

- **The side fix works.** Side always matches stance, and the 14:01 PUT's map
  side agreed. The old CALL-geometry-on-PUT bug did not recur.
- **Clean winners.** The 09:32 SENSEX PUT hit target in 14 min (MFE +181). The
  10:30 SENSEX CALL hit target in 10 min (MFE +188). Both were ALERT_TAPE
  episodes with a reasonable MFE.
- **Honest invalidation.** Every loss was a real kill, not an expiry, and the
  ledger recorded MFE and MAE faithfully.

### Efficiency verdict

| Question | Answer |
|---|---|
| Alert precision | **Poor**: 2 targets in 13 episodes (15%) |
| Chase efficiency (WALL_CHASE) | **Poor**: 0 of 5, median 69 min held |
| Tape efficiency (ALERT_TAPE) | **Flat**: 2 of 8, net ≈ 0 pts |
| Signal hygiene | **Noisy**: 33 cards for 13 episodes, 4 stacked CALLs, two re-arms within minutes |
| Side logic | **Fixed** (0 mismatches) |

Sample is one day and one regime (a two-way Sensex day). Do not retune on
this alone. The clearest fixes are structural: dedupe on ACTIVE, a cooldown
after kill, a non-fixed kill, a reachable target on wall-chase, and a chase
zone gate on tape alerts.

---

## Scorecard

| Dimension | Rating | Notes |
|---|:---:|---|
| Premarket | **Good** | Discovery, gap class and unfilled call held. Digest late, store carried 23-Nov opens |
| Reliability | **Mixed** | Prod serving, readiness and chain continuous. Chain degraded 79% of fetches, 86 breaker trips, OI gate dark |
| Order flow / tape | **Weak** | Tape dark 14:51 → 15:30. Sensex bull CVD 0 of 9 |
| GO desk | **Good** | +₹24.9k, 8 of 10 wins, SESSION_END fix verified. Biggest loser held to close |
| Recommendations | **Weak** | 61 ACTIVE from today, 752 stale ACTIVE, no EOD expiry |
| Commentary | **Good** | 125 entries, 23-Nov had 2. No LLM narratives. Duplicate cards |
| Blast (Nifty) | **N/A** | Not an expiry day |
| Target engine | **Unknown** | 0 persisted runs; on-demand only |
| CALL/PUT chase | **Poor** | 2 of 13 hits. Side fix verified, design issues dominate |

---

## Gaps / follow-ups (prioritised)

**Before tomorrow's Sensex expiry**

1. **Prove blast WS auth** with a supervised dry-run before 12:56 IST on
   26-Nov. Untested since 23-Nov.
2. **Chain latency and breaker.** `network_ms` above 10 s on 79% of fetches.
   Find out why Dhan chain pulls take 8–34 s and whether breaker trips are
   starving the Sensex snapshot.
3. **Sentinel tape stall at 14:51.** Find out why the WebSocket feed stopped
   after the 14:52 resync request. The 14:52 watchdog logged a request but no
   completion.

**High (chase and stops)**

4. **Watch dedupe on ACTIVE**, not trigger age. Block a new same-side episode
   while one is ACTIVE.
5. **Kill cooldown.** No re-arm for ~5 min after a kill.
6. **Structural kill on tape episodes**, not the fixed 60-pt fallback. Refuse
   a kill inside one-minute noise.
7. **Reachable WALL_CHASE target.** Cap target distance or use the nearest wall,
   not a wall 1,000 pts out.
8. **Chase zone gate on tape alerts.** Do not open WATCH with zone `PINNED`
   unless tape confirms a chase.
9. **Single invalidation level per card.** Remove "clear ₹X" when kill is Y.
10. **Stop policy for GO directional positions** after entry, before the trail
    arms. The 13:11 VOL_DISPERSION held to −57% of premium.

**Medium (data quality)**

11. **Gate spot_stale and OI age intraday.** A session-aware OI threshold (for
    example, >5 min) and `spot_stale` should suppress WATCH and alerts. Set the
    timeseries `stale` column from them.
12. **Pre-discovery open.** Do not expose yesterday's open in the 09:00–09:08
    window.
13. **Pre-open digest** should seal before 09:00. Today it sealed at 10:32.
14. **Recs end-of-day expiry.** Expire ACTIVE recs at `SESSION_END` as GO does,
    and clear the 752 stale rows.
15. **Heartbeat write failure** at 19:04. Find the traceback.

**Low (ops hygiene)**

16. Stop logging `process.started` and lane starts every ~2 s (~169k rows/day).
    Log state changes only.
17. `/api/cockpit-data` (avg 1.9 s) and `/api/status` are the slow reads.
18. `go_skips` returned nothing for today. Confirm the skip path writes rows,
    then explain the 67 FIRE-to-8-entries gap.

---

## Disclaimer

Decision-support review, not financial advice. The human confirms all
decisions. Sample sizes here are one session. Read the chase findings as
hypotheses to test, not as tuning instructions.
