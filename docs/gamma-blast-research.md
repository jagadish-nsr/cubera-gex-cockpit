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
