# Session Evaluation — Friday, 13-Nov-2026

Canvas: `session-eval-2026-11-13` (IDE canvases folder).

## Reliability scorecard

Session: `2026-11-13` · cite SLI burn from `docs/sli-slo.md`.

| Metric | Value | Notes |
|---|---|---|
| Process version (as-of close / fix) | 5.3.130 midday → **5.3.133** post-fix | Must match promoted tag |
| Degrade mode (as-of) | OFF_SESSION | Post-close |
| Ready minutes (session) | **0** | burn=**100%** · 358 min sampled (`slo.readiness.minute`) |
| Not-ready minutes | **358** | Target burn ≤ 1% of 09:20–15:20 — **failed** |
| Watchdog restarts | **0** | Mid-session serve OK |
| Ingest heartbeat | dual, alive | age_s fine while readiness lied |
| Session checklist (as-of) | PASS off-session | CRITICAL readiness false held 09:17–15:40 |
| VWAP / Ideas refuses | **0** | No Missing session VWAP |

Root cause of 100% burn: EXTERNAL serve readiness probed **local-empty** chain/spot/WS while ingest published. Fixed in **v5.3.133** (windowed store key + snap meta + SQLite spot + heartbeat feed detail; EXTERNAL no longer starts chain threads).

## Scorecard (desk)

| Dimension | Rating | Notes |
|---|:---:|---|
| Macro | **Mild risk-off / chop** | Nifty-FUT −27, BN-FUT −105, SX-FUT +41; cash −155 / −484 / +45 |
| Tape completeness | **Excellent** | 384 / 383 / 383 1m bars; live OI on all three |
| Trade Ideas | **Online (from 10:14)** | 1,993 gens · 0 zero-count · 0 VWAP refuses |
| Sentinel OF | **Mixed (45.5% WR)** | 48 signals; CONT_BULL 36%; ABS_BEAR 3/3 |
| Sentinel paper | **+₹4,491** | 7 TARGET / 16 SPOT_INVAL |
| GO Desk | **FIRE proven** | 42 FIRE · 10 STAGE · 10 `go_open` · 4 trail closes +₹7,830 |
| Stability | **Noisy** | Pre-open promote 127→130; chain p95 ~35s; readiness SLO false all day |

## Headline

Yesterday’s open checks largely cleared: Ideas mint without VWAP refuse, and
sentinel-led GO crossed FIRE → auto-stage → trail. The book that printed was a
chop day — CONT_BULL stayed weak, closed GO trails paid, and open GO marks were
deep underwater into the close. Anti-fabrication held: sentinel-led rows keep
empty `targets`; Ideas-latched rows carry live TE/BS levels, not LTP×ratios.

## Futures path

| Index | Open | High | Low | Close | Chg | Range | 1m bars | Distinct OI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NIFTY-FUT | 25,202 | 25,286 | 25,083 | 25,175 | −27 | 203 | 384 | 275 |
| BANKNIFTY-FUT | 61,025 | 61,140 | 60,666 | 60,920 | −105 | 474 | 383 | 294 |
| SENSEX-FUT | 81,561 | 81,967 | 81,350 | 81,603 | +41 | 617 | 383 | 50 |

Cash session-close snapshot: Nifty 25,144 / BN 61,034 / Sensex 81,592.
Walls at close: NF 25,200/25,000 · BN 61,000/60,500 · SX 81,500/80,000.

## Trade Ideas — live proof

| Hour IST | `rec.generated` | Notes |
|---|---:|---|
| 09 | **80** DB / **535** ops | Ideas **did** mint from 09:15 on ingest; serve latch was empty |
| 10 | 14 DB | First GO FIRE 10:13 was sentinel-led (serve `_REC_BATCH` miss) |
| 11 | 23 | Quiet mid-morning |
| 12–15 | 353 / 507 / 608 / 450 | Steady through close |

- Ops: **~6.7k** `rec.generated` across the day; DB `recommendation_records` **190**
  with **80** in hour 09 from **09:15:01**.
- **0** sustained Missing session VWAP refuses (3 early wall/flip refuses only).
- First GO FIRE at 10:13 was **sentinel-led** because EXTERNAL **serve** never saw
  ingest’s in-memory Ideas latch — not because Ideas were dark.

Fix (v5.3.136+): ingest writes `latest_rec_{SYMBOL}.pkl`; serve `_latest_rec_batch`
reads it; EXTERNAL serve must not start `rec.publisher`.

## OrderFlow Sentinel

| Index | Signals | Hit / Stop | Resolved WR | mean \|Δratio\| | bars ≥0.40 |
|---|---:|---:|---:|---:|---:|
| Nifty | 6 | 1 / 5 | 16.7% | 0.237 | 81 |
| Bank Nifty | 19 | 6 / 7 | 46.2% | 0.272 | 112 |
| Sensex | 23 | 8 / 6 | 57.1% | 0.245 | 151 |
| **All** | **48** | **15 / 18** | **45.5%** | — | — |

By type (resolved):

| Type | n | Hit | Stop | WR |
|---|---:|---:|---:|---:|
| CONTINUATION_BULL | 23 | 8 | 14 | **36.4%** |
| CONTINUATION_BEAR | 9 | 0 | 1 | 0% (mostly unresolved) |
| CVD_DIVERGENCE_BULL | 6 | 3 | 1 | 75% |
| CVD_DIVERGENCE_BEAR | 4 | 0 | 2 | 0% |
| ABSORPTION_BEAR | 3 | 3 | 0 | **100%** |
| EXHAUSTION_DOWN | 3 | 1 | 0 | 100% |

Nifty is no longer “silent” on \|Δratio\| (81 bars ≥0.40 vs 6 on 12-Nov), but
**all 6 prints were MICRO_BURST** and none after 11:09. Confirmed-bar CONT was
muted by an **inflated TOD RVol baseline** (~12k midday vs empirical ~6.6k):
73 CONT-shaped bars after 11:10, **0** cleared real `rvol≥1.5`. BN/SX still
printed because their baselines matched tape. **Do not lower CONT Δ/VWAP/oi
gates** — recalibrate the RVol denominator (empirical asymmetric U-curve,
fit 05–12 Nov, holdout 13).

## Sentinel paper

23 closed · 7 TARGET (+₹9,875) · 16 SPOT_INVAL (−₹5,384) · **net +₹4,491**.
Sensex +₹4,567 / BN +₹1,883 / Nifty −₹1,959. Same shape as yesterday: absorption
and CVD targets carry; CONT_BULL spot-invals drag Nifty.

## GO Desk — FIRE → stage → trail

| Metric | Value |
|---|---|
| Events | 68 (EMIT 58 · STAGE 10) |
| State mix | FIRE 42 · SCOUT 26 |
| Symbols | Nifty 64 · Sensex 4 · BN 0 |
| `go_open` | 10 rows (4 closed TRAIL_STOP · 6 still OPEN) |
| Closed net | **+₹7,830** (985 + 1,012 + 3,230 + 2,604) |
| Open MTM (rough) | **≈ −₹14.3k** at last marks |

Closed trail sample:

| Opened | Key | Net ₹ | MFE | MAE |
|---|---|---:|---:|---:|
| 10:13 | CONTINUATION_BULL | +985 | 58 | 61 |
| 10:14 | ICEBERG_FADE | +1,012 | 54 | 65 |
| 12:32 | RATIO_REVERSAL | +3,230 | 112 | 7 |
| 13:18 | ICEBERG_FADE | +2,604 | 109 | 4 |

Anti-fabrication check on `decision_json`:

- Sentinel-led CONTINUATION_BULL (10:13, 17:50): `targets == {}`.
- Ideas-latched ICEBERG / STRUCTURE / RATIO: live `t1`/`t2`/`sl` present (TE/BS),
  not LTP×1.25/1.50/0.70.

Gap: STAGE at **17:50** (post cash close) opened another sentinel-led CONT_BULL
with empty targets — desk should not auto-stage after the session window.

## Walls / shared gamma

| Index | Dominant pair | Shared-gamma notes |
|---|---|---|
| Nifty | 25,200 / 25,000 (337) | Brief 25,000/25,000 (12 pubs, hour 14) |
| Bank Nifty | **61,000 / 60,500 (501)** | No equal walls today (vs all-day 60,500 pin on 12-Nov) |
| Sensex | 81,500/81,500 (232) + 82,000/81,500 (214) | Shared pin afternoon hours 14–15; close map 81,500/80,000 |

Soft-flag path for equal walls remains correct; BN did not need it today.

## Stability / builds

- Pre-open restarts: **v5.3.127 → 128 → 129 → 130** (08:49–09:11). Midday
  stayed on 130; post-close **131 → 132** (17:50–17:52).
- Admin alerts: readiness false 383 consecutive minutes
  (`no_fresh_chain` / `ws_disconnected` / CB open) — conflicts with live chain
  publishes and Ideas gens; treat as SLO instrumentation debt, not a dark book.
- Afternoon: Dhan rate-gate headroom 0; chain p95 ≈ 35s on all three; intermittent
  circuit-open on SX/BN/NF.
- Gap alert at 15:45 for missing futures/options journals — separate from
  Sentinel 1m tape (which was complete).

## Yesterday’s checklist — verdict

| Check | Result |
|---|---|
| Ideas after first 1m bar (no VWAP refuse) | **Held on ingest 09:15** — serve latch fixed v5.3.136 |
| GO FIRE → `go_open` under live premium | **Proven** — 10 staged; trails paid |
| BN shared-gamma soft flag / Nifty OF quiet | BN not equal today; Nifty still scarce prints |
| No fabricated GO T1/T2/SL on sentinel-led | **Held** — empty targets |
| No Ideas LTP×ratio / writer lot×2000 margin | **Held** (shipped v5.3.114+) |

## Next open checks

1. ~~Ideas by ~09:20~~ — mint was already on; **serve latch** fixed via `latest_rec_*.pkl`.
2. ~~No GO STAGE / `go_open` after cash close~~ — gated (session_closed / 400).
3. ~~CONT_BULL / MICRO_BURST~~ — unconfirmed CONT → **SCOUT** (not FIRE+HALF);
   MICRO_BURST score **65** + hard block on Sentinel paper; FIRE only with
   same-side ABSORPTION / CVD_DIVERGENCE on a confirmed bar.
4. ~~Readiness SLO false red~~ — **v5.3.133**; confirm ages under EXTERNAL after ~09:16.
5. ~~Sensex equal walls~~ — `shared_gamma_strike` + VANNA corridor skip covered for
   step=100 (same as BN); regression `test_shared_gamma_strike_soft_flag_sensex_equal_walls`.
6. ~~Incomplete-journals GAP vs complete Sentinel~~ — **v5.3.135** REPAIR INFO;
   legacy backfill-only GAP rows reclassified on alert refresh (**v5.3.137+**).

### Operator checklist (first 30 min)

1. `/api/health` version matches promoted tag (expect ≥ **v5.3.142** after promote).
2. Readiness: finite `probes.chains.*.age_s`; `feed.source=ingest_heartbeat`.
3. After ~09:16: Ideas UI / GO latch not empty while `latest_rec_NIFTY.pkl` is fresh.
4. On FIRE with Ideas present: latch should prefer Ideas (not only sentinel-led).
5. No STAGE after F&O close; CONT alone / MICRO_BURST → SCOUT until ABS/CVD on confirmed bar.

## Disclaimer

Decision-support only — not financial advice. Human confirms all orders.
