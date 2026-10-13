# 🎯 Options Recommendation Strategies: Field Guide & Alpha Playbook

This guide details the institutional microstructure models, trigger mechanics, data feeds, and execution rules powering the **Options Recommendations Engine (Score ≥ 75)** in the Indian-GEX Cockpit.

---

## Part 1: Implemented Recommendation Strategies & Quality Gating

The engine synthesizes **GEX structural walls**, **OrderFlow volume delta (CVD)**, **Session VWAP equilibrium**, and **live Dhan WebSocket streaming prices** across seven complementary institutional strategies per side.

### 🛡️ Quality Gating: Score ≥ 75 (High Conviction Only)
Rather than displaying a static number of trade ideas regardless of quality, the engine evaluates **all 7 strategies** for Calls (CE) and Puts (PE) and applies an institutional quality gate:
* **Only setups achieving Quality Score ≥ 75 (Grade A / A+ High Conviction)** are presented in the primary recommendation cards.
* In flat, choppy, or low-conviction market regimes where no setup achieves $\ge 75$, the terminal activates **Capital Preservation Mode**, advising traders to stand aside.
* Traders can expand the **Lower-Conviction Watchlist (< 75 Score)** anytime to inspect candidate setups under formation.

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       OPTIONS RECOMMENDATION TAXONOMY                                          │
├──────────────────────┬──────────────────────────┬───────────────────────┬──────────────────────────────────────┤
│ Strategy             │ Strike & Delta           │ R:R Ratio             │ Core Edge / Trigger                  │
├──────────────────────┼──────────────────────────┼───────────────────────┼──────────────────────────────────────┤
│ 1. MOMENTUM          │ ATM (Δ ~0.50)            │ 1 : 2.5               │ Retest & bounce off Session VWAP     │
│ 2. STRUCTURE         │ ATM ± 1 step (Δ ~0.40)   │ 1 : 3.0               │ Wall defense & reversal bounce       │
│ 3. GAMMA SQUEEZE     │ At GEX Wall (Δ ~0.28)    │ 1 : 3.5               │ Breach of Wall + aggressive CVD push │
│ 4. ICEBERG FADE      │ ATM (Δ ~0.50)            │ 1 : 4.5               │ Passive limit absorption at ±1σ VWAP │
│ 5. VANNA-CHARM       │ OTM ± 2 steps (Δ ~0.20)  │ 1 : 2.0               │ Expiry afternoon theta compression   │
│ 6. VOL DISPERSION    │ ATM (Δ ~0.50)            │ 1 : 4.0               │ Compression breakout / long gamma    │
│ 7. RATIO REVERSAL    │ ATM ± 1 step (Δ ~0.40)   │ 1 : 3.0               │ Moderate trending wall rotation      │
└──────────────────────┴──────────────────────────┴───────────────────────┴──────────────────────────────────────┘
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
- **Option Price Targets & Stop Loss (Target Engine Repriced)**:
  - **Dynamic Calculation**: Evaluated via **Target Engine Black-76 analytical forward repricing** (`_reprice_trade_idea_targets` in `analytics/trade_ideas.py`).
  - **Stop Loss ($V_{\text{sl}}$)**: Modeled by repricing the option contract at the exact **Spot Invalidation** level under adverse IV shock $\sigma_{\text{sl}}$ and holding horizon time decay $T_{\text{sl}}$.
  - **Target 1 ($V_{\text{tgt1}}$) & Target 2 ($V_{\text{tgt2}}$)**: Repriced at the primary session target level and secondary momentum expansion level.
  - *Legacy Baseline Benchmark*: Prior to Target Engine integration, the heuristic baseline assumed approximately $-28\%$ from entry LTP ($0.72 \times \text{LTP}$) for stops, $+35\%$ for Target 1 ($1.35 \times \text{LTP}$), and $+70\%$ for Target 2 ($1.70 \times \text{LTP}$). Today, exact prices and Net EV are dynamically calibrated to live volatility and the underlying invalidation boundary.

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

### Strategy 4: OrderFlow Iceberg Absorption Fade (`ICEBERG FADE`)

#### 1. Concept & Objective
Capitalizes on passive institutional liquidity absorption occurring at the outer Session VWAP $\pm 1\sigma$ envelopes. When aggressive market orders fail to press prices beyond these bands, smart money limit orders are taking the other side of the trade, creating an ultra-asymmetric mean-reversion opportunity.

#### 2. Data Points Used
- **Session VWAP $\pm 1\sigma$ Envelopes**: Calculated dynamically across completed bars.
- **OrderFlow Delta Divergence**: Aggressive selling occurring while spot holds or ticks higher (bullish absorption), or aggressive buying while spot refuses to rise (bearish absorption).
- **Tape Absorption Signals**: Microstructure signals from OrderFlow Sentinel.

#### 3. Strike Selection
- **Call (CE)**: ATM Strike: $K = \text{ATM}$ ($\Delta \approx 0.50$).
- **Put (PE)**: ATM Strike: $K = \text{ATM}$ ($\Delta \approx -0.50$).

#### 4. Decision-Making & Execution Rules
- **Entry Trigger (CE)**: Passive limit buyer absorption confirmed at lower VWAP envelope with positive CVD delta divergence.
- **Entry Trigger (PE)**: Passive limit seller absorption confirmed at upper VWAP envelope with negative CVD delta divergence.
- **Spot Invalidation**:
  - CE: Spot breakdown below lower VWAP band by $>0.25 \times \text{step}$ ($<15\text{ pts}$ on NIFTY).
  - PE: Spot breakout above upper VWAP band by $>0.25 \times \text{step}$.
- **Option Price Targets & Stop Loss**:
  - **Stop Loss**: $-20\%$ from entry LTP ($0.80 \times \text{LTP}$, tightest stop).
  - **Target 1**: $+40\%$ from entry LTP ($1.40 \times \text{LTP}$, $2.0\text{R}$, at VWAP).
  - **Target 2**: $+90\%$ from entry LTP ($1.90 \times \text{LTP}$, $4.5\text{R}$, at opposite band).

---

### Strategy 5: 0DTE Vanna-Charm Afternoon Theta Compression (`VANNA-CHARM`)

#### 1. Concept & Objective
Exploits second-order Greek acceleration ($\partial \Delta / \partial t$) during expiry sessions or late-cycle market sessions. When spot is pinned between the Put Wall and Call Wall with Gamma Flip near spot, dealer delta hedging dampens volatility, causing OTM options premiums to collapse towards zero.

#### 2. Data Points Used
- **Days to Expiration (DTE)**: High edge within 1 session of contract settlement.
- **Gamma Flip & Wall Distance**: Spot tightly contained within $PW$ and $CW$ and within $0.5 \times \text{step}$ of Flip.
- **Realized Volatility**: Intraday ATR compression indicating range lock.

#### 3. Strike Selection
- **Call (CE)**: OTM Strike at $\text{ATM} + 2 \times \text{step}$ ($\Delta \approx 0.20$).
- **Put (PE)**: OTM Strike at $\text{ATM} - 2 \times \text{step}$ ($\Delta \approx -0.20$).

#### 4. Decision-Making & Execution Rules
- **Entry Trigger (CE/PE)**: Spot pinned between major walls with low realized volatility, monetizing rapid premium collapse.
- **Spot Invalidation**: Volatility expansion breaking through the outer wall ($CW + 0.3 \times \text{step}$ or $PW - 0.3 \times \text{step}$).
- **Option Price Targets & Stop Loss**:
  - **Stop Loss**: $-35\%$ from entry LTP ($0.65 \times \text{LTP}$).
  - **Target 1**: $+30\%$ from entry LTP ($1.30 \times \text{LTP}$).
  - **Target 2**: $+60\%$ from entry LTP ($1.60 \times \text{LTP}$).

---

### Strategy 6: Volatility Dispersion / Long Gamma Explosion (`VOL DISPERSION`)

#### 1. Concept & Objective
Deployed during compressed volatility regimes where India VIX drops or market consolidation has persisted inside narrow bands. Options trade at low implied volatility, making gamma convexity unusually cheap. Once spot breaks out of the consolidation band, simultaneous vega expansion and gamma explosion generate outsized gains.

#### 2. Data Points Used
- **India VIX & Realized Volatility**: Low baseline volatility or multi-hour range contraction.
- **VWAP Envelope Bandwidth**: Squeeze in the distance between $+1\sigma$ and $-1\sigma$ bands.
- **Volume Expansion Surge**: Sudden influx of volume breaking past consolidation boundaries.

#### 3. Strike Selection
- **Call (CE)**: ATM Strike: $K = \text{ATM}$ ($\Delta \approx 0.50$).
- **Put (PE)**: ATM Strike: $K = \text{ATM}$ ($\Delta \approx -0.50$).

#### 4. Decision-Making & Execution Rules
- **Entry Trigger (CE)**: Compression breakout above Session VWAP $+0.5\sigma$ with expanding green volume.
- **Entry Trigger (PE)**: Compression breakdown below Session VWAP $-0.5\sigma$ with expanding red volume.
- **Spot Invalidation**: Spot falls back into the consolidation band midpoint ($\text{VWAP} \pm 0.4 \times \text{step}$).
- **Option Price Targets & Stop Loss**:
  - **Stop Loss**: $-30\%$ from entry LTP ($0.70 \times \text{LTP}$).
  - **Target 1**: $+50\%$ from entry LTP ($1.50 \times \text{LTP}$, $1.67\text{R}$).
  - **Target 2**: $+120\%$ from entry LTP ($2.20 \times \text{LTP}$, $4.0\text{R}$).

---

### Strategy 7: Zero-Cost Ratio Risk Reversal (`RATIO REVERSAL`)

#### 1. Concept & Objective
Synthetic 1x2 ratio structure where buying 1 near-money option is funded by the expected resistance of the wall. Formulated for steady trending regimes where spot is expected to move toward the Call Wall or Put Wall without blasting through it, providing high percentage returns with zero theta drag on sideways chop.

#### 2. Data Points Used
- **Directional Bias**: Moderate conviction score ($+1.0$ to $+3.5$ for CE; $-1.0$ to $-3.5$ for PE).
- **Distance to Target Wall**: Sweet spot of $1.5$ to $3.0$ strike steps away from the target wall.
- **Institutional Accumulation/Distribution**: Steady tape buildup towards the wall.

#### 3. Strike Selection
- **Call (CE)**: Strike at $\text{ATM} + \text{step}$ ($\Delta \approx 0.40$), targeting Call Wall rotation.
- **Put (PE)**: Strike at $\text{ATM} - \text{step}$ ($\Delta \approx -0.40$), targeting Put Wall rotation.

#### 4. Decision-Making & Execution Rules
- **Entry Trigger (CE)**: Moderate upward drift with steady buyer accumulation targeting the Call Wall.
- **Entry Trigger (PE)**: Moderate downward drift with steady seller distribution targeting the Put Wall.
- **Spot Invalidation**: Spot reversal across VWAP by $>0.35 \times \text{step}$.
- **Option Price Targets & Stop Loss**:
  - **Stop Loss**: Calibrated by Target Engine Black-76 repricing at Spot Invalidation.
  - **Target 1**: Repriced at primary wall rotation barrier.
  - **Target 2**: Repriced at secondary volatility expansion move.

---

## Part 2: Target Engine Quantitative Repricing Architecture

In the latest release, the Recommendations Engine has been upgraded from crude static heuristic multipliers (`* 1.35`, `* 0.72`) to the **Target Engine Quantitative Suite**:

### 1. Black-76 Forward Pricing & Repricing
Every trade recommendation computes `Target 1`, `Target 2`, and `Stop Loss` using analytical Black-76 forward pricing:
- **Baseline Pricing**: Initial option entry premium $V_0$ is derived from current underlying spot/futures $F_0$, strike $K$, annualized time to expiry $T_0$, and ATM implied volatility $\sigma_{\text{atm}}$.
- **Forward Invalidation & Target Repricing**:
  - **Target 1 Premium ($V_{\text{tgt1}}$)**: Repriced at $F = \text{Target Spot 1}$, $T = T_0 - \Delta t$, and $\sigma = \text{Forecast IV}$.
  - **Target 2 Premium ($V_{\text{tgt2}}$)**: Repriced at $F = \text{Target Spot 2}$ under scenario volatility expansion/compression.
  - **Stop Loss Premium ($V_{\text{sl}}$)**: Repriced at $F = \text{Spot Invalidation Level}$.

### 2. Reflection-Principle Barrier Touch Probabilities
Rather than assuming a static $50/50$ coin-flip, the engine computes closed-form touch probabilities using the Brownian reflection principle:
$$P(\text{touch target 1}) = 2 \cdot \left[ 1 - \Phi\left( \frac{|\text{Target 1} - \text{Spot}|}{\sigma_h} \right) \right]$$
$$P(\text{touch SL}) = 2 \cdot \left[ 1 - \Phi\left( \frac{|\text{Spot Invalidation} - \text{Spot}|}{\sigma_h} \right) \right]$$
where $\sigma_h = \text{Spot} \cdot \sigma_{\text{atm}} \cdot \sqrt{h / (375 \times 252)}$ is the 1-SD horizon expected move.

### 3. Probability-Weighted Net Expected Value (Net EV)
Displayed on every recommendation card and the Confluence Strategy Matrix:
$$\text{Net EV (pts)} = \left[ P(\text{target 1}) \times (V_{\text{tgt1}} - V_0) \right] - \left[ P(\text{SL}) \times (V_0 - V_{\text{sl}}) \right]$$
A trade is only considered institutional Grade A if $\text{Net EV} > 0$, guaranteeing positive mathematical expectancy over repeated executions.

### 4. Greek Attribution Decomposition
Every repriced target decomposes expected return into:
- **Delta PnL**: Linear underlying index follow-through ($\Delta \cdot \Delta F$).
- **Gamma PnL**: Convexity acceleration on momentum expansion ($\frac{1}{2}\Gamma (\Delta F)^2$).
- **Vega PnL**: Impact of IV forecast shifts ($\mathcal{V} \cdot \Delta\sigma$).
- **Theta Bleed**: Expected time decay over the holding horizon ($\Theta \cdot \Delta t$).

