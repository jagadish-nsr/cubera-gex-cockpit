# 🧭 Market Intelligence: Sectorial Performance & News Sentiment Guide

> **Module:** Market Intelligence Engine (Tab 5 · `analytics/market_intelligence.py`, `data/sectors.py`, `data/news.py`)  
> **Market Microstructure:** Institutional Breadth Integration & Exogenous Catalyst Control (Phase B)  
> **Applicable Indices:** NIFTY 50, BANK NIFTY, SENSEX  
> **Terminal View:** Served directly inside Tab 5 or standalone at `/?view=market_intelligence_guide`  

---

## 1. Overview & Trading Philosophy

Gamma Exposure (GEX) is a **probability map of dealer hedging reflexivity, not a directional crystal ball**. In Indian markets:
1. **Sector Breadth Sets the Ground Truth**: When Nifty moves, is the rally broad-based or an isolated drag/lift by a single heavyweight like Reliance or HDFC Bank?
2. **Exogenous Catalysts Override Dealer Flow**: Breaking geopolitical or macro headlines (e.g. crude shocks, RBI/Fed surprises, conflict) can breach dealer walls and render pure option sign tests misleading (Phase B Confound Control).

The **Market Intelligence Engine** integrates:
- **Exchange Sector Breadth** (NSE `/api/allIndices` & Dhan `/marketfeed/quote`) across all 11 constituent sectors with realistic index weights.
- **Real-Time Financial News Aggregation** (Economic Times & Times of India) with keyword-weighted impact scoring and automatic thematic categorization.
- **Executive Synthesis & Alignment**: Real-time cross-validation between index spot price action and underlying sector breadth.

---

## 2. Sector Performance & Rotation Regimes

### Key Sector Weightings (~Nifty 50)
| Sector | ~Nifty Weight | Role / Classification |
|---|---|---|
| **Bank / Pvt Bank** | ~30% / 27% | Core Cyclical & Liquidity Engine |
| **Energy / Oil & Gas** | ~12% / 10% | High-Beta Macro (Reliance-heavy) |
| **IT** | ~12% | Export / Currency / Global Growth |
| **FMCG** | ~8% | Classic Safe-Haven Defensive |
| **Auto** | ~7% | Domestic Cyclical Consumption |
| **Healthcare / Pharma** | ~4.5% / 4% | Defensive / Low Beta |
| **Metal** | ~4% | Global Commodity & China Beta |
| **Consumer Durables** | ~2% | Discretionary Retail Beta |

### Sector Rotation Regimes
The analytical engine categorizes intraday market structure into 5 distinct regimes:
1. **🚀 CYCLICAL LEADERSHIP**: High-beta BFSI, IT, Energy & Auto advancing strongly while defensives lag. Signals risk-on institutional conviction and high continuation probability.
2. **🛡️ DEFENSIVE ROTATION**: FMCG, Pharma and Healthcare outperforming while Banking and Auto stall or slide. Signals institutional risk-off hedging / smart money seeking safe havens.
3. **🟢 BROAD EXPANSION**: Over 70% of sectors advancing concurrently across cyclicals and defensives. Strong confirmation that index rallies will respect call walls as breakout targets rather than fakeouts.
4. **🔴 BROAD LIQUIDATION**: Over 70% of sectors slumping with negative weighted contribution. High risk of dealer gamma flip breakdown and stop-hunting cascades.
5. **⚖️ BALANCED DISPERSION / CHOP**: Mixed sectors with high dispersion. Favors range-bound mean-reversion tactics (e.g., Short Iron Condors / Strangles).

---

## 3. Financial News Aggregation & Impact Scoring

### RSS News Pipeline
- Sources: Economic Times (Markets & Economy) and Times of India (Top Stories).
- Parsed via pure stdlib XML and cached for 5 minutes (`300s TTL`) to eliminate latency.
- Fail-soft architecture: Network hiccups drop only the failing feed and never stall option chain calculations.

### Headline Classification & Sentiment
- **Categories**:
  - `Geopolitics`: War, missile strikes, sanctions, territorial conflicts.
  - `Energy & Crude`: Brent/WTI crude, OPEC decisions, gas prices.
  - `Macro & Policy`: RBI, Fed/FOMC, repo rates, inflation/CPI, fiscal policy.
  - `Corporate & Earnings`: Quarterly earnings, mergers, acquisitions, IPOs.
  - `Markets & Economy`: Broader market trends, indices, FII/DII institutional flows.
- **Sentiment**: Keyword-scored as `🟢 BULLISH`, `🔴 BEARISH`, or `⚪ NEUTRAL`.
- **Impact Scoring**: Weighted heuristic (3 = high shock, 2 = medium, 1 = normal).

### Phase B Confound Control
Days with an aggregate fresh headline impact score $\ge 6$ or breaking shock catalysts are automatically flagged as **News-Dominated Days**. On these days, dealer delta-hedging can temporarily break down under exogenous order flow pressure.

---

## 4. UI Ergonomics in the Cockpit

### A. Compact Mini-Strip (Tab 0: Cockpit)
Positioned immediately below the Executive Telemetry header for instant glanceability:
- **Breadth Ratio**: E.g., `8/11 Green (73%)` with green/red indicator chips.
- **Weighted Contribution**: E.g., `+0.46%`.
- **Top Sector Leader**: E.g., `🚀 FMCG (+1.80%)`.
- **News Sentiment & Top Headline**: E.g., `📰 BULLISH "Indices snap four-day losing streak as oil prices ease..."`.

### B. Dedicated Workstation (Tab 5: Market Intelligence)
1. **Executive Macro Verdict Banner**: One-line situational verdict + plain-English narrative synthesis + exogenous risk level pill (`🟢 LOW EXOGENOUS RISK`, `⚡ MODERATE CATALYSTS`, `⚠️ EXOGENOUS SHOCK`).
2. **4-Metric Telemetry KPI Cards**:
   - **Sector Breadth Ratio**: Visual ratio with percentage and breadth state.
   - **Net Weighted Contribution**: Percentage contribution with active rotation regime.
   - **News Catalyst Sentiment**: Sentiment label with aggregate impact score and count of fresh stories.
   - **Spot Move Alignment**: Verifies if spot direction is confirmed or desynced from sector breadth.
3. **Actionable Trader Takeaways**: Specific tactical bullet points based on the synthesis.
4. **Sector Performance Leaderboard**: Visual horizontal performance bars for all 11 sectors + benchmark broad indices (Midcap, Smallcap, Nifty 50) with last traded prices, percentage change, and weights.
5. **Interactive Financial News Stream**: Filterable by category pills (`ALL`, `Energy & Crude`, `Macro & Policy`, etc.) with timestamps, age in minutes, sentiment tags, and impact scores.
