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

## 1. Codebase Audit Checklist (first deliverable, before code)

| Area | Exists? | Location | Reuse plan |
|---|---|---|---|
| chain_snapshots, ATM IV, PCR, max pain | | | |
| Wall detection (OI walls) | | | |
| GEX / gamma exposure code | | | |
| Black-76 pricer / Greeks (Target Engine T5) | | | |
| Scenario scoring, targets, candidates (T2, T3, T6, T7) | | | |
| Backtest / PointInTimeView / replay harness (T9) | | | |
| Futures price + OI + depth feeds | | | |
| Bank Nifty / heavyweight / breadth quote feeds | | | |
| News ingestion (source, timestamps) | | | |
| LLM client / prompt templates | | | |
| Notification channels | | | |
| Positions API client | | | |
| Config, scheduler, DB, API, frontend | | | |

Rule: any "Yes" means the spec describes only the **delta**.

---

## 2. Architecture Overview

```
 Replayable inputs: chain snapshots, futures ticks, depth, spot candles,
 constituents/Bank Nifty, news items, event calendar, (optional) positions
                          |
                          v
   C1 Detectors  ---->  Event(ts, type, severity_raw, facts{}, quality_flags)
                          |
                          v
   C2 Significance scoring + confluence merge + cooldown + daily budget
                          |
                          v
   C3 Story State  (phase of day, running thesis, open threads, prior entries)
                          |
              +-----------+-----------+
              v                       v
   C4 Suggestion Bite Engine     C5 Narrator (LLM, facts-only)
   (reads Target Engine,                |
    gates, builds bites)                |
              +-----------+-----------+
                          v
   C6 Delivery: feed, push alerts, chart snapshots, EOD recap
                          |
   C7 Replay & QA harness: regenerates any day, grades events and bites,
                           feeds tuning and bite demotion back to C2/C4
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
              lifecycle{state, resolved_ts, outcome} }
Entry       { id, ts, event_ids[], text, bites[], chart_ref, verified: bool }
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

#### D1. Opening Gap
- **Inputs:** prior close, first print/open price, pre-open indications if available (GIFT Nifty if the source exists), walls, max pain, ATM IV.
- **Compute:** `gap_pts`, `gap_sigma = gap_pts / σ_1day`, gap direction, position relative to walls (gap into a wall? through a wall?), gap size percentile versus the trailing N days.
- **Event facts:** gap size (pts, %, σ), nearest wall and its OI share, whether the gap is above/below prior day's range.
- **Follow-up events (same thread):** *gap-fill progress* (retrace % of gap at 15/30/60 min), *gap holds* (still open after X minutes), *gap-and-go* (extends beyond opening range).
- **Edge cases:** missing pre-open data (fall back to open vs prior close); gaps on expiry day (flag higher gamma sensitivity); tiny gaps below `min_gap_sigma` produce no event.

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

#### D10. Scheduled-Event Markers
- From the event calendar: "RBI policy in 30 minutes," "US CPI tonight." These feed the story and the bite gating (Stand-Aside bites).

---

### C2. Significance Scoring, Merging, and Budget

**Score:** `significance = f(magnitude_sigma) × novelty × confluence × time_weight × quality`
- **Magnitude:** monotone, saturating function of σ-normalized size.
- **Novelty:** decays if a similar event fired recently (same type, same level).
- **Confluence:** events within a short window that share levels or direction are **merged** into one richer event (e.g., wall break + IV spike + volume burst + news match = one entry).
- **Time weight:** configurable multipliers (the opening, the expiry hour and the close are more significant).
- **Quality:** multiplied down when quality flags are set (stale data, thin chain, missing depth).

**Publication rules:**
1. Publish only if `significance >= publish_threshold`.
2. Respect `min_gap_minutes` unless severity is extreme (a "breaking" override).
3. Daily budget: when the cap is near, raise the threshold dynamically.
4. Every suppressed event is stored with its reason (for replay tuning).

**Output:** `Scored` events with `reasons[]` explaining the score, so threshold tuning is data-driven.

---

### C3. Story State Manager

Maintains the narrative continuity that separates a story from a log:
- **Phase of day** (pre-open, opening, morning, midday, afternoon, closing) and the phase-specific tone.
- **Running thesis:** a small state `{bias: bullish|bearish|range|unclear, confidence, since}` derived from the Target Engine scenario set and recent events. Changes in thesis are themselves story events ("the morning's range-bound thesis is breaking").
- **Open threads:** unresolved items (a gap not yet filled, a wall under test, a divergence unresolved). Later entries reference and **close** threads ("the 23,000 wall that held this morning has now given way").
- **Callbacks:** the narrator receives the last few entries and open threads, and must reference at least the relevant thread when one exists.
- **Resets:** state resets at session start; EOD recap reads the full thread history.

---

### C4. Suggestion Bite Engine

**Purpose:** Add short, applicable, conditional guidance to commentary entries so a trader knows what a development means for decisions, without issuing commands or guarantees.

#### C4.1 Bite types

| Type | Meaning | Example (illustrative numbers) |
|---|---|---|
| **WATCH** | A level or condition worth monitoring; no action implied | "Watch 23,000: a 5-min close above it would weaken the call-wall thesis." |
| **PREPARE** | A setup forming; here's what would trigger it and what structure would fit | "If price reclaims 22,900, bull scenario strengthens. A 22,950/23,100 call debit spread would fit; est. max loss ₹X per lot." |
| **ACT-IF** | A conditional, fully specified idea: trigger, structure, invalidation, target, risk | "If 5-min closes above 23,000: bull call spread 23,000/23,200 fits; invalid on a close below 22,930; target zone 23,150-23,200; R:R 1.8; max loss ₹Y per lot." |
| **MANAGE** | Guidance for an existing position (position-aware mode only) | "Your long 22,900 CE is up; price is at the 23,000 wall where resistance is likely. Partial profit-booking or trailing the stop to 22,960 are options." |
| **STAND-ASIDE** | Low edge or elevated risk; reducing size or waiting is valid | "RBI decision in 40 min, IV already elevated, edge is low. Waiting is a reasonable position." |
| **HEDGE** | Risk-reduction framing for short-gamma or large exposures | "Short-gamma structures into this event: defined-risk wings reduce tail risk." |

#### C4.2 What goes into every bite (required fields; a bite missing any is not published)

1. **Condition:** machine-checkable trigger (e.g., `close_5m > 23000`), so the system can detect when it fires.
2. **Action frame:** the structure that fits, taken from the Target Engine's ranked **TradeCandidate** (T6/T7), never invented by the LLM or by this module.
3. **Invalidation:** the level or condition that cancels the idea (T3 stop logic).
4. **Valid-until:** a time bound (end of horizon or session phase); bites expire.
5. **Risk numbers:** max loss per lot, R:R, breakevens at horizon (from T7), lot size from the instrument master, and `lots` for the configured risk budget if a profile exists.
6. **Scenario link:** which scenario this belongs to and its probability, with the **badge**.
7. **Badge:** `heuristic / uncalibrated` or `calibrated (N samples, Brier X)`, plus `mode: shadow|live`.
8. **Facts used:** the event and feature IDs that justify the bite (the "why").

#### C4.3 Gating rules (all must pass; each failure is logged)

- **Target Engine verdict:** ACT-IF and PREPARE bites require a candidate with verdict `favorable` (or `marginal` if config allows, with a "marginal" label). `avoid`/`no_edge` → only WATCH or STAND-ASIDE bites.
- **Quality flags:** stale snapshot, thin chain, wide spreads, missing depth, or an event window suppress actionable bites.
- **Calibration policy:** while the Target Engine is uncalibrated, actionable bites are either suppressed or capped at PREPARE with the "uncalibrated" badge (config: `allow_actionable_when_uncalibrated`, default `false`). Flip it only after the shadow-mode track record supports it.
- **Time gating:** no new ACT-IF in the first N minutes after open or the last M minutes before close; expiry-day bites carry a high-gamma caution and are limited to defined-risk structures.
- **Profile gating:** respect `allowed_structures` (e.g., `defined_risk_only`: no naked short options, ever).
- **Budget and cooldown:** `max_actionable_bites_per_day`, no duplicate bite for the same level within the cooldown, conflicting bites (bullish and bearish ACT-IF at once) are not issued together, and the story's thesis must be consistent with the bite direction or the bite is labelled "counter-thesis."
- **Liquidity:** all legs pass the Target Engine's liquidity filters.
- **Kill switch:** a single config flag disables all bites immediately.

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

### C6. Delivery

- **Live feed:** chronological entries; bites shown as distinct chips (type, badge, trigger, invalidation, risk) beneath the narrative text.
- **Push alerts (existing channel):** only for (a) breaking/extreme events, (b) **bite triggers**, (c) bite invalidations when the user has "followed" a bite. Respect quiet hours and rate limits.
- **Chart snapshots:** each entry may attach a small chart (price, walls, flip level, the event marker) rendered with the repo's charting stack.
- **Pre-market brief (optional):** prior day's recap, today's levels, events calendar, GIFT/global cues if available, initial scenarios and WATCH bites.
- **End-of-day recap:** the day as a story in ≤ 10 lines: gap and opening, key wall behavior, regime changes, the biggest move, the news linked, divergences that resolved, **plus a scorecard**: which walls held, which bites triggered and how they ended, and what the engine got wrong.
- **Export:** recap as markdown/PDF/message for sharing (subject to Section 9).

---

### C7. Replay and QA Harness

- **Replay:** rerun any stored day through C1-C5 at original pace or accelerated; LLM stubbed or cached for determinism; outputs diffed against golden files for regression testing.
- **Detector QA:** for each detector, a labelled set of historical sessions marking true events; report precision/recall per detector, and tune thresholds on a train period, verify on later periods (walk-forward, no random splits).
- **Narrative QA:** daily "spam score" (entries per day vs budget), duplicate-theme rate, thread-closure rate, verifier rejection rate, template-fallback rate.
- **Bite QA (the key part):** simulate bite outcomes using Target Engine conventions (entry at the next quote after trigger, conservative handling when target and stop are crossed in one bar, costs included). Report per bite type: trigger rate, win rate, avg R, expectancy after costs, max drawdown, performance split by regime (IV rank bucket, DTE bucket, event vs non-event, trend vs range). **Baselines:** random-direction bites, and "always WATCH" (no action). A bite type that doesn't beat baselines after costs is not promoted.
- **Wall-hold audit:** how often did walls named in commentary actually hold within X pts? Report honestly; this feeds the narrator's hedging strength.
- **Live monitoring:** live bite outcomes versus backtest expectancy with a decay alarm.

---

### C8. Persistence, API, UI

**Tables:**
```
events        id, ts, type, facts_json, magnitude_sigma, quality_flags, significance, published
suppressed    event_id, reason
entries       id, ts, event_ids, text, verified, template_fallback, llm_model
bites         id, entry_id, ts, type, condition_json, structure_json, levels_json,
              risk_json, valid_until, badge_json, mode, state, resolved_ts, outcome_json
bite_feedback bite_id, ts, feedback
story_state   ts, phase, thesis_json, open_threads_json
news_items    id, source, headline, url, published_ts, ingested_ts, relevance, linked_event_id
recaps        date, text, scorecard_json
```

**API (adapt to repo naming):**
- `GET /commentary/{symbol}/feed?date=` entries with bites
- `GET /commentary/{symbol}/stream` SSE/websocket for live entries
- `GET /commentary/{symbol}/bites?state=open|resolved`
- `GET /commentary/{symbol}/recap?date=`
- `GET /commentary/track-record?type=` bite-type track records
- `POST /commentary/bites/{id}/feedback`
- `GET /commentary/{symbol}/explain/{entry_id}` events, facts, formulas, gating decisions
- `POST /commentary/replay` (admin) run a stored day

**UI (reuse the repo's chart library and components):**
- Timeline/feed with entries, phase dividers, and chart thumbnails
- Bite chips with type, trigger, invalidation, risk, badge; open bites pinned in a side panel with live state
- Price ladder: spot, walls, gamma flip, max pain, bite triggers and invalidations
- "Why this?" drawer for every entry and bite (events, facts, gating decisions)
- Track-record panel and the EOD recap view
- Settings: profile (capital, risk %, allowed structures), alert preferences, bite types enabled, shadow/live indicator

---

## 5. Example Day (illustrative numbers, shows how entries and bites fit together)

> **09:15 | Gap-up open.** Nifty opened 0.7% (about 1.0σ) above the prior close, straight into the 23,000 call wall (24% of nearby call OI). *Bite: WATCH 23,000. A 5-min close above it would weaken the wall-defence read.*
>
> **09:48 | Wall holds so far.** Buyers reached 22,985 but couldn't close above 23,000. Call OI there rose 18% during the test, which looks like writers defending it. *Bite: PREPARE. If price slips back under 22,900 (estimated gamma flip), a range-to-bearish read strengthens; 22,900/22,750 put debit spread would fit; est. max loss ₹X/lot; invalid on a close above 23,030. Badge: heuristic, shadow-graded.*
>
> **11:20 | Divergence.** A fresh high, but futures OI is falling and ATM IV is rising: the move looks like short covering rather than new buying. 
>
> **13:05 | Impulsive break.** −1.4σ in 12 minutes on 3x normal volume after the flip level gave way; estimated negative-gamma conditions may have amplified it. *Bite: TRIGGERED (the earlier 22,900 put-spread idea is now live; target zone 22,780, invalid above 22,960).*
>
> **13:07 | Possible catalyst.** A headline about [X] was published 4 minutes before the move.
>
> **14:40 | Bite update.** Target zone reached at 22,785; the idea is resolved (+1.7R in simulated terms). *Bite: MANAGE (if you hold this, booking is an option here; the next put wall is 22,700).*
>
> **15:10 | Close.** Back inside the opening range; max pain at 22,950 pulled price toward the middle. *Bite: STAND-ASIDE for tomorrow's open: event risk overnight.*
>
> **EOD scorecard:** 6 entries, 3 bites (1 triggered, target hit; 1 expired; 1 watch). Wall at 23,000 held all day.

---

## 6. Testing Strategy

| Layer | Tests |
|---|---|
| Detectors | Fixture sessions with known events; cooldown/hysteresis behavior; point-in-time violation raises; stale/missing-data suppression |
| GEX/flip | Hand-computed GEX on a tiny chain; flip level equals the zero crossing; sign-convention switch works |
| News matching | Labelled set: precision/recall; wrong-timestamp items are never linked; lead/lag windows respected |
| Scoring/budget | Merge logic; budget cap raises the threshold; extreme override works; suppressed events logged with reasons |
| Story state | Threads open and close; callbacks present when relevant; reset at session start |
| Bite gating | Each gate has a test that a failing condition blocks the bite; unconditional or forbidden wording is rejected; no bite when no_edge or uncalibrated (default config) |
| Bite lifecycle | Trigger/invalidation/expiry on synthetic price paths; same-bar target and stop resolves conservatively |
| Verifier | An injected wrong number is caught; directional contradiction is caught; fallback template used; LLM-disabled run still produces entries |
| Replay | Same inputs plus same config reproduce identical output; golden-file regression |
| Bite QA | Synthetic data with a known edge is detected; with **no edge, reports none** |
| Integration | End-to-end on a stored day: events → entries → bites → recap |

---

## 7. Non-Functional Requirements

- **Determinism:** same stored inputs, config, and model version produce identical output (LLM cached/stubbed in replay).
- **Latency:** detection-to-publication within a few seconds for breaking events; the narrator call must not block detectors.
- **Explainability:** every entry and bite has a "why" trace (events, facts, thresholds, gating results).
- **Resilience:** missing feeds degrade gracefully (flag the detector as degraded, never fabricate); the LLM outage uses templates.
- **Auditability:** store config version, model version, and prompt version per entry; keep every published bite and its outcome permanently.
- **Safety rails:** kill switch, forbidden-phrase lint, footer notice, rate-limited alerts, the shadow-mode default.
- **Observability:** per-detector firing counts, suppression reasons, verifier rejection rate, LLM cost per day, bite track record, live-vs-backtest drift.

---

## 8. Known Limitations (surface these in the product)

1. **GEX is estimated.** Dealer positioning is unobservable; conventions are assumptions. Treat the flip level as approximate.
2. **"Institutional" activity is inferred.** Footprints are labelled as such; identity is only confirmable end-of-day from participant data.
3. **News causality is not provable.** Matching is temporal; wording stays hedged.
4. **Bites inherit the Target Engine's uncertainty.** Until calibrated, they are heuristics, and the badge says so.
5. **Walls fail.** Especially on trend days, expiry days, and event days; the commentary's hedging should scale with the audited wall-hold rate.
6. **Narration can over-explain noise.** The budget, thresholds, and spam score exist to counter this; tune with replay.
7. **Costs and slippage matter.** Short-dated option edges can disappear after costs; bite risk numbers include them.
8. **Data dependencies.** Constituent/breadth data, GIFT Nifty, and timestamped news come from outside Dhan; features depending on them degrade when absent.

---

## 9. Regulatory and Responsible-Use Considerations

- **Personal use:** a tool that guides your own trading decisions is one thing.
- **Sharing with others:** publishing specific, actionable trade ideas (levels, structures, entries/exits) to other people, especially publicly or for a fee, can fall under SEBI's research analyst and investment adviser regulations in India, and SEBI has also tightened rules on trade-related content on social platforms. If the output will ever leave your own screen (channel, subscribers, clients), consult a SEBI-registered professional or a securities lawyer **before** distributing, and keep the shadow-mode, disclaimer, and audit trail features on.
- **Design consequence:** keep a config switch separating `personal` and `shared` modes; in `shared` mode, default to commentary and WATCH/STAND-ASIDE bites only, until compliance is settled.
- **Never** auto-trade from bites in this module.

---

## 10. Open Questions to Resolve Early

1. News source: which feed(s), and do they provide reliable `published_ts`?
2. Is Bank Nifty / heavyweight / breadth data accessible for the divergence detectors?
3. Is pre-open/GIFT Nifty data available, or should the gap detector run on open vs prior close only?
4. How many logged sessions exist now, and how many shadow sessions will be required before promoting each bite type?
5. Personal use only, or will the feed be shared? This sets the default bite policy (Section 9).
6. Preferred channel(s) for alerts and the EOD recap.
7. Buyer-only, defined-risk-only, or open to short-premium structures? This drives bite structure gating.
8. Should position-aware MANAGE bites be built in v1, or deferred?

---

*Hand Sections 0-4 to the AI editor as the working brief. First required output: the Section 1 audit table. Hard gate: bites remain in shadow mode until Phase 7 grading supports promotion.*
