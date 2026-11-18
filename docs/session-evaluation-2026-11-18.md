# Session Evaluation — Wednesday, 18-Nov-2026

Cash session closed. Eval from prod journals (`cubera-gex-cockpit-prod`),
tag **v5.3.150** (promoted overnight after the 17-Nov EXTERNAL heal). Canvas:
`session-eval-2026-11-18.canvas.tsx`.

## Reliability scorecard

| Metric | Value | Notes |
|---|---|---|
| Process version | v5.3.150 | Health + reliability API |
| Degrade mode (as-of) | OFF_SESSION | Post-close |
| Ready minutes (09:20–15:20) | **253 / 360** | Admin scorecard |
| Not-ready minutes | **107** | Burn **29.7%** (SLO ≤ 1%) |
| Not-ready reasons | Almost all `book_from_nse_scrape:*` | Nifty 76, Bank Nifty 26, both 4 |
| `no_fresh_chain` | **0** | Fix held — chains aged from snap meta |
| `feed_detail_missing` | **2** min | 10:45 and 14:08 with ingest heartbeat blip |
| Watchdog restarts | 0 mid-session | Serve+ingest since 02:20 promote |
| Ingest heartbeat | age ~0.5–5s; detail populated | WS fields present (not `{}`) |
| NSE fallback events | **270** | Nifty 191, Bank Nifty 79 |
| Chain publishes (ingest) | **2,362** | Books were live; ages on not-ready mins were seconds, not hours |

### What DEGRADED meant today

Not the 17-Nov false `no_fresh_chain`. Serve readiness read ingest snap meta;
chain ages on not-ready minutes stayed in the single-digit / low-tens of seconds.
The banner was loud because Dhan 429s pushed the book onto the NSE scrape path
(`book_from_nse_scrape`). That is a real degraded **source**, correctly surfaced.

Heartbeat detail was populated for nearly the whole session. Two single-minute
blips still combined `feed_detail_missing` with `ingest_heartbeat_missing`.

## Scorecard (desk)

| Dimension | Rating | Notes |
|---|:---:|---|
| Macro | Soft down day | Nifty −159.5 (−0.63%); BN −234.6; Sensex −500.4. Nifty CVD close −65.9k (net sellers) |
| Tape completeness | Strong | Sentinel 1m ~384 bars/index through ~15:38; 15m/5m/3m complete |
| Trade Ideas | Mixed | 303 records with T1/SL filled; Nifty high-conviction empty all day (scores under 75) |
| GO Desk | Loud but incomplete | 76 events today; **targets `{}` on every EMIT/STAGE**; paper trail closes worked |
| Sentinel OF | Active | 146 signals; 56 TARGET_HIT / 77 INVALIDATED / 13 ACTIVE |
| Stability | Partial | Burn 30% on real NSE scrape; no zombie-chain false alarm |
| GEX / close book | OK | Close snaps + shared-gamma notes on BN/Sensex; Nifty walls 25,300 / 25,000 |
| Timeseries journal | Weak | **Only Nifty** CSV written (73 rows); BN/Sensex absent |
| Gamma blast | Quiet | No score files today; blast tick journal idle |

## Close book

| Index | Settle | Call / put wall | Note |
|---|---:|---|---|
| Nifty | 25,227.95 | 25,300 / 25,000 | `session-close:dhan`; flip ~25,110 |
| Bank Nifty | 62,038.65 | 62,000 / 62,000 | shared-gamma; pin_efficiency 61.3% |
| Sensex | 82,031.81 | 82,000 / 82,000 | shared-gamma; pin_efficiency 68.2% |

Nifty timeseries modal walls during the day: 25,300/25,000 (34) then
25,500/25,000 (26); 13 shared-gamma 25,500/25,500 rows. Sources 54 dhan /
19 nse-fallback.

## Trade Ideas — targets / stops / efficiency

**Stored cards had target and stop.** All 303 `recommendation_records` that
touched today have non-zero `target_1` and `stop_loss`. Latest pickles same.
The blank T/SL complaint does **not** match the Ideas store.

What *was* thin on the Ideas surface:

- Nifty `higher_conviction` stayed empty (exec summary: best CE 74 / PE 66 under
  the 75 line). Desk saw tactical / below-line cards only.
- Opening-watch structure for Nifty was **unpriced** (`premium_note: unpriced`,
  `target_buy_pts` / `target_net_pts` null).
- Five `rec.refused` (3 missing walls/flip, 2 missing session VWAP) — not sustained.

Today’s closed Ideas outcomes (not the rolling multi-day effectiveness panel):

| Status | n |
|---|---:|
| ACTIVE (still open at eval) | 170 |
| TGT1_HIT | 41 |
| TGT2_HIT | 9 |
| SL_HIT | 44 |
| INVALIDATED | 39 |

Closed hit rate vs SL: **50 / 94 = 53%**. Including invalidations:
**50 / 133 = 38%**. HIGH tier closed **1 TGT / 8 SL / 5 INV** — poor.

Rolling effectiveness panel (API, multi-day tracked sample): Nifty T1 33.4% /
SL 14.7%; Bank Nifty T1 21.8% / spot-inval 30.3%; Sensex T1 32.2% / spot-inval
33.9%. Structure and Iceberg Fade remain the cleaner sleeves; Vol Dispersion
and Strike Break stay weak.

## GO Desk — empty targets (the real T/SL gap)

Every one of today’s **76** `go_events` persisted `targets: {}`. FIRE EMITs
still carried contracts + `risk.max_loss_inr`, and invalidation spot rules, but
no premium/spot target payload for the card. That matches “recommendations
without target/SL” if the user was on GO, not Ideas.

Today’s GO activity (events table): Sensex 49, Nifty 27, Bank Nifty 0 in the
day slice; strategies EXHAUSTION_DOWN 29, CONTINUATION_BULL 27, ABSORPTION_BEAR
19. Paper/`go_open`: 1 Sensex EXHAUSTION_DOWN closed TRAIL_STOP (+₹712), 2 still
OPEN; several prior-day trails marked today morning.

Efficiency API (cumulative process window, not only today): Nifty net ~₹21.5k
on 8 closed (all wins in that ledger); Sensex ~₹5.8k on 3 closed. Treat as
paper ledger, not live P&L.

## Sentinel order-flow tape

| Index | 1m bars | Window |
|---|---:|---|
| Nifty | 384 | 09:15–15:38 |
| Bank Nifty | 384 | 09:15–15:38 |
| Sensex | 382 | 09:15–15:37 |

146 signals. Types: CONTINUATION_BULL 36, CVD_DIV_BULL 32, CVD_DIV_BEAR 23,
CONTINUATION_BEAR 22, EXHAUSTION_DOWN 19, ABSORPTION_BEAR 10. Outcomes: target
hit 56, invalidated 77, active 13. All signals had `target_1_pts` and
invalidation populated.

Sentinel-linked trade recommendations: **22** (8 TARGET_HIT, 14 SPOT_INVAL),
avg `pnl_pct` **−0.82%**. Paper trades touching today: 61 (59 closed), net
sum ~₹9.1k; exits SPOT_INVAL 36 / TARGET 19 / TRAIL_STOP 4.

## Commentary / alerts / other surfaces

- Commentary entries: **279** (Nifty 107, Sensex 146, Bank Nifty 26). Phases
  dominated by MARKET_ALERT (169), then morning/midday/afternoon.
- Big-money lane ran; raw parquet for all three indices.
- Target Engine: off-market refuse when ATM IV ≤ 5% still applies (seen in
  promote gate); not a cash-session desk signal.
- HTTP slow: 45 (session-checklist, go/digest, cockpit-data).
- Gamma blast score/tick journals effectively idle today.

## Gaps / follow-ups

Validation as-of **2026-11-18 17:40 IST** on prod journals + tree at **v5.3.153**
(cash session ran **v5.3.150**).

| # | Gap | Status | Evidence |
|---|---|---|---|
| 1 | GO `targets` always empty | **Fixed in code; not cash-proven** | `sentinel_spot_targets` shipped in `c073c05` (17:11). Cash window: **74/74 empty**. After 17:11: **6/8 filled** with `spot_t*`. |
| 2 | NSE scrape burn ~30% | **Fixed in code; not cash-proven** | `BOOK_NSE_SCRAPE` + ready stays true when chain fresh; UI treats as OK (`cockpit.js`). Cash burn still **29.7%** under v5.3.150. |
| 3 | Timeseries only Nifty | **Fixed in code** | `capture_once` prefers fresh `latest_snap_*.pkl` before live fetch so BN/SX are not starved; `run_timeseries.sh` covers all three. Cash proof next open. |
| 4 | Ideas HIGH tier 1-for-8 | **Fixed in code** | `conviction_tier_from_score` caps HIGH→MEDIUM on NSE scrape / soft-down bias (`<= -BIAS_STRONG`). |
| 5 | Opening-watch unpriced | **Fixed** | `premium_note: unpriced` + title/caution; UI chip; tests green. |
| 6 | `degrade_mode` on minute SLO | **Fixed in code; partial journal** | `slo_monitor` journals `degrade_mode` (`c073c05`). Today: **25 / 1061** minute rows carry the key (post-promote). |
| 7 | Blast quiet Wednesday | **Confirmed by design** | 1-min blast only on expiry + 13:00–close; Wed 18-Nov was non-expiry for BN/SX. |

Next open: prove **#1 / #2 / #3 / #6** under cash hours (BN/SX CSV rows; ready burn without scrape false reds; GO `spot_t*`; minute `degrade_mode`).

## Disclaimer

Decision-support review, not financial advice. Human confirms all decisions.
