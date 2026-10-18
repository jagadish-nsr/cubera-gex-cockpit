# UI Migration Issue Register & Context Reference Guide
> **Purpose**: This reference register documents frequently occurring bugs, root causes, architectural patterns, and strict engineering principles established during the migration of the Cubera GEX Cockpit from Streamlit to the high-performance FastAPI + Vanilla JS institutional architecture. Feed this document into context during future iterations to prevent regressions.

---

## 1. Executive Master Table of All Issues & Audits

| # | Domain / Component | Bug / Audit Trigger | Root Cause | Architectural Solution & Prevention Rule |
|---|-------------------|---------------------|------------|------------------------------------------|
| **1** | **Target Engine Tab** | Missing Candidate Option Structures & Setups (±5 strikes ATM) | Streamlit had a 10-strike ladder with calculated Targets/SL, POP, and Greeks that was initially omitted from the new HTML/JS layout. | **Candidate Structures Table**: Implemented `/api/target-engine` returning ±5 strikes around spot with calculated T1/T2/T3, Invalidation Levels, Risk/Reward ratios, and direct 1-click execution hooks. |
| **2** | **Cockpit Tab** | Missing OrderFlow stream summary & Pre-Market Discovery Banner | Pre-market auction phase (09:00–09:08) state detector and real-time microstructure alert badges were not ported to the initial cockpit dashboard. | **Intraday Phase & Microstructure Stream**: Added Pre-Market Discovery Banner (09:07:30 Indicative Equilibrium) and live tape alert stream under the key levels cards. |
| **3** | **Options Analytics Tab** | Missing Top 6-Card KPI Strip & 25Δ IV Smile Curves | Initial migration only showed basic tables, missing the institutional KPI strip and term structure curve present in Streamlit. | **6-Card KPI Strip & Dual Smile SVG**: Added ATM IV, PCR OI, Max Pain Pin, IV Rank 30D, India VIX, Directional Bias cards, and the SVG 25Δ IV Skew & Term Structure Curve. |
| **4** | **Off-Market Hours Data Feed** | Recommendations and analytics showed "Session Closed. Off-Market Hours" instead of latest data | API endpoints previously blocked execution if `not is_market_open()`, returning empty states or errors. | **Graceful Session Persistence**: During off-market hours or weekends, APIs must seamlessly load and display the latest completed session's database snapshot (`data/gex.db`, `data/sentinel.db`) rather than halting. |
| **5** | **Dhan API Rate Limits & Off-Hours Calls** | Risk of exhausting Dhan API limits by polling broker endpoints while markets are closed | Background loops were firing live broker quote queries on weekends and nights. | **Off-Hours API Gating**: Freeze all live Dhan order and quote API calls when market is closed (`15:35` to `09:14` and weekends), except for asynchronous public market news RSS feeds. |
| **6** | **Zero-Fabrication Contract (All Tabs & Sidebar)** | Spot Live Quote flickered with synthetic price noise; prototype demo seeders injected fake rows | Prototype mock generator routines were mutating spot price and inserting dummy rows even outside of simulator mode. | **Strict Zero-Fabrication Rule**: Mock/demo data is strictly prohibited unless `simulation_mode == True`. Offline sessions freeze to official exchange close. All seeders return `ignored` when simulation mode is off. |
| **7** | **Header Telemetry Clock** | Subtitle clock flickering/jittering between two different date/time formats | WebSocket ticks sent `"%H:%M:%S IST"` while 3s polling sent `"%Y-%m-%d %H:%M:%S IST"`. The two streams continually overwrote each other. | **Single Source of Format**: Globally enforce `"%H:%M:%S IST"` across both WS and REST endpoints. Client sanitizes redundant `"IST"` tokens via regex before appending `" IST"`. |
| **8** | **TradingView Lightweight Charts Time Axis** | Time axis showed UTC hours (`03:45` to `10:00`) instead of Indian market hours (`09:15` to `15:30 IST`), causing an exact 5h 30m offset | Lightweight Charts strictly renders numeric timestamps in UTC. In Python, `.timestamp()` on an IST datetime returns UTC epoch seconds. | **IST Timestamp Shifting (+19,800s)**: Add `+19,800` seconds (5h 30m) to epoch timestamps sent to Lightweight Charts. When rendered via UTC, `10:00 UTC + 5h30m = 15:30`, perfectly displaying IST hours. Add `localization.timeFormatter` for hover badges. |
| **9** | **OrderFlow CVD Chart Profiles** | All 4 profile modes (Full, Pure, CVD, Trend) looked visually identical; no EMA 9/21 or GEX walls | Sub-series visibility was not toggled cleanly. GEX walls were plotted outside price scale because `autoscaleInfoProvider` was missing. | **Profile Matrix & Autoscale Provider**: Standardize 4 profiles with explicit series visibility/margins. Add `autoscaleInfoProvider` to `candlestickSeries` so GEX walls auto-expand chart scale margins. |
| **10** | **Popout Window (`/chart/sentinel`)** | "Open in New Window" was stuck on only one fixed profile, missing buttons, VWAP, EMAs, and walls | The popout button didn't forward the active profile in the URL. `/chart/sentinel` only instantiated candles and CVD, lacking full profile engine parity. | **100% Feature Parity**: Forward `&profile=...` in URL. Implement full indicator series, profile switcher buttons, and GEX walls in `/chart/sentinel` identical to the main cockpit. |
| **11** | **Target Engine Repricer & Target/SL Calculator** | Expiry contract was wrong/outdated (e.g. `2026-10-06`), Bull/bear/base presets and reset buttons were non-functional | In `web_server.py`, `get_target_engine` and `reprice_target_engine` used raw `dash.expiries` from stale offline snapshots instead of `get_sidebar_active_expiries()` (which dynamically computes upcoming active exchange expiries, e.g. `2026-10-19`). In `cockpit.js`, `applyTEPreset` looked for ID `te-sl-spot` instead of `te-input-sl-spot`, causing an immediate null return before doing anything. | **Active Expiry Dynamic Resolution & Synced Presets**: In `web_server.py`, resolve expiries via `get_sidebar_active_expiries` and filter crossed dates. In `cockpit.js`, fix element ID to `te-input-sl-spot`, wire dynamic target engine values to Bull/Bear/Base/Reset presets across both Mode 1 (Calculator) and Mode 2 (Sliders), and drive Black-76 Greek repricing from authentic model telemetry. |
| **12** | **Session Commentary Pre-Market Shock Matrix** | Pre-Market Shock Matrix was a static block taking excessive vertical height | Rendered as a plain non-collapsible `<div>` block without collapse/expand toggling. | **Collapsible Matrix with State Retention**: Converted to an accessible `<details class="collapsible-matrix">` element with interactive chevron indicator (`▼`/`▶`), `ontoggle` memory hook preserving user's collapsed state across auto-refreshes, and subtle institutional hover styling. |

---

## 2. The 4 Historical UI Migration Audit Phases

### Phase 1: Streamlit 11-Tab Feature Parity Audit
Triggered by: *"i still see lot of elements missing.. plz audit: cockpit orderflow stream summary, pre-market summary; target engine ±5 strikes ATM with targets/SL; sentinel tape tradingviewlite charts, 10 days CVD barcharts..."*
- **Target Engine**: Recreated the Candidate Option Structures & Setups table (`app/tab_target_engine.py`) with strikes ±5 around spot, projected Targets (TP1/TP2/TP3), stop losses, Probability of Profit (POP), and delta/gamma Greeks.
- **Cockpit**: Added Pre-Market 09:07:30 Discovery Banner and the live tape alerts stream under the key levels cards.
- **Sentinel Tape**: Integrated TradingView Lightweight Charts (v4.x) replacement for Streamlit Plotly charts, adding 60 FPS CVD Canvas, 10-Day Closing CVD Suite, and live block trade table.
- **Options Analytics**: Ported top 6-Card KPI Strip (ATM IV, PCR OI, Max Pain Pin, IV Rank 30D, India VIX, Directional Bias), dual IV smile curve SVG, and daily CVD ledger.

### Phase 2: Off-Market Feed & Dhan API Guarding Audit
Triggered by: *"how is the recommendations engine getting the data during off market hours, it was showing 'Session Closed'... check if we are calling any Dhan APIs during off market hours..."*
- **Off-Market Graceful Caching**: Replaced hardcoded "Session Closed" blocks with fallback readers that load the latest completed trading session from SQLite (`data/gex.db`, `data/sentinel.db`), ensuring full functionality on weekends/evenings.
- **API Quota Protection**: Added market hour guards so that Dhan broker APIs are never pinged outside 09:15–15:30 IST, preserving rate limits and API security.

### Phase 3: Zero-Fabrication & Mock Purging Audit
Triggered by: *"why is the 'Spot Live Quote' flicker without live? please audit thoroughly on all tabs (including sidebar) and ensure no demo/fabricated data is used..."*
- **Eliminated Fake Price Jitter**: Removed random price drift timers in `cockpit.js` and `web_server.py`. When market is closed, spot LTP freezes to official exchange close.
- **Simulator Mode Quarantine**: Confined demo data generators (`seed_sentinel_demo_if_needed`, `/api/sentinel-tape/reseed`) strictly to `simulation_mode == True`. Outside simulation mode, all seeding endpoints return `ignored`.

### Phase 4: Time Axis & Multi-Profile Standalone Parity Audit
Triggered by: *"I think UTC time issue still exists, check the time axis on the orderflow cvd charts... audit everywhere and fix it" / "orderflow CVD chart 'open in new window' is only showing one profile always"*
- **Lightweight Charts UTC Offset**: Converted all epoch seconds sent to charts with `+19,800s` (+5h 30m) offset via `_to_ist_chart_timestamp()`, causing Lightweight Charts UTC renderer to output Indian Market Hours (`09:15` to `15:30 IST`).
- **Profile Parity in Popout Window**: Rebuilt `/chart/sentinel` with query parameter `profile: str = "Full"`, interactive buttons (`⚡ Full`, `🎯 Pure`, `🌊 CVD`, `📈 Trend`), all indicator series (VWAP, EMA ribbons, delta volume, CVD), GEX walls, and live IST crosshair HUD.

---

## 3. Strict Rules for Future Development

When modifying or adding features to the Cockpit:

1. **Rule of Single Timezone Authority (IST Only)**:
   - All server times must be explicitly anchored in `IST` (`Asia/Kolkata` / `+05:30`).
   - For chart epoch feeds (Lightweight Charts), always route through `_to_ist_chart_timestamp()` so UTC formatting matches IST calendar hours.
   - For hover tooltips on shifted timestamps, use `getUTCHours()` and `getUTCMinutes()`.

2. **Rule of Zero Synthetic Noise Outside Simulator**:
   - Never inject random fluctuations, demo price drift, or fake ticks into live or offline WebSocket/REST streams.
   - All synthetic generators (`seed_sentinel_demo_if_needed`, `/reseed`) must be gated behind `simulation_mode == True`.

3. **Rule of Embedded & Popout Parity**:
   - Any indicator, overlay, wall, or feature added to the embedded Sentinel tab chart MUST also be added to `/chart/sentinel` (and vice-versa).
   - Both windows must support all 4 profiles (`Full`, `Pure`, `CVD`, `Trend`).

4. **Rule of GEX Wall Autoscale Inclusion**:
   - Whenever GEX price lines (Call Wall, Put Wall, Gamma Flip) are rendered on a Lightweight Chart series, an `autoscaleInfoProvider` must be attached to prevent the lines from being clipped off the screen.

5. **Rule of Off-Market Persistence**:
   - Never show blank/error states when market is closed. Always display the latest completed session's verified database snapshot.
