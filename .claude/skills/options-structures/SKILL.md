---
name: options-structures
description: Map GEX regime to the right option structure and strike selection. Use when implementing the structures/ module or generating trade suggestions.
---

# Regime -> structure mapping

One-line principle: SELL premium in positive gamma, BUY premium in negative gamma. Walls pick the strikes, flip picks the side.

## Positive gamma -> sell premium, defined risk
- Iron condor / credit spread. Short strikes AT the walls:
  - short call spread at/above call wall
  - short put spread at/below put wall
- Iron fly / short straddle near magnet on expiry-pin days (respect no-hold-to-expiry).
- Short strike goes ABOVE the wall, not AT the magnet (magnet gets tagged on noise).

## Negative gamma (price holds below flip) -> buy premium
- Debit spread / long option in the DIRECTION of the break.
- REDUCE size: moves run 1.5-2x. Require a CONFIRMED hold below flip, not an intraday wick.
- Never sell premium here (short gamma into amplification).

## Hard constraints on every suggestion
- Saxo: no hold-to-expiry. Set a forced exit-by date BEFORE expiry. Flag DTE.
- Defined max-loss in $ shown before the suggestion surfaces.
- Margin gate (<40% to add) checked first — veto if breached.
- XSP execution: SPX-derived wall / 10 -> XSP strike. Round to listed XSP strikes; flag if nearest listed strike is >1pt off computed wall (thin-OI risk).
- Macro/thesis conflict with regime -> DE-SIZE, do not blindly fade a thesis-aligned move.

## Golden fixtures to satisfy (see BUILD_PLAN.md section 7)
- Fixture A: positive gamma + bearish -> call credit spread short ABOVE call wall (NOT a long put).
- Fixture B: negative gamma + break held below flip -> put debit spread, reduced size.
- Fixture C: conflict/no-edge -> emit no add; surface de-size if naked position >40% margin.