# 📊 Indian-GEX Data Dictionary & Calculation Reference Guide

This reference document catalogs every data point ingested from **Dhan REST APIs** and **Dhan WebSocket Streams**, as well as the complete mathematical and quantitative formulas used to derive institutional indicators, Gamma Exposure (GEX) levels, and real-time execution metrics.

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

#### B. Dealer Sign Assumption
- **Calls**: Option buyers are typically retail/speculative; dealers are net **Short Calls** $\rightarrow \text{DealerSign} = -1$ (or $+1$ in standard customer sign convention). When spot rises, short call dealers must **buy futures/spot** to stay delta-neutral.
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

### 2. Intraday OrderFlow & Volume Profiling

#### A. Cumulative Volume Delta (CVD)
Measures aggressive market buyer volume minus aggressive market seller volume:
$$\Delta V_t = V_{\text{buyer\_initiated}} - V_{\text{seller\_initiated}}$$
$$\text{CVD}_T = \sum_{t=0}^T \Delta V_t$$
In 1-minute bars without tick-level classification, tick-rule approximation is used:
$$\Delta V_t = \text{Volume}_t \times \left(\frac{2 \cdot (\text{Close}_t - \text{Low}_t)}{\text{High}_t - \text{Low}_t} - 1\right)$$

#### B. Volume Weighted Average Price (VWAP) & Standard Deviation Bands
$$\text{VWAP}_T = \frac{\sum_{t=1}^T \text{TypicalPrice}_t \times \text{Volume}_t}{\sum_{t=1}^T \text{Volume}_t} \quad \text{where} \quad \text{TypicalPrice}_t = \frac{\text{High}_t + \text{Low}_t + \text{Close}_t}{3}$$

$$\sigma_{\text{VWAP}} = \sqrt{\frac{\sum_{t=1}^T \text{Volume}_t \times (\text{TypicalPrice}_t - \text{VWAP}_T)^2}{\sum_{t=1}^T \text{Volume}_t}}$$
$$\text{Upper Band} = \text{VWAP} + 1.0 \times \sigma_{\text{VWAP}}, \quad \text{Lower Band} = \text{VWAP} - 1.0 \times \sigma_{\text{VWAP}}$$

---

### 3. Directional Bias Score (-10.0 to +10.0)
Synthesizes 8 independent market microstructure indicators into a single institutional conviction rating:

$$\text{Bias Score} = \sum_{i=1}^8 w_i \times S_i$$

| Factor ($i$) | Weight ($w_i$) | Input Metric | Bullish Condition ($+1$) | Bearish Condition ($-1$) |
| :--- | :---: | :--- | :--- | :--- |
| **PCR Trend** | $1.5$ | Put-Call Ratio ($\frac{\sum \text{Put OI}}{\sum \text{Call OI}}$) | $\text{PCR} > 1.25$ or $\Delta \text{PCR} > +0.10$ | $\text{PCR} < 0.75$ or $\Delta \text{PCR} < -0.10$ |
| **Wall Alignment** | $1.5$ | Spot location relative to Walls | Spot within $25\%$ of Call Wall | Spot within $25\%$ of Put Wall |
| **Gamma Regime** | $1.5$ | Spot vs Gamma Flip | Spot $> GF$ (Low vol, squeeze risk) | Spot $< GF$ (High vol, cascade risk) |
| **CVD Slope** | $1.5$ | 15-minute Cumulative Volume Delta | $\text{CVD}$ rising with higher highs | $\text{CVD}$ falling with lower lows |
| **VWAP Location** | $1.0$ | Spot vs Session VWAP | $\text{Spot} > \text{VWAP} + 0.5\sigma$ | $\text{Spot} < \text{VWAP} - 0.5\sigma$ |
| **OI Velocity** | $1.0$ | Strike-level $\Delta \text{OI}$ in last 15m | Heavy Call Unwinding + Put Addition | Heavy Put Unwinding + Call Addition |
| **IV Skew** | $1.0$ | $\text{OTM Put IV} - \text{OTM Call IV}$ | Put IV softening (hedges removed) | Put IV surging (panic downside hedge) |
| **VIX Delta** | $1.0$ | Day change in India VIX | $\text{VIX} < -3\%$ (calm environment) | $\text{VIX} > +4\%$ (fear expansion) |

---

### 4. Real-Time Delta Mark-to-Market (MTM) Option Pricing

When a raw exchange WebSocket trade tick has not yet arrived for a specific option contract, the system updates the option's LTP with sub-millisecond latency using the live spot move and Black-Scholes Delta:

$$\Delta_{\text{Call}} = \mathcal{N}(d_1), \quad \Delta_{\text{Put}} = \mathcal{N}(d_1) - 1.0$$
$$\Delta S = S_{\text{live\_ws}} - S_{\text{snapshot}}$$
$$\text{LTP}_{\text{live}} = \max\left(\text{LTP}_{\text{snapshot}} + (\Delta \times \Delta S), \; \text{IntrinsicPrice} + 0.50, \; 0.50\right)$$

Where:
- $\text{IntrinsicPrice}_{\text{Call}} = \max(0, S_{\text{live}} - K)$
- $\text{IntrinsicPrice}_{\text{Put}} = \max(0, K - S_{\text{live}})$

---

### 5. Position Sizing & Risk Management Formulas

The system calculates institutional lot sizes dynamically per trade recommendation based on capital and maximum drawdown tolerance:

1. **Trade Risk Budget (₹)**:
   $$\text{Budget}_{\text{risk}} = \text{AccountCapital} \times \left(\frac{\text{RiskPct}}{100}\right)$$
2. **Capital Outlay Cap (₹)** (Max 15% of account in a single option purchase):
   $$\text{Cap}_{\text{outlay}} = \text{AccountCapital} \times 0.15$$
3. **Risk per Lot (₹)**:
   $$\text{Risk}_{\text{lot}} = (\text{LTP} - \text{StopLoss}) \times \text{LotSize}$$
4. **Capital Outlay per Lot (₹)**:
   $$\text{Outlay}_{\text{lot}} = \text{LTP} \times \text{LotSize}$$
5. **Recommended Lots**:
   $$\text{Lots} = \max\left(1, \; \min\left(\left\lfloor \frac{\text{Budget}_{\text{risk}}}{\text{Risk}_{\text{lot}}} \right\rfloor, \; \left\lfloor \frac{\text{Cap}_{\text{outlay}}}{\text{Outlay}_{\text{lot}}} \right\rfloor\right)\right)$$
