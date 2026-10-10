# 📖 Indian-GEX Strategic Cockpit — Master User Guide

Welcome to the **Indian-GEX Cockpit**, an institutional-grade derivatives analytics and microstructure execution terminal for NSE and BSE indices (NIFTY, BANKNIFTY, SENSEX).

---

## 🧭 Navigation & Help Documentation Library

Access any of the specialized technical guides directly in your browser or click the help buttons in the Cockpit header:

1. **[📊 Data Dictionary & Calculations Reference](/?view=data_dictionary)**
   - Comprehensive inventory of all raw data points fetched from **Dhan WebSocket** (`wss://api-feed.dhan.co`) and **Dhan REST APIs**.
   - Mathematical formulas for Gamma Exposure (GEX), Call/Put Walls, Gamma Flip, Max Pain, CVD, VWAP bands, Delta MTM option pricing, and dynamic position sizing.

2. **[🎯 Options Recommendation Strategies Guide](/?view=options_strategies_guide)**
   - Operational field guide for the **Top 3 CE & Top 3 PE Recommendation Engine**.
   - Deep dive on **MOMENTUM**, **STRUCTURE**, and **GAMMA SQUEEZE** plays: data points used, inference logic, entry triggers, spot invalidation levels, and target/stop pricing.
   - Roadmap for next-generation alpha strategies (0DTE Vanna-Charm compression, OrderFlow Iceberg absorption, Volatility Dispersion).

3. **[⚡ OrderFlow Sentinel Stream Signals Guide](/?view=orderflow_guide)**
   - Microstructure indicators: CVD, OrderFlow Delta, absorption, trapped buyers/sellers, and momentum divergence.

4. **[🗺️ Indian GEX Playbook & Research Manual](/?view=gex_playbook)**
   - Theoretical mechanics of dealer positioning, gamma hedging, and Gamma Blast squeeze regimes.

5. **[🧭 Participant Open Interest Guide](/?view=participant_guide)**
   - Daily institutional positioning tracking FII, Proprietary Desk, and Client positioning drift.

6. **[📈 Signal Efficiency & Edge Analytics](/?view=signal_efficiency_guide)**
   - Statistical performance verification, Maximum Favorable Excursion (MFE), and Maximum Adverse Excursion (MAE).

---

## 🚀 Core Trading Philosophy

- **Calm Regime (Spot > Gamma Flip & inside walls)**: *Be the Casino* — option writers dominate, volatility is suppressed, and mean-reversion toward equilibrium (VWAP) is favored.
- **Wild Regime (Spot < Gamma Flip or breaching walls)**: *Be the Careful Trend Trader* — dealer gamma is negative, market makers hedge aggressively in the direction of the move, and explosive convex breakouts (Gamma Squeezes) occur.

*Note: All recommendations, levels, and order simulations are for decision-support and paper trading only.*
