# TO-DO / backlog

Running backlog of planned work. Newest entries on top. Each entry is timestamped
(IST). Decision-support project — verify-live before trusting NSE data.

---

## 2026-07-13 IST — [T1a] Regime → risk-posture overlay  ✅ DONE (2026-07-13 — `_regime_posture_note` card under the regime banner)

**Why:** the vanna gauge tells you the REGIME but not what to DO. Turn the one confirmed edge
(|vanna|→forward vol) into a decision. The dashboard currently stops at "SIDEWAYS/TRENDING".

**Build:** a `_regime_posture_section` (dashboard) keyed to `intraday_vol_regime` tag + exp_rv:
- **HIGH concentration (calm/pinning)** → mean-revert: fade extremes toward the magnet, sell
  premium near the walls, tighter stops, size UP.
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

**Progress:** step 1 (cache enriched with `call_wall/put_wall/magnet/max_pain/gamma_flip` per stamp,
NIFTY rebuilt 1239 days) ✅. **A1 (magnet lock) ✅ DONE → NEGATIVE** (Study #12): the pin is
stickiness not attraction — no pull to the pin from a distance (closer 39% < mirror 51%), which also
undercuts A2. Next: rebuild NIFTY_M/BANKNIFTY caches with levels, then **B1-vs-B2** (wall bounce-vs-
break — the best remaining directional shot).


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

## 2026-07-13 IST — [T4b] Vanna-VRP follow-up — is the residual TRADEABLE?  ✅ MOSTLY DONE (2026-07-13 — intraday NO, docs #11b; only the to-EXPIRY hold remains, low prior)

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
