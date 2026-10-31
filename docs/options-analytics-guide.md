# Options Analytics & Microstructure Intelligence Guide (IOAS v3.0)

> **Cubera GEX Cockpit — Institutional Options Analytics System (IOAS v3.0)**  
> *Quantitative Architecture, Data Points, Interpretations, Mathematical Formulas & Operational Trader Playbooks for Indian Derivatives (NIFTY, BANKNIFTY, SENSEX)*

---

## 1. Executive Architecture & Philosophy

The **Options Analytics** tab implements the **Intraday Options Analytics System (IOAS v3.0)**. Unlike conventional option chain tables that dump raw numbers, IOAS v3.0 is built specifically for **Indian Exchange Microstructure (NSE/BSE)** with the following foundational principles:

1. **One book**: Both call and put gamma are stored short. The call-gamma strike and the put-gamma strike are concentrations. They are not floors, ceilings, or levels a writer is measured to defend. Who hedges on NSE is unmeasured.
2. **What moves the bias number**: The card shows eight rows. PCR, the gamma-strike midpoint, max pain, and open-interest changes are facts and contribute 0. Only 25Δ skew and India VIX move the score.
3. **Open interest is not flow**: A change in open interest is a stock of contracts. It is not a buyer-versus-seller print and it does not forecast a top or a bottom.
4. **Independent Card Polling**: Each visual panel is isolated via Streamlit card fragments, ensuring that rapid real-time updates do not reload the page or disrupt user focus.

---

## 2. Section-by-Section Deep Dive

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  0. NOA Executive KPI Strip (ATM IV · PCR · Max Pain · IV Rank · VIX · Bias)     │
├────────────────────────────────────────┬─────────────────────────────────────────┤
│  1A. Directional Bias Score (IOAS v3.0)│  1B. Key Strike Observations & Anchors  │
│      [-10 to +10, 8 Sub-Signals]       │      [Call Wall · Put Wall · Flip]      │
├────────────────────────────────────────┼─────────────────────────────────────────┤
│  2A. OI Distribution Chart (±4% Band)  │  2B. Expected Move Range (±1σ)          │
│      [Call OI vs Put OI Bar Clusters]  │      [IV Model Move vs Straddle Pricing]│
├────────────────────────────────────────┼─────────────────────────────────────────┤
│  3A. Strike Buildup Matrix (Flow)      │  3B. IV Skew & Term Structure Curve     │
│      [4-Quadrant Flow & Conviction]    │      [25Δ Skew · Term Spread · Smile]   │
├────────────────────────────────────────┼─────────────────────────────────────────┤
│  4A. Active Structural Divergences     │  4B. Intraday Shift Log & Parity Monitor│
│      [Real-time Desync Severity Feed]  │      [Chronological Alerts & Arb Devs]  │
└────────────────────────────────────────┴─────────────────────────────────────────┘
```

---

### Section 0: Executive KPI Strip

The top horizontal strip aggregates critical macroeconomic and options market metrics for instant orientation:

| Metric | Calculation / Data Source | Normal Baseline | Thresholds & Interpretation | Trader Action |
| :--- | :--- | :--- | :--- | :--- |
| **ATM IV** | Near ATM implied volatility from front-month options chain. | $11.0\% - 15.0\%$ | • `< 11\%`: Volatility crushed (Cheap options, favor buying debit spreads)<br>• `> 18\%`: Volatility expanded (Expensive options, favor selling credit spreads) | Select trade strategy by Vega sensitivity. |
| **PCR (OI)** | Put open interest divided by call open interest. | $0.90 - 1.10$ | Above 1 is put-heavy open interest. Below 1 is call-heavy open interest. Neither is a floor, a ceiling, or a squeeze. | A ratio. The user confirms. |
| **Max Pain** | Strike that minimizes total intrinsic payout. | Distance from spot. | A nearby max pain is a payout fact. It is not a forecast that expiry will pin there. | Context on expiry day. The user confirms. |
| **IV Rank (30D)** | $\frac{\text{IV}_{\text{current}} - \text{IV}_{\text{30D Min}}}{\text{IV}_{\text{30D Max}} - \text{IV}_{\text{30D Min}}} \times 100$ | $20\% - 60\%$ | • `< 20\%`: Low IV Rank (Favor long Vega)<br>• `> 70\%`: High IV Rank (Favor short Vega / Iron Condors) | Select Net Debit vs Net Credit structures. |
| **India VIX** | Annualized implied volatility of NIFTY options across out-of-the-money strikes. | $12.0 - 15.0$ | • `< 13.0`: Calm regime (Orderly trends, tight expected moves)<br>• `> 18.0`: High turbulence regime (Wider stop-losses required) | Size positions and adjust stop-loss distance according to VIX. |
| **Bias Gauge** | IOAS composite score synthesized from 8 weighted sub-signals ($-10.0$ to $+10.0$). | $-1.0 \text{ to } +1.0$ | • `> +3.0`: Strong Bullish Regime<br>• `< -3.0`: Strong Bearish Regime<br>• `[-1, +1]`: Balanced Auction | Dictates macro trade direction. Never take naked shorts against a $+5.0$ score. |

---

### Section 1A: Directional Bias Engine (IOAS v3.0)

The bias card always shows eight rows. The weights still sum to 10 so the table stays complete. Six of the rows are facts and contribute 0. The number is skew plus India VIX, clamped to $[-10, +10]$.

#### Sub-Signal Decomposition

1. **$s_1$ PCR level (weight 1.5, contribution 0)**: Put open interest divided by call open interest. Above 1.25 is put-heavy. Below 0.85 is call-heavy. Neither is a direction.
2. **$s_2$ PCR change (weight 1.0, contribution 0)**: How PCR moved over 15 minutes. A change is a fact, not a bottom.
3. **$s_3$ 25$\Delta$ IV skew (weight 1.5)**: Put IV minus call IV. This row does move the score.
4. **$s_4$ Gamma-strike midpoint (weight 1.5, contribution 0)**: Whether spot is above or below the midpoint of the call-gamma and put-gamma strikes. Location only.
5. **$s_5$ Max pain (weight 1.0, contribution 0)**: Distance from spot to the settlement strike that minimises intrinsic payout. Not a pull.
6. **$s_6$ Open-interest change (weight 1.5, contribution 0)**: How much call and put open interest changed. Not buyer-versus-seller flow.
7. **$s_7$ Strike buildup (weight 1.0, contribution 0)**: How many strikes added put open interest versus call open interest. Not a direction.
8. **$s_8$ India VIX (weight 1.0)**: A low VIX adds a point. A high VIX subtracts a point. This row does move the score.

---

### Section 1B: Key Strike Observations & GEX Anchors

Defines the institutional boundaries governing price movements:

1. **Call-gamma strike ($CW$)**:
   - *Definition*: Strike with the largest call-gamma magnitude.
   - *Role*: A concentration on the book. A close through it is a location, not a short-covering squeeze and not a point target.
2. **Put-gamma strike ($PW$)**:
   - *Definition*: Strike with the largest put-gamma magnitude.
   - *Role*: A concentration on the book. A close through it is a location, not a trapdoor.
3. **Max pain ($K^*$)**:
   - *Definition*: Strike that minimises total intrinsic payout at expiry.
   - *Role*: A settlement fact. Distance to it is not a rubber band.
4. **Call−put balance ($\Gamma_{\text{flip}}$)**:
   - *Definition*: Where call-gamma magnitude and put-gamma magnitude cross.
   - *Above it*: the call book is larger on this side of the map.
   - *Below it*: the put book is larger on this side of the map.
   - Neither side is a mean-reversion order or a cascade.

---

### Section 2A & 2B: Open Interest Distribution & Expected Move Range

#### Open Interest Distribution Chart ($\pm 4\%$ Band)
- Plots Call OI (Cyan `#38bdf8`) versus Put OI (Green `#4ade80`) for every strike in the front expiry within $\pm 4\%$ of spot.
- **Visual Clues**:
  - *Tall green bar*: large put open interest. A concentration, not a floor.
  - *Tall Cyan Tower*: Heavy call writing ceiling (Resistance).
  - *OI Valleys*: Strikes with low total OI where price moves through frictionless "vacuum" channels.

#### Expected Move Range ($\pm 1\sigma$)
Combines analytical Black-Scholes volatility pricing with synthetic market consensus:

$$\text{IV Model Move} = \text{Spot} \times \text{ATM IV} \times \sqrt{\frac{\text{DTE}}{365}}$$

$$\text{ATM Straddle Move} = \text{Call}_{\text{ATM}}^{\text{LTP}} + \text{Put}_{\text{ATM}}^{\text{LTP}}$$

- **Range Low**: $\text{Spot} - \text{IV Move}$
- **Range High**: $\text{Spot} + \text{IV Move}$
- **Interpretation**: 68.2% of normal distributions resolve inside this bracket. If spot trades outside the Expected Move on normal news, premiums are overextended, presenting high-probability mean-reversion credit spreads.

---

### Section 3A & 3B: Strike Buildup Matrix & IV Skew Curve

#### The 4 Strike Buildup Flow Quadrants
Tracks transaction telemetry across price delta ($\Delta P$) and open interest delta ($\Delta \text{OI}$):

| Quadrant | Price ($\Delta P$) | Open Interest ($\Delta \text{OI}$) | Institutional Meaning | Market Impact |
| :--- | :---: | :---: | :--- | :--- |
| **🟢 Long Buildup** | $\uparrow$ | $\uparrow$ | Fresh aggressive buyers entering long contracts. | Bullish continuation |
| **🔴 Short Buildup** | $\downarrow$ | $\uparrow$ | Fresh aggressive writers/sellers entering short positions. | Bearish resistance build |
| **🟡 Short Covering** | $\uparrow$ | $\downarrow$ | Trapped short sellers covering/buying back positions. | Squeeze momentum |
| **🟠 Long Unwinding** | $\downarrow$ | $\downarrow$ | Long holders taking profits or hitting stop-losses. | Exhaustion / correction |

#### Top-3 High-Conviction Strikes
Filters out noise to spotlight the 3 strikes exhibiting the highest absolute net contract velocity ($\Delta \text{COI} + \Delta \text{POI}$).

#### 25-Delta IV Skew & Term Structure Curve
- **25$\Delta$ Put IV vs 25$\Delta$ Call IV**:
  - *Normal Put Skew ($+2\%$ to $+5\%$)*: Normal risk premium. Downside protection is priced higher than upside calls.
  - *Steep Put Skew ($> +7\%$)*: Severe institutional crash hedging. Downside momentum is active.
  - *Inverted Call Skew ($< 0\%$)*: Call IV exceeds Put IV. Squeeze frenzy / retail upside greed.
- **Term Spread (Near ATM IV vs Next ATM IV)**:
  - *Contango ($\text{Next IV} > \text{Near IV}$)*: Normal, stable market.
  - *Backwardation ($\text{Near IV} > \text{Next IV}$)*: Event risk / imminent high-volatility shock priced in the front contract.

---

### Section 4A & 4B: Divergences, Intraday Shift Log & Parity Monitor

#### Active Structural Divergences
The divergence detection engine compares price action with underlying positioning to identify institutional trapdoors:

1. **Bullish OI Divergence**: Spot falls while Put Writing surges and Call Writing halts. (Bear Trap).
2. **Bearish OI Divergence**: Spot makes higher highs while Call Writing accelerates at resistance. (Bull Trap).
3. **PCR / Price Desync**: Spot breaks upward but PCR fails to rise (Lack of put underwriting follow-through).
4. **IV Expansion on Rally**: Spot rises while ATM IV spikes aggressively (Smart money buying downside protection even as spot rises).

#### Intraday Shift Events Log
Chronological ledger recording key microstructure transitions:
- Wall Tests & Defenses
- PCR Regime Flips (Cross above 1.1 or drop below 0.9)
- Opening Bracket Discoveries

#### Put-Call Parity & Synthetic Futures Arbitrage
Monitors the no-arbitrage condition for European options:

$$C - P = S - K \cdot e^{-rT} \implies \text{Synthetic Future} = C - P + K$$

$$\text{Basis Discrepancy} = \text{Actual Future Price} - \text{Synthetic Future Price}$$

- **Discrepancy $> +15$ pts**: Actual Futures are overpriced relative to options (Cash-and-carry box arb barrier).
- **Discrepancy $< -15$ pts**: Actual Futures are underpriced relative to options (Reverse cash-and-carry arb barrier).
- Highlights institutional dislocations and pricing efficiency across strikes.

---

## 3. Operational Trader Playbooks & Decision Matrix

### Decision Matrix: "If Metric X, Then Take Action Y"

| Market Condition | Triggering Indicators | Recommended Action | Invalidation Rule |
| :--- | :--- | :--- | :--- |
| **Spot at the call-gamma strike** | A 15-minute print is at the largest call-gamma strike. | Read it as a location. The user confirms any structure. | A later print back through the strike. |
| **Spot through the call-gamma strike** | A 15-minute close is beyond the call-gamma strike. | A location on the book. Not a squeeze and not an automatic long. | The user stands aside. |
| **Spot through the put-gamma strike** | A 15-minute close is beyond the put-gamma strike. | A location on the book. Not a trapdoor and not an automatic short. | The user stands aside. |
| **Expiry afternoon** | After 13:30 IST on the listed expiry (Nifty Tuesday, Bank Nifty last Tuesday, Sensex Thursday). Spot is near max pain. | Distance to the settlement strike. Not a pin and not an automatic short straddle. | The user confirms. |
| **Spot outside the one-sigma band** | Spot is beyond the expected-move band and IV rank is high. | A location and a volatility fact. Not an automatic fade. | The user confirms. |

---

## 4. Summary & Best Practices

1. **Always Verify Cross-Confirmation**: Never execute based on a single metric. Require at least **3 aligned factors** (e.g., Bias Score + Wall Proximity + Strike Buildup Flow).
2. **Watch the Time of Day**:
   - `09:15 - 09:30 IST`: Discovery noise. Do not enter full-size positions.
   - `09:45 - 11:30 IST`: Primary institutional trend establishment.
   - `11:30 - 13:00 IST`: European overlap / midday consolidation.
   - `13:30 - 15:15 IST`: Expiry window. Near-the-money gamma is high because time is short. That is not a pin.
3. **Respect Independent Card Fragments**: The Options Analytics tab updates smoothly without page jumps. Use the top KPI strip for situational awareness and dive into specific rows for tactical trade execution.

---

## 🔗 Related Technical Documentation
- [🎯 Options Recommendation Strategies & Alpha Playbook](/?view=options_strategies_guide)
- [🎯 Target Engine — Scenario & Repricing Suite Technical Guide](/?view=target_engine_guide)
- [⚡ OrderFlow Sentinel Stream Signals Field Guide](/?view=orderflow_guide)
- [🧭 Market Intelligence: Sector Breadth & News Sentiment Guide](/?view=market_intelligence_guide)
- [🎙️ Session Commentary & Suggestion Bites Guide](/?view=session_commentary_guide)
- [🌅 Pre/Post-Market Microstructure & Recommendations Lifecycle Guide](/?view=pre_post_market_guide)
- [📈 Signal Efficiency & MFE/MAE Performance Guide](/?view=signal_efficiency_guide)
- [📊 Quantitative Calculations & Formulas Reference](/?view=data_dictionary)
- [🧭 Participant Open Interest Guide](/?view=participant_guide)
- [🗺️ Indian GEX Playbook & Research Manual](/?view=gex_playbook)
- [📘 Master Cockpit User Guide](/?view=user_guide)
