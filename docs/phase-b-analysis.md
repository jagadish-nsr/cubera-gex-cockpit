# Phase B — Analysing the intraday time-series

**Status:** Spec, not yet built. Runs on the Phase A captures
(`data/journal/timeseries/YYYY-MM-DD.csv`). Read `CLAUDE.md` first — especially
the dealer-sign landmine and "GEX is a probability map, not a predictor".

Phase A captures intraday GEX levels + OI/volume build-up + **India VIX** every
~15 min. Phase B turns that into two things:

1. **Validation** — settle, empirically, whether the `long_call_short_put`
   dealer-sign convention is correct *for Nifty* (or inverted).
2. **Description** — measure how structure (levels, magnet, walls, flip) moves
   through the day and whether any of it carries usable information.

Until the sign is confirmed, all trade suggestions stay **ILLUSTRATIVE / paper**.

---

## 0. The core idea

We cannot observe dealer positioning directly. But the dealer-sign model makes
**falsifiable predictions about realised price behaviour**, and a sign flip would
*invert* those predictions. So Phase B tests: *does the behaviour that actually
happens match the regime label our sign assumption assigns?*

Mechanism recap (see the dealer-hedging explanation): long-gamma (our "POSITIVE")
hedging is **stabilising** → low vol, mean-reversion, pinning. Short-gamma (our
"NEGATIVE") hedging is **amplifying** → high vol, momentum, walls break. If the
sign is inverted, the labels are swapped and these predictions come out backwards.

---

## 1. Experimental design — run BOTH sign conventions in parallel

For every snapshot, compute the levels/regime under **both**
`long_call_short_put` **and its inverse**. This is cheap and is the heart of the
test: a sign flip swaps the regime label, so we get two competing labellings of
each moment and **let the data pick the winner** rather than committing first.

> Note: the *walls* (per-side OI/gamma concentration) may sit at similar strikes
> under either sign; what flips is the **sign of net-GEX, the regime label, and
> the flip location / interpretation**. The test is about interpretation, not
> wall location.

---

## 2. The three core analyses

### A. Realised volatility by regime  *(validation)*
Per regime label, measure realised intraday vol (std of 15-min log returns, or
mean high–low range per snapshot interval).
- **Prediction if our sign is right:** NEGATIVE-labelled periods show **higher**
  realised vol than POSITIVE-labelled.
- **Inverted if:** our "POSITIVE" periods are systematically the *more volatile*
  ones.
- **India VIX cross-check (independent anchor):** India VIX is implied vol computed
  *without* our dealer-sign assumption. If NEGATIVE-labelled periods coincide with
  elevated/rising VIX and POSITIVE with low/falling VIX, that corroborates the sign
  using a yardstick that doesn't beg the question. (Also: VIX vs realised vol = the
  vol-risk premium; and low VIX + negative gamma = fragile complacency.)

### B. Return autocorrelation by regime  *(validation — the sharpest test)*
Lag-1 (and lag-k) autocorrelation of 15-min returns, conditioned on regime. This
maps **directly** onto the hedging mechanism and needs no "walls":
- Amplifying (true NEGATIVE) → **positive** autocorrelation (momentum).
- Stabilising (true POSITIVE) → **negative** autocorrelation (mean-reversion).
- **Verdict:** if NEGATIVE-labelled periods show positive autocorrelation and
  POSITIVE-labelled show negative → **sign CONFIRMED**. Systematically reversed →
  **sign INVERTED** → set `config.DEALER_SIGN_MODEL` to the inverse and re-run.

### C. Δmagnet → Δspot lead–lag  *(the "does the magnet predict trend" hypothesis)*
Tests whether magnet *migration* leads price, **beyond** what price's own recent
move already explains.

⚠️ **Endogeneity trap:** the magnet is recomputed at the current spot, so it can
move *because* spot moved — a magnet that "follows" price is often just an **echo**,
not a predictor. So the test is a lead–lag question, point-in-time strict:

> Does Δmagnet over [t−1, t] predict Δspot over [t, t+k], controlling for Δspot
> over [t−1, t]? (Partial correlation / simple regression with the lagged spot
> move as a control.)

- Genuine lead (magnet reflects fresh OI that front-runs price) → real signal.
- Only coincident/lagging → echo, discard.
- **Prior:** likely mostly an echo — but it's clean and falsifiable, so measure it.

Also remember: **regime predicts *character* (calm vs wild), not *direction*.**
Direction in a negative regime is mostly set by the exogenous catalyst; GEX
amplifies it. So expect magnitude/persistence signal, not a direction forecast.

---

## 3. Secondary / descriptive analyses
- **Pin vs break:** in POSITIVE regime, does spot stay within the walls / converge
  to magnet into the close? In NEGATIVE, does it break out? (Supersedes the sparse
  twice-daily `score_sessions.py` test — see VALIDATION.md.)
- **Wall as barrier:** does price reject at the call/put wall more than at random
  nearby strikes?
- **Flip as pivot:** does crossing the gamma flip precede a change in vol character
  / a directional break?
- **OI / volume build-up:** does put-OI building (support forming) / call-OI
  building (resistance forming) lead level migration or price? (Same echo caveat.)

---

## 4. Methodology discipline (non-negotiable)
- **Point-in-time correct.** Any feature at time *t* uses only data with timestamp
  ≤ *t*. No shuffling; walk forward; out-of-sample **by day**.
- **Baseline first.** Any "predictive" claim must beat a dumb baseline (e.g.
  "always predict pin / no move" or "tomorrow = today") out-of-sample, or it's
  noise.
- **Hand-checkable.** Each statistic reproducible on a small slice (CLAUDE.md #3).
- **Sample size honesty.** State n; don't read a verdict off a handful of days.
  Realised-vol/autocorrelation need many in-session intervals; pin/break needs
  ~5–10+ days per regime.
- **Data hygiene.** Drop/flag stale rows (OI staleness flag), holiday sessions,
  and snapshots outside 09:15–15:30 IST (the Phase A `--once` guard already filters
  most of these).

---

## 5. Deliverable
`scripts/analyze_timeseries.py` (offline, pure-function core like
`score_sessions.py`):
- Load N days of `data/journal/timeseries/*.csv`.
- Recompute regime under both sign conventions per snapshot (or store both in
  Phase A capture — TBD).
- Emit: realised-vol-by-regime, autocorrelation-by-regime, the Δmagnet→Δspot
  lead-lag, and the pin/break summary — with n and a plain-language verdict on the
  dealer sign.
- Unit-tested on synthetic series with a known answer.

**When the sign verdict reads clearly:** update `config.DEALER_SIGN_MODEL` if
needed, close the dealer-sign item in `VALIDATION.md`, and only *then* move from
ILLUSTRATIVE suggestions toward validated ones (still human-confirmed, no
auto-execution).

---

## 6. Non-goals (Phase B)
- No ML/black-box predictor before the sign is validated and a baseline is beaten.
- No auto-execution. No order wiring.
- No Bank Nifty / FinNifty — Nifty index only.
- Storage may move to SQLite here if cross-day queries get awkward (the Phase A
  storage is isolated behind `app/timeseries.py` to make that a one-file change).
