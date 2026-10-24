# 🎯 Options Recommendation Strategies: Field Guide & Alpha Playbook

This guide details the institutional microstructure models, trigger mechanics, data feeds, and execution rules powering the **Options Recommendations Engine (Score ≥ 75)** in the **Cubera GEX Cockpit**.

---

## Part 1: Implemented Recommendation Strategies & Quality Gating

The engine synthesizes **GEX structural walls**, **OrderFlow volume delta (CVD)**, **Session VWAP equilibrium**, **ADX trend regimes**, **Sector Breadth**, **FII positioning**, and **live Dhan WebSocket streaming prices** across seven complementary institutional strategies per side.

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
│ 2. STRUCTURE         │ ATM ± 1 step (Δ ~0.40)   │ 1 : 3.0               │ Location at a gamma strike           │
│ 3. STRIKE BREAK      │ Near a gamma strike (Δ ~0.28) │ 1 : 3.5            │ A print through the strike           │
│ 4. ICEBERG FADE      │ ATM (Δ ~0.50)            │ 1 : 4.5               │ Passive limit absorption at ±1σ VWAP │
│ 5. VANNA-CHARM       │ OTM ± 2 steps (Δ ~0.20)  │ 1 : 2.0               │ Expiry afternoon theta compression   │
│ 6. VOL DISPERSION    │ ATM (Δ ~0.50)            │ 1 : 4.0               │ Compression breakout / long gamma    │
│ 7. RATIO REVERSAL    │ ATM ± 1 step (Δ ~0.40)   │ 1 : 3.0               │ Drift with a gamma strike on the path │
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
- **Contract ATM Delta ($\Delta \approx 0.50$)**: Black-76 estimate or live option chain delta.

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
  - Dynamic Black-76 repricing anchored to target walls and invalidation levels.

---

### Strategy 3: Convex break at a gamma strike (`STRIKE BREAK`)

#### 1. Concept & Objective
A fast-move template when spot leaves a gamma concentration with heavy orderflow. The concentration is a map. It does not mean a dealer must buy futures.

#### 2. Data Points Used
- **Call Wall / Put Wall OI Concentration**: Relative ratio of wall OI vs average strike OI.
- **Gamma slope along the ladder**: how fast absolute gamma changes from strike to strike. It is a shape of the book. It is not a dealer-delta speed.
- **Aggressive OrderFlow Surge**: Spikes in CVD crossing the 90th percentile of intraday volume.
- **India VIX Momentum**: Sudden intraday expansion in volatility.

#### 3. Inference Made
- If spot leaves the call-gamma strike with heavy buy orderflow, premiums on that side can reprice quickly. That is an observation about the print. It is not a statement that a market maker must buy futures.

#### 4. Strike Selection
- **Call (CE)**: At the Call Wall strike: $K = CW$ (or $\text{ATM} + 2\cdot\text{step}$ if $CW \le S$).
- **Put (PE)**: At the Put Wall strike: $K = PW$ (or $\text{ATM} - 2\cdot\text{step}$ if $PW \ge S$).

#### 5. Decision-Making & Execution Rules
- **Entry Trigger (CE)**: High-volume breakout candle closing above Call Wall accompanied by a positive CVD volume spike.
- **Entry Trigger (PE)**: High-volume breakdown candle closing below Put Wall with negative market delta expansion.
- **Spot Invalidation**:
  - CE: Trapdoor failure where spot falls back below Call Wall ($-0.4 \times \text{step}$).
  - PE: Failed breakdown where spot recovers back above Put Wall ($+0.4 \times \text{step}$).

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

#### 4. Execution Rules
- **Entry Trigger (CE)**: Passive limit buyer absorption confirmed at lower VWAP envelope with positive CVD delta divergence.
- **Entry Trigger (PE)**: Passive limit seller absorption confirmed at upper VWAP envelope with negative CVD delta divergence.
- **Spot Invalidation**: Spot breach beyond the band by $>0.25 \times \text{step}$.

---

### Strategy 5: 0DTE Vanna-Charm Afternoon Theta Compression (`VANNA-CHARM`)

#### 1. Concept & Objective
Expiry-afternoon theta template. Spot between the gamma strikes is a location. It is not a dealer pin, and it does not by itself mean premiums will collapse.

#### 2. Strike Selection & Execution
- **Call (CE)**: OTM Strike at $\text{ATM} + 2 \times \text{step}$ ($\Delta \approx 0.20$).
- **Put (PE)**: OTM Strike at $\text{ATM} - 2 \times \text{step}$ ($\Delta \approx -0.20$).
- **Entry Trigger**: Pinned between major walls with low realized volatility, monetizing rapid premium collapse.

---

### Strategy 6: Volatility Dispersion / Long Gamma Explosion (`VOL DISPERSION`)

#### 1. Concept & Objective
Deployed during compressed volatility regimes where India VIX drops or market consolidation has persisted inside narrow bands. Options trade at low implied volatility, making gamma convexity unusually cheap. Once spot breaks out of the consolidation band, simultaneous vega expansion and gamma explosion generate outsized gains.

#### 2. Strike Selection & Execution
- **Call / Put**: ATM Strike ($K = \text{ATM}$).
- **Entry Trigger**: Squeeze breakout above Session VWAP $+0.5\sigma$ with expanding volume.

---

### Strategy 7: Zero-Cost Ratio Risk Reversal (`RATIO REVERSAL`)

#### 1. Concept & Objective
A ratio structure aimed at a gamma strike. The strike is a concentration. The structure does not assume spot must stop there, and it does not assume spot must travel to the other strike. The user confirms. Defined risk is the wing width times the lot.

#### 2. Strike Selection & Execution
- **Call (CE)**: Strike at $\text{ATM} + \text{step}$ ($\Delta \approx 0.40$).
- **Put (PE)**: Strike at $\text{ATM} - \text{step}$ ($\Delta \approx -0.40$).

---

## Part 2: Defined-Risk Vertical Spread Alternatives

To eliminate theta decay drag and protect capital against sudden intraday whipsaws, every qualifying naked recommendation is accompanied by an institutional **Defined-Risk Vertical Spread Alternative** (`_build_spread_alternative`):

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 🛡️ DEFINED-RISK DEBIT VERTICAL SPREAD ALTERNATIVE                                      │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ • Bull Call Spread (CE): Buy Strike K1 (ATM) + Sell Strike K2 (K1 + step)              │
│ • Bear Put Spread (PE) : Buy Strike K1 (ATM) + Sell Strike K2 (K1 - step)              │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 1. Net Debit Outlay  = Long Premium (LTP_long) - Short Premium (LTP_short)             │
│ 2. Maximum Loss      = Net Debit Paid (100% Capped Risk - Zero Overnight Ruin)         │
│ 3. Maximum Gain      = Strike Width (step) - Net Debit Paid                            │
│ 4. Spread R:R Ratio  = Max Gain / Max Loss                                             │
│ 5. Theta Decay Hedge = Short leg theta decay directly offsets long leg theta bleed      │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Lower Capital Requirement**: Net debit outlays are typically $40\%–60\%$ lower than buying naked options.
- **High-Volatility Defense**: When IV is elevated, selling the higher strike finances the long position and cushions the trade against volatility crush.

---

## Part 3: Multi-Factor Sigmoid Conviction Scoring & Confluence

The Recommendations Engine does not rely on simple indicator thresholds. Instead, candidate setups pass through a multi-factor quantitative scoring model:

$$\text{Final Conviction Score} = \text{Sigmoid}\left(\text{Base Score} + \sum \text{Confluence Modifiers}\right) \times 100$$

### Confluence Factors:
1. **ADX Trend Regime (`compute_adx`)**:
   - `ADX >= 25` (Strong Trend): $+5\text{ pts}$ bonus for Momentum, $+10\text{ pts}$ for a strike break.
   - `ADX < 20` (Choppy Range): $-10\text{ pts}$ penalty on Momentum; favors Iceberg Fade and Vanna-Charm.
2. **CVD Divergence (`compute_cvd_divergence`)**:
   - `BULLISH_ABSORPTION`: Spot printing flat/lower lows while CVD rises $\to +8\text{ pts}$ on CE.
   - `BEARISH_EXHAUSTION`: Spot printing higher highs while CVD falls $\to +8\text{ pts}$ on PE.
   - Opposing divergence penalizes candidate score by $-12\text{ pts}$.
3. **Trend Alignment (`compute_trend_context`)**:
   - Spot vs 20 EMA, 50 SMA, and Session VWAP. Full triple-bullish alignment awards $+10\text{ pts}$.
4. **Lunch-Hour Chop Filter (`is_lunch_hour_chop`)**:
   - Session window **11:30 – 13:00 IST** suffers from low European overlap volume. Breakout setups receive an automatic $-8\text{ pts}$ chop damping penalty.
5. **Sector Breadth Alignment (`compute_sector_breadth_context`)**:
   - When $\ge 70\%$ of the 11 constituent sectors advance, Call setups gain $+6\text{ pts}$; Put setups lose $-8\text{ pts}$.
6. **FII Net Futures Positioning (`compute_fii_positioning_context`)**:
   - Aligns trades with institutional foreign desk positioning from exchange clearing data.

---

## Part 4: Anti-Flicker Latching & Lifecycle State Machine

To eliminate card flickering, disappearance, and false exits during live streaming ticks, recommendations are governed by an explicit finite-state machine managed by `RecommendationLatchManager`:

```
   ┌─────────┐
   │ DORMANT │ ◀── Initial State (Score < 75)
   └────┬────┘
        │ Quality Score >= 75 (Initial Qualification)
        ▼
  ┌───────────┐
  │ QUALIFIED │
  └─────┬─────┘
        │ Quality Score >= min_score (75/85) & Initial Lease Granted
        ▼
   ┌──────────┐ ◀──────────────────────────────────────────────┐
   │  ACTIVE  │ ── Score fluctuates between [65, 100] (Hysteresis)│
   └────┬─────┘ ── 180s Dwell Lease Guarantees Display         │
        │                                                      │
        ├──────────────────────┬──────────────────────┐        │
        │ Target 1 Hit         │ Spot Invalidation    │ Lease Expired
        │ (Spot >= T1 / <= T1) │ Breached             │ & Score < 65
        ▼                      ▼                      ▼        │
 ┌────────────┐        ┌─────────────┐        ┌──────────┐     │
 │ TARGET_HIT │        │ INVALIDATED │        │ DEGRADED │ ────┘
 └────────────┘        └─────────────┘        └──────────┘  Re-qualifies if score >= 75
 (Card flags Target    (Filtered out          (Demoted to
  Reached; locks gain)  from display)          Watchlist)
```

### State Machine Invariants:
1. **Dwell Time Lease (180 Seconds)**: Once promoted to `ACTIVE`, an options recommendation card is guaranteed a minimum dwell lease of 180 seconds. It will not vanish on ephemeral microsecond tick dips.
2. **Score Hysteresis ($\Delta = 10\text{ pts}$)**:
   - Initial entry requires $\text{Score} \ge 75$ (or 85 for High Conviction).
   - Once `ACTIVE`, the card remains in active display even if score fluctuates down to **65** ($\text{min\_score} - 10$).
   - Only when score stays below 65 *after* the 180s dwell lease expires does the card transition to `DEGRADED`.
3. **Continuous Sub-Millisecond Telemetry (`update_trade_idea_live_telemetry`)**:
   - Tracks live option MTM, current PnL, Maximum Favorable Excursion (MFE), and Maximum Adverse Excursion (MAE) tick-by-tick.
4. **"Time Since Last Active" Dynamic Counter**:
   - Displayed in every card header:
     - `🟢 Active: 2m 15s`: Actively dwelling within valid execution window.
     - `🎯 Target Hit (30s ago)`: Target 1 achieved; locks gains.
     - `🛑 Inval (1m ago)`: Structural stop breached; alerts trader.
     - `⏱️ Last Active: 3m 40s ago`: Demoted after dwell expiration.

---

## Part 5: Strict Market Hours Gating & Real Spot Sourcing

1. **Market Hours Invariant (09:15 – 15:30/15:40 IST)**:
   - Live recommendation generation is active **only during continuous exchange trading hours**.
   - Outside market hours, generation is cleanly gated off. The cockpit loads the last verified closing state from the persistent SQLite market snapshot registry (`market_snapshots`).
2. **Zero Synthetic / Fabricated Prices**:
   - All spot prices, strike quotes, and GEX levels are sourced strictly from Dhan WebSocket feeds or verified exchange snapshots. Synthetic fallback levels are strictly blocked.
3. **Clamping of Target Spot Reach**:
   - Target spot levels are mathematically clamped to realistic 1-SD horizon expected moves ($\sigma_h$), preventing unachievable targets on low-volatility sessions.
4. **Pinned Cards Retention**:
   - Pinned cards persist in the local storage registry across page reloads and browser sessions until explicitly unpinned by the user or until the contract crosses its expiry date.
