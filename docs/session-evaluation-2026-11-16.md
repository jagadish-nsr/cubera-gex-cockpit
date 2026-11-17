# Session Evaluation — Monday, 16-Nov-2026

Canvas: `session-eval-2026-11-16`.

Reading of the recorded book and the recorded outcomes. Not a forecast.
You confirm every decision. Sign is the call−put book (`short_call_short_put`):
POSITIVE means call-heavy. It is not a statement that price will fade or run.

## Reliability scorecard

Session: `2026-11-16` · cite SLI burn from `docs/sli-slo.md`.

| Metric | Value | Notes |
|---|---|---|
| Process version | tag on disk **v5.3.149** | `process.started` does not log the version. Tag is `git describe`, not a captured `/api/health` banner |
| Role | **combined** | `ingest_external: false` from 05:58. One web pid (55432) held after 06:38 |
| Degrade mode (as-of) | not on the minute payload | `slo.readiness.minute` fields have `ready` / `reasons`, no `degrade_mode`. Not invented |
| Ready minutes (session) | **280** | burn=**22.22%** · 360 min sampled, 09:20–15:19 IST |
| Not-ready minutes | **80** | Target burn ≤ 1% — **failed**. Every not-ready minute is `book_from_nse_scrape` (BN 41, NF 34, both 5). Feed and ingest ages stayed under a few seconds |
| Watchdog restarts | **0** | No `watchdog.restart`. 14 `web` `process.started` events, all 05:58–06:38, then quiet |
| Ingest heartbeat | combined, alive through the close | Sampled `ingest_age_s` about 0.6–4.2s on the not-ready minutes |
| Session checklist | not re-run | Off-session reading. Do not mark PASS from this note |
| VWAP / Ideas refuses | **0** `rec.refused` | 3,632 `rec.generated`. Commentary printed “Session VWAP Reclaimed” at 15:28 |

Chain age on the same 361 readiness samples: Nifty p50 7.8s / p95 25.3s, Bank Nifty p50 12.8s / p95 40.5s, Sensex p50 15.7s / p95 40.3s. NSE fallback events: 228 (Nifty 155, Bank Nifty 73), spread across the cash session.

## Scorecard (desk)

| Dimension | Rating | Notes |
|---|:---:|---|
| Macro | **Gap-up, extension, giveback** | Cash snapshot NF +147, BN +542, SX +512 vs Friday’s cash. Futures gave back most of the 09:45 spike |
| Tape completeness | **Sentinel full, journals thin** | 384 / 383 / 383 1m bars. No Sensex futures file. Nifty cash timeseries stuck at 25,220.05 from 15:16 |
| Trade Ideas | **Minting, mostly LOW** | 262 stored plays, 3,632 generations. New rows stop at 14:04; `last_appeared` runs to 15:39 |
| Sentinel OF | **Late, 35% resolved** | 27 signals. None before 11:04. Opening drive unprinted. 9 target / 17 stop / 1 still active |
| Sentinel paper | **−₹1,183** | 4 TARGET +₹4,232 · 3 SPOT_INVAL −₹5,415. Lots 65 / 30 / 20 |
| GO Desk | **Marks not on the chain** | 90 FIRE, 5 STAGE, all Nifty long calls before 10:16. `go_open` premiums are not in `oi_strikes` |
| Stability | **Usable, source-noisy** | Pre-open web thrash, then one process. 22% readiness burn is the NSE scrape, not a dark book |

## Headline

Monday opened above Friday and ran for about half an hour. Bank Nifty futures
added about 520 points from the 09:15 print into the 09:45 bar. Nifty futures
printed 25,370 and Sensex futures 82,369 in that same bar. Sentinel emitted
nothing until 11:04, and that first print was a Sensex bear continuation into
the giveback.

From there the futures round-tripped the open. Nifty futures were back under
the 09:15 price by 13:45 and again near it at 14:45, then closed the 15:30 bar
at 25,315, about 57 points above the session open and about 144 above Friday’s
futures journal close (25,171). Bank Nifty held a few hundred points of the
spike (15:30 bar 61,490). Sensex futures went back through the open in the
14:00 hour and closed 82,200.

The desk’s Nifty long calls were staged into that opening extension. The
strike journal never prints the premiums `go_open` is still marking. Against
the 15:16 chain those five calls are about **−₹10,530** gross, and
`paper_trades` still has them OPEN. Two of the five are TRAIL_STOP inside
`go_open` at premiums the oi tape does not reach.

## What the close actually is

Three stores disagree. Use them as labeled. Do not average them.

| Store | Nifty | Bank Nifty | Sensex |
|---|---:|---:|---:|
| Session-close snapshot (`as_of` 15:40 IST) | **25,291.1** | **61,575.9** | **82,104.8** |
| Friday cash in that snapshot / auction | 25,144.3 | 61,034.1 | auction prev **81,592.5** (snapshot prev was overwritten with the close) |
| Cash change | **+147** (+0.58%) | **+542** (+0.89%) | **+512** vs the auction prev |
| Sentinel futures, 15:30 bar close | 25,315 | 61,490 | 82,200 |
| Futures journal file | 25,293.6 | 61,439.2 | **file missing** |
| Nifty timeseries 15:16, 15:22, 15:27 | **25,220.05 three times** | no series | no series |

The timeseries freeze is the one to discard as a settlement. 25,220.05 repeats
on a Dhan row and an NSE-fallback row while parity on the 15:16 oi snapshot is
25,241 and the session-close snapshot is 25,291. Sentinel futures at 25,315
versus that cash snapshot is about 24 points, a plausible basis for the 23-Nov
contract. The futures journal close (25,293.6) sits on the cash snapshot, not
on the sentinel futures print.

Pre-open auction (`market_auctions_history`): Nifty discovered 25,210 vs prev
25,144 (gap +66). Bank Nifty 61,301 vs 61,034 (gap +266). Sensex 81,834 vs
81,592 (gap +241). The cash open that then printed in the session snapshot is
higher than discovery: Nifty 25,206, Bank Nifty 61,226, Sensex 81,811.

## Futures path

Sentinel 1m, 09:15–15:30 IST. Close column is the 15:30 15-minute bar.
`|Δratio| ≥ 0.40` counts 1m bars.

| Index | Open | High | Low | 15:30 close | vs Fri fut | Range | 1m bars | Distinct OI | bars ≥ 0.40 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NIFTY-FUT | 25,258 | 25,370 | 25,205 | 25,315 | +144 vs 25,171 | 165 | 376 (384 to 15:38) | 324 | **10** |
| BANKNIFTY-FUT | 61,107 | 61,660 | 61,107 | 61,490 | +556 vs 60,933 | 553 | 375 (383 to 15:38) | 261 | 38 |
| SENSEX-FUT | 82,009 | 82,369 | 81,850 | 82,200 | +597 vs Fri eval 81,603 | 519 | 376 (383 to 15:38) | **33** | **91** |

Session shape on the 15-minute closes, points from the 09:15 open:

- 09:45: Nifty +87, Bank Nifty +523, Sensex +351. This is the drive.
- 13:45–14:00: Nifty −15, Sensex −59 (back through the open), Bank Nifty still +253.
- 15:30: Nifty +57, Bank Nifty +383, Sensex +191.

Daily CVD at the sentinel close is positive on all three (Nifty +183k, Bank
Nifty +49k, Sensex +3.8k). That is buy-volume minus sell-volume on the
futures tape. It is not a positioning forecast.

Nifty futures intraday CSV is a sparse ~5-minute file (about 40 rows from
09:23). Bank Nifty and Sensex have no intraday futures file. Sensex has no
EOD futures file either. A GAP alert at 15:45 says “BANKNIFTY sentinel
PARTIAL” while the 1m table holds 383 bars — treat the alert as a journal-gap
rule, and check it before trusting it as a hole in the tape.

## Book

Nifty timeseries, 74 rows, 09:15–15:27, none marked stale.

| | Count |
|---|---:|
| POSITIVE (call-heavy) | 55 |
| NEGATIVE (put-heavy) | 19 |
| Source `nse (Dhan fallback)` | 41 |
| Source `dhan` | 33 |

Walls split by source. They are not one map.

| Source | Dominant pair | Rows |
|---|---|---:|
| NSE fallback | **25,300 / 25,200** | 41 of 41 |
| Dhan | 25,300 / 25,200 | 18 |
| Dhan | **25,500 / 25,000** | 12 |
| Dhan | 26,000 / 25,000 and 25,300 / 25,300 | 1 each |

The 15:00 row in `sessions.csv` matches the NSE book: spot 25,217, flip
25,161, walls 25,300 / 25,200, regime POSITIVE, book balance about +2.02e11,
284 contracts, `short_call_short_put`. The session-close snapshot instead
stores the Dhan wide pair **25,500 / 25,000**, flip 25,193, max pain 25,200.
`|net GEX|` on the Dhan wide rows is about an order of magnitude smaller than
the NSE-fallback rows. Do not rank the day’s concentration off the mixed series.

Spot spent the afternoon near the flip (flip roughly 25,140–25,270 while cash
traded 25,175–25,220). The 15:00 log has spot above the flip in a call-heavy
book. Commentary printed “Regime Shift: NEGATIVE_GAMMA” at 12:20, 12:51 and
13:23, then “POSITIVE_GAMMA” at 13:54. Those times line up with the put-heavy
stretch in the timeseries. The word on the tape is still the retired gamma
label. The number underneath is call magnitude minus put magnitude.

Close snapshot walls for the other two indices are equal:

- Bank Nifty **61,000 / 61,000**, flip 61,311, max pain 61,300. Cash snapshot
  61,576 is above that shared strike. Commentary migrated the call wall
  between 62,000 and 61,000 through the afternoon.
- Sensex **82,000 / 82,000**, flip 82,438 (above the 82,105 snapshot), max pain
  82,000. Commentary oscillated 81,500 / 82,000 / 82,200 all day.

Equal walls are a real close-map condition on Bank Nifty and Sensex today.
The soft-flag path is the one that applies. Walls are concentration. They are
not floors.

Nifty expiry on this book is Tuesday 17 Nov (the sentinel paper legs say
`17NOV2026`). Bank Nifty legs say `23NOV2026`. Sensex legs say `19NOV2026`.
Forty-one stored ideas are badged “0DTE Vanna-Charm Theta Compression” on a
Monday. That label is a day early for Nifty.

Participant sheet (`participant_oi/2026-11-16.csv`, exchange fut columns, not
a Nifty-only tape): FII fut 61,039 long vs 117,726 short. Client fut 171,404
long vs 113,020 short. Open interest is not buyer-versus-seller flow.

## Trade Ideas

`rec.generated` is steady: 450, 523, 551, 619, 696, 478, 315 across hours
09–15. Stored `recommendation_records` are not: 233 in hour 09 (55 of them
at 09:15), 17 in hour 10, 12 at 14:04, and no new `triggered_at` after that.
`last_appeared` reaches 15:39:59, so later generations were updates of the
262 rows, not new plays. Zero `rec.refused`.

| | Nifty | Bank Nifty | Sensex | All |
|---|---:|---:|---:|---:|
| Rows | 69 | 109 | 84 | 262 |
| ACTIVE | 29 | 31 | 38 | 98 |
| TGT1 or TGT2 | 19 | 34 | 16 | 69 |
| SL_HIT | 10 | 10 | 23 | 43 |
| INVALIDATED | 11 | 34 | 7 | 52 |

Tier: LOW 235, MEDIUM 5, HIGH 22. Side: CE 129, PE 133. Seven play types, 36–41
rows each, so the set is the full menu rather than a filtered book. Among rows
that left ACTIVE, 69 of 164 reached a target label. That label is the
lifecycle status, not a rupee fill.

## OrderFlow Sentinel

27 signals. First bar 11:04. The 09:15–10:00 extension is absent.

| Index | Signals | Target | Stop | Still active | Resolved target rate |
|---|---:|---:|---:|---:|---:|
| Nifty | 12 | 5 | 6 | 1 (15:30 CVD bear) | 5/11 |
| Bank Nifty | 9 | 2 | 7 | 0 | 2/9 |
| Sensex | 6 | 2 | 4 | 0 | 2/6 |
| **All** | **27** | **9** | **17** | **1** | **9/26 = 35%** |

| Type | Target | Stop | Active |
|---|---:|---:|---:|
| CONTINUATION_BULL | 2 | 5 | 0 |
| CONTINUATION_BEAR | 2 | 3 | 0 |
| CVD_DIVERGENCE_BULL | 3 | 5 | 0 |
| CVD_DIVERGENCE_BEAR | 1 | 1 | 1 |
| EXHAUSTION_DOWN | 1 | 1 | 0 |
| EXHAUSTION_UP | 0 | 1 | 0 |
| ABSORPTION_BEAR | 0 | 1 | 0 |

Nifty order-flow was quiet on this definition: 10 of 376 session bars had
`|Δratio| ≥ 0.40`, against 81 such bars on 13 Nov. Sensex was the loud tape
(91 bars) and also the only symbol with `sentinel.silence_resync`: 64 warnings,
09:45–15:30, all `ok: false`. The resyncs did not punch a hole in the 1m count.
Distinct Sensex OI is only 33, so the watchdog and the thin contract are the
same story.

Bull continuations at 12:08–12:15 (Bank Nifty scores 87 and 95, Sensex 69)
all stopped, with MFE 0 on the signal record. They fired into the second push
and the push failed. The two Nifty continuation bulls that did hit were both
score 65 at 14:02 and 14:07, after the low.

## Sentinel paper

Seven closed rows in `paper_trades`, all `exit_by=SENTINEL_ENGINE`.
Net **−₹1,183** after brokerage and slippage.

| Opened | Leg | Exit | Net ₹ |
|---|---|---|---:|
| 11:10 | Sensex 82,000 PE, lot 20 | TARGET 11:48 | +433 |
| 12:09 | Bank Nifty 61,500 CE, lot 30 | TARGET 12:12 (3 min) | +1,798 |
| 12:12 | Bank Nifty 61,600 CE, lot 30 | SPOT_INVAL 12:25 | −2,035 |
| 12:23 | Nifty 25,300 CE, lot 65 | SPOT_INVAL 13:35 | −1,467 |
| 13:37 | Bank Nifty 61,500 CE, lot 30 | SPOT_INVAL 14:00 | −1,914 |
| 14:02 | Nifty 25,200 CE, lot 65 | TARGET 14:33 | +1,223 |
| 14:40 | Nifty 25,250 CE, lot 65 | TARGET 15:26 | +779 |

`sentinel_trade_recommendations` keeps one row per `symbol_strike_side`.
The 12:09 Bank Nifty 61,500 CE winner was overwritten by the 13:37 loser
(`srec_20261116_BANKNIFTY_61500_CE` status SPOT_INVAL). The paper table is
the one that still has both.

## GO Desk

156 events, every one NIFTY. EMIT 151, STAGE 5. FIRE 90, SCOUT 66.
No Bank Nifty stage, no Sensex stage, no stage after 10:16, no stage after
the cash close. Ten WRITE_OTM scouts between 01:33 and 06:38, none staged.

Staged rows are Ideas-led (`sentinel_type` null). Targets are populated.
They are not the old LTP×1.25 / 1.50 / 0.70 recipe. One of them should not
have been a FIRE on its own numbers: STRIKE_BREAK at 09:17 has `touch_p` 0.01
and `ev` −6.3, and it was staged.

| Opened | Key | Strike | Entry prem | go_open status | go_open premium | Chain max CE | Chain 15:16 CE |
|---|---|---:|---:|---|---:|---:|---:|
| 09:15 | STRUCTURE | 25,400 CE | 67.60 | OPEN, mark 162.57 | 162.57 | **62.08** (12:12) | **34.00** |
| 09:17 | STRIKE_BREAK | 25,450 CE | 46.53 | TRAIL_STOP 10:01 | 51.18 | **46.20** | **24.73** |
| 09:42 | MOMENTUM | 25,350 CE | 76.70 | TRAIL_STOP 10:03 | 84.37 | 82.10 | 46.83 |
| 10:04 | MOMENTUM | 25,350 CE | 85.85 | OPEN, mark 185.32 | 185.32 | 82.10 | 46.83 |
| 10:15 | STRIKE_BREAK | 25,300 CE | 101.33 | OPEN, mark 210.93 | 210.93 | 104.70 | 63.62 |

The oi file has 40 timestamps from 09:23 to 15:16. Across those prints:

- 25,400 CE never trades 67.60 or 162. The entry is above the day’s recorded
  maximum, eight minutes before the first oi row, with cash almost unchanged.
- 25,450 CE never trades 51.18. The trail price is above the recorded maximum.
- 25,350 and 25,300 closes (46.83 and 63.62) are far under the open marks of
  185 and 211.

`paper_trades` still says OPEN for all five, including the two `go_open` calls
TRAIL_STOP. Entry costs of ₹20 brokerage and ₹16.25 slippage are stamped; no
exit, no `net_realized_pnl`.

Marked to the 15:16 call LTP, lot 65, gross, treating them as still open
because the paper ledger does:

| Leg | Points | Gross ₹ |
|---|---:|---:|
| 25,400 CE 67.60 → 34.00 | −33.60 | −2,184 |
| 25,450 CE 46.53 → 24.73 | −21.80 | −1,417 |
| 25,350 CE 76.70 → 46.83 | −29.87 | −1,942 |
| 25,350 CE 85.85 → 46.83 | −39.02 | −2,536 |
| 25,300 CE 101.33 → 63.62 | −37.71 | −2,451 |
| **Sum** | | **−10,530** |

`go_open` also still carries Friday Nifty rows (ICEBERG, VOL_DISPERSION,
RATIO_REVERSAL) marked at 21:35 today to spot 25,291. Those marks use the
same premium field. Do not read Friday’s open MTM off it until the marker
is checked against a strike LTP the same way.

## Commentary and stability

273 commentary rows, 06:30–15:40. 188 of them are Nifty `MARKET_ALERT`.
The same headline repeats inside a single minute (15 “between walls / testing
call wall” lines around 10:02–10:04, and a similar burst 14:18–14:24).
Three bites, all PREPARE. Ops counted 7,471 `commentary.evaluated` against
273 stored rows.

`book.stale` 31 times, all before 06:38. `http.slow` 278, p50 1.1s, p95 1.4s,
max 2.1s. Chain errors on the web process are non-zero (Nifty error_count 641,
Bank Nifty 223, Sensex 0) with last errors at 15:37–15:39, and the chains
still published through 15:40 (3,134 `chain.published`). Sentinel status on
the web process shows `ok_count` 0 and `error_count` 79, last error 15:33,
while the bar table itself is complete — the status row and the tape disagree.

An October 29 alert, “log_session heartbeat is stale,” is still open and now
points at today’s 15:00 beat. After the close that quiet is expected. The
alert row is old.

Gamma-blast files for 16 Nov are absent. `blast.lane` last ok is 06:38.

## Yesterday’s checklist — verdict

| Check | Result |
|---|---|
| Ideas after the first bar, no VWAP refuse | **Held.** Generations from 09:15, zero refuses |
| No STAGE after the cash close | **Held.** Last stage 10:15 |
| Sentinel-led FIRE does not invent targets | No sentinel-led stage today. Ideas-led stages carry t1/t2/sl |
| Readiness burn near 1% | **Failed.** 22% on NSE-scrape fallback, book still publishing |
| Serve vs ingest latch | Combined process today, so Friday’s split-latch miss is not the setup |
| Equal-wall soft flag | **In force at the close snapshot** for Bank Nifty and Sensex |

## Follow-ups

1. `go_open.mark_premium` / trail for these five Nifty calls is not the strike
   LTP in `oi_strikes`. Paper and `go_open` disagree on whether two of them
   closed. Fix the marker, then recompute MTM. Do not trade off the 162 / 185 /
   211 marks.
2. Nifty timeseries repeats 25,220.05 from 15:16 through 15:27. The
   session-close snapshot says 25,291.1. One writer is stuck.
3. Session-close Nifty walls follow the Dhan wide pair. The NSE-fallback book
   was 25,300 / 25,200 on every one of its 41 rows. The snapshot should say
   which book it closed.
4. Sensex `session_snapshots.prev_close` equals the close (82,104.79). The
   auction row still has 81,592.45.
5. Commentary still says NEGATIVE_GAMMA / POSITIVE_GAMMA, and it repeats the
   same Nifty headline many times a minute.
6. “0DTE Vanna-Charm” on Monday 16 Nov, with Nifty expiry Tuesday 17 Nov.
7. Sentinel was silent through the only impulsive half-hour. Sensex fired 64
   failed silence resyncs on a tape that was actually printing.
8. STRIKE_BREAK staged at `touch_p` 0.01 and negative EV.
9. Futures journal close, sentinel 15:30 close, and the cash snapshot are
   three numbers. Sensex futures journal is missing. The 15:45 Bank Nifty
   “sentinel PARTIAL” alert does not match the 383 stored 1m bars.
10. `sentinel_trade_recommendations` is last-write-wins per strike and dropped
    the Bank Nifty 61,500 CE winner.

## Disclaimer

Decision-support only. Not financial advice. Human confirms all orders.
GEX is a positioning map. The sign is call-heavy versus put-heavy.
It is not a direction call, and an unmeasured hedge is not a wall defense.
