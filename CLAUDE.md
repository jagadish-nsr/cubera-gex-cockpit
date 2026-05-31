# CLAUDE.md

Project context and operating rules for Claude Code. Read this before any task.

## What this project is
The **Nifty (NSE)** port of the GEX engine. Computes dealer-gamma levels (call/put
walls, gamma flip, magnet, max pain), classifies regime, and emits a one-sentence
playbook + (later) trade *suggestions* for **Nifty index options**. Human confirms
— NO auto-execution.

The pure engine (`gex/`, `regime/`, `data/schema.py`, `data/parity.py`) was copied
from the SPX project (`../GEX`) and is **market-agnostic** — it is reused unchanged.
Only the **data layer** (`data/nse.py`), **lot size / expiry calendar**, and the
**dealer-sign model** are India-specific. See `docs/nifty-nse-adapter-scope.md`.

**This software influences real-money trading decisions.** A silent bug (wrong
gamma sign, a stale OI read presented as live, a wrong lot size) can cause a real
loss. Correctness and transparency beat features and cleverness, every time.

## ⚠️ The India-specific landmine (read first)
The **dealer-sign convention** (long call gamma / short put gamma, `DEALER_SIGN_MODEL`)
is a **US-index assumption**. India's heavy retail-option-SELLING microstructure
may invert or distort it. **Do NOT trust any Nifty wall/flip until this is
re-validated empirically** (test both conventions vs realised Nifty behaviour and
a third-party Indian read). Treat it as UNKNOWN, not as the US default.

## Core engineering principles (Karpathy-flavoured)
1. **Smallest thing that works end-to-end first.** One source, Nifty index only,
   hardcoded-but-named constants. Pull chain → compute walls → print table +
   playbook before any strategy code.
2. **Resist premature abstraction.** No multi-broker layer, no Bank Nifty/FinNifty,
   no config framework until a second real case forces it.
3. **Be suspicious of your own code. Verify empirically.** Every quant number must
   be reproducible by hand on a small example (the fixture tests are inherited).
4. **Make state visible.** Log per-strike GEX, the flip profile, OI timestamp,
   lot size, dealer-sign model, and the parity-implied r/q on every output.
5. **No silent assumptions.** Dealer sign, expiry inclusion, **lot size**, and the
   **NSE expiry calendar** are explicit params, logged — never hidden.
6. **Fail loud, not silent.** Stale OI, missing greeks, a flip that never crosses
   zero, an implausible parity rate → flag/raise, don't paper over.
7. **Tune toward the loss that matters:** real P&L survival.

## Hard rules (never violate)
- **No auto-execution.** Every order staged, explicit user confirm.
- **Verify-live, never guess** the three NSE unknowns: data source + pricing,
  **current Nifty lot size**, and the **current weekly/monthly expiry rules**
  (SEBI/NSE changed these in 2024–25). These are marked VERIFY in `config.py` /
  `data/expiry.py`.
- **Dealer-sign model is UNVALIDATED for India** — see the landmine above.
- **Stops beyond walls, never at the magnet.**
- **Point-in-time correctness in backtests** (label each bar with the regime as
  it stood at that bar's open; feature at time t uses only data before t).
- **GEX is a probability map, not a predictor.** Headlines override; de-size on
  regime/thesis conflict.

## What's reused unchanged from the SPX project
`gex/bs.py, core.py, flip.py, maxpain.py, levels.py`; `regime/classify.py,
playbook.py`; `data/schema.py, parity.py`; the synthetic fixture tests. If you fix
a bug in these, consider porting it to/from `../GEX` (the two copies can diverge —
a shared core package is a future option).

## Workflow expectations
- Small, reviewable commits; one logical change each.
- Write the hand-checkable test alongside the code.
- When a build-time unknown appears (NSE pricing, lot size, expiry rule), SURFACE
  it — don't guess and bury the guess.
- Finish a phase → stop and summarise built / validated / assumed.

## Disclaimer
Decision-support tooling, not financial advice. The user confirms all decisions.
