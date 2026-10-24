# Session Commentary Engine (with Suggestion Bites): Implementation Plan & AI-Editor Build Prompt

**Companions to:** `nifty-options-analytics-implementation-plan.md` (Analytics Service) and `target-engine-implementation-plan.md` (Target Engine).
**What this adds:** A engine that narrates the trading session as a short, high-signal story (opening gap, gamma/OI wall shifts, institutional-style activity, divergences, impulsive moves, news-linked moves, expiry dynamics) and attaches **Suggestion Bites**: brief, conditional, level-anchored guidance a trader can act on, each with an invalidation level, risk numbers and a calibration badge.

**Design stance (important for the AI editor):**
- **Story first, signal second.** A day should produce roughly 6-15 commentary entries, not a ticker.
- **Facts are verified, prose is generated.** Detectors produce structured events; the LLM only phrases them; every number in the text is checked against the source data before publishing.
- **Suggestions are conditional, never imperative.** A bite says "if X holds, this structure fits, invalid below Y," never "buy now." Every bite carries its own invalidation, risk, and honesty badge.
- **Bites earn their place.** They run in shadow mode, get graded against outcomes, and bite types with poor track records are demoted automatically.
- **Advisory only.** No order placement. Ever. Execution, if ever added, is a separate gated module.

> Analytical tool, not investment advice. Options carry substantial risk of loss. See Section 9 for regulatory considerations if the output is shared with anyone other than yourself.

---

## 0. Instructions to the AI coding editor (read first, every session)

```
You are adding a Session Commentary Engine, including a Suggestion Bite
subsystem, to an existing Nifty options analytics codebase. The Analytics
Service (chain snapshots, ATM IV, PCR, max pain, OI buildup, walls, skew, RV,
expected move) and the Target Engine (scenarios, targets, IV forecast, option
repricer, candidate ranking, backtest harness) are built or partly built.
DO NOT rebuild any of it. This engine CONSUMES their outputs.

Before writing code:

1. Audit the repo and report:
   - Which Analytics Service and Target Engine components already exist, where
   - Existing GEX/gamma code, Black-76/Greeks code (the Target Engine pricer is
     reused for gamma-flip computation), any wall-detection code
   - Existing news ingestion, RSS/API clients, NLP/embedding utilities
   - Existing LLM client or wrapper (provider, retry, prompt templates, cost
     controls). Reuse it; do not add a second LLM client.
   - Existing notification channels (Telegram/Slack/email/webhook/push)
   - Existing replay / point-in-time machinery from the backtest harness
   - Existing positions/orders fetchers (needed only for position-aware bites)
   - Existing config pattern, scheduler, DB layer, API and frontend frameworks

2. For each feature in Section 4, search for existing code first. EXTEND or
   WRAP. Report per feature: reused / extended / net-new, with file paths.

3. Match the repo's language, async style, naming, logging, error handling.
   New dependencies only if nothing in the repo does the job; flag first.

4. Build in the phase order in Section 3. Each feature = pure functions with
   unit tests on fixtures first, then persistence, then API, then UI.

5. Hard rules:
   a. ALL thresholds, weights, cooldowns, budgets, phrase lists, and gating
      rules live in ONE config file. No magic numbers in code.
   b. Everything must be REPLAYABLE: any past session can be re-run from stored
      data to regenerate the exact commentary and bites (with the LLM stubbed
      or cached for determinism). Replay is how thresholds get tuned.
   c. NO look-ahead: an event or bite at time t may only use data with ts <= t.
      Enforce with the existing PointInTimeView wrapper and tests.
   d. The LLM NEVER originates facts, numbers, levels, or recommendations. It
      receives structured JSON and returns prose. Every number in its output is
      verified against the JSON; mismatches cause rejection and a template
      fallback. The suggestion content (structure, levels, risk numbers) comes
      from the Target Engine and rules, not from the LLM.
   e. Every probability or confidence shown carries its calibration status
      ("heuristic/uncalibrated" or "calibrated on N samples").
   f. Forbidden-language lint runs on all output (Section 4.C4.8).
   g. No order placement code anywhere in this module.

6. Ask before proceeding if: no timestamped news source exists; the repo's LLM
   wrapper has no way to enforce structured output; tick/depth data needed by
   the institutional-footprint detector isn't available; or positions data
   cannot be fetched (then position-aware bites are skipped, not faked).
```

---

## 1. Codebase Audit Checklist (Verified against Live Repository)

| Area | Exists? | Location | Reuse plan / Implementation Status |
|---|---|---|---|
| **chain_snapshots, ATM IV, PCR, max pain** | **Yes** | [`data/market_snapshot.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/market_snapshot.py), [`app/data_access.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/app/data_access.py) | **Reused:** PointInTimeView pulls spot, ATM IV, 1-day expected move, PCR, and Max Pain directly. |
| **Wall detection (OI walls)** | **Yes** | [`commentary/detectors/walls.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/commentary/detectors/walls.py), [`target_engine/levels.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/target_engine/levels.py) | **Extended:** Wall tests, breaks, and strike migrations evaluated with hysteresis and cooldown. |
| **GEX / gamma exposure code** | **Yes** | [`data/market_snapshot.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/market_snapshot.py), [`app/data_access.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/app/data_access.py) | **Reused:** Gamma Flip zero-crossing level and Net GEX sign (amplifying vs dampening) integrated. |
| **Black-76 pricer / Greeks** | **Yes** | [`target_engine/pricer.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/target_engine/pricer.py) | **Reused:** Evaluates strike implied volatility, delta, and target/invalidation probabilities. |
| **Scenario scoring & Trade Recommendations** | **Yes** | [`analytics/trade_ideas.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/analytics/trade_ideas.py), [`target_engine/scenarios.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/target_engine/scenarios.py) | **Extended:** 14 institutional candidate strategies (7 CE, 7 PE), signal latching, live telemetry, and pre-market regime weighting. |
| **Pre-Market Equilibrium & Gap Regimes** | **Yes** | [`data/pre_post_market.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/pre_post_market.py) | **Reused:** 09:00–09:08 equilibrium discovery, 5 auction regimes, and dynamic `gap_fill_status`. |
| **Backtest / PointInTimeView** | **Yes** | [`commentary/qa/replay.py`](commentary/qa/replay.py) | **Test harness only.** Walks stored commentary with no look-ahead. `app/market_simulator.py` was removed. It is not a product screen, and this process has no `simulation_mode`. |
| **Futures price + OI + depth feeds** | **Yes** | [`app/ws_spot_feed.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/app/ws_spot_feed.py), [`app/spot_ticker.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/app/spot_ticker.py), [`data/dhan_live.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/dhan_live.py) | **Reused:** WebSocket tick feed (<1ms push) provides spot ticks, futures OI, and option quotes. |
| **Dual Streaming Modes (Live / Offline)** | **Yes** | `config.is_market_open` and `web_server.py` | **Reused:** Live feed during the F&O session (09:15–15:40 IST). Closed hours serve the last stored snapshot. |
| **Bank Nifty / heavyweight / breadth feeds** | **Yes** | [`data/sectors.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/sectors.py), [`data/indices.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/indices.py) | **Reused:** Sector breadth and relative strength feed divergence detectors. |
| **News ingestion & Macro Catalysts** | **Yes** | [`data/news.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/news.py), [`commentary/detectors/news.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/commentary/detectors/news.py) | **Reused:** Published timestamp matching with $[-10\text{m}, +3\text{m}]$ lead/lag window. |
| **Narrator & Fact Verifier** | **Yes** | [`commentary/narrator/`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/commentary/narrator/) | **Net-New:** Deterministic template library, strict numeric verification, and storytelling formatter. |
| **Pinned Positions / Bookmarked Trades** | **Yes** | [`data/journal/recommendations_store.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/journal/recommendations_store.py), [`commentary/detectors/pinned.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/commentary/detectors/pinned.py) | **Extended:** Real-time tracking of pinned ideas for Target 1 approach, target hit, and invalidation. |
| **Persistence, API & Presentation** | **Yes** | [`commentary/storage/db.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/commentary/storage/db.py), `web_server.py` | SQLite WAL store and the web commentary stream. |

---

## 2. Architecture Overview

### 2.1 Dual Streaming Modes: Real-Time vs Offline Auto-Switch

The engine operates deterministically across two production streaming modes:

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│                             COCKPIT STREAMING ARCHITECTURE                            │
├───────────────────────────────────────────┬───────────────────────────────────────────┤
│ Mode 1: 🟢 Real-Time WebSocket Mode       │ Mode 2: 🛑 Offline Auto-Switch Mode       │
├───────────────────────────────────────────┼───────────────────────────────────────────┤
│ • Active during Market Hours (09:15–15:30)│ • Automatically engages when market closes│
│ • Pre-Open Discovery (09:00–09:08 IST)    │   (evenings, weekends, holidays).         │
│ • Extended Settlement (15:30–15:40 IST)   │ • Pauses external network polling.        │
│ • Evaluates detectors on live ticks.      │ • Serves the last SQLite snapshot.        │
│ • Streams verified entries on the web UI │ • Closed hours keep the last snapshot.   │
└───────────────────────────────────────────┴───────────────────────────────────────────┘
```

```
  Data Sources:
  [Real-Time WebSocket Tick Feed]  OR  [Last stored SQLite snapshot]
  (Spot, Futures OI, Depth, ATM IV, Pre-Market Equilibrium, Pinned Recommendations)
                           │
                           ▼
    C1 Detectors ───────► Event(ts, type, severity_raw, facts{}, quality_flags)
                           │
                           ▼
    C2 Significance Scoring + Confluence Merge + Cooldown + Daily Budget
                           │
                           ▼
    C3 Story State  (phase of day, running thesis, open threads, prior entries)
                           │
               ┌───────────┴───────────┐
               ▼                       ▼
    C4 Suggestion Bite Engine     C5 Narrator (Deterministic Template & Verifier)
    (14 institutional strategies,        │
     signal latching & telemetry)        │
               └───────────┬───────────┘
                           ▼
    C6 Delivery: Freeflowing Storytelling Stream, Pinned Alerts Strip, EOD Recap
                           │
    C7 Replay & QA Harness: Walk-forward session replay, expectancy grading ($0.08R$)
```

**Core contracts:**

```
Event       { id, ts, type, facts{}, magnitude_sigma, quality_flags[], related_ids[] }
Scored      { event, significance, reasons[], merged_event_ids[] }
StoryState  { phase, thesis{bias, confidence, since_ts}, open_threads[], entries[] }
Bite        { id, ts, type, headline, condition{machine_checkable}, action_frame,
              structure{legs[], from_candidate_id}, levels{trigger, invalidation, target},
              risk{max_loss_per_lot, rr, breakevens}, valid_until,
              scenario_link, badge{calibrated, n_samples, mode}, facts_used[],
              lifecycle{state, resolved_ts, outcome}, pinned_rec_id }
Entry       { id, ts, event_ids[], headline, body, bites[], verified: bool, tags[], facts{} }
```

---

## 3. Build Phases

| Phase | Features | Exit criteria |
|---|---|---|
| **0** | Audit | Report and table. No code. |
| **1** | C0 contracts/config, C1a gap, wall, session-structure detectors | Detectors replay on a stored day and produce plausible events with facts |
| **2** | C1b GEX/flip, divergence, impulse, volatility-regime, expiry detectors | Each detector has unit tests plus a golden replay day |
| **3** | C1c institutional-footprint and news-link detectors | Footprint labels are never identity claims; news matching has a precision check on a labelled sample |
| **4** | C2 scoring, C3 story state | A replayed day yields 6-15 entries, no spam, coherent thread references |
| **5** | C5 narrator and verifier | 100% of published numbers verified; fallback templates work with the LLM disabled |
| **6** | C4 Suggestion Bites, **shadow mode only** | Bites are generated, tracked, and graded but not shown as actionable |
| **7** | C7 replay/QA and bite grading over a minimum number of sessions | Per-bite-type track record exists; demotion logic works |
| **8** | C6 delivery, C8 API/UI, enable bites to users per gating rules | Live feed with the badge, trigger tracking, and EOD recap |

**Sequencing note:** Phase 6-7 (bites in shadow mode, graded) must complete before bites are shown as actionable. The commentary (Phases 1-5) is useful on its own, and bites are only as good as the Target Engine's calibration.

---

## 4. Feature Specifications

### C0. Contracts, Config, Time

All thresholds in one file (`commentary.yaml` or the repo's equivalent). Key sections:

```
session: { open: "09:15", close: "15:30", tz: "Asia/Kolkata",
           phases: [pre_open, opening_15m, morning, midday, afternoon, closing_30m] }
budget:  { max_entries_per_day: 15, min_gap_minutes: 8,
           max_actionable_bites_per_day: 3, max_bites_per_entry: 2 }
significance: { publish_threshold: 0.6, weights: {...}, time_of_day_multipliers: {...} }
detectors: { <name>: {thresholds..., cooldown_min} }
bites: { enabled_types: [...], mode: shadow|live, gating: {...}, phrase_lint: {...} }
narrator: { model, max_tokens, temperature: 0.2, fallback: template }
news: { sources, relevance_keywords, match_window_before_min: 10, match_window_after_min: 3 }
profile: { capital, max_risk_pct, allowed_structures: [defined_risk_only|buyer_only|any],
           lot_size_source: instrument_master }
```

Time rules: all timestamps timezone-aware (Asia/Kolkata); every detector declares which phases it is active in; special sessions (muhurat, early close) handled via config flag, not hardcoded dates.

---

### C1. Event Detectors

Each detector is a pure function `(point_in_time_view, state) -> list[Event]`, with a **cooldown** and **hysteresis** so it doesn't flicker. All distances are expressed in σ units (using ATM IV expected move) so thresholds are regime-independent.

#### D1. Opening Gap & Pre-Market Auction Regime
- **Inputs:** prior close, pre-market equilibrium discovery ([`data/pre_post_market.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/pre_post_market.py), discovered 09:00–09:08 IST), first print/open price, walls (call wall, put wall), gamma flip level, max pain, ATM IV.
- **Compute:** `gap_pts = open - prior_close`, `gap_pct`, `gap_sigma = gap_pts / σ_1day`, gap direction, relative positioning against GEX walls.
- **Pre-Market Regime Classification (5 Auction Archetypes):**
  1. `GAP_UP_RUNAWAY`: Breakaway open above Call Wall with positive delta momentum.
  2. `GAP_DOWN_TRAP`: Breakdown open below Put Wall with negative delta acceleration.
  3. `SHORT_GAMMA_WALL_TEST`: Gap open directly into Call Wall ($\le 0.15\sigma$), triggering immediate call writer defense.
  4. `PUT_WALL_BREAKDOWN_TEST`: Gap open directly into Put Wall ($\le 0.15\sigma$), testing put writer resolve.
  5. `AUCTION_BALANCED`: Contained within prior day range / between walls, favoring rotational mean-reversion.
- **Intraday Gap Fill Tracking:** Dynamically evaluates `gap_fill_status` (`FILLED` vs `UNFILLED`) against live high/low prints. When price fills the gap, emits a `GAP_FILLED` event ("Morning opening gap has been 100% filled as spot returns to ₹X").
- **Event facts:** `open`, `prior_close`, `gap_pts`, `gap_pct`, `gap_sigma`, `nearest_wall`, `dist_to_wall`, `gap_regime`, `gap_fill_status`.
- **Edge cases:** pre-open feed unavailable $\to$ fall back to regular 09:15 open vs prior close; expiry day $\to$ flag higher gamma sensitivity; tiny gaps below `min_gap_sigma` suppress standalone gap events.

#### D2. OI Wall / GEX Wall Shifts
- **Walls:** reuse the Analytics Service wall detector (OI share, OI change).
- **GEX per strike (estimated):** `gex_k = sign * gamma_k * OI_k * lot_size * spot^2 * 0.01`, where `sign` follows a **configurable dealer-positioning convention** (default: calls +, puts − as the common retail convention). Label everything "estimated gamma exposure."
- **Gamma flip level:** recompute net GEX across a grid of hypothetical spots (reusing the Target Engine pricer to get gamma at each spot) and find the zero crossing. Output the flip level and the net GEX sign at current spot (**positive-gamma regime = dampening; negative = amplifying**, as an estimate).
- **Events:**
  - *Wall migration:* the top call/put wall moves to a different strike (and holds for N snapshots).
  - *Wall strengthening/erosion:* wall OI change beyond X% within Y minutes.
  - *Wall test:* price within `0.1σ` of a wall (record whether it rejects or breaks).
  - *Wall break:* price closes N bars beyond a wall with confirming OI behavior (call OI unwinding on an upside break = more credible).
  - *Flip level cross:* spot crosses the estimated flip level.
  - *Regime change:* net GEX sign flips.
- **Edge cases:** thin chain or stale data → suppress; expiry day → walls change fast, widen hysteresis; two near-equal walls → report as a wall zone.

#### D3. Institutional-Style Activity (footprints, not identities)
- **Signals (any combination, each with its own z-score vs trailing norm):**
  - Futures: price move with OI change of significant size (the 4-state matrix) and volume burst.
  - Options: single-strike OI jump far above that strike's trailing change distribution; simultaneous OI build at paired strikes (spread/condor-like footprint).
  - Depth: persistent bid/ask quantity imbalance (EMA-smoothed, rejecting single-snapshot spikes).
  - Basis: abrupt futures premium change.
  - Large prints if the tick feed supports trade size.
- **Output labels (strict vocabulary):** `institutional-style long buildup`, `institutional-style short buildup`, `aggressive call writing at 23,000`, `put unwinding at 22,800`, `spread-like positioning`. **Never** "FIIs bought" or "smart money." Attach the evidence list.
- **End-of-day enrichment:** when NSE participant-wise OI and FII/DII data arrive after the close, the EOD recap may add "participant data later showed FIIs net short futures," clearly marked as after-the-fact confirmation.
- **Edge cases:** hedge/arbitrage flows look like directional flow, so a confidence field is lowered when basis and futures move together (cash-futures arbitrage signature).

#### D4. Divergences
Each divergence is a rule over two series with a sign test over a rolling window and a minimum magnitude:
- Price new high/low **vs** futures OI falling (move without fresh participation).
- Price up **vs** ATM IV up (unusual; hedging or event premium).
- Price vs PCR trend (price rising while PCR falling persistently).
- Nifty vs Bank Nifty relative strength.
- Index vs breadth / heavyweight contribution (needs constituent quotes; skip with a flag if absent).
- Price vs OI-buildup type (rally on long unwinding of puts, i.e. support leaving).
- **Event facts:** the two series, direction of each, window, magnitude, historical hit rate if available.
- **Edge cases:** require N consecutive confirmations to avoid reporting noise; auto-expire when the divergence resolves (and emit a "resolved" follow-up if it mattered).

#### D5. Impulsive Moves
- **Detection:** displacement over a rolling window (e.g., 5/10/15 min) exceeding `k·σ_window`, **and** a volume burst ratio above threshold.
- **Classification (assigned after a short lookahead of M minutes, in replay and in live mode, using only data up to that time):** `follow-through`, `reversal` (retraced >X% quickly), `absorption` (high volume, little progress at a wall).
- **Facts:** magnitude (pts, σ), speed, volume ratio, what was crossed (wall, flip level, VWAP, day high/low), simultaneous IV/skew change.
- **Edge cases:** the first 5 minutes after open and the closing auction window are excluded or threshold-adjusted; scheduled event prints (RBI at known time) are tagged as `scheduled_event_move`.

#### D6. News-Linked Moves
- **Ingestion:** adapters for configured sources (RSS/news API/exchange announcements) producing `{source, headline, url, published_ts, ingested_ts}`. **Use `published_ts` for matching, never ingestion time.**
- **Relevance:** entity and keyword map (RBI, Fed, CPI, crude, geopolitical, index heavyweights' results, regulatory actions) plus an optional embedding similarity filter; unrelated headlines are dropped.
- **Matching rule:** an impulsive move (D5) or volatility spike (D7) links to a headline whose `published_ts` falls in `[move_start − match_window_before, move_start + match_window_after]`. Multiple candidates: choose by relevance score; show up to two.
- **Wording constraint:** always "possible catalyst" or "coincided with"; the system cannot establish causation. If the move **precedes** the headline by more than the allowed lead, no link is made.
- **Quality check:** a labelled sample of historical moves is used to measure match precision before enabling this detector for users.
- **Edge cases:** news with unreliable timestamps → flagged `timestamp_uncertain`, no link; breaking-news bursts → merged into one thread.

#### D7. Volatility Regime
- ATM IV spike or crush versus intraday reference, skew shift, term-structure inversion, straddle repricing, India VIX moves.
- Facts: before/after levels, the percentile, and whether it coincided with a price move or a scheduled event.

#### D8. Expiry Dynamics
- Active in the last sessions: max pain proximity and pinning behavior, gamma concentration near spot, premium decay anomalies in the straddle, last-hour OI unwinding.
- Gated by DTE; expiry-day entries carry a high-gamma caution flag.

#### D9. Session Structure
- Opening-range (first 15/30 min) break up/down, VWAP loss/reclaim, day-high/low tests, previous-day high/low interactions, midday compression (range contraction) and expansion.

#### D10. Scheduled-Event Markers & Pinned Position Sentinel
- **Scheduled Macro Events:** From event calendar: "RBI policy in 30 minutes," "US CPI tonight." Drives STAND-ASIDE gating.
- **Pinned Recommendation Sentinel ([`commentary/detectors/pinned.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/commentary/detectors/pinned.py)):** Monitors trader-bookmarked recommendations in real time against live ticks:
  - `PINNED_TARGET_APPROACH`: Spot within proximity threshold of Target 1.
  - `PINNED_TARGET_HIT`: Spot reaches Target 1; emits update for trailing stop to entry or partial booking.
  - `PINNED_INVALIDATION_WARNING`: Spot within warning threshold of invalidation wall; emits thesis protection alert.

---

### C2. Significance Scoring, Merging, and Budget

**Score:** `significance = f(magnitude_sigma) × novelty × confluence × time_weight × quality`
- **Magnitude:** monotone, saturating function of σ-normalized size: $f(\sigma) = 1 - e^{-\sigma/1.2}$.
- **Novelty:** decays if a similar event fired recently (same type, same level).
- **Confluence:** events within a short window that share levels or direction are **merged** into one richer event (e.g., wall break + IV spike + volume burst + news match = one entry).
- **Time weight:** configurable multipliers (opening, expiry hour, and closing are more significant).
- **Quality:** multiplied down when quality flags are set (stale data, thin chain, missing depth).

**Publication rules:**
1. Publish only if `significance >= publish_threshold` (0.60).
2. Respect `min_gap_minutes` (8 minutes) unless severity is extreme ($\ge 0.88$ breaking override).
3. Daily budget: when cap is near ($\le 3$ remaining of 15), raise threshold dynamically ($+0.15$).
4. Every suppressed event is stored with its reason in SQLite `commentary_suppressed` for replay tuning.

---

### C3. Story State Manager

Maintains the narrative continuity that separates a story from a log:
- **Phase of day:** `pre_open`, `opening_15m`, `morning`, `midday`, `afternoon`, `closing_30m`.
- **Pre-Market Regime Integration:** Captures equilibrium regime (`GAP_UP_RUNAWAY`, `GAP_DOWN_TRAP`, `SHORT_GAMMA_WALL_TEST`, `PUT_WALL_BREAKDOWN_TEST`, `AUCTION_BALANCED`) and gap fill state.
- **Running thesis:** `{bias: bullish|bearish|range|unclear, confidence, since}` derived from scenarios and events.
- **Open threads:** Unresolved items (gap not yet filled, wall under test, divergence open). Follow-up entries reference and close threads.
- **Callbacks:** Narrator receives prior entries and open threads, referencing them naturally.

---

### C4. Suggestion Bite Engine (Trade Recommendations Integration)

**Purpose:** Add short, applicable, conditional guidance to commentary entries so a trader knows what a development means for decisions, without issuing commands or guarantees.

#### C4.1 Institutional Recommendations Pipeline
- **14 Parallel Strategies:** 7 Call strategies (Momentum, Structure, Breakout, Vanna, Gamma Squeeze, Charm, Reversal) and 7 Put strategies evaluated concurrently.
- **Quality-Gate Partitioning:**
  - *Higher Conviction:* Score $> 85$. Displayed whenever available.
  - *Lower Fallback:* $75 \le \text{Score} \le 85$. Displayed only if zero higher conviction candidates exist.
- **Signal Latching (`_LATCH_MANAGER`):** Eliminates card flickering and disappearing across live streaming ticks. Maintains candidates in an active latched state until invalidated or expired.
- **Live Telemetry Tracking:** `update_trade_idea_live_telemetry` provides real-time target progress %, spot invalidation buffer (`inval_buffer`), and breach status (`inval_breached`).
- **Pre-Market Regime Confluence:** Recommendation scores incorporate Factor 13 ($\pm 6$ pts) linking the pre-market auction regime and gap fill state with strategy suitability.

#### C4.2 Bite types

| Type | Meaning | Storytelling Example |
|---|---|---|
| **WATCH** | A level or condition worth monitoring; no action implied | "Bite: WATCH 23,000. A 5-min close above it would weaken the wall-defence read." |
| **PREPARE** | A setup forming; trigger and fitting structure identified | "Bite: PREPARE. If price slips back under 22,900 (estimated gamma flip), a range-to-bearish read strengthens; 22,900/22,750 put debit spread would fit; est. max loss ₹X/lot; invalid on a close above 23,030. Badge: heuristic, shadow-graded." |
| **ACT-IF** | Conditional idea: trigger, structure, invalidation, target, risk | "Bite: ACT-IF. If 5-min closes beyond 23,000: bull call spread 23,000/23,200 fits; target zone 23,180, invalid beyond 22,940; R:R 1.8; est. max loss ₹2,200/lot. Badge: heuristic, shadow-graded." |
| **TRIGGERED** | A previously issued conditional idea has fired its trigger | "Bite: TRIGGERED (the earlier 22,900 put-spread idea is now live; target zone 22,780, invalid above 22,960)." |
| **MANAGE** | Guidance for an active or pinned trade | "Bite: MANAGE (Target approached near 22,785; trailing stop to breakeven or partial profit booking are valid options; invalidation 22,960)." |
| **STAND-ASIDE** | Low edge or elevated risk; waiting is favored | "Bite: STAND-ASIDE for tomorrow's open: event risk overnight." |
| **HEDGE** | Risk-reduction framing for short-gamma or large exposures | "Bite: HEDGE. Short-gamma exposure into resistance; defined-risk spreads reduce tail risk." |

#### C4.3 Required Fields & Gating Rules
- **Machine-checkable condition:** e.g., `{"field": "close_5m", "op": ">", "val": 23000.0}`.
- **Action frame:** Directly from ranked `TradeIdea`, never hallucinated.
- **Gating Rules:**
  - *Verdict gate:* ACT-IF/PREPARE requires favorable score ($\ge 75$, latch active).
  - *Quality flags:* Stale ticks, missing depth, or thin chain suppresses actionable bites.
  - *Time gating:* No new ACT-IF during initial 10m of open or final 15m of close.
  - *Structure limits:* Defined-risk only (spreads, condors, long options); naked short options strictly blocked.
  - *Kill switch:* Master config switch disables all bites instantly if triggered.
  - *Shadow mode gating:* Live display requires minimum 20 shadow sessions passing out-of-sample expectancy ($> +0.25R$ post-friction). Auto-demotion downgrades degrading types.

#### C4.4 Bite lifecycle (tracked, not fire-and-forget)

```
ISSUED -> TRIGGERED -> (TARGET_HIT | STOPPED | EXPIRED_AFTER_TRIGGER)
       -> INVALIDATED (invalidation hit before trigger)
       -> EXPIRED (valid_until passed untriggered)
```
- A condition evaluator runs on every new bar/snapshot. When a bite **triggers**, the engine emits a short follow-up entry ("Trigger reached: 23,000 closed above on rising volume; the idea is now live, invalid below 22,930"). When it invalidates or expires, it says so. This closes the loop and prevents stale advice lingering.
- Outcome fields (realized underlying path, option P&L if simulated using the repricer and subsequent quotes with the Target Engine's entry/exit conventions) are stored for grading.

#### C4.5 Position-aware mode (optional)
- If a positions client exists, fetch open index-option positions and generate MANAGE bites: profit-protection near walls, risk flags when price approaches a stop level, greek exposure warnings (e.g., "your net short vega with an IV spike underway").
- Read-only. If positions can't be fetched, skip silently and say position-aware bites are off.

#### C4.6 Shadow mode and promotion
- **Shadow mode (default for first release):** bites are generated, stored, and graded, but shown only in a research view, not in the live feed.
- **Promotion rule (per bite type):** a bite type is enabled for live display only after N shadow sessions (config minimum) with out-of-sample outcomes showing: trigger precision, post-trigger expectancy after costs not below configured minimum, and acceptable invalidation behavior. Bootstrap confidence intervals required; small samples are labelled "not significant."
- **Auto-demotion:** a live bite type whose rolling track record drops below thresholds is automatically downgraded (e.g., ACT-IF → PREPARE → WATCH) and flagged.

#### C4.7 Feedback and track record
- Each bite has "useful / not useful / acted / ignored" feedback buttons (optional, stored).
- Track-record panel: per bite type, count issued, triggered %, target-hit %, stop-hit %, average realized R, calibration of the attached probabilities.

#### C4.8 Language rules and lint (enforced in code, not just prompts)
- **Required patterns:** conditional framing ("if", "a close above", "would fit"), an invalidation level, and a risk number in every ACT-IF/PREPARE bite.
- **Forbidden phrases (configurable list):** "sure shot," "guaranteed," "can't lose," "must buy/sell," "buy now," "sell now," "risk-free," "easy money," urgency language ("hurry," "last chance"), and any unconditional imperative.
- **Tone:** neutral and specific; options are offered ("partial booking or trailing the stop are options"), not commanded.
- **Lint failure:** the bite falls back to its deterministic template; if that fails too, the bite is dropped and logged.
- Every bite ends with a short, fixed footer in UI surfaces: "Decision-support, not advice. Check position sizing against your own risk limits."

---

### C5. Narrator (LLM, facts-only) and Verifier

**Input to the LLM (strict JSON):** merged event(s) with facts, story state (phase, thesis, open threads), last 3 entries, style guide, and, for bites, the **already-built** Bite object (the LLM phrases it, it doesn't design it).

**Output contract:** `{ "headline": str, "body": str, "bite_text": str|null }` in a structured-output mode if the repo's LLM client supports it.

**Verifier (mandatory, deterministic):**
1. Extract every number, level, percentage, and time from the output; each must match a value in the input JSON (with formatting tolerance). Any unmatched number → reject.
2. Check directional words against facts (an entry can't say "rallied" if the facts say a decline).
3. Run the C4.8 lint.
4. Check length limits and that referenced threads exist.
5. On failure: retry once with the errors fed back; then fall back to the deterministic template for that event type.

**Templates:** every event type and bite type has a plain deterministic template, so the engine works with the LLM disabled (also used in replay for determinism and in outages).

**Style guide (config):** short sentences, plain English, hedged wording for inferred items ("looks like," "suggests"), no hype, no emojis unless configured, ≤ N words per entry, a narrative callback when an open thread exists.

**Cost control:** one narrator call per published entry (not per event); cache by event-fact hash; configurable model and token limits.

---

### C6. Delivery & UI Integration

- **Freeflowing Storytelling Stream:** Clean chronological or reverse-chronological narrative stream without individual card frames or per-paragraph expander bloat. Native Streamlit Markdown rendering eliminates unrendered HTML tags or raw JSON leakage. Format: `DD Mon YYYY, HH:MM IST | **Bold Topic Header**: Narrative body. **Bite: TYPE** details.`
- **Pinned Recommendations Alert Strip:** Persistent top strip alerting on real-time Target 1 approach, Target 1 reach, and Invalidation warnings for bookmarked trades.
- **Copy-to-Journal Exporter:** One-click plain text copy block for pasting the session's clean narrative into personal trading journals.
- **Unified Fact Verification Drawer:** Single bottom drawer verifying ground-truth exchange data (Spot, Call Wall, Put Wall, Gamma Flip, Max Pain, Expected Move) using native Streamlit metrics without raw JSON dumps.
- **Push Alerts (via configured webhook/stream):** High-priority alerts for (a) extreme break events ($\ge 0.88$ override), (b) bite triggers, and (c) pinned trade invalidations.
- **End-of-Day Recap & Scorecard:** Session story in $\le 10$ lines with scorecard: which walls held, which bites triggered, outcomes, and prediction audit.

---

### C7. Replay and QA Harness

- **Replay:** Rerun stored sessions through C1–C5 deterministically; template fallback ensures 100% reproducible prose without LLM token cost.
- **Detector QA:** Validates precision/recall on labelled fixture sessions; enforces zero point-in-time violations.
- **Bite QA & Track Record:** Simulates bite outcomes using realistic friction ($0.08R$ costs per trade). Evaluates win rate, average $R$, and post-cost expectancy ($> +0.25R$ promotion threshold).
- **Auto-Demotion Engine ([`commentary/qa/demotion.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/commentary/qa/demotion.py)):** Monitors rolling track records and demotes underperforming live bite types automatically (`ACT-IF` $\to$ `PREPARE` $\to$ `WATCH`).

---

### C8. Persistence, API, UI

**Database Tables ([`commentary/storage/db.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/commentary/storage/db.py)):**
- `commentary_entries`: `entry_id`, `ts`, `symbol`, `phase`, `headline`, `body`, `event_ids_json`, `verified`, `bites_json`, `tags_json`, `facts_json`
- `commentary_bites`: `bite_id`, `entry_id`, `ts`, `symbol`, `bite_type`, `headline`, `action_frame`, `levels_json`, `risk_json`, `condition_json`, `badge_json`, `state`, `resolved_ts`, `outcome_json`, `pinned_rec_id`
- `commentary_events`: `event_id`, `ts`, `symbol`, `event_type`, `facts_json`, `magnitude_sigma`, `quality_flags_json`
- `commentary_suppressed`: `suppress_id`, `event_id`, `ts`, `symbol`, `event_type`, `reason`
- `session_recaps`: `session_date`, `symbol`, `recap_text`, `open_threads_json`, `stats_json`

**UI:** the web cockpit commentary stream (`web_server.py`, `app/static/js/cockpit.js`).
- Filter options: all entries, actionable bites, pinned recommendations.

---

## 5. Example Day (Production Verified Freeflowing Story Format)

> **14 Oct 2026, 09:15 IST | **Gap-Up Open**: Nifty opened 0.7% (about 1.0σ) above the prior close, straight into the 23,000 call wall (24% of nearby call OI). **Bite: WATCH 23,000** — A 5-min close above it would weaken the wall-defence read.
>
> **14 Oct 2026, 09:48 IST | **Wall Holds So Far**: Buyers reached 22,985 but couldn't close above 23,000. Call OI there rose 18% during the test, which looks like writers defending it. **Bite: PREPARE** — If price slips back under 22,900 (estimated gamma flip), a range-to-bearish read strengthens; 22,900/22,750 put debit spread would fit; est. max loss ₹X/lot; invalid on a close above 23,030. *Badge: heuristic, shadow-graded.*
>
> **14 Oct 2026, 11:20 IST | **Divergence**: A fresh high, but futures OI is falling and ATM IV is rising: the move looks like short covering rather than new buying.
>
> **14 Oct 2026, 13:05 IST | **Impulsive Break**: −1.4σ in 12 minutes on 3x normal volume after the flip level gave way; estimated negative-gamma conditions may have amplified it. **Bite: TRIGGERED** — (the earlier 22,900 put-spread idea is now live; target zone 22,780, invalid above 22,960).
>
> **14 Oct 2026, 13:07 IST | **Possible Catalyst**: A headline about [X] was published 4 minutes before the move.
>
> **14 Oct 2026, 14:40 IST | **Bite Update**: Target zone reached at 22,785; the idea is resolved (+1.7R in simulated terms). **Bite: MANAGE** — (if you hold this, booking is an option here; the next put wall is 22,700).
>
> **14 Oct 2026, 15:10 IST | **Session Close**: Back inside the opening range; max pain at 22,950 pulled price toward the middle. **Bite: STAND-ASIDE** — for tomorrow's open: event risk overnight.
>
> **EOD scorecard:** 6 entries, 3 bites (1 triggered, target hit; 1 expired; 1 watch). Wall at 23,000 held all day.

---

## 6. Testing Strategy

| Layer | Tests |
|---|---|
| **Detectors (Phases 1–3)** | [`tests/test_commentary_phase1.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/tests/test_commentary_phase1.py), [`tests/test_commentary_phase2.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/tests/test_commentary_phase2.py), [`tests/test_commentary_phase3.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/tests/test_commentary_phase3.py): Fixture sessions with verified events, cooldown/hysteresis, and zero point-in-time leaks. |
| **Scoring & Story State (Phase 4)** | [`tests/test_commentary_phase4.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/tests/test_commentary_phase4.py): Merge logic, dynamic thresholding near budget cap, and thread resolution. |
| **Narrator & Verifier (Phase 5)** | [`tests/test_commentary_phase5.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/tests/test_commentary_phase5.py): Catches forbidden language, direction contradiction, and numeric hallucinations. |
| **Suggestion Bites & Gating (Phase 6)** | [`tests/test_commentary_phase6.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/tests/test_commentary_phase6.py): Verdict gating, defined-risk structure limits, and shadow-mode gating. |
| **Replay & Grading (Phase 7)** | [`tests/test_commentary_phase7.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/tests/test_commentary_phase7.py): Point-in-time replay, win rate, expectancy after costs ($0.08R$), and demotion logic. |
| **Persistence & UI (Phase 8)** | [`tests/test_commentary_phase8.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/tests/test_commentary_phase8.py): SQLite WAL persistence, pinned recommendations, and UI retrieval. |

---

## 7. Non-Functional Requirements

- **Determinism:** Same inputs and config reproduce identical storytelling output; template fallback eliminates token latency and cost.
- **Latency:** Real-time WebSocket push updates within < 1ms; commentary evaluated without blocking market feeds.
- **Resilience:** Graceful offline auto-switch when market closes, serving local authenticated snapshots.
- **Auditability:** Ground-truth fact verification drawer surfaces all underlying GEX levels for every session.
- **Safety Rails:** Kill switch, forbidden-phrase lint, advisory-only disclaimer, and shadow-mode gating.

---

## 8. Regulatory and Responsible-Use Policy

- **Advisory Only:** No automated order placement code anywhere in this module.
- **Personal Use:** Designed as a personal decision-support cockpit.
- **Shadow Mode Default:** Suggestion bites require 20 verified shadow sessions with proven edge before live promotion.

---

## 9. Resolutions to Architectural Questions

1. **Streaming Feed Architecture:** Dual-mode architecture established: Real-Time WebSocket Mode (`<1ms` push) during live hours; Offline Auto-Switch Mode serving authenticated local snapshots when closed.
2. **Pre-Market & Gap Auction:** Integrated with [`data/pre_post_market.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/pre_post_market.py). Discovers equilibrium at 09:00–09:08 IST, classifies 5 auction regimes, and tracks dynamic gap-fill progress.
3. **Trade Recommendations Integration:** 14 institutional candidate strategies (7 CE, 7 PE), signal latching (`_LATCH_MANAGER`) to eliminate card flickering, live telemetry (`inval_buffer`, `target_progress_pct`), and real-time pinned positions monitoring.
4. **Presentation Format:** Bulky card containers replaced with a unified, freeflowing storytelling stream (`HH:MM | Headline. Body. Bite: TYPE...`) and a single consolidated fact-verification drawer.
5. **Macro News Source:** Sourced from [`data/news.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/news.py) with published timestamps strictly matching $[-10\text{m}, +3\text{m}]$ lead/lag windows.
6. **Sector Breadth & Divergences:** Sourced from [`data/sectors.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/sectors.py) and Bank Nifty relative strength feeds.
7. **Shadow Mode Gating:** 20 sessions required with post-cost expectancy $> +0.25R$ before bite promotion. Auto-demotion active.
8. **Position-Aware Bites:** Pinned trade bookmarks ([`data/journal/recommendations_store.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/journal/recommendations_store.py)) feed real-time MANAGE alerts for target approaches, target hits, and invalidation breaches.
