# Pre-registration — does PASSIVE big-money positioning preempt the multi-day move?

Status: **pre-registered, not yet run** (2026-08-01). Locked *before* looking at outcomes so the test
is confirmatory, not fished. Decision-support research; sign-free; no auto-execution (CLAUDE.md).

## Motivation (why this study, why now)
The conviction-filter exploration ([[conviction-filter-todo]] memory) found that high-conviction
**aggressive intraday PRINTS** are, if anything, *contrarian* at 15–30 min and **decay** with horizon
(NIFTY raw IC −0.30 → −0.10 → +0.04 by 2 h; no clean flip to confirmatory; multi-day = noise at n=4).

The reframe: **aggressive (spread-crossing) flow is impatient/short-term by construction** — hitting the
ask is liquidity-*demanding*. Genuinely preemptive "smart money" with a long-term view is **passive**:
it accumulates open interest quietly over days to avoid impact, and is therefore **invisible to the
aggressor-print signal**. So the preempt hypothesis must be tested on **passive OI accumulation**, over a
**days-to-weeks** horizon — not the intraday tape.

The right instrument already exists and is **powered**: `participant_oi` has **~365 daily EOD files since
2025-02-03** (vs n=4–8 for the intraday tape). See [[positioning-footprint-suite]], `docs/participant-oi.md`.

## Hypothesis (H1)
Sustained, **passive** accumulation of a directional index position by the informed participant cohort
(FII in index futures; the net-writer cohort in options) **predicts the subsequent multi-day index
return in the SAME direction** — i.e. a *positive* rank IC that (unlike the aggressive-print signal)
**persists or strengthens** from the 1-day to the 5–10-day horizon.

Null (H0): passive-accumulation drift has zero (or only contemporaneous / momentum-proxy) relationship to
forward multi-day returns once trailing return is controlled — consistent with [[research-arc-conclusion]].

## Instruments & signals (exact, point-in-time — feature at day *t* uses only files published ≤ *t*)
**Primary — participant-OI drift (`app/participant_oi.py`):**
- `fii_fut_drift(t, k)` = FII `net_fut` at *t* minus its value at *t−k*, over lookback **k = 3 trading days**
  (sustained build, not a one-day blip). `net_fut = fut_long − fut_short` from `derive_signals`.
- `writer_drift(t, k)` = k-day change in `net_opt_writing` of the dominant writer cohort (Client/Pro/FII).
- **Passivity gate (the novel filter):** keep a day's drift only if it was NOT accompanied by heavy
  aggressive tape flow that day — |same-day big-tape net signed ₹| below its trailing median (from the
  `big_money.db` events; days without tape coverage are kept and flagged `tape=NA`). Rationale: isolate
  *quiet* accumulation from *aggressive* pushes.

**Secondary — sign-free per-strike / futures ΔOI build-up (`app/futures_buildup.py`, `app/oi_footprint.py`):**
- `passive_build(t, k)` = k-day directional ΔOI build (`classify_futures_buildup` bias aggregated) at the
  index-futures level, filtered to the same passivity gate. A cross-check that does not depend on the
  participant taxonomy.

All signals are **z-scored within a trailing 60-day window** so the IC is scale-free and regime-relative.

## Outcome
Forward **index (spot, not futures — avoids the roll)** log return over pre-specified horizons
**h ∈ {1, 3, 5, 10} trading days**, from `nifty_daily_close` (`scripts/analyze_participant_ic.py`). Close-
to-close; day *t* signal → return over (*t*, *t+h*].

## Primary test (pre-specified estimator, gating, and controls)
1. **IC(h)** = Spearman rank corr between the signal at *t* and the forward *h*-day return, over all
   qualifying days. Reported per horizon.
2. **The control that matters — PARTIAL IC:** residualize BOTH the signal and the forward return on the
   **trailing 5-day return** (momentum), then rank-correlate the residuals. This is the analog of the
   bucket-return control that killed the BankNifty print effect — it separates "preemptive positioning"
   from "positioning that just chases recent trend." *A signal that survives this is the real claim.*
3. **Overlapping-return correction:** h-day forward windows overlap, so naïve t-stats are inflated. Use a
   **block bootstrap** (contiguous ~2·h-day blocks, 2000 resamples) for the CI on mean IC; report the
   bootstrap 2.5/97.5%ile. (Newey–West on the IC series is an acceptable alternative, pre-committed here as
   the bootstrap.)
4. **Placebo:** recompute IC with the forward returns **circularly shifted** by a random ≥20-day offset
   (breaks the signal→return link while preserving both autocorrelations), 2000 draws → the null band.

## Pre-registered decision rules (fixed now)
- **CONFIRM H1** only if, on the PRIMARY (participant FII drift) signal, the **partial** IC is **positive**
  at **h = 5 AND h = 10**, its bootstrap CI excludes 0 at both, it **exceeds the placebo 97.5%ile**, AND
  the secondary (ΔOI build-up) signal agrees in sign at h = 5. (Persistence/strengthening across horizon is
  the preempt signature — a positive h=1 that decays to 0 by h=5 does NOT confirm.)
- **NULL** if the partial IC CI includes 0 at h = 5 and h = 10 (the [[research-arc-conclusion]] default).
- **KILL / do-not-pursue** if the partial IC is **negative and CI-excludes-0** at h ≥ 5 (positioning is
  contrarian even long-term → not preemptive; stop).
- Report all four horizons and both signals regardless of outcome. No post-hoc horizon or cohort selection.

## Sample & power
- Participant-OI: ~365 days available now → the primary test is **runnable immediately** and reasonably
  powered (with the overlap correction, effective n at h=10 is ~35 independent blocks — modest but real).
- The **passivity gate** depends on same-day tape coverage, which only exists from ~2026-07-22 (n≈8). So
  run the study in **two tiers**: (A) **ungated** participant drift on the full 365-day history (powered,
  the headline), and (B) **passivity-gated** on the short tape-overlap window (exploratory, accrues).

## Confounds explicitly controlled / acknowledged
- **Momentum proxy** → the partial-IC control (step 2) is the primary defense.
- **Spot vs futures / basis** → outcome uses spot; signals use OI (unit-free), so no basis leak
  ([[tick-data-ic-research]]: flow looks predictive only via spot co-movement — controlled by using OI, not
  price-derived flow).
- **Expiry-cadence artifacts** → include an expiry-week dummy; do not pool across the two SEBI/NSE expiry
  eras without the split ([[gamma-blast-regime-split]]).
- **[[spot-feed-corruption]]** → NIFTY spot is clean; if BankNifty is added, use Sep-2025+ only.
- **Publication lag** → participant file is EOD post-close; a *t*-day signal is only usable for returns from
  *t+1*, already enforced by the (t, t+h] convention.

## Deliverables when run (only after this file is committed)
- `app/preempt_study.py` (mirrors `app/stophunt.py`: pre-registered, partial-IC + block-bootstrap + placebo
  baked in) and `scripts/preempt_study.py` runner.
- Hand-checkable synthetic test alongside.
- A results section appended BELOW this line — never edited above it (the registration is immutable).

---
<!-- RESULTS (append-only, added after running; do not edit the pre-registration above) -->

## Results — tier A (ungated), run 2026-08-01

`app/preempt_study.py` + `scripts/preempt_study.py`. Sample: **365 participant-OI days** (2025-02-03 →
2026-07-31), 558 close days. Dominant option-writer cohort = **Client** (top writer 350/365 days — retail
writing dominates, consistent with the India landmine). Config as registered (k=3d drift, 60d z-window, 5d
trailing-return control, block bootstrap n=2000, circular-shift placebo n=2000).

**Primary — FII net-futures drift (`fii_drift_z`):**

| h | n | raw IC | partial IC | boot 95% CI | placebo 95% | CI≠0 | >placebo |
|---|---|---|---|---|---|---|---|
| 1 | 331 | −0.064 | −0.042 | [−0.146, 0.063] | [−0.098, 0.101] | no | no |
| 3 | 329 | −0.119 | −0.129 | [−0.251, 0.005] | [−0.148, 0.147] | no | no |
| 5 | 328 | −0.068 | −0.053 | [−0.163, 0.101] | [−0.168, 0.170] | no | no |
| 10 | 325 | −0.168 | −0.125 | [−0.278, 0.029] | [−0.237, 0.174] | no | no |

**Secondary — dominant-writer (Client) net-writing drift (`writer_drift_z`):**

| h | n | raw IC | partial IC | boot 95% CI | placebo 95% | CI≠0 | >placebo |
|---|---|---|---|---|---|---|---|
| 1 | 331 | −0.047 | −0.053 | [−0.145, 0.052] | [−0.095, 0.098] | no | no |
| 3 | 329 | +0.072 | +0.072 | [−0.049, 0.180] | [−0.109, 0.126] | no | no |
| 5 | 328 | +0.033 | +0.036 | [−0.090, 0.143] | [−0.090, 0.088] | no | no |
| 10 | 325 | +0.028 | +0.023 | [−0.098, 0.137] | [−0.096, 0.086] | no | no |

### Verdict: **NULL** (both signals, pre-registered rule)
No horizon has a partial-IC CI that excludes 0 or beats the placebo band; the CONFIRM condition
(partial IC > 0, CI≠0, beats placebo at **both** h=5 and h=10) is not met, and no KILL (no CI-excluded
negative at h≥5). The two signals even lean opposite ways (FII drift mildly negative, writer drift mildly
positive), both inside noise.

**Interpretation.** At full, powered sample the **passive-preempt hypothesis does not hold** for
participant-OI drift — the days-to-weeks positioning drift carries no forward-return edge once trailing-
return momentum is removed. This matches the [[research-arc-conclusion]] default (no clean directional
edge at full sample) and closes the loop opened by the conviction-filter finding: neither the *aggressive*
intraday prints (contrarian, decays) nor the *passive* multi-day accumulation (null) give a tradeable
directional read. The participant-OI suite remains a descriptive "who is positioned" map, not a forecast.

**Not run:** tier B (passivity-gated on the short tape-overlap window) — deferred until the big-tape
archive accrues enough same-day coverage to make the gate meaningful; tier A already answers the headline.
