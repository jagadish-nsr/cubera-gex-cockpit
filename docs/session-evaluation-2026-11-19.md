# Session Evaluation — Thursday, 19-Nov-2026

Sensex weekly expiry. Cash session closed. Eval from prod journals
(`cubera-gex-cockpit-prod`), tag **v5.3.157**. Canvas:
`session-eval-2026-11-19.canvas.tsx`.

Strong risk-on day: Nifty +286 (+1.13%), Bank Nifty +1,024 (+1.65%),
Sensex +990 (+1.21%). Institutional CVD net buyers on all three.

## Reliability scorecard

| Metric | Value | Notes |
|---|---|---|
| Process version | v5.3.157 | Health + reliability API |
| Degrade mode (as-of) | OFF_SESSION | Post-close |
| Ready minutes (09:20–15:20) | **360 / 360** | Burn **0.0%** (SLO ≤ 1%) |
| Not-ready minutes | **0** | Soft NSE / degraded flags no longer fail ready |
| Degrade modes (minute) | OK 161 · CHAIN_DEGRADED 131 · BOOK_NSE_SCRAPE 69 | Modes journaled; ready stayed true |
| `no_fresh_chain` | **0** | Snap-meta aging held |
| Watchdog restarts | 0 | Mid-session serve/ingest stable |
| Ingest heartbeat | age ~1–2s | Detail populated |
| NSE fallback events | **281** | Nifty 132, Bank Nifty 149 — real Dhan pressure |
| Chain publishes | **2,363** | |
| Session checklist | PASS | |

### What “CHAIN_DEGRADED / BOOK_NSE” meant with 0% burn

The overnight soft-ready change held: scrape/source warnings set
`degrade_mode` but did **not** append to `reasons`, so the minute rollup
stayed ready. Banner can still note source quality without a false
all-day outage.

## Premarket

| Item | Value |
|---|---|
| Digest | Sealed OK · `HIGH_SHOCK` · heuristic narrative |
| Theme | Hawkish Fed / lower open cues; Gold dump; India heavyweights |
| Nifty auction | Open ~25,187 (−41 / −0.16%) · `MODERATE_DRIFT` · **MAP ONLY** |
| Bank Nifty | Open ~62,204 (+165 / +0.27%) · shared-gamma watch · structure null |
| Sensex | Open ~82,377 (+345 / +0.42%) · shared-gamma watch @ 82,000 · structure null |

Gap fill: **FILLED** on all three. Opening watches staged without priced
premiums (MAP / watch-only — correct for those regimes).

## Close book / KPIs

| Index | Settle | Session Δ | Call / put | Note |
|---|---:|---:|---|---|
| Nifty | 25,514.3 | +286.3 (+1.13%) | 25,500 / 25,300 | Pin efficiency **71.4%** to call-gamma; CVD +913k |
| Bank Nifty | 63,063.0 | +1,024.3 (+1.65%) | 62,500 / 62,000 (close snap) | Ran through open wall; CVD +170k |
| Sensex | 83,021.75 | +989.9 (+1.21%) | 82,700 / 82,600 (close snap) | Expiry; post-close near-book expired on live KPI |

Off-session cockpit: Nifty put-heavy / net GEX −7.7B; BN call-heavy shared
63,000; Sensex walls null (`Near book expired`) — expected after 0DTE roll
off. Timeseries last print (15:17) still had Sensex walls 83,000 / 82,900.

## Timeseries journal

All three indices written (gap from 18-Nov fixed): **25 rows each**,
09:17→15:17. Sources mostly dhan; Nifty/BN 3 NSE-fallback rows; Sensex
100% dhan.

## Order-flow / Sentinel tape

| Index | 1m bars | Signals | Closing CVD |
|---|---:|---:|---:|
| Nifty | 384 (→15:38) | 103 | +912,771 |
| Bank Nifty | 384 | 79 | +170,428 |
| Sensex | 383 | 37 | +8,286 |

**219 signals** total. Bias LONG 181 / SHORT 38. Types dominated by
CONTINUATION_BULL (90) and CVD_DIVERGENCE_BULL (77). Status: TARGET_HIT
**141** / INVALIDATED 60 / ACTIVE 18. All had `target_1_pts`.

Bull continuation/CVD sleeves printed high target-hit rates (Nifty CONT
28/38, BN CVD bull 32/36, Sensex CONT 14/16). Sensex bear absorption /
CVD bear were stopped hard into the trend (0 target hits).

Sentinel trade recs: **22** (14 TARGET / 5 SPOT_INVAL / 3 ACTIVE), avg
`pnl_pct` **+8.6%**. Paper trades: 50 (45 closed), net ~**₹19.7k**, exits
SPOT_INVAL 25 / TARGET 20.

## GO Desk

Today’s events: **37** (35 EMIT, 2 STAGE) — mostly Nifty (34), Sensex 3.
FIRE 30 / SCOUT 7. Strategies: CONT_BULL 10, EXHAUSTION_DOWN 9, plus
WRITE_OTM / CVD_BULL / ABSORPTION_BEAR.

**Targets fix held:** premium 3 · **spot 30** · empty only **4**.
(Yesterday every event was empty.)

Paper GO opens: 2 new Nifty FIRE opens still OPEN at eval; prior-day
orphans still OPEN (trail/close gap remains). Cumulative efficiency API
unchanged vs ledger (Nifty net ~₹21.5k on 8 closed historical).

## Trade Ideas / recommendations

| Metric | Value |
|---|---|
| Records touching today | **383** (missing T1/SL: **0**) |
| Closed | 235 · TGT 127 · SL 36 · INV 72 |
| Hit vs SL | **78%** (127/163) |
| Nifty closed | 64 · TGT-heavy (26+24 T1/T2, SL 6) |
| Bank Nifty closed | 70 · TGT 36 / INV 29 / SL 5 |
| Sensex closed | 101 · more mixed (INV 35, SL 25) |
| HIGH tier closed | 11 · TGT 6 / SL 5 |

Refuses: 24 (ATM IV 11, walls/flip 9, VWAP 4) — not a sustained blackout.
Latest pickle: Nifty/BN still have high-conviction CE clusters; **Sensex
batch empty** post-expiry (`Missing call wall…`) — matches near-book
expired KPI.

Rolling effectiveness panel: Nifty T1 38.2%; Structure / Iceberg Fade
still the cleaner sleeves.

## Session commentary

**250** entries. MARKET_ALERT heavy on Nifty (90) and Sensex (38); morning /
midday / afternoon / closing_30m present on all three. Late prints include
call-wall test/break at 25,500 and BN exhaustion into the close.

## Sensex expiry — gamma blast

Blast lane **ran** (unlike quiet 18-Nov):

| Journal | Rows | Window |
|---|---:|---|
| `gamma_blast_score/SENSEX` | 81 | 12:07→15:20 |
| `gamma_blast_dhan/SENSEX` | 2,640 strike-minutes | 13:02→15:20 |
| `blast_ticks/SENSEX` | 114,358 ticks | 13:00→15:39 |
| Ops | 159 `blast_ticks.count`, 20 crossovers | |

Score states: NO_TRIGGER 34 · **COILING 32** · **TRIGGERED 15**.
TRIGGERED 13:19→15:19, mostly `GAMMA PIN: COMPRESS` at 82,500 then 82,700;
one `GAMMA BLAST: ACTIVE`. Max score **96** at 15:19 (primary 82,900,
sustain FAKE-leaning pin/cap). Cover side mostly call. Settle finished
**~322 pts above** the close-snap call wall — location, not a pin
(pin_efficiency 0%).

## Target Engine

| Index | Off-session read |
|---|---|
| Nifty | OK · dominant **bull** · conviction 1.0 · bull target = day high 25,514 |
| Bank Nifty | OK · dominant **bull** · target ~63,261 |
| Sensex | **Refuse** — `target_engine_unavailable` (no live ATM IV after 0DTE book expired) |

Honest refuse on Sensex; do not invent 0.15 IV.

## Scorecard (desk)

| Dimension | Rating | Notes |
|---|:---:|---|
| Premarket | Solid | HIGH_SHOCK sealed; MAP/watch regimes matched soft open |
| Reliability | Excellent | **0% burn**; scrape modes soft |
| Tape / OF | Strong | Full 1m bars; bull OF dominated; CVD buyers |
| Trade Ideas | Strong | 78% vs SL; Sensex post-expiry empty expected |
| GO | Improved | Spot targets on 30/37; still thin Sensex EMITs |
| Commentary | Active | 250 cards through close |
| Sensex blast | Active | COILING/TRIGGERED with tick + strike journals |
| Target Engine | Mixed | Nifty/BN bull; Sensex IV refuse post-expiry |
| KPIs / close | Clear | Nifty pin to 25,500; BN/Sensex trend extension |

## Gaps / follow-ups

1. **GO open orphans** — prior-day OPEN rows never trail-closed; today’s new
   opens still OPEN after F&O close.
2. **Sensex post-expiry empty Ideas/TE** — expected once near book expires;
   surface an explicit “0DTE rolled off” notice instead of bare refuse.
3. **CHAIN_DEGRADED minutes while ready** — confirm banner copy stays soft
   (source/LKGS flag) so operators do not re-read it as outage.
4. **Timeseries cadence** — 25 rows/day is sparse vs 5-min intent across
   09:15–15:30 (~75 slots); check skip/dedup vs scheduler.
5. Blast sustain often **FAKE-leaning** into the final hour — keep gated
   language; settle was not a pin.

## Disclaimer

Decision-support review, not financial advice. Human confirms all decisions.
