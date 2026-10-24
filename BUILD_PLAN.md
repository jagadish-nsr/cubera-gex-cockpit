# Nifty GEX Engine — Build Plan

**Status:** Spec for Claude Code to implement
**Underlying:** NIFTY index options (NSE). Bank Nifty / FinNifty / stocks = later.
**Mode:** Signal + suggestions (human confirm). NO auto-execution.
**Lineage:** Historical note. An early draft was copied from an SPX tree.
Production is the Nifty book-balance engine in this repository. Do not
reintroduce an SPX multiplier, a second sign model, or that draft's playbook.
`docs/nifty-nse-adapter-scope.md` is the retired scope for that draft.

> Decision-support, not financial advice. Read `CLAUDE.md` first — especially the
> dealer-sign landmine.

---

## 0. What this system does
1. Pulls the live Nifty option chain (OI + IV/price) from an NSE source.
2. **Computes GEX itself** — call wall, put wall, gamma flip (zero-gamma),
   magnet, max pain — recomputing BS gamma from IV (never trusting a vendor's
   static gamma).
3. Classifies regime (positive = pin/fade, negative = trend/amplify) and prints
   the one-sentence playbook.
4. (Later) maps regime + levels into trade *suggestions* with defined risk.

## 1. What's already done (inherited, validated by fixtures)
- `gex/` math: per-strike GEX, walls, gamma-flip zero-crossing, max pain.
- `data/parity.py`: implies r/q **and spot** from the chain via put-call parity.
- `data/schema.py`: common `OptionsSnapshot`.
- `regime/`: regime classification + wall-strength-vs-flip + playbook headline.
- 20 passing tests (incl. hand-checkable fixtures + an NSE adapter/expiry test).

## 2. Build phases

### Phase 0 — Resolve the NSE data source (GATING, verify live)
Pick ONE and confirm coverage + cost + rate limits **live** (don't guess):
- Official NSE option-chain endpoint (free, fragile, bot-gated) — OK for PoC.
- Broker API (Kite Connect / Upstox / Fyers / Angel / Dhan) — paid, stable.
- Data vendor (TrueData / GlobalDatafeeds / …).
Confirm: does it give OI + IV (or price to derive IV) + the Nifty spot?

### Phase 1 — NSE adapter + level table (smallest slice)
- Implement `data/nse.py: fetch_chain` + finish `from_nse_results` mapping for the
  chosen source. Smoke test: pull one chain, report field-population, save raw.
- **Verify live:** current **Nifty lot size** (`config.NIFTY_LOT_SIZE`) and the
  **NSE expiry rules** (`data/expiry.py` weekly day + monthly last-Thursday;
  add the holiday roll-back using the NSE holiday calendar).
- Print the level table + playbook (a `cli.py` like the SPX one).
- **Acceptance:** validate walls/flip vs an independent Indian GEX/OI read within
  tolerance; explain divergences (expiry window, lot size).
- **Dealer-sign:** test both conventions; choose empirically (do NOT assume US).

### Phase 2 — Regime + alerts
- Regime + wall-strength-vs-flip already coded; add an NSE-session scheduler
  (09:15–15:30 IST, pre-open + intraday refresh) and alert delivery — only AFTER
  the numbers validate.

### Phase 3 — Strategy + risk gate
- Sell-premium-in-positive / buy-in-negative playbooks → suggestions with defined
  stop + max-loss. Margin/size gate. Macro-bias conflict de-sizing.

### Phase 4 — Order staging (human-in-loop)
- Broker order formatting + confirm flow (paper/dry-run first). No auto-send.

### Phase 5 — Backtest
- Point-in-time-correct replay through the SAME `gex/` + `regime/` code (see the
  `backtesting` skill). Source historical Nifty chains.

## 3. Open items to resolve at build time (verify, don't guess)
1. NSE data source + pricing + reliability (Phase 0).
2. Current **Nifty lot size** (NSE circular).
3. Current **weekly/monthly expiry rules** (2024–25 SEBI/NSE changes) + holiday
   roll-back calendar.
4. **Dealer-sign model** validity for India — empirical, before trusting output.

## 4. Guardrails (every phase)
- No auto-execution. GEX = probability map; news overrides → de-size on conflict.
- Stops beyond walls, never at the magnet. OI staleness + dealer-sign + lot size
  surfaced on every reading.
- Verify-live the NSE unknowns; never bury a guess.
