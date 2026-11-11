# Decisive GO — Product Spec & Backlog

**Status:** Phases 0–3 complete; completion pass (2026-11-10) closed A2 open-long Greeks, F1 15m digest + collapsed raw tapes, H5 TOD/DOW/lunch scorecard UI, B6 10Δ/25Δ smile on structure GO, F4 operator-language verified. Writer SPAN remains deferred by design. Additive firing lane; no auto-execution.  
**Date:** 2026-11-10  
**Audience:** Cubera GEX Cockpit product + engineering  
**Goal:** Turn the cockpit into a **high-confidence trade-firing system** for medium–high risk FNO traders who want a clear **GO**, exact contracts, and hard invalidations — not a balanced essay.

**Non-goals:** Auto-execution. Dealer-sign forecasts. “Walls will be defended” language. Second GEX sign model. Full cockpit redesign (see §3.3).

**Requirement sources audited (2026-11-10):** original three-persona FNO brief; Gemini trader analysis; decisive/high-risk reframing; scope clarification (additive firing lane); this completeness audit. Traceability in **§14**.

---

## 1. Product thesis

The trader opens the app to answer one question in under 10 seconds:

> **Do I take a trade right now — and if yes, which exact contract, size, entry rule, stop, and targets?**

And, before or after the click, a second question that must never be opaque:

> **Why this GO — which checkable facts support it, and how will we know if that class of decision is working?**

### 1.1 What “decisive” means (definition)

A recommendation is **decisive** only when all of the following are true:

| # | Criterion | Trader can verify |
|---|-----------|-------------------|
| D1 | **Binary posture** — FIRE / SCOUT / HOLD, not a paragraph of maybe | Posture strip |
| D2 | **Exact contract(s)** — action, type, strike, expiry, LTP/bid-ask | GO card legs |
| D3 | **Exact risk** — max loss ₹ (and margin est. for writers); size tier | Risk grid |
| D4 | **Exact invalidation + targets** — spot/structure rule; T1/T2 or BE | Targets / inval |
| D5 | **Explicit enter rule** — NOW / VWAP reclaim / wall hold / pre-bell plan | Enter badge |
| D6 | **Evidence pack** — each score factor with value, threshold, pass/fail, source link | Why panel |
| D7 | **Freshness** — OI ts, decision `expires_at`, stale → HOLD with reason | Context line |
| D8 | **One primary CTA** — no competing Sentinel vs Trade Ideas printers | Home / GO Desk |

If any of D1–D8 fail, the system may show SCOUT or HOLD — it must **not** present a soft “interesting setup” as FIRE.

**Personality:** Aggressive-capable. Prefer lighting green when confluence exists. Use size tiers and invalidations for risk — not veto culture as the default brand. HOLD is a state, not the brand.

**Honesty constraints (unchanged):**
- GEX is a positioning map. Walls / magnet / flip are concentrations and balance points — not dealer defense or pin forecasts.
- POSITIVE / NEGATIVE = call-heavy / put-heavy book. Not fade/momentum commands.
- Absolute |GEX| bucket = vol context (calm / wild / mid).
- Short strikes (when any) sit **beyond** gamma concentrations, never on the magnet.
- OI timestamp and lot size always visible on every GO card.
- User confirms at broker. App stages; never sends orders.

---

## 2. Personas & modes

A persistent **Trading Mode** selector filters UI and reinterpret the same underlying signal into a persona-specific GO template.

| Mode ID | Persona | Primary instrument | Success metric |
|---------|---------|--------------------|----------------|
| `directional` | Naked CE/PE buyer | Singles (ATM / OTM by delta pref) | Fired trades with MFE capture; trail discipline |
| `structure` | Straddle / strangle / hedge trader | Multi-leg defined-risk | Credit/debit clarity; adjustment speed |
| `writer` | Naked / near-naked premium seller | Short OTM ± hedge wing | IV−RV edge; margin-aware size; kill/hedge latency |

Mode persists per user (localStorage + optional server preference). Changing mode **does not** change GEX math; it changes which GO template is primary and which panels are emphasized.

---

## 3. Information architecture

### 3.1 Always-on chrome (all modes)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Cubera  ·  NIFTY | BN | SX   ·  Mode: [Directional|Structure|Writer]         │
│ OI 10:42 IST · lot 65 · short_call_short_put · expiry Tue                    │
├──────────────────────────────────────────────────────────────────────────────┤
│ POSTURE STRIP                                                                │
│  ● FIRE   CONTINUATION_BULL · score 82 · CVD buyer · above VWAP              │
│  Spot 25420 · PW 25200 · Flip 25350 · CW 25600 · |GEX| CALM                  │
└──────────────────────────────────────────────────────────────────────────────┘
│ PRIMARY GO CARD (mode-specific)                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ Secondary: Alert tape detail · Sentinel chart (collapsed default)            │
│ Tertiary: Target Engine / Analytics / Journal (existing tabs, mode-filtered) │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Rule:** The primary GO card is impossible to miss. Commentary, news, and extra tabs sit below or behind mode filters. Signal abundance is solved by narrowing what can light green — not by adding more caution copy.

### 3.2 Page map

| Page / surface | Job | Mode visibility |
|----------------|-----|-----------------|
| **Cockpit Home** | Posture strip + primary GO + mini ladder | All |
| **GO Desk** (enhances Trade Recommendations) | Ranked FIRE/SCOUT list, pins, lifecycle | All (templated) |
| **Structure Desk** | Live multi-leg card, BE, net Greeks, adjust | Structure primary; others secondary |
| **Writer Risk** | Inventory, margin proxy, IV−RV, kill/hedge | Writer primary |
| **Sentinel Tape** | CVD / signals (feeds GO; not competing recs) | All |
| **Target Engine** | Reprice / what-if (feeds GO targets) | All |
| **Journal / Scorecard** | strategy_type taxonomy + persona × regime | All |
| **Ops** | Feeds, calendar, tokens | All |

Existing 11-tab taxonomy is retained but **re-weighted**: Home + GO Desk become the firing lane; other tabs become support.

### 3.3 Redesign scope (explicit)

**This is not a full cockpit redesign.** It is a **firing-lane layer** on the existing terminal.

| Change type | What happens |
|-------------|--------------|
| **ADD** | Mode selector (header/sidebar). Posture strip (FIRE/SCOUT/HOLD) on Cockpit Home. Primary GO card above existing Home content. Optional Writer Risk / Structure Manage panels when that mode is active. |
| **CHANGE** | Trade Recommendations becomes **GO Desk**: one ranked feed (merge Trade Ideas + Sentinel recs). Home “Active Recommendations” becomes the single primary GO + scout list. Journal gains `strategy_type`. |
| **KEEP** | Sidebar, index/expiry controls, GEX ladder, alert tape (feeds strip), Sentinel Tape charts, Target Engine, Options Analytics, Market Intel, Commentary, Ops, Reports, Admin, Scorecard shell, TradingView chart. Visual language (surfaces, tabs, tokens) stays Cubera. |

Phase 0 can ship as **additive chrome + one Home card + GO Desk merge** without touching most tabs. Phases 1–2 deepen cards; they still do not require rebuilding the shell.

---

## 4. Unified GO contract (data)

### 4.1 `GoDecision` (single source of truth)

Produced by a new aggregator (suggested module: `analytics/go_desk.py`) that merges:
- Sentinel signals / recommendations
- Trade Ideas (≥65 / ≥75 tiers)
- `structures/suggest.py` candidates
- Target Engine repriced levels
- Blast risk adjust (`app/blast_risk.py`)
- IV−RV / skew / catalyst (writer & structure)
- Directional bias, breadth, CVD regime

```text
GoDecision
  id: uuid
  state: FIRE | SCOUT | HOLD
  mode: directional | structure | writer
  symbol: NIFTY | BANKNIFTY | SENSEX
  score: 0..100
  score_weights: { factor: weight, ... }   # exact blend used this emit
  strategy_key: MOMENTUM | STRUCTURE | STRIKE_BREAK | ... | IRON_CONDOR | LONG_STRANGLE | NAKED_SHORT_OTM | ...
  thesis: one sentence (map language, no dealer defense)
  evidence: [EvidenceItem...]  # REQUIRED — trader double-check pack (§4.4)
  soft_flags: [string]         # size-down reasons (corridor, lunch, coil…)
  hard_blocks: [string]        # if any → state must be HOLD (or forced defined-risk)
  invalidation: { rule: string, spot_level?: float, structure?: string }
  enter_rule: NOW_CLOSE | VWAP_RECLAIM | WALL_HOLD | OPEN_AUCTION_PLAN
  size_tier: FULL | HALF | SCOUT
  contracts: [GoLeg...]
  risk: { max_loss_inr, margin_est_inr?, credit_or_debit, breakevens?, spread_pct_mid? }
  targets: { t1, t2, trail_rule?, touch_p?, ev? }
  greeks_live?: { delta, gamma, theta, vega }  # per leg + net when multi
  context: { oi_ts, lot_size, call_wall, put_wall, flip, magnet, abs_gex_bucket, blast_tag? }
  sources: { sentinel_id?, trade_idea_id?, structure_name?, alert_tape_headline? }
  deep_links: { sentinel?: path, target_engine?: path, analytics?: path, ladder?: path }
  expires_at: ISO datetime   # anti-stale latch
  emitted_at: ISO datetime
```

```text
EvidenceItem           # every FIRE/SCOUT must list contributing factors
  factor: string       # e.g. sentinel_score, cvd_vwap, abs_gex, iv_rv, breadth
  label: string        # human: "Sentinel CONTINUATION_BULL"
  value: number|string # observed
  threshold: string    # e.g. "≥ 75"
  pass: bool
  weight: number       # contribution to unified score
  source_ref: string   # module / panel id trader can open
  note?: string        # map language only
```

```text
GoLeg
  action: BUY | SELL
  opt_type: CE | PE
  strike: int
  expiry: date
  delta_target?: float
  ltp?: float
  bid?: float
  ask?: float
```

### 4.4 Evidence pack & Why panel (double-check contract)

Every FIRE and SCOUT card exposes a **Why** expand (default collapsed on mini; open on full):

1. Ranked `evidence[]` with pass/fail and numeric values (not slogans).
2. Soft flags and hard blocks listed plainly.
3. `score_weights` used for this emit (so calibration is auditable).
4. One-click `deep_links` into Sentinel / Target Engine / ladder / skew — same numbers the engine read.
5. HOLD shows **why not** (failed evidence + hard blocks) with equal honesty.

**Acceptance:** A trader can recompute the GO posture from the Why panel + linked panels without trusting the score alone. Narrative thesis is summary only; evidence is authoritative.

### 4.2 State machine rules (decisive defaults)

| Condition | Result |
|-----------|--------|
| Unified score ≥ 75, no hard conflict, chain authentic, corridor OK for mode | **FIRE** · size FULL (or HALF if soft risk flag) |
| Score 65–74 | **SCOUT** · size HALF or SCOUT |
| Score &lt; 65, mid |GEX| with no structure edge, missing IV/spot, dual-engine conflict unresolved | **HOLD** |
| Headline / catalyst HIGH_SHOCK | Writers: HOLD or hedge-only. Directional: size HALF max unless STRIKE_BREAK FIRE with tape. |
| Blast ACTIVE (expiry) | Writers: no new shorts near magnet; show hedge. Directional: FIRE only on break template with SCOUT/HALF. |
| Book vs tape conflict | Prefer tape for directional FIRE; prefer |GEX| for structure/writer; if both conflict → HOLD |

**Soft flags (size down, do not block FIRE):** lunch chop, inside corridor for directional, mild blast coil, breadth mixed.

**Hard blocks (HOLD or force defined-risk):** placeholder chain, stale OI beyond config, spot missing, writer HIGH_SHOCK, short strike on magnet.

### 4.3 Persona templates (same signal → different GO)

| Upstream signal | Directional template | Structure template | Writer template |
|-----------------|----------------------|--------------------|-----------------|
| CONTINUATION_BULL ≥75 | BUY OTM/ATM CE by delta pref | Debit call spread / long call vertical | Flat or buy-back short calls if exposed |
| ABSORPTION at put wall | BUY CE on hold (SCOUT→FIRE if CVD confirms) | Bull put credit / tighten put wing | Hold short puts; alert if wall shifts in |
| CALM + inside corridor | SCOUT directional only | **FIRE** iron condor / credit | **FIRE** short beyond walls if IV−RV &gt; 0 |
| WILD + low \|GEX\| | FIRE long premium / break | **FIRE** long strangle | HOLD new shorts |
| STRIKE_BREAK | FIRE OTM in break direction | Debit spread toward break | Hedge / flatten threatened short |

---

## 5. Rituals & playbooks (operator contract)

The product encodes these as **mode checklists + timed banners**, not as separate docs the trader must memorize. Each step maps to a panel already in the cockpit or to the GO card.

### 5.1 Shared daily skeleton

| Window (IST) | Ritual step | System support |
|--------------|-------------|----------------|
| 06:30–09:00 | Prior book: walls, flip, \|GEX\|, FII lean, OI ts | Offline snapshot / morning briefing |
| 09:07–09:15 | Pre-bell: cash open vs walls; straddle shock; stage candidates | Pre-market banner + Target Engine what-if |
| 09:15–10:30 | First decision block: posture → GO → stage | Posture strip + primary GO |
| 11:00–13:30 | Prefer manage-only / SCOUT; lunch soft flag | Soft size-down, not hard lockout |
| 13:00–15:10 | Expiry/blast location risk | Blast tag on GO; writer kill rail |
| 15:30–16:00 | Journal taken/skipped; efficiency glance | Stage outcomes + GO Efficiency (§7) |

### 5.2 Directional playbook

1. Mode = Directional. Prefer FIRE with enter rule clear.
2. Choose δ; confirm premium vs Target Engine σₕ (rich → prefer vertical alt or smaller size).
3. Soft corridor → HALF/SCOUT unless STRIKE_BREAK.
4. Stage; trail after configured +%; exit on spot inval (5m close) or T1/T2.
5. EOD: FIRE taken win-rate, MFE capture %, skipped-FIRE opportunity cost.

### 5.3 Structure playbook

1. Abs \|GEX\| first: calm → short-vol defined risk; wild → long strangle/debit; mid → HOLD.
2. Shorts beyond walls; never magnet. Confirm credit, BE, max loss ₹ before stage.
3. Sentinel absorption/exhaustion supports range; continuation/break → adjust or exit.
4. Wall shift inside short → Adjust CTA (roll / wing / close side).
5. EOD: structure class × regime P&L; adjustment count; % max-profit exits.

### 5.4 Writer playbook

1. CALM + IV−RV &gt; 0 + beyond walls + catalyst clear → FIRE write.
2. Size from margin est. + worst-case move; stage kill wings ready.
3. Kill on wall-inside, blast active, CVD break through wall, VIX shock.
4. ≤5–7 DTE → roll hint. Overnight: gap-risk line on Writer Risk.
5. EOD: theta collected vs MAE; IV−RV realized; kill latency.

---

## 6. UI specifications

### 6.1 Posture strip

- Full-width bar under header.
- Left: state pill (`FIRE` green / `SCOUT` amber / `HOLD` slate).
- Center: strategy key · score · 1-line thesis.
- Right: spot vs PW/Flip/CW · \|GEX\| bucket · OI age.
- Click expands Outcome / Drivers / Risk (current alert-tape payload) without leaving Home.

### 6.2 Directional GO card

**Title:** `FIRE · CONTINUATION_BULL · 82`  
**Contract chooser:** segmented `δ0.50 | δ0.35 | δ0.25` (user pref default).  
**Grid:** Entry LTP · T1 · T2 · Spot inval · R:R · Touch p · EV  
**Enter rule:** badge `ENTER NOW` or `ENTER ON VWAP RECLAIM`  
**Size:** FULL / HALF / SCOUT with ₹ max loss  
**Why:** evidence table + deep links (§4.4)  
**Actions:** `Stage in journal` · `Skip FIRE` · `Pin` · `Copy strikes`  
**Secondary:** vertical spread alt (one tap expand) — optional, not the primary CTA.

### 6.3 Structure GO card

**Title:** `FIRE · IRON CONDOR · CALM`  
**Legs table:** SELL/BUY · type · strike · LTP · delta  
**Economics:** Net credit/debit · Upper/Lower BE · Max loss ₹ · Max profit ₹  
**Net Greeks:** Δ Γ Θ V  
**Invalidation:** corridor break / flip cross / blast  
**Why:** abs \|GEX\| fit, corridor geometry, IV context, blast safety  
**Actions:** `Stage structure` · `Show payoff` · `Adjust…` · `Skip FIRE`  
**Live MTM** once staged (multi-leg combined).

### 6.4 Writer GO card

**Title:** `FIRE · WRITE OTM · IV−RV +3.3%`  
**Shorts:** strike(s), credit, delta, distance-to-wall (pts / σ)  
**Margin est.** (proxy SPAN-style) · portfolio add  
**Why:** IV, RV windows, distance, catalyst, tape-at-wall  
**Kill triggers:** list with one-tap `Buy wing` / `Roll` / `Flatten`  
**Blast / wall-shift** callouts action-shaped: “DO THIS NOW — buy 25700 CE hedge ≈ ₹15”

### 6.5 GO Desk list

Two tiers retained:
- Core FIRE (≥75)
- Scout (65–74)

No separate competing “Sentinel recs” vs “Trade Ideas” columns. One ranked list; `sources` + Why on expand. Mini/full toggle kept. Pin + lifecycle audit kept.

### 6.6 Notifications (FIRE only by default)

Payload: symbol, state, strategy, contract summary, entry, inval, T1, top 3 evidence passes, deep link to GO card.  
Channels: webhook / Telegram (config). SCOUT optional; HOLD never pushes.

---

## 7. Gaps → requirements (persona inventory)

Gaps from the trader analyses, mapped so none are orphaned. Severity is trader impact; phase is build order.

### 7.1 Directional (naked buyer)

| Gap ID | Missing capability | Spec requirement | Phase |
|--------|-------------------|------------------|-------|
| A1 | OTM / delta strike choice | B1 | 1 |
| A2 | Live Greeks on open long | G3 — shipped (`/api/go/open` + open panel) | 2 ✓ |
| A3 | Trailing stop | B5 | 1 |
| A4 | Multi-TF confluence | Phase 3 score boost (not extra UI homework) | 3 |
| A5 | Strike OI/volume surge alert | Evidence factor + Analytics deep link | 2 |
| A6 | Explicit enter timing | B2 + `enter_rule` | 0 |
| A7 | Push on FIRE | B6 | 1 |
| A8 | Time-of-day / DOW efficiency | H5 — shipped (by_hour / by_dow / lunch cohort) | 2 ✓ |

### 7.2 Structure (strangle / hedge)

| Gap ID | Missing capability | Spec requirement | Phase |
|--------|-------------------|------------------|-------|
| B1 | Combined multi-leg MTM | C2 | 2 |
| B2 | Net Greeks | C3 | 2 |
| B3 | Adjustment recommendations | C5 | 2 |
| B4 | Payoff diagram | C4 | 2 |
| B5 | Position what-if | Phase 3 scenario | 3 |
| B6 | IV smile (beyond 25Δ) | `smile_by_delta` 10Δ/25Δ/ATM on structure GO | 3 ✓ |
| B7 | Calendar / diagonal | Phase 3 | 3 |
| B8 | Breakevens on card | C1 | 1 |
| B9 | % max-profit exit hint | C6 | 2 |

### 7.3 Writer (naked / near-naked)

| Gap ID | Missing capability | Spec requirement | Phase |
|--------|-------------------|------------------|-------|
| C1 | Margin calculator | D3 | 1 |
| C2 | IV vs RV tracker | D1 | 1 |
| C3 | Portfolio risk view | Phase 3 Writer Risk | 3 |
| C4 | Tail / gap risk alert | D5 + overnight gap line | 2 |
| C5 | Hedge recommendation | D4 | 2 |
| C6 | Premium decay tracking | Journal MTM + % credit decay | 2 |
| C7 | Overnight gap assessment | Writer Risk post-close | 2 |
| C8 | Position-aware commentary | D5 + commentary hook | 2 |
| C9 | DTE roll suggestion | D6 | 2 |

---

## 8. Complexity & inefficiency → requirements

Problems called out in chat / prior analyses, turned into build obligations:

| ID | Inefficiency | Requirement | Acceptance |
|----|--------------|-------------|------------|
| X1 | Dual recommendation engines (Trade Ideas vs Sentinel) | A3, E4, GO Desk merge | **Deferred:** keep Higher/Secondary Trade Ideas + Sentinel OF recs visible beside GO Desk until GO efficiency is proven; then retire to one ranked feed with provenance in `sources` |
| X2 | No persona mode → cognitive overload | A1, F2 | Mode filters primary CTA within 1 refresh |
| X3 | Commentary / signal flood | F1 — shipped (`/api/go/digest` + Show raw tapes) |
| X4 | Journal strategy as free text | E1 | Enum queryable in scorecard |
| X5 | Eleven equal tabs hide the GO | §3 re-weight; Home firing lane | FIRE visible without tab hop |
| X6 | Docs claim dealer defense vs engine honesty | F4 — verified; guides already deny dealer-defense |
| X7 | Opaque scores (cannot double-check) | §4.4, E5 | Why panel with evidence + deep links |
| X8 | Wins tracked for Sentinel bars, not staged GOs | Epic H | Every staged GoDecision gets forward MFE/MAE ledger |

---

## 9. Functional requirements by epic

### Epic A — Posture & Mode (P0)

| ID | Requirement | Acceptance |
|----|-------------|------------|
| A1 | Mode selector in header | Persists; filters GO template within 1 refresh |
| A2 | Posture strip FIRE/SCOUT/HOLD | State matches `GoDecision`; updates with feed |
| A3 | Single ranked GO list | No dual contradictory primary CTAs on Home |
| A4 | OI/lot/convention on every GO card | Always visible |
| A5 | Decisive checklist D1–D8 enforced in aggregator | FIRE emit rejected if contract/risk/evidence/freshness missing |

### Epic B — Directional FIRE (P0–P1)

| ID | Requirement | Acceptance |
|----|-------------|------------|
| B1 | Delta preference OTM/ATM | User setting; GO card offers 3 strikes |
| B2 | Enter rule explicit | One of the enum values shown as CTA language |
| B3 | Target Engine SL/T1/T2 on card | Matches repricer; missing → HOLD or hide targets with reason |
| B4 | Soft corridor size tier | Inside corridor → HALF/SCOUT unless STRIKE_BREAK FIRE |
| B5 | Trailing rule | After +30% premium (configurable), trail stop updates on 5m close |
| B6 | Push on FIRE | Optional webhook fires within 2s of decision |

### Epic C — Structure FIRE (P1)

| ID | Requirement | Acceptance |
|----|-------------|------------|
| C1 | Live structure card from `suggest_structures` | Legs + credit + BE + max loss |
| C2 | Combined multi-leg MTM in journal/cockpit | One net ₹ number |
| C3 | Net Greeks | Aggregated on card |
| C4 | Payoff sketch | Expiry curve + optional T−2d BS curve |
| C5 | Adjustment suggestions | Wall shift inside short or delta breach → Roll / Add wing / Close side with ₹ impact |
| C6 | Auto-close hint at % max profit | 50/60/75% configurable alert (user confirms close) |

### Epic D — Writer FIRE (P1–P2)

| ID | Requirement | Acceptance |
|----|-------------|------------|
| D1 | IV−RV spread on Writer GO | 5d/10d/20d RV vs ATM IV; positive → seller edge label |
| D2 | Shorts beyond walls | Magnet/wall nudge rules from `structures/suggest.py` enforced |
| D3 | Margin estimate | Per position + portfolio sum (documented as estimate) |
| D4 | Kill/hedge actions | One-tap stage hedge/roll/flatten journal entries |
| D5 | Position-aware alerts | Wall shift / blast / CVD break references open shorts |
| D6 | DTE roll hint | At ≤5–7 DTE, suggest roll with credit/debit |

### Epic E — Explainability & trust (P0–P1)

| ID | Requirement | Acceptance |
|----|-------------|------------|
| E1 | `strategy_type` enum on journal | NAKED_LONG, NAKED_SHORT, VERTICAL, IRON_CONDOR, STRADDLE, STRANGLE, CUSTOM |
| E2 | Scorecard by mode × state × strategy | Win rate, PF, avg MFE/MAE for FIRE vs SCOUT |
| E3 | Skipped FIRE log | User can mark Skip; stored with decision id + timestamp |
| E4 | Unify Sentinel + Trade Ideas scoring | One score; weights in `score_weights` on every emit |
| E5 | Evidence / Why panel on every FIRE/SCOUT/HOLD | `evidence[]` complete; deep links open source panels with same symbols/ts |
| E6 | Immutable decision snapshot at stage | Journal stores full GoDecision JSON (point-in-time); never rewritten by later book |

### Epic F — Anti-hesitation / complexity (P0–P2)

| ID | Requirement | Acceptance |
|----|-------------|------------|
| F1 | Signal digest under strip | 15m rollup; raw flood collapsed |
| F2 | Mode-filtered primary CTAs | Writer does not lead with momentum CE cards |
| F3 | Copy strikes / stage journal ≤2 clicks | Timed UX acceptance |
| F4 | Docs language scrub | Remove dealers-defend-walls claims from operator guides; research stays historical |
| F5 | Ritual checklist chips (optional) | Pre-bell / first-block / EOD chips mark done; do not block FIRE |

### Epic G — Manage / Greeks (P2)

| ID | Requirement | Acceptance |
|----|-------------|------------|
| G1 | Net Greeks multi-leg | On structure/writer cards + open positions |
| G2 | Multi-leg combined MTM | Single ₹ + % of max |
| G3 | Per-leg live Greeks for naked longs | Directional open positions |
| G4 | Action-shaped adjusts | Roll / wing / flatten stage with ₹ impact preview |

### Epic H — Decision success & efficiency measurement (P1–P2)

Extend existing Signal Efficiency patterns from bar-signals to **staged GoDecisions**.

| ID | Requirement | Acceptance |
|----|-------------|------------|
| H1 | Forward ledger per staged GO | MFE/MAE/hit/stop at +1m…+30m and at exit; option premium and spot both logged |
| H2 | Taken vs Skipped vs Expired FIRE | Counts + opportunity gap (skipped that later hit T1) |
| H3 | Efficiency by mode × strategy_key × state × size_tier × enter_rule | Filterable in Scorecard / Efficiency tab |
| H4 | Process metrics | Median seconds emit→stage; % enter-rule followed; % trail armed; kill latency (writer) |
| H5 | Time-of-day / DOW / lunch slices | Same metrics bucketed; lunch soft-flag cohort visible |
| H6 | Calibration | Predicted EV / touch_p vs realized; abs GEX bucket vs outcome |
| H7 | DSS health | Dual-CTA incidents (=0 after merge); stale FIRE blocked count; HOLD reason histogram |
| H8 | Cost-aware P&L | Journal net of brokerage/slippage config (existing journal costs) |

**Success KPIs (product):**

| KPI | Target direction |
|-----|------------------|
| Time-to-stage on FIRE | ↓ (median under 30s once trained) |
| FIRE taken expectancy (net ₹) | ↑ after costs |
| Edge ratio (avg MFE / avg MAE) on taken FIRE | ≥ 1.5 preferred |
| Skipped-FIRE that would have hit T1 | Monitor; high → hesitation or score distrust |
| False FIRE rate (should have hard-blocked) | → 0 |
| Writer kill latency after breach | ↓ |
| % emits with complete evidence pack | = 100% |

---

## 10. Backlog (build order)

### Phase 0 — Firing lane (1–2 weeks)

1. `GoDecision` schema + `evidence[]` + `analytics/go_desk.py` aggregator.
2. Header Mode selector + persistence.
3. Posture strip on Cockpit Home.
4. Replace dual primary rec panels with one ranked GO Desk feed.
5. Directional GO card v1 (ATM + enter rule + Target Engine levels + size tier + Why panel).
6. Journal `strategy_type` + immutable GoDecision snapshot on stage / skip.
7. Decisive checklist gate (A5) in aggregator.

**Exit criteria:** Directional trader can FIRE from Home without other tabs **and** expand Why to verify each factor.

### Phase 1 — Aggression tools + measurement spine (2–3 weeks)

1. Delta / OTM strike chooser on directional GO.
2. Trailing stop lifecycle on pinned/staged ideas.
3. Structure GO card: credit, breakevens, max loss, stage multi-leg.
4. Writer GO card: IV−RV, beyond-wall shorts, margin estimate v1.
5. FIRE webhook/Telegram (include top evidence).
6. Soft corridor → size tier (not hard block).
7. GO forward ledger H1–H3 hooked to Efficiency / Scorecard.

**Exit criteria:** All three modes stage concrete GOs with ₹ economics; taken FIRE shows MFE/MAE within session.

### Phase 2 — Manage the trade + process KPIs (3–4 weeks)

1. Net Greeks + multi-leg MTM.
2. Adjustment recommendations (structure + writer).
3. Payoff diagram.
4. Position-aware commentary hooks.
5. Trail / % max-profit alerts.
6. Scorecard persona slices + H4–H8 process/DSS health.
7. Ritual checklist chips (F5).

**Exit criteria:** Manage via action buttons; EOD shows whether FIRE classes paid and whether process lag hurt.

### Phase 3 — Portfolio & polish (4+ weeks)

1. Writer portfolio risk dashboard (multi-index).
2. Scenario what-if on open positions (Target Engine backend).
3. Calendar/diagonal suggestions when term structure supports.
4. Multi-timeframe confluence as **score boost**, not extra homework UI.
5. Cockpit backtest slice for last N sessions of FIRE templates.
6. Strike-level unusual OI/volume as evidence factor.

---

## 11. Scoring sketch (unified)


Keep existing piece scores; normalize to 0–100 then blend:

```text
directional_score =
  0.40 * sentinel_score
+ 0.25 * trade_idea_score
+ 0.15 * cvd_vwap_alignment
+ 0.10 * breadth_alignment
+ 0.10 * target_engine_confluence
− penalties(conflict, stale_oi, placeholder_chain)

structure_score =
  0.45 * abs_gex_fit (calm→short-vol, wild→long-vol)
+ 0.20 * corridor_geometry_clean
+ 0.15 * iv_context
+ 0.10 * sentinel_range_or_break_fit
+ 0.10 * blast_safety
− penalties(...)

writer_score =
  0.35 * abs_gex_calm
+ 0.25 * max(0, iv_minus_rv_edge)
+ 0.15 * distance_beyond_walls
+ 0.15 * catalyst_clear
+ 0.10 * tape_not_breaking_walls
− penalties(blast_active_near_short, wall_shift_inside, high_shock)
```

Exact weights are tunable behind config; ship with logged weight set on every decision.  
Every emit persists `score_weights` + `evidence[]` so Efficiency can attribute outcomes to factors later.

---

## 12. API / endpoint sketch

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/go/decision?symbol=&mode=` | Current `GoDecision` (+ evidence) |
| GET | `/api/go/feed?symbol=&mode=` | Ranked FIRE/SCOUT list |
| POST | `/api/go/prefs` | mode, delta_pref, trail_pct, webhook_url |
| POST | `/api/go/stage` | Journal entry + immutable snapshot |
| POST | `/api/go/skip` | Skip FIRE with decision id |
| POST | `/api/go/action` | trail / adjust / hedge / flatten staging |
| GET | `/api/go/writer/risk?symbol=` | inventory + margin + IV−RV |
| GET | `/api/go/structure/payoff?id=` | curve points for staged/suggested structure |
| GET | `/api/go/efficiency?symbol=&from=&to=` | H1–H8 aggregates for Scorecard / Efficiency tab |

Reuse existing chain, sentinel, target engine, blast, journal, and sentinel efficiency stores. Prefer façade over rewrite.

---

## 13. Telemetry & safety

Log every emitted decision: id, state, score, `score_weights`, full `evidence[]`, sources, OI ts, lot, mode, size_tier, blast_tag, soft_flags, hard_blocks.  
Fail loud → HOLD with reason string (never silent stale FIRE).  
Paper-first staging remains default; live broker sync stays out of scope unless separately requested.  
Disclaimer unchanged: decision support, not advice; user confirms.

---

## 14. UI preview index

Interactive mockups (chat): Home posture + GO cards; scope preview (ADD/CHANGE/KEEP).

### Directional FIRE card

```
┌─ FIRE 82 ──────────────────────────────────────────────┐
│ CONTINUATION_BULL · NIFTY · ENTER NOW                  │
│ δ [0.50] [0.35●] [0.25]                                │
│ BUY 25500 CE  LTP 72   Δ 0.32                          │
│ Entry 72 · T1 98 · T2 118 · Spot inval 25320           │
│ Size FULL · Max loss ₹4,680/lot · Trail after +30%     │
│ Thesis: Buyer CVD + above VWAP; call wall 25600 map.   │
│ Why ▸ Sentinel 82≥75 · CVD align · VWAP above · TE 68  │
│ [Stage] [Skip FIRE] [Pin] [Vertical alt]               │
└────────────────────────────────────────────────────────┘
```

### Structure / Writer cards

Same as prior ASCII plus **Why ▸** evidence line and **Skip FIRE** where applicable; writer keeps kill CTAs.

---

## 15. Open questions (build-time)

1. Default mode for new users — recommend `directional` with first-run picker.
2. Margin model accuracy vs NSE SPAN — ship as **estimate** with VERIFY label.
3. Whether SCOUT pushes are on by default — recommend off.
4. Weight calibration — freeze v1 weights; retune from journal FIRE outcomes after ≥20 sessions.
5. Whether Skip FIRE is prompted on dismiss or only explicit button — recommend explicit button only.

---

## 16. Success definition

A medium–high risk trader can:
1. Set mode once.
2. See FIRE and know the exact trade within 10 seconds (decisive D1–D8).
3. Open Why and double-check each factor against live panels.
4. Stage/journal (or Skip) in two clicks with immutable snapshot.
5. Manage with action buttons when risk changes.
6. Measure whether FIRE classes paid — MFE/MAE, expectancy, taken vs skipped, process lag — by mode and strategy without reconstructing the day from commentary noise.

If the product still feels like “eleven tabs of balanced caution,” or FIRE cannot be audited from evidence, Phase 0 is not done.

---

## 17. Requirements audit matrix (chat → spec)

| Theme from chat | Covered in spec? | Where |
|-----------------|------------------|-------|
| Decisive FIRE/SCOUT/HOLD for active risk-taker | Yes | §1, §1.1 D1–D8 |
| Not overly conservative / no veto brand | Yes | §1 personality; soft vs hard flags §4.2 |
| Three personas / modes | Yes | §2, templates §4.3 |
| Daily rituals / playbooks | Yes | §5 |
| Gaps per persona | Yes | §7 → epics |
| Complexity / inefficiency | Yes | §8 X1–X8 → F/E/H |
| Improvement roadmap phased | Yes | §10 |
| Additive not full redesign | Yes | §3.3 |
| Exact contract + enter + risk on card | Yes | §6, Epic B/C/D |
| OTM / trail / push / BE / IV−RV / margin | Yes | B1 B5 B6 C1 D1 D3 |
| Adjust / hedge / Greeks / payoff | Yes | Epic C/G/D |
| Reasoning trader can double-check | Yes | §4.4, E5, Why UI |
| Immutable entry context | Yes | E6 |
| Success & efficiency of decisions | Yes | Epic H, KPIs §9, API efficiency |
| Taken vs skipped measurement | Yes | E3, H2 |
| Process metrics (time-to-stage, trail, kill) | Yes | H4 |
| DSS health (dual CTA, stale FIRE) | Yes | H7, X1 |
| Wire to existing Signal Efficiency patterns | Yes | Epic H intro |
| Pre-bell / expiry blast in ritual | Yes | §5.1 |
| Honesty: map not dealer defense | Yes | §1 constraints, F4 |
| No auto-execution | Yes | Non-goals, §13 |

**Residual / deferred (explicit, not missing silently):**

| Item | Status |
|------|--------|
| Full interactive IV surface chart | Analytics tab deep link + GO smile strip (10Δ/25Δ/ATM) |
| Multi-TF as primary UI | Deferred; score boost only |
| True NSE SPAN | Estimate + VERIFY — writer intentionally incomplete |
| Live broker order route | Out of scope |
| Calendar/diagonal structures | Shipped when front IV rich |
| Portfolio multi-index writer dashboard | Shipped (margin proxy, not SPAN) |

**Audit verdict:** Buyer/structure completion pass closed A2, B6, X3/F1, A8/H5, X6/F4. Writer remains intentionally below 100% without exchange SPAN. No auto-execution.
