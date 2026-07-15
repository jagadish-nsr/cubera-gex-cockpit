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
