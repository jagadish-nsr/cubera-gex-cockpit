# 🎯 Target Engine — Scenario & Repricing Suite Technical & Operational Guide

> **Module:** Target Engine v1.0 (IOAS Quant Pricing & Decision-Support Suite)  
> **Applicable Indices:** NIFTY 50 (`NIFTY-FUT`), BANK NIFTY (`BANKNIFTY-FUT`), SENSEX (`SENSEX-FUT`)  
> **Underlying Mechanics:** Black-76 Analytical Forward Pricing, Reflection-Principle Barrier Probabilities, Dirichlet Scenario Modeling, Greek Attribution Decomposition  
> **Audience:** Quantitative traders, index derivatives discretionary traders, and systematic risk managers

---

## 1. Executive Summary & Core Objective

The **Target Engine** is a rigorous quantitative decision-support suite designed to bridge the gap between underlying microstructure analysis (GEX, OrderFlow, Session VWAP) and actual index options contract pricing.

Historically, retail and discretionary trading setups have relied on crude heuristic approximations—such as assuming a fixed $+35\%$ profit target, $-20\%$ stop loss, or linear point targets on option premiums. In reality, option premiums are non-linear derivatives governed by forward price distribution, volatility skew dynamics, and accelerating time decay ($\Theta$).

### 🛡️ Core Philosophy: Decision-Support, Not a Prediction Oracle
1. **Scenario-Conditioned Probabilities, Never a Single Price:**
   The market is fundamentally stochastic. The engine never emits a single "price forecast." Instead, it outputs a complete joint probability distribution:
   $$\{ \text{Scenario } s, P(s), \text{Underlying Target } F_s, \text{Forecast IV } \sigma_s, \text{Repriced Option Premium } V_s \}$$
2. **Explainable Analytical Pricing (No Black Boxes):**
   Every number displayed—from expected value to scenario probability—is derived from explicit closed-form mathematical equations (Black-76, Brownian reflection principle) and visible feature weights.
3. **Full Cross-Platform Integration:**
   The Target Engine does not sit in isolation. It powers:
   - The dedicated **Target Engine Tab** (interactive scenarios, ranked candidate payoffs, "what-if" repricer).
   - **Trade Recommendations Engine** (closed-form Black-76 target/stop pricing, touch probabilities $P(\text{touch})$, Net EV).
   - **Pre-Market 7-Minute Alpha Window** (09:08–09:15 IST opening option shock repricing & straddle expansion/crush).
   - **OrderFlow Sentinel Tape** (dynamic vol & GEX wall target calibration, live contract play chips).
   - **Signal Efficiency Analytics** (model calibration alpha against theoretical diffusion, options expectancy realization curve).

---

## 2. System Scope & Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       TARGET ENGINE ARCHITECTURE                                        │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                         │
│   ┌──────────────────────────┐    ┌──────────────────────────┐    ┌─────────────────────────────────┐   │
│   │   OrderFlow & VWAP Tape  │    │      GEX Option Chain    │    │     Dhan Real-Time Telemetry    │   │
│   │   CVD, Delta, RVol, P/C  │    │   Walls, Skew, ATM IV    │    │   Spot Index & Futures Price    │   │
│   └────────────┬─────────────┘    └────────────┬─────────────┘    └────────────────┬────────────────┘   │
│                │                               │                                   │                    │
│                └───────────────────────────────┼───────────────────────────────────┘                    │
│                                                ▼                                                        │
│                                ┌───────────────────────────────┐                                        │
│                                │   Feature Extraction Engine   │                                        │
│                                │   (features.py - 10 Factors)  │                                        │
│                                └───────────────┬───────────────┘                                        │
│                                                ▼                                                        │
│                                ┌───────────────────────────────┐                                        │
│                                │   Scenario Probabilities      │                                        │
│                                │   (scenarios.py - Dirichlet)  │                                        │
│                                └───────────────┬───────────────┘                                        │
│                                                │                                                        │
│               ┌────────────────────────────────┴───────────────────────────────┐                        │
│               ▼                                                                ▼                        │
│ ┌───────────────────────────┐                                    ┌───────────────────────────┐          │
│ │ Underlying Price Targets  │                                    │   Term Structure & IV     │          │
│ │ (levels.py - σ_h & Walls) │                                    │  (iv_forecast.py - Skew)  │          │
│ └─────────────┬─────────────┘                                    └─────────────┬─────────────┘          │
│               │                                                                │                        │
│               └────────────────────────────────┬───────────────────────────────┘                        │
│                                                ▼                                                        │
│                                ┌───────────────────────────────┐                                        │
│                                │   Black-76 Repricing Engine   │                                        │
│                                │  (pricer.py - Prices & Greeks)│                                        │
│                                └───────────────┬───────────────┘                                        │
│                                                ▼                                                        │
│                                ┌───────────────────────────────┐                                        │
│                                │  Greek Attribution Engine     │                                        │
│                                │  (Delta, Gamma, Vega, Theta)  │                                        │
│                                └───────────────┬───────────────┘                                        │
│                                                ▼                                                        │
│                                ┌───────────────────────────────┐                                        │
│                                │ Candidate Selector & Ranking  │                                        │
│                                │ (selector.py - Net EV & R:R)  │                                        │
│                                └───────────────┬───────────────┘                                        │
│                                                │                                                        │
│         ┌───────────────────────┬──────────────┴───────────────┬───────────────────────┐                │
│         ▼                       ▼                              ▼                       ▼                │
│ ┌───────────────┐      ┌─────────────────┐           ┌───────────────────┐    ┌─────────────────┐       │
│ │ Target Engine │      │ Trade Ideas     │           │ Pre-Bell 7-Min    │    │ Signal          │       │
│ │ Tab Suite     │      │ P(Touch), Net EV│           │ Option Shock      │    │ Calibration     │       │
│ └───────────────┘      └─────────────────┘           └───────────────────┘    └─────────────────┘       │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Supported Indices & Horizons
- **Instruments:**
  - `NIFTY 50` — every Tuesday, 50-point strikes, lot 65.
  - `BANK NIFTY` — last Tuesday of the month, 100-point strikes, lot 30.
  - `SENSEX` — every Thursday, 100-point strikes, lot 20.
  A holiday moves that expiry to the previous trading day.
- **Time Horizons ($h$):**
  - **$30\text{ mins}$ ($0.5\text{ hr}$):** Rapid orderflow continuation or scalping reaction.
  - **$60\text{ mins}$ ($1.0\text{ hr}$):** Morning trend extension following the 09:15–09:45 opening range.
  - **$120\text{ mins}$ ($2.0\text{ hr}$):** Default tactical intraday swing horizon.
  - **$240\text{ mins}$ ($4.0\text{ hr}$):** Mid-day rotation across structural GEX walls.
  - **$375\text{ mins}$ (Full Session):** The close. A settlement location, not a pin target.

---

## 3. Mathematical Foundations & Full Functionality

### 3.1 Feature Extraction & Directional Scoring (`target_engine/features.py`)
The engine extracts normalized, standardized signals from market state across 10 quantitative factors:
1. **Spot Drift vs. VWAP ($z_{\text{vwap}}$):** Standardized distance $(S - \text{VWAP}) / \sigma_{\text{vwap}}$.
2. **Futures Basis / Premium ($B_{\text{fut}}$):** Annualized futures spread $(F - S) / S$.
3. **Cumulative Volume Delta Slope ($\text{CVD}_{15\text{m}}$):** 15-minute slope of buyer vs. seller aggression.
4. **Put-Call Ratio Velocity ($\Delta\text{PCR}$):** Rate of change in session volume and OI PCR.
5. **GEX Wall Proximity ($d_{\text{wall}}$):** Relative distance to the nearest Call Wall and Put Wall.
6. **Book balance ($\Gamma_{\text{net}}$):** Call-gamma magnitude minus put-gamma magnitude. Call-heavy and put-heavy are inventory words, not a pin or a squeeze.
7. **Call−put balance ($GF$):** The price where that difference crosses zero. It is not a directional switch.
8. **IV Skew Gradient:** Slope of OTM Put IV vs. OTM Call IV (institutional hedging demand).
9. **Realized vs. Implied Volatility Spread ($\text{IV} - \text{RV}$):** Volatility risk premium indicator.
10. **Pre-Market Gap Impulse:** Initial gap magnitude relative to the 20-day Average True Range (ATR).

### 3.2 Scenario Probability Engine (`target_engine/scenarios.py`)
The directional features are combined through calibrated weights to compute logit scores for three mutually exclusive, collectively exhaustive scenarios:
- **Bull:** upside path. The call-gamma strike is a concentration on that path, not a ceiling the path must stop at.
- **Base (Range):** a range inside the book. Live wall-proximity features are forced to zero, so a nearby gamma strike is not a pin boost.
- **Bear:** downside path. The put-gamma strike is a concentration on that path, not a floor.

The raw scores are transformed into valid probabilities using a Dirichlet-calibrated Softmax function:
$$P(s) = \frac{e^{\lambda z_s}}{\sum_{j \in \{\text{bull}, \text{base}, \text{bear}\}} e^{\lambda z_j}}, \quad \sum P(s) = 1.0$$

#### Conviction Scalar ($C$) & Edge Classification:
$$\text{Conviction } C = 1.0 - 3.0 \times \min(P_{\text{bull}}, P_{\text{base}}, P_{\text{bear}})$$
- **`CONFIRMED EDGE` (Green 🟢):** A directional scenario dominates ($\max(P) \ge 0.45$ with $P_{\text{base}} < 0.45$).
- **`NO EDGE — RANGE DOMINANT` (Amber 🟡):** The Base scenario exceeds $0.45$ or conviction is low ($C < 0.25$). Traders are alerted to avoid naked directional buying.

---

### 3.3 Underlying Price Target Generation (`target_engine/levels.py`)
For any chosen horizon $h$ (in minutes), the engine computes the **1-SD Horizon Expected Move ($\sigma_h$)**:
$$\sigma_h = \max\left( F \cdot \sigma_{\text{atm}} \cdot \sqrt{\frac{h}{375 \times 252}}, \; 15.0 \right)$$

Targets and stops are established via structural anchoring:
- **Bull Target:** $\min\left( \text{Call Wall}, \; F + 0.6 \cdot \sigma_h \right)$
- **Bull Stop (Invalidation):** $\max\left( \text{Put Wall}, \; F - 0.4 \cdot \sigma_h, \; \text{VWAP} - 0.3 \cdot \sigma_h \right)$
- **Bear Target:** $\max\left( \text{Put Wall}, \; F - 0.6 \cdot \sigma_h \right)$
- **Bear Stop (Invalidation):** $\min\left( \text{Call Wall}, \; F + 0.4 \cdot \sigma_h, \; \text{VWAP} + 0.3 \cdot \sigma_h \right)$
- **Base / Range Target:** Structural Pin level (Max Pain or Midpoint of Call/Put Walls).

#### Analytical Barrier Touch Probability (Reflection Principle):
Using Brownian motion diffusion with reflection boundaries, the probability of touching barrier $d = |F_{\text{target}} - F|$ before horizon $h$ is:
$$P(\text{touch}) = 2 \cdot \left[ 1 - \Phi\left( \frac{d - \mu_h}{\sigma_h} \right) \right]$$
clipped safely within $[0.01, 0.99]$.

#### Terminal Close Beyond Probability:
The probability that price finishes beyond the target at horizon expiration:
$$P(\text{close beyond}) = 1 - \Phi\left( \frac{d - \mu_h}{\sigma_h} \right)$$

---

### 3.4 Term Structure & IV Forecasting (`target_engine/iv_forecast.py`)
Options pricing requires anticipating implied volatility shifts over the horizon. The engine models:
- **Sticky-Strike vs. Sticky-Delta Regimes:** Adjusts baseline IV as price traverses the smile.
- **Scenario Volatility Shocks:**
  - *Bull Scenario:* Volatility compression ($\Delta\sigma \approx -0.5\text{ to } -1.5\text{ pts}$) due to institutional call writing and index grind.
  - *Bear Scenario:* Volatility expansion ($\Delta\sigma \approx +1.0\text{ to } +3.5\text{ pts}$) driven by protective put buying and panic demand.
  - *Base Scenario:* Mean-reverting compression toward session ATM mean.

---

### 3.5 Black-76 Analytical Forward Pricing Engine (`target_engine/pricer.py`)
Because Indian index options (NIFTY, BANKNIFTY) trade on forward/futures pricing with cash settlement, the engine uses the **Black-76 model**:

$$V_{\text{CE}} = e^{-rT} \left[ F \Phi(d_1) - K \Phi(d_2) \right]$$
$$V_{\text{PE}} = e^{-rT} \left[ K \Phi(-d_2) - F \Phi(-d_1) \right]$$

where:
$$d_1 = \frac{\ln(F/K) + \frac{1}{2}\sigma^2 T}{\sigma \sqrt{T}}, \quad d_2 = d_1 - \sigma \sqrt{T}$$
- $F$: Underlying futures/forward price.
- $K$: Strike price.
- $T$: Annualized time to expiry ($T = \text{DTE} / 365.0$).
- $r$: Continuously compounded risk-free rate (calibrated at $6.5\% = 0.065$).
- $\sigma$: Implied volatility.

#### Full Analytical Greeks:
- **Delta ($\Delta$):** $\Delta_{\text{CE}} = e^{-rT}\Phi(d_1), \quad \Delta_{\text{PE}} = -e^{-rT}\Phi(-d_1)$
- **Gamma ($\Gamma$):** $\Gamma = \frac{e^{-rT}\phi(d_1)}{F \sigma \sqrt{T}}$
- **Vega ($\mathcal{V}$):** $\mathcal{V} = \frac{F e^{-rT} \phi(d_1) \sqrt{T}}{100}$ (per $1\%$ IV change)
- **Theta ($\Theta$):** Calendar day theta decay:
  $$\Theta_{\text{CE}} = \frac{1}{365} \left[ -\frac{F e^{-rT} \phi(d_1) \sigma}{2\sqrt{T}} + r F e^{-rT} \Phi(d_1) - r K e^{-rT} \Phi(d_2) \right]$$
- **Higher-Order Sensitivities:** Vanna ($\partial\Delta / \partial\sigma$) and Volga ($\partial\mathcal{V} / \partial\sigma$).

#### High-Precision IV Inversion Solver:
To extract IV from market quotes, the engine applies:
1. **Corrado-Miller / Brenner-Subrahmanyam closed-form approximation** as initial guess:
   $$\sigma_0 \approx \sqrt{\frac{2\pi}{T}} \cdot \frac{V_{\text{mkt}}}{e^{-rT} F}$$
2. **Newton-Raphson iteration** using analytical Vega derivatives with quadratic convergence.
3. **Bisection fallback** if Newton-Raphson exceeds boundaries or encounters near-zero vega.

---

### 3.6 First- and Second-Order Greek Attribution
To ensure complete transparency, every repriced option PnL is decomposed into its fundamental driving forces:
$$\Delta P_{\text{modeled}} = \underbrace{\Delta \cdot \Delta F}_{\text{Delta PnL}} + \underbrace{\frac{1}{2}\Gamma (\Delta F)^2}_{\text{Gamma PnL}} + \underbrace{\mathcal{V} \cdot \Delta\sigma_{\text{pts}}}_{\text{Vega PnL}} + \underbrace{\Theta \cdot \Delta t_{\text{days}}}_{\text{Theta Decay}} + \underbrace{\epsilon}_{\text{Residual}}$$

The **Residual ($\epsilon$)** captures higher-order cross-effects (e.g. Vanna, Volga) and confirms the mathematical integrity of the Taylor expansion.

---

### 3.7 Candidate Selection & Ranking (`target_engine/selector.py`)
Rather than recommending every option strike, the engine filters the entire chain and ranks candidate structures by **Probability-Weighted Net Expected Value ($\mathbb{E}[V]$)**:

$$\mathbb{E}[V] = P(\text{bull}) \cdot \text{PnL}_{\text{bull}} + P(\text{base}) \cdot \text{PnL}_{\text{base}} + P(\text{bear}) \cdot \text{PnL}_{\text{bear}}$$

#### Selection Rules & Filters:
- **Liquidity & Spread Gating:** Excludes illiquid strikes with wide bid-ask spreads or low volume.
- **Moneyness Bounds:** Constrains candidates to ATM and near-OTM strikes ($\Delta \in [0.20, 0.65]$).
- **Asymmetric Risk-to-Reward:** Rejects structures where downside risk exceeds realistic upside potential.

---

## 4. Comprehensive Walkthrough: The Target Engine Tab

The **🎯 Target Engine** tab in the Cockpit interface is organized into five distinct, intuitive sections:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 🎯 Target Engine — Scenario & Repricing Suite                                   📖 Target Engine Guide ↗│
│ Decision-support quant engine: Probability-weighted scenarios, underlying price targets & Black-76 repricing│
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [Target Horizon: 120 mins ▼]     [Scoring Mode: Heuristic Baseline ▼]    Auto-refreshes with live chain│
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ DOMINANT SCENARIO: BASE (45.1%)                                            [NO EDGE — RANGE DOMINANT]  │
│ Conviction: 50% | Spot: 22,363.3                                                                       │
│ [████████████████████ Bull: 25.9% ████████████████ Base: 45.1% ██████████████████ Bear: 28.9%]        │
├───────────────────────────────────┬───────────────────────────────────┬────────────────────────────────┤
│ 🚀 Bull Target (120m)             │ ⚖️ Base / Range Target (120m)     │ 🔻 Bear Target (120m)          │
│ 22,393.8                          │ 22,300.0                          │ 22,332.9                       │
│ Stop: 22,287.3                    │ Band Stop: 22,615.2               │ Stop: 22,439.4                 │
│ • P(Touch Target): 76.4%          │ • P(Touch Pin): 21.1%             │ • P(Touch Target): 76.4%       │
│ • P(Close Beyond): 38.2%          │ • P(Stay in Band): 45.0%          │ • P(Close Beyond): 38.2%       │
│ • Forecast ATM IV: 14.98%         │ • Forecast ATM IV: 15.10%         │ • Forecast ATM IV: 15.05%      │
│ • Anchor: 0.6-Sigma Move (22394)  │ • Anchor: Midpoint of Walls       │ • Anchor: 0.6-Sigma Move       │
├───────────────────────────────────┴───────────────────────────────────┴────────────────────────────────┤
│ ⚡ Live Target / SL Strike Ladder (ATM ± 5 Strikes) — Dual-Wing Matrix [● 11/11 Live WS Ticks]         │
│ 🟢 CALLS (CE) WING                   │            STRIKE            │ 🔴 PUTS (PE) WING                │
│ T1 PnL | R:R | T2 | T1 | SL | LiveLTP│ 25000 ★ ATM                  │ LiveLTP | SL | T1 | T2 | R:R | PnL│
├──────────────────────────────────────┴──────────────────────────────┴──────────────────────────────────┤
│ 📋 Ranked Trade Candidates (Scenario-Repriced)                                                         │
│ ▶ Rank #1 — LONG CALL (22350 CE) | Net EV: +₹4,850 | R:R: 2.85:1                                       │
│   Scenario Payoff Matrix: Target Spot | Entry Prem | Target Prem | PnL % | Delta PnL | Vega | Theta     │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 🎛️ Interactive 'What-If' Option Repricer & Target/SL Calculator                                       │
│ [Strike: 22350 ▼]  (o) CE  ( ) PE  [Spot Shift: +50 pts ────]  [IV Shift: 0.0 pts ────] [Time: 60m ────]│
│ Current: ₹143.8  | Repriced: ₹172.5 (+28.7 pts) | PnL/Lot: +₹1,865 | Residual: -0.15 pts               │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 🔍 Diagnostics, Feature Contributions & Track Record (Expandable Drawer)                               │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Section 1: Header & Control Bar
- **Header Banner:** Displays the suite title, explanatory subtitle, and a direct link to this guide (`📖 Target Engine Guide ↗`).
- **Target Horizon Dropdown:**
  - Select between `30 mins`, `60 mins`, `120 mins`, `240 mins`, and `375 mins`.
  - Switching the horizon instantly recalibrates $\sigma_h$, underlying target distances, touch probabilities, and Black-76 time decay $T$.
- **Scoring Mode Dropdown:**
  - `Heuristic Baseline`: Uses expert quant rules and structural GEX weights.
  - `Calibrated (Stage B)`: Incorporates historical backtest Brier-score calibration coefficients.

### 4.2 Section 2: Dominant Scenario Banner & Segmented Probability Bar
- **Dominant Scenario Title:** Displays the highest-probability scenario (e.g. `BULL (62.4%)` in green, `BEAR` in red, or `BASE` in blue).
- **Edge Status Badge:**
  - `CONFIRMED EDGE` (Dark Green): Actionable directional bias detected.
  - `NO EDGE — RANGE DOMINANT` (Dark Amber): Market is range-bound; long premium buying should be avoided.
- **Conviction & Spot Metric:** Shows the scalar conviction ($0\%$ to $100\%$) and live underlying spot reference.
- **Segmented Visual Progress Bar:** Clean three-color horizontal breakdown representing $P(\text{Bull})$, $P(\text{Base})$, and $P(\text{Bear})$ at a glance.

### 4.3 Section 3: Tri-Scenario Price Ladder Cards
Three distinct side-by-side cards display the underlying targets for each scenario:
1. **Bull Target Card (Green Accent):**
   - **Target Spot:** Numerical index level projected for the horizon.
   - **Stop / Invalidation:** Explicit level where the bullish hypothesis is void.
   - **$P(\text{Touch Target})$:** Probability of price touching the target within horizon.
   - **$P(\text{Close Beyond})$:** Probability of price closing beyond the target at horizon end.
   - **Forecast ATM IV:** Projected implied volatility under the bullish regime.
   - **Structural Anchor Reason:** Explains why this target was selected (e.g., Call Wall, $0.6\sigma_h$ move).
2. **Base / Range Target Card (Blue Accent):**
   - **Range centre:** the base anchor. Max pain or the mid of the gamma strikes is a location, not an expected pin.
   - **Band Stop:** Outer boundary of the expected range.
   - **$P(\text{Touch Pin})$ & $P(\text{Stay in Band})$:** Reversion and containment metrics.
   - **Forecast ATM IV:** Projected volatility under sideways consolidation.
3. **Bear Target Card (Red Accent):**
   - **Target Spot:** Lower target level (Put Wall or negative $0.6\sigma_h$).
   - **Stop / Invalidation:** Upper stop cutoff.
   - **$P(\text{Touch})$ & $P(\text{Close})$:** Downside barrier probabilities.
   - **Forecast ATM IV:** Projected volatility under market selloff (including skew bump).

### 4.4 Section 4: Live WebSocket Dynamic Target / SL Strike Ladder (ATM ± 5 Strikes)
An institutional-grade, real-time streaming options execution matrix placed directly beneath the price targets:
- **ATM ± 5 Strikes Band:** Automatically identifies the current active ATM strike and computes repricing for the 11-strike band ($K_{\text{atm}-5}$ down to $K_{\text{atm}+5}$) using official exchange strike steps (50 pts for NIFTY, 100 pts for BANKNIFTY/SENSEX).
- **Dual-Wing Matrix (Option A):**
  - **Left Wing (Calls / CE):** Live LTP, Invalidation Stop-Loss ($V_{\text{sl}}$), Primary Target ($V_{\text{tgt1}}$), Secondary Extension ($V_{\text{tgt2}}$), Risk-Reward ratio ($R:R$), and ₹ PnL per lot.
  - **Center Pillar (Strike):** Prominently styled strike price with glowing `★ ATM` badge on the active ATM strike and point offset indicators.
  - **Right Wing (Puts / PE):** Symmetrical mirror layout displaying Live LTP, Invalidation Stop-Loss ($V_{\text{sl}}$), Primary Target ($V_{\text{tgt1}}$), Secondary Extension ($V_{\text{tgt2}}$), Risk-Reward ratio ($R:R$), and ₹ PnL per lot.
- **WebSocket Streaming & Isolation:** Powered by `@st.fragment(run_every=2)`, updating every 2 seconds via in-memory Dhan v2 WebSocket quotes with zero full-page reload and pulsing live indicators (`● live`).
- **Flexible Matrix Views:** Fast toggle between **Dual-Wing Matrix**, **Calls (CE) Only**, and **Puts (PE) Only** (featuring expanded Greeks and volatility skew metrics).

### 4.5 Section 5: Ranked Candidate Strategy Cards
Displays the top scenario-repriced trade structures ranked by Net Expected Value ($\mathbb{E}[V]$):
- **Card Header:** Structure name (e.g. `LONG CALL`, `BULL SPREAD`, `LONG PUT`), Net EV in INR, and Risk-Reward ratio.
- **Summary Metrics Strip:** Net EV, R:R ratio, Max Loss (INR and pts), and Breakeven price levels.
- **Per-Scenario Repriced Payoff Table:**
  - Shows how the option contract behaves across all 3 scenarios:
    - Target Spot Level.
    - Entry Premium vs. Repriced Target Premium.
    - PnL in points and percentage return.
    - **Greek Attribution Decomposition:** Delta PnL, Vega PnL, and Theta bleed explicitly segregated.
- **Rationale Bullet Points:** Microstructure reasons why this contract was selected.

### 4.6 Section 6: Interactive "What-If" Option Repricer & Target/SL Calculator
A real-time quantitative sandbox allowing traders to calculate exact target and stop-loss option premiums, PnL per lot, and risk-reward across any contract and expiry:
- **Contract & Expiry Selector:**
  - Expiry selector (`📅 Expiry`) supporting weekly, monthly, and far-month options.
  - Strike selector automatically populated from the selected expiry chain.
  - Option Type toggle (`CE` or `PE`).
  - Real-Time Market Reality strip: Displays Current Market Premium (Live LTP), Theoretical Black-76 price, Strike Implied Volatility (skew-adjusted), and live Greeks (Delta, Gamma, Vega, Theta).
- **Mode 1: Target & Stop-Loss Calculator:**
  - Target Spot level input (with quick presets for Bull Target, Bear Target, or Base/Pin level).
  - Stop Loss / Invalidation Spot level input.
  - Holding Horizon slider ($5$ to $375$ mins).
  - Regime Volatility Shock (Auto regime-conditioned, vol crush, or vol expansion).
  - **Outputs:**
    - Repriced Target Option Premium ($V_{\text{target}}$) with point gain, % gain, and INR PnL per lot.
    - Repriced Stop-Loss Option Premium ($V_{\text{sl}}$) with point risk, % loss, and INR risk per lot.
    - Risk : Reward ratio ($R:R$).
    - Reflection-principle touch probabilities: $P(\text{Target Touch})$ and $P(\text{SL Touch})$.
    - Touch-weighted Net EV per lot.
    - Greek attribution breakdown table at Target and Stop Loss (Delta, Gamma, Vega, Theta bleed, and Residual).
- **Mode 2: Custom 'What-If' Parameter Sliders:**
  - Spot Shift slider ($-400$ to $+400$ index points).
  - IV Shift slider ($-8.0$ to $+8.0$ volatility percentage points).
  - Time Elapsed slider ($0$ to $375$ minutes).
  - Live repriced theoretical premium, PnL per lot, and full Greek attribution decomposition.

### 4.6 Section 6: Diagnostics & Explain Drawer
Collapsible drawer providing 100% mathematical auditability:
- **Directional Feature Contributions Table:** Displays every input feature's raw value and its exact weighted contribution to the scenario logit.
- **Model & Quality Metadata:** Model version, conviction scalar $C$, active data quality flags, and detected GEX Call/Put walls.

---

## 5. Cross-Platform Integrations Across the Cockpit

The Target Engine serves as the core quantitative pricing nucleus across four other modules in the Cockpit:

### 5.1 Trade Recommendations Engine (`analytics/trade_ideas.py`)
- Replaces static heuristic targets (`* 1.35`, `* 0.72`) with Black-76 forward repricing.
- Embeds analytical reflection touch probabilities `P(Touch)` and `P(SL)` into every trade card.
- Computes `Net EV` for every recommendation card and confluence matrix row.

### 5.2 Pre-Market 7-Minute Alpha Window (`data/pre_post_market.py`)
- Executes at 09:08:00 IST immediately upon discovery of the cash opening equilibrium auction price.
- Reprices ATM $\pm 3$ strikes using Black-76 before the F&O market opens at 09:15:00 IST.
- Calculates ATM Straddle Shock % (expansion vs. crush) to prepare traders for opening volatility.

### 5.3 OrderFlow Sentinel Tape (`sentinel/engine/outcomes.py`)
- Replaces arbitrary 25 pt risk/target assumptions with instrument-specific risk scaling (`SENSEX`: 75, `BANKNIFTY`: 50, `NIFTY`: 25).
- Anchors outcome targets to structural Call/Put walls or $0.6\sigma_h$ horizon moves.
- Adds actionable `🎯 Option Play: {strike} {CE/PE}` chips with stop levels to every live microstructure stream card.

### 5.4 Signal Efficiency Analytics (`sentinel/engine/efficiency.py`)
- Introduces the 4th tab **"🎯 Model Calibration & Options Expectancy"**.
- Computes **Calibration Alpha** = $Empirical\\,Hit\\,Rate - Theoretical\\,P(touch)$.
- Generates the **Black-76 Repriced ATM Option Expectancy Curve** across $+1\text{m}, +3\text{m}, +5\text{m}, +15\text{m},$ and $+30\text{m}$ horizons with Delta and Theta breakdown.

---

## 6. Operational Playbook & Best Practices

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 TARGET ENGINE OPERATIONAL PLAYBOOK                                     │
├─────────────────────────┬────────────────────────────┬─────────────────────────────────────────────────┤
│ Market Phase / Regime   │ Target Engine State        │ Recommended Action                              │
├─────────────────────────┼────────────────────────────┼─────────────────────────────────────────────────┤
│ 09:08 – 09:15 Pre-Bell  │ Pre-Market Option Shock    │ Inspect projected gap matrix; identify OTM      │
│                         │ Matrix active              │ contracts underpriced by yesterday's close.     │
├─────────────────────────┼────────────────────────────┼─────────────────────────────────────────────────┤
│ Morning Trend (09:45)   │ CONFIRMED EDGE             │ Align with Dominant Scenario; select Rank #1    │
│                         │ Bull or Bear > 50%         │ candidate; set stop at underlying Invalidation. │
├─────────────────────────┼────────────────────────────┼─────────────────────────────────────────────────┤
│ Mid-Day Consolidation   │ NO EDGE — RANGE DOMINANT   │ Stand aside on directional buying; favor credit │
│ (11:30 – 13:30)         │ Base Scenario > 45%        │ spreads or mean-reversion toward Pin Level.     │
├─────────────────────────┼────────────────────────────┼─────────────────────────────────────────────────┤
│ Afternoon Expiry Squeeze│ GEX Wall Breach +          │ Check "What-If" Repricer for Gamma acceleration │
│ (14:00 – 15:15)         │ Expanding CVD Delta        │ and rapid Delta migration toward 1.00.          │
└─────────────────────────┴────────────────────────────┴─────────────────────────────────────────────────┘
```

### Risk Rules to Remember:
1. **Never Trade Negative EV:** If a setup exhibits negative Net EV, statistical expectancy is negative regardless of win rate.
2. **Respect the Range Dominant Warning:** When the engine flags `NO EDGE`, choppy whipsaws will erode option premiums through theta bleed.
3. **Always Anchor Stops to the Underlying Invalidation:** Option price stops are subject to market maker bid-ask spread spikes; the true invalidation is always the underlying index level.

---

## 🔗 Related Technical Documentation
- [🎯 Options Recommendation Strategies & Alpha Playbook](/?view=options_strategies_guide)
- [🔬 Options Analytics & Microstructure Intelligence Guide (IOAS v3.0)](/?view=options_analytics_guide)
- [⚡ OrderFlow Sentinel Stream Signals Field Guide](/?view=orderflow_guide)
- [🧭 Market Intelligence: Sector Breadth & News Sentiment Guide](/?view=market_intelligence_guide)
- [🎙️ Session Commentary & Suggestion Bites Guide](/?view=session_commentary_guide)
- [🌅 Pre/Post-Market Microstructure & Recommendations Lifecycle Guide](/?view=pre_post_market_guide)
- [📈 Signal Efficiency & MFE/MAE Performance Guide](/?view=signal_efficiency_guide)
- [📊 Quantitative Calculations & Formulas Reference](/?view=data_dictionary)
- [🧭 Participant Open Interest Guide](/?view=participant_guide)
- [🗺️ Indian GEX Playbook & Research Manual](/?view=gex_playbook)
- [📘 Master Cockpit User Guide](/?view=user_guide)
