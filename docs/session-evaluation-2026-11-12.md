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

## Next open checks

1. Ideas publish after first 1m bar (no continuous `rec.refused` VWAP).
2. At least one GO FIRE → `go_open` under trend.
3. Nifty OF volume vs Sensex flood.
