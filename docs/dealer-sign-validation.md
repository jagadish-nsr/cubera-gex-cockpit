# Dealer-sign validation — historical record

**Production status (2026-10-24).** This file records an old two-convention test.
The engine now rejects every sign model except `short_call_short_put`. The signed
total is call-gamma magnitude minus put-gamma magnitude. Do not set
`DEALER_SIGN_MODEL` back to `long_call_short_put`. The statistics below are not
a reason to restore a second model. Who delta-hedges on NSE is still unmeasured.

Status at the time of the study: a weak, non-significant lean, not a verdict.
Reproduce with `python scripts/analyze_timeseries.py --both-signs`. **Decision-
support only.** This is a *weak, non-significant lean*, **not** a verdict.

---

## 0. TL;DR

There is a **weak, non-significant lean toward INVERSION** — i.e. the data leans
toward: the engine's **NEGATIVE** label behaving *calm/pinned* and its
**POSITIVE** label behaving *trendy/wild* — the opposite of the US convention the
engine ships with. Two independent axes (gamma-bucket **volatility** and **PCR**)
point the same way. **But** it is statistically insignificant, the
**momentum/autocorrelation** evidence pulls the *other* way, magnet-pinning
itself was not demonstrated, and **every captured day is contaminated by the
Israel–Iran war**. **Dealer sign stays UNVALIDATED.** Do not trade a flipped sign.

> **Refresh 2026-07-01 (n=12 usable days, 290 intervals):** the lean **holds** —
> convention **B (inverted) scores 2/3 vs A 1/3**; G+ (net_gex>0) is the wilder
> bucket and the **sign-free PCR tercile check independently corroborates B**
> (low-PCR/call-heavy is wildest). Still **not significant** (Levene p ≈ 0.43).
> The sentences below describe the study as it was run. They are not the
> current engine. Production accepts only `short_call_short_put`. Do not put
> the config back on a US reading, and do not add a second convention because
> this lean was weak.

---

## 1. The question

The study scored a retired US reading, `long_call_short_put`, which *predicted*:

| Engine regime | Dealer gamma | Predicted behaviour |
|---|---|---|
| **POSITIVE** (net_gex > 0) | long | **pinned / calm** (dealers fade moves) |
| **NEGATIVE** (net_gex < 0) | short | **trendy / wild** (dealers amplify) |

**Inversion** (convention `short_call_long_put`) would flip the *reading* of the
current labels to: **POSITIVE ≈ trendy**, **NEGATIVE ≈ calm/pinned**.

Because GEX is *linear* in the dealer sign, flipping the convention **negates
`net_gex` and inverts the regime label EXACTLY** (flip-location, magnet, max-pain
invariant; walls swap) — verified numerically. So both conventions can be scored
on the historical captures by **relabelling** fixed net-gamma-sign buckets, with
no need for the raw chains.

---

## 2. Data

- ~13 captured days, **10 usable** (Jun 2026). One day (06-26) was a **frozen
  feed** dropped by hygiene.
- Quarantine **OFF** here on purpose (working thesis: news is baked into ΔOI/gamma,
  so the regime label encodes it). Note the separate quarantine view found *every*
  day news-dominated, leaving a clean set of zero — see
  `[[news-gauge-quarantine]]`.
- **Confound:** the whole window sits inside the Israel–Iran/oil war; India VIX
  bled 16.9 → 13.0 over the period. Direction was largely exogenous.

---

## 3. Result A — realised volatility by regime (the discriminating test)

Buckets are FIXED by net-dealer-gamma sign under the US convention (G+ = POSITIVE,
G− = NEGATIVE). The model wants the **short-gamma** bucket to be the wilder one.

| Bucket | n (15-min rets) | realised vol | reading |
|---|---|---|---|
| **G+ = POSITIVE** (net_gex>0) | 171 | **0.096%** (wilder) | behaves *trendy* → inversion |
| **G− = NEGATIVE** (net_gex<0) | 73 | 0.077% (calmer) | behaves *pinned* → inversion |

- US convention A says G+ should be the **calm** bucket; it is the **wild** one →
  **contradicts A**, **consistent with B (inverted)**.
- **Significance: NONE.** Levene test p = **0.48** — the gap is indistinguishable
  from no difference at n=10.

**Convention scorecard** (3 model predictions): **B (inverted) 2/3 vs A (US) 1/3** —
but driven entirely by the (insignificant) volatility leg.

---

## 4. Result B — autocorrelation (pulls the OTHER way; underpowered)

Lag-1 autocorrelation of 15-min returns within a regime (positive = momentum =
"trendy"):

| Bucket | pairs | lag-1 autocorr |
|---|---|---|
| G+ = POSITIVE | 146 | +0.05 |
| G− = NEGATIVE | 54 | +0.15 |

The **NEGATIVE** bucket looked *more* trending — which points **back toward the US
convention** (NEGATIVE = momentum), the opposite of the volatility leg. Both
values are within ~2·SE of zero, so **neither resolves**. The vol and momentum
signatures **disagree**, and both are underpowered.

---

## 5. Result C — magnet pinning was NOT demonstrated

"Calmer" ≠ "pinned to the magnet strike." A direct day-level test of magnet
attraction:

> Close moved **toward** the magnet on only **4/10** days; mean pull **+4 pts**
> (i.e. slightly *away*).

Essentially a coin-flip → **no magnet attraction shown for either regime.** What
we have is *lower realised vol* in the NEGATIVE/put-heavy state, not evidence price
is tractor-beamed to the magnet.

---

## 6. Result D — PCR (sign-free independent check)

PCR (put/call OI) owes nothing to the dealer-sign assumption.

1. **Vol by PCR tercile (corroborates inversion):** low PCR (call-heavy) is the
   **wildest** tape (rv 0.114%); high PCR (put-heavy) is calmer (~0.078%, mid
   0.072%). Low-PCR/call-heavy *is* the G+/POSITIVE state → independently says
   POSITIVE behaves wild = **inversion**.
2. **PCR overlaps regime (real signal):** mean PCR is **1.02** in POSITIVE vs
   **1.16** in NEGATIVE — put-heavier when short-gamma-labelled.
3. **PCR vs direction (separate, contrarian):** corr(open PCR, day return) =
   **−0.52** — high PCR preceded *down* days. Contrarian to the "high PCR =
   bullish put-writing" lore; suggests the marginal put flow was *protection
   buying* (a war signature). Not a gamma-sign test; n=10, not significant.
4. **ΔOI skew is a dud:** fresh put-vs-call OI building vs next 15-min return =
   **+0.02** — no directional lead-lag from *aggregate* ΔOI. (Per-strike ΔOI, now
   captured, may do better — see `[[gamma-blast-feature]]`.)

---

## 7. Verdict

Two independent axes — gamma-bucket **volatility** and **PCR** — lean the **same
way: toward inversion** (NEGATIVE → calm/pinned, POSITIVE → trendy/wild). That
agreement is worth something.

**But:**
- The lean is **statistically insignificant** (Levene p = 0.48; autocorr within
  noise).
- The **momentum** evidence contradicts the **volatility** evidence.
- **Magnet-pinning itself was not shown** (4/10, coin-flip).
- **Every day is war-confounded.**

**Dealer sign stays UNVALIDATED** — a weak, consistent *hint* of inversion, not a
confirmed flip. The dashboard correctly continues to warn that the regime could be
inverted.

### To settle it
- Capture many more **non-war, full-session** days (including the open).
- Use the **per-strike ΔOI + gamma** capture (`[[gamma-blast-feature]]`) for a
  sharper pin-vs-blast test than aggregate ΔOI allows.
- Cross-check against an independent third-party Indian dealer-positioning read.

---

## 8. Reproduce / related

- `python scripts/analyze_timeseries.py --both-signs` — Results A, B, D.
- `python scripts/analyze_timeseries.py` — per-day structure + the quarantine
  view.
- Memory: `[[both-signs-dealer-sign-read]]`, `[[news-gauge-quarantine]]`,
  `[[timeseries-market-structure-direction]]`.
