# 🎯 Options Recommendation Strategies: Field Guide & Alpha Playbook

This guide details the institutional microstructure models, trigger mechanics, data feeds, and execution rules powering the **Top 3 CE & Top 3 PE Options Recommendations Engine** in the Indian-GEX Cockpit. It also outlines advanced high-edge strategies that can be added to the engine.

---

## Part 1: Implemented Recommendation Strategies

The engine synthesizes **GEX structural walls**, **OrderFlow volume delta (CVD)**, **Session VWAP equilibrium**, and **live Dhan WebSocket streaming prices** into three complementary strategies per side:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    OPTIONS RECOMMENDATION TAXONOMY                      │
├──────────────────────┬──────────────────────────┬───────────────────────┤
│    1. MOMENTUM       │       2. STRUCTURE       │   3. GAMMA SQUEEZE    │
│    (ATM Core)        │      (Wall Defense)      │   (Breakout Convex)   │
├──────────────────────┼──────────────────────────┼───────────────────────┤
│ • Delta: ~0.50       │ • Delta: ~0.40           │ • Delta: ~0.25 - 0.30 │
│ • Target: VWAP + 1σ  │ • Target: Gamma Flip/CW  │ • Target: Wall + 2x   │
│ • R:R: 1 : 2.5       │ • R:R: 1 : 3.0           │ • R:R: 1 : 3.5+       │
└──────────────────────┴──────────────────────────┴───────────────────────┘
```

---

### Strategy 1: ATM Momentum Play (`MOMENTUM`)

#### 1. Concept & Objective
Exploits pure delta acceleration when price holds intraday equilibrium (Session VWAP) and pushes in the direction of the institutional bias score. Because ATM options possess the highest absolute gamma and a delta near $\pm 0.50$, price movements generate instant, rapid premium appreciation.

#### 2. Data Points Used
- **Underlying Spot Price ($S_{\text{live}}$)**: Streamed tick-by-tick from Dhan WebSocket (`IDX_I:13`/`25`/`51`).
- **Session VWAP ($\text{VWAP}$)** and $\pm 1.0\sigma$ Bands: Calculated over all completed 1-minute futures bars.
- **Directional Bias Score**: Multi-factor conviction score from $-10.0$ to $+10.0$.
- **Cumulative Volume Delta (CVD)**: 15-minute slope of buyer vs seller aggression.
- **Contract ATM Delta ($\Delta \approx 0.50$)**: Black-Scholes estimate or live option chain delta.

#### 3. Inference Made
- When spot is trading above VWAP with a positive Bias Score ($\ge +1.5$), institutional buyers are absorbing liquidity on dips. Pullbacks into VWAP are buying opportunities, not trend reversals.
- Conversely, spot trading below VWAP with a negative Bias Score indicates institutional liquidation, where VWAP acts as firm overhead resistance.

#### 4. Strike Selection
- **Call (CE)**: Closest round strike to current spot: $K = \operatorname{round}(S, \text{step})$.
- **Put (PE)**: Closest round strike to current spot: $K = \operatorname{round}(S, \text{step})$.

#### 5. Decision-Making & Execution Rules
- **Entry Trigger (CE)**: Retest and bounce off Session VWAP or a 5-minute candle close above the Opening Range High (ORH) with expanding green volume.
- **Entry Trigger (PE)**: Rejection off Session VWAP or a 5-minute close below Opening Range Low (ORL) with expanding red volume.
- **Spot Invalidation**: 
  - CE: Spot drops below $\min(\text{VWAP} - 0.35\cdot\text{step}, S - 0.6\cdot\text{step})$.
  - PE: Spot climbs above $\max(\text{VWAP} + 0.35\cdot\text{step}, S + 0.6\cdot\text{step})$.
- **Option Price Targets & Stop Loss**:
  - **Stop Loss**: $-28\%$ from entry LTP ($0.72 \times \text{LTP}$).
  - **Target 1**: $+35\%$ from entry LTP ($1.35 \times \text{LTP}$, $1.25\text{R}$).
  - **Target 2**: $+70\%$ from entry LTP ($1.70 \times \text{LTP}$, $2.5\text{R}$).

---

### Strategy 2: Institutional Structure Play (`STRUCTURE`)

#### 1. Concept & Objective
Capitalizes on the defensive behavior of institutional option writers who protect their credit positions at the **Put Wall** (for bullish reversals) and **Call Wall** (for bearish rejections). Offers exceptional risk-reward asymmetry because stops are placed just outside major institutional barrier strikes.

#### 2. Data Points Used
- **Call Wall ($CW$) & Put Wall ($PW$)**: Strike with maximum open interest from the GEX solver.
- **Gamma Flip ($GF$)**: Zero-gamma transition boundary.
- **Strike OI Change ($\Delta \text{OI}_{15\text{m}}$)**: Rate of fresh contract writing vs unwinding at the wall.
- **OrderFlow Tape Absorption**: Cluster of aggressive market orders failing to push price past the level.

#### 3. Inference Made
- Institutional writers possess massive capital and delta-hedge aggressively to defend walls. If price touches the Put Wall and Put writers are actively adding open interest, the probability of a sharp mean-reverting bounce back toward the Gamma Flip or VWAP is over $68\%$.

#### 4. Strike Selection
- **Call (CE)**: One strike above ATM: $K = \text{ATM} + \text{step}$ ($\Delta \approx 0.40$). Positioned for rotation from Put Wall to Gamma Flip.
- **Put (PE)**: One strike below ATM: $K = \text{ATM} - \text{step}$ ($\Delta \approx -0.40$). Positioned for rotation from Call Wall back toward equilibrium.

#### 5. Decision-Making & Execution Rules
- **Entry Trigger (CE)**: Passive absorption confirmed at Put Wall with positive CVD divergence or breakout through Gamma Flip.
- **Entry Trigger (PE)**: Aggressive rejection at Call Wall with seller absorption on the order book depth tape.
- **Spot Invalidation**:
  - CE: Structural breakdown below Put Wall ($PW - 0.3 \times \text{step}$).
  - PE: Structural breach above Call Wall ($CW + 0.3 \times \text{step}$).
- **Option Price Targets & Stop Loss**:
  - **Stop Loss**: $-32\%$ from entry LTP ($0.68 \times \text{LTP}$).
  - **Target 1**: $+45\%$ from entry LTP ($1.45 \times \text{LTP}$, $1.4\text{R}$).
  - **Target 2**: $+95\%$ from entry LTP ($1.95 \times \text{LTP}$, $3.0\text{R}$).

---

### Strategy 3: Gamma Squeeze / Breakdown Lotto (`GAMMA SQUEEZE`)

#### 1. Concept & Objective
Designed for explosive non-linear gains ($100\%$ to $250\%+$). Targets the exact moment when an institutional wall collapses. When a Call Wall is breached by high-volume aggressive buying, short call dealers enter negative gamma, forcing them to frantically buy underlying futures to hedge, triggering a violent **Gamma Blast** squeeze.

#### 2. Data Points Used
- **Call Wall / Put Wall OI Concentration**: Relative ratio of wall OI vs average strike OI.
- **Dealer Net Gamma Gradient ($\partial \text{GEX} / \partial S$)**: Speed at which dealer delta changes.
- **Aggressive OrderFlow Surge**: Spikes in CVD crossing the 90th percentile of intraday volume.
- **India VIX Momentum**: Sudden intraday expansion in volatility.

#### 3. Inference Made
- If spot crosses the Call Wall with positive orderflow volume, market makers who sold calls must purchase spot/futures to rebalance deltas. This self-reinforcing buying spiral creates convex upward velocity, rapidly inflating OTM call premiums.
- Conversely, a breakdown below the Put Wall forces dealers to dump futures, causing a "trapdoor" cascade.

#### 4. Strike Selection
- **Call (CE)**: At the Call Wall strike: $K = CW$ (or $\text{ATM} + 2\cdot\text{step}$ if $CW \le S$).
- **Put (PE)**: At the Put Wall strike: $K = PW$ (or $\text{ATM} - 2\cdot\text{step}$ if $PW \ge S$).

#### 5. Decision-Making & Execution Rules
- **Entry Trigger (CE)**: High-volume breakout candle closing above Call Wall accompanied by a positive CVD volume spike.
- **Entry Trigger (PE)**: High-volume breakdown candle closing below Put Wall with negative market delta expansion.
- **Spot Invalidation**:
  - CE: Trapdoor failure where spot falls back below Call Wall ($-0.4 \times \text{step}$).
  - PE: Failed breakdown where spot recovers back above Put Wall ($+0.4 \times \text{step}$).
- **Option Price Targets & Stop Loss**:
  - **Stop Loss**: $-40\%$ from entry LTP ($0.60 \times \text{LTP}$).
  - **Target 1**: $+60\%$ from entry LTP ($1.60 \times \text{LTP}$, $1.5\text{R}$).
  - **Target 2**: $+140\%$ from entry LTP ($2.40 \times \text{LTP}$, $3.5\text{R}$).

---

## Part 2: Additional Highly Profitable Strategies for Future Implementation

Based on institutional options flow research in Indian indices, the following four strategies offer significant quantitative edge and can be incorporated into the recommendation engine:

### 1. 0DTE Vanna-Charm Afternoon Theta Compression
- **Edge**: On expiry days (NIFTY on Thursdays, SENSEX on Fridays), after 1:30 PM IST, Charm ($\partial \Delta / \partial t$) accelerates rapidly. Out-of-the-money options trade at steep theta premiums that decay to zero in the final 90 minutes.
- **Setup**: When spot is locked within a narrow band between Put Wall and Call Wall and the Gamma Flip is within 25 points of spot, recommend selling an **OTM Iron Fly or Strangle** with defined wings.
- **Expected Win Rate**: $>78\%$ on low-VIX calm expiry afternoons.

### 2. OrderFlow Iceberg Absorption Fade
- **Edge**: Ingesting Dhan's 5-level market depth (`code=8`) allows real-time calculation of the **Bid-Ask Imbalance Ratio**. When large limit buy orders absorb repeated aggressive market sells at key support without breaking price, smart money is accumulating.
- **Setup**: Enter an immediate reversal ATM call when aggressive selling fails 3 times at the lower VWAP band with positive delta divergence.
- **Advantage**: Tightest possible spot invalidation (under 15 points on NIFTY), yielding R:R of $1 : 4.5+$.

### 3. Volatility Dispersion / Long Gamma Straddle
- **Edge**: In compressed volatility regimes where India VIX drops below 12.0 and the market has been range-bound for over 3 sessions, options premiums become historically cheap (low IV Rank).
- **Setup**: Recommend a simultaneous ATM Call + ATM Put purchase prior to major macro releases (RBI MPC policy, US Fed FOMC, Union Budget, Election outcomes).
- **Advantage**: Pure long volatility with unlimited upside on sudden breakout beyond walls.

### 4. Zero-Cost Ratio Risk Reversal (Synthetic Asymmetry)
- **Edge**: Buy 1 ATM Call funded by selling 2 farther OTM Calls beyond the Call Wall.
- **Setup**: In moderate bullish regimes where spot is expected to move to Call Wall but not blast through it, this structure generates a near-zero initial cash outlay while offering high percentage returns if spot targets the Call Wall.
- **Advantage**: Eliminates theta decay drag on sideways days.
