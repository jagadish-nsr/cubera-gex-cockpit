# Findings summary — can options/futures OI predict Nifty? (research phase, closed)

A plain-English record of what we tested, how, and what we found. Companion to
`docs/research-agenda.md` (the literature map + results log). Bottom line up front:

> **After testing OI/options/futures signals at both intraday and daily horizons, at
> proper sample sizes, with point-in-time discipline and controls: none give Nifty a clean
> DIRECTIONAL edge in our data.** Every promising small-sample hint decayed toward zero as
> the sample grew. What holds up is *structure / positioning / expected-volatility* context
> for a human to read — not a mechanical trade signal.

This is a genuine, useful result: we falsified our own edges *before* risking capital.

---

## 1. The question
Does the open interest in Nifty index **options** (per-strike OI/ΔOI/premium/volume) and
**futures** (OI/ΔOI/volume/price), plus **participant positioning** (FII/DII/Client/Pro) and
**dealer gamma (GEX)**, predict the *direction* of the next move — intraday or next-day?

The academic literature says OI *does* predict returns, but mostly at **daily-to-monthly**
horizons and in **US/large-sample** settings (see `research-agenda.md`). India's index-option
market is **writer-dominated** and retail-heavy, so nothing was assumed to transfer.

## 2. Data we captured / built
| Source | Cadence | Fields | How |
|---|---|---|---|
| Time-series | 5-min | GEX levels (walls, flip, magnet, max-pain), net_gex, PCR, VIX, USDINR, news | live NSE, scheduled capture |
| Per-strike options | 5-min | OI, ΔOI, premium (LTP), volume, net_gex | live NSE |
| Index futures | 5-min + EOD | price, OI (near+agg), volume, ΔOI | live + F&O bhavcopy |
| Options EOD | daily, **2 yrs** | per-strike OI/ΔOI/volume + spot | **F&O bhavcopy backfill (543 days)** |
| Participant OI | daily, **17 mo** | FII/DII/Client/Pro fut+opt long/short | **NSE archive backfill (353 days)** |

The **free bhavcopy backfill** was decisive: it let the daily studies run at n≈350–540 instead
of the n≈8–11 we'd accrued live — which is exactly where the "edges" turned out to be noise.

## 3. Methodology (the discipline that mattered)
Every study followed the same rules — the honesty is in the method, not the result:

- **Point-in-time correctness.** The signal at time *t* uses only data available *before/at t*;
  the forward return is measured strictly *after t*. No look-ahead.
- **Spearman rank IC** as the yardstick (robust to outliers and nonlinearity; reads the
  monotonic relationship, not a fragile linear fit).
- **De-trending.** Forward returns were de-meaned per day to remove the day's *drift*, so we
  tested the *signal* not the market's trend. (This alone flipped several raw results to ~0 —
  e.g. intraday "momentum" was entirely drift.)
- **Controls.** A candidate was only credited if it beat a naive control: the pinning test used
  a **60-min trailing-mean anchor** (which *beat* the magnet → "pinning" was generic
  mean-reversion); the blast auxiliaries used a **momentum control** (which exposed the
  "two-sided parity" signal as price-in-disguise).
- **Sign-free where possible.** To dodge the unvalidated **dealer-sign landmine** (CLAUDE.md),
  we preferred magnitudes (|gamma|, |move|), positioning (OI-covering), and mean-reversion —
  quantities invariant to the sign convention.
- **Sample-size honesty.** We reported *n*, flagged overlapping/autocorrelated windows and
  data-gap-spanning pairs, and **never trusted magnitude at small n.** The recurring test:
  *does the IC survive as n grows?* It never did.

## 4. Results

### 4a. Intraday (5-min) — no directional edge; strong generic mean-reversion
| Study (script) | Result |
|---|---|
| Build-up **streaks** → naked option (`analyze_streak_ic`) | IC −0.12/−0.14/−0.05 (15/30/60m, n=211). Mean-reverting; green longs lose (−12%/1h, 26% win). |
| **Turnover** → forward return (`turnover_ic`) | Direction IC ≈ 0; **\|move\| IC +0.31** — turnover predicts move **size, not direction.** |
| Premium **thrust + turnover** (`thrust_ic`) | Aggressive up-moves **mean-revert** (−3% at 15m; worse the bigger the thrust). |
| **Gamma → realized vol** #1 (`analyze_gamma_vol`) | \|gamma\|→RV ≈ 0; signed cross-day-confounded. G− tercile *calmest* → dealer-sign nudge. |
| **Pinning** to magnet/max-pain #2 (`analyze_pinning`) | Looked strong (−0.27…−0.55) but a **trailing-mean control beat the magnet** → generic mean-reversion, not OI pinning. |
| Intraday **futures build-up** direction (`analyze_futures_intraday_ic`) | De-trended IC ≈ 0; build-up-only a faint +0.09/15m, gone by 30m. |
| Build-up **× turnover** | HI-turnover build-ups *reverse* (exhaustion); LO-turnover quietly continue — opposite of "turnover = conviction." |
| Futures × options **agreement** (`analyze_futures_options_agreement`) | No lift from agreement-gating. |

**The one intraday thing that HELD UP:** the sign-free **gamma-blast staying-power grade**
(`analyze_blast`) — Spearman **+0.79** (n=24), live-validated on the 2026-07-07 expiry (4/4
crossings called). But it grades *how hard a break, once it happens, will run* — **confirmation,
not prediction.** (Turnover-ignition and two-sided-parity add-ons were tested and **rejected**.)

### 4b. Daily (EOD) — hints deflated to ~0 at full sample
| Study | small-n "hint" | **full sample** |
|---|---|---|
| **#4 OTM-OI growth** → next-day (`analyze_otm_oi_ic`) | OTM-call IC **−0.71** (n=8) | **−0.02** (n=542) — null |
| **#5 FII net-fut level** → next-day (`analyze_participant_ic`) | **−0.74** (n=11) | **−0.10** (n=352) — decayed to null |
| #5 FII hedging pressure (normalized) | −0.63 (n=11) | −0.07 (n=352) |
| Futures EOD build-up → next-day (`analyze_futures_ic`) | — | ~0 (n=29) |

The FII signal's **monotonic decay** (−0.74 → −0.21 → −0.10 as n went 11 → 154 → 352) is the
textbook signature of a small-sample mirage.

### 4c. Direction-family re-examination — full sample (~5 yrs, both books), all NULL
After a literature review (Pan-Poteshman flow, Xing-Zhang-Zhao skew, Bakshi-Kapadia-Madan risk-
neutral moments, Gao-Han-Li-Zhou intraday momentum, Barbon-Buraschi dealer-gamma) we re-tested every
option-implied **directional** family it flagged — this time on the **Dhan 1-min store (~5 yrs, ATM±10
options + IV, NIFTY *and* BankNifty, n≈1200 days each)**, with day-detrended forward returns, IV+DTE
vol-partials, tercile spreads, and walk-forward OOS. Every one came back null:

| Family (study) | NIFTY | BankNifty | Verdict |
|---|---|---|---|
| Dealer-gamma **pins** — magnet-lock / wall-break / ORB (#12/#14/#16) | ~0 | ~0 | NULL (generic mean-reversion, not OI pinning) |
| **Participant / OI** flow (#4/#5, §4b) | −0.02 | — | NULL (decayed from small-n) |
| **Futures OI** build-up (§4a/4b) | ~0 | — | NULL |
| **RN skewness** — BKM whole-chain (#19) | IC −0.05, tercile −0.33% | +0.06, +0.46% | NULL — *and sign flips across books* (weak null: ±2.3% wing truncation clips the tail integral) |
| Signed **intraday momentum** — first 30m→last 30m (#20) | Spearman +0.040, agree 52.8% | −0.047, 49.3% | NULL (OOS negative, non-monotone, sign-flip) |
| **IV-skew** level & change — Xing-Zhang-Zhao (#21) | every IC ≤ \|0.05\| | ≤ \|0.07\| | **STRONG NULL** — skew level economically correct & cross-book-stable (+4.58/+2.86 vol-pts), signal still absent |

The IV-skew result (#21) is the cleanest: the two-point smirk is *measured correctly* (put IV richer
than ATM, downside fear priced, stable both books) yet predicts nothing — the best-documented option-
skew signal in the literature does not call Nifty direction. The literature was right about **where the
alpha lives** (variance/skew *risk premium* — the sell side) and right that the flow signals need
**trade-initiation signing we cannot replicate** from chain data.

**The one surviving edge is VOL / short-premium:** selling rich IV (backtest S6 → S11, leak-free
IV-rank + intraday stop) plus the confirmed **next-day vol-regime persistence** (#17 — today's |vanna|
forecasts tomorrow's realized range beyond today's move; regime sticky ~56–62% DTE-stripped). Both are
*volatility* reads, not direction. See `docs/intraday-results.md` (#12–#21) and `docs/strategy-chart.html`.

### 4d. Support / resistance revalidation — the STANDING-OI centre is the one that holds (2026-07-16)
Do the dashboard's support/resistance levels actually hold? We tested five S/R definitions for
price-respect on the Dhan 1-min store (700 days, all three eras), sign-free: at each stamp, when spot
is within the 30-min expected move of a level, the day-detrended **push toward the level** (push<0 =
rejects/holds, >0 = breaks) vs a mid-zone placebo. `scripts/analyze_footprint_sr.py`:

| S/R definition (30-min) | edge vs placebo | across eras | verdict |
|---|---:|---|---|
| Footprint **ΔOI-weighted** (fresh build, centre-of-mass — *what the panel shows today*) | **+1.6 bp** | +1.1 / +3.0 / +0.3 | NULL, **wrong sign** (breaks toward, not rejects) |
| Footprint **ΔOI-top** (fresh build, single peak strike) | +0.8 bp | consistent wrong | NULL |
| **Total-OI-weighted** (standing book, centre-of-mass) | **−2.6 bp** | **−2.2 / −3.4 / −2.1** | **HOLDS — right sign, robust all eras** |
| Total-OI-**top** (standing book, single peak strike) | −0.2 bp | mixed | NULL |
| **GEX walls** (gamma/OI peak, engine, dealer-sign) | −0.5 bp | −0.6 / −0.5 / −0.4 | ~NULL (right sign, ~0) |

Three clean takeaways: (1) **weighted beats top** — the *centre of mass* of standing OI rejects price;
the single peak strike does not. (2) **Standing total-OI beats fresh ΔOI** — the fresh-build footprint
we currently display has the *wrong* sign (price drifts through it), while the standing-OI centre
rejects. (3) It also beats the dealer-sign **GEX walls** — a **sign-free** level does best. The winner,
**total-OI-weighted S/R (−2.6 bp, stable across all three eras)**, is the first S/R read that isn't a
wrong-signed null. The magnitude is still small (~0.03%), so it's a **trustworthy map, not a
mechanical fence** — but it says the dashboard should draw S/R from the **standing-OI centre-of-mass**,
not the fresh-ΔOI build (which belongs to the *"where money is building"* flow, a different question).
Consistent with §4a–4c; the fix is wired into the dashboard (weighted + top shown, standing-OI based).

**Turnover-weighting doesn't help the LEVEL (2026-07-16 follow-up).** We also tested S/R weighted by
**turnover** (ΔOI or OI × premium), both fresh and standing: every turnover variant is *worse* — edge
+1.2 (standing) / ambiguous −1.7 (fresh, near-level ~0 with a spot-hugging placebo). The reason is
structural: **premium peaks at-the-money**, so premium-weighting collapses the "level" onto spot,
turning it into a price-tracker rather than a level that holds. So for **levels**, use contract
concentration (total-OI-weighted); **turnover belongs to the *flow*** — the dashboard now surfaces the
single strike taking the most **fresh-writing ₹** ("💰 most money building", `window_footprint`
peak-turnover) as a *where-the-money-is-committing* read, explicitly not an S/R level.

## 5. What holds up (it isn't nothing)
0. **Dealer-gamma concentration → forward realized VOLATILITY** (the strongest, most
   theory-consistent result). On 2 yrs of raw bhavcopy, **|GEX| → forward RV is inversely
   related** (Spearman **−0.20 at 10d** with per-strike-IV GEX; −0.15 at 5d), coherent and
   growing with horizon — the sign-free SqueezeMetrics "gamma dampens vol" pattern. **Not a
   direction call** (signed GEX ≈ 0; dealer sign still open). Productionized as the dashboard
   **🌡️ volatility-regime gauge** (`app/gex_history.py`): high |GEX| = expect calmer /
   mean-reverting, low = wilder / trending. Weak/borderline but *right*, and the one signal
   the data genuinely supports.
1. **Gamma-blast staying-power grade** (+0.79, sign-free, validated) — confirmation of a
   break's momentum, not a directional predictor.
2. **Intraday mean-reversion** — strong and robust, but **generic** (revert-to-recent-price),
   *not* specific to any OI structure (a naive trailing mean beats the magnet).
3. **Turnover = expected move size, not direction** — validated repeatedly; useful for sizing /
   long-gamma structures, never for picking direction.
4. **Dealer-sign evidence leans to the inverted "model B"** — three independent nudges
   (both-signs realized-vol, PCR cross-check, and the G−-calmest gamma→vol tercile). Still a
   *lean*, not a verdict; `DEALER_SIGN_MODEL` stays on the US default.
5. **India microstructure is writer-dominated** — OTM-call OI building reads as *writing/
   resistance* (contrarian), not the US buyer-driven bullishness.
6. **CALM-regime short strangle at the walls — the vol edge, made tradeable (2026-07-16).**
   `scripts/backtest_wall_strangle.py` backtests the dashboard's *"sell premium near the walls"*
   play directly: at 09:20, in the CALM (|GEX| top-tercile) regime, sell the call@call-wall +
   put@put-wall, stop on a wall breach, exit at close. **Positive expectancy that beats the
   no-filter baseline and survives spread** — CALM @2% half-spread: **+11.0%/trade** (58% win,
   payoff 1.55×, 49% stopped) vs ALL-days baseline +5.3%; still **+7.3%** at a 4% spread. It is the
   short-premium signature (win often, occasional stop-out, net positive) — and it's the **vol
   edge (§5.0 / S11)**, *not* the walls containing price (§4d): the walls only set the strikes and
   the stop. **Honest caveat — it's thinning in the current regime:** era-split (calm @2%) pre-Nov24
   **+12.2%**, Nov24-Aug25 **+17.7%**, **Sep25+ (current Tuesday) only +4.1% with a 61% stop-rate**
   (up from ~44%) — the Tuesday-weekly regime breaches the walls more, so the edge is real but
   fragile now. Returns are % of the *credit* (small vs SPAN margin); mid-close fills + modelled
   spread. This is the first *dashboard-play* backtest that comes out positive.

7. **Defined-risk variant — the short IRON FLY is NOT better after cost (2026-07-17).**
   `scripts/backtest_iron_fly.py`. When the call & put walls coincide on one strike (a strong pin),
   "sell at the walls" degenerates into a naked straddle, so the dashboard now steps the strikes ±1
   out (a 2-leg stepped strangle) — and we tested the obvious defined-risk alternative, a short iron
   fly (sell the pin straddle + BUY wings W strikes out), held to close (the wings are the stop).
   Same CALM entry; return = % of *defined max risk* (ROI). **At mid it is only marginally positive
   (CALM +0.6 / +1.1 / +3.0%/trade for wings W=2/3/4, payoff <1×), and after a realistic per-leg
   spread it goes firmly NEGATIVE** (CALM @2% half-spread: −59 / −22 / −10%/trade for W=2/3/4; every
   expiry era negative). The reason is structural: a 4-leg fly crosses the bid/ask **8 times** (four
   legs, entry+exit) vs the strangle's four, so the bought wings cost more theta than the tail they
   remove. Wider wings bleed least (bigger denominator) but never clear cost. **Takeaway: the capped
   tail is not worth the extra two legs' spread — the 2-leg (stepped) strangle is the better execution
   of the calm short-premium edge; use the fly only if a hard max-loss cap is required, eyes open to
   the negative post-cost drift.** Nearest-strike premiums, modelled per-leg spread, MTM-at-close;
   sign-free.

## 6. What the tool is for
Not a directional-alpha engine. A **structure / positioning / expected-volatility
decision-support dashboard** for a human to read context — walls, magnet/max-pain, build-up
regime, participant flows, gamma state, and the validated blast staying-power grade — and then
**confirm every decision manually** (no auto-execution; CLAUDE.md). That framing is honest,
useful, and matches what the data can actually support.

## 7. Honest caveats
- **Regime coverage — now broad.** The Dhan 1-min backfill (§4c) extends the intraday studies to
  **~5 years (2021–2026), both books**, spanning multiple macro regimes — the direction nulls are no
  longer resting on a few months. (Daily EOD studies remain ~2 yrs.)
- **Sign-of-signal open.** The dealer-sign landmine is *leaning* toward inversion, not resolved;
  we deliberately never traded a flipped sign.
- **Approximate flow.** The strongest US results use *signed* option flow (buyer/seller-
  initiated); we only *approximate* signing (build-up matrix / OI-change), a known weakness.
- **Intraday history — now backfilled.** The Dhan `rollingoption` feed (~5 yrs of 1-min ATM±10
  options with OI + IV, both books) closed the gap the live-only NSE OI left; the §4c direction nulls
  now rest on years, not months. Two doors stay open but low-prior: a **full-wing (deep-OTM) chain**
  could revisit BKM #19 (our ATM±10 store truncates at ±2.3%), and the source papers are
  **cross-sectional** (rank many names) whereas we test a single index time series.

## 8. Where this leaves the project
Reposition from **"find a signal"** to **"maintain a best-in-class decision-support dashboard +
keep capture clean."** Re-run the `analyze_*` scripts periodically as data accrues (they are the
standing tests, with de-trending and controls baked in), but treat any new small-n excitement
with the scepticism this phase earned. The scripts, the discipline, and this record are the
durable output.
