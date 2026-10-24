# CLAUDE.md

Project context and operating rules. Read this before any task.

## What this project is
Decision support for **Nifty, Bank Nifty, and Sensex** index options. It computes
gamma concentration (call wall, put wall, magnet), the call−put balance (gamma
flip and the signed total), and max pain, then a one-sentence book-balance line.
Human confirms. **No auto-execution.**

**This software influences real-money trading decisions.** A silent bug (a stale
OI read presented as live, a wrong lot size, a playbook that calls a ratio a
pin) can cause a real loss. Correctness and transparency beat features.

## One GEX convention
Production code has one convention, `short_call_short_put`:

- Both legs are stored negative.
- Walls are the strikes with the largest call-gamma and put-gamma magnitudes.
- The flip and the regime total are **call magnitude minus put magnitude**.
- POSITIVE means call-heavy. NEGATIVE means put-heavy. Those words are not a
  forecast that price will fade or run.
- `contract_size` is the index lot from `config.INDICES`. Current lots and
  expiries, user-confirmed 2026-10-24:
  **Nifty 65, every Tuesday. Bank Nifty 30, last Tuesday of the month.
  Sensex 20, every Thursday.** If that day is an exchange holiday, the expiry
  is the previous trading day. Do not pass 100, and do not revive Nifty 75,
  Bank Nifty 35, or Sensex 10. `"NIFTY" in "BANKNIFTY"` is true, so never
  choose a lot or an expiry weekday with a substring check.
- Absolute GEX rank is the volatility context (higher concentration, calmer
  forward vol in the research sample). The sign is not a direction call.

Do not add a second sign model, a US pin playbook, or an SPX contract multiplier.
Historical notes under `docs/` that compare an old long-call/short-put reading
are research records. They are not instructions for the engine.

Who actually delta-hedges on NSE is still unmeasured. Open interest is not
buyer-versus-seller flow. Do not write "dealers will defend this wall."

Files under `docs/` and `scripts/analyze_*.py` that mention
`long_call_short_put`, model A or model B, an SPX parent, or flipping
`DEALER_SIGN_MODEL` are records of a retired comparison. Do not copy their
playbook into production. An unmeasured hedge is not a reason to restore a
US pin reading.

## Core engineering principles
1. **Smallest thing that works end-to-end first.** Nifty index options. Named
   constants. Pull chain → compute walls → print the book-balance line.
2. **Resist premature abstraction.** No multi-broker layer until a second live
   case forces it.
3. **Be suspicious of your own code. Verify empirically.** Every quant number
   must be reproducible by hand on a small example.
4. **Make state visible.** Log per-strike GEX, the flip profile, OI timestamp,
   lot size, the sign convention, and the parity-implied carry on every output.
5. **No silent assumptions.** Lot size, expiry inclusion, and the NSE calendar
   are explicit params, logged.
6. **Fail loud, not silent.** Stale OI, missing greeks, a flip that never
   crosses zero, an implausible parity rate, a second sign model → flag/raise.
7. **Tune toward the loss that matters:** real P&L survival.

## Hard rules
- **No auto-execution.** Every order staged, explicit user confirm.
- **Verify-live, never guess** the NSE lot size and the weekly/monthly expiry
  rules. Marked VERIFY in `config.py` / `data/expiry.py`.
- **Stops beyond the gamma strikes, never at the magnet.**
- **Point-in-time correctness in backtests** (the label at a bar uses only data
  available at that bar).
- **GEX is a positioning map, not a predictor.** Headlines override. De-size
  when the aggregate book and the local side of the flip disagree.

## Workflow
- Small, reviewable commits. One logical change each.
- Write the hand-checkable test alongside the code.
- When a build-time unknown appears (pricing, lot size, expiry rule), surface it.
- Finish a phase, then stop and summarise what is built, what was checked, and
  what is still assumed.

## Disclaimer
Decision-support tooling, not financial advice. The user confirms all decisions.
