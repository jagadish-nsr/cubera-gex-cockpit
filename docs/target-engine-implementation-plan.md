# Target Engine: Implementation Plan & AI-Editor Build Prompt

**Companion to:** `nifty-options-analytics-implementation-plan.md` (the "Analytics Service").
**What this adds:** A module that turns the Analytics Service's data (chain snapshots, ATM IV, PCR, OI walls, buildup, max pain, skew, order flow) into **scenario-based underlying targets, an IV forecast, and repriced option targets** (premium target, stop, P&L, risk-reward, probability-weighted expected value), plus a **backtest and calibration harness** that tells you whether any of it actually works.

**Design stance (important for the AI editor):**
- This is a **decision-support and scenario tool, not a prediction oracle.** Output is always "scenario + probability + repriced premium," never a single price.
- **Nothing ships on heuristics alone.** Heuristic weights are the starting point; the backtest harness (Feature T9) decides whether they stay.
- Every number the engine outputs must be **explainable**: which inputs, which weights, which formula.

> Not investment advice. Options carry substantial risk of loss.

---

## 0. Instructions to the AI coding editor (read first, every session)

```
You are adding a "Target Engine" module to an existing Nifty options analytics
service. The Analytics Service (chain fetch, snapshot persistence, ATM IV, PCR,
Max Pain, OI buildup, skew, IV rank, RV, expected move) is either already built
in this repo or partially built. DO NOT rebuild any of it.

Before writing code:

1. Audit the repo and produce a report:
   - Which Analytics Service features (ATM IV, PCR, max pain, buildup, skew,
     RV, expected move, chain_snapshots table, poller) already exist, and where
   - Any existing option pricing / Black-Scholes / Greeks / implied-vol solver
     code (search for "black", "scholes", "greeks", "implied_vol", "norm.cdf",
     "scipy.stats")
   - Any existing backtest, simulation, or research notebooks
   - Existing config pattern, DB layer, scheduler, API framework, test setup
   - Existing futures/market-depth fetchers (the engine needs futures OI,
     futures price, and top-of-book bid/ask quantity)
   - Existing lot-size / instrument-master handling

2. For every feature in Section 2, search for existing code first. EXTEND or
   WRAP what exists. State per feature: reused / extended / net-new, with paths.

3. Match the repo's language, framework, naming, async style, error handling,
   and logging. Do not introduce new libraries unless nothing in the repo does
   the job; if you must (e.g. scipy for the normal CDF, numpy/pandas for the
   backtest), flag it and justify before adding.

4. Build in the phase order in Section 3. Each feature = pure function(s) with
   unit tests on hand-checkable fixtures FIRST, then persistence, then API,
   then UI. No UI before the pure function is tested.

5. Hard rules:
   - All thresholds, weights, and horizons live in ONE config file
     (target_engine.yaml or the repo's equivalent). No magic numbers in code.
   - The engine must work from LOGGED snapshots (replayable), never only from
     a live call. Every live computation must be reproducible offline from
     stored data. This is what makes the backtest valid.
   - No look-ahead bias anywhere: a feature computed at time t may only use
     data with timestamp <= t. Enforce this with tests.
   - Never present a probability without also exposing its calibration status
     ("uncalibrated heuristic" vs "calibrated on N samples, Brier X").

6. Ask before proceeding if: the repo has an existing pricing library whose
   conventions (r, q, day-count, forward vs spot) differ from Section 2.5;
   futures depth data isn't available from the existing client; or the DB
   cannot store the new tables reasonably.
```

---

## 1. Codebase Audit Checklist (first deliverable, before code)

| Area | Exists? | Location | Reuse plan |
|---|---|---|---|
| chain_snapshots persistence | | | |
| ATM IV / iv_history | | | |
| PCR / pcr_history | | | |
| Max pain function | | | |
| OI buildup classifier | | | |
| Skew / term structure | | | |
| RV / expected move | | | |
| Futures price + OI fetcher | | | |
| Market depth (bid/ask qty) fetcher | | | |
| Black-76 / Black-Scholes / Greeks code | | | |
| IV solver (Newton / bisection) | | | |
| Lot size / instrument master | | | |
| Backtest / research code | | | |
| Config system | | | |
| Scheduler / worker | | | |
| API framework / frontend | | | |

Rule: any row marked "Yes" means the spec below describes only the **delta**.

---

## 2. Architecture Overview

```
[chain_snapshots + futures ticks + depth + history]      (from Analytics Service)
              |
              v
  T1 Feature Extraction  -->  FeatureVector(t)            (pure, point-in-time)
              |
              v
  T2 Scenario Scoring    -->  P(bull), P(base), P(bear)   (heuristic -> calibrated)
              |
              v
  T3 Underlying Targets  -->  per-scenario target/stop/horizon, touch probabilities
              |
              v
  T4 IV Forecast         -->  per-scenario expected IV at horizon
              |
              v
  T5 Option Repricer     -->  per-scenario premium (full reprice + Greek attribution)
              |
              v
  T6 Strike & Structure Selector --> ranked candidates (single leg, spreads)
              |
              v
  T7 Risk/Reward & Sizing -->  EV, R:R, breakeven, lots, max loss
              |
              v
  T8 Persistence + API + Alerts + UI
              |
  T9 Backtest & Calibration Harness (offline, reads same stored data,
                                      feeds weights/calibration back to T2/T4)
```

**Core data contract (all stages pass these):**

```
FeatureVector     { ts, expiry, dte, spot, fut, features: {name: value}, quality_flags[] }
ScenarioSet       { ts, scenarios: [ {name, prob, calibrated: bool} ] }
UnderlyingTarget  { scenario, target, stop, horizon_minutes, p_touch_target, p_touch_stop, p_close_beyond }
IVForecast        { scenario, iv_at_horizon, method }
OptionTarget      { strike, type, scenario, entry_premium, target_premium, pnl_pts, greek_attribution{delta,gamma,vega,theta,residual} }
TradeCandidate    { structure, legs[], per-scenario OptionTargets, ev, rr, breakevens, max_loss, margin_est, rank_score, explain[] }
```

---

## 3. Build Phases

| Phase | Features | Exit criteria |
|---|---|---|
| **0** | Audit | Report + table above. No code. |
| **1** | T0 contracts/config, T5 pricer core | Black-76 price, Greeks, IV solver pass reference tests; round-trips IV to within 1e-6 |
| **2** | T1 features, T3 level engine | Features reproducible from stored snapshots; walls/levels validated by eye against a real chain |
| **3** | T2 scoring, T4 IV forecast | Scenario probabilities sum to 1; every probability carries an explanation |
| **4** | T6 selector, T7 risk/sizing | Ranked candidates with EV, R:R, breakevens for a real snapshot |
| **5** | T9 backtest and calibration | Walk-forward report with hit rate, Brier score, EV after costs; weights/calibration updated from it |
| **6** | T8 API, persistence, alerts, UI | Dashboard shows live scenarios; every number has an "explain" drawer |

**Important sequencing note:** do Phase 5 *before* polishing Phase 6. If the backtest shows no edge, the UI should say so honestly rather than present confident-looking targets.

---

## 4. Feature Specifications

### T0. Contracts, Config, and Time Handling

**Purpose:** Fix the data contracts (Section 2) and a single config file before anything else.

**Config keys (all tunable, none hardcoded):**
```
horizons_minutes: [30, 120, 375]          # intraday, half-day, full-session; plus expiry-based
scenario_names: [bull, base, bear]
risk_free_rate: 0.065                     # make configurable; source from repo or config
dividend_yield: 0.0                       # using futures (Black-76) so usually not needed
day_count: 365                            # calendar-day convention for T
wall_strike_window: 10                    # strikes either side of ATM considered for walls
wall_min_oi_share: 0.15                   # a wall must hold >= 15% of side's windowed OI
min_liquidity: { min_oi: 5000, max_spread_pct: 5.0 }
feature_weights: { ... }                  # see T2
iv_forecast: { ... }                      # see T4
costs: { brokerage_per_order, stt_pct, exchange_charges_pct, slippage_pts }
market_hours: { open: "09:15", close: "15:30", tz: "Asia/Kolkata" }
```

**Time handling (explicit, because it's a classic bug source):**
- Time to expiry `T` in years = (expiry_datetime − now) / (365 days), with expiry at 15:30 IST on expiry date. Count fractional days. Do **not** use whole-day DTE for intraday targets.
- Decide and document whether `T` uses calendar days (default) or trading-time. Keep consistent with how the exchange/vendor computes IV, or the repriced IV won't match the quoted IV.
- Horizon end must be clipped to `min(now + horizon, expiry)`.

---

### T1. Feature Extraction (point-in-time, pure)

**Purpose:** Convert a snapshot (plus recent history) into a numeric `FeatureVector`. All features are computed using data with `timestamp <= t` only.

Each feature below has a definition, a normalization, and a sign convention (**+1 = bullish pressure, −1 = bearish**).

| # | Feature | Definition | Normalization to [-1, 1] |
|---|---|---|---|
| F1 | **Call-wall proximity** | Distance from spot to nearest call wall above, in units of expected 1-SD move | `-clip(1 - dist/σ, 0, 1)`: closer wall = more resistance (bearish) |
| F2 | **Put-wall proximity** | Distance from spot to nearest put wall below, in 1-SD units | `+clip(1 - dist/σ, 0, 1)`: closer support = bullish |
| F3 | **Wall momentum** | Change in OI at the walls over last N minutes (wall strengthening or being unwound) | Call-wall OI falling = bullish (+), put-wall OI falling = bearish (−) |
| F4 | **Aggregate buildup** | From the Analytics Service classifier, calls and puts separately | Short buildup/long unwinding = −, long buildup/short covering = + |
| F5 | **PCR level vs own history** | z-score of `pcr_oi` vs trailing window | tanh(z/2); read as contrarian/trend per config (see T2 note) |
| F6 | **PCR trend** | Slope of `pcr_oi` over last N minutes | tanh(slope / scale) |
| F7 | **PCR of OI change** | `pcr_oi_change` (fresh positioning only) | tanh(log(x)) with divide-by-zero guard |
| F8 | **Futures buildup** | Futures price change vs futures OI change (same 4-state matrix) | ±1 by state, scaled by magnitude percentile |
| F9 | **Futures basis change** | Change in (fut − spot) vs recent norm | tanh(z); widening premium = bullish |
| F10 | **Depth imbalance (index futures / ATM options)** | `(bid_qty − ask_qty) / (bid_qty + ask_qty)` at top of book, time-averaged over N snapshots | Already in [-1, 1]; smooth with EMA, reject single-snapshot spikes |
| F11 | **Volume burst** | Current-window volume / trailing median volume, in direction of price move | `sign(Δprice) * tanh(log(ratio))` |
| F12 | **ATM straddle change** | % change in straddle premium vs N minutes ago | Non-directional; feeds **volatility** logic (T4), not direction |
| F13 | **IV change (ATM)** | Level and change from Analytics Service | Non-directional; feeds T4 |
| F14 | **Skew change** | 25-delta put IV minus call IV, and its change | Put skew rising = bearish hedging demand (−) |
| F15 | **Max-pain pull** | `sign(maxpain − spot) * w(dte)`, where `w(dte)` ramps from ~0 at DTE>3 to ~1 at DTE<=1 | Heavily gated by DTE (config curve) |
| F16 | **Intraday trend** | Price vs VWAP, and distance from day high/low in σ-units | tanh |
| F17 | **Realized vs implied** | `RV_short / ATM_IV` | Non-directional; feeds T4 |
| F18 | **Event proximity** | Hours to next configured event (RBI, Fed, CPI, budget, results) | Non-directional; feeds T4 and the risk flag |

**Wall detection algorithm (for F1-F3 and T3):**
1. Take strikes within `wall_strike_window` of ATM.
2. For calls: candidate walls are strikes above spot; for puts: strikes below spot.
3. Wall score = `OI_strike / sum(OI on that side in window)`; keep strikes with share >= `wall_min_oi_share`.
4. Merge adjacent strikes if combined share is meaningful (a "wall zone", not a single strike).
5. Wall strength = OI share + OI-change share; a wall that's being **added to** is stronger than one being unwound.
6. Output: ordered list of `{strike, side, oi, share, oi_change, strength}`.

**Output also includes `quality_flags`:** `stale_snapshot`, `thin_chain`, `missing_depth`, `first_snapshot_of_day`, `wide_spreads`, `expiry_day`, `event_window`. Downstream stages **down-weight or suppress** output when flags are set.

**Edge cases:** missing depth data → set F10 to 0 and flag it (don't fabricate); expiry day → many features become unstable, flag and widen uncertainty; first 15 minutes after open → buildup is noisy, flag.

---

### T2. Scenario Scoring & Probabilities

**Purpose:** Combine the directional features into probabilities for bull, base (range), and bear.

**Stage A: Heuristic baseline (ships first, clearly labeled "uncalibrated")**

1. Directional score: `D = Σ w_i * F_i` over directional features (F1-F11, F14-F16), weights in config, normalized so `Σ|w_i| = 1` so D is in [-1, 1].
2. Conviction/regime scalar `C` in [0,1] from non-directional features: high when features agree and quality flags are clean; low when mixed or flagged. Compute agreement as `1 - normalized dispersion of signed feature contributions`.
3. Scenario logits:
   - `z_bull = a * D * C`
   - `z_bear = -a * D * C`
   - `z_base = b + c * (1 - C) + d * range_regime_score`
   where `range_regime_score` is high when price is pinned between a close call wall and put wall.
4. Probabilities = softmax(z). `a, b, c, d` in config.

**PCR interpretation switch (config):** PCR can be read as trend-following or contrarian. Make this an explicit config option per regime (default: neutral weight until the backtest shows which reading works for your data).

**Stage B: Calibrated model (after backtest data exists)**

- Replace the hand-set weights with a multinomial logistic regression (or gradient-boosted classifier if the repo already has the tooling) trained to predict **which scenario realized at the horizon**.
- Scenario label definition (must be fixed before training; config):
  - **bull** if `close_at_horizon − entry >= +k·σ_h`
  - **bear** if `<= −k·σ_h`
  - **base** otherwise, with `σ_h` = IV-implied 1-SD move for that horizon, `k` default 0.5
- Apply **probability calibration** (isotonic or Platt) on a held-out period; store reliability curves.
- Keep the heuristic as a fallback and as a benchmark: the calibrated model must **beat the heuristic on out-of-sample Brier score** or it isn't promoted.

**Output:** `ScenarioSet` with `calibrated` flag, `model_version`, `n_train_samples`, `brier_oos`, and a per-feature contribution table (the "why").

**Edge cases:** if conviction `C` is below the minimum threshold, output `no_edge` as the headline and show base-case-dominant probabilities. Explicit "no trade" is a valid, valuable output.

---

### T3. Underlying Target & Level Engine

**Purpose:** For each scenario, produce a target level, a stop level, and **probabilities of touching** them within the horizon.

**Target generation per scenario and horizon `h`:**

- `σ_h = fut * ATM_IV * sqrt(h_years)` (1-SD move over the horizon; for intraday use trading-time scaling per config).
- **Bull target candidates:** (a) next call wall above (resistance), (b) `entry + m·σ_h` for m in config (default 0.5, 1.0), (c) max pain if DTE <= 1 and it lies above, (d) day-high / prior-day-high. Pick the **nearest credible** candidate, because resistance caps upside.
- **Bear target candidates:** mirror with put walls, day-low/prior-day-low.
- **Base scenario target:** the midpoint of the nearest put-wall and call-wall (the pinned range), with range = the wall-to-wall band.
- **Stop levels:** beyond the opposing structure: for a bull trade, below the nearest put wall zone or `entry − s·σ_h` (default s = 0.5), whichever is tighter-but-structurally-valid.
- **Never output a target beyond ±2σ_h**; if structure suggests it, cap and flag.

**Probability of touching a level (not the same as closing beyond it):**

- Under a driftless random-walk approximation, the probability of touching a level at distance `d` within horizon `h` is about `2 · (1 − N(d / σ_h))` (reflection principle). Use this as the **baseline**.
- Adjust by scenario drift: shift `d` by the scenario-implied drift `μ_s·h`, where `μ_s` is configured per scenario.
- Output **both** `p_touch` and `p_close_beyond` (`1 − N(d/σ_h)`). Intraday traders care about touch; position-holders care about close.
- In the backtest (T9), compare these formula probabilities to empirical touch frequencies by distance bucket and **replace/rescale with the empirical curve** if they diverge.

**Output:** `UnderlyingTarget` per scenario and horizon, with the structural reason for each level ("call wall 23,000, share 24%, OI rising").

**Edge cases:** target and stop closer than a configurable minimum (e.g., < 0.15σ_h) → reject as untradeable noise; two walls at nearly equal strength → report both and take the nearer; expiry day → shrink σ_h consistent with remaining time and flag extreme gamma.

---

### T4. IV Forecast (the part most people forget)

**Purpose:** Estimate ATM IV at the horizon for each scenario, because vega can erase or double a delta-based target.

**Method (rule-based baseline, then empirical):**

`IV_h(scenario) = IV_now + ΔIV_mean_reversion + ΔIV_spot_move + ΔIV_event`

1. **Mean-reversion term:** `κ · (IV_longrun − IV_now) · h_days`, where `IV_longrun` is a trailing median (e.g., 60d) and `κ` is in config. After a spike (e.g., IV change > +10% on the day) with no event ahead, this term is negative (a vol-crush bias).
2. **Spot-move term ("spot-vol correlation"):** for index options, down-moves lift IV and up-moves compress it. `ΔIV = β_down · max(−return, 0) + β_up · max(return, 0)`, with `β` signs per config and re-estimated empirically.
3. **Event term:** if an event falls inside the horizon, add a pre-event drift upward (vol builds into events) and, if the horizon ends **after** the event, a post-event crush; both amplitudes come from config and are calibrated historically.
4. **Skew/surface term:** a repriced strike's IV must be taken from the **smile at the new spot**, not just the ATM IV shift. Implementation: sticky-delta by default (the strike's IV moves with its delta/moneyness), with sticky-strike as a config alternative. Output which convention was used.

**Stage B (empirical):** regress next-horizon ΔATM_IV on [ΔIV so far, IV rank, spot return, event flags, IV−RV gap] using logged history; use the fitted model if it beats the rule-based baseline out-of-sample on RMSE.

**Output:** `IVForecast` per scenario with the component breakdown (so a trader can see "−1.2 pts from mean reversion, +0.8 from the spot move").

**Edge cases:** insufficient IV history → fall back to the IV_now-flat assumption and flag `iv_forecast_unreliable`; always expose the **IV sensitivity table** (premium at IV −2/−1/0/+1/+2 pts) so the user can override.

---

### T5. Option Repricing Engine (core quant module)

**Purpose:** Given (spot/forward, strike, type, IV, time, rates), return price and Greeks; reprice any option at a hypothetical (forward, IV, time) point.

**Model:** **Black-76 on the futures price** (the index options are European-style on the index and price off the forward), with `F` = the current futures price (adjust F by scenario move in T3).

```
d1 = [ln(F/K) + 0.5·σ²·T] / (σ·√T)
d2 = d1 − σ·√T
Call = e^(−rT) · [F·N(d1) − K·N(d2)]
Put  = e^(−rT) · [K·N(−d2) − F·N(−d1)]
```
Greeks (per standard Black-76) computed analytically; theta expressed per calendar day; vega per 1 IV point (1%).

**Required functions (pure, tested):**
- `price(F, K, T, sigma, r, type)`
- `greeks(F, K, T, sigma, r, type)` returning delta, gamma, vega, theta, plus optional vanna/volga for the attribution residual
- `implied_vol(price, F, K, T, r, type)` using Newton with a bisection fallback, with bounds and no-arbitrage checks (return `None` below intrinsic)
- `reprice(option, F_new, iv_new, T_new)` → full reprice
- `greek_attribution(option, F_new, iv_new, T_new)` → `{delta, gamma, vega, theta, residual}` where `residual = full_reprice_change − sum(first/second-order terms)`; this shows when the Taylor approximation breaks

**Why both full reprice and Greek attribution:** the full reprice is the **truth**; the attribution is the **explanation**. A large residual (big move, near expiry) is itself a warning to display.

**Reconciliation test (mandatory):** reprice at the **current** inputs and confirm the model price is within tolerance of the live LTP/mid. If the gap is larger than `max_model_gap_pts`, surface it, since it means the IV convention, `r`, or `T` is mismatched with the data source. Use the **chain's own quoted IV** as the starting `σ` for consistency.

**Path grid (time decay realism):** besides the horizon-end point, compute a grid over (spot × time) so the user sees premium at, say, 5 spot levels × 4 time points; this exposes theta bleed if the move takes longer than expected.

**Edge cases:** `T` extremely small (last minutes) → guard against division by zero, clamp `σ√T`, flag; deep ITM/OTM with zero vega → IV solver returns `None`, handled; negative-time guard if horizon > expiry.

---

### T6. Strike & Structure Selector

**Purpose:** Rank candidate trades for the highest-probability scenario (or for a user-chosen directional/vol view).

**Candidate generation:**
- **Directional view:** long ATM call/put; long OTM call/put (delta 0.25-0.40); debit spreads (bull call, bear put) with width set by the target distance; credit spreads when IV is rich and a wall supports them.
- **Range view:** iron condor / iron fly with short strikes at or just beyond the walls, wings beyond ±1σ_h.
- **Vol view:** long straddle/strangle pre-event when IV is cheap vs the expected event move; short-vega defined-risk when IV is high vs RV and no event ahead.

**Hard filters (reject, with reason):**
- Liquidity: bid-ask spread% > `max_spread_pct` or OI < `min_oi` on any leg
- Spread width/cost: net premium cost vs max gain makes R:R below `min_rr`
- Target not reachable: structure's breakeven beyond ±1.5σ_h of entry for the horizon
- Time: DTE too short for the premium to survive the scenario's time-path

**Ranking score (config-weighted):** `rank = w1·EV_per_risk + w2·P(profit) + w3·liquidity_score − w4·theta_burn_ratio − w5·vega_exposure_mismatch`.
`vega_exposure_mismatch` penalizes being long vega when T4 forecasts a crush, and vice versa.

**Output:** top-N candidates, each with an `explain[]` list ("long 23,000 CE chosen: delta 0.35, call wall is at 23,100, vega exposure acceptable because IV forecast flat").

**Edge cases:** no candidate passes the filters → return `no_trade` with reasons rather than relaxing filters silently.

---

### T7. Risk/Reward, EV, and Sizing

**Purpose:** Convert each candidate into the numbers a trader needs to decide.

**For each candidate and each scenario s (from T3-T5):** target premium `P_s`, P&L per lot = `(P_s − entry) · lot_size` (sign-adjusted per leg).

**Metrics:**
- **Probability-weighted EV** = `Σ_s prob_s · pnl_s` (using T2 probabilities), minus **costs** (brokerage, STT on sell side/exercise, exchange charges, GST, slippage from config).
- **R:R** = (reward at the primary-scenario target) / (loss at the stop), using **premium-stop and structure-stop** both (the stop on the underlying level converted to a premium via T5).
- **Breakeven(s)** at expiry **and** at the horizon (these differ, and the horizon breakeven matters for short holding periods).
- **Max loss, max gain, margin estimate** (use the broker margin API if the existing client exposes one; otherwise the config approximation, flagged as approximate).
- **Sizing:** `lots = floor( risk_budget / max_loss_per_lot )` with `risk_budget = capital · max_risk_pct` (config; default 1%). Optional fractional-Kelly cap shown as an **informational** line only, never auto-applied, and only when the model is calibrated.

**Lot size:** read from the instrument master via the existing client, never hardcode. Nifty lot size has changed over time; treat it as data.

**Output:** `TradeCandidate` with the full metric set and a one-line verdict: `favorable / marginal / avoid`, driven by config thresholds (EV after costs > 0, R:R >= min, P(profit) >= min, model calibrated or explicitly flagged not).

---

### T8. Persistence, API, Alerts, UI

**Tables (adapt to repo conventions):**
```
target_runs        id, ts, expiry, model_version, features_json, quality_flags
scenarios          run_id, name, prob, calibrated
underlying_targets run_id, scenario, horizon_min, target, stop, p_touch_t, p_touch_s, reason
iv_forecasts       run_id, scenario, horizon_min, iv, components_json
trade_candidates   run_id, rank, structure, legs_json, ev, rr, breakevens_json, max_loss, verdict, explain_json
outcomes           run_id, horizon_min, realized_spot, realized_scenario, realized_option_pnl   # filled in later for backtest/live tracking
```
Persist every run (not just the latest) so live accuracy can be tracked against `outcomes`.

**Cadence:** recompute on each new snapshot batch, but throttle to a config interval (default 15-60s); targets shouldn't flicker every poll, so apply **hysteresis** (only change the headline scenario if the probability margin exceeds a threshold for N consecutive runs).

**API (adapt to repo naming):**
- `GET /target/{symbol}/latest?expiry=&horizon=` → scenarios + targets + IV forecast
- `GET /target/{symbol}/candidates?expiry=&view=auto|bull|bear|range|vol`
- `POST /target/reprice` → body `{strike, type, expiry, spot_scenario|F_new, iv_new|iv_shift, minutes_ahead}` returns the premium, Greeks, and attribution (the user's "what-if" calculator)
- `GET /target/{symbol}/explain/{run_id}` → feature contributions and formula trace
- `GET /target/{symbol}/track-record` → live hit rate, calibration curve, EV realized vs predicted

**Alerts (reuse the existing channel):** scenario flips (with hysteresis), price reaching a computed level, "no_edge to edge" transitions, IV-forecast-vs-actual divergence, model-gap alarm from T5's reconciliation.

**UI (reuse the repo's chart library, no new one):**
- Scenario bar (bull/base/bear with probabilities and a calibrated/uncalibrated badge)
- Price ladder: spot, walls, targets, stops, max pain, expected-move band
- Candidate table with EV, R:R, breakevens, verdict, and an "explain" drawer
- What-if calculator: sliders for spot, IV shift, minutes ahead → live repriced premium + Greek attribution
- Payoff-at-horizon chart (not just at expiry) with scenario markers
- Track-record panel: how well past probabilities matched outcomes

---

### T9. Backtest & Calibration Harness (the honesty module)

**Purpose:** Establish, with your own logged data, whether the engine has any edge, tune it, and keep it honest in production.

**Data requirements:** `chain_snapshots` (full chains), futures price/OI ticks, depth snapshots, spot candles, event calendar. If logged history is thin, **say so** and report the sample size prominently. Start the logger immediately, because the backtest is only as good as the history, and the historical-options endpoint can backfill ATM IV and OI by offset but not full-fidelity chains or depth.

**Replay engine:**
- Iterate through stored timestamps; at each step feed only data with `ts <= t` into T1-T7 (enforced by a `PointInTimeView` wrapper that raises on any future access, with a test that tries to cheat).
- Simulate entry at the **next available quote after signal** (not the same snapshot): price at ask for buys, bid for sells, plus configured slippage; this avoids the biggest source of fake backtest alpha.
- Exits: target hit, stop hit, or horizon end, evaluated on **subsequent** snapshots; resolve same-snapshot ambiguity (target and stop both crossed) **conservatively** (assume stop first).

**Validation protocol:**
- **Walk-forward**, never random splits: train on [t0, t1], validate on (t1, t2], roll forward; report each fold.
- Hold out the **most recent** period untouched until the end.
- Parameter search limited to a small, pre-declared grid to limit overfitting; report how many configurations were tried.

**Metrics (all reported out-of-sample):**
- Scenario classifier: accuracy, **multiclass Brier score**, log-loss, and a **reliability diagram** per scenario (predicted prob bucket vs realized frequency).
- Level accuracy: empirical `p_touch` vs formula `p_touch` by distance bucket; wall-hold rate (how often price reversed at a computed wall within X pts).
- IV forecast: RMSE and bias vs the flat-IV baseline.
- Trade level: win rate, average win/loss, **EV per trade after costs**, profit factor, max drawdown, worst losing streak, results **split by regime** (IV rank bucket, DTE bucket, event vs non-event, trend vs range day).
- **Baselines to beat:** (a) always-base-case, (b) random direction, (c) buy-and-hold ATM option same horizon. If the engine doesn't beat these after costs, report it plainly.
- **Statistical honesty:** bootstrap confidence intervals on EV and win rate; flag results with n < config minimum as "not significant."

**Feedback loop:** a `promote_model` command updates T2 weights/calibration and T4 coefficients **only if** out-of-sample metrics beat the incumbent by a margin; every promotion is versioned (`model_version`) and logged.

**Live monitoring:** the `outcomes` table is back-filled as horizons pass; a daily job recomputes live Brier/EV and raises an alert if live performance drifts materially below backtest (a **model decay alarm**).

**Edge cases:** survivorship in expiries (include all expiries, not only convenient ones); data gaps in the logger (skip, don't interpolate); regime shifts (report performance by year/quarter so degradation is visible).

---

## 5. Testing Strategy

| Layer | Tests |
|---|---|
| T5 pricer | Known Black-76 reference values; put-call parity; IV round-trip; Greeks vs finite-difference; extreme inputs (T→0, σ→0) |
| T1 features | Fixture chain → hand-computed walls, PCR, buildup; point-in-time test (future data must raise) |
| T2 | Probabilities sum to 1; monotonic response (stronger bullish features → higher bull prob); no_edge triggers |
| T3 | Touch-probability formula vs a Monte Carlo simulation (within tolerance); target/stop sanity rules |
| T4 | Component additivity; crush-after-spike behavior; fallback when history is thin |
| T6/T7 | Hand-built spread payoff checks; cost model; sizing arithmetic; filter rejections with reasons |
| T9 | Replay on synthetic data with a known edge (should detect it) and **with no edge (should report none)**, the key test that the harness doesn't hallucinate alpha |
| Integration | End-to-end on one stored session: snapshots in → candidates out, deterministic across runs |

---

## 6. Non-Functional Requirements

- **Determinism:** the same stored inputs and config version always produce identical outputs (seed any randomness).
- **Explainability:** every output field carries a `reason`/`explain` trace; the "why" is a first-class output.
- **Performance:** a full engine run (T1-T7) on one snapshot should complete well inside the polling interval; cache the pure-function results keyed by snapshot id.
- **Safety rails in the UI and API:** always show calibration status, sample size, and a "not investment advice" notice; never display a single-point "price will reach X" without its probability and scenario context.
- **Versioning:** config, model version, and code version are stored with each run so any historical output is reproducible.
- **Observability:** log feature quality flags, model gap from reconciliation, run latency, and live-vs-backtest drift.

---

## 7. Known Limitations (document these in the product, not just here)

1. **Option OI has no side information.** "Call writers defend this strike" is an inference. Treat walls as probabilistic, and expect them to fail on trend days and expiry days.
2. **Order-flow edge is typically small and decays.** Depth imbalance and flow signals are noisy at the top of book and can be spoofed or ephemeral; expect modest hit rates and rely on R:R and sizing.
3. **Max pain is weak before the final sessions.** Gated by DTE for this reason.
4. **IV forecasting is hard.** The rule-based version is a structured guess until the empirical model beats it out-of-sample.
5. **Backtest realism is limited by logged data.** Without full historical chains and depth, parts of the harness can only run on data you accumulate going forward.
6. **Regimes change.** Calibration from one volatility regime may not transfer; the track-record panel and decay alarm exist for this reason.
7. **Execution costs are real.** Spreads, STT, and slippage on short-dated options can consume small edges; EV is always shown net of costs.

---

## 8. Open Questions to Resolve Early

1. Primary horizons: scalp (minutes), intraday, or multi-day? This changes T2 labels, T3 σ-scaling, and what the backtest must contain.
2. Trading style: buyer-only, or willing to run defined-risk short-vega structures? Affects T6 and the margin logic.
3. How much logged history exists today, and how soon can the logger start recording **full chains, futures OI, and depth** at fine resolution?
4. Event calendar source (manual config file vs an external feed)?
5. Is a broker margin API available in the existing client, or should margin be approximated?
6. Should the engine ever auto-generate orders? **Recommendation: no.** Keep it advisory-only; if execution is added later, make it a separate, explicitly-gated module.

---

*Hand Sections 0-4 to the AI editor as the working brief. Its first required output is the Section 1 audit table; its last gate before any UI polish is the Phase 5 backtest report.*
