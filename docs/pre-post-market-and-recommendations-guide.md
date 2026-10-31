# 🌅 Pre/Post-Market Microstructure & Recommendations Lifecycle Guide

This comprehensive guide details the architecture, mathematical formulations, data pipelines, and operator workflows for:
1. **Pre-Open Market Microstructure & Pre-Bell Tactical Planning (09:00:00 – 09:15:00 IST)**
2. **Closing Auction Session (CAS) & F&O Extended Close (15:30:00 – 15:40:00 / 16:00:00 IST)**
3. **High-Conviction Recommendations Lifecycle & Bookmarking Persistence**
4. **Conviction Trajectory Microstructure Audit & UI Polish**

---

## 🧭 Master Daily Session Timeline

The Indian financial markets operate under distinct microstructure regimes across equities and derivatives. The Cockpit orchestrates automated polling, alert triggers, audio notifications, and analytical models tailored to each phase:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 INDIAN MARKET MICROSTRUCTURE TIMETABLE                                 │
├───────────────┬─────────────────────────┬──────────────────────┬───────────────────────────────────────┤
│ Time (IST)    │ Session Phase           │ Streaming Mode       │ Core Microstructure Mechanics         │
├───────────────┼─────────────────────────┼──────────────────────┼───────────────────────────────────────┤
│ 06:30 – 09:00 │ PRE_MARKET_SETUP        │ 🛑 Offline Snapshot  │ Prior-session book on disk. No Dhan poll. │
│ 09:00 – 09:07 │ PRE_MARKET_ENTRY        │ Static Standby       │ Order accumulation; no matching       │
│ 09:07:30–09:15│ PRE_MARKET_DISCOVERY    │ Adaptive Discovery   │ Equilibrium price discovery & Pre-Bell│
│ 09:15 – 15:30 │ REGULAR_SESSION         │ 🟢 WebSocket Live    │ Continuous trading & intraday signals │
│ 15:30 – 15:40 │ CAS_AND_FNO_EXTENDED    │ 🟢 WebSocket Live    │ 30m VWAP cash auction & F&O final run │
│ 15:40 – 16:00 │ POST_MARKET_SETTLEMENT  │ 🛑 Offline Snapshot  │ Settlement versus the continuous close │
│ 16:00 – 06:30 │ OFF_HOURS               │ 🛑 Offline Snapshot  │ Overnight off-hours & holiday standby │
└───────────────┴─────────────────────────┴──────────────────────┴───────────────────────────────────────┘
```

> [!NOTE]
> **Streamlined 2-Mode Streaming Architecture**: The legacy 4-tier selectbox (3s/10s/30s/Manual) has been consolidated into two strict operating modes:
> 1. **🟢 WebSocket Live Feed**: Active during trading hours (09:15–15:40 IST), Pre-Market Discovery (09:07:30–09:15 IST), and the F&O window through 15:40 IST. Push latency is <1ms from the Dhan binary feed. There is no replay mode on this feed.
> 2. **🛑 Offline Snapshot Mode**: Active during off-hours, weekends, holidays, and post-market settlement. Background API pollers are paused to conserve rate limits and prevent phantom ticks; the UI serves authentic closing data cached from the SQLite snapshot registry with manual on-demand refresh.

---

## Part 1: Pre-Open Market Microstructure & Trader Action Playbook (06:30 – 09:15 IST)

### 1.1 The Master Morning Pre-Bell Architecture (06:30 – 09:15 IST)

The pre-market window is not merely a waiting period; it is the single most asymmetric information window of the trading day. From 06:30 the cockpit reads the book already on disk. It does not poll a quote.

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                             MORNING PRE-BELL SEQUENCE ARCHITECTURE                               │
├───────────────────┬─────────────────────────┬────────────────────────────────────────────────────┤
│ Time Window (IST) │ Phase Name              │ Data Engine & Microstructure Mechanics             │
├───────────────────┼─────────────────────────┼────────────────────────────────────────────────────┤
│ 06:30 – 09:00:00  │ PRE_MARKET_SETUP        │ Local prior-session snapshot only. Walls, flip,    │
│                   │                         │ OI timestamp, FII derivatives, and prior-session   │
│                   │                         │ CVD already on disk. No Dhan quote and no chain.   │
├───────────────────┼─────────────────────────┼────────────────────────────────────────────────────┤
│ 09:00 – 09:07:59  │ PRE_MARKET_ENTRY        │ NSE Cash Equity call auction order accumulation.    │
│                   │                         │ Orders entered/cancelled; zero execution.          │
│                   │                         │ Live countdown to equilibrium matching.            │
├───────────────────┼─────────────────────────┼────────────────────────────────────────────────────┤
│ 09:07:30 – 09:08  │ PRE_MARKET_DISCOVERY    │ Adaptive 3.0s polling engages on Dhan quote feed.  │
│                   │                         │ Detects single clearing equilibrium price.         │
├───────────────────┼─────────────────────────┼────────────────────────────────────────────────────┤
│ 09:08:15 IST      │ 🔔 DISCOVERY LOCK-IN    │ Four-tone chime sounds. Equilibrium open locked.   │
│                   │                         │ Black-76 forward model reprices option chain.      │
├───────────────────┼─────────────────────────┼────────────────────────────────────────────────────┤
│ 09:08 – 09:14:59  │ 7-MIN ALPHA WINDOW      │ Cash open is known; options remain frozen at       │
│                   │                         │ yesterday's close. Trader executes pre-bell        │
│                   │                         │ tactical playbook before derivatives open at 09:15.│
└───────────────────┴─────────────────────────┴────────────────────────────────────────────────────┘
```

#### The NSE Pre-Open Call Auction Mechanics:
- **Cash Equity Single-Price Auction**: The National Stock Exchange (NSE) accumulates buy and sell orders between 09:00:00 and 09:07:59. At random closure (~09:07:30 to 09:08:00), the exchange's matching engine solves for the single clearing price that maximizes executable volume.
- **Derivatives Asymmetry**: **The F&O segment does NOT participate in this auction.** Index futures and option contracts remain frozen at yesterday's settlement price until **09:15:00 IST**.
- **The Asymmetry Edge**: Between 09:08:15 and 09:15:00 IST, the trader knows where the underlying cash index will open, while option contract order books are completely empty. This 7-minute window allows the Cockpit to project theoretical option opening prices, calculate straddle decay or expansion, classify the market regime, and formulate precise execution orders before the bell rings.

---

### 1.2 Quantitative Decoding: What Pre-Market Data Means to the Trader

Every metric displayed on the Cockpit's Pre-Market Briefing Banner represents an actionable institutional signal:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                            PRE-MARKET DATA INTERPRETATION DECODER                                │
├─────────────────────────┬───────────────────────────────────┬────────────────────────────────────┤
│ Pre-Market Metric       │ What the Metric Quantifies        │ What This Means to the Trader      │
├─────────────────────────┼───────────────────────────────────┼────────────────────────────────────┤
│ Discovered Spot         │ Single-price volume-maximizing    │ The true anchor for the day's open.│
│ (S_disc)                │ equilibrium clearing price of cash│ Validates overnight macro news,    │
│                         │ index constituents.               │ earnings, or global gap risk.      │
├─────────────────────────┼───────────────────────────────────┼────────────────────────────────────┤
│ Overnight Gap           │ S_disc - S_prev_close             │ Quantifies opening directional     │
│ (Points & Percentage)   │ Absolute and percentage shift     │ impulse. Gaps > 0.40% signal trend;│
│                         │ from previous official close.     │ gaps < 0.15% signal rotational chop│
├─────────────────────────┼───────────────────────────────────┼────────────────────────────────────┤
│ Structural GEX Walls    │ Largest call-gamma and            │ A concentration on the book.       │
│ • Call Wall (CW)        │ put-gamma strikes.                │ Not a ceiling. Not a hedge.        │
│                         │                                   │                                    │
├─────────────────────────┼───────────────────────────────────┼────────────────────────────────────┤
│ Put-gamma strike        │ Largest put-gamma magnitude.      │ A concentration below spot.        │
│                         │                                   │ Not a floor.                       │
│                         │                                   │                                    │
├─────────────────────────┼───────────────────────────────────┼────────────────────────────────────┤
│ Call−put balance        │ Spot where call magnitude minus  │ Above it the local book is         │
│                         │ put magnitude crosses zero.       │ call-heavy. Below it, put-heavy.   │
│                         │                                   │ Neither word is a fade or a chase. │
│                         │                                   │                                    │
├─────────────────────────┼───────────────────────────────────┼────────────────────────────────────┤
│ ATM Straddle Shock %    │ Percentage change in total ATM    │ Net straddle expansion vs crush.   │
│                         │ premium (CE + PE) from yesterday  │ Negative: Theta crush dominant.    │
│                         │ to synthetic 09:15 open.          │ Positive: Volatility expansion.    │
├─────────────────────────┼───────────────────────────────────┼────────────────────────────────────┤
│ FII Net Derivatives     │ Institutional participant open    │ Contextual institutional bias.     │
│ Position                │ interest in index futures (net    │ Warns against fighting major FII   │
│                         │ long vs short contracts).         │ distribution (> 200k short fut).   │
├─────────────────────────┼───────────────────────────────────┼────────────────────────────────────┤
│ Prior Session CVD       │ Cumulative Volume Delta from the  │ Persistent buying vs selling       │
│ & Orderflow Bias        │ preceding day's closing session.  │ pressure carrying into open.       │
└─────────────────────────┴───────────────────────────────────┴────────────────────────────────────┘
```

#### Detailed Deep-Dive on Key Insights:

#### 1. Spot relative to the gamma strikes
- **Call-heavy book (regime POSITIVE):** call-gamma magnitude is larger than put-gamma magnitude. In the research sample, high absolute GEX lined up with calmer forward vol. That is context. It is not an instruction to fade the edges or to sell credit.
- **Put-heavy book (regime NEGATIVE):** put-gamma magnitude is larger. That is a label on the book. It is not a momentum forecast and it is not a reason to chase.

#### 2. The ATM Straddle Shock % (Decay vs. Expansion):
The Cockpit models overnight time decay ($T_{\text{open}} = T_{\text{prev}} - 0.5/252$) alongside jump volatility adjustments:
$$\text{Straddle Shock \%} = \frac{(V_{\text{open,CE}} + V_{\text{open,PE}}) - (V_{\text{prev,CE}} + V_{\text{prev,PE}})}{V_{\text{prev,CE}} + V_{\text{prev,PE}}} \times 100$$

- **Straddle Crush ($< -5.0\%$)**: 
  - *Occurs When*: Market opens flat ($|Gap| \le 0.15\%$) or within normal overnight variance.
  - *Trader Insight*: Implied volatility and overnight theta bleed immediately strip value from both calls and puts at 09:15:00. **Option buyers will lose money immediately even if they pick the right direction.** Option writers collect overnight decay.
- **Straddle Expansion ($> +5.0\%$)**: 
  - *Occurs When*: Discovered open gaps significantly ($> 0.35\%$), forcing the in-the-money wing's intrinsic gain to outstrip theta loss.
  - *Trader Insight*: Overnight gamma jump. Volatility expansion favors option buyers. Option writers who sold straddles/strangles face immediate margin expansion and delta imbalances.

#### 3. 7-Strike Synthetic Option Repricing Grid:
Because option chain quotes remain frozen at yesterday's close until 09:15:00, retail traders entering market orders at open frequently suffer 20% to 50% slippage. The Cockpit solves this by evaluating Black-76 forward prices across seven strikes:
$$K \in \{K_{\text{atm}} - 3s, \dots, K_{\text{atm}}, \dots, K_{\text{atm}} + 3s\}$$
- **Projected Open LTP**: The exact theoretical price where the contract should trade at 09:15:00.
- **Shock Pts & Shock %**: Projected change relative to yesterday's close.
- **Delta ($\Delta$)**: The directional exposure per point move in the underlying.
- *Trader Action*: Instead of hitting market orders at 09:15:00, traders can place limit orders right at or slightly below the Projected Open LTP during the pre-bell window, capturing fills without adverse spread widening.

---

### 1.3 The Complete 8-Regime Gamma-Aware Opening Taxonomy

At 09:08:15 IST, the discovery engine compares $S_{\text{disc}}$ against $S_{\text{prev}}$, $CW$, $PW$, and $GF$ to classify the session into one of eight mutually exclusive, institutional opening regimes:

```
┌─────────────────────────────────┬─────────────────────────────────┬──────────────┬───────────────────────────────────────────────────────┐
│ Regime Identifier               │ Quantitative Trigger Condition  │ Badge Color  │ Institutional Microstructure Thesis                   │
├─────────────────────────────────┼─────────────────────────────────┼──────────────┼───────────────────────────────────────────────────────┤
│ CALL_WALL_BREACH_SQUEEZE        │ S_disc >= Call Wall             │ 🚀 Green     │ Open is at or through the call-gamma strike.          │
│                                 │                                 │ (var(--go))  │ Favored play: watch the hold. Not a squeeze.          │
├─────────────────────────────────┼─────────────────────────────────┼──────────────┼───────────────────────────────────────────────────────┤
│ PUT_WALL_BREAKDOWN_CASCADE      │ S_disc <= Put Wall              │ 💥 Red       │ Open is at or through the put-gamma strike.           │
│                                 │                                 │ (var(--stop))│ Favored play: watch the hold. Not a cascade.          │
├─────────────────────────────────┼─────────────────────────────────┼──────────────┼───────────────────────────────────────────────────────┤
│ GAP_UP_INTO_POS_GAMMA           │ (CW - 50) <= S_disc < CW        │ 🛡️ Yellow    │ Gap up toward the call-gamma strike, still below it.  │
│                                 │ AND S_disc > Gamma Flip         │ (var(--warn))│ Call book is larger. That is not a fade.              │
├─────────────────────────────────┼─────────────────────────────────┼──────────────┼───────────────────────────────────────────────────────┤
│ GAP_DOWN_INTO_POS_GAMMA         │ PW < S_disc <= (PW + 50)        │ 🛡️ Yellow    │ Gap down toward the put-gamma strike, still above it. │
│                                 │ AND S_disc > Gamma Flip         │ (var(--warn))│ Call book is larger. That is not a floor.             │
├─────────────────────────────────┼─────────────────────────────────┼──────────────┼───────────────────────────────────────────────────────┤
│ AUCTION_BALANCED                │ |Gap %| <= 0.15%                │ ⚖️ Blue      │ Flat open. The only regime that still adjusts the     │
│                                 │                                 │(var(--spark))│ flat-open score. It is not a pin to VWAP.             │
├─────────────────────────────────┼─────────────────────────────────┼──────────────┼───────────────────────────────────────────────────────┤
│ MODERATE_DRIFT                  │ 0.15% < |Gap %| < 0.40%         │ ↔️ Blue      │ Mild directional drift within structural boundaries.  │
│                                 │ AND inside walls                │(var(--spark))│ Structural rotation towards next key liquidity pocket.│
├─────────────────────────────────┼─────────────────────────────────┼──────────────┼───────────────────────────────────────────────────────┤
│ GAP_UP_RUNAWAY                  │ Gap % >= +0.40%                 │ 📈 Green     │ Large runaway gap-up driven by external catalyst.     │
│                                 │ AND S_disc < CW                 │ (var(--go))  │ Strong open-drive momentum; watch for 09:15 hold.     │
├─────────────────────────────────┼─────────────────────────────────┼──────────────┼───────────────────────────────────────────────────────┤
│ GAP_DOWN_TRAP                   │ Gap % <= -0.40%                 │ 📉 Red       │ Large runaway gap-down driven by external catalyst.   │
│                                 │ AND S_disc > PW                 │ (var(--stop))│ Heavy institutional liquidation; watch for gap-trap.  │
└─────────────────────────────────┴─────────────────────────────────┴──────────────┴───────────────────────────────────────────────────────┘
```

---

### 1.4 Trader Action Playbook: Tactical Execution Protocol by Regime

For each regime, the Cockpit outlines a battle-tested institutional execution protocol:

#### Playbook: open at or through the call-gamma strike (`CALL_WALL_BREACH_SQUEEZE`)
- **What the code says:** the favored play is watch the hold above the call strike. The regime key still contains the word SQUEEZE so older logs stay readable. The briefing does not tell you to buy a squeeze.
- **Reading:** a call that is in the money has a higher delta. That is option math. It is not evidence that a dealer must buy futures.
- **09:15:00 Execution Trigger**:
  1. Observe the opening 3-minute candle (09:15 – 09:18 IST).
  2. If the 3-minute candle closes green **above the Call Wall** and orderflow CVD is positive, trigger long entry.
- **Preferred Vehicle & Strike**:
  - *Aggressive*: Buy ATM Call Option (delta $\approx 0.50 - 0.55$).
  - *Institutional / Defined Risk*: Bull Call Spread (Buy ATM Call, Sell OTM Call at $CW + 100$).
- **Profit Target (Take-Profit)**:
  - Target 1: $CW + 0.5 \times \text{Expected Move 1D}$ (approx $+50$ to $+75$ pts on NIFTY).
  - Target 2: $CW + 1.0 \times \text{Expected Move 1D}$.
- **Invalidation Boundary (Stop-Loss)**:
  - Spot falls back and closes below the Call Wall (e.g. $S < CW - 10\text{ pts}$). Exit immediately; failure to hold the Call Wall triggers a bull trap.
- **Caution:** do not sell calls just because price is through the call-gamma strike, and do not fade that location because a label said squeeze. The user confirms any trade. The app does not forecast a 200-point expansion.

---

#### 🔴 Playbook 2: Put Wall Breakdown Cascade (`💥 PUT WALL BREAKDOWN (CASCADE)`)
- **Microstructure Thesis**: The Put Wall represents the foundational floor of the options structure. When spot breaks below it, put writers face massive delta risk ($\Delta \to -1.0$). Option sellers are forced to dump underlying futures to cover margin requirements, triggering a cascading liquidation waterfall into negative gamma.
- **09:15:00 Execution Trigger**:
  1. Observe the opening 3-minute candle (09:15 – 09:18 IST).
  2. If the candle closes red **below the Put Wall** with expanding negative CVD and high volume, enter short.
- **Preferred Vehicle & Strike**:
  - *Aggressive*: Buy ATM Put Option (delta $\approx -0.50$ to $-0.55$).
  - *Institutional / Defined Risk*: Bear Put Spread (Buy ATM Put, Sell OTM Put at $PW - 100$).
- **Profit Target (Take-Profit)**:
  - Target 1: $PW - 0.5 \times \text{Expected Move 1D}$ (approx $-50$ to $-75$ pts on NIFTY).
  - Target 2: $PW - 1.0 \times \text{Expected Move 1D}$.
- **Invalidation Boundary (Stop-Loss)**:
  - Spot recovers and closes back above the Put Wall ($S > PW + 10\text{ pts}$). Exit immediately; reclaim indicates an institutional bear trap / spring.
- **⚠️ Cardinal Rule**: **NEVER SELL NAKED PUTS.** Do not "buy the dip" blindly when the Put Wall is violated.

---

#### 🛡️ Playbook 3: Gap-Up into Positive Gamma Resistance (`🛡️ GAP-UP INTO POS GAMMA`)
- **Reading**: Spot gaps up +0.2% to +0.4% and sits within 50 points below the call-gamma strike, with the call book larger than the put book. That is a location in the book. It is not a ceiling a dealer will sell.
- **09:15:00 Execution Trigger**:
  1. Monitor initial 09:15 – 09:20 rally test of the Call Wall.
  2. Look for price rejection at Call Wall (long upper wick, exhaustion tape, negative delta divergence).
- **Preferred Vehicle & Strike**:
  - *Income / Credit*: Bear Call Credit Spread (Sell $CW$ Call, Buy $CW + 100$ Call for protection).
  - *Directional Fade*: Buy ATM Put targeting mean-reversion down to Session VWAP.
- **Profit Target (Take-Profit)**:
  - Target 1: Session VWAP.
  - Target 2: Previous Session Close (100% Gap Fill).
- **Invalidation Boundary (Stop-Loss)**:
  - Spot breaks cleanly above Call Wall ($S > CW + 15\text{ pts}$). If breached, regime flips to Squeeze Playbook 1!

---

#### 🛡️ Playbook 4: Gap-Down into Positive Gamma Support (`🛡️ GAP-DOWN INTO POS GAMMA`)
- **Reading**: Spot gaps down -0.2% to -0.4% and sits within 50 points above the put-gamma strike, with the call book larger than the put book. That is a location in the book. It is not a floor a dealer will buy.
- **09:15:00 Execution Trigger**:
  1. Monitor 09:15 – 09:20 test of the Put Wall.
  2. Confirm absorption at the Put Wall (long lower wick, bid delta absorption, inability of bears to push lower).
- **Preferred Vehicle & Strike**:
  - *Income / Credit*: Bull Put Credit Spread (Sell $PW$ Put, Buy $PW - 100$ Put for protection).
  - *Directional Reversal*: Buy ATM Call targeting bounce to Session VWAP.
- **Profit Target (Take-Profit)**:
  - Target 1: Session VWAP.
  - Target 2: Previous Session Close (100% Gap Fill).
- **Invalidation Boundary (Stop-Loss)**:
  - 3-minute candle close below Put Wall ($S < PW - 15\text{ pts}$). If violated, regime flips to Breakdown Playbook 2!

---

#### ⚖️ Playbook 5: Balanced Auction / Flat Open (`⚖️ BALANCED AUCTION`)
- **Book reading:** a flat open (|gap| at or under 0.15%) between the gamma strikes is a location. Overnight premium change is a separate number. The code does not say market makers will pin the open.
- **09:15:00 Execution Trigger**:
  1. Allow the initial 15-minute range (09:15 – 09:30 IST) to establish High and Low.
  2. Confirm that price remains bounded inside the morning range without testing walls.
- **Preferred Vehicle & Strike**:
  - *Non-Directional Income*: Iron Condor (Sell $CW$ Call / Buy $CW+100$ Call; Sell $PW$ Put / Buy $PW-100$ Put).
  - *Expiry Day / Low Volatility*: Iron Fly centered at ATM strike.
  - *Intraday Tactical*: Fade edges (Sell near 15m high, Buy near 15m low) targeting Session VWAP.
- **Profit Target (Take-Profit)**:
  - Harvest 30% to 50% of credit received on short option structures.
- **Invalidation Boundary (Stop-Loss)**:
  - Sustained breakout outside the 15-minute opening range with expanding volume (> 1.5x average).

---

#### 📈 Playbook 6: Runaway Open-Drive Trend (`📈 GAP-UP RUNAWAY` / `📉 GAP-DOWN TRAP`)
- **Microstructure Thesis**: A massive macro or earnings gap ($> +0.40\%$ or $< -0.40\%$) that opens far beyond normal value area distribution. Often accompanied by heavy institutional futures positioning alignment.
- **09:15:00 Execution Trigger**:
  1. Inspect the 09:15 – 09:18 opening candle.
  2. An **Open-Drive** pattern occurs when the candle has virtually zero wick in the direction of the open (e.g. Low = Open on gap-up, High = Open on gap-down) and high aggressive volume.
- **Preferred Vehicle & Strike**:
  - Directional Momentum Debit Spread (Bull Call Spread for Runaway Up; Bear Put Spread for Trap Down).
- **Profit Target (Take-Profit)**:
  - Target: $1.0\times$ to $1.5\times$ 1-day Expected Move.
- **Invalidation Boundary (Stop-Loss)**:
  - Price retraces and closes back inside the previous day's value area / extreme range.

---

#### ↔️ Playbook 7: Moderate Structural Rotation (`↔️ MODERATE DRIFT`)
- **Microstructure Thesis**: Opening gap between 0.15% and 0.40% inside structural boundaries. Represents standard rotational inventory adjustment without institutional urgency.
- **09:15:00 Execution Trigger**:
  1. Wait for 09:20 – 09:30 IST to establish directional relationship with Session VWAP.
  2. If price holds above VWAP, enter structural long on pullback; if below VWAP, enter short.
- **Preferred Vehicle & Strike**:
  - ATM Calendar or Vertical Spread aligned with VWAP bias.

---

### 1.5 The 7-Minute Pre-Bell Operational Checklist (09:08:00 – 09:14:59 IST)

To execute with institutional discipline, follow this strict minute-by-minute protocol every trading morning:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                            THE TRADER'S 7-MINUTE PRE-BELL PROTOCOL                               │
├──────────────┬────────────────────────┬──────────────────────────────────────────────────────────┤
│ Clock (IST)  │ Operational Step       │ Exact Action to Take                                     │
├──────────────┼────────────────────────┼──────────────────────────────────────────────────────────┤
│ 09:08:15 IST │ 1. Listen for Chime    │ Confirm discovery audio chime sounds.                    │
│              │    & Read Spot         │ Note Discovered Spot (S_disc) and Gap (Pts / %).         │
├──────────────┼────────────────────────┼──────────────────────────────────────────────────────────┤
│ 09:09:00 IST │ 2. Identify Regime     │ Check Regime Badge on Pre-Market Banner:                 │
│              │    & Straddle Shock %  │ • Squeeze / Breakdown / Pos Gamma / Balanced?            │
│              │                        │ • Is Straddle Shock crushed (< -5%) or expanding?        │
├──────────────┼────────────────────────┼──────────────────────────────────────────────────────────┤
│ 09:10:00 IST │ 3. Inspect 7-Strike    │ Look at ATM strike and adjacent +/- 1 strike:            │
│              │    Repricing Grid      │ • Check Projected Open LTP for target CE and PE.         │
│              │                        │ • Note expected jump / shock percentage.                 │
├──────────────┼────────────────────────┼──────────────────────────────────────────────────────────┤
│ 09:11:00 IST │ 4. Select Playbook     │ Match regime to Playbooks 1 through 7:                   │
│              │    & Formulate Plan    │ • Momentum Breakout vs Mean-Reversion Fade vs Income?    │
│              │                        │ • Note Invalidation Spot boundary (hard stop level).     │
├──────────────┼────────────────────────┼──────────────────────────────────────────────────────────┤
│ 09:12:30 IST │ 5. Pre-Populate Order  │ Open broker terminal / trading pad:                      │
│              │    Pad in Broker       │ • Select target strike contract.                         │
│              │                        │ • Set limit price equal to Projected Open LTP.           │
│              │                        │ • Do NOT submit yet—wait for confirmation!               │
├──────────────┼────────────────────────┼──────────────────────────────────────────────────────────┤
│ 09:14:30 IST │ 6. Mental Alignment    │ Review Cardinal Rules:                                   │
│              │    & Risk Check        │ • If Squeeze: NEVER sell calls.                          │
│              │                        │ • If Balanced: Do NOT chase open; wait for 15m range.    │
├──────────────┼────────────────────────┼──────────────────────────────────────────────────────────┤
│ 09:15:00 IST │ 7. 🔔 Bell Rings!      │ Watch first 1-min & 3-min candle print. Verify candle    │
│              │    Confirm & Fire      │ holds above Invalidation before triggering order!        │
└──────────────┴────────────────────────┴──────────────────────────────────────────────────────────┘
```

---

### 1.6 Day-Session Intraday Gap-Fill Tracking & Dynamic Strategy Transition

The 09:08 discovered price remains an authoritative structural anchor throughout the continuous trading session (09:15 – 15:30 IST):

1. **Trade Quality Engine Confluence (Factor 13)**:
   - In [`analytics/trade_ideas.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/analytics/trade_ideas.py#L2957-L2970), the recommendation scoring engine actively rewards setups aligned with the opening regime:
     - `CALL_WALL_BREACH_SQUEEZE` / `GAP_UP_RUNAWAY`: Grants **+5 to +8 points** bonus to Bull Momentum & Call Spreads; applies heavy penalty (**-6 to -10 points**) to Put buying setups while gap remains unfilled.
     - `PUT_WALL_BREAKDOWN_CASCADE` / `GAP_DOWN_TRAP`: Grants **+5 to +8 points** bonus to Bear Momentum & Put Spreads; penalizes Call buying.
     - `AUCTION_BALANCED`: Boosts Mean-Reversion, Iceberg Absorption, and Theta Income strategies (+4 pts); penalizes high-beta directional momentum setups (-3 pts).

2. **Real-Time Dynamic Gap-Fill Monitoring**:
   As 1-minute and 5-minute bars print, `get_pre_market_state(symbol)` continuously inspects the session high and low:
   - **For an Opening Gap-Up** ($S_{\text{disc}} > S_{\text{prev}}$): The gap is declared **`FILLED`** the moment session $\text{Low} \le S_{\text{prev}}$.
   - **For an Opening Gap-Down** ($S_{\text{disc}} < S_{\text{prev}}$): The gap is declared **`FILLED`** the moment session $\text{High} \ge S_{\text{prev}}$.

3. **Intraday Strategy Transition Upon Gap Fill**:
   The moment the gap is declared `FILLED`:
   - Directional runaway bonuses and counter-trend penalties are immediately lifted.
   - The market has completed its overnight inventory rebalancing and re-entered the prior day's value area.
   - The Cockpit automatically elevates **Mean-Reversion and VWAP Absorption** setups, warning traders that morning open-drive momentum has concluded and two-sided rotational auction dynamics have resumed.

4. **Intraday Opening Baseline HUD Strip (09:25 – 15:30 IST)**:
   After 09:25 IST, the extensive pre-bell 7-strike matrix seamlessly contracts into a persistent, compact telemetry HUD banner positioned directly above the Live Recommendations Table:
   ```
   🌅 Day Baseline: Open ₹22,680.0 (+80.0 / +0.35%)  [🚀 CALL WALL BREACH]  [⚡ GAP UNFILLED]
   The open is still +80.0 pts from the previous close. Play: watch the hold.
   ```
   When the gap is filled later in the session, the HUD immediately updates:
   ```
   🌅 Day Baseline: Open ₹22,680.0 (+80.0 / +0.35%)  [🚀 CALL WALL BREACH]  [✅ GAP FILLED]
   The open has traded back through the previous close. That is a fill. It is not a mean-reversion order.
   ```

---

## Part 2: Closing Auction Session (CAS) & F&O Extended Close (15:30 – 16:00 IST)

### 2.1 The Two Closing Milestones (15:30 vs 15:40 IST)
In the Indian microstructure:
1. **15:30:00 IST**: Regular continuous trading in Equity Cash halts. Continuous option prices freeze.
2. **15:30 – 15:35/15:40 IST (Cash Closing Auction Session - CAS)**:
   - Cash market enters a closing call auction.
   - The exchange calculates the **Official Settlement Price** as the **30-minute volume-weighted average price (VWAP)** between 15:00:00 and 15:30:00 IST.
3. **15:40:00 IST (F&O Extended Close)**:
   - Under the NSE circular framework (effective August 3, 2026), equity derivatives trade until **15:40:00 IST** (`FNO_CLOSE_MIN = 15*60 + 40`).
   - This provides derivatives traders a 10-minute window to hedge positions directly against the cash CAS settlement indications.

---

### 2.2 Post-Market Settlement & Pinning Scorecard
Once the session concludes after 15:30/15:40 IST, the Cockpit renders the **Post-Market Settlement Scorecard**:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              POST-MARKET SETTLEMENT SCORECARD                          │
├──────────────────────────┬──────────────────────────────┬──────────────────────────────┤
│ Metric                   │ Calculation Formula          │ Institutional Significance   │
├──────────────────────────┼──────────────────────────────┼──────────────────────────────┤
│ Official Settlement Spot │ 30-min Cash VWAP (from Dhan) │ Final contract expiry mark   │
├──────────────────────────┼──────────────────────────────┼──────────────────────────────┤
│ Settlement Divergence    │ S_settle - S_continuous     │ Measures closing auction slip│
│                          │                              │ vs continuous 15:30 price    │
├──────────────────────────┼──────────────────────────────┼──────────────────────────────┤
│ Distance to the strike   │ 100 - (|S_settle - strike|/step)│ How close the settle printed to a │
│                          │                              │ gamma strike. Not a hedge score.   │
└──────────────────────────┴──────────────────────────────┴──────────────────────────────┘
```

#### Distance of the settle to a gamma strike:
$$\text{Closeness} = \max\left(0, 100 - \frac{|S_{\text{settle}} - \text{Strike}|}{\text{Step Size}} \times 100\right)$$

- A high number means the official settle printed near that strike.
- A low number means it printed away from that strike.
- Neither number says a dealer pinned the close or was forced to liquidate a hedge.

---

## Part 3: High-Conviction Recommendations Lifecycle

### 3.1 Recommendation State Machine Lifecycle Architecture

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

#### State Machine Invariants:
1. **Dwell Time Lease (180 Seconds)**: Once promoted to `ACTIVE`, an options recommendation card is guaranteed a minimum dwell lease of 180 seconds. It will not vanish on ephemeral microsecond tick dips.
2. **Score Hysteresis ($\Delta = 10\text{ pts}$)**:
   - Initial entry requires $\text{Score} \ge 75$ (or 85 for High Conviction).
   - Once `ACTIVE`, the card remains in active display even if score fluctuates down to **65** ($\text{min\_score} - 10$).
   - Only when score stays below 65 *after* the 180s dwell lease expires does the card transition to `DEGRADED`.
3. **Target 1 Touch Recognition (`TARGET_HIT`)**:
   - Monitored on live spot and option LTP ticks.
   - For Calls: Triggers when $\text{Spot} \ge \text{Target 1 Spot}$.
   - For Puts: Triggers when $\text{Spot} \le \text{Target 1 Spot}$.
   - Once hit, the card prominently displays the green `TARGET 1 REACHED` celebration state.
4. **Spot Invalidation Breach (`INVALIDATED`)**:
   - Triggers when live spot breaches the structural invalidation boundary (e.g. Call Spot < Invalidation).
   - Once invalidated, the card is immediately purged from active display to prevent executing flawed setups.

---

### 3.2 Pinned Cards Retention Rules
Previously, bookmarked recommendation cards could be cleared across intraday restarts or daily scans. The updated lifecycle model enforces strict operational persistence:

```
[Card Generated & Bookmarked by User]
                  │
                  ▼
   Card Stored in Persistent SQLite DB
                  │
                  ▼
        Check Daily & On Each Scan:
                  │
       ┌──────────┴──────────┐
       ▼                     ▼
[User Clicks 'Unpin']    [today > contract_expiry]
       │                     │
       └──────────┬──────────┘
                  ▼
         Card Evicted / Expired
```

#### Retention Guarantees:
1. **Cross-Session Persistence**: Pinned cards remain active across application restarts, browser refreshes, and multi-day holds.
2. **Contract Expiry Enforcement**:
   - Evaluated by `is_contract_expired(record)` in [`data/pre_post_market.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/pre_post_market.py#L360-L402).
   - Card remains active until the end of the contract expiry date.
   - Once `today > contract_expiry_date`, the card is automatically retired.
3. **Multi-Format Parsing**:
   - Supports ISO format: `payload["expiry"] = "2026-10-15"`
   - Supports formatted symbol string: `"NIFTY 13 OCT 22700 CE"` $\to$ extracts Day (`13`), Month (`OCT`), and Year (`2026`).

---

### 3.3 Recommendation Card Visual Enhancements & "Time Since Last Active" Counter

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 🟢 NIFTY 13 OCT 22700 CE  [NEW] ★ Pinned  🟢 Active: 2m 15s           Grade A+ (95/100)│
│ ────────────────────────────────────────────────────────────────────────────────────── │
│ Entry: ₹45.2   |   T1: ₹68.0 (+50%)   |   T2: ₹90.0 (+99%)   |   SL: ₹28.0 (-38%)      │
│ 📦 2 Lots (50 Qty) · Outlay: ₹2,260 (0.9% Port) · Conservative Risk Budget             │
│ 🛑 Spot Invalidation: ₹22,640 (Testing Call Wall 22,700)                               │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

#### 1. "Time Since Last Active" Dynamic Counter
Every recommendation card (across Pinned Cards, Live Trade Ideas, and Confluence Strategy Clusters) displays an ultra-low-latency (<0.05ms) state-aware timer chip in the card header:

| Card State | Chip Badge Format | Microstructure Meaning |
| :--- | :--- | :--- |
| **`ACTIVE`** | `🟢 Active: 45s` / `🟢 Active: 2m 15s` | Setup is live, qualified ($\ge 75$), and actively dwelling within its valid execution window. Shows continuous dwell time since initial activation. |
| **`TARGET_HIT`** | `🎯 Target Hit (30s ago)` | Take-profit milestone (Target 1) was achieved. Shows elapsed time since target was reached. |
| **`INVALIDATED`** | `🛑 Inval (1m ago)` | Structural stop/invalidation was breached. Displays elapsed time since setup was invalidated. |
| **`DEGRADED`** | `⏱️ Last Active: 3m 40s ago` | Setup dropped below conviction threshold ($\text{Score} < 65$) after dwell expiration. Informs operator exactly how long the setup has been dormant. |
| **`QUALIFIED`** (Pre-Active) | `⏱️ Pending Active` | Card is newly qualified but awaiting first active promotion lease confirmation. |

- **Sub-Millisecond Live Recalculation**:
  `update_trade_idea_live_telemetry()` continuously recomputes `time_since_active_label` and `time_since_active_sec` on every incoming WebSocket option and underlying tick without waiting for full 3-second cycle re-evaluations.
- **Confluence Strategy Matrix Tracking**:
  In multi-leg clustered cards (`_render_cluster_card`), the strategy comparison table features a dedicated **`Active Dwell`** column displaying individual leg dwell timers (e.g. `🟢 Active: 1m 20s` vs `⏱️ Last Active: 4m ago`).

#### 2. Visual Polish & Styling
1. **Card Borderlines**: Clean, high-contrast borderlines (`1.5px solid rgba(34,197,94,.4)` for CE, `rgba(239,68,68,.4)` for PE) with subtle glassmorphic elevation.
2. **Blinking "NEW" Pill**:
   - Rendered on newly generated recommendations whose detection timestamp is within **5 minutes** (`age_seconds < 300`).
   - Uses CSS animation `rec-pulse-badge` (1.2s infinite pulse) that automatically subsides after 5 minutes.
3. **Full Contract Notation**:
   - Uses full official derivative format:
     $$\text{SYMBOL} \quad \text{DD} \quad \text{MMM} \quad \text{STRIKE} \quad \text{TYPE}$$
     Example: `NIFTY 13 OCT 22700 CE` or `BANKNIFTY 15 OCT 55200 PE`.

---

### 3.4 Conviction Trajectory Audit Redesign
The **Conviction Trajectory Audit Table** tracks how an option recommendation's score evolved as market microstructure shifted:

#### Key Fixes & Upgrades:
1. **Reverse Chronological Sorting**:
   - The latest audit snapshot is strictly on top (`records.sort(key=lambda x: x["timestamp"], reverse=True)`).
2. **Granular Microstructure Reasoning**:
   - Replaced generic static strings with specific causal attribution:
     - *Directional Bias Shifts*: `"Directional bias strengthened +0.6 (from +3.2 to +3.8)"`
     - *Wall Proximity Shifts*: `"Testing Call Wall ₹22,500 (103 pts proximity)"`
     - *Gamma Buffer Changes*: `"Positive gamma buffer (+3 pts above Flip)"`
3. **Compact & Legible Typography**:
   - Padding reduced to `4px 8px` with tabular monospace numerals.
   - Green color coding for positive conviction updates (`+5`), red for fades (`-10`), and neutral for steady conviction.

---

## Part 4: Market Calendar & Holiday Suppression

To prevent phantom chart bars or stale data processing during exchange holidays:
1. **`is_trading_day()` Verification**:
   - Checks NSE holiday calendar against `data/holidays.py`.
   - Any weekday declared an official NSE trading holiday (e.g., Diwali, Independence Day, Gandhi Jayanti) is automatically routed to `OFF_HOURS`.
2. **Weekend Suppression**:
   - Saturdays and Sundays are hard-routed to `OFF_HOURS` (`weekday >= 5`).
3. **Chart Guarding**:
   - OrderFlow Sentinel and Candlestick Tape chart queries suppress synthetic live updates when `is_trading_day(today)` is false.

---

## Part 5: Operator Checklist & Quick Reference

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              OPERATOR DAILY ACTION CHECKLIST                           │
├─────────────┬──────────────────────────────────────────────────────────────────────────┤
│ 09:00 IST   │ Terminal initializes; Pre-Market Order Entry status displayed.           │
│ 09:07:30 IST│ Adaptive 3-second polling automatically engages. Stand by for chime.    │
│ 09:08:15 IST│ 🔔 Morning Bell Sounds! Opening price discovered.                        │
│ 09:08–09:14 │ Review Pre-Bell Tactical Briefing:                                       │
│             │ • Check Opening Gap Regime (Wall breach vs Balanced Auction)             │
│             │ • Note Synthetic Option Opening LTPs (Delta shock expected jumps)        │
│             │ • Align with Favored Play (MOMENTUM vs REVERSAL)                         │
│ 09:15:00 IST│ Bell rings! Execute trade ideas that hold above spot invalidation.      │
│ 15:30:00 IST│ Continuous equity trading closes. CAS 30m VWAP calculation begins.       │
│ 15:30–15:40 │ F&O Extended Window: Manage final hedges prior to 15:40 derivatives close│
│ 15:40:00 IST│ Review Post-Market Settlement Scorecard & Dealer Pin Efficiency Index.   │
└─────────────┴──────────────────────────────────────────────────────────────────────────┘
```

---

## 🔗 Related Technical Documentation
- [🎯 Options Recommendation Strategies & Alpha Playbook](/?view=options_strategies_guide)
- [🎯 Target Engine — Scenario & Repricing Suite Technical Guide](/?view=target_engine_guide)
- [🔬 Options Analytics & Microstructure Intelligence Guide (IOAS v3.0)](/?view=options_analytics_guide)
- [⚡ OrderFlow Sentinel Stream Signals Field Guide](/?view=orderflow_guide)
- [🧭 Market Intelligence: Sector Breadth & News Sentiment Guide](/?view=market_intelligence_guide)
- [🎙️ Session Commentary & Suggestion Bites Guide](/?view=session_commentary_guide)
- [📈 Signal Efficiency & MFE/MAE Performance Guide](/?view=signal_efficiency_guide)
- [📊 Quantitative Calculations & Formulas Reference](/?view=data_dictionary)
- [🧭 Participant Open Interest Guide](/?view=participant_guide)
- [🗺️ Indian GEX Playbook & Research Manual](/?view=gex_playbook)
- [📘 Master Cockpit User Guide](/?view=user_guide)
