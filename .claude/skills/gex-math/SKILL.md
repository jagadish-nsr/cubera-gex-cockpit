---
name: gex-math
description: Compute gamma exposure (GEX), walls, gamma flip, and max pain from an options chain. Use whenever implementing or modifying GEX calculations.
---

# GEX math

## Per-strike dollar GEX (SpotGamma convention)
- call_gex[k] = gamma[k] * OI[k] * 100 * spot^2 * 0.01
- put_gex[k]  = -1 * gamma[k] * OI[k] * 100 * spot^2 * 0.01
- net_gex[k]  = call_gex[k] + put_gex[k]
- total_net_gex = sum over chosen strikes & expiries

Contract size = 100 (SPX). The spot^2 * 0.01 term scales to $ hedging per 1% move.

## Derived levels
- Call wall  = strike with max positive call GEX (resistance).
- Put wall   = strike with max absolute put GEX (support if above flip; speed bump if below).
- Peak GEX / magnet = strike with max absolute net GEX.
- Max pain   = strike minimising total ITM payout to holders at expiry (separate calc over OI).

## Gamma flip (zero-gamma) — NOT a static strike
Build a profile, do not read a single number:
1. Recompute every option's BS gamma across a range of hypothetical spots (e.g. spot * [0.90 .. 1.10] in fine steps).
2. Sum total GEX at each hypothetical spot.
3. The flip = price where the summed-GEX curve crosses zero.
If the curve never crosses zero in range, FLAG it — do not return a fabricated level.

## Regime
- total_net_gex > 0 -> POSITIVE: dealers counter-trend, pin/fade, suppressed vol.
- total_net_gex < 0 -> NEGATIVE: dealers pro-cyclical, momentum, ~1.5-2x expected move.
- Always judge wall strength RELATIVE to the flip.

## Non-negotiables
- DEALER_SIGN_MODEL (default: long call gamma / short put gamma) is an explicit param, logged on every output.
- Expiry window is an explicit param (0DTE / weekly / monthly / all) — it materially changes results.
- Every output carries the OI timestamp. OI is end-of-day lagging; never imply it's live intraday.
- Use vectorised BS gamma (numpy/scipy). Recompute gamma for the flip profile — do not reuse the vendor's static gamma.
- MUST have a hand-checkable unit test: compute walls on ~5 strikes verifiable with a calculator.
- Validate computed walls against a live third-party read (FlashAlpha free tier / InsiderFinance) within tolerance; document any divergence and its cause (usually expiry window).