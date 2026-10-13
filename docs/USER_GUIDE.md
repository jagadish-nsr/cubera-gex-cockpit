# 📖 Indian-GEX Strategic Cockpit — Master User Guide

Welcome to the **Indian-GEX Cockpit**, an institutional-grade derivatives analytics and microstructure execution terminal for NSE and BSE indices (NIFTY, BANKNIFTY, SENSEX).

---

## 🧭 Navigation & Help Documentation Library

Access any of the specialized technical guides directly in your browser or click the help buttons in the Cockpit header:

1. **[🎯 Target Engine — Scenario & Repricing Suite Guide](/?view=target_engine_guide)**
   - Authoritative technical and operational guide for the **Target Engine (Black-76 & Reflection Principle Suite)**.
   - Scenario-conditioned joint probability modeling (Bull, Base, Bear), 1-SD horizon expected moves ($\sigma_h$), reflection barrier touch probabilities $P(\text{touch})$, and Black-76 analytical forward option repricing.
   - Walkthrough of the dedicated **Target Engine Tab**, Tri-Scenario price ladder cards, ranked candidate payoffs, Greek attribution (Delta, Gamma, Vega, Theta), and the interactive "What-If" option repricer.

2. **[📊 Data Dictionary & Calculations Reference](/?view=data_dictionary)**
   - Comprehensive inventory of all raw data points fetched from **Dhan WebSocket** (`wss://api-feed.dhan.co`) and **Dhan REST APIs**.
   - Mathematical formulas for Gamma Exposure (GEX), Call/Put Walls, Gamma Flip, Max Pain, CVD, VWAP bands, Delta MTM option pricing, and dynamic position sizing.

3. **[🎯 Options Recommendation Strategies Guide](/?view=options_strategies_guide)**
   - Operational field guide for the **High-Conviction Options Recommendations Engine (Score ≥ 75)**.
   - Deep dive on all 7 institutional strategies: **MOMENTUM**, **STRUCTURE**, **GAMMA SQUEEZE**, **ICEBERG FADE**, **VANNA-CHARM**, **VOL DISPERSION**, and **RATIO REVERSAL**.
   - Target Engine Black-76 forward target/stop pricing, touch probabilities $P(\text{touch})$, and Net Expected Value (Net EV).

4. **[🌅 Pre/Post-Market & Recommendations Lifecycle Guide](/?view=pre_post_market_guide)**
   - Microstructure guide covering the **Pre-Open Auction (09:00–09:15)**, 09:07:30 adaptive equilibrium discovery, and the 7-minute Pre-Bell Alpha Window.
   - Target Engine Black-76 pre-bell option shock modeling (ATM $\pm 3$ strikes) and ATM Straddle Shock % (expansion vs. crush).
   - Cash Closing Auction Session (CAS) 30-min VWAP settlement and F&O extended close (15:40 IST).
   - Persistent bookmarked card lifecycle (active until user unpins or contract crosses expiry date).

5. **[⚡ OrderFlow Sentinel Stream Signals Guide](/?view=orderflow_guide)**
   - Microstructure indicators: CVD, OrderFlow Delta, absorption, trapped buyers/sellers, and momentum divergence.
   - Dynamic volatility targets, structural wall anchors, and live `🎯 Option Play` contract chips.

6. **[🗺️ Indian GEX Playbook & Research Manual](/?view=gex_playbook)**
   - Theoretical mechanics of dealer positioning, gamma hedging, and Gamma Blast squeeze regimes.

7. **[🧭 Participant Open Interest Guide](/?view=participant_guide)**
   - Daily institutional positioning tracking FII, Proprietary Desk, and Client positioning drift.

8. **[📈 Signal Efficiency & Edge Analytics](/?view=signal_efficiency_guide)**
   - Statistical performance verification, Maximum Favorable Excursion (MFE), and Maximum Adverse Excursion (MAE).
   - Sub-Tab 4: Target Engine Model Calibration Alpha ($Empirical - Theoretical$) and Black-76 Repriced ATM Option Expectancy Curve (+1m to +30m).

---

## 🚀 Core Trading Philosophy

- **Calm Regime (Spot > Gamma Flip & inside walls)**: *Be the Casino* — option writers dominate, volatility is suppressed, and mean-reversion toward equilibrium (VWAP) is favored.
- **Wild Regime (Spot < Gamma Flip or breaching walls)**: *Be the Careful Trend Trader* — dealer gamma is negative, market makers hedge aggressively in the direction of the move, and explosive convex breakouts (Gamma Squeezes) occur.

*Note: All recommendations, levels, and order simulations are for decision-support and paper trading only.*
