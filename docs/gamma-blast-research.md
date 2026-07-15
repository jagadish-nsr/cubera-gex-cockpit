# Gamma Blast — Research & Validation Notes

Status: research + design record for the dashboard "Gamma-blast crossover watch"
(`app/gammablast.py`, the panel in `app/dashboard.py`, and the 1-min capture in
`scripts/log_gamma_blast.py`). **Decision-support only — not financial advice, no
auto-execution.** Read `CLAUDE.md` first, especially the dealer-sign landmine.

---

## 1. What a "gamma blast" is

A **gamma blast** is the expiry-day phenomenon where an option premium expands
violently (often 2–3× in minutes) after a quiet session, because at-the-money
(ATM) options have enormous **gamma** as time-to-expiry → 0. Gamma is the rate of
change of delta; near expiry a small move in the underlying swings the ATM option
from ~0.5 delta toward 0 or 1 very fast, so the premium accelerates. It is an
**option-buying** play (buyers love the convexity; sellers fear it).

Typical folklore setup for Nifty/Bank Nifty (practitioner sources, **not** a
validated rule):

- **Day:** weekly/monthly **expiry day** only (gamma is maximal).
- **Quiet morning:** index moves **< ~1%** from 09:15 to ~13:45.
- **Trigger window:** after ~**13:45 IST**, a breakout on rising volume + an IV
  uptick.
- **Strike:** buy **ATM or slightly-OTM** (high gamma, delta ~0.3–0.5).
- **Risk:** small fixed stop (e.g. 30% of premium), **exit by ~15:15**, risk
  1–2% of capital — because most expiry days the move never comes and theta
  shreds the premium to zero.

The payoff shape is a **lottery**: *lose small often, win big rarely*. This is the
opposite of the "high win-rate" impression beginners take from the highlight
reels. (See §6.)

---

## 2. The wife's rule, decoded — and why it is *exact*

Her rule: take the **ATM strike**, watch its **call and put premiums**, and a
**crossover** signals a likely blast. This is not folklore — it is **put-call
parity**.

At a fixed strike `K`, same expiry, with discount factor `DF` and forward `F`:

```
C(K, t) − P(K, t) = DF · (F_t − K) ≈ S_t − K      (near expiry: DF≈1, F≈S)
```

So **`C = P` exactly when `S = K`.** The ATM call and put premiums **cross
precisely when spot crosses the strike.** Therefore:

- `call > put`  ⇒  spot is **above** the strike.
- `put > call`  ⇒  spot is **below** the strike.
- `call ≈ put`  ⇒  spot is **at** the strike — the crossover/pivot.

The "crossover" is a **price-crossing-a-level detector expressed in premium
terms**, and it is **sign-free**: pure parity, no dealer-sign assumption. After
the cross, whichever option goes in-the-money gains intrinsic value ~1:1 with
spot while the other collapses toward zero — and near expiry the gamma makes that
divergence explosive. That divergence *is* the blast.

### 2a. The anchoring subtlety (critical)

You must **anchor** (freeze) the strike. A **rolling ATM** (always the strike
nearest spot) is, by construction, *always* ~crossed (`C ≈ P` all day) → **zero
signal**. The signal only exists on a **fixed** strike that spot then moves
*through*. We anchor two fixed strikes at **13:45 IST**:

| Anchor | Definition | "Maximum gamma" sense |
|--------|------------|------------------------|
| **ATM** | listed strike nearest spot at 13:45 | max **per-contract** BS gamma (peaks ATM) |
| **Magnet** | strike of peak **gamma exposure** (`|net_gex|`) | max **OI-weighted** gamma exposure |

These two anchors are exactly the two meanings of "the maximum-gamma strike" —
bare gamma vs OI-weighted gamma **exposure**. When spot crosses the magnet on
expiry day, the crossover *and* the engine's pin-break coincide.

---

## 3. The mechanism, and how it maps to our engine

A blast is the **negative-gamma** (dealer-short-gamma) expiry regime: when the
hedging dealers are **short** gamma near the pin, a break of the strike forces
them to hedge **with** the move (buy as it rises / sell as it falls) →
**amplification**. When dealers are **long** gamma, the same concentration
**pins** price (they fade the move) → low volatility. This is standard
gamma-hedging feedback (see references in §7).

Our engine already computes everything this needs:

- **Gamma flip** (`gex/flip.py`) — the calm⇄wild boundary.
- **Magnet** = peak `|net_gex|` strike (`gex/core.py`) — the pin.
- **Per-strike `net_gex`** — gamma exposure by strike (the energy map).
- **Regime** — POSITIVE (pin/fade) vs NEGATIVE (momentum/amplify).

So the crossover watch is **spot crossing the magnet/flip on expiry day** — a lens
on what we already model, not a bolt-on.

### 3a. Tracking *change* in gamma at a strike

Gamma exposure at a strike `G(K,t) = γ(K,Sₜ,Tₜ) · OI(K,t)` changes from three
drivers:

1. **Spot moving** (γ rises as spot nears K) — deterministic.
2. **Time decay** (γ of near-ATM strikes balloons as T→0) — deterministic.
3. **OI changing** (`ΔOI` — fresh positioning) — **the informative part.**

A naïve `dG/dt` is dominated by (1)+(2). The blast-relevant signal is **fresh
gamma being built**, `γ(K,Sₜ)·ΔOI(K)`, near spot — "where is the energy piling
up." The dashboard's **gamma-concentration strip** ranks strikes by `|net_gex|`
near spot and shows their `ΔOI`; the 1-min capture stores `net_gex` + `ΔOI`
per strike so this is studyable offline.

---

## 4. The landmine: blast vs pin depends on the (unvalidated) dealer sign

A high-gamma-exposure strike **either pins or blasts**, and which one depends
entirely on the **dealer sign** — which is **UNVALIDATED for India** (see
`CLAUDE.md` and the `--both-signs` analysis, which even leans *inverted*). The
crossover *detection* is sign-free and safe to show; we must **not** assert
"this will blast" vs "this will pin" from the regime yet.

**The opportunity:** logging gamma concentration + `ΔOI` at strikes and then
recording whether spot **pinned or blasted** there is simultaneously a
**blast-finder** and the exact **pin-vs-blast evidence that validates the dealer
sign**. They are the same experiment. The 1-min per-strike capture is the
substrate.

---

## 5. What we built (sign-free, decision-support)

- `app/gammablast.py` — pure, hand-checkable: `pick_anchors`, `crossover_state`,
  `detect_crossovers`, `gamma_concentration` (`tests/test_gammablast.py`).
- Dashboard **"Gamma-blast crossover watch"**: two anchored call-vs-put premium
  charts with crossover flags + the gamma-concentration strip. **PCR(OI)** added
  to the key metrics.
- Capture: per-strike **nearest-expiry premiums** (`nearest_expiry_premiums`) +
  per-strike **`net_gex`** ride the per-strike logging.
- `scripts/log_gamma_blast.py` + `run_gamma_blast.bat` — **1-min**, **expiry day
  only**, **13:00–15:30 IST**, into `data/journal/gamma_blast/`.

Everything is labelled decision-support; it flags **where/when** a pivot happens,
never the **direction**.

---

## 6. Honest validation verdict

- **The rule is mechanically real** (put-call parity) — ✅.
- **It is coincident, not predictive.** The crossover *confirms* spot is at the
  strike; the edge is entirely in the *post-crossover continuation* — a momentum
  bet with brutal theta and a **lottery payoff** (lose-small-often /
  win-big-rarely).
- **No rigorous public backtest** with a win rate exists; the "2–3×" examples are
  hindsight-picked. Practitioner/quant writeups warn that after costs many
  high-win-rate expiry structures are **flat-to-negative EV**.
- **Direction (blast vs pin) is unproven for India** — gated on the dealer-sign
  landmine.

**Bottom line:** a sound, sign-free *detector* worth having as a visual aid and
as a data-collection harness — **not** a validated edge. The
crossover-precedes-blast hypothesis is **UNTESTED** until enough 1-min expiry days
accumulate; then a dedicated analysis mode can measure it.

---

## 7a. Regime-split re-examination (2026-07-15) — the expiry-structure eras

A Claude-Web review flagged a real methodological gap: our blast studies **pooled ~5 years
across two SEBI/NSE structural breaks**, and pooling contaminates any expiry-day backtest
(the live weekly venues AND the weekend-theta profile both shift mid-sample):

- **2024-11-20** — SEBI single-weekly-per-exchange: NSE kept the **Nifty** weekly; Bank Nifty,
  FinNifty, Midcap weeklies were discontinued (monthly-only after).
- **2025-09-01** — NSE moved the **Nifty** weekly **Thursday → Tuesday**.

We added a single source of truth (`app/expiry_regime.py`, `expiry_era()`) and re-sliced the
four blast studies by era (`pre-Nov24` / `Nov24-Aug25` / `Sep25+`). NIFTY results:

| Study | Pooled read | Per-era (the fix) |
|-------|-------------|-------------------|
| **Compression / retail coil** (`analyze_blast_compression`) | ρ(morning,aft)=+0.45 (persistence) | ρ **POSITIVE in all 3 eras** (+0.46 / +0.39 / **+0.57** current); tight-morning blast tail ≤ wide-morning **in every era** → "<1% morning ⇒ blast" is folklore in **every** regime, not a pooling artefact. **Null hardened.** |
| **Intraday pinning** (`analyze_intraday_pinning`) | +0.06→+0.12 by horizon | pin_gap→fwd **POSITIVE and era-robust** (+0.06→+0.17), largest at 60 min, and the **current Sep25+ era has the LARGEST 60-min IC (+0.166)** → a small but real, sign-free, era-robust effect. |
| **Charm → direction** (`analyze_charm_blast`) | ~0 | **no edge in any era** (pre +0.009, Nov24 −0.055, Sep25 +0.038 < the +0.05 bar; true-blast sign-alignment ~50%). Charm does not call blast direction in any regime. |
| **Staying-power grade** (`analyze_blast`) | Spearman +0.70 (n=57) | the **entire** live-captured sample (57 crossings, 11 days) is in **Sep25+** → the +0.70 is **not** a pooled artefact; it reflects the *current* market structure. |

**Verdict:** the regime split **hardens** the direction nulls (compression, charm) rather than
revealing a hidden edge, and **confirms** the two survivors are current-era-valid — pinning is
era-robust (and strongest now), and the staying-power grade is entirely from the live Tuesday
era. The §6 bottom line stands: sign-free detectors + the staying-power grade are worth keeping;
**direction remains dead**. Next step (`docs/TODO.md` [B1]): a first-class blast **backtest**
with honest IV-crush + fill-realism + era-split cost modelling.

## 7b. Does the GRADE deliver the retail "2–3× premium jump"? (2026-07-15)

We tied the sign-free blast **grade** (TRUE / WEAK / FAKE) to the retail "2–3× in minutes"
folklore on the 8,142-crossing leak-free dataset (`build_blast_dataset.py`, now storing the
winning-leg premium multiple; graded offline via the shared `app.gammablast.grade_blast`, so the
grader can never drift from the live one). The multiple = *winning (ITM-going) leg premium at peak
/ at entry* over the 40-min payoff window. `scripts/analyze_blast_premium.py`:

| Window | TRUE peak≥3× | WEAK peak≥3× | FAKE peak≥3× |
|--------|:---:|:---:|:---:|
| All crossings (DTE≤2.5) | 1% | 1% | 1–2% |
| 0-DTE | 1% | 1% | 1–2% |
| **0-DTE afternoon (≥13:45)** | **2%** | **3%** | 2–**8%** |

Findings (NIFTY):
1. **A ≥3× jump is rare** — ~1% of crossings, and only ~2–3% even in the 0-DTE *afternoon* blast
   window. (≥2× is ~3–5%, ~8–14% in the afternoon.)
2. **The grade does NOT pick the 3× winners.** The TRUE bucket is *not* higher than WEAK/FAKE; the
   cheapest bucket (FAKE "writers defending", median entry ~24 pts) has the *highest* ≥3× rate (8%)
   — a low-denominator artefact (a small move triples a penny option). The grade measures the
   staying-power of the **spot move**, not the **premium payoff**.
3. **In the current Sep25+ (Tuesday-weekly) regime, ZERO afternoon 0-DTE crossings hit ≥3×**
   (TRUE 0/37, WEAK 0/30). The handful of 3× events are almost all pre-Nov24 (multi-weekly,
   Thursday) — and even there only ~1–3%.

**Bottom line:** the "2–3× gamma-blast" is a **selection-biased highlight reel** — a ~1–2% tail
that the grade can't forecast, essentially absent in the current market structure. Consistent with
§6 / §7a: the grade is honest *confirmation of a break's staying power*, **not** a premium-jump
predictor. This is exactly why the [B1] backtest (docs/TODO.md) must score the **full lottery
distribution** with real expiry-afternoon fills, never the mean or a mid-price peak.

## 7c. Grade TRAJECTORY, speed-of-ignition & WEAK→TRUE upgrades (2026-07-15)

The grade above is a single fixed-window (10-min) snapshot — it discards the fact that a crossing's
grade **evolves** (FAKE→WEAK→TRUE, sometimes TRUE→decay). We added the trajectory to
`build_blast_dataset.py`: per crossing, the static 10-min grade, the first 5-min step the
*expanding-window* grade reaches TRUE (`k_true`, ≤30 min else "never"), and the winning-leg outcome
over a **fixed 20-min window AFTER that trigger** (`cont_after`, `mult_after_peak`) — fast-vs-slow
apples-to-apples, strictly point-in-time. `scripts/analyze_blast_speed.py`. Evaluated at a **≥1.5×**
"good blast" bar (a TRUE call that runs 1.5× is a win; ≥3× is a ~1% lottery — see §7b):

- **Most crossings never ignite.** Only **24%** ever reach TRUE within 30 min (35% in the 0-DTE
  afternoon); **76% never confirm** → the grade is first a **no-go filter**.
- **Speed genuinely matters at 1.5×.** Post-trigger P(≥1.5×) is **monotone in speed** — 0-DTE
  afternoon: **FAST (≤10 min) 21% · MID 14% · SLOW 11%** (all crossings 12/8/6%); the tail too
  (FAST p95 2.41× vs SLOW 1.59×). Continuation medians are still ~0, so this is a **premium-payoff /
  confirmation-quality** effect, not a spot-direction edge.
- **And it SURVIVES the current regime** (unlike ≥3×, which was 0%). FAST ≥1.5% by era: pre-Nov24
  **23%** · Nov24-Aug25 21% · **Sep25+ (current Tuesday) 15%**.
- **Value of waiting (transition table).** Of crossings graded **WEAK at 10 min, 57% later upgrade
  to TRUE**, and 13% of those deliver ≥1.5× — worth watching, but below a **static-TRUE (22% ≥1.5×)**.
  FAKE-pin rarely upgrades (18%). So: a fast/early TRUE is best; a WEAK is worth waiting on (upgrades
  more often than not) but pays less; a FAKE-pin is mostly a pass.

**Answering the two questions this raised:** (1) yes — the old design graded each crossing *once* at
a fixed 10-min window, mislabelling a FAKE→WEAK→TRUE path by its early snapshot; the trajectory fixes
that. (2) time-to-TRUE **does add signal at the ≥1.5× bar** — fast ignition ~doubles the good-blast
rate vs slow and survives into the current regime — but it is a **confirmation-quality conditioner,
still sign-free and directionless**, and the multiples are **MID peaks** (real expiry-afternoon fills
are worse, so treat the rates as optimistic). This is the most promising blast refinement so far and
the concrete hypothesis for the [B1] backtest: *enter on a FAST/early TRUE, size by ignition speed,
model the fills honestly.* The `k_true` / static-grade / post-trigger fields now live in the dataset.

## 7d. 1-MINUTE ignition timing & the WEAK cutoff (2026-07-15)

The Dhan store is genuinely **1-minute** (premium AND OI both update every minute) — the 5-min
dataset just downsampled it. `scripts/analyze_blast_timing.py` reads the raw store at 1-min, detects
each first near-money crossing, and builds the blast-score trajectory **minute-by-minute** from the
cross (0-DTE only; 3,108 crossings / 458 days). Direction isn't the question — the rising leg *is*
the direction — so this purely times the winning-leg premium.

- **Sub-5-min ignition is real and is the best signal.** 83% never ignite; of the 17% that do,
  **43% reach TRUE within 5 min** (median time-to-TRUE 6 min). Post-trigger, **≤5-min-TRUE** dominates:
  0-DTE afternoon **P(≥1.5×) = 36%** (median 1.27×, ≥2× 11%) vs 6-10 min 15% and 11-15 min 19%.
- **There IS a WEAK cutoff — and it's early.** Bucketing by the **score at 3 min** (0-DTE afternoon),
  P(reach TRUE ≤15 min) rises monotonically:

  | score@3min | reach TRUE | of which ≤5 min | good ≥1.5× |
  |---|---:|---:|---:|
  | <25 (dead) | 7% | 0% | — |
  | 25-34 | 13% | 8% | 8% |
  | **35-44** (WEAK-lo) | 37% | 19% | 23% |
  | **45-54** (WEAK-hi) | **54%** | 29% | 14% |
  | **55-59** (near-TRUE) | **90%** | 67% | 33% |
  | ≥60 (TRUE@3m) | 100% | 100% | 35% |

  So a WEAK is **not one thing**: a **score@3min ≥ 45** upgrades to TRUE more than half the time (≥55
  → 90%, two-thirds of them within 5 min), while a WEAK-lo (<45) mostly fizzles. The early score is a
  strong, actionable read of whether waiting on a WEAK will pay.
- **Holds in the current regime** (small n): 0-DTE-afternoon ≤5-min-TRUE good-blast rate — pre-Nov24
  33%, Nov24-Aug25 50%, **Sep25+ 27%** (n≈11).

**Takeaways** (answering the asks): (1) **time-from-cross is a real factor** — ignition speed and the
early-score slope should feed the score/entry, not just the static grade; the concrete rule is *act on
a fast rise to score ≥45 within ~3 min; a ≤5-min TRUE is the premium sweet spot*. (2) The WEAK band
splits cleanly at **~45-55 @3min**. Caveats stay: 83% never ignite (no-go filter first), high-score
bands are small-n, and multiples are **MID peaks** (real expiry-afternoon fills are worse). This is the
1-min entry model for the [B1] backtest.

## 7. References

Practitioner / strategy descriptions (Indian market):

- Enrich Money — *Gamma Blast Strategy in Options Trading | NSE Expiry Guide*:
  https://enrichmoney.in/blog/gamma-blast-strategy-nse-options-expiry
- Samco — *Gamma Blast Strategy: A Deep Dive*:
  https://www.samco.in/knowledge-center/articles/gamma-blast-strategy-a-deep-dive/
- Quantsapp — *As your options explode: The GAMMA FACTOR!*:
  https://medium.com/@quantsapp.optiontrading/as-your-options-explode-the-gamma-factor-2f42be10dd91
- Quantsapp Classroom — *Option Greek Gamma Explained (the delta-accelerator)*:
  https://web.quantsapp.com/quantsapp-classroom/option-greeks/gamma
- TradingView — *Gamma Blast Detector (Nifty)* indicator (soansnoble):
  https://www.tradingview.com/script/fqymTFet-Gamma-Blast-Detector-Nifty/

Options theory (the parity backbone of the crossover rule):

- OIC — *Put/Call Parity*:
  https://www.optionseducation.org/advancedconcepts/put-call-parity
- CME — *Put-Call Parity*:
  https://www.cmegroup.com/education/courses/introduction-to-options/put-call-parity
- Wikipedia — *Put–call parity*:
  https://en.wikipedia.org/wiki/Put%E2%80%93call_parity

Gamma-hedging → realised-volatility mechanism (academic / quant):

- *Gamma Positioning and Market Quality*, J. Econ. Dynamics & Control (2024):
  https://www.sciencedirect.com/science/article/pii/S0165188924000721
- Baltussen et al., *The impact of option hedging on the spot market volatility*
  (2022): https://www.sciencedirect.com/science/article/pii/S0261560622000304
- Dim, Eraker, Vilkov — *0DTEs: Trading, Gamma Risk and Volatility Propagation*
  (SSRN): https://papers.ssrn.com/sol3/Delivery.cfm/4692190.pdf?abstractid=4692190
- *Beta-Dependent Gamma Feedback and Endogenous Volatility Amplification* (arXiv
  2025): https://arxiv.org/html/2511.22766

India microstructure (who is on each side — context for the dealer-sign landmine):

- SEBI — *93% of individual traders incurred losses in equity F&O, FY22–24*
  (Sept 2024):
  https://www.sebi.gov.in/media-and-notifications/press-releases/sep-2024/updated-sebi-study-reveals-93-of-individual-traders-incurred-losses-in-equity-fando-between-fy22-and-fy24-aggregate-losses-exceed-1-8-lakh-crores-over-three-years_86906.html

GEX background (US convention this engine was ported from):

- SpotGamma — *GEX (Gamma Exposure) Explained*:
  https://support.spotgamma.com/hc/en-us/articles/15214161607827-GEX-Gamma-Exposure-Explained-What-It-Is-and-How-SpotGamma-Uses-It

> Caveat: the practitioner links describe the strategy as taught; they are **not**
> independent validation. The academic links validate the *mechanism* (dealer
> short gamma amplifies vol), **not** the specific entry rule or its sign for
> Nifty. Treat §6 as the governing verdict.
