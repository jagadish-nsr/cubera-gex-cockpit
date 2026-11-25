# Session Evaluation — Monday, 23-Nov-2026

Nifty weekly expiry (holiday-adjusted Monday). Risk-off / put-chase day.
Live tag claimed **v5.3.166**, but port **8640 cwd = DEV tree** (not
`cubera-gex-cockpit-prod`). Cron still wrote blast/timeseries into **prod**
journals — split brain. Canvas: `session-eval-2026-11-23.canvas.tsx`.

Nifty timeseries **25,937 → 25,350** (−587). Bank Nifty **65,109 → 62,609**
(−2,500). Sensex timeseries **missing** on prod; auction/path thin.

## Reliability scorecard

| Metric | Value | Notes |
|---|---|---|
| Process / port | 8640 from **DEV cwd** | `lsof` cwd = `cubera-gex-cockpit`, not prod worktree |
| Last promote | v5.3.166 @ 20-Nov 19:23 | No promote today |
| `slo.readiness.minute` today | **0** | Last print 20-Nov 19:22 — SLO meter dark |
| Chain publishes | **63**, all **09:07–09:16** | Then silent for the cash day |
| Ingest / serve start events | **None** today | Only `big_money` + `blast_ticks` cron |
| NSE fallback | 504 | Nifty 252 · BN 252 (timeseries path) |
| Timeseries | NF 81 · BN 73 · **SX 0** | Densify OK where written; Sensex starved |
| Commentary (prod DB) | **2** pre_open only | Session commentary effectively dead on prod journal |
| Sentinel 1m / signals | **0 / 0** | No tape ledger for the day |
| GO / Ideas outcomes / paper | **0** | No desk events in prod DBs |
| Blast ticks | **0** all window | `WebSocketBadStatusException` every minute 13:00–15:39 |

### What burned

This was not a soft-mode day — the **live dual stack did not stay armed**. Cron
(timeseries, blast score via chain snapshot, big_money) kept writing into prod
paths while the UI process on 8640 was the DEV tree. Readiness minutes were not
journaled; Ideas refused 21× on missing VWAP whenever something polled.

## Premarket

| Item | Value |
|---|---|
| Digest file | **Missing** (`pre_open_digest_2026-11-23.json` absent) |
| Nifty auction | Open **25,368** (−146 / −0.57%) · `PUT_WALL_BREAKDOWN_TEST` · shared-gamma watch · gap **FILLED** · path INVALIDATED by T0915 (print ~25,801) |
| Bank Nifty | Open **62,657** (−406 / −0.64%) · `PUT_WALL_BREAKDOWN_CASCADE` · watch hold below put · gap **FILLED** · path INVALIDATED immediately (T0915 ~64,635) |
| Sensex | No auction row completed / no timeseries |

Premarket bearish cascade tags were **directionally closer** than last Friday’s
false cascade, but path scores still invalidated in seconds as the open thrust
printed away from discovery — discovery vs first continuous print still noisy
(especially BN).

## Close book / KPIs

| Index | Close snap | Open→close (ts) | Walls (late) | Note |
|---|---:|---:|---|---|
| Nifty | 25,349.7 (lkgs) | −587 | shared ~25,350 then cleared | Expiry; TE refuse `near_book_expired` |
| Bank Nifty | 62,585 (lkgs) | −2,500 | shared ~62,600 | Hard selloff |
| Sensex | stale 20-Nov snap | — | — | No SX journal today |

Pin efficiency unset on auctions (no settle wrap). Not a pin day — trend down
with walls marching lower.

## Order-flow / Sentinel tape

**Empty in prod sentinel.db for 23-Nov** (0 bars, 0 signals, 0 daily CVD).
Cannot score CONT/CVD hit rates from the ledger. Futures CSV on prod has Nifty
prints; big_money reconnect storm (399/359) without a usable CVD close row.

## GO / recommendations

| Surface | Today |
|---|---|
| GO events | **0** |
| Ideas triggered / outcomes | **0** |
| Ideas refuses | 21 · all “Missing session VWAP” |
| Paper entered | **0** |
| TE runs persisted | **0** |
| TE off-session API | Nifty/BN **`near_book_expired`** (explicit 0DTE copy) · Sensex **bear** confluence |

## Session commentary

Prod commentary DB: **2** entries at 09:14 (Nifty opening range, BN call wall
broken). No morning/midday/close cards. Watch-episode commentary lived on the
**DEV** journal path used by the 8640 process — not the prod commentary DB.

## Nifty expiry — gamma blast (not Sensex)

Sensex is Thursday; today is **Nifty (+ BN same-day capture) expiry**.

| Lane | Result |
|---|---|
| Score CSV | Nifty 157 · BN 157 rows (13:03→15:39) |
| Nifty states | NO_TRIGGER 88 · COILING 51 · TRIGGERED 18 |
| Nifty TRIGGERED | PIN/COMPRESS 10 · **ACTIVE 8** (13:19→15:18, FAKE-leaning) |
| BN TRIGGERED | PIN 5 · **ACTIVE 3** (max score 72) |
| Tick tape | **0 ticks** — WS bad status entire window |
| Sensex blast | skipped (no spot) |

Blast **score** path ran; **tick** path failed hard. Treat ACTIVE as location
noise without ticks — do not promote expansion reads.

## Target Engine

Post-close: Nifty/BN honest **near_book_expired** refuse. Sensex live **bear**
dial (next weekly IV present). No intraday `target_runs` rows.

---

## Deep dive — CALL / PUT WATCH & wall-chase

Source: `/api/watch-episodes` for 23-Nov (**40** API / **45** files on DEV
journal). Down day = natural **PUT** side.

### Scorecard

| Stance | N | TARGET_HIT | INVALIDATED | ACTIVE | Avg MFE | Avg MAE |
|---|---:|---:|---:|---:|---:|---:|
| **CALL WATCH** | 17 | **0** | **16** | 1 | +8 | **−29** |
| **PUT WATCH** | 23 | **7** | 8 | 8 | **+35** | −30 |

All **7 TARGET_HIT** were Nifty **PUT WATCH** with `side=PUT` (aligned). None
of the CALL WATCH episodes hit target — correct for a −600 / −2,500 day, but
the desk still **opened 17 call watches** into the selloff (late bounce probes
14:36–15:04 especially).

PUT wall map: successive put/call walls **migrated down** with spot (Nifty
26,000→25,350; BN 65,000→62,600). That is a **put-side chase / map follow**,
not a call extension chase.

### Your observation — CALL WATCH while bearish / puts paying

**Yes — this is a real logic/ledger deviation, not healthy “divergence.”**

Designed alert-tape rule (`analytics/market_alert.py`): if stance is CALL WATCH
and fresh tape side is PUT (or the reverse), stance is forced to **STAY OUT**.
So the **stance string** should not stay CALL WATCH on a bearish print.

What actually happened in the **watch episode ledger** (9 cases today):

| Pattern | Example | What’s wrong |
|---|---|---|
| `side=CALL`, `stance=PUT WATCH`, bearish trigger text | Nifty 15:21, BN 15:22, Sensex 15:35 | Wall-chase **map side** (which wall migrated) stored as `side`, while **stance** follows tape |
| `side=PUT`, `stance=CALL WATCH`, bullish trigger text | Sensex 10:27 | Same split, opposite way |

Root cause in `web_server._log_wall_chase_to_commentary`:

```text
side   ← row["side"]          # wall_chase map side (CALL/PUT wall events)
stance ← row["stance"]        # alert CALL/PUT WATCH after tape gates
open_watch_episode(side=side, stance=stance, ...)
derive_watch_levels(side=...) # kill/target follow side, not stance
```

So you can see **PUT WATCH** copy (“hold bearish / clear put”) while `side`
remains **CALL** because call walls were the ones migrating. Kill/target are
then derived on the **CALL** geometry (target above, kill below) even though
the human-facing stance is put — e.g. Sensex PUT WATCH with target **84,100**
while spot ~82,436.

That reads in the UI as: “CALL” somewhere in the chase chrome + bearish tape
on top + puts marking higher premium on a down tape — exactly the confusion
you flagged.

**Symmetric PUT note** (message cut off at “PUT”): PUT WATCH with agreeing
`side=PUT` **worked** today (7 target hits, positive MFE). PUT WATCH with
`side=CALL` mismatches did **not** produce clean hits — treat those as
corrupt episodes, not put-chase edge.

### What wall-chase should mean on a day like this

- **PUT chase (valid):** successive put-wall breaks/migrates lower + CONT_BEAR /
  CVD_BEAR ≥65 + zone TEST_PUT / BELOW_PUT → **PUT WATCH**, `side=PUT`.
- **CALL chase (invalid today):** CALL WATCH into a one-way selloff → almost
  all invalidated; should stay **STAY OUT** unless a real bounce tape + call
  extension zone agree.
- **Map-lag:** large open thrust with failed chase gates should stay quiet
  (`🗺 MAP LAG`), not flip WATCH.

---

## Scorecard (desk)

| Dimension | Rating | Notes |
|---|:---:|---|
| Premarket | Weak | No digest; cascade tags OK-ish; path instant invalidate |
| Reliability | Critical | Split brain DEV@8640; no readiness minutes; chain died 09:16 |
| Tape / OF | Failed ledger | 0 sentinel bars/signals in prod DB |
| Trade Ideas / GO | Dark | VWAP refuses only; 0 GO |
| Commentary | Failed (prod) | 2 pre_open lines |
| Blast (Nifty expiry) | Score only | ACTIVE/PIN without ticks; WS dead |
| Target Engine | Honest refuse | near_book on Nifty/BN |
| CALL/PUT chase | Put OK / Call poor / **side bug** | PUT 7/23 hits; CALL 0/17; 9 side≠stance |

## Gaps / follow-ups

1. **Fix serve cwd** — 8640 must run from prod worktree; alert if cwd ≠ prod.
2. **Ingest arming** — chain silence after 09:16 with no process.started is a
   P0 watchdog miss; page when `chain.published` gap > 2 min in session.
3. **Restore SLO minute journal** — 0 readiness rows = blind scorecard.
4. **Watch episode `side` must equal stance side** — derive side from stance
   (or force STAY OUT / skip episode when wall_chase.side ≠ tape side).
5. **Kill/target from stance, not map side** — mismatched geometry today.
6. **Sensex timeseries / digest** — both missing; treat as collection failure.
7. **Blast tick WS** — 0 ticks on expiry day; fix auth/status before next
   Nifty Tuesday.
8. **Suppress CALL WATCH** when spot is making session lows / put walls
   migrating down unless CONT_BULL confirms.

## Disclaimer

Decision-support review, not financial advice. Human confirms all decisions.
