# Situational / scenario analysis — catalog & protocol

Point-in-time studies that ask a **conditional** directional/vol question keyed to where **spot**
sits relative to the GEX **levels** (call/put wall, gamma-magnet, max-pain, peak-OI pin, gamma-flip)
at a given intraday stamp. The premise is the mirror image of the [research-arc conclusion]
(`../memory/research-arc-conclusion.md`): direction is **dead when pooled**, so we STOP pooling and
slice to the narrow geometry+regime cells where a conditional edge might survive — guarded by
pre-registration and a locked holdout so we don't mine noise.

Sign-free by default. Where a scenario is inherently directional, the **level geometry** supplies the
direction (sign of `magnet − spot`, which wall spot is nearest), **never** the unvalidated
`DEALER_SIGN` landmine. Decision-support only; the human confirms.

---

## Prerequisite — enrich the 5-min feature cache (`app/intraday_features.py::day_features`)

Today the cache stores `peak_oi_strike` (pin proxy) but not the walls / magnet / max-pain / flip.
Add, per 5-min stamp (one extra pass over the ATM±10 cross-section already loaded):

| field | definition |
|---|---|
| `call_wall` | strike of max call-OI (or max call-GEX) above spot |
| `put_wall`  | strike of max put-OI below spot |
| `magnet`    | gamma-weighted mean strike `Σ k·\|gex_k\| / Σ\|gex_k\|` (the real GEX magnet ≠ peak-OI) |
| `max_pain`  | strike minimizing total ITM payout (sign-free) |
| `gamma_flip`| strike where cumulative net-GEX crosses zero — ⚠️ sign-dependent; also store both-sign + sign-free `|dist_to_flip|` |

`peak_oi_strike` stays. All scenarios then read the enriched ~93k-row cache — **no raw scans, no
data in the conversation**.

## Shared measurement protocol

- **Point-in-time.** Feature at stamp *t* uses only data ≤ *t*; outcome uses *t+1…*.
- **Outcomes:** (a) horizon returns 15/30/60-min forward; (b) **terminal** — signed distance closed
  to a target level by **15:15 spot** (chosen: before last-15-min settlement thrash, ≈ when a
  pin/blast trade is actually placed): `converged = (|spot_t − target| − |spot_15:15 − target|)/|spot_t − target|`.
- **Normalize distance** by the day's scale — express every gap in units of `σ·√T_remaining` (ATM
  straddle expected move) or ATM-straddle price, never raw points (80 pts means different things at
  8% vs 20% IV). This makes slices comparable across days.
- **Day-detrend** forward returns (subtract the day's mean) to kill index beta — as
  `analyze_intraday_pinning.py` already does.
- **Bad-tick robust:** reuse the ±8% 5-min clip / median-based stats already in the cache.
- **Benchmarks always:** every directional claim races the neutral anchors already in the pinning
  script — revert-to-open, revert-to-30m-SMA, trailing-momentum. A signal counts only if it BEATS them.
- **Confound controls:** condition on **time-of-day** and **DTE bucket** (both dominate near expiry).
- **Locked holdout:** last 30% of days walk-forward; a scenario is "real" only if it survives OOS
  net of a cost. Anything that dies is recorded dead so we don't re-mine it.

---

## Scenario catalog

Each: hypothesis → entry → prediction → metric. Prior = honest guess it survives.

### A · Pin convergence (expiry day)
- **A1 Magnet lock.** ✅ DONE → **NEGATIVE** (Study #12, `analyze_magnet_lock.py`). When a real gap
  exists, spot is NOT pulled to the pin — closer-rate to peak-OI 39% < mirror 51%, σ-shrink negative,
  no concentration cell >50%. The pin is **stickiness** (stays if already there; 45% of expiry stamps
  already pinned), **not attraction**. Kills the pin-convergence trade. Consistent with #2, sharper.
- **A2 Blast-target play** (the 13:45 example: pin 20100, spot 20020 → 20050 CALL). ⚠️ **Prior now
  LOW** — A1 already tested the core bet (spot drifts to pin from a distance) and found the opposite
  (mild continuation *away*). A2 is essentially answered NEGATIVE by A1; only run a residual check if
  the *direction of the gap* itself (continuation) turns out tradeable — which folds into **B2**.
- **A3 Convergence-by-clock.** Time-of-day ramp of A1's (non-)pull. Low value now that A1 is negative;
  the only live sub-question is whether *stickiness* (already-pinned → stays pinned) strengthens late.
  *Reframed: measure persistence of the pinned state by clock, not attraction.*

### B · Wall bounce vs. break — the regime-conditional core
- **B1/B2** ✅ DONE → **NEGATIVE (direction), cross-book** (Study #14, `analyze_wall_break.py`). Push
  toward the wall is ~0 in every regime tercile and the LOW−HIGH break-vs-bounce contrast flips sign
  across horizons/books (noise, placebo-matched). The only real effect is **containment**: LOW-conc
  crosses walls more (NIFTY +5–6pp, weaker off-NIFTY) — a restatement of |vanna|→lower vol, not
  direction. The wall is a containment boundary, not a directional pivot; the break is a coin flip.

### C · Gamma-flip regime (⚠️ sign landmine)
- **C1 Above/below flip.** Spot above flip = stable/mean-reverting, below = trending. Test BOTH sign
  conventions + sign-free (`|dist_to_flip|` → forward *vol*). *Prior: low for direction, med for vol;
  mainly re-probes the landmine.*

### D · Magnet-pull extension
- **D1** Extend the [morning magnet-pull IC +0.45](`../memory/ic-morning-magnet-pull.md`) with the
  *proper* gamma-magnet (not peak-OI), sliced by GEX bucket & distance. Does the real magnet beat the
  peak-OI proxy? *Prior: worth it — sharpens a signal we already like.*

### E · No-man's-land / squeeze (vol, sign-free)
- **E1 Pinned-at-peak → dead.** Spot within 0.1σ of peak-OI → lowest forward vol (confirmatory).
- **E2 Between-walls-midpoint → coiled.** Spot far from every wall/pin → higher forward vol/expansion.
  *Prior: med; feeds the vol gauge, not direction.*

### H · Gamma-blast compression precondition (expiry) — user hypothesis
- **H1 Coiled morning.** ✅ DONE → **REFUTED** (Study #13, `analyze_blast_compression.py`). Tight
  morning HL-range (<1%) does NOT precede an afternoon blast — ρ(morning, afternoon range) = **+0.45**
  (persistence: quiet→quiet), LOW-morning has the *smallest* afternoon, and high-GEX pinning doesn't
  flip it. Actionable inversion: a blast is likelier when the morning is ALREADY wide (≥1%), not tight.
  Uses raw 1-min spot (no OHLC download needed).

### F · Max-pain drift (weekly)
- **F1** Does spot drift toward max-pain over the last 1–2 days of a weekly? Metric: multi-day
  `converged` to max-pain. *Prior: low-med; classic but usually priced.*

### G · Level × footprint combo (gated on A/B showing life)
- **G1** Wall strength conditioned on participant-OI ([FII/Client footprint]
  (`../memory/positioning-footprint-suite.md`)): call-wall + FII-short-build → firmer resistance.
  *Prior: unknown; second-stage.*

### I · Opening-Range Breakout (ORB) × pin regime — user scenario
The intraday-price analogue of B1/B2: the **Opening Range** (OR) high/low form price-based support/
resistance; test whether a breakout **runs** (trends) or **fails** (reverts back into the range), and
whether the **pin/concentration tercile** flips the outcome — then size the run against the Study #15
per-bucket day range. Sign-free (tested in the breakout's own direction; the dealer-sign landmine is
untouched). Uses raw 1-min spot (OR + breakout timing) + the 5-min feature cache (concentration at the
OR close). **All data on hand — no download.**

**Definitions (loose ends rounded off):**
- **OR window:** first **15 / 30 / 60 min** from 09:15 → `OR_high`, `OR_low`, `OR_range = high−low`
  (from 1-min spot). Three windows tested; **30-min is the pre-registered primary**.
- **Breakout:** the first 1-min spot to *close* beyond a boundary by ≥ a **buffer =
  max(0.10·OR_range, 3 bp of spot)** (kills marginal touches). Record direction (up/down) + time. The
  test is on the break's **own** direction (sign-free) — we do NOT predict which way it breaks.
- **Pin tercile (the conditioner):** dealer-gamma **concentration at the OR-window close**
  (point-in-time — known when you'd trade the break), DTE-switched |vanna| (off-expiry) / |charm|
  (≤1.5 DTE) like the gauge → **HIGH pin = SIDEWAYS/calm**, MID, **LOW pin = TRENDY/wild**. Terciles
  within the sample; non-expiry primary, expiry (charm) reported separately.
- **Run / follow-through (point-in-time, breakout→close):** MFE (favorable extension beyond the
  boundary) and MAE (adverse, back into the OR). **TRUE run** = MFE reaches **1×OR_range** beyond the
  boundary (classic 1:1 ORB target) *before* price returns fully inside the OR; **FAKE** = returns
  inside the OR (or hits the opposite boundary) first. Also report **close-beyond-boundary** rate.

**Sub-tests:**
- **I1 Breakout follow-through by pin tercile × OR window.** Incidence (does it break at all?) and
  TRUE-run rate. Prediction: LOW-pin (trendy) → more breaks *and* truer runs; HIGH-pin (calm) → fewer
  breaks, more fakes (OR range holds). This is the B1/B2 contrast on price levels.
- **I2 OR high/low as S/R.** Touch→bounce vs break rate at the OR boundaries, and the **range-hold**
  share (day never breaks OR by the buffer), by pin tercile. HIGH-pin should hold the range more.
- **I3 Run vs day-range (Study #15 linkage).** Normalize MFE and the full-day HL by `OR_range` and by
  the bucket's Study-#15 typical day range; does an ORB "fill" the expected range, and does LOW-pin
  fill more? **Range-exhaustion check:** wide `OR_range / expected_day_range` → less room left to run
  (a breakout late in a used-up range under-runs).

**Controls / nulls (so it isn't mined):**
- **Placebo band:** breakout of a *random* same-width band (not the OR) → OR must beat it, else the OR
  level carries nothing beyond "a move of that size happened."
- **Vol confound (the key one):** does pin add *beyond* realized vol? Partial the run on the OR-window
  realized range / `atm_iv` — i.e. is LOW-pin's bigger run just "it's a high-vol day" (Study #15 / B1-
  B2), or does pin add on top? Honest prior: **mostly a vol restatement**; the discriminating value is
  any residual after controlling vol, and whether OR beats the placebo band.
- **Momentum benchmark:** ORB run vs trailing-momentum-at-breakout (is ORB more than "buy strength"?).
- **Multiple testing:** primary = 30-min OR TRUE-run rate by pin tercile; everything else secondary;
  **last-30% walk-forward holdout** before anything graduates.

*Prior: modest — likely a vol/persistence story (LOW-pin = trendy = bigger runs), but ORB is a real
traded strategy and the price-based OR may beat the OI walls B1/B2 found null; worth the clean test.*

---

## Execution order (cheapest / highest-prior first)
1. **Cache extension** ✅ + **A1** ✅ (NEGATIVE), **B1/B2** ✅ (NEGATIVE), **H** ✅ (REFUTED),
   **daily-move per bucket** ✅ (Study #15, wired to dashboard).
2. **I · ORB × pin** — next up (user scenario): I1 follow-through → I2 OR-as-S/R → I3 run vs day-range.
   Pre-registered primary: 30-min OR TRUE-run rate by pin tercile; vol-partial + placebo band + OOS.
3. **D1, E1/E2** (sharpen the vol signals we trust).
4. **C1, F1, G1** (lower prior / gated).

Each is one small script (like `analyze_intraday_pinning.py`) writing a compact table to
`docs/intraday-results.md`; **no raw data hits the conversation**. Survivors of the OOS holdout
graduate to a dashboard "situation" chip; failures are recorded dead.
