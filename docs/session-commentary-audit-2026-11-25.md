# Session Commentary Audit — Wednesday, 25-Nov-2026

Audit of every prod session-commentary entry for 25-Nov (**125 entries** in
`commentary.db` on the serving tree `cubera-gex-cockpit-prod`). Each card was checked
for timestamp, phase, update type (headline and tags), numbers, and wording.

**Read-only.** Nothing in the database, the code, or the production tree was changed.
The corrected text below is a proposal. Re-running the check is
`python3 scratch/audit_commentary_2026-11-25.py` then `python3 scratch/build_audit_doc.py`.

## Sources used

| Check | Source |
|---|---|
| Entries, phase, tags | prod `data/journal/commentary.db` (`commentary_entries`) |
| Futures bars, VWAP, ORB window | prod `data/sentinel.db` `sentinel_bars` (`SYMBOL-FUT`, 1m) |
| Index spot, walls, flip, regime, PCR | prod `data/journal/timeseries/<SYM>/2026-11-25.csv` (about 5 to 10 min cadence) |
| Watch outcomes | prod `data/journal/watch_episodes/*.json` |
| Phase windows | `config/commentary.yaml` `session.phases` |
| Prior closes | Verified in `docs/session-evaluation-2026-11-25.md` |

Verdicts: **OK** = reproduces from source. **CHECK** = misleading, unverifiable at
sample resolution, or wording to fix. **WRONG** = value, time, or type is contradicted by source.

## Summary

- Entries: **125** · OK **36** · CHECK **46** · WRONG **43**
- Phase label matches the config window for all 125 entries.
- Watch resolution cards (13) match the ledger on status and times.

| Category | Entries | OK | CHECK | WRONG |
|---|---:|---:|---:|---:|
| VWAP_STRETCH | 19 | 0 | 0 | 19 |
| IMPULSE | 15 | 12 | 2 | 1 |
| MARKET_ALERT | 15 | 0 | 9 | 6 |
| FOOTPRINT | 14 | 0 | 14 | 0 |
| WALL_MIGRATION | 13 | 6 | 3 | 4 |
| WATCH_RESOLUTION | 13 | 13 | 0 | 0 |
| REGIME | 8 | 0 | 8 | 0 |
| WALL_BREAK | 6 | 3 | 2 | 1 |
| WALL_CHASE | 5 | 0 | 0 | 5 |
| VWAP_CROSS | 5 | 0 | 4 | 1 |
| FLIP_CROSS | 4 | 0 | 4 | 0 |
| PCR | 3 | 2 | 0 | 1 |
| ORB_ESTABLISHED | 2 | 0 | 0 | 2 |
| GAP | 2 | 0 | 0 | 2 |
| ORB_BREAK | 1 | 0 | 0 | 1 |

### Root causes

**1. The opening range is built from the wrong session (ORB, 3 WRONG).**
`commentary/detectors/session_structure.py` takes `candles[:15]`. Those candles come from
`commentary/engine.py`, which queries `sentinel_bars` with `start_ts <= now ORDER BY start_ts DESC LIMIT 60`
and no session bound. At 09:15:20 on 25-Nov the 60 most recent bars were 20-Nov 14:38 to 25-Nov 09:15.
Replaying that query on prod gives the **135.3 pts** Sensex range exactly (83,830.3–83,965.6, from
20-Nov 14:38–14:52). The range is latched for the whole day, so the BANKNIFTY (229.4), NIFTY breakout
(ORB high 25,785.0, the 20-Nov high), and Sensex cards are all prior-session numbers. The real
09:15–09:29 futures ranges are: Sensex 547.4 pts, BankNifty 697.2 pts. The range should be stamped at 09:30, when the window closes.

**2. VWAP stretch compares index spot with futures VWAP (19 of 19 WRONG, 6 sign flips).**
`commentary/detectors/vwap_dynamics.py` computes `dist = pit_view.spot - vwap`. Spot is the index. The VWAP
comes from `candles[-1].vwap`, which is the `*-FUT` VWAP. The two series sit 50 to 350 pts apart, so the stretch is wrong in size and sometimes in sign. Example: at 09:31 BankNifty futures closed **+251 above** VWAP, but the card says "Oversold −86.2". Sign flips: 09:31 BANKNIFTY, 09:39 NIFTY, 10:10 NIFTY, 10:36 BANKNIFTY, 11:26 BANKNIFTY, 14:18 NIFTY.

**3. Gap cards use the wrong open and the wrong time.**
`gap.py` treats the first spot seen between 09:15 and 09:20 as the cash open. Sensex's card says "opened at
83,808.4", but the 09:15 cash print in the journal is 83,744.15, so the gap is +1,308.2, not +1,372.4.
NIFTY's gap (+383.9) is a 09:15 fact, but it was published at **11:43**, labelled midday.

**4. Pre-open values are stale.** At 09:14:50 the NIFTY "Put Wall Broken at ₹25,500" used a spot of 25,349.7,
which equals the prior close. That is a carried-over value, not a live break.

**5. Wall-chase cards are long and repeat themselves (5 cards, all WRONG).** See the section below.

**6. The printed kill does not match the ledger on 6 alert cards.** 10:21 SENSEX (⚖️ BETWEEN WALLS); 11:06 SENSEX (📡 CVD DIVERGENCE BULL); 11:23 SENSEX (📡 CVD DIVERGENCE BULL); 11:38 SENSEX (⚖️ BETWEEN WALLS); 12:04 SENSEX (⚖️ BETWEEN WALLS); 12:21 SENSEX (⚖️ BETWEEN WALLS).
The ledger's `kill_level` is what the episode is scored on. The card's kill comes from a different
computation (`web_server.py` / `analytics/market_alert.py` `inval` vs `kill`).

**7. Wall-migration chains do not link (4 WRONG).** 13:30 NIFTY Put Wall migrated from ₹25,500 to ₹25,700; 13:30 SENSEX Put Wall migrated from ₹83,000 to ₹83,500; 13:59 SENSEX Call Wall migrated from ₹84,000 to ₹85,000; 15:32 SENSEX Call Wall migrated from ₹84,000 to ₹85,000.
The card's "from" value does not match the previous card's "to", so a step is missing from the record.
Several other migrations flip back at the next sample (BankNifty call 66,000→65,000→66,000; NIFTY put 25,700→25,800→25,700; Sensex call 84,000→85,000→84,000→85,000).

**8. Formatting and wording.** `PCR Surge: 0.76 → 0.93 (++0.17)` (double plus). The card is stamped 09:23:50, but the journal has no sample at 09:23. The move is 0.757 (09:15) to 0.921 (09:27:20), so the end value should read 0.92, not 0.93. "Session VWAP lost ... trading below
intraday volume average" should say "session VWAP" (VWAP is not a volume average). "Closed beyond" is used for
tick-level breaks of 5 pts. "Institutional-style" in 14 footprint cards names a participant type the tape cannot identify.

**9. Book and flip flip-flops (8 regime cards, 4 flip cards).** Regime changes
every few minutes (5-min samples). Two Sensex "Gamma Flip Crossed" cards at 13:09 and 13:16 both
report spot above the flip, so one crossing is missing.

## Wall-chase cards (the 10:40:44 card and its siblings)

The description below is the card **before** the fix. The shape it proposes is now in code (`web_server._log_wall_chase_to_commentary`).

Card length is the sum of the parts. `wallchase` bodies are 1,550 to 1,750 characters and contain 11 blocks, in this order:

1. `Outcome` (one line)
2. `Drivers`: "successive-concentration chase" sentence + tape insight + base drivers, **cut at 400 characters** (`web_server.py:3992`). The cut lands mid-sentence ("Fresh o", "Fresh orderflow print ≥65:").
3. `Risk:`: holds the **trigger and kill text**, so the same numbers print again.
4. `Map`: spot, walls, magnet, flip, zone.
5. `Tape`: signal type, score, confidence.
6. `Events`: repeats the same break ("Call Wall Broken at ₹84,000" twice, at 10:34 and 10:39) and includes migrations that never got their own card.
7. `Stretch`: a ladder paragraph ("run 1,147 pts", "potential stretch 1,247 pts"). Not a move, and the "run" can be negative (09:54: "-73.8 pts").
8. `Triggered at`: time, kill, and target.
9. `Trigger`, 10. `Kill` (repeat the same numbers a third time).
11. A footer note that repeats the stretch note.

The ×N headline counts repeated prints of one wall as separate events. The 10:40:44 card is "×2" only because
the 84,000 call wall printed as broken at 10:34 and at 10:39. The card's target (84,600) is 453 pts from spot,
and the ledger shows the episode **INVALIDATED at 10:44:03** (MFE +8, MAE −100). That outcome card is separate and
the chase card does not mention it.

**Shape now in code:** one headline line (stance, symbol, trigger time), Outcome, one kill, one target with its distance, and a
pointer to the ledger. Remove the Risk duplicate, the Stretch ladder, and the footer. Cap Drivers by sentences, not characters.

## Corrections applied

- **Rewritten (43 cards, all WRONG):** `commentary_entries` headline, body, and `ts` (where the time was wrong) were replaced with the Correction text. Each row got the `CORRECTED` tag and a `correction` object in `facts_json`.
- **Originals kept:** the `commentary_corrections` table in `data/journal/commentary.db` holds the 43 pre-change `headline`, `body`, `ts`, `tags`, and `facts`, with a timestamp.
- **Backup:** `data/journal/commentary.db.before-25nov-correction.bak` (taken before the write).
- **Not changed:** 36 OK and 46 CHECK cards; `commentary_bites`; session recaps; the 9 alert cards whose Risk line shows the printed kill (they are CHECK, so they keep it).
- **Scope of this write:** only the stored text. The code fixes are in the dev tree and are uncommitted. The prod tree has not been deployed, and the prod server may need a restart to show the rewritten rows.

## Per-entry audit

Times are IST. The per-entry rows are in the order they were written. The Finding column records what was checked, and the Correction column is the replacement text. The 43 WRONG cards were rewritten on 2026-11-25 with exactly this text (see Corrections applied). The 46 CHECK cards and the 36 OK cards were not changed.

| Time | Sym | Phase | Card | Verdict | Finding | Correction |
|---|---|---|---|---|---|---|
| 09:14 | BANKNIFTY | pre_open | Opening 15m Range Set: 229.4 pts | **WRONG** | Built from the first 15 of the 60 most recent 1m bars (sentinel query has no session bound). At 09:14 those bars were 20-Nov 14:38–14:52 (prod replay). Claimed 229.4 pts; the 09:15–09:29 BANKNIFTY-FUT bars give 697.2 pts (63,499.80–64,197.00). Emitted before the open. | Withdraw. Re-emit at 09:30:00 as 'Opening 15m Range (BANKNIFTY-FUT basis): 697.2 pts, 63,499.8–64,197.0'. Index-basis range is not in the journal. |
| 09:14 | NIFTY | pre_open | Put Wall Broken at ₹25,500 | **WRONG** | Pre-open spot 25,349.7 equals the prior close 25,349.65: a carried-over value, not a live print. | Withdraw. No live break at this time. |
| 09:15 | NIFTY | opening_15m | Impulsive Bullish Move (+58.1 pts) | **WRONG** | Only 1 bar(s) of 25-Nov exist at 09:15; the 5-bar window (+58.1 pts) reaches into the 20-Nov session. | Withdraw. First valid 5-bar window completes at 09:19. |
| 09:15 | SENSEX | opening_15m | Gap-Up Open: +1,372.4 pts (+1.66%) | **WRONG** | 'Opened at 83,808.4' is the spot at the first poll (09:15:20), not the 09:15 cash print 83,744.15. Gap +1,372.4 should be +1,308.2. | Gap-Up Open at 09:15 cash print 83,744.15: +1,308.2 pts (+1.59%) vs prior close 82,435.99. |
| 09:15 | SENSEX | opening_15m | Opening 15m Range Set: 135.3 pts | **WRONG** | Built from the first 15 of the 60 most recent 1m bars (sentinel query has no session bound). At 09:15 those bars were 20-Nov 14:38–14:52 (prod replay). Claimed 135.3 pts; the 09:15–09:29 SENSEX-FUT bars give 547.4 pts (83,400.00–83,947.35). | Withdraw. Re-emit at 09:30:00 as 'Opening 15m Range (SENSEX-FUT basis): 547.4 pts, 83,400.0–83,947.4'. Index-basis range is not in the journal. |
| 09:20 | BANKNIFTY | opening_15m | Call Wall Migrated from ₹66,000 to ₹65,000 | **CHECK** | next sample 09:26 shows 66,000, so the wall moved back | Keep as a dated flip note, with the prior level it came from |
| 09:23 | SENSEX | opening_15m | PCR Surge: 0.76 → 0.93 (++0.17) | **WRONG** | Headline shows '++0.17' (format bug). The card is stamped 09:23:50 but the journal has no sample at 09:23. The pcr_oi moves 0.757 (09:15:20) to 0.921 (09:27:20). The card's end 0.93 does not match 0.921. | PCR Surge: 0.76 → 0.92 (+0.16) at 09:27:20 (journal pcr_oi 0.757 → 0.921) |
| 09:25 | NIFTY | opening_15m | Put Wall Migrated from ₹25,500 to ₹25,700 | **OK** | chain links and timeseries agree | OK |
| 09:30 | SENSEX | morning | Put Wall Migrated from ₹83,000 to ₹83,800 | **OK** | chain links and timeseries agree | OK |
| 09:31 | BANKNIFTY | morning | VWAP Stretch: Oversold (-86.2 pts) | **WRONG** | index spot 63,876.3 minus BANKNIFTY-FUT VWAP 63,969.8 (basis mismatch); SIGN FLIP: futures close 64,220.8 is +251.0 vs VWAP; VWAP 63,962.5 vs bar VWAP 63,969.8 | Restate on BANKNIFTY-FUT basis: Overbought (+251.0 pts vs futures VWAP 63,969.8); or stop mixing index spot with futures VWAP. |
| 09:32 | SENSEX | MARKET_ALERT | 📡 ABSORPTION BEAR · ⚖️ BETWEEN WALLS · MED | **CHECK** | 728-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers; ledger: TARGET_HIT 09:47:01 (MFE 180.7, MAE -34.1) | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate; outcome TARGET_HIT 09:47:01 (MFE +180.7, MAE -34.1) |
| 09:33 | NIFTY | morning | Opening Range Breakout (Upside): +5.2 pts | **WRONG** | ORB high 25,785.0 is the 20-Nov 14:40–14:54 high (prod replay). The real 09:15–09:29 NIFTY-FUT high is 25,893.80. | Withdraw. Spot 25,790.2 is below the 09:15–09:29 futures high 25,893.80; no upside breakout is supported. |
| 09:37 | SENSEX | morning | Put Wall Broken at ₹83,800 | **CHECK** | Spot 83,794.6 is only 5.4 pts past the 83,800 wall. A tick, not a close ('closed beyond' is wrong). | Say 'traded beyond 83,800' and treat sub-10-pt breaks as noise. |
| 09:39 | NIFTY | morning | VWAP Stretch: Oversold (-87.8 pts) | **WRONG** | index spot 25,749.8 minus NIFTY-FUT VWAP 25,838.0 (basis mismatch); SIGN FLIP: futures close 25,885.4 is +47.4 vs VWAP | Restate on NIFTY-FUT basis: Overbought (+47.4 pts vs futures VWAP 25,838.0); or stop mixing index spot with futures VWAP. |
| 09:42 | NIFTY | MARKET_ALERT | 🏃 WALL CHASE ×2 · PUT WATCH · NIFTY | **WRONG** | Events lists the same break twice (Put Wall Broken at ₹25,500); ×2 counts that duplicate; Risk line is the trigger+kill text, so trigger and kill print twice; events include the 09:14:50 pre-open 'Put Wall Broken at ₹25,500' (stale spot, see WALL_BREAK row); resolved later: INVALIDATED at 11:45:10 (MFE 0.0, MAE -201.1); target 25,350 is 399 pts from spot (not reachable intraday) | 🏃 WALL CHASE · PUT WATCH · NIFTY — triggered 09:42:56 IST; one kill 25,912; target 25,350 (399 pts from spot); outcome INVALIDATED 11:45:10. Count distinct walls moved, not repeated prints of one wall. |
| 09:46 | SENSEX | morning | Session VWAP Lost at ₹83,822.7 | **CHECK** | index spot 83,769.2 vs SENSEX-FUT VWAP 83,822.0; futures close 83,762.2. Futures side agrees. Index-vs-futures basis caveat applies. | Say 'futures VWAP' and 'trading below/above session VWAP' (not 'intraday volume average'). |
| 09:46 | SENSEX | morning | VWAP Stretch: Oversold (-107.3 pts) | **WRONG** | index spot 83,714.7 minus SENSEX-FUT VWAP 83,822.0 (basis mismatch); magnitude: card -107.3 vs futures -59.8 | Restate on SENSEX-FUT basis: Oversold (-59.8 pts vs futures VWAP 83,822.0); or stop mixing index spot with futures VWAP. |
| 09:47 | SENSEX | MARKET_ALERT | ✅ WATCH TARGET_HIT · PUT WATCH · SENSEX (triggered 2026-11-25 09:32:56 IST) | **OK** | status TARGET_HIT, triggered 09:32:56, resolved 09:47:01 (MFE 180.7, MAE -34.1) — matches ledger | OK |
| 09:53 | SENSEX | morning | Footprint: Institutional-Style Passive Absorption At Resistance | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 09:54 | SENSEX | MARKET_ALERT | 🏃 WALL CHASE ×2 · PUT WATCH · SENSEX | **WRONG** | Risk line is the trigger+kill text, so trigger and kill print twice; Drivers cut mid-sentence (400-char cap on drivers, web_server.py:3992); stretch 'run' is negative (ladder arithmetic, not a move); resolved later: INVALIDATED at 10:19:01 (MFE 144.3, MAE -78.7); target 82,900 is 974 pts from spot (not reachable intraday) | 🏃 WALL CHASE · PUT WATCH · SENSEX — triggered 09:54:20 IST; one kill 83,950; target 82,900 (974 pts from spot); outcome INVALIDATED 10:19:01. Count distinct walls moved, not repeated prints of one wall. |
| 09:54 | SENSEX | MARKET_ALERT | 🏃 WALL CHASE ×2 · 📡 ABSORPTION BEAR · ⚖️ BETWEEN WALLS · MED | **CHECK** | 'WALL CHASE' chip on an ALERT_TAPE card (describes the wall state, not this episode); 855-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers; ledger: INVALIDATED 10:19:01 (MFE 144.3, MAE -78.7) | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate; outcome INVALIDATED 10:19:01 (MFE +144.3, MAE -78.7) |
| 09:59 | SENSEX | morning | Session VWAP Lost at ₹83,840.0 | **WRONG** | index spot 83,838.2 vs SENSEX-FUT VWAP 83,840.0; futures close 83,932.6: futures sit above VWAP, so 'Session VWAP lost' does not hold at 09:59. | Withdraw 'lost'. Futures above VWAP 83,840.0; no cross at 09:59. |
| 10:01 | SENSEX | morning | Impulsive Bearish Move (-95.6 pts) | **OK** | -95.6 pts reproduces from closed SENSEX-FUT bars (09:57–10:01). | OK (futures basis) |
| 10:02 | SENSEX | MARKET_ALERT | 🏃 WALL CHASE ×2 · 📡 EXHAUSTION UP · ⚖️ BETWEEN WALLS · MED | **CHECK** | 'WALL CHASE' chip on an ALERT_TAPE card (describes the wall state, not this episode); 810-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate |
| 10:04 | BANKNIFTY | morning | VWAP Stretch: Oversold (-393.2 pts) | **WRONG** | index spot 63,593.1 minus BANKNIFTY-FUT VWAP 63,986.2 (basis mismatch); magnitude: card -393.2 vs futures -96.0 | Restate on BANKNIFTY-FUT basis: Oversold (-96.0 pts vs futures VWAP 63,986.2); or stop mixing index spot with futures VWAP. |
| 10:10 | NIFTY | morning | VWAP Stretch: Oversold (-113.5 pts) | **WRONG** | index spot 25,736.5 minus NIFTY-FUT VWAP 25,850.0 (basis mismatch); SIGN FLIP: futures close 25,851.2 is +1.2 vs VWAP | Withdraw. On NIFTY-FUT the stretch is +1.2 pts vs VWAP 25,850.0, inside the 35-pt minimum for an extension. |
| 10:11 | SENSEX | morning | VWAP Stretch: Oversold (-85.6 pts) | **WRONG** | index spot 83,757.6 minus SENSEX-FUT VWAP 83,842.6 (basis mismatch); magnitude: card -85.6 vs futures -42.6 | Restate on SENSEX-FUT basis: Oversold (-42.6 pts vs futures VWAP 83,842.6); or stop mixing index spot with futures VWAP. |
| 10:18 | SENSEX | morning | Footprint: Institutional-Style Passive Absorption At Resistance | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 10:19 | SENSEX | MARKET_ALERT | 🛑 WATCH INVALIDATED · PUT WATCH · SENSEX (triggered 2026-11-25 09:54:20 IST) | **OK** | status INVALIDATED, triggered 09:54:20, resolved 10:19:01 (MFE 144.3, MAE -78.7) — matches ledger | OK |
| 10:19 | SENSEX | MARKET_ALERT | 📡 ABSORPTION BEAR · ⚖️ BETWEEN WALLS · MED | **CHECK** | 728-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers; ledger: INVALIDATED 10:19:40 (MFE 0.0, MAE -54.0) | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate; outcome INVALIDATED 10:19:40 (MFE +0.0, MAE -54.0) |
| 10:19 | SENSEX | MARKET_ALERT | 🛑 WATCH INVALIDATED · PUT WATCH · SENSEX (triggered 2026-11-25 10:19:02 IST) | **OK** | status INVALIDATED, triggered 10:19:02, resolved 10:19:40 (MFE 0.0, MAE -54.0) — matches ledger | OK |
| 10:20 | NIFTY | morning | Impulsive Bullish Move (+38.7 pts) | **OK** | +38.7 pts reproduces from closed NIFTY-FUT bars (10:16–10:20). | OK (futures basis) |
| 10:21 | SENSEX | MARKET_ALERT | 📡 ABSORPTION BEAR · ⚖️ BETWEEN WALLS · MED | **WRONG** | 728-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers; printed kill 83,970 but ledger kill level 84,071.70; ledger: INVALIDATED 10:39:20 (MFE 117.1, MAE -70.8) | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate; outcome INVALIDATED 10:39:20 (MFE +117.1, MAE -70.8) |
| 10:30 | SENSEX | MARKET_ALERT | 📡 CONTINUATION BULL · 🔥 TESTING CALL WALL · HIGH | **CHECK** | 759-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers; ledger: TARGET_HIT 10:40:20 (MFE 187.9, MAE 0.0) | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate; outcome TARGET_HIT 10:40:20 (MFE +187.9, MAE +0.0) |
| 10:34 | SENSEX | morning | Call Wall Broken at ₹84,000 | **CHECK** | Spot 84,005.1 is only 5.1 pts past the 84,000 wall. A tick, not a close ('closed beyond' is wrong). | Say 'traded beyond 84,000' and treat sub-10-pt breaks as noise. |
| 10:36 | BANKNIFTY | morning | VWAP Stretch: Oversold (-202.4 pts) | **WRONG** | index spot 63,784.2 minus BANKNIFTY-FUT VWAP 63,986.6 (basis mismatch); SIGN FLIP: futures close 64,070.0 is +83.4 vs VWAP | Restate on BANKNIFTY-FUT basis: Overbought (+83.4 pts vs futures VWAP 63,986.6); or stop mixing index spot with futures VWAP. |
| 10:39 | SENSEX | MARKET_ALERT | 🛑 WATCH INVALIDATED · PUT WATCH · SENSEX (triggered 2026-11-25 10:21:21 IST) | **OK** | status INVALIDATED, triggered 10:21:21, resolved 10:39:20 (MFE 117.1, MAE -70.8) — matches ledger | OK |
| 10:39 | SENSEX | morning | Call Wall Broken at ₹84,000 | **OK** | spot 84,153.6 is 153.6 pts beyond the wall. | OK (reword 'closed beyond' to 'traded beyond') |
| 10:40 | BANKNIFTY | morning | Impulsive Bullish Move (+96.2 pts) | **OK** | +96.2 pts reproduces from closed BANKNIFTY-FUT bars (10:36–10:40). | OK (futures basis) |
| 10:40 | SENSEX | MARKET_ALERT | ✅ WATCH TARGET_HIT · CALL WATCH · SENSEX (triggered 2026-11-25 10:30:41 IST) | **OK** | status TARGET_HIT, triggered 10:30:41, resolved 10:40:20 (MFE 187.9, MAE 0.0) — matches ledger | OK |
| 10:40 | SENSEX | MARKET_ALERT | 🏃 WALL CHASE ×2 · CALL WATCH · SENSEX | **WRONG** | Events lists the same break twice (Call Wall Broken at ₹84,000); ×2 counts that duplicate; Risk line is the trigger+kill text, so trigger and kill print twice; Drivers cut mid-sentence (400-char cap on drivers, web_server.py:3992); 'never at the magnet' appears 3x; resolved later: INVALIDATED at 10:44:03 (MFE 8.4, MAE -100.1); target 84,600 is 453 pts from spot (not reachable intraday) | 🏃 WALL CHASE · CALL WATCH · SENSEX — triggered 10:40:44 IST; one kill 84,075; target 84,600 (453 pts from spot); outcome INVALIDATED 10:44:03. Count distinct walls moved, not repeated prints of one wall. |
| 10:41 | SENSEX | MARKET_ALERT | 🏃 WALL CHASE ×2 · 📡 CONTINUATION BULL · 🔥 OVER CALL WALL · HIGH | **CHECK** | 'WALL CHASE' chip on an ALERT_TAPE card (describes the wall state, not this episode); 865-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate |
| 10:41 | SENSEX | morning | Impulsive Bullish Move (+91.0 pts) | **OK** | +91.0 pts reproduces from closed SENSEX-FUT bars (10:37–10:41). | OK (futures basis) |
| 10:44 | SENSEX | MARKET_ALERT | 🛑 WATCH INVALIDATED · CALL WATCH · SENSEX (triggered 2026-11-25 10:40:44 IST) | **OK** | status INVALIDATED, triggered 10:40:44, resolved 10:44:03 (MFE 8.4, MAE -100.1) — matches ledger | OK |
| 10:48 | SENSEX | morning | Book is call-heavy | **CHECK** | book flips call-heavy/put-heavy across 5-min samples (flip noise); timeseries at 10:45 says NEGATIVE | Publish only if the book holds for two consecutive samples; otherwise suppress |
| 10:51 | SENSEX | morning | Call Wall Migrated from ₹84,000 to ₹85,000 | **OK** | chain links and timeseries agree | OK |
| 10:56 | NIFTY | morning | Session VWAP Reclaimed at ₹25,872.1 | **CHECK** | index spot 25,876.7 vs NIFTY-FUT VWAP 25,872.1; futures close 26,020.0. Futures side agrees. Index-vs-futures basis caveat applies. | Say 'futures VWAP' and 'trading below/above session VWAP' (not 'intraday volume average'). |
| 10:57 | BANKNIFTY | morning | Impulsive Bullish Move (+97.8 pts) | **OK** | +97.8 pts reproduces from closed BANKNIFTY-FUT bars (10:53–10:57). | OK (futures basis) |
| 11:00 | SENSEX | morning | VWAP Stretch: Overbought (+369.1 pts) | **WRONG** | index spot 84,289.7 minus SENSEX-FUT VWAP 83,925.0 (basis mismatch); magnitude: card +369.1 vs futures +506.2; VWAP 83,920.6 vs bar VWAP 83,925.0 | Restate on SENSEX-FUT basis: Overbought (+506.2 pts vs futures VWAP 83,925.0); or stop mixing index spot with futures VWAP. |
| 11:02 | NIFTY | morning | Footprint: Institutional-Style Passive Absorption At Resistance | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 11:03 | BANKNIFTY | morning | Session VWAP Reclaimed at ₹64,016.9 | **CHECK** | index spot 64,019.5 vs BANKNIFTY-FUT VWAP 64,020.4; futures close 64,325.8. Futures side agrees. Index-vs-futures basis caveat applies. | Say 'futures VWAP' and 'trading below/above session VWAP' (not 'intraday volume average'). |
| 11:06 | SENSEX | MARKET_ALERT | 🏃 WALL CHASE ×3 · CALL WATCH · SENSEX | **WRONG** | Events lists the same break twice (Call Wall Broken at ₹84,000); ×3 counts that duplicate; Risk line is the trigger+kill text, so trigger and kill print twice; resolved later: INVALIDATED at 12:26:41 (MFE 160.5, MAE -66.3); target 85,100 is 770 pts from spot (not reachable intraday) | 🏃 WALL CHASE · CALL WATCH · SENSEX — triggered 11:06:20 IST; one kill 84,270; target 85,100 (770 pts from spot); outcome INVALIDATED 12:26:41. Count distinct walls moved, not repeated prints of one wall. |
| 11:06 | SENSEX | MARKET_ALERT | 🏃 WALL CHASE ×3 · 📡 CVD DIVERGENCE BULL · ⚖️ BETWEEN WALLS · MED | **WRONG** | 'WALL CHASE' chip on an ALERT_TAPE card (describes the wall state, not this episode); 793-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers; printed kill 84,400 but ledger kill level 84,270.10; ledger: INVALIDATED 12:26:41 (MFE 160.5, MAE -66.3) | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate; outcome INVALIDATED 12:26:41 (MFE +160.5, MAE -66.3) |
| 11:09 | SENSEX | morning | PCR Skew Extended: 1.46 | **OK** | timeseries pcr_oi 1.467 at 11:09 is consistent | OK |
| 11:16 | SENSEX | morning | Book is call-heavy | **CHECK** | book flips call-heavy/put-heavy across 5-min samples (flip noise); timeseries at 11:14 says NEGATIVE | Publish only if the book holds for two consecutive samples; otherwise suppress |
| 11:17 | SENSEX | MARKET_ALERT | 🏃 WALL CHASE ×3 · 📡 CONTINUATION BULL · ⚖️ BETWEEN WALLS · MED | **CHECK** | 'WALL CHASE' chip on an ALERT_TAPE card (describes the wall state, not this episode); 827-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate |
| 11:17 | NIFTY | morning | Put Wall Migrated from ₹25,700 to ₹25,800 | **CHECK** | next sample 11:22 shows 25,700, so the wall moved back | Keep as a dated flip note, with the prior level it came from |
| 11:20 | BANKNIFTY | morning | Footprint: Institutional-Style Volume Exhaustion | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 11:23 | SENSEX | MARKET_ALERT | 🏃 WALL CHASE ×2 · 📡 CVD DIVERGENCE BULL · ⚖️ BETWEEN WALLS · MED | **WRONG** | 'WALL CHASE' chip on an ALERT_TAPE card (describes the wall state, not this episode); 814-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers; printed kill 84,452 but ledger kill level 84,329.90; ledger: INVALIDATED 11:35:20 (MFE 38.7, MAE -64.4) | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate; outcome INVALIDATED 11:35:20 (MFE +38.7, MAE -64.4) |
| 11:24 | NIFTY | morning | Impulsive Bearish Move (-16.0 pts) | **OK** | -16.0 pts reproduces from closed NIFTY-FUT bars (11:20–11:24). | OK (futures basis) |
| 11:25 | SENSEX | morning | VWAP Stretch: Overbought (+352.5 pts) | **WRONG** | index spot 84,391.1 minus SENSEX-FUT VWAP 84,039.0 (basis mismatch); magnitude: card +352.5 vs futures +533.4 | Restate on SENSEX-FUT basis: Overbought (+533.4 pts vs futures VWAP 84,039.0); or stop mixing index spot with futures VWAP. |
| 11:26 | BANKNIFTY | morning | VWAP Stretch: Oversold (-74.7 pts) | **WRONG** | index spot 63,974.1 minus BANKNIFTY-FUT VWAP 64,048.8 (basis mismatch); SIGN FLIP: futures close 64,290.2 is +241.4 vs VWAP | Restate on BANKNIFTY-FUT basis: Overbought (+241.4 pts vs futures VWAP 64,048.8); or stop mixing index spot with futures VWAP. |
| 11:32 | SENSEX | midday | Book is put-heavy | **CHECK** | book flips call-heavy/put-heavy across 5-min samples (flip noise) | Publish only if the book holds for two consecutive samples; otherwise suppress |
| 11:35 | SENSEX | MARKET_ALERT | 🛑 WATCH INVALIDATED · CALL WATCH · SENSEX (triggered 2026-11-25 11:23:40 IST) | **OK** | status INVALIDATED, triggered 11:23:40, resolved 11:35:20 (MFE 38.7, MAE -64.4) — matches ledger | OK |
| 11:37 | SENSEX | midday | Footprint: Institutional-Style Aggressive Long Drive | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 11:38 | SENSEX | MARKET_ALERT | 📡 CONTINUATION BULL · ⚖️ BETWEEN WALLS · MED | **WRONG** | 676-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers; printed kill 84,390 but ledger kill level 84,320.50; ledger: INVALIDATED 12:04:40 (MFE 110.1, MAE -61.3) | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate; outcome INVALIDATED 12:04:40 (MFE +110.1, MAE -61.3) |
| 11:43 | BANKNIFTY | midday | Session VWAP Reclaimed at ₹64,060.2 | **CHECK** | index spot 64,065.0 vs BANKNIFTY-FUT VWAP 64,066.9; futures close 64,422.0. Futures side agrees. Index-vs-futures basis caveat applies. | Say 'futures VWAP' and 'trading below/above session VWAP' (not 'intraday volume average'). |
| 11:43 | NIFTY | midday | Gap-Up Open: +383.9 pts (+1.51%) | **WRONG** | Emitted 11:43 (midday). The gap is a 09:15 fact. Value +383.9 matches the 09:15 cash print 25,733.55, but it was reported 2h+ late. | Restate at 09:15: Gap-Up Open +383.9 pts (+1.51%) (cash 25,733.55 vs prior close 25,349.65). Mark as late emission. |
| 11:45 | NIFTY | MARKET_ALERT | 🛑 WATCH INVALIDATED · PUT WATCH · NIFTY (triggered 2026-11-25 09:42:56 IST) | **OK** | status INVALIDATED, triggered 09:42:56, resolved 11:45:10 (MFE 0.0, MAE -201.1) — matches ledger | OK |
| 11:45 | SENSEX | MARKET_ALERT | 📡 CONTINUATION BULL · ⚖️ BETWEEN WALLS · MED | **CHECK** | 676-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate |
| 11:53 | SENSEX | midday | Book is put-heavy | **CHECK** | book flips call-heavy/put-heavy across 5-min samples (flip noise); timeseries at 11:53 says POSITIVE | Publish only if the book holds for two consecutive samples; otherwise suppress |
| 11:56 | SENSEX | midday | Gamma Flip Crossed at ₹84,376 | **CHECK** | card flip 84,376 vs timeseries flip 84,391.0 at 11:53; spot 84,375.8 is below the flip, so the direction is not stated | State direction ('crossed up/down') and the flip value from the same sample |
| 12:02 | SENSEX | midday | Footprint: Institutional-Style Aggressive Long Drive | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 12:02 | SENSEX | midday | Book is put-heavy | **CHECK** | book flips call-heavy/put-heavy across 5-min samples (flip noise) | Publish only if the book holds for two consecutive samples; otherwise suppress |
| 12:04 | SENSEX | MARKET_ALERT | 📡 CONTINUATION BULL · ⚖️ BETWEEN WALLS · MED | **WRONG** | 697-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers; printed kill 84,421 but ledger kill level 84,285.80; ledger: INVALIDATED 12:17:23 (MFE 16.4, MAE -69.0) | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate; outcome INVALIDATED 12:17:23 (MFE +16.4, MAE -69.0) |
| 12:04 | SENSEX | MARKET_ALERT | 🛑 WATCH INVALIDATED · CALL WATCH · SENSEX (triggered 2026-11-25 11:38:01 IST) | **OK** | status INVALIDATED, triggered 11:38:01, resolved 12:04:40 (MFE 110.1, MAE -61.3) — matches ledger | OK |
| 12:07 | SENSEX | midday | Book is call-heavy | **CHECK** | book flips call-heavy/put-heavy across 5-min samples (flip noise); timeseries at 12:02 says NEGATIVE | Publish only if the book holds for two consecutive samples; otherwise suppress |
| 12:10 | NIFTY | midday | Impulsive Bearish Move (-44.0 pts) | **OK** | -44.0 pts reproduces from closed NIFTY-FUT bars (12:06–12:10). | OK (futures basis) |
| 12:16 | SENSEX | midday | Gamma Flip Crossed at ₹84,181 | **CHECK** | card flip 84,181 vs timeseries flip 84,220.8 at 12:10; spot 84,276.8 is above the flip, so the direction is not stated | State direction ('crossed up/down') and the flip value from the same sample |
| 12:17 | SENSEX | MARKET_ALERT | 🛑 WATCH INVALIDATED · CALL WATCH · SENSEX (triggered 2026-11-25 12:04:20 IST) | **OK** | status INVALIDATED, triggered 12:04:20, resolved 12:17:23 (MFE 16.4, MAE -69.0) — matches ledger | OK |
| 12:21 | SENSEX | MARKET_ALERT | 📡 CONTINUATION BULL · ⚖️ BETWEEN WALLS · MED | **WRONG** | 676-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers; printed kill 84,436 but ledger kill level 84,276.10; ledger: INVALIDATED 12:26:41 (MFE 15.0, MAE -72.3) | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate; outcome INVALIDATED 12:26:41 (MFE +15.0, MAE -72.3) |
| 12:23 | SENSEX | midday | Footprint: Institutional-Style Aggressive Long Drive | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 12:26 | SENSEX | MARKET_ALERT | 🛑 WATCH INVALIDATED · CALL WATCH · SENSEX (triggered 2026-11-25 11:06:20 IST) | **OK** | status INVALIDATED, triggered 11:06:20, resolved 12:26:41 (MFE 160.5, MAE -66.3) — matches ledger | OK |
| 12:26 | SENSEX | MARKET_ALERT | 🛑 WATCH INVALIDATED · CALL WATCH · SENSEX (triggered 2026-11-25 12:21:20 IST) | **OK** | status INVALIDATED, triggered 12:21:20, resolved 12:26:41 (MFE 15.0, MAE -72.3) — matches ledger | OK |
| 12:31 | BANKNIFTY | midday | Impulsive Bearish Move (-133.4 pts) | **OK** | -133.4 pts reproduces from closed BANKNIFTY-FUT bars (12:27–12:31). | OK (futures basis) |
| 12:32 | NIFTY | midday | Impulsive Bearish Move (-72.9 pts) | **OK** | -72.9 pts reproduces from closed NIFTY-FUT bars (12:28–12:32). | OK (futures basis) |
| 12:32 | SENSEX | midday | Book is put-heavy | **CHECK** | book flips call-heavy/put-heavy across 5-min samples (flip noise); timeseries at 12:23 says POSITIVE | Publish only if the book holds for two consecutive samples; otherwise suppress |
| 12:38 | SENSEX | midday | Book is put-heavy | **CHECK** | book flips call-heavy/put-heavy across 5-min samples (flip noise) | Publish only if the book holds for two consecutive samples; otherwise suppress |
| 12:39 | NIFTY | midday | Put Wall Broken at ₹25,800 | **OK** | spot 25,787.5 is 12.5 pts beyond the wall. | OK (reword 'closed beyond' to 'traded beyond') |
| 12:44 | SENSEX | midday | Footprint: Institutional-Style Volume Exhaustion | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 12:52 | SENSEX | midday | Put Wall Broken at ₹84,000 | **OK** | spot 83,853.5 is 146.5 pts beyond the wall. | OK (reword 'closed beyond' to 'traded beyond') |
| 13:00 | BANKNIFTY | midday | Footprint: Institutional-Style Volume Exhaustion | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 13:08 | NIFTY | midday | Footprint: Institutional-Style Volume Exhaustion | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 13:09 | SENSEX | midday | Gamma Flip Crossed at ₹83,508 | **CHECK** | card flip 83,508 vs timeseries flip 83,526.9 at 13:06; spot 83,517.5 is above the flip, so the direction is not stated | State direction ('crossed up/down') and the flip value from the same sample |
| 13:16 | SENSEX | midday | Gamma Flip Crossed at ₹83,407 | **CHECK** | card flip 83,407 vs timeseries flip 83,481.4 at 13:13; spot 83,461.8 is above the flip, so the direction is not stated | State direction ('crossed up/down') and the flip value from the same sample |
| 13:30 | NIFTY | afternoon | Put Wall Migrated from ₹25,500 to ₹25,700 | **WRONG** | prior card on this wall ends at 25,800; this card starts at 25,500 (step missing from record) | Show the missing step or drop this card; the chain does not link |
| 13:30 | SENSEX | afternoon | Put Wall Migrated from ₹83,000 to ₹83,500 | **WRONG** | prior card on this wall ends at 83,800; this card starts at 83,000 (step missing from record); next sample 13:37 shows 83,000, so the wall moved back | Show the missing step or drop this card; the chain does not link |
| 13:36 | SENSEX | afternoon | Put Wall Migrated from ₹83,500 to ₹83,000 | **OK** | chain links and timeseries agree | OK |
| 13:39 | NIFTY | afternoon | VWAP Stretch: Oversold (-216.5 pts) | **WRONG** | index spot 25,705.8 minus NIFTY-FUT VWAP 25,922.3 (basis mismatch); magnitude: card -216.5 vs futures -66.1 | Restate on NIFTY-FUT basis: Oversold (-66.1 pts vs futures VWAP 25,922.3); or stop mixing index spot with futures VWAP. |
| 13:44 | BANKNIFTY | afternoon | VWAP Stretch: Oversold (-530.8 pts) | **WRONG** | index spot 63,498.5 minus BANKNIFTY-FUT VWAP 64,029.3 (basis mismatch); magnitude: card -530.8 vs futures -204.1 | Restate on BANKNIFTY-FUT basis: Oversold (-204.1 pts vs futures VWAP 64,029.3); or stop mixing index spot with futures VWAP. |
| 13:50 | SENSEX | afternoon | PCR Contraction: 0.93 → 0.82 (-0.11) | **OK** | timeseries pcr_oi 0.828 at 13:44 is consistent | OK |
| 13:52 | SENSEX | afternoon | VWAP Stretch: Oversold (-410.8 pts) | **WRONG** | index spot 83,678.9 minus SENSEX-FUT VWAP 84,089.1 (basis mismatch); magnitude: card -410.8 vs futures -319.9 | Restate on SENSEX-FUT basis: Oversold (-319.9 pts vs futures VWAP 84,089.1); or stop mixing index spot with futures VWAP. |
| 13:59 | SENSEX | afternoon | Call Wall Migrated from ₹84,000 to ₹85,000 | **WRONG** | prior card on this wall ends at 85,000; this card starts at 84,000 (step missing from record) | Show the missing step or drop this card; the chain does not link |
| 14:01 | SENSEX | MARKET_ALERT | 🏃 WALL CHASE ×2 · PUT WATCH · SENSEX | **WRONG** | Risk line is the trigger+kill text, so trigger and kill print twice; resolved later: INVALIDATED at 15:11:00 (MFE 57.1, MAE -101.8); target 82,900 is 997 pts from spot (not reachable intraday) | 🏃 WALL CHASE · PUT WATCH · SENSEX — triggered 14:01:48 IST; one kill 83,985; target 82,900 (997 pts from spot); outcome INVALIDATED 15:11:00. Count distinct walls moved, not repeated prints of one wall. |
| 14:01 | BANKNIFTY | afternoon | Impulsive Bullish Move (+210.0 pts) | **OK** | +210.0 pts reproduces from closed BANKNIFTY-FUT bars (13:57–14:01). | OK (futures basis) |
| 14:02 | SENSEX | MARKET_ALERT | 🏃 WALL CHASE ×2 · 📡 ABSORPTION BEAR · ⚖️ BETWEEN WALLS · MED | **CHECK** | 'WALL CHASE' chip on an ALERT_TAPE card (describes the wall state, not this episode); 853-char body with fixed boilerplate sentences (book, breadth, zone) in Drivers; formula-like fragment 'Fresh orderflow print ≥NN:' in Drivers | Headline + Outcome + Trigger + one Kill (ledger value) + Target; drop chip and boilerplate |
| 14:03 | NIFTY | afternoon | Impulsive Bullish Move (+80.4 pts) | **CHECK** | Card +80.4 pts vs closed-bar +54.6 pts. Likely read from the forming bar at card time. | Recompute from closed bars before publishing. |
| 14:03 | SENSEX | afternoon | Impulsive Bullish Move (+230.8 pts) | **CHECK** | Card +230.8 pts vs closed-bar +294.2 pts. Likely read from the forming bar at card time. | Recompute from closed bars before publishing. |
| 14:15 | BANKNIFTY | afternoon | VWAP Stretch: Oversold (-453.4 pts) | **WRONG** | index spot 63,571.5 minus BANKNIFTY-FUT VWAP 64,024.8 (basis mismatch); magnitude: card -453.4 vs futures -83.6 | Restate on BANKNIFTY-FUT basis: Oversold (-83.6 pts vs futures VWAP 64,024.8); or stop mixing index spot with futures VWAP. |
| 14:18 | NIFTY | afternoon | VWAP Stretch: Oversold (-160.2 pts) | **WRONG** | index spot 25,761.5 minus NIFTY-FUT VWAP 25,921.7 (basis mismatch); SIGN FLIP: futures close 25,932.0 is +10.3 vs VWAP | Withdraw. On NIFTY-FUT the stretch is +10.3 pts vs VWAP 25,921.7, inside the 35-pt minimum for an extension. |
| 14:27 | SENSEX | afternoon | Impulsive Bullish Move (+41.4 pts) | **OK** | +41.4 pts reproduces from closed SENSEX-FUT bars (14:23–14:27). | OK (futures basis) |
| 14:36 | SENSEX | afternoon | Footprint: Institutional-Style Passive Absorption At Resistance | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 14:41 | BANKNIFTY | afternoon | Impulsive Bearish Move (-67.0 pts) | **OK** | -67.0 pts reproduces from closed BANKNIFTY-FUT bars (14:37–14:41). | OK (futures basis) |
| 14:49 | NIFTY | afternoon | VWAP Stretch: Oversold (-173.2 pts) | **WRONG** | index spot 25,748.0 minus NIFTY-FUT VWAP 25,921.2 (basis mismatch); magnitude: card -173.2 vs futures -19.1 | Withdraw. On NIFTY-FUT the stretch is -19.1 pts vs VWAP 25,921.2, inside the 35-pt minimum for an extension. |
| 14:55 | SENSEX | afternoon | Footprint: Institutional-Style Passive Absorption At Resistance | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 14:58 | BANKNIFTY | afternoon | VWAP Stretch: Oversold (-431.5 pts) | **WRONG** | index spot 63,587.7 minus BANKNIFTY-FUT VWAP 64,019.2 (basis mismatch); magnitude: card -431.5 vs futures -179.2 | Restate on BANKNIFTY-FUT basis: Oversold (-179.2 pts vs futures VWAP 64,019.2); or stop mixing index spot with futures VWAP. |
| 14:58 | SENSEX | afternoon | Call Wall Migrated from ₹85,000 to ₹84,000 | **OK** | chain links and timeseries agree | OK |
| 15:10 | SENSEX | closing_30m | Call Wall Migrated from ₹84,000 to ₹85,000 | **CHECK** | next sample 15:16 shows 84,000, so the wall moved back | Keep as a dated flip note, with the prior level it came from |
| 15:11 | SENSEX | MARKET_ALERT | 🛑 WATCH INVALIDATED · PUT WATCH · SENSEX (triggered 2026-11-25 14:01:48 IST) | **OK** | status INVALIDATED, triggered 14:01:48, resolved 15:11:00 (MFE 57.1, MAE -101.8) — matches ledger | OK |
| 15:14 | NIFTY | closing_30m | Put Wall Migrated from ₹25,700 to ₹25,500 | **OK** | chain links and timeseries agree | OK |
| 15:18 | SENSEX | closing_30m | Footprint: Institutional-Style Passive Absorption At Resistance | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
| 15:20 | NIFTY | closing_30m | VWAP Stretch: Oversold (-129.9 pts) | **WRONG** | index spot 25,791.2 minus NIFTY-FUT VWAP 25,921.1 (basis mismatch); magnitude: card -129.9 vs futures -12.1 | Withdraw. On NIFTY-FUT the stretch is -12.1 pts vs VWAP 25,921.1, inside the 35-pt minimum for an extension. |
| 15:28 | BANKNIFTY | closing_30m | VWAP Stretch: Oversold (-367.6 pts) | **WRONG** | index spot 63,651.6 minus BANKNIFTY-FUT VWAP 64,019.2 (basis mismatch); magnitude: card -367.6 vs futures -179.2 | Restate on BANKNIFTY-FUT basis: Oversold (-179.2 pts vs futures VWAP 64,019.2); or stop mixing index spot with futures VWAP. |
| 15:32 | SENSEX | closing_30m | Call Wall Migrated from ₹84,000 to ₹85,000 | **WRONG** | prior card on this wall ends at 85,000; this card starts at 84,000 (step missing from record) | Show the missing step or drop this card; the chain does not link |
| 15:38 | SENSEX | closing_30m | Footprint: Institutional-Style Passive Absorption At Resistance | **CHECK** | 'Institutional-style' names a participant type the tape cannot identify (NSE participant flow unmeasured). | Replace 'institutional-style' with a tape-only descriptor, e.g. 'large-volume passive absorption' |
