# UI Migration Issue Register & Context Reference Guide

> **Purpose**: Regression register for the Cubera GEX cockpit. The web UI (`web_server.py`, `app/static/index.html`, `app/static/js/cockpit.js`) is the only decision screen. The Streamlit apps were removed. Do not copy an old Streamlit label back onto the web page.
>
> Read the **Current rule** column. The **Incident** column is history: it says what broke, not what to build next.
>
> Status: **Holds** means the current tree matches the rule. **Open** means the rule is current and the code still breaks it. Updated 24 Oct 2026, after the book-balance pass and the replay/simulator cleanup.

---

## Current product

One GEX convention, `short_call_short_put`:

- Both legs are stored negative. Walls are the largest call-gamma and put-gamma magnitudes. The flip and the regime total are call magnitude minus put magnitude.
- POSITIVE means call-heavy. NEGATIVE means put-heavy. The sign is not a fade or a run. Absolute GEX is the volatility context.
- Max pain is the strike that minimises ITM payout. It is a settlement strike, not a pin.
- A gamma strike is concentration. Do not write that dealers will defend it. Open interest is not buyer-versus-seller flow.
- Eight book-balance rows are always shown and the weights still sum to 10. Only skew (`s3_iv_skew`) and India VIX or near-ATM IV (`s8_iv_rank`) move the score. PCR, the gamma-strike midpoint, max pain, and OI changes are facts with zero contribution. If India VIX last is missing, the row says ATM IV. Bands: below 12 → +1, above 22 → −1.
- Lots, confirmed 24 Oct 2026: Nifty 65, every Tuesday. Bank Nifty 30, last Tuesday of the month. Sensex 20, every Thursday. A holiday expiry is the previous trading day. Never choose a lot or an expiry weekday with a substring check (`"NIFTY" in "BANKNIFTY"` is true).
- Paper only. The user confirms. A paper ticket is refused when a leg has no chain last or mid. Do not invent a premium from spread width.
- No simulator flag on this process. `config.SIMULATION_MODE` is gone. Production does not read `simulation_mode`, and it has no branch that writes synthetic bars, signals, or a replay clock. The next replay or simulator is a separate application on its own port. It must not write this process’s SQLite files.
- Left in place because they are not that application: `commentary/qa/replay.py` (commentary QA harness), `app/blast_study.py` `replay_series` (the live study card’s point-in-time series), `data/session_flags.py` (expiry and blast labels), and `analytics/position_context.py` (participant holdings and one prior value-area line). `sentinel/feed/demo.py` only loads the live feed or the last EOD snapshot.

Session clock for F&O quotes is `config.is_market_open`: 09:15–15:40 IST, weekdays. Cash continuous close is 15:30. Do not freeze F&O at 15:35 or at 15:30.

---

## 1. Issue register

| # | Status | Domain | Incident (do not reintroduce) | Current rule |
|---|---------|--------|-------------------------------|--------------|
| **1** | Holds | Target engine | The first web pass omitted the strike ladder, targets, stops, and touch probability. | `/api/target-engine` returns the ATM ±5 ladder (targets, stops, touch probability, IV, delta) plus ranked candidates and the feature table. A custom strike belongs in the repricer. There is no second greek board. Entry is a paper ticket with a chain price and a confirm. There is no live one-click order. |
| **2** | Holds | Cockpit tape | The pre-open banner and the alert stream were missing. | Keep the pre-market state and the tape under the levels. Headlines describe wall location and session phase. They do not call a pin, a blast, or a dealer defense. |
| **3** | Open | Options analytics | The first web pass had no KPI strip and no skew curve, and an early port labeled the cards “Max Pain Pin” and “IV Rank 30D”. | Six cards: ATM IV, PCR as an open-interest mix, max pain as a settlement strike, a 10–25% IV band map (this is not a 30-day rank), India VIX as an em dash when `last` is missing, and the book-balance score. The skew curve uses chain quotes. Do not draw a synthetic smile or fall back to 0.018 skew. The page still seeds `+1.8%` and `₹23,146.2` until the fetch paints. |
| **4** | Holds | Off-market reads | Early endpoints returned an empty error whenever `is_market_open()` was false, including for the GEX book. A later pass also let `simulation_mode` keep recommendations running off hours. | Cockpit GEX, walls, parity, the ladder, and the scorecard load the latest stored snapshot when the market is closed. Recommendations do not. `/api/recommendations` returns Session Closed and empty clusters whenever the session is closed. There is no flag that bypasses that. A missing snapshot spot is `0`, not a stand-in price. The notice says 09:15–15:40 IST. |
| **5** | Holds | Dhan calls | Background loops queried the broker on nights and weekends. | Do not call Dhan order or quote endpoints unless `config.is_market_open()` is true (09:15–15:40 IST). Public news RSS may still run. Startup skips chain ingest when the session is closed. |
| **6** | Open | Fabricated numbers | Demo seeders and a price-jitter timer wrote fake spot and fake rows into the live page. `simulation_mode` used to turn the synthetic bar writer back on. | No random drift and no invented premium, IV, VIX, parity row, spot, touch probability, or scorecard seed. A missing value is an em dash or a refusal. This process never writes a synthetic sentinel session. `seed_sentinel_demo_if_needed` loads the live feed, else the EOD snapshot, else writes nothing. Still open: the HTML seeds, the IV and spot fallbacks, the recommendation serializer defaults, and the execute-route defaults in section 4. |
| **7** | Holds | Header clock | The websocket and the poll used different datetime formats and overwrote each other. | User-facing status, cockpit, and tape stamps use `%H:%M:%S IST`. The client strips a redundant `IST` before appending ` IST`. `/api/health` uses a full datetime and is not the header clock. A snapshot from another calendar day may include the date via `_fmt_snapshot_as_of`. |
| **8** | Holds | Chart time axis | Lightweight Charts drew UTC hours, 5h 30m behind IST. | Route chart epochs through `_to_ist_chart_timestamp()` (`+19800` seconds). The live tick path in `cockpit.js` adds the same offset. Hover formatters read `getUTCHours()` and `getUTCMinutes()` on that shifted time. |
| **9** | Holds | Sentinel profiles | Full, Pure, CVD, and Trend looked the same, and walls sat off scale. | The four profiles toggle series visibility. Any series that draws a call wall, put wall, or flip attaches `autoscaleInfoProvider`. |
| **10** | Holds | Sentinel popout | `/chart/sentinel` ignored the active profile. | The popout URL carries `profile`. The page has the same four profiles, VWAP, EMAs, delta, CVD, and walls as the embedded tape. |
| **11** | Open | Repricer expiry and presets | The repricer used a stale snapshot expiry, and Bull/Bear/Base looked up `te-sl-spot`. | Resolve expiries with `get_sidebar_active_expiries` and drop dates that have already passed. The spot-stop input id is `te-input-sl-spot`. Presets write that field in both calculator modes. The resolver and the input id hold. The empty-list fallback in the repricer is still the string `2026-10-19`, and `/api/recommendations/execute` still defaults the leg expiry to `2026-10-15`. |
| **12** | Holds | Pre-market shock matrix | The shock matrix was a tall static block. | It is `<details class="collapsible-matrix">`. `ontoggle` keeps the open state across refresh. |
| **13** | Holds | Pinned and fresh setups | Audits checked that the key existed and missed the empty branch and the fresh-idea branch. | Aggregate SQLite bookmarks with recommendations triggered inside 600 seconds. Render the empty state when both counts are zero, and mark cards PINNED or NEW. Verify both branches. Paper confirm, not a live order. |
| **14** | Open | Cockpit chart | The cockpit tab had no intraday chart. An interim design offered a TradingView widget toggle beside the GEX canvas. | The cockpit chart is the GEX canvas at 1m, 5m, and 15m, with walls, VWAP, EMAs, a crosshair HUD, and resize. `renderCockpitChart` forces `gex_canvas`. `/chart/tradingview` still exists as a popout. That popout sets `allow_symbol_change: true`. The embedded widget sets it false. Do not bring the dual-engine toggle back onto the cockpit tab. |
| **15** | Holds | Alert tape | The tape stuck on BIG MOVE COMING because a blanket NEGATIVE rule ran first, and a breadth rule asked for 28 declines from a 14-sector set. | Production alerts come from `analytics/market_alert.py`: pre-open, session closed, magnet side, wall test, wall breach, between walls, unclear geometry, no spot. Do not restore BIG MOVE COMING, STRONG DOWNTREND, a blanket `regime === "NEGATIVE"` rule, or a bull/bear PCR divergence. Breadth, if shown, uses a threshold the sector set can actually reach. A commentary-log test still posts the old badge as a fixture. That fixture is not the live tape. |
| **16** | Holds | Outcomes and lifecycle | Forward returns were never evaluated, recommendations stayed ACTIVE across days, and a gamma-squeeze idea used a deep OTM wall. | `evaluate_signal_outcomes` runs from the live sentinel sync and tick path. `evaluate_recommendation_lifecycle` runs on the recommendation serialize path and resets a fresh day’s status. A gamma-squeeze wall is used only when it sits within six strike steps of spot. The old counts (193/199 ACTIVE, 17000 PE) are the incident, not the current book. |
| **17** | Open | Symbol races | A slower response for the previous index overwrote spot and the chart, and the TradingView widget restored a cookie symbol. | Discard an in-flight REST body when `data.symbol` or the requested symbol is not the active index. `handleLiveTick` requires a strict symbol match. On index switch, clear the forming bar and the HUD. `allow_symbol_change` is false on the embedded widget. It is still true on `/chart/tradingview`. Uvicorn `reload_excludes` includes `*.db`, `*.db-*`, `data/*`, and `*.log`. |
| **18** | Holds | Book balance | An 8-signal score treated PCR, walls, max pain, and OI as directional points, and copy called max pain a pin. | Follow `analytics/directional_bias.py` and the current-product section above. Web options analytics shows contribution per row. The Streamlit screens that carried the older IOAS wording were removed. Do not port that wording back. |
| **19** | Holds | Replay and simulator | A stored-clock replay, a scenario simulator, a `simulation_mode` flag, a Streamlit demo checkbox, and a big-money replay clock lived in this process. | This process has no simulator flag, no synthetic-bar writer, and no Streamlit control that loads a fixture or scrubs a stored day. Do not add `simulation_mode`, `SIMULATION_MODE`, `reseed_sim`, `step_sentinel_live_feed`, “Demo data (offline)”, or the big-money clock back. The next replay or simulator is a separate application on its own port and must not open this process’s SQLite files. The commentary QA harness, the blast-study series, session flags, and the position-context tables stay. |
| **20** | Open | Paper ticket and lots | Sample P&L, lot 25, and a premium of 0 on quick-close were shown as if they were the book. | Lot size comes from `config.INDICES`. The web journal uses that lot. `POST /api/journal/paper-structure` returns 409 when there is no structure or a leg has no chain last or mid. That route holds. `POST /api/recommendations/execute` still defaults a missing premium to `100`, a missing strike to `24500`, and books the trade. The client’s initial `state.spot` is `24500` before the first snapshot. |

---

## 2. What the five audit passes mean now

These passes are history. The rule to follow is section 1, not the design named in the old phase notes.

### Phase 1: First parity pass

Brought over the ladder, the pre-open banner, the sentinel canvas, and the six analytics cards. The card names from that pass (Max Pain Pin, IV Rank 30D, a synthetic 25Δ smile) are retired. See issues 1, 2, 3, and 18.

### Phase 2: Off-market feed and Dhan gating

Stopped empty GEX responses and stopped broker polling when the session was closed. Recommendations are the exception: they stay closed off hours. The written window 09:15–15:30 is retired. See issues 4 and 5.

### Phase 3: Zero-fabrication

Removed price jitter. The synthetic sentinel writer and its `simulation_mode` switch were removed from this process on 24 Oct 2026. See issues 6 and 19.

### Phase 4: Chart clock and sentinel popout

Added the +19800 second shift and the four-profile `/chart/sentinel` page. See issues 8, 9, and 10.

### Phase 5: Target-engine controls and the dual pipeline

Added horizon, scoring mode, the three scenario cards, and the pinned-plus-fresh split. Touch probability stays. A live one-click order does not. See issues 1 and 13.

---

## 3. Rules for the next change

1. **IST only.** Server times use `Asia/Kolkata`. Chart epochs go through `_to_ist_chart_timestamp()`. Hover formatters use `getUTCHours()` and `getUTCMinutes()` on the shifted value.

2. **No synthetic number on the decision screen.** No drift, no invented premium, IV, VIX, parity row, spot, or scorecard seed. A missing value is an em dash or a loud empty state. This process does not generate sentinel bars. `/api/sentinel-tape/reseed` accepts `sync_live` and `load_eod` only.

3. **Sentinel popout matches the embedded tape.** A wall, overlay, or profile added to one is added to the other. Both support Full, Pure, CVD, and Trend.

4. **Walls autoscale.** A Lightweight Charts series that draws a call wall, put wall, or flip attaches `autoscaleInfoProvider`.

5. **Off-market split.** Cockpit book data shows the latest stored snapshot. Recommendations return Session Closed and empty clusters whenever `config.is_market_open()` is false. There is no flag that bypasses that.

6. **Check both branches.** A recommendations change is done only after the empty state (`pinned + fresh == 0`) and the populated state are both exercised. Key presence is not enough.

7. **Mount the canvas before the fetch.** Create the Lightweight Chart and the candle series before awaiting `/api/sentinel-tape`, so a live tick can call `series.update()` while history is still loading.

8. **One book.** `short_call_short_put` only. POSITIVE is call-heavy and NEGATIVE is put-heavy. Only skew and India VIX or ATM IV move the book-balance score. Max pain is a settlement strike. Walls are concentration. Stops sit beyond the gamma strikes, never on the magnet. No second sign model, no US pin playbook, no SPX multiplier.

9. **Symbol isolation.** Discard an in-flight body whose symbol is not the active index. Reset the forming bar and the HUD on index switch. Every TradingView widget, embedded or popout, sets `allow_symbol_change: false`.

10. **Paper only.** No order leaves the process. A paper ticket uses the config lot and a chain last or mid. Refuse the ticket when a leg has no price.

11. **No simulator inside this process.** Do not read or branch on `simulation_mode`. Do not restore the deleted clock, simulator, day player, or synthetic bar writer. A future replay or simulator is a separate application on another port and does not write this database. Do not delete the commentary QA harness, the blast-study series, session flags, or the position-context tables while keeping the production book clean.

12. **Lots and expiries are named.** Nifty 65 Tuesday, Bank Nifty 30 last Tuesday, Sensex 20 Thursday, holiday to the previous trading day. No substring match on the symbol.

---

## 4. Open gaps after the 24 Oct 2026 re-audit

These are the places the current tree still breaks a rule above. They are not permission to change the rule.

| Gap | Where | Rule |
|-----|--------|------|
| HTML seeds `₹23,146.2` on the ladder and the expected-move line, and `+1.8%` on 25Δ skew | `app/static/index.html` | 2 |
| Client spot starts at `24500` before a snapshot arrives | `cockpit.js` `state.spot`, and the target-engine preset fallback | 2 |
| ATM IV falls back to `0.14`; spot falls back to `24500` / `51500` / `80000` | `_extract_active_atm_iv`, `_extract_active_spot` | 2 |
| Reprice uses futures, then spot, then `24500`, and an empty expiry list becomes `2026-10-19` | `/api/target-engine/reprice` | 2, 12 |
| A recommendation with no stored touch probability, EV, or spread is filled with `0.65`, `0.35`, `18.5`, a 100-point wing, debit = 62% of LTP, and R:R `1 : 1.6` | `_serialize_trade_idea` | 2, 8 |
| Missing score history is two invented rows, including “absorption at Put Wall” and “distribution at Call Wall” | `_serialize_trade_idea` `score_history` | 2, 8 |
| Execute books premium `100`, strike `24500`, and expiry `2026-10-15` when the body omits them | `POST /api/recommendations/execute` | 2, 10 |
| TradingView popout allows a symbol change inside the iframe | `/chart/tradingview`, `allow_symbol_change: true` | 9 |
