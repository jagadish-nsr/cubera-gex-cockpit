# TO-DO / backlog

Running backlog of planned work. Newest entries on top. Each entry is timestamped
(IST). Decision-support project — verify-live before trusting NSE data.

---

## 2026-07-22 IST — [W2] Big-money FUTURES tape (live websocket)  ✅ CORE BUILT (2026-07-22 — decoder+filter+collector+dashboard+tests, proven live; remaining = calibrate floors, next-month roll)

**Built (2026-07-22, proven end-to-end on the live feed):** `data/dhan_feed.py` (v2 WS client + binary
decoder, offsets LOCKED by `tests/test_dhan_feed.py` hex fixtures), `app/big_money.py` (pure filter:
`notional`/`classify_aggressor`/`_BurstTracker`/`BigMoneyDetector` + `Event` + JSONL store), and
`scripts/run_big_money_collector.py` (headless runner, `--dry-run`, `--print-floor`/`--burst-floor`).
Verified live: prints + bursts fire with correct aggressor side (▲BUY/▼SELL/•), notional, lots, and
EXCHANGE time (fixed a Dhan-LTT IST-wall-as-epoch +5:30 bug).

**+ Automated & integrated (2026-07-22):** `app/big_money_live.py` — a `Collector` daemon thread
auto-started from the dashboard via `st.cache_resource` (one singleton/server; sidebar toggle
"🐋 Big-money tape (live, auto)", default on). It also **archives every raw Quote/Full tick** to
`data/journal/big_money_raw/<SYM>/<day>.csv` — because Dhan's HISTORICAL API has NO tick data (finest =
1-min candles, verified in docs), the live feed is the only tick source. The tape card shows a live
health strip (connected · last-tick age · events · raw-ticks · per-symbol tick counts). Optimised
startup: `resolve_futures` now downloads the scrip master ONCE (was 3×). 251 tests green.

**+ Ops (2026-07-22):** `scripts/run_big_money_collector.py --market-hours` (IST-aware: waits 09:15,
stops 15:30, auto-logs) now runs the shared `Collector` (raw + events); `scripts/install_collector_schedule.ps1`
registers a Windows weekday task (fires 03:15 local → collector waits for open; DST-robust since it waits
in IST); `scripts/big_money_summary.py` reports the print/burst notional distributions (→ suggested p99
floors) + biggest prints + buy-vs-sell signed flow (the directional lean) for calibration.

**+ Events → SQLite + flow bias (2026-07-22):** `app/big_money_db.py` — the big-money EVENT store is now
SQLite (`data/journal/big_money.db`, WAL, UNIQUE-key dedupe); `append_event`/`load_events` delegate to it
(callers unchanged), `migrate_jsonl` imports legacy files, `daily_rollup` aggregates per (day,symbol):
buy vs sell-initiated ₹, net flow, lean, biggest. `scripts/big_money_rollup.py` prints the day-by-day
directional ledger. The dashboard tape now opens with a **"Big-money flow bias · today"** block (per-symbol
buy/sell ₹ + net lean) so opening mid-day gives the running read — DESCRIPTIVE order flow, explicitly NOT
a validated forecast (to be sharpened with data/study).

**+ Parquet compaction + flow-IC harness (2026-07-22):** `scripts/compact_raw_ticks.py` compacts finished
raw-tick CSVs → Parquet (merge+dedupe; `--market-hours` collector auto-compacts today on exit;
`load_raw_ticks` prefers Parquet). `scripts/big_money_flow_ic.py` = the pre-registered validation of the
flow-bias (daily net-flow lean → next-session return, Spearman IC vs shuffled mirror, per symbol); reports
INSUFFICIENT until ~15+ captured sessions — the flow bias stays DESCRIPTIVE until it beats the placebo.
253 tests green.

**Scheduled tasks (audited 2026-07-22):** the raw-tick collector `GEX-BigMoneyCollector` IS registered
(Ready). The existing `*GEX-TimeSeries` / `*Log` / `GEX-FuturesBuildup` / `GEX-ParticipantOI` tasks collect
DIFFERENT data (option-chain OI already Dhan-primary w/ NSE backup; gex_history; NSE bhavcopy;
**NSE-only participant-wise FII/DII OI**) — big-money (futures ticks) does NOT replace them, so they were
KEPT. Optional future: flip `log_timeseries` to Dhan-only (drop NSE fallback) but keep the EOD NSE tasks.

**+ Futures rollover — Option A (2026-07-23):** watch the TWO nearest contracts per symbol
(`resolve_futures(rolls=2)` → `nearest_futures`, tagged roll=near/next) and aggregate by symbol. Prints
stay per-contract (tagged `expiry`), bursts aggregate to symbol level (`_SymbolBurstTracker` sums both
contracts' volume deltas into one window, tagged `AGG`), flow/rollup fold in via `GROUP BY symbol`. New
`futures_state` table + rollover gauge on the tape (`%rolled = next_OI/(near+next OI)`; verified live
2026-07-23: NIFTY ~29%, BankNifty ~32% rolled to Aug). Dedupe keys extended with `expiry`; collector
re-resolves near/next daily. 258 tests green. NOT yet: calendar-spread detection.

**+ ΔOI / positioning (2026-07-24):** every event now carries `oi` (symbol total, near+next) + `doi`
(ΔOI over the burst window) — `_SymbolBurstTracker` tracks per-contract OI. `buildup(direction, doi)` =
the four-quadrant read (long/short build-up, covering, unwinding). `big_money_analysis.py` §4 = day OI
trend + per-bucket build-up + per-print build-up ₹; the tape shows a per-row build-up tag. Turns bursts
from *flow* into *positioning* and gives them a direction. `docs/burst-analysis.md` is the research/reading
guide. 261 tests green. Needs a session captured AFTER this change (pre-ΔOI events show "no OI").

**+ Conviction ΔOI/Δvol + 180s window fix (2026-07-24):** conviction = share of window volume that became
NET positioning vs churn. FIXED the window: measured OI updates ~every 60s, so the 10s burst window gave
|conv|≈0 — conviction now uses a decoupled 180s positioning window (`POS_WINDOW_S`); the tracker returns
`(fired,wnot,wqty,tot_oi,doi,conv)` and events carry `conv`. **Validated on 2026-07-24 raw: |conv| med
0.34, 65–71% positioning, net +0.2–0.3 BUILDING** (was 0.00/17% at 10s). `scripts/rederive_events.py`
replays the raw archive through the current detector so a fix reaches already-captured days (raw =
source of truth); `big_money_analysis.py --db`. Flow-IC run: INSUFFICIENT (2 pairs, need ≥15). 262 green.

**+ Dashboard day-readout + tick REPLAY (2026-07-24):** analyse-compute moved to `app/big_money_analysis.py`
(`analyse` / `analyse_frame`, shared by the script). Tape card now shows a "Day so far" readout for the
selected symbol (counts, day OI, build-up ₹ split, net conviction + positioning share, heaviest prices).
Sidebar *🎬 Replay big-money* scrubs a captured day (day + IST clock) — re-derives events tick-by-tick from
the raw archive up to the clock (`rederive_events(upto_epoch=…)`, `available_raw_days`) and renders the tape
+ readout as of that moment. Live + replay renders verified. 262 green.

**Remaining:** enable BSE (SENSEX); calibrate floors on a full session; RUN the flow-IC once sessions
accrue; burst-level directional tag (long vs short build-up, needs window Δprice); intraday (half→half)
flow test; calendar-spread detection; deferred phases (constituents, cross-book synthesis).

**SENSEX added (2026-07-22) but BSE-gated:** wired into floors/defaults/tape and resolves from the
scrip master (BSE_FNO, lot 20), but verify-live shows the websocket returns **0 SENSEX packets** on the
current token (a NIFTY control on the same connection ticks) — BSE live data is a separate Dhan
entitlement. Its 0-count is flagged ⚠️ in the health strip; it lights up automatically once BSE is
enabled. **Remaining:** enable BSE live-data for SENSEX; calibrate ₹ floors on a full session;
next-month roll (current-expiry thins pre-expiry); deferred phases (constituents, cross-book synthesis).



**Why:** the live complement to our stored-tick studies — surface **unusually large activity** in the
index futures in real time. Honest framing (the landmine): retail feeds are **aggregated broadcast**, so
we CANNOT see orders or participant identity — only large **prints** (single-tick LTQ) and **bursts**
(volume/OI clustered in seconds). So this is a *large-trade / unusual-activity tape*, an inference of big
money, NOT literal order tracking. A big print is also **direction-ambiguous** (a ₹cr sell could be a
hedge); the most we honestly add is **aggressor side** via best bid/ask (Lee-Ready). Report the flow,
never predict from it — same sign-free discipline as the dealer-sign landmine. Scope decided with the user:
**NIFTY + BankNifty CURRENT-EXPIRY FUTURES only** — options are deferred because you can't separate a
directional print from a hedge/spread leg in the chain; futures are one clean directional instrument each.

**Gate — DONE (2026-07-21):** live Market-Feed websocket entitlement CONFIRMED via `scripts/dhan_ws_probe.py`;
all tiers flow (Ticker/Quote/Full), little-endian, **LTQ (Quote) + 5-level bid/ask (Full) both available**, so
the ₹ filter AND aggressor-side are buildable. Endpoint/format/ids in the `dhan-live-ws-feed` memory.

**Architecture (decoupled — Streamlit can't hold a long-lived socket; reuse the "collector → file store →
dashboard reads" shape of the live pipeline):**
```
Dhan WS ─► collector (always-on) ─► append-only event store (JSONL/day) + in-mem ring ─► dashboard tape fragment
           decode → ₹/burst filter → aggressor tag              (tails last N events, auto-refresh, filters)
```

**Build (smallest-first):**
1. **`data/dhan_feed.py` — the WS client + decoder** (new; keep raw, no SDK, mirror `data/dhan.py` secret
   handling). `connect()` builds the v2 URL from `_secret` token + client id (NEVER logged), subscribes to the
   two futures from `near_month_future_id("NIFTY"/"BANKNIFTY")`. A `decode(frame)` for the binary packets
   (header @0, LTP float32 @8, little-endian — calibrate LTQ/volume/bid-ask offsets from the probe's raw hex
   FIRST, verify vs a live Quote/Full packet). Reconnect + daily-token-refresh loop (24h cap, same as REST).
   ⚠️ non-blocking recv with a drop/coalesce policy — packet rate is highest exactly during the bursts we care
   about (measure it live first, see VERIFY).
2. **`app/big_money.py` — the filter (pure, testable)**. `notional = ltp * ltq * lot` (lot from the resolved
   contract). Two filters: (a) **absolute single-print** ≥ a per-index ₹ floor (named constants, CALIBRATE on
   day-1 real tape — 1 NIFTY lot ≈ ₹15.7L, 1 BankNifty lot ≈ ₹17.4L, so a "big" print is tens of lots ≈ few
   ₹cr); (b) **relative burst** — Δvolume over a K-sec window ≥ X× the instrument's rolling-median trade size
   (catches sliced/iceberg orders). `aggressor(ltp, bid, ask)` → buy/sell-initiated (Lee-Ready; None if
   inside the spread). Every fired event records the raw fields that tripped it (make-state-visible / fail-loud).
3. **`scripts/run_big_money_collector.py` — the always-on process**. Wires client→filter→store: append each
   qualifying event to `data/journal/big_money/<SYMBOL>/<day>.jsonl` (gitignored) with the EXCHANGE tick
   timestamp (not receipt time; note latency), plus a small in-memory ring for freshness. `--dry-run` prints to
   console (the day-1 calibration run). Graceful on close/disconnect; logs packet-rate stats on exit.
4. **`app/simple_dashboard.py` — the tape fragment (read-only)**. A "🐋 Big-money tape" card that tails the
   event store (last N events), one timestamped row each: `HH:MM:SS · index · ▲buy/▼sell · N lots · ₹X.Xcr ·
   @price`, colour by side, newest on top. Filters row: index / min-notional / side. Auto-refresh like the
   other live panels. Purely a readout — **no verdict, no trade call** (decision-support; you confirm).
5. **Tests** (`tests/test_big_money.py`): `notional`/`aggressor` hand-checks; abs floor fires at threshold and
   not below; relative burst on a synthetic volume series; a decoder round-trip on a captured sample packet
   (hex fixture) so the offsets are pinned. Store read/write + graceful-missing-file.
6. **Docs:** `docs/big-money-tape.md` — the honest-framing preamble (prints not orders; direction-ambiguous),
   the filter definitions + calibrated floors, the field/format reference (from the probe), and the run recipe.

**VERIFY-live before trusting (never guess):** (a) tick-RATE during 09:15–15:30 IST — re-run
`dhan_ws_probe.py 30` live to size throughput/backpressure (closed-market probe can't show it); (b) the exact
Quote/Full field offsets past LTP (LTQ/volume/bid/ask) against a live packet; (c) re-resolve the futures ids
each session (they roll — the probe already does). Token 24h-capped → collector needs re-paste/reconnect.

**Out of scope (deferred, note so it's not a silent gap):** the options chain (hedge-vs-directional
unknowable); **next-month roll** — "current expiry only" thins in the last 1–2 days as liquidity rolls, so
late-cycle big prints migrate to the contract we're not watching (add auto-roll / next-month later); any
auto-execution (staged, human-confirm only — hard rule). Effort: med (new WS decoder is the real work; filter +
tape are small). Sign-free; decision-support only. Phasing: 1→2→(3 dry-run, calibrate floors)→4.

---

## 2026-07-21 IST — [W1] Expiry-week value overlay (upgrade the "5-day composite")  ⏳ TODO (visual only; Study #29 `docs/intraday-results.md`)

**Why:** Study #29 computed the value profile at the **option-expiry-weekly** frequency (Wed→Tue for a
NIFTY Tuesday expiry) and found the useful content is a *map*, not a signal: spot sitting **outside the
weekly value area** (+9 pp FAST-TRUE) and a wide **|pin − weekly-POC| gap** (monotone, IC +0.15) are a
mild trend-day tendency — but the combined cell is **n.s. (p=0.43)** and it's the same prior the blast
verdict already shows as PINNED/UN-PINNED. So the defensible dashboard change is to make the *weekly value
area itself* a cleaner reference layer, and **NOT** add any new verdict/trigger text. The dashboard already
has a "📊 5-day value (composite)" toggle (`simp_weekly`), but it's a plain trailing-5-days composite on the
base 5-pt bin — this task corrects it to the true expiry-week boundary and the session-comparable bin.

**Build (contained, purely visual — do NOT touch the blast verdict / `_pin_context_line`):**
1. **Factor Study #29's helpers into `app/market_profile.py`** (reuse, single source):
   - `expiry_week_days(symbol, source, today) -> list[str]` — trading days from **the day after the prior
     expiry through `today`**, bounded to ≤9 calendar days of `today` (skip feature-cache gaps). Find the
     prior expiry with `data.replay._expiry_cache(symbol)` (dte<0.6 set) and fall back to
     `data.expiry.next_weekly_expiry` when the cache doesn't cover recent days — same dual path
     `data.replay.is_expiry_day` already uses. Port `_week_days`/`WEEK_MAX_GAP=9` from
     `scripts/analyze_weekly_profile_blast.py`.
   - `weekly_bin(arr, base_bp, n_target=45)` — port `_weekly_bin`: coarsen to ~week_range/45 (≈10 pt for
     NIFTY) so `_count_modes`/VA read at session grain, not the 100+-bin jitter of the 5-pt bin.
2. **Rewire `overlay_profiles(..., weekly=True)`** (lines ~168-172): replace the `recent[-5:]` composite with
   `expiry_week_days(...)` — each prior week-day's full session (`_spot_for`) **plus today's developing bars
   ≤ now** (point-in-time; today's series is already fetched as `ts` for `today_prof`, reuse it), concat, and
   `day_profile(pd.Series(arr), bin_pts=weekly_bin(arr, bp))`. Keep the graceful-None contract (short/empty
   week → `weekly=None`). Add the week's day-count to the return dict (e.g. `"week_days": n`).
3. **Relabel the UI "5-day"→"week"** — the toggle text/help (`app/simple_dashboard.py` ~449-451: "📊 Weekly
   value (expiry week)"), the candle legend "5-day (5D·)" (~723-725), and the label prefixes inside
   `_range_candle` where the weekly VAH/POC/VAL are drawn dotted (grep "5D" in `_range_candle`, ~1200-1260).
   Keep the dotted style so week ≠ prior-day (dashed) ≠ today (solid).
4. **Docs:** `docs/simple-dashboard.md` §4.6 — one line that the composite is now the true expiry-week (Wed→Tue)
   value map, explicitly a **reference map, not a signal** (cite Study #29's n.s. combined result so nobody
   reads the overlay as a trigger).
5. **Tests (`tests/test_market_profile.py` or a new `tests/test_expiry_week.py`):** `expiry_week_days` returns
   Wed→Tue for a known expiry and drops a >9-day cache gap; `weekly_bin` coarsens a wide-range week and floors
   at `base_bp` for a narrow one; `overlay_profiles(weekly=True)` on a replay/stored day yields a `weekly` dict
   whose VAL≤POC≤VAH and `week_days≈5`, and `None` when there's <2 week-days.

**Explicitly out of scope:** any spot-vs-weekly-value or pin↔weekly-POC **distance readout / chip / verdict
line** — that would dress the n.s. Study #29 tendency as a signal (same reason #26/#28 stayed maps). Revisit
only if a future study makes the weekly distance significant. Effort: low–med (one overlay path + labels +
tests; no engine, no new capture). Sign-free; point-in-time; decision-support only.

---

## 2026-07-15 IST — [B1] Gamma-blast BACKTEST skill  ✅ DONE (2026-07-15 — `scripts/backtest_gamma_blast.py` on the 1-min entry model; verdict: real selector, NOT tradeable after spread — see gamma-blast-research.md §7e)

**Result:** built the 1-min crossing/trajectory primitives (`scripts/_blast1min.py`), the 1-min timing
study (§7d) and the backtest (§7e). The fast-TRUE entry model beats a naive baseline and is +3.2%/trade
at mid, but breakeven is ~2-3% half-spread and it's negative (−11.4% in the current Sep25+ regime) after
realistic expiry-afternoon fills. Honest use = a live confirmation-quality readout, not automation.
Follow-on left open: a straddle (directionless) variant, and a real bid/ask fill model if book data
appears.

## 2026-07-15 IST — [B1-orig] Gamma-blast BACKTEST skill (original spec)  ⬜ superseded by the DONE entry above

**Why:** we have detectors, IC studies, and a sign-free staying-power grade (`analyze_blast`,
Spearman +0.70, n=57, all in the current Sep25+ Tuesday era), but **no first-class backtest** of the
blast as a *tradeable* event with honest cost modelling. The payoff is lottery-shaped, so mean return
is meaningless — the skill must evaluate the full distribution.

**Build:** a reusable backtest skill/harness specialised for expiry-day gamma blast that models the
things that kill naive blast backtests:
- **Full payoff distribution**, not the mean: hit rate, win/loss payoff ratio, max adverse excursion,
  and how often the needed move actually arrived inside the stop (lottery accounting).
- **IV crush** — afternoon IV deflation that offsets gamma gains even when directionally right.
- **Fill realism** — expiry-afternoon spreads/depth blow out exactly when premiums explode; model
  explicit spread + slippage, not mid-price. Be suspicious of any edge living only in the last 15 min.
- **Expiry-structure era split baked in** (`app/expiry_regime.py`) — never pool across the 20-Nov-24
  and 1-Sep-25 breaks; treat Nifty-Tuesday (Sep25+) as the only live weekly venue.
- Two-model framing: a **vol-expansion regime detector** (directionless — straddle / staying-power)
  vs a **direction trigger** (futures OFI/ΔOI, VWAP break, zero-gamma cross — all found weak so far).
- Straddle-breakout proxy (compression → breakout of the ATM straddle's OWN intraday range) as the
  cleanest directionless tradable, sidestepping CE-vs-PE.

Effort: med–high. ⚠️ Direction is dead (research arc); the defensible version studies the hedging-flow
/ vol-expansion phenomenon with honest costs, NOT "buy cheap options and hope." Decision-support only.

---

## 2026-07-13 IST — [T1a] Regime → risk-posture overlay  ✅ DONE (2026-07-13 — `_regime_posture_note` card under the regime banner)

**Why:** the vanna gauge tells you the REGIME but not what to DO. Turn the one confirmed edge
(|vanna|→forward vol) into a decision. The dashboard currently stops at "SIDEWAYS/TRENDING".

**Build:** a `_regime_posture_section` (dashboard) keyed to `intraday_vol_regime` tag + exp_rv:
- **HIGH concentration** → calmer forward vol in the sample. Do not fade to the
  magnet because a label says positive gamma. Short strikes, if any, sit outside
  the gamma concentrations. Defined risk. The user confirms.
- **LOW concentration (trending/wild)** → respect the break: momentum/breakout, buy premium,
  stops BEYOND the walls, size DOWN.
- **MID** → neutral / mixed, no vol-regime tilt.
Feed both the live-intraday vanna regime AND the EOD `gex_history` regime (daily posture). Compact
"how to trade this regime" card; sign-free; decision-support ("you confirm"). Effort: low–med.

---

## 2026-07-13 IST — [T1b] DTE-aware regime metric (vanna + charm blend)  ✅ DONE (2026-07-13 — charm@≤1.5DTE / vanna else via `regime_now`; validated cross-book)

**Why:** Study #8 — **charm** is the best vol metric at **0-DTE** (IV-partial −0.20) while **vanna**
wins otherwise. A DTE-switched/blended gauge should beat either alone. Builds directly on done work.

**Build:** add a `charm` column to the feature cache (like `vanna`, in `day_features`); in
`_regime_stats`/`intraday_vol_regime` pick the metric by DTE (charm ≤1.5d, vanna else) OR blend the
two percentiles. Add `data_access._live_charm` (mirror `_live_vanna`) for the live value. VALIDATE
the blend's IV-partial beats vanna-alone cross-book (`greek_exposure_screen`) before wiring. Effort:
med (cache rebuild + live charm). ⚠️ still a vol-regime read, not alpha.

---

## 2026-07-13 IST — [T2] Live gamma-blast confirmation panel (wife's strategy)  ✅ DONE (2026-07-13 — live-confirmation readout: actionable bias + confirmation-lag state + fuel gauge)

**Why:** the blast score is **confirmation, not prediction** (#6) — so its value is a clean
real-time "is this break SUSTAINING?" readout as the 40-min unfolds. This is the wife's actual
trade; the live-confirmation UX *is* the product.

**Build:** enhance the dashboard gamma-blast section for LIVE use: the writer-covering **fuel gauge**
building over the window, the **TRUE/FAKE grade** with its confirmation-lag note (~5min violent →
~15–30min ordinary; early readings bias FAKE — NOT a pivot-entry trigger), the wall-ahead read, and
"what to watch next". Point-in-time, decision-support. Ties to `app/gammablast.py::blast_score`,
`docs/gamma-blast-score.md`. Effort: med.

---

## 2026-07-14 IST — [T6] Situational / scenario analysis (spot × levels)  ⏳ IN PROGRESS (spec: docs/scenario-catalog.md)

**Progress:** step 1 (cache enriched with `call_wall/put_wall/magnet/max_pain/gamma_flip` per stamp;
all 3 books rebuilt) ✅. Results so far — direction stays dead, consistent with the arc:
- **A1 magnet lock** ✅ NEGATIVE (Study #12): pin is stickiness not attraction (closer 39% < mirror 51%); undercuts A2.
- **B1/B2 wall bounce-vs-break** ✅ NEGATIVE cross-book (Study #14): no directional edge at the wall; only containment (= vol regime).
- **H blast compression** ✅ REFUTED (Study #13, user hypothesis): tight morning → QUIETER afternoon (ρ +0.45, persistence), not a coil; invert the filter.
- **Study #15 daily-move per bucket** ✅ wired to dashboard (expected-move badge, |vanna|-based).
- **I ORB × pin** ✅ NEGATIVE (Study #16, user scenario): pin doesn't flip ORB run; OR always breaks; "smaller OR → trendy" is a normalization illusion (absolute ρ(OR%,day-HL%)=+0.59, persistence); OR not special vs mid-day placebo.
- **Regime persistence** ✅ **POSITIVE cross-book** (Study #17): vol regime clusters (autocorr +0.26–0.36, ~1.9d runs); today's |vanna| forecasts tomorrow's move BEYOND realized vol (partial −0.15/−0.22/−0.18, survives DTE); label sticky 56–62% DTE-stripped. The gauge/badge earn their keep as a NEXT-DAY prior. → dashboard: carry regime into next session.

Remaining (lower prior): C1 flip regime, D1 magnet-pull sharpen, E1/E2 squeeze (vol), F1 max-pain
drift, G1 level×footprint. Direction stays dead (A1/B/H/I); the surviving value is VOL — now with a
confirmed NEXT-DAY persistence edge (#17). Next: to-expiry vanna-VRP (T4b remainder).


**Why:** direction is dead POOLED (research-arc conclusion), so STOP pooling — slice to the narrow
geometry+regime cells (spot vs wall / magnet / max-pain / pin / flip at a given stamp) where a
conditional edge might survive. Turns the user's expiry-pin/blast-target intuition into pre-registered,
holdout-guarded tests. Sign-free (geometry supplies direction, not the dealer-sign landmine).

**Build (full spec + scenario catalog A–G in `docs/scenario-catalog.md`):**
1. Enrich the 5-min feature cache with per-stamp `call_wall/put_wall/magnet/max_pain/gamma_flip`
   (one extra pass in `day_features`; `peak_oi_strike` stays). Rebuild caches.
2. Shared protocol: point-in-time, terminal `converged`-to-target at **15:15 spot**, distance
   normalized by `σ√T`, day-detrend, race the neutral anchors, DTE+time-of-day controls, last-30%
   walk-forward holdout, dead scenarios recorded.
3. Order: A1→A3→A2 (pinning, highest prior) → B1-vs-B2 (wall bounce-vs-break, best directional shot)
   → D1/E (sharpen) → C1/F1/G1 (gated). One small script each → `docs/intraday-results.md`.
   Survivors graduate to a dashboard "situation" chip. Effort: med (cache rebuild + per-scenario scripts).

---

## 2026-07-13 IST — [T4] Vol-risk-premium study — IV vs vanna-predicted RV (daily/overnight)  ⏳ TODO (PRIORITY — the one vol-alpha shot)

**Why:** the INTRADAY straddle was priced-in (#6b/#7), but the **daily/overnight variance-risk-
premium conditioned on the opening vanna regime** is UNtested. If IV systematically over/under-
prices the vanna-predicted realized vol, that's a genuine vol trade (unlike the intraday version).

**Build:** per day, opening ATM IV vs the subsequent realized vol (to next day / to expiry), bucketed
by opening **vanna** regime tercile; the systematic **IV − RV gap by regime**, net of a straddle-cost
proxy. **MUST control DTE** (the #7 confound: vanna∝1/σ, |GEX|∝1/√T ≈ near-expiry — hold DTE fixed).
Point-in-time. Honest prior: could well be null (efficient), but it's the one place a tradeable vol
edge could hide. Effort: med.

---

## 2026-07-13 IST — [T4b] Vanna-VRP follow-up — is the residual TRADEABLE?  ✅ DONE (2026-07-14 — NO at BOTH horizons: intraday #11b + hold-to-expiry #18; partial|IV+DTE ≈ 0. Closed.)

**Why:** Study #11 (T4) found the FIRST vol result to survive IV+DTE controls in the right direction
AND replicate cross-book: partial(|vanna|, VRP | IV+DTE) = +0.11 NIFTY / +0.24 BankNifty (higher vanna
→ IV under-prices the calm → larger VRP → short-vol tilt). Modest but real — the only tradeable-vol
candidate left.

**What to do:** (1) ROBUST realized vol (BankNifty mean RV is bad-tick-corrupted — median/winsorized,
or drop the ~1.8% corrupt-spot days) so the P&L magnitude is trustworthy; (2) a proper regime-timed
**short-vol backtest** — sell the ATM straddle on high-vanna days (matched IV+DTE bucket), hold to
close/expiry, **net of a realistic cost + slippage**; (3) OUT-OF-SAMPLE / walk-forward split;
(4) also a daily/overnight horizon (not just entry→close). If the cost-adjusted, OOS edge holds, this
is the first genuinely tradeable thing in the project. Honest prior: modest, may not clear costs.

---

## 2026-07-13 IST — [T3] Self-healing capture via nightly Dhan backfill  📋 BACKLOG

**Why:** we keep LOSING live sessions (S0-machine sleep, no wake-timer) — but it's now RECOVERABLE
since Dhan has the full 1-min history. Stop losing data.

**Build:** a nightly job — detect missing intraday days in the journal, backfill them from Dhan
(`scripts/dhan_backfill.py` incremental) + append to the feature caches (`intraday_features.build`
appends; vanna/charm columns). Windows scheduled task / cron. Self-heals gaps so the gauge + studies
never have holes. Also a lightweight nightly `intraday_features.build(store)` to keep caches current.
Effort: med. (Deprioritized vs T1/T2/T4 per user, but high value — chronic pain, now cheap.)

---

## 2026-07-13 IST — [T5] Order-flow / tick-data direction spike (Dhan websocket)  📋 BACKLOG (future)

**Why:** every OI/options/futures/greek angle is EXHAUSTED for direction (#1–#10). The ONLY data
class with a fresh, unpriced directional prior is **trade-level order flow** (aggressor side, sweep
detection, depth imbalance) — which we don't have yet.

**Route (user-confirmed):** Dhan API offers a **WEBSOCKET** market-data feed → the path to tick/depth.
Scoped feasibility spike: connect the Dhan websocket, capture ticks/depth for Nifty futures + ATM
options, and test whether order-flow imbalance / sweep detection shows directional IC beyond price
momentum. Big lift, uncertain payoff — a spike, not a commitment. **Future** (after T1/T2/T4).
Effort: high.

---

## 2026-07-13 IST — Charm vs Gamma-blast analysis  ✅ DONE (2026-07-13 — NULL: charm doesn't call blast direction; docs/intraday-results.md #10)

**Why:** **charm** (∂delta/∂time) is the dealer greek that drives **expiry-day directional drift**
(forced one-way re-hedging as deltas decay — "charm flow"), and it's a near-expiry-dominated signal
(∝ 1/T). The **gamma blast** is an expiry-day strike-crossing phenomenon. So charm is the most
theory-plausible thing to connect to blast *direction/continuation* — the one dimension the blast
score can't predict (Study #6: direction is a coin flip). In the greek screen charm was also the
strongest vol metric on NIFTY (IV-partial −0.228) but inconsistent on BankNifty. See
`docs/vanna-charm.md`, `docs/intraday-results.md` #5/#6/#8.

**What to test (point-in-time, leak-free — reuse `scripts/analyze_dhan_blast.py` /
`build_blast_dataset.py` crossing machinery):**
1. Charm EXPOSURE (signed, model-A) and its 15-min change at/just-after a crossing → does it
   predict the *direction* of continuation (up vs down) or the true-vs-fake blast, beyond momentum?
   (The signed test the blast score fails — charm is the candidate because it's directional flow.)
2. Charm at KEY LEVELS (the crossed strike / wall ahead) vs aggregate charm.
3. Does charm-flow SIGN align with the blast direction on true blasts specifically (near expiry)?
4. Add `CHARM` (and its Δ) as a feature to the blast ML dataset and re-check the walk-forward AUC /
   EV — does the one greek with a directional mechanism move the needle?

**Honest prior:** low (direction has been null everywhere, incl. charm's signed→ret ~0 in the
pooled screen) — BUT charm × expiry-day × blast is the one untested *interaction*, and charm's
1/T concentration means a pooled test dilutes it. Condition on DTE≤1.5 (0-DTE). Sign-dependent
(dealer-sign landmine) → report |IC| + both signs. Exploratory; confirmation-not-alpha unless it
clearly beats momentum.

---

## 2026-07-03 03:33 IST — Intraday mini-buildup streak detector (5/15-min) + dashboard list  ✅ DONE (2026-07-03)

**Built:** `app/oi_footprint.py::intraday_buildup_streaks` — per-strike consecutive-snapshot
mini build-up (ΔOI step + premium %-step via the `classify_buildup` matrix), flags a strike
when one colour runs ≥3 (green = long build-up, red = writing). Analysis first: step
magnitudes scale with capture cadence (15-min day ~4–7k ΔOI/step & ~3–4%/step; finer day
~540 & ~1.5%), so absolute per-step thresholds don't generalise → **direction + run length
is the filter**, with `min_cum_doi` (default 2000) killing deep-OTM illiquid noise and
`tol_prem_pct` (0.5%) screening flat premiums. Dashboard `_buildup_streaks_section` lists
strike/side/streak/cum-ΔOI/cum-prem%/window + a **directional read** (call-green=bullish,
call-red=resistance, put-green=bearish/hedge, put-red=support) and a **⏺ live** marker when
the run is ongoing at the latest snapshot. Tests in `tests/test_oi_footprint.py` (red/green
runs, run<3, illiquid floor, OI-drop break). Validated on 06-29/07-01/07-02 captures.

**Cadence note:** baseline capture is 15-min (~24 steps/day) so the detector runs at 15-min;
a true 5-min view needs tightening the capture cadence (deferred — 15-min already flags the
sustained builds). Momentum companion to the vs-prev-close footprint tag. Sign-free.

<details><summary>original spec</summary>

**Why (user request):** the vs-prev-close build-up tag says *net* build since yesterday,
but not the intraday *momentum*. Compute a per-snapshot (5-min or 15-min) MINI build-up per
strike from the intraday deltas — ΔOI and premium-%-change *between consecutive snapshots* —
classify each interval green (long build-up) / red (short/writing) / neutral, and when a
strike prints **3 greens in a row** (accelerating long build) or **3 reds in a row**
(accelerating writing), surface it on the dashboard as a flagged list. Needs data analysis
(pick cadence, thresholds, and whether % or absolute).

**What to build:**
1. Intraday per-strike deltas from the `oi_strikes` day frame (consecutive snapshots):
   `ΔOI_step = oi[t] − oi[t-1]` (or use `call_doi`/`put_doi` step-over-step) and
   `prem_pct_step = (ltp[t]/ltp[t-1] − 1)`. New pure fns in `app/oi_footprint.py`
   (e.g. `intraday_steps(day_df, side, cadence)` + `streak_flags(steps, run=3)`), tested.
2. Classify each step via the existing matrix (`classify_buildup(ΔOI_step, prem_step)`):
   green = "long build-up", red = "writing (short build-up)". Count consecutive runs.
3. Cadence: the 15-min time-series already gives ~24 steps/day; resample/aggregate for a
   5-min view only if the capture cadence supports it (it doesn't at 15-min baseline — decide
   whether to tighten capture or offer 15-min only). ANALYSE first on captured `oi_strikes`
   history (06-29 onward) to set sensible ΔOI/premium thresholds (avoid noise flags).
4. Dashboard: a "🔥 Intraday build-up streaks" list under the footprint — strike, side,
   3× green/red, cumulative ΔOI + premium move over the run. Sign-free, exploratory.
5. Point-in-time / fail-soft; degrade cleanly when a day has < a few snapshots.

**Scope note:** builds directly on the exact-tag work below (same `classify_buildup` matrix,
now aligned). Analysis-first (thresholds), then wire. See `docs/participant-oi.md` sibling
for the footprint context.

</details>

---

## 2026-07-02 03:12 IST — Precise per-strike build-up classification (fix the expiry/window mismatch)  ✅ DONE (2026-07-03, live-verified)

**Built:** NSE payload carries per-side `change` (LTP vs prev close) alongside
`changeinOpenInterest` — so `data/nse.py::per_strike_nearest_changes` captures per-strike
**nearest-expiry** OI-change + premium-change, both vs prev close (aligned expiry + window).
Wired through `app/data_access.py` (enrich `oi_strikes`), `app/timeseries.py`
(`STRIKE_FIELDS` + `build_strike_rows`, back-compat), `app/oi_footprint.py::top_builds`
(passthrough `doi_near`/`dprem`), and `app/dashboard.py::_oi_footprint_section` (prefer the
EXACT tag from a single snapshot, fall back to the intraday first→latest method for old
CSVs; caption states which). Tests in `tests/test_oi_footprint.py` (fixture: CE 23550 →
+221 / −76.25 → writing). The build-up tag is now exact for live snapshots, no ≥2-snapshot
dependency.

<details><summary>original spec</summary>

**Why:** the dashboard "Top support/resistance builds" tables (footprint section,
`app/oi_footprint.py::classify_buildup`) tag each strike as writing / long build-up /
short covering / long unwinding from the **price+OI matrix**. It works, but the tag is
**approximate** because of a data mismatch:
- **ΔOI** (`call_doi`/`put_doi`) is `changeinOpenInterest` **summed across ALL expiries**,
  measured **vs previous close**.
- **Premium change** is computed intraday (**first → latest snapshot LTP**), and the
  captured `call_ltp`/`put_ltp` is the **NEAREST expiry only**.
So price and OI use different expiries AND different windows. Fine for near-money,
nearest-expiry-dominated strikes; wrong for multi-expiry or far strikes.

**Fix (schema addition to the capture):** log, per strike, the fields that let price and
OI share the same expiry + window:
1. Capture **per-strike, per-nearest-expiry OI + OI-change-vs-prev-close** (not summed
   across expiries) so ΔOI matches the LTP's expiry. OR additionally store the
   **previous-close premium** per strike so "premium change vs prev close" lines up with
   `call_doi` (which is already vs prev close).
2. Then `classify_buildup` uses same-expiry, same-window ΔOI + Δpremium → an exact tag.
3. Update `STRIKE_FIELDS` (`app/timeseries.py`) + `per_strike_oi`/`nearest_expiry_premiums`
   (`data/nse.py`); keep back-compat with existing CSVs (missing columns → degrade to the
   current approximate tag).

**Scope note:** low urgency — the approximate intraday tag is usable now. Do this when
tightening the footprint, ideally together with the participant-OI work below (both are
positioning-precision improvements). See `memory/positioning-footprint-suite.md`.

</details>

---

## 2026-07-02 02:55 IST — Add NSE participant-wise OI capture  ✅ DONE (2026-07-02, live-verified)

**Built:** `data/nse.py::parse_participant_oi`/`fetch_participant_oi` (fail-soft, index
cols only), `app/participant_oi.py` (journal I/O + `derive_signals` + `capture_latest`
walk-back), `scripts/log_participant_oi.py` (daily post-close task), dashboard
`_participant_oi_section` (FII net fut, option-writing, dealer-sign hint), and
`tests/test_participant_oi.py`. Live capture 2026-07-02 confirmed: **Client is the
dominant index-option writer → supports the INVERTED dealer-sign lean (model B)**,
corroborating `memory/both-signs-dealer-sign-read`. Wire a Windows scheduled task
~18:30 IST to `scripts/log_participant_oi.py` for guaranteed daily history.

<details><summary>original spec</summary>

**Why:** the direct **big-player positioning** read (the thesis in
`memory/positioning-footprint-suite.md`): GEX/VIX are *responses* to news; participant
OI is *who is actually positioned*. It is **sign-free** and is the cleanest shot at
finally resolving the **dealer-sign landmine** (it tells us WHO writes index options —
Client/DII/FII/Pro long vs short calls/puts).

**Endpoint — VERIFIED LIVE 2026-07-01 (works; the www host 404s):**
```
https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_DDMMYYYY.csv
```
- Fetch with the primed session (`data.nse._primed_session`); host is `nsearchives.nseindia.com`.
- Daily EOD file, one per trading day. `DDMMYYYY` (e.g. `fao_participant_oi_01072026.csv`).
- There is a sibling `fao_participant_vol_DDMMYYYY.csv` (volume) — same shape.

**File format (verified, 01-Jul-2026):**
- Line 0: title `""Participant wise Open Interest ... as on Jul 01, 2026""` (has stray quotes; skip).
- Line 1: header (note some columns have trailing spaces — strip). **Column order:**
  `Client Type, Future Index Long, Future Index Short, Future Stock Long, Future Stock Short,`
  `Option Index Call Long, Option Index Put Long, Option Index Call Short, Option Index Put Short,`
  `Option Stock Call Long, Option Stock Put Long, Option Stock Call Short, Option Stock Put Short,`
  `Total Long Contracts, Total Short Contracts`
  ⚠️ note the option order is **Call Long, Put Long, Call Short, Put Short** (not Call L/S then Put L/S).
- Rows: `Client`, `DII`, `FII`, `Pro`, `TOTAL` (numbers are contract counts).

**What to build (next session):**
1. `data/nse.py`: `fetch_participant_oi(date)` (build URL, primed GET, return text) +
   `parse_participant_oi(text) -> dict` keyed by participant (Client/DII/FII/Pro) with
   the index F&O fields. Focus on **index** columns (Future Index Long/Short,
   Option Index Call/Put Long/Short); stock columns are out of scope for now.
   Fail-soft (best-effort, like VIX/USDINR) — a miss must not break a GEX snapshot.
2. Derive per-participant signals: net index-future position (Long−Short), and net
   option-writing (short calls+puts − long) — i.e. **who is net SHORT options** (the
   dealer-sign clue). FII net fut, Pro/Client option-writing skew.
3. Capture: it's DAILY (not intraday), so a once-a-day fetch (piggyback the last
   session capture, or a separate small daily task) into a new journal
   `data/journal/participant_oi/<SYMBOL-agnostic>/YYYY-MM-DD.csv` (participant data is
   market-wide, not per-index — store once, not per index).
4. Dashboard readout: "Big players today — FII net futures {+/-}, option-writing by
   Client/Pro/FII" + a **dealer-sign hint** (if Client/Pro are the dominant option
   writers → supports the inverted-sign lean). Sign-free.
5. Tests: hand-check `parse_participant_oi` on the captured sample (Client row:
   Future Index Long 251066 / Short 63543; Option Index Call Long 2163161 ... etc.).

**Caveats:** EOD only (T+0 after close), market-wide (not Nifty-vs-BankNifty split),
index+stock combined file (use the *index* columns). Cross-reference with
`[[both-signs-dealer-sign-read]]` to see if participant option-writing corroborates
the inverted dealer sign.

**Sample row captured (Client, 01-Jul-2026):** FutIdx L 251066 / S 63543 ·
OptIdx CallL 2163161 / PutL 1888743 / CallS 2028147 / PutS 2586510.

</details>
