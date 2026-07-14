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
- **B1 Wall rejection (calm).** Spot approaches call-wall from below in high-concentration regime →
  mean-reverts down (put wall symmetric). Entry: `dist_to_wall < 0.3σ√T` AND calm. *Prior: med.*
- **B2 Wall-break continuation (wild).** Same approach, low-concentration → breaks & continues (blast).
  **The B1-vs-B2 contrast is the real test**: same geometry, opposite regime, opposite outcome. If the
  split is clean it's a genuine conditional edge. *Prior: med — best directional shot in the set.*

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

---

## Execution order (cheapest / highest-prior first)
1. **Cache extension** (walls/magnet/max-pain/flip per stamp) — unblocks everything.
2. **A1 → A3 → A2** (pinning family — highest prior, dashboard-relevant).
3. **B1 vs B2** (the bounce-vs-break contrast — best directional shot).
4. **D1, E1/E2** (sharpen signals we trust).
5. **C1, F1, G1** (lower prior / gated).

Each is one small script (like `analyze_intraday_pinning.py`) writing a compact table to
`docs/intraday-results.md`; **no raw data hits the conversation**. Survivors of the OOS holdout
graduate to a dashboard "situation" chip; failures are recorded dead.
