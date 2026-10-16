# 🎙️ Session Commentary & Suggestion Bites — Comprehensive Guide & Field Manual

> **Module:** Session Commentary Engine (Tab 10 · `commentary/` & `app/tab_commentary.py`)  
> **Market Microstructure:** Native Indian Market Mode (`short_call_short_put` / Delta-Hedging & Strike Defense)  
> **Applicable Indices:** NIFTY 50, BANK NIFTY, SENSEX  
> **Storage & Engine:** SQLite WAL Mode (`data/commentary.db`) · Deterministic Template Narrator · Fact Verifier  

---

## 1. System Overview & Core Philosophy

The **Session Commentary Engine** narrates the intraday trading session as a continuous, high-signal story rather than an overwhelming firehose of raw ticks. It translates complex derivatives positioning, GEX structural shifts, orderflow dynamics, news shocks, and trader-pinned recommendations into human-readable executive entries.

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    SESSION COMMENTARY PIPELINE                                          │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                         │
│   ┌──────────────────────────┐    ┌──────────────────────────┐    ┌─────────────────────────────────┐   │
│   │   OrderFlow & VWAP Tape  │    │      GEX Option Chain    │    │      Pinned Recommendations     │   │
│   │   CVD, Delta, RVol, Spikes│   │   Walls, Skew, ATM IV    │    │   Target 1, Invalidation Levels │   │
│   └────────────┬─────────────┘    └────────────┬─────────────┘    └────────────────┬────────────────┘   │
│                │                               │                                   │                    │
│                └───────────────────────────────┼───────────────────────────────────┘                    │
│                                                ▼                                                        │
│                                ┌───────────────────────────────┐                                        │
│                                │   Event Detectors (Phases 1-3)│                                        │
│                                │   Walls, Gaps, Shocks, Pinned │                                        │
│                                └───────────────┬───────────────┘                                        │
│                                                ▼                                                        │
│                                ┌───────────────────────────────┐                                        │
│                                │ Scoring & Cooldown Management │                                        │
│                                │ Merge Logic · Thread Resolvers│                                        │
│                                └───────────────┬───────────────┘                                        │
│                                                ▼                                                        │
│                                ┌───────────────────────────────┐                                        │
│                                │ Deterministic Template Engine │                                        │
│                                │ (commentary/narrator/templates)│                                       │
│                                └───────────────┬───────────────┘                                        │
│                                                ▼                                                        │
│                                ┌───────────────────────────────┐                                        │
│                                │ Strict Numerical Fact Verifier│                                        │
│                                │ (Zero Hallucination Guarantee)│                                        │
│                                └───────────────┬───────────────┘                                        │
│                                                ▼                                                        │
│               ┌────────────────────────────────┴───────────────────────────────┐                        │
│               ▼                                                                ▼                        │
│ ┌───────────────────────────┐                                    ┌───────────────────────────┐          │
│ │   Commentary Narrative    │                                    │     Suggestion Bites      │          │
│ │  (6 - 15 Stories / Day)   │                                    │  (ACT-IF · PREPARE · WATCH│          │
│ └─────────────┬─────────────┘                                    └─────────────┬─────────────┘          │
│               │                                                                │                        │
│               └────────────────────────────────┬───────────────────────────────┘                        │
│                                                ▼                                                        │
│                                ┌───────────────────────────────┐                                        │
│                                │   SQLite WAL Storage & UI     │                                        │
│                                │ (commentary.db · Tab 10 UI)   │                                        │
│                                └───────────────────────────────┘                                        │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 🛡️ Five Guiding Tenets:
1. **Story First, Signal Second:** A complete trading session produces roughly **6 to 15 curated entries**, not a noisy ticker tape. Each entry captures a genuine market phase (Opening Drive, Wall Test, Impulsive Expansion, Midday Consolidation, Expiry Pinning, Close Settlement).
2. **Deterministic Facts, Verified Prose:** Events are synthesized from audited mathematical indicators. Text generation uses deterministic template libraries with strict numerical checking—every price, strike, and percentage is cross-verified against the input facts before publication.
3. **Conditional Suggestions, Never Imperatives:** Guidance is delivered as structured **Suggestion Bites** (`ACT-IF`, `PREPARE`, `WATCH`). A bite states *"If Spot holds above 22,650, Bull Call Spread fits, invalid below 22,580"*—never unqualified instructions like *"Buy now"*.
4. **Zero Look-Ahead Enforcement:** Every historical replay and backtested session relies strictly on data with timestamp $t_i \le t$, eliminating future leakage.
5. **Decision-Support Only:** No automated order execution. The trader retains 100% final discretion over capital deployment.

---

## 2. Event Detectors & Microstructure Triggers

The commentary pipeline evaluates market telemetry across six dedicated detector modules:

### 2.1 Opening Auction & Gap Detector (`detectors/opening.py`)
- **Trigger Window:** 09:15:00 to 09:20:00 IST.
- **Microstructure Analysis:**
  - Evaluates opening spot against the previous day's settlement and Value Area (VAH / VAL).
  - Classifies opening type:
    - `GAP_UP_OUTSIDE_RANGE`: Spot opens $> \text{Prior High}$. High probability of opening drive or mean-reverting gap fill.
    - `GAP_DOWN_OUTSIDE_RANGE`: Spot opens $< \text{Prior Low}$. Put writers under immediate delta pressure.
    - `IN_RANGE_OPEN`: Spot opens within prior day's range. Favors structural two-way auction.
  - Computes opening volatility shock against pre-market expectations.

### 2.2 GEX & OI Wall Sentinel (`detectors/walls.py`)
- **Wall Defense / Absorption:** Spot approaches within $0.25 \times \text{step}$ of Call Wall ($CW$) or Put Wall ($PW$) and volume delta stalls or reverses. Emits institutional wall defense alert.
- **Wall Breakout / Squeeze:** Spot closes beyond Call Wall with high positive CVD ($>80\text{th}$ percentile) or below Put Wall with aggressive selling. Emits Gamma Squeeze or Trapdoor alert.
- **Strike Migration:** Open interest shifts from one strike to another (e.g. Call Wall rolling up from 22,600 to 22,700), signaling institutional capitulation or target expansion.
- **Cooldown & Hysteresis:** Wall events enforce a 15-minute cooldown to prevent repetitive logging during prolonged tests.

### 2.3 Exogenous News & Macro Shock Detector (`detectors/news.py`)
- Cross-references real-time RSS feeds (Economic Times, Times of India) with index price velocity.
- Matches headlines within a $[-10\text{m}, +3\text{m}]$ lead/lag window when abnormal price moves occur ($\ge 1.5\sigma$ in 5 minutes).
- Categorizes shocks: `Geopolitics`, `Crude & Energy`, `Macro & Policy (RBI/Fed)`, `Corporate Earnings`.

### 2.4 Pinned Recommendations Sentinel (`detectors/pinned.py`)
Directly bridges trader-bookmarked cards in the **Journal / Recommendations Store** (`data/journal/recommendations_store.py`) with real-time market ticks:
- **`TARGET_APPROACHING`**: Spot moves within $15\%$ of Target 1. Alerts the trader to prepare partial profit-taking or trail stops.
- **`TARGET_HIT`**: Live spot or option LTP touches Target 1. Celebrates the profit milestone and logs the execution victory.
- **`INVALIDATION_WARNING` / `INVALIDATED`**: Spot violates the designated invalidation boundary. Alerts the trader that the setup is invalid.

---

## 3. Actionable Suggestion Bites Taxonomy

Attached to relevant narrative entries, **Suggestion Bites** provide concise, level-anchored execution frames:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                  SUGGESTION BITE TAXONOMY                                        │
├──────────────┬────────────────────────┬──────────────────────────────────┬───────────────────────┤
│ Bite Type    │ Urgency / Posture      │ Typical Scenario                 │ Risk Profile          │
├──────────────┼────────────────────────┼──────────────────────────────────┼───────────────────────┤
│ 1. ACT-IF    │ High (Active Trigger)  │ Wall breakout or confirmed bounce│ Defined Risk / Spread │
│ 2. PREPARE   │ Medium (Anticipatory)  │ Approaching key level or Target 1│ Risk Assessment       │
│ 3. WATCH     │ Low (Observational)    │ Range compression or lunch chop  │ Stand Aside / Monitor │
└──────────────┴────────────────────────┴──────────────────────────────────┴───────────────────────┘
```

### 3.1 Bite Anatomy & Invariants

Every suggestion bite renders with complete quantitative transparency:

```
┌────────────────────────────────────────────────────────────────────────┐
│ ⚡ ACT-IF: Call Wall Breakout Continuation                             │
│ ────────────────────────────────────────────────────────────────────── │
│ Condition : Spot holds above 22,650 for 5-min candle close             │
│ Structure : Bull Call Debit Spread (22650 CE / 22750 CE)               │
│ Target 1  : 22,720 (+70 pts)   |   Target 2 : 22,780 (+130 pts)        │
│ Inval/Stop: 22,615 (Spot invalidation below breakout base)             │
│ Max Risk  : ₹32.0 (Net Debit Outlay) · Calibration Grade: 🟢 A+        │
└────────────────────────────────────────────────────────────────────────┘
```

1. **Explicit Invalidation:** No bite is ever emitted without a hard price stop.
2. **Defined-Risk Structures Only:** Naked option writing is strictly forbidden; all suggested option plays default to debit vertical spreads or disciplined single options with capped loss.
3. **Shadow-Mode Track Record & Auto-Demotion:**
   Bites are tracked forward in the SQLite registry. If a specific bite category experiences win rates $<45\%$ or negative expectancy after simulated costs ($0.08R$), the **Auto-Demotion Engine** (`commentary/qa/demotion.py`) automatically downgrades it (`ACT-IF` $\to$ `PREPARE` $\to$ `WATCH`).

---

## 4. Deterministic Narrator & Strict Fact Verifier

To eliminate the hallucination risks inherent in raw LLM outputs, the commentary pipeline enforces a dual-stage generation architecture:

```
   ┌──────────────────────┐
   │  Audited Event Facts │ (JSON: spot=22640, cw=22700, cvd=+14200)
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │ Deterministic Engine │ (commentary/narrator/templates.py)
   │  & Template Library  │ Emits structured, contextual prose
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │  Strict Fact Verifier│ (commentary/narrator/verifier.py)
   │  - Numeric Regex     │ Checks every number in text against facts JSON
   │  - Direction Check   │ Verifies text sentiment aligns with price delta
   │  - Forbidden Phrasing│ Rejects financial advice ("guaranteed", "buy now")
   └──────────┬───────────┘
              │
       Pass   │   Fail
      ┌───────┴───────┐
      ▼               ▼
 ┌─────────┐   ┌─────────────┐
 │ PUBLISH │   │ REJECT /    │
 │ TO FEED │   │ LOG SUPPRESS│
 └─────────┘   └─────────────┘
```

### Fact Verifier Invariants:
- **Numerical Fidelity:** Every digit sequence representing a price, percentage, or volume in the narrative must have an exact or rounded equivalent in the event facts dictionary.
- **Directional Concordance:** A narrative describing a "bullish thrust" when spot delta is negative is rejected immediately.
- **Compliance Shield:** Phrases including *"guaranteed profits"*, *"sure shot"*, *"all-in"*, or *"buy now"* trigger immediate suppression into `commentary_suppressed`.

---

## 5. Persistence & SQLite WAL Architecture

Commentary data is stored in [`data/commentary.db`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/commentary.db) using SQLite with **Write-Ahead Logging (WAL)**:

### Table Schemas:
1. **`commentary_entries`**:
   - `entry_id` (TEXT, PK): Unique UUID for the narrative story.
   - `ts` (REAL): Unix epoch timestamp.
   - `symbol` (TEXT): `NIFTY`, `BANKNIFTY`, or `SENSEX`.
   - `phase` (TEXT): Market phase (`OPENING`, `MORNING_DRIVE`, `MIDDAY`, `EXPIRY_WINDOW`, `CLOSE`).
   - `headline` (TEXT): High-contrast summary title.
   - `body` (TEXT): Fact-verified narrative prose.
   - `event_ids_json` (TEXT): Associated detector event IDs.
   - `bites_json` (TEXT): Attached Suggestion Bites array.
   - `tags_json` (TEXT): Topic chips (e.g. `["GEX Wall", "CVD Divergence"]`).
   - `facts_json` (TEXT): Raw verified telemetry dictionary.

2. **`commentary_bites`**:
   - Tracks individual bite lifecycle (`bite_id`, `state`, `resolved_ts`, `outcome_json`, `pinned_rec_id`).

3. **`commentary_events` & `commentary_suppressed`**:
   - Stores raw detected events and suppression logs for offline threshold tuning and replay calibration.

---

## 6. UI Ergonomics in the Cockpit (Tab 10)

The **Session Commentary Tab** (`app/tab_commentary.py`) provides an institutional newsroom interface:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 🎙️ Session Commentary Stream · NIFTY                     🔴 LIVE (Auto-Refreshes: 3s) │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ Filters: [ALL] [🎯 Trade Tracking] [🛡️ Wall Events] [⚡ OrderFlow] [📰 Macro/News]   │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│ 🕒 14:15 IST · EXPIRY PINNING WINDOW                                                   │
│ 📌 Call Writers Defend 22,700 Strike Under Heavy Passive Inflow                         │
│ ────────────────────────────────────────────────────────────────────────────────────── │
│ Spot tested the 22,700 Call Wall at 14:12 IST (+45 pts session gain). OrderFlow delta  │
│ revealed sharp seller absorption with CVD plateauing at +14,200 contracts. Option OI   │
│ data confirms 22,700 CE writing increased by 18.4 Lakh shares, reinforcing resistance. │
│                                                                                        │
│ ⚡ ACT-IF BITE: Mean-Reversion Scalp toward Max Pain (22,650)                           │
│ Condition : Spot fails to breach 22,710 on 3-minute close                              │
│ Play      : Bear Put Debit Spread (22700 PE / 22600 PE)                                │
│ Target    : 22,650   |   Invalidation : 22,725 (Above wall defense high)               │
│                                                                                        │
│ 🏷️ Tags: [Call Wall Defense] [CVD Absorption] [Max Pain Pin]                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Key UI Features:
1. **Isolated Streaming Fragment (`@st.fragment`)**:
   Tab 10 refreshes its narrative feed independently every 3 seconds without re-rendering the outer application or disrupting chart views.
2. **Category Filter Chips**:
   Toggle views instantly between `ALL`, `🎯 Trade Tracking`, `🛡️ Wall Events`, `⚡ OrderFlow`, and `📰 Macro/News`.
3. **Pinned Recommendation Indicators**:
   Entries tied to active bookmarked trades feature distinct golden badges (`★ Pinned Setup Update`), providing immediate visual cues on monitored positions.
4. **Historical Session Dropdown**:
   Inspect past session stories to audit market behavior and evaluate how previous days unfolded.
