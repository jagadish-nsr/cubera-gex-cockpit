# Scope — Simple Nifty GEX Dashboard + Paper-Trade Journal

**Status:** Scoping only (no code yet). A future phase of `gex-indian`.
**User:** Naveen's wife — a Nifty options trader, **non-coder**. The app must be
openable and usable with zero terminal/coding.

> Decision-support, **not advice**. **Paper only** — no real money, no broker, no
> order execution. And a standing banner: the **Nifty dealer-sign model is
> UNVALIDATED** (the regime could be inverted), so everything is for *practice and
> learning* until the signal is validated (see `nifty-nse-adapter-scope.md` §6).

---

## 1. Goal
A simple **local Streamlit web app** with three screens:
1. **Dashboard** — today's Nifty levels + one-sentence playbook.
2. **Candidate structures** — illustrative textbook trades off the walls (marked
   UNVALIDATED), with a one-click "paper-trade this."
3. **Journal + scorecard** — log paper trades, track mark-to-market + realised
   P&L (net of costs), and see honest descriptive stats.

The framing for her: **the app is a map and a logbook, not an oracle or an
autopilot.** It measures and remembers; the judgement is hers.

Decided: **local Streamlit** (runs on her Windows machine — required anyway since
NSE blocks datacenter/cloud IPs; a one-click `.bat` launches it in her browser).
Decided: **show illustrative candidate structures**, loudly gated.

---

## 2. Screens

### 2a. Dashboard (Today)
- A **Refresh** button → pull the NSE chain (`data/nse.fetch_chain`) → `compute_levels`.
- Show: **spot**, the **5 levels** (call wall, put wall, gamma flip, magnet, max
  pain), **regime**, and the **one-sentence playbook** (`regime.classify`).
- A simple price-ladder visual: spot with the walls/flip drawn as horizontal lines.
- **Caveats panel (always visible):** dealer-sign UNVALIDATED, lot size (VERIFY),
  **OI timestamp + staleness**, "decision-support, not advice."
- **Graceful failure:** if NSE throttles, show the last good snapshot + its time
  and a clear "refresh failed — showing data from HH:MM" (a non-coder can't debug).

### 2b. Candidate structures (illustrative)
- From levels + regime, apply the `options-structures` skill rules → 1–2 textbook
  structures with **concrete strikes off the walls**, a **forced exit-by date**
  (no hold-to-expiry), and estimated **max-loss / credit**.
  - Positive gamma → sell premium (call/put credit spread or condor at the walls).
  - Negative gamma (held below flip) → buy premium (debit spread, reduced size).
  - Regime/flip conflict → **no add / de-size** (show the warning, not a trade).
- **Marked loudly: "ILLUSTRATIVE — UNVALIDATED SIGNAL — paper only."**
- "Paper-trade this" → pre-fills a journal entry (she can edit before saving).

### 2c. Journal + scorecard
- **Log a paper trade:** structure, legs/strikes, entry premium (auto-filled from
  the chain LTP, editable), entry date/time, **a snapshot of the levels+regime at
  entry**, forced exit-by date.
- **Open positions:** mark-to-market using the current chain's LTP for the same
  strikes (clearly "approx, last refresh HH:MM").
- **Close:** realised P&L = exit premium − entry premium, **net of configurable
  costs** (brokerage + slippage per leg — no fantasy fills).
- **Scorecard (descriptive only):** win rate, total paper P&L, breakdown by
  **regime** and by **structure**, a simple equity curve. **No auto-tuning** — this
  feeds Naveen's *offline, out-of-sample* research, never an in-app optimiser.

---

## 3. New modules (thin layer over the existing engine)
- `structures/suggest.py` — deterministic `suggest(levels, view) -> [CandidateStructure]`
  (legs, strikes off walls, exit-by date, max-loss/credit, rationale, `unvalidated=True`).
  Unit-tested against the options-structures **golden fixtures** (positive+bearish →
  call credit spread ABOVE the wall, not a long put; negative+held-below-flip → put
  debit spread reduced size; conflict → no add).
- `journal/store.py` — **SQLite** (a local file; invisible to her) for paper trades
  + entry-context snapshot + status.
- `journal/pnl.py` — mark-to-market + realised P&L, net of costs; point-in-time honest.
- `journal/scorecard.py` — aggregate stats (descriptive).
- `app/` — Streamlit multipage app (Dashboard / Structures / Journal) + `run_app.bat`
  one-click launcher. Add `streamlit` to `requirements.txt`.
- Reused unchanged: `gex/`, `regime/`, `data/nse.py`, `data/parity.py`, `config.py`.

---

## 4. Point-in-time honesty (from the `backtesting` skill — non-negotiable)
- **Snapshot levels/regime AT ENTRY** into the journal row, so the scorecard
  reflects what was knowable then — never re-label a past trade with today's GEX.
- Mark-to-market uses only the current chain; realised P&L uses the logged exit
  premium. No lookahead.
- **Costs are real:** brokerage + slippage per leg, configurable, subtracted. A GEX
  edge that dies after costs is not an edge.
- OI is EOD-lagging — surfaced on every reading.

## 5. Safety / guardrails (every screen)
- **Paper only.** No broker, no orders, no real money — a permanent banner.
- **Dealer-sign UNVALIDATED** banner; candidate structures double-marked illustrative.
- **No auto-execution, no auto-parameter-tuning** (research is offline/human,
  out-of-sample).
- Every structure carries a **forced exit-by date** (no hold-to-expiry).
- Graceful handling when NSE throttles.

## 6. Phasing (smallest thing first)
- **v1a** — Dashboard (levels + playbook + caveats + graceful refresh). Useful and
  safe immediately, even before validation, behind the UNVALIDATED banner.
- **v1b** — Candidate-structures panel.
- **v1c** — Journal + mark-to-market + scorecard.
- **Trust gate:** structures stay "illustrative" until the signal is validated
  (dealer-sign resolved + market-hours data + a point-in-time backtest beating
  random-entry after costs). The app ships and is *usable for paper practice*
  before then; it just won't claim the suggestions are proven.

## 7. Open items (verify, don't guess)
- **Lot size** + **dealer-sign model** (shared with the engine's validation track).
- **NSE refresh reliability** from her machine — if it throttles often, consider a
  broker API (Kite/Upstox/…) for stability later.
- **Cost assumptions** — use her actual broker's brokerage + realistic slippage.
- **Mark-to-market accuracy** on illiquid strikes (flag low-volume legs).

## 8. Non-goals (v1)
Auto-execution; broker integration; auto-parameter-tuning; real money; hosting;
multi-underlying (Nifty only); an in-app historical backtester (that's Naveen's
separate research track).
