---
name: backtesting
description: Point-in-time-correct backtesting of GEX strategies. Use whenever building or modifying the backtest harness or any historical evaluation.
---

# Backtesting — avoid lookahead at all costs

The #1 way this project produces a fake edge is lookahead bias. Guard against it obsessively.

## Rules
- Replay historical snapshots through the SAME gex/ and strategy/ code as live. No separate backtest-only logic.
- Label each bar with the regime as it stood at that bar's OPEN. Net GEX flips intraday — using close-of-day GEX to label a 10:30 entry measures a feature that did not exist yet.
- Any feature at time t uses ONLY data strictly before t. Percentiles, rolling stats, regime tags — all point-in-time.
- OI is end-of-day. A backtest entry intraday must use the PRIOR session's OI, not today's close. Model the real latency you'll have live.
- Account for costs: spread, commission, slippage. A GEX edge that dies after costs is not an edge.
- Out-of-sample discipline: tune on one period, validate on an untouched period. Report both.

## Sanity checks before trusting any result
- Does equity curve survive a random-entry control with the same sizing? If random does as well, the "edge" is sizing/vol-targeting, not the signal.
- Shuffle the signal labels — performance should collapse. If it doesn't, there's leakage.
- Inspect the best and worst trades by hand. Do they make mechanical sense?

## Karpathy check
Build the dumbest possible backtest first (single setup, one underlying, printed trade log you can read line by line) before any vectorised/optimised version. Verify a handful of trades by hand against the raw data.