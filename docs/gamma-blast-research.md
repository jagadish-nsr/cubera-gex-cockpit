# Gamma Blast — Research & Validation Notes

Status: research + design record for the dashboard "Gamma-blast crossover watch"
(`app/gammablast.py`, the panel in `app/dashboard.py`, and the 1-min capture in
`scripts/log_gamma_blast.py`). **Decision-support only — not financial advice, no
auto-execution.** The production book is `short_call_short_put`. Both legs are stored
negative. The balance is call-gamma magnitude minus put-gamma magnitude.
Who hedges is unmeasured. Do not read POSITIVE as a pin or NEGATIVE as a chase.

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
expiry day, the premium crossover and the largest-gamma strike are the same place. That is a location, not a pin breaking.

---

## 3. What the engine actually marks

A blast in this cockpit is a **price location on expiry day**: spot moving
through a fixed strike while near-the-money gamma is large because time is
short. Black-Scholes gamma of an at-the-money option rises as expiry
approaches. That is option math. It does not say a dealer must buy the rally
or sell the dip.

The production book stores both call and put gamma negative
(`short_call_short_put`). Walls are the strikes with the largest call-gamma
and put-gamma magnitudes. The regime total and the flip are **call magnitude
minus put magnitude**:

- **POSITIVE** means the call book is larger than the put book.
- **NEGATIVE** means the put book is larger than the call book.

Those words are not a pin, a fade, or a momentum forecast. Absolute GEX is
the volatility context that held in the research sample (higher concentration,
calmer forward vol). The sign is not a direction call.

The engine already computes the map this watch uses:

- **Gamma flip** (`gex/flip.py`) — where call magnitude and put magnitude cross.
- **Magnet** — the strike with the largest absolute gamma (`gex/core.py`).
- **Per-strike gamma** — the concentration map.
- **Regime** — call-heavy or put-heavy, as defined above.

The crossover watch is spot crossing a frozen strike on expiry day. It is a
parity detector sitting on that map.

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

## 4. One convention

The comparison that tried a second sign (`long_call_short_put`, dealers long
calls and short puts) is closed. Production rejects any sign other than
`short_call_short_put`. Switching that old sign did not move the flip, the
regime, or the walls. It only changed the magnet and the per-strike net sign.

Who actually delta-hedges on NSE is unmeasured. Open interest is not
buyer-versus-seller flow. A high-gamma strike is a concentration. It is not
evidence that the strike will pin or blast.

The crossover detector stays. It is put-call parity: call premium equals put
premium when spot is at the frozen strike. Logging whether spot later sat
there or left is a research record. It is not a reason to restore a second
sign model or to tell a later edit to flip `DEALER_SIGN_MODEL`.

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
- **A blast and a pin are not two signs of one hedge.** The crossover says spot is at the frozen strike. It does not say the strike will hold or run. Who hedges is unmeasured.

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

## 7e. [B1] Backtest of the 1-min entry model (2026-07-15)

`scripts/backtest_gamma_blast.py` trades the §7d model directly: at each 0-DTE crossing, enter the
**rising (winning) leg** the moment the score first hits TRUE within ≤5 min ("fast TRUE"); exit +50%
target / −30% stop / 15-min time. IV crush needs no model (real 1-min premium path); a spread cost is
swept; compared to a naive "enter every crossing" baseline; era-split. 246 model trades / 458 0-DTE
days (~0.5/day).

| rule / half-spread | win% | expectancy/trade | payoff | target-hit |
|---|---:|---:|---:|---:|
| **MODEL @0% (mid)** | 52% | **+3.2%** | 1.13× | 21% |
| MODEL @3% | 46% | **−2.8%** | 0.94× | 21% |
| MODEL @6% | 42% | −8.5% | 0.71× | 21% |
| BASELINE @0% | 42% | −1.7% | 1.16× | 10% |
| BASELINE @3% | 33% | −7.5% | 0.97× | 10% |

**Verdict — a real signal, not a tradeable edge.** The model has a genuine *structural* edge at
mid-price (+3.2%/trade, target-hit 2× the baseline) and **decisively beats indiscriminate entry** — so
the fast-TRUE trigger is a real selector, confirming §7d. But it **does not survive transaction costs**:
breakeven is ~2-3% half-spread, and it goes negative there — worst in the **current regime** (era split
@3%: pre-Nov24 +0.8%, Nov24-Aug25 −3.8%, **Sep25+ −11.4%**; afternoon-only −4.1%). This is exactly the
IV-crush + fill-realism story the literature warns about, quantified: the edge is real but **thinner
than the spread you actually pay on expiry-afternoon options.** Consistent with the whole arc —
decision-support, not alpha. Caveats: mid-close fills (real book worse), close-to-close exits, first-TRUE
trigger, sign-free. **[B1] is answered:** the honest use is a live *confirmation-quality* readout (see
the dashboard timing panel), not an automated trade.

## 7f. Freeze-time robustness — 1:45pm vs 2:30pm (2026-07-20)

Everything above froze the blast window at **13:45** (the "1:45pm ATM" anchor of §2a). To check the
studies aren't an artifact of that one cut, the afternoon boundary was made a single env-overridable
knob — **`BLAST_AFT_CUT='HH:MM'`** (default `13:45`), read by `app.gammablast.afternoon_cut()` and used
by `analyze_blast_{speed,timing,premium,compression}.py` and `backtest_gamma_blast.py §3`. Re-running the
whole suite at **`BLAST_AFT_CUT=14:30`** vs the 13:45 baseline (NIFTY, same 458 0-DTE days):

| study / metric | 13:45 | 14:30 | note |
|---|---|---|---|
| speed — FAST (≤10m) 0-DTE aft, ≥1.5× / ≥2× | 21% / 7% (n=191) | 23% / **14%** (n=73) | tighter, ~⅓ the n |
| timing — ≤5-min-TRUE aft, ≥1.5× / ≥2× | 36% / 11% (n=47) | **41%** / **18%** (n=22) | same shape, half the n |
| premium — 0-DTE aft `peak≥3×`, TRUE vs WEAK vs FAKE-def | 2 / 3 / 8% | 2 / 6 / 11% | grade **still doesn't** top the ≥3× lottery |
| backtest §3 — afternoon MODEL @3% expectancy | −4.1% (n=69) | **−6.9%** (n=44) | negative at both, **worse** later |
| compression — expiry ρ(morning, aft_range) | +0.43 | +0.43 | still **persistence, not a coil** |

**Verdict — the freeze time is not load-bearing.** Moving to 14:30 tightens the speed/timing
distributions (the late-blast subset is a touch cleaner) but on **roughly half the crossings**, and it
changes **none** of the conclusions: the grade still fails to separate the premium payoff (§7b), the
afternoon backtest is still negative — in fact more negative (§7e), and the morning→afternoon
autocorrelation stays positive so the "quiet-morning coil" is still ordinary vol persistence. 13:45
remains the documented default (more data, no worse signal); 14:30 is a defensible but not superior
alternative. The knob stays so any freeze is one env var away, but the research verdict is unchanged.

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

India microstructure (who is on each side). The sign comparison is closed. Who hedges is unmeasured:

- SEBI — *93% of individual traders incurred losses in equity F&O, FY22–24*
  (Sept 2024):
  https://www.sebi.gov.in/media-and-notifications/press-releases/sep-2024/updated-sebi-study-reveals-93-of-individual-traders-incurred-losses-in-equity-fando-between-fy22-and-fy24-aggregate-losses-exceed-1-8-lakh-crores-over-three-years_86906.html

Background reading on a US GEX write-up. Not the production convention:

- SpotGamma — *GEX (Gamma Exposure) Explained*:
  https://support.spotgamma.com/hc/en-us/articles/15214161607827-GEX-Gamma-Exposure-Explained-What-It-Is-and-How-SpotGamma-Uses-It

> Caveat: the practitioner links describe the strategy as taught; they are **not**
> independent validation. The academic links study a hedge-and-volatility
> mechanism in other markets. They are not the production convention and they
> do not set the Nifty sign. Treat §3 and §4 as the governing reading.

---

## 🔗 Related Technical Documentation
- [🎯 Options Recommendation Strategies & Alpha Playbook](/?view=options_strategies_guide)
- [🎯 Target Engine — Scenario & Repricing Suite Technical Guide](/?view=target_engine_guide)
- [🔬 Options Analytics & Microstructure Intelligence Guide (IOAS v3.0)](/?view=options_analytics_guide)
- [⚡ OrderFlow Sentinel Stream Signals Field Guide](/?view=orderflow_guide)
- [🧭 Market Intelligence: Sector Breadth & News Sentiment Guide](/?view=market_intelligence_guide)
- [🎙️ Session Commentary & Suggestion Bites Guide](/?view=session_commentary_guide)
- [🌅 Pre/Post-Market Microstructure & Recommendations Lifecycle Guide](/?view=pre_post_market_guide)
- [📈 Signal Efficiency & MFE/MAE Performance Guide](/?view=signal_efficiency_guide)
- [📊 Quantitative Calculations & Formulas Reference](/?view=data_dictionary)
- [🧭 Participant Open Interest Guide](/?view=participant_guide)
- [📘 Master Cockpit User Guide](/?view=user_guide)
