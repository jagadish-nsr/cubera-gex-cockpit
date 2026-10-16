# Options Analytics & Microstructure Intelligence Guide (IOAS v3.0)

> **Cubera GEX Cockpit — Institutional Options Analytics System (IOAS v3.0)**  
> *Quantitative Architecture, Data Points, Interpretations, Mathematical Formulas & Operational Trader Playbooks for Indian Derivatives (NIFTY, BANKNIFTY, SENSEX)*

---

## 1. Executive Architecture & Philosophy

The **Options Analytics** tab implements the **Intraday Options Analytics System (IOAS v3.0)**. Unlike conventional option chain tables that dump raw numbers, IOAS v3.0 is built specifically for **Indian Exchange Microstructure (NSE/BSE)** with the following foundational principles:

1. **Seller-Centric Asymmetry (SCSP)**: In Indian index derivatives, institutional prop desks and retail writers provide the bulk of passive liquidity. Strike walls act as hard barriers where option sellers defend premium until hedging breaks.
2. **Multi-Factor Directional Synthesis**: Market direction cannot be evaluated through price or PCR in isolation. IOAS v3.0 computes an 8-factor synthetic score combining Open Interest, IV Skew, GEX boundaries, Max Pain gravitation, and OI velocity.
3. **Pre-Emptive Divergence Detection**: Structural tops and bottoms in Indian markets routinely signal via Open Interest changes 15 to 45 minutes before spot breaks.
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
| **PCR (OI)** | Total Put Open Interest divided by Total Call Open Interest: $\frac{\sum \text{Put OI}}{\sum \text{Call OI}}$ | $0.90 - 1.10$ | • `> 1.25`: Heavy put writing (Bullish floor)<br>• `< 0.75`: Heavy call writing (Bearish ceiling)<br>• `> 1.60`: Extreme overbought / squeeze risk | Use as baseline market posture; align with writer bias. |
| **Max Pain** | Strike $K^*$ that minimizes total intrinsic payout: $\arg\min_K \sum_i \text{Payoff}(K, K_i)$ | Within $\pm 0.5\%$ of Spot | • **Pin Score $\ge 80$**: High probability of expiration pinning.<br>• **Pin Score $< 40$**: Spot runaway trend breaking through pain barriers. | On 0DTE/Expiry, anchor mean-reversion targets toward Max Pain. |
| **IV Rank (30D)** | $\frac{\text{IV}_{\text{current}} - \text{IV}_{\text{30D Min}}}{\text{IV}_{\text{30D Max}} - \text{IV}_{\text{30D Min}}} \times 100$ | $20\% - 60\%$ | • `< 20\%`: Low IV Rank (Favor long Vega)<br>• `> 70\%`: High IV Rank (Favor short Vega / Iron Condors) | Select Net Debit vs Net Credit structures. |
| **India VIX** | Annualized implied volatility of NIFTY options across out-of-the-money strikes. | $12.0 - 15.0$ | • `< 13.0`: Calm regime (Orderly trends, tight expected moves)<br>• `> 18.0`: High turbulence regime (Wider stop-losses required) | Size positions and adjust stop-loss distance according to VIX. |
| **Bias Gauge** | IOAS composite score synthesized from 8 weighted sub-signals ($-10.0$ to $+10.0$). | $-1.0 \text{ to } +1.0$ | • `> +3.0`: Strong Bullish Regime<br>• `< -3.0`: Strong Bearish Regime<br>• `[-1, +1]`: Balanced Auction | Dictates macro trade direction. Never take naked shorts against a $+5.0$ score. |

---

### Section 1A: Directional Bias Engine (IOAS v3.0)

The Directional Bias Engine decomposes market positioning into 8 mathematically audited sub-signals:

$$\text{Composite Score} = \sum_{i=1}^{8} w_i \cdot s_i \quad \text{clamped to } [-10.0, +10.0]$$

#### Sub-Signal Decomposition

1. **$s_1$ PCR Level ($w = 2.0$)**:
   - *Formula*: Evaluates whether aggregate PCR sits above 1.15 (+1) or below 0.85 (-1).
   - *Microstructure Logic*: Put writing generates positive dealer delta; heavy call writing caps upside.
2. **$s_2$ PCR Trend ($w = 1.5$)**:
   - *Formula*: Intraday rate of change: $\frac{\text{PCR}_t - \text{PCR}_{t-1}}{\Delta t}$.
   - *Microstructure Logic*: Even if PCR is 0.90 (bearish), an increasing PCR indicates intraday bottom formation.
3. **$s_3$ 25$\Delta$ IV Skew ($w = 1.2$)**:
   - *Formula*: $\text{IV}(\text{Put}_{25\Delta}) - \text{IV}(\text{Call}_{25\Delta})$.
   - *Microstructure Logic*: Measures fear premium. Put IV $>$ Call IV is normal; inversion signals retail call FOMO.
4. **$s_4$ Wall Alignment ($w = 1.8$)**:
   - *Formula*: Relative proximity of Spot to Call Wall ($CW$) versus Put Wall ($PW$): $\frac{(Spot - PW) - (CW - Spot)}{CW - PW}$.
   - *Microstructure Logic*: When spot approaches the Call Wall, upside reward/risk diminishes rapidly.
5. **$s_5$ Max Pain Gravity ($w = 1.0$)**:
   - *Formula*: Gravitational pull vector toward $K^*$ weighted by time decay $\frac{1}{\sqrt{\text{DTE}}}$.
   - *Microstructure Logic*: Strongest in final 90 minutes of Thursday expiry session.
6. **$s_6$ OI Velocity ($w = 1.5$)**:
   - *Formula*: Net change in intraday contracts: $\Delta \text{Put OI}_{\text{total}} - \Delta \text{Call OI}_{\text{total}}$.
   - *Microstructure Logic*: Reveals real-time institutional capital deployment during the current session.
7. **$s_7$ Buildup Dominance ($w = 1.5$)**:
   - *Formula*: Ratio of net Long Buildups + Short Coverings to Short Buildups + Long Unwindings across all strikes.
   - *Microstructure Logic*: Verifies if buyers are paying up (Long Buildup) or sellers are aggressively dumping (Short Buildup).
8. **$s_8$ IV Rank Anchor ($w = 0.8$)**:
   - *Formula*: Dampens directional conviction when IV is abnormally elevated (whipsaw risk).

---

### Section 1B: Key Strike Observations & GEX Anchors

Defines the institutional boundaries governing price movements:

1. **Call Wall ($CW$)**:
   - *Definition*: Strike with the highest absolute Call Open Interest concentration.
   - *Microstructure Role*: Strong institutional resistance. Call writers aggressively sell futures to hedge delta if spot challenges this level.
   - *Breakout Signal*: A 15-minute candle close **above** Call Wall triggers a violent short-covering **Gamma Squeeze** (+100 to +250 pts on NIFTY).
2. **Put Wall ($PW$)**:
   - *Definition*: Strike with the highest absolute Put Open Interest concentration.
   - *Microstructure Role*: Strong institutional floor. Put writers buy dips to defend their strike.
   - *Breakdown Signal*: A 15-minute candle close **below** Put Wall creates a **Trapdoor Liquidation** as put writers capitulate.
3. **Max Pain Strike ($K^*$)**:
   - *Definition*: Strike where total option value expires worthless, leaving maximum profit for option writers.
   - *Microstructure Role*: Acts as an elastic rubber band. If spot stretches too far on low volume, expect mean reversion toward Max Pain.
4. **Gamma Flip Level ($\Gamma_{\text{flip}}$)**:
   - *Definition*: The price point where market maker aggregate gamma shifts from positive (mean-reverting) to negative (trend-accelerating).
   - *Spot Above Flip*: Market makers sell rallies and buy dips (Calm, mean-reverting, tight wicks).
   - *Spot Below Flip*: Market makers sell sell-offs and buy breakouts (Volatile, cascade runs, large wicks).

---

### Section 2A & 2B: Open Interest Distribution & Expected Move Range

#### Open Interest Distribution Chart ($\pm 4\%$ Band)
- Plots Call OI (Cyan `#38bdf8`) versus Put OI (Green `#4ade80`) for every strike in the front expiry within $\pm 4\%$ of spot.
- **Visual Clues**:
  - *Tall Green Tower*: Heavy put writing floor (Support).
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
| **Call Wall Rejection (Range Scalp)** | Spot tests Call Wall ($\pm 15$ pts); Delta negative; Bias Score $\le +1.0$; $s_7$ Short Buildup on Call. | **Sell Bear Call Spread** or Buy Put Debit Spread. Target: Spot mean reversion to Max Pain. | 15-minute close above Call Wall + RVol $\ge 2.0\times$. |
| **Gamma Breakout Squeeze** | 15m candle closes above Call Wall; PCR $> 1.25$; Bias Score $\ge +3.5$; Call OI drops ($\Delta \text{COI} < 0$). | **Buy Bull Call Debit Spread** or Outright Calls. Target: $+1.0\sigma$ Expected Move High. | Spot falls back inside Call Wall. |
| **Put Wall Trapdoor Breakdown** | 15m candle closes below Put Wall; PCR $< 0.80$; Bias Score $\le -3.5$; Put OI drops ($\Delta \text{POI} < 0$). | **Buy Bear Put Debit Spread** or Outright Puts. Target: $-1.0\sigma$ Expected Move Low. | Spot recovers above Put Wall. |
| **0DTE Expiry Pinning** | Time $> 13:30$ IST on Expiry Day; Spot within $0.4\%$ of Max Pain; Pin Score $\ge 80$; Term Spread in Contango. | **Sell ATM Iron Butterfly** or Short Straddle centered on Max Pain strike. | Spot breaks beyond ATM Straddle breakevens on volume spike. |
| **Overextended Premium Fade** | Spot breaches $\pm 1\sigma$ Expected Move band; Active Structural Divergence flagged; IV Rank $> 70\%$. | **Fade the move**: Sell OTM Credit Spread against the breakout direction. | Price consolidates above $1.5\sigma$ band with fresh OI accumulation. |

---

## 4. Summary & Best Practices

1. **Always Verify Cross-Confirmation**: Never execute based on a single metric. Require at least **3 aligned factors** (e.g., Bias Score + Wall Proximity + Strike Buildup Flow).
2. **Watch the Time of Day**:
   - `09:15 - 09:30 IST`: Discovery noise. Do not enter full-size positions.
   - `09:45 - 11:30 IST`: Primary institutional trend establishment.
   - `11:30 - 13:00 IST`: European overlap / midday consolidation.
   - `13:30 - 15:15 IST`: Expiry pinning & gamma acceleration window.
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
