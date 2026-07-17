# Vanna & Charm — the second-order dealer greeks (a plain-English guide)

This explains **vanna** and **charm** — what they are, why they move markets, how we compute
them, and what we found (Study #8: |vanna| is a sharper volatility-regime metric than |GEX|, now
wired into the dashboard gauge). It assumes you know GEX; if you know PCR and delta you'll be fine.

---

## 1. Why we sum "greek × open-interest" at all

Market makers (dealers) sit on the other side of the options everyone else trades. To avoid taking
a directional bet, they **hedge in the underlying** (buy/sell Nifty futures) to stay neutral.

The catch: their "neutral" hedge is only neutral *right now*. The moment **spot moves, vol moves,
or time passes**, their required hedge changes — and re-adjusting it means **buying or selling the
index**. Those forced hedging trades push the market. That is the whole game.

Each **greek** measures how sensitive an option's value (or hedge) is to one of those changes.
Multiply a greek by the **open interest** at each strike and sum across strikes, and you get the
**dealer exposure** — an estimate of *how much hedging flow* a given change will unleash:

```
Exposure(greek) = Σ_strikes  greek(strike) × (callOI − putOI)      [our model-A sign]
```

- **GEX** (gamma exposure) = how much dealers must re-hedge when **spot** moves. (You know this one.)
- **Vanna exposure** = how much they must re-hedge when **volatility** moves.
- **Charm exposure** = how much they must re-hedge simply because **time passed**.

> **One-line analogy for the whole family:** the dealer is a driver holding a car steady on a
> moving walkway. **Gamma** is how hard they steer when the walkway jolts (spot). **Vanna** is how
> the steering changes when the surface gets slippery (vol). **Charm** is the slow drift they must
> correct just because the walkway keeps creeping under them (time).

---

## 2. The greek cheat-sheet (from a trader's seat)

| Greek | Is the derivative… | Hedge flow triggered by… | Peaks at | Everyday analogy |
|-------|--------------------|--------------------------|----------|------------------|
| Delta | ∂Value/∂Spot | — (it *is* the hedge) | deep ITM | **speed** |
| **Gamma** | ∂Delta/∂Spot | a **spot** move | **ATM** | **acceleration** / shock-absorber |
| Vega | ∂Value/∂Vol | (position vol risk) | ATM (in ₹) | thermometer sensitivity |
| **Vanna** | ∂Delta/∂Vol = ∂Vega/∂Spot | a **vol** move | **OTM wings** | **steering-in-the-rain** |
| **Charm** | ∂Delta/∂Time | **time** passing | OTM, **near expiry** | **melting ice / hourglass** |

The two we care about are the *second-order cross-terms*: **vanna** couples vol to the spot hedge,
**charm** couples time to the spot hedge.

---

## 3. Vanna — the vol↔spot coupling

### Concept
**Vanna = ∂Delta/∂Vol.** It answers: *"if implied vol changes, how much does my delta hedge
change?"* Because delta is the hedge, a big vanna means **a move in IV forces the dealer to trade
the index** — even if spot itself didn't move. That links the vol market to the spot market.

### Analogy
Picture cruise-control that also reacts to how **wet the road** is. Vanna is *how much the steering
auto-corrects when the road gets wetter* (vol up) or drier (vol down). When vanna exposure is
large, a drop in IV makes dealers **buy** the index and a spike in IV makes them **sell** — the
classic **"vanna rally"** (calm tape + falling VIX quietly levitating the index) and its ugly twin,
the vol-spike air-pocket.

### The shape — why vanna is *not* just gamma
This is the key insight behind our finding. With `d1, d2` the standard Black-Scholes terms:

```
gamma = φ(d1) / (S · σ · √T)          ← biggest AT the money (d1 ≈ 0 ⇒ φ(d1) is max)
vanna = − φ(d1) · d2 / σ              ← ZERO at the money (d2 ≈ 0), biggest in the OTM WINGS
```

So **gamma is an ATM meter, vanna is an OTM-wings meter.** Vanna automatically **up-weights the
big out-of-the-money walls** (where the fat open interest actually sits) and ignores the ATM strike.
It turns out that OTM-wall structure predicts the next hour's realized vol *better* than the
ATM-centric gamma sum — which is exactly why |vanna| beat |GEX| in the screen.

### One nuance we had to control for
Vanna has a `1/σ` in it, so **|vanna| is mechanically large when IV is low** — and low IV trivially
means low realized vol. That could have been "IV in disguise." We controlled for it (partial
correlation vs ATM IV) and vanna *still* beat GEX beyond IV — so the OTM-wing structure carries real
information, not just the IV level. (Details in §6.)

---

## 4. Charm — the time-decay drift

### Concept
**Charm = ∂Delta/∂Time.** As expiry approaches, option deltas **drift on their own**: an OTM
option's delta bleeds toward 0, an ITM option's toward ±1 — *even if spot and vol are frozen*. That
drifting delta forces the dealer into a **steady, one-directional re-hedge** that gets stronger the
closer you are to expiry. This is the engine behind **expiry-week / Friday-afternoon drift**
("charm flow").

### Analogy
**Melting ice.** Even if you touch nothing, the dealer's hedge slowly melts as the clock ticks, and
they must keep topping it up in the *same* direction. Or an **hourglass**: sand runs at a steady
rate, faster as the neck (expiry) nears — a predictable, time-driven pull, not a reaction to news.

### The math (our r = q = 0 form)
```
charm ≈ φ(d1) · d2 / (2T)            ← blows up as T → 0 (∝ 1/T): huge near expiry
```
Like vanna it's a wings metric (∝ d2, zero at ATM), but it's dominated by the `1/T` term, so it's
almost entirely a **near-expiry** phenomenon.

---

## 5. How we compute them (the code)

All from the same per-strike inputs we already have (strike `K`, spot `S`, per-strike implied vol
`σ` from the OTM-side smile, time-to-expiry `T`, and call/put OI). We use `r = q = 0` (the engine's
convention). With `φ` = standard-normal pdf, `N` = cdf:

```
d1 = [ ln(S/K) + ½ σ² T ] / (σ √T)
d2 = d1 − σ √T

gamma =  φ(d1) / (S σ √T)
vanna = −φ(d1) · d2 / σ
charm =  φ(d1) · d2 / (2T)
vega  =  S · φ(d1) · √T
```

Dealer exposure = the OI-weighted sum (model-A sign: `+call − put`):

```
GEX      = Σ  gamma · (callOI − putOI) · S² · 0.01     app/intraday_features.py::day_features
VannaEX  = Σ  vanna · (callOI − putOI)                 (× lot on the live side — see below)
CharmEX  = Σ  charm · (callOI − putOI)                 scripts/greek_exposure_screen.py
```

**Where it lives:**
- `scripts/greek_exposure_screen.py` — computes the full family (delta/vanna/charm/vega/GEX +
  key-level variants) per 5-min stamp and screens their predictive IC. This is the research tool.
- `app/intraday_features.py::day_features` — computes `net_gex` **and** `vanna` into the 5-min
  feature cache (the distribution the gauge percentiles against).
- `app/data_access.py::_live_vanna` — the **live** dealer-vanna exposure from the current NSE chain.

**The one scale subtlety (live vs cache):** the Dhan cache stores OI in *shares* (contracts × lot),
while the live NSE chain reports OI in *contracts*. So the live vanna is multiplied by the **lot
size** to land on the same scale as the cached distribution — the same bridge we use for GEX.

---

## 6. What we found (Study #8) — and why it matters

Point-in-time screen, NIFTY-W + BankNifty, full 5-yr sample. The decisive column is
**`|·| → forward-vol | IV`** (does the metric predict the next 30-min realized vol *beyond* what IV
already says?):

| metric | NIFTY-W \|IV | BankNifty \|IV | direction (signed→ret) |
|--------|-------------:|--------------:|:----------------------:|
| **VANNA** | **−0.187** | **−0.190** | ~0 |
| GEX (old gauge) | −0.128 | −0.169 | ~0 |
| CHARM | −0.228 | −0.182 | ~0 |
| GEXpeak (single peak-OI strike) | −0.141 | — | ~0 |
| VEGA | +0.080 (junk) | −0.149 | ~0 |

**Reading it:**
- **|Vanna| beats |GEX|** for forward-vol prediction in **both** indices (strongly on NIFTY, modestly
  on BankNifty), surviving both the IV and DTE controls → it's a genuinely sharper **vol-regime**
  metric. Now the default metric behind the dashboard's Market-regime gauge.
- **Charm** is even stronger on NIFTY (−0.228) but **inconsistent** across books and unstable (its
  `1/T` term makes it a near-expiry-only signal), so we did *not* productionize it — it's a noted
  future refinement.
- **GEXpeak** (gamma at just the single dominant-OI strike) beats aggregate GEX (−0.141 vs −0.088),
  confirming the intuition that **GEX at key levels** carries more signal than the average — but
  still below vanna.
- **Vega** is pure IV (flips positive under the IV control) — not useful.

### The honest caveats (same as everything else in this project)
1. **Direction is dead.** Every greek's `signed → return` IC is ~0. Vanna/charm predict **how wild**
   the next move is (vol), **not which way** (direction). There is no directional edge here.
2. **It's a regime read, not alpha.** Since it only forecasts vol, and vol is priced into option
   premiums (see the straddle studies #6b/#7), you can't *trade* it — you can only *read* the
   regime with it. That's precisely what the gauge does.
3. **Sign landmine.** The `+call − put` sign is the unvalidated dealer-sign convention, so we use
   the **magnitude** |vanna| for the regime (sign-free), never the signed value for direction.

---

## TL;DR
- **Gamma** = re-hedge flow from a **spot** move (ATM-centric) → the old GEX gauge.
- **Vanna** = re-hedge flow from a **vol** move (OTM-wings) → **a ~2× sharper vol-regime metric**,
  now the gauge default. Analogy: steering that auto-corrects as the road gets wet/dry.
- **Charm** = re-hedge flow from **time** passing (near-expiry drift) → strong but unstable; a
  future refinement. Analogy: melting ice / an hourglass.
- All three tell you **how volatile**, never **which direction** — a regime map, not a trade signal.
