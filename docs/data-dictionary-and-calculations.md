# 📊 Cubera GEX Data Dictionary & Quantitative Calculations Reference

This reference document catalogs every data point ingested from **Dhan REST APIs** and **Dhan WebSocket Streams**, as well as the complete mathematical and quantitative formulas used to derive institutional indicators, Gamma Exposure (GEX) levels, Black-76 forward repricing, reflection barrier probabilities, and execution risk metrics.

---

## Part 1: Raw Data Points Ingested from Dhan

### 1. Dhan WebSocket Real-Time Stream (`wss://api-feed.dhan.co`)
The application maintains a persistent binary WebSocket connection streaming packets at sub-millisecond wire latency.

| Packet Code | Packet Name | Exchange Segment | Wire Fields Ingested | Description |
| :--- | :--- | :--- | :--- | :--- |
| **`code=2`** | **Ticker Packet** | `IDX_I`, `NSE_FNO`, `BSE_FNO` | `ltp`, `ltt` | Last Traded Price (float, 2 decimals) and Last Traded Time (epoch seconds). |
| **`code=4`** | **Quote Packet** | `IDX_I`, `NSE_FNO`, `BSE_FNO` | `ltp`, `ltt`, `ltq`, `volume`, `open`, `high`, `low`, `prev_close`, `day_avg_price`, `total_buy_qty`, `total_sell_qty` | Comprehensive quote snapshot emitted on trades and price level changes. |
| **`code=5`** | **Open Interest Packet** | `NSE_FNO`, `BSE_FNO` | `oi`, `security_id` | Real-time Open Interest for active derivatives contracts (emitted by exchange on contract open/close). |
| **`code=8`** | **5-Level Depth Packet** | `NSE_FNO`, `IDX_I` | 5 Bids (px, qty, orders), 5 Asks (px, qty, orders) | Market depth book used for Bid-Ask spread analysis and iceberg order detection. |

#### Subscribed Instruments on WebSocket:
1. **Underlying Index Spot**: `IDX_I:13` (NIFTY 50), `IDX_I:25` (BANKNIFTY), `IDX_I:51` (SENSEX).
2. **Market Volatility**: `IDX_I:26` (INDIA VIX).
3. **Index Futures**: Near-month futures contracts for NIFTY, BANKNIFTY, and SENSEX.
4. **Active Recommended Options**: Dynamically subscribed option contracts (Top 3 CE strikes + Top 3 PE strikes resolved via security master).

---

### 2. Dhan REST APIs (`https://api.dhan.co/v2`)

| Endpoint | Method | Response Fields Ingested | Purpose |
| :--- | :--- | :--- | :--- |
| **`/marketfeed/quote`** | `POST` | `last_price`, `ohlc`, `volume`, `oi`, `prev_close`, `depth` | Static fallback quote and baseline spot calibration. |
| **`/optionchain`** | `POST` | Array of strikes with `call_oi`, `put_oi`, `call_ltp`, `put_ltp`, `call_volume`, `put_volume`, `call_iv`, `put_iv` | Full option chain snapshot for GEX solver and profile generation. |
| **`/charts/historical`** | `POST` | `start_time`, `open`, `high`, `low`, `close`, `volume` | Intraday (1-min) and daily historical candles for VWAP, ATR, and Sentinel orderflow replay. |
| **Dhan Scrip Master Cache** | `GET` | `SEM_SMST_SECURITY_ID`, `SEM_INSTRUMENT_NAME`, `SEM_EXPIRY_DATE`, `SEM_STRIKE_PRICE`, `SEM_OPTION_TYPE` | Local CSV cache used for $O(1)$ resolution of exchange Security IDs. |

---

## Part 2: Calculated & Derived Metrics (Mathematical Formulas)

### 1. Gamma Exposure (GEX) & Structural Walls

#### A. Single Strike Gamma Exposure ($\text{GEX}_k$)
Gamma measures the rate of change of delta per 1-point move in the underlying spot price:
$$\Gamma_k = \frac{\phi(d_1)}{S \cdot \sigma \cdot \sqrt{T}}$$
Where:
- $\phi(x) = \frac{1}{\sqrt{2\pi}} e^{-\frac{x^2}{2}}$ (Standard Normal Probability Density Function).
- $d_1 = \frac{\ln(S / K) + (r + \frac{1}{2}\sigma^2)T}{\sigma\sqrt{T}}$.
- $S$ = Current Spot price, $K$ = Strike price.
- $\sigma$ = Implied Volatility (annualized), $T$ = Days to expiry $/ 365$.
- $r$ = Risk-free rate (typically $6.5\%$ for Indian Sovereign yield).

The monetary Gamma Exposure (in ₹ Crores per 1% spot move) for strike $k$:
$$\text{GEX}_k = \Gamma_k \times S^2 \times \text{OI}_k \times \text{LotSize} \times 0.01 \times \text{DealerSign}$$

#### B. Dealer Sign Assumption (Indian Market Mode: `short_call_short_put`)
- **Calls**: Retail/speculative traders buy calls; dealers are net **Short Calls** $\rightarrow \text{DealerSign} = -1$.
- **Puts**: Option writers are institutional; retail hedges with puts $\rightarrow \text{DealerSign} = +1$.

#### C. Structural Level Calculations
- **Call Wall ($CW$)**: Strike with maximum Call Open Interest:
  $$CW = \operatorname{argmax}_K (\text{Call OI}_K)$$
- **Put Wall ($PW$)**: Strike with maximum Put Open Interest:
  $$PW = \operatorname{argmax}_K (\text{Put OI}_K)$$
- **Gamma Flip ($GF$)**: The price level where cumulative dealer gamma shifts from positive to negative:
  $$GF = S^* \quad \text{such that} \quad \sum_K \text{GEX}_K(S^*) = 0$$
- **Max Pain**: The strike at which option writers incur minimum total payout:
  $$\text{MaxPain} = \operatorname{argmin}_S \sum_K \left[\max(0, S - K) \cdot \text{CallOI}_K + \max(0, K - S) \cdot \text{PutOI}_K\right]$$

---

### 2. Target Engine Quantitative Pricing Suite

#### A. Analytical Black-76 Forward Option Pricing
Used for options on index futures and forward spot levels:
$$d_1 = \frac{\ln(F / K) + \frac{1}{2}\sigma^2 T}{\sigma \sqrt{T}}, \quad d_2 = d_1 - \sigma \sqrt{T}$$
$$\text{Call Value } (C) = e^{-rT} \left[ F \cdot \Phi(d_1) - K \cdot \Phi(d_2) \right]$$
$$\text{Put Value } (P) = e^{-rT} \left[ K \cdot \Phi(-d_2) - F \cdot \Phi(-d_1) \right]$$

#### B. 1-SD Horizon Expected Move ($\sigma_h$)
For a scenario horizon of $h$ trading minutes (where 1 trading day = 375 minutes):
$$\sigma_h = \text{Spot} \times \sigma_{\text{atm}} \times \sqrt{\frac{h}{375 \times 252}}$$

#### C. Brownian Reflection Principle Touch Probability ($P_{\text{touch}}$)
Closed-form analytical probability that the underlying touches barrier level $B$ within horizon $h$:
$$P(\text{touch barrier } B) = 2 \cdot \left[ 1 - \Phi\left( \frac{|B - \text{Spot}|}{\sigma_h} \right) \right]$$

#### D. Probability-Weighted Net Expected Value (Net EV)
$$\text{Net EV (pts)} = \left[ P(\text{touch Target 1}) \times (V_{\text{tgt1}} - V_0) \right] - \left[ P(\text{touch Invalidation}) \times (V_0 - V_{\text{sl}}) \right]$$

#### E. Greek Attribution Decomposition
Expected price change decomposed into first- and second-order derivatives:
$$\Delta V \approx \underbrace{\Delta \cdot \Delta F}_{\text{Delta PnL}} + \underbrace{\frac{1}{2}\Gamma (\Delta F)^2}_{\text{Gamma PnL}} + \underbrace{\mathcal{V} \cdot \Delta \sigma}_{\text{Vega PnL}} + \underbrace{\Theta \cdot \Delta t}_{\text{Theta Bleed}}$$

---

### 3. Defined-Risk Vertical Spreads

For Bull Call Spreads (CE) and Bear Put Spreads (PE):
- **Net Debit Outlay**:
  $$\text{Debit} = \text{LTP}_{\text{long}} - \text{LTP}_{\text{short}}$$
- **Maximum Risk**:
  $$\text{Max Risk} = \text{Debit} \times \text{LotSize}$$
- **Maximum Profit**:
  $$\text{Max Profit} = (\text{Spread Width} - \text{Debit}) \times \text{LotSize}$$
- **Risk-to-Reward Ratio**:
  $$\text{R:R} = \frac{\text{Spread Width} - \text{Debit}}{\text{Debit}}$$

---

### 4. Put-Call Parity & Synthetic Futures Basis

Monitors no-arbitrage conditions across European options:
$$C - P = S - K \cdot e^{-rT} \implies \text{Synthetic Future} = C - P + K$$
$$\text{Basis Discrepancy} = \text{Actual Futures Price} - \text{Synthetic Futures Price}$$
- Discrepancy $> +15\text{ pts}$: Long cash-and-carry dislocation.
- Discrepancy $< -15\text{ pts}$: Reverse cash-and-carry dislocation.

---

### 5. Position Sizing & Risk Management Formulas

1. **Trade Risk Budget (₹)**:
   $$\text{Budget}_{\text{risk}} = \text{AccountCapital} \times \left(\frac{\text{RiskPct}}{100}\right)$$
2. **Capital Outlay Cap (₹)** (Max 15% of account in a single trade):
   $$\text{Cap}_{\text{outlay}} = \text{AccountCapital} \times 0.15$$
3. **Risk per Lot (₹)**:
   $$\text{Risk}_{\text{lot}} = (\text{LTP} - \text{StopLoss}) \times \text{LotSize}$$
4. **Capital Outlay per Lot (₹)**:
   $$\text{Outlay}_{\text{lot}} = \text{LTP} \times \text{LotSize}$$
5. **Recommended Lots**:
   $$\text{Lots} = \max\left(1, \; \min\left(\left\lfloor \frac{\text{Budget}_{\text{risk}}}{\text{Risk}_{\text{lot}}} \right\rfloor, \; \left\lfloor \frac{\text{Cap}_{\text{outlay}}}{\text{Outlay}_{\text{lot}}} \right\rfloor\right)\right)$$
