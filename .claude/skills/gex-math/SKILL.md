---
name: gex-math
description: Compute gamma exposure (GEX), walls, gamma flip, and max pain from an NSE index option chain. Use whenever implementing or modifying GEX calculations.
---

# GEX math (Nifty book balance)

One convention: `short_call_short_put`. There is no second sign model.

## Per-strike rupee GEX
- magnitude[k] = gamma[k] * OI_lots[k] * lot_size * spot^2 * 0.01
- call_gex[k] = -magnitude on calls
- put_gex[k] = -magnitude on puts
- stored net_gex[k] = call_gex + put_gex (always ≤ 0; do not classify from this sum)
- book balance = call magnitude − put magnitude

`lot_size` is `Index.lot_size` from `config.INDICES`: Nifty 65 (every Tuesday), Bank Nifty 30 (last Tuesday of the month), Sensex 20 (every Thursday). A holiday moves that expiry to the previous trading day. OI must already be in lots.
`spot^2 * 0.01` is the rupee hedge for a 1% move. Do not use 100 as the multiplier.

## Derived levels
- Call wall = strike with the largest call-gamma magnitude.
- Put wall = strike with the largest put-gamma magnitude.
- Magnet = strike with the largest absolute stored net GEX (combined gamma).
- Max pain = strike minimising total ITM payout to holders at expiry. The lot
  scales the rupee total and does not move the strike.

## Gamma flip
Not a static strike. Recompute BS gamma on a spot grid (spot * [0.90 .. 1.10]).
Sum call magnitude minus put magnitude at each hypothetical spot. The flip is
where that curve crosses zero. No crossing in range → flag it. Do not invent a level.

The flip is the price where the two books balance. It is not a long-gamma pin.

## Regime
- book balance > 0 → POSITIVE, call-heavy book.
- book balance < 0 → NEGATIVE, put-heavy book.
- Do not map that sign to "fade" or "momentum".
- Calm versus wild is absolute GEX rank, a separate number.

## Non-negotiables
- Reject any sign model other than `short_call_short_put`.
- Expiry window is an explicit param (weekly / monthly / all). It changes the levels.
- Every output carries the OI timestamp. OI lags. Do not present it as live flow.
- Recompute gamma for the flip profile. Do not reuse a vendor gamma across hypothetical spots.
- Hand-checkable unit test on about 5 strikes (`tests/test_fixture_gex.py`).
- Walls are concentration. They are not floors, ceilings, or breakout triggers.
