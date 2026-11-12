# Session Evaluation — Thursday, 12-Nov-2026

Canvas: `session-eval-2026-11-12` (IDE canvases folder).

## Scorecard

| Dimension | Rating | Notes |
|---|:---:|---|
| Macro | **Bull trend** | Nifty-FUT +115, BN-FUT +636, SX-FUT +369 |
| Tape completeness | **Excellent** | 384 / 384 / 380 1m bars; live OI on all three |
| Trade Ideas | **Offline** | 0 setups; ~6.8k `Missing session VWAP` refuses |
| Sentinel OF | **Mixed (45.8% WR)** | 92 signals; Sensex-heavy; ABS_BEAR best |
| Sentinel paper | **+₹11,806** | 13 TARGET / 13 SPOT_INVAL |
| GO Desk | **SCOUT only** | 16 EMIT; 0 FIRE / go_open |
| Stability | **Good after open** | Pre-open promotes v5.3.102–106; no mid-session 503 burst |

## Root cause — Trade Ideas blackout

Harden (v5.3.102+) correctly refuses ideas without live VWAP σ. The rec publisher called `generate_trade_recommendations(dash)` **without** `latest_bar` / Sentinel bars, so VWAP was always missing → all-day empty Ideas.

**Fix (in tree):** publisher now loads `list_recent_bars` + `list_recent_signals` and passes them into generation.

## GO Desk

Writer SCOUT `WRITE_OTM` @ 73.8 (Nifty) and directional SCOUT absorption/continuation (SX/BN) emitted, but nothing staged to `go_open`. No GO trail/MFE sample today.

## Follow-ups shipped after close

1. **v5.3.107** — Ideas publisher passes Sentinel `latest_bar` (VWAP gate).
2. **Sentinel-led GO** — when Ideas latch is empty, fold Trade Ideas weight into
   primary Sentinel score and fill ATM `chain_ltp` so FIRE can auto-stage
   (12 Nov SCOUT ceiling was Ideas blackout + unfilled premium).
3. **Anti-fabrication** — strip sentinel-led LTP×ratio T1/T2/SL; no invented TE
   60/40 confluence; CVD/VWAP from tape/bars only.
4. **Shared gamma** — soft-flag equal walls; skip VANNA corridor Ideas; writer
   distance_beyond fails on collapsed walls (BN 60500/60500 was authentic).
5. **GO FIRE path** — missing TE/breadth/tape no longer invents fillers; unused
   weight folds into primary so sentinel-led + live premium can clear FIRE →
   auto-stage. Health version tracks git tag.

## Diagnosis — Nifty OF scarcity (authentic)

| Index | 1m bars | mean \|Δratio\| | CONT-gate candidates (crude) | Signals |
|---|---:|---:|---:|---:|
| Nifty | 384 | 0.15 | 15 | **4** (CVD only) |
| Bank Nifty | 384 | 0.16 | 29 | 18 |
| Sensex | 380 | 0.24 | 78 | **70** |

Sensex’s thin book prints extreme \|Δratio\| (≥0.40 on 114 bars vs Nifty’s 6).
Nifty silence is microstructure + CONT gates (`oi_fresh`, TOD RVol, VWAP,
close position) — **not** a missing feed. Do not lower CONT thresholds to
fabricate Nifty prints. Keep day-extreme / oi_fresh gates (CONT_BULL quality).

## Diagnosis — BN equal walls (authentic)

All 699 `chain.published` rows on 12-Nov carried `call_wall = put_wall = 60500`
with spot ~61k. Session-close snapshot matched. Gamma blast already badges this
as `GAMMA PIN`. GO now soft-flags `shared_gamma_strike`; VANNA-CHARM Ideas that
need corridor width are skipped. **Do not invent a second wall.**

## Next open checks

1. Ideas publish after first 1m bar (no continuous `rec.refused` VWAP).
2. At least one GO FIRE → `go_open` under trend (sentinel-led or Ideas latch; no fabricated T1/T2/SL).
3. BN shared-gamma soft flag visible when walls collapse; Nifty OF stays quiet unless tape earns it.
