# Intraday research results (Dhan 1-min store)

Running log of studies over the Dhan intraday option store (5yr, 1-min per-strike OI+IV;
see the `dhan-intraday-data` memory). All studies read the **Layer-1 5-min feature cache**
(`app/intraday_features.py` → `data/journal/intraday_features/<store>.parquet`), never the raw
30M rows. Sign-free unless noted (net_gex sign = the unvalidated dealer landmine → use |GEX|).

## Methodology
- **Feature cache**: one row per (day, 5-min stamp). `net_gex` from real per-strike Dhan IV
  (OTM side per strike, iv in %÷100), model-A dealer sign, ATM±10 band. Time-to-expiry `T`
  backed out calendar-free by BS-inverting the OPEN ATM call (r=q=0), held fixed across the day.
- **Point-in-time**: feature at stamp t; outcome from (t, t+h] only, within the same session.
- **Controls**: time-of-day neutralization (subtract each stamp's cross-day mean); partial
  Spearman vs ATM IV.
- ⚠️ Overlapping forward windows autocorrelate → IC significance overstated; treat ICs as effect
  sizes, not p-values.

## Pipeline validation (gate)
`scripts/validate_intraday_gex.py` — intraday last-stamp |GEX| vs EOD |GEX| recomputed
apples-to-apples (nearest expiry, ±2% band, true bhavcopy T): **Spearman +0.86, Pearson +0.92**,
medians ~equal (5.2e10 vs 4.9e10) over ~100 days. The calendar-free T proxy and Dhan-IV smile
reproduce the true-expiry EOD engine. (Against the *default* gex_history — ±10% band, MIN_DTE≥3 —
agreement is ~0, because that is a genuinely different measure, not because the build is wrong.)

## Study #1 — intraday |GEX| → forward realized vol  (NIFTY weekly, 1239 days, 93k stamps)
`scripts/analyze_intraday_gex_vol.py`. SqueezeMetrics: dealer gamma dampens realized vol.

| horizon | IC raw | IC tod-neutral | atm_iv IC | **|GEX| \| atm_iv** |
|--------:|-------:|---------------:|----------:|--------------------:|
| 15m | −0.147 | −0.097 | +0.323 | **−0.099** |
| 30m | −0.186 | −0.172 | +0.431 | **−0.132** |
| 60m | −0.206 | −0.194 | +0.505 | **−0.153** |

**Result:** negative and monotonic in horizon (more gamma → calmer next-hour vol), matches the
EOD −0.20 @10d now confirmed intraday with real IV, **survives** both the time-of-day control and
the ATM-IV partial — so |GEX| carries vol information *beyond* IV. The one robust edge of the arc,
now at intraday resolution. Sign-free; a vol-regime read, not a direction call.

### Study #1 across books (partial |GEX| \| atm_iv IC — the meaningful control)
| book | 15m | 30m | 60m |
|------|----:|----:|----:|
| NIFTY weekly  | −0.099 | −0.132 | −0.153 |
| BANKNIFTY     | −0.113 | −0.168 | −0.224 |
| NIFTY monthly | −0.063 | −0.090 | −0.131 |

Negative, monotonic, survives the ATM-IV partial in ALL THREE books — cross-instrument. Weekly
gamma > monthly (near-term gamma drives near-term vol). BANKNIFTY strongest at 60m though its
time-of-day-neutral IC is weak (−0.03/−0.04/−0.12), so lean on the partial-IV control there.

## Study #2 — intraday pinning / max-pain  (NIFTY weekly, 1239 days)  → NEGATIVE
`scripts/analyze_intraday_pinning.py`. Ni–Pearson–Poteshman: price should be pulled to the
peak-OI strike, strongest at expiry. Sign-free directional IC of pin_gap=(peakOI−spot)/spot vs
de-trended forward return.

- pin_gap IC positive (+0.08/+0.10/+0.14 at 15/30/60m) — price *does* drift toward the pin…
- …but it does **NOT** strengthen at expiry (≤1.5d bucket is the *weakest*, >6.5d the strongest) —
  the discriminating prediction FAILS.
- **Neutral-anchor control kills it:** reverting to the **day open** predicts far better
  (+0.13/+0.17/+0.23) and a dumb 30-min SMA (+0.06/+0.08/+0.11) matches it. Trailing momentum is
  negative (generic reversion exists).

**Conclusion:** no OI-specific pinning edge — the "pull to peak-OI strike" is just generic
intraday mean-reversion, and a trivial revert-to-open anchor beats it. Consistent with the
research-arc finding: OI positioning gives no clean directional edge for NIFTY.
**Holds in all three books** (NIFTY_M, BANKNIFTY): pin_gap ≤ open_gap / sma_gap everywhere;
BANKNIFTY 30-min-SMA reversion (+0.18/+0.19/+0.21) is the strongest anchor of all. Null generalizes.

## Study #3 — turnover-confirmed premium thrust → continuation?  (NIFTY, 1239 days)  → REFUTED
`scripts/analyze_intraday_streaks.py`. Tests the recurring live-only idea ("build-up with
turnover is key"; "premium up 3–5% in 5 min + big TO"). ATM off=0, CE+PE pooled, 5-min grid;
forward naked-long option return vs a same-book baseline.

| signal | n | mean f15 | hit15 | mean f30 | hit30 |
|--------|--:|---------:|------:|---------:|------:|
| baseline (all stamps) | 178,539 | +2.95% | 45.6% | −1.77% | 43.2% |
| 3-green streak        | 9,427   | −6.83% | 26.9% | −8.18% | 24.8% |
| 3-green + high TO     | 3,372   | −7.71% | 25.9% | −9.42% | 22.2% |
| ≥3% jump + high TO    | 23,010  | −7.90% | 27.7% | −9.79% | 25.6% |

**Result:** the thrust does NOT continue — it mean-reverts hard (mean ≈ −8%, hit ≈ 27% vs
baseline 44–46%). The "turnover build-up sustains" impression from a handful of live days was
noise. Likely mechanism: IV mean-reversion (a premium spike is an IV pop that deflates), so the
clean read is "fade turnover-confirmed premium/IV spikes" (~73% as a seller), not "buy the thrust".

## Overall (intraday, real n)
Same shape as the EOD research arc: ONE genuine sign-free effect — **|GEX| → lower forward
realized vol** (cross-instrument, survives ATM-IV partial) — and **no directional edge** from OI
positioning (pinning = generic reversion; turnover thrust = reversion, not continuation). The tool
is a vol-regime / mean-reversion read, not a direction predictor.

## Study #4 — signed GEX × bucket: does the SIGN gauge regime / resolve the landmine?  → INCONCLUSIVE
`scripts/analyze_intraday_gex_sign.py` (NIFTY, 1239 days). Tests whether the dealer-gamma SIGN
splits the tape into trending vs mean-reverting (and thus which sign is long-gamma).

- Confirms the MAGNITUDE reading (sign-free): LOW |GEX| → rv ~9 (wilder), HIGH |GEX| → rv ~6–8
  (calmer). The bucket gauges trendy vs sideways — robust.
- **Sign is NOT resolvable here.** Magnitude-controlled (|GEX| tercile × sign) continuation IC is
  tiny (|·|≤0.05) and flips across bands (neg cont60 +0.032 / −0.017 / +0.052 for LOW/MID/HIGH) —
  no convention makes one sign consistently calm+mean-reverting. HIGH-|GEX|-negative is calmer
  (6.8 vs 8.0, would lean model B) but is simultaneously the MOST trending (+0.052, contradicts B).
  Vol says B, continuation says A → contradictory, thin negative sample.
- meanFwd ≈ 0 in every bucket: GEX sign gives no up/down directional edge (as theory predicts).

**Takeaway:** use the SIGN-FREE |GEX| magnitude bucket for the vol-regime gauge (what works); the
dealer-sign landmine remains genuinely unvalidated ([[both-signs-dealer-sign-read]]).

## Cross-book completion + gamma-blast (2026-07-13)

**Study #3 (turnover thrust) — NIFTY_M & BANKNIFTY: REFUTED too.** ≥3%+high-TO jump → −4.4% f15
(NIFTY_M) / −4.3% (BANKNIFTY), ~25–28% hit vs baseline ~48%. Same mean-reversion as NIFTY weekly
in all three books.

**Study #4 (signed-GEX regime) — NIFTY_M & BANKNIFTY: INCONCLUSIVE too.** Magnitude-controlled
continuation ICs tiny (|·|≤0.13) and incoherent across bands in both books — the dealer sign does
not resolve. Generalizes the NIFTY finding.

**Study #5 — gamma-blast score re-validation (`scripts/analyze_dhan_blast.py`, NIFTY, 556 near-
expiry days DTE≤2.5, 8352 crossings).** The blast_score (TRUE-vs-FAKE staying-power) was validated
on only n=24 (+0.79). At real n:
- **Contemporaneous (score over the full 40-min window vs continuation over the SAME window):
  Spearman +0.64**, cleanly monotone (TRUE-leaning 94% continue / +66pts; FAKE-defending 20% / −29pts).
  Looks amazing — but score and outcome share the window.
- **POINT-IN-TIME (score from first 15 min → continuation over the remaining 25 min): +0.020**,
  momentum benchmark +0.006, **partial (beyond momentum) +0.019**, %continued flat ~50% across all
  score buckets. **No predictive power.**

**Conclusion:** the gamma-blast score is a strong **real-time CONFIRMATION** descriptor (writers
covering + premium expanding *is* the break unfolding) but **NOT a point-in-time predictor** — the
+0.79/+0.64 collapses to ~0 once observation and outcome windows are separated. Same decay-to-zero
as every other hint, now rigorously shown at n=8352, not asserted from n=24. Confirmation, not alpha.

**Data-quality note:** all three Dhan caches carry ~1.8% corrupt spot ticks (spurious >3% 5-min
jumps). Rank-based ICs are robust to them; a mean forward-RV is not — so the live vol-regime gauge
uses the **median** forward RV per |GEX| tercile (+ an 8% return clip). Realistic terciles: NIFTY
7.4/6.8/5.9, NIFTY_M 7.2/7.2/6.7, BANKNIFTY 10.6/9.1/7.9 (lo/mid/hi). Option-premium studies
(#3, #5) are unaffected (they don't use spot RV).

## Study #1 deep-dive — NIFTY vs BANKNIFTY (GEX→vol head-to-head)
`|GEX|` scale differs ~16× (NIFTY median 4.9e10 vs BANKNIFTY 3.0e9) → never compare raw |GEX|
across indices; each needs its own percentile distribution (the dashboard gauge is per-store).

Spearman IC (more negative = stronger vol-damping); `partial|IV` = |GEX| after removing ATM-IV
(the genuine-gamma content, the meaningful control):

| horizon | IC raw N/B | IC tod-neutral N/B | **partial\|IV N/B** | atm_iv IC N/B |
|--------:|-----------:|-------------------:|--------------------:|--------------:|
| 15m | −0.147 / −0.143 | −0.138 / −0.082 | **−0.099 / −0.113** | +0.323 / +0.327 |
| 30m | −0.186 / −0.196 | −0.182 / −0.099 | **−0.132 / −0.168** | +0.431 / +0.426 |
| 60m | −0.206 / −0.245 | −0.201 / −0.176 | **−0.153 / −0.225** | +0.505 / +0.474 |

Vol-damping — median forward RV by |GEX| tercile (annualized %):

| index | 15m lo→hi | 30m lo→hi | 60m lo→hi | high-gamma calmer |
|-------|-----------|-----------|-----------|-------------------|
| NIFTY     | 5.9→4.6 | 7.4→5.9 | 8.1→6.3 | ~21–22% |
| BANKNIFTY | 8.2→6.3 | 10.6→7.9 | 12.0→8.7 | ~23–27% |

**Read:**
- BANKNIFTY is genuinely STRONGER at longer horizons and in the IV-partial: 60m partial|IV
  −0.225 vs NIFTY −0.153; its damping grows with horizon (27% @60m vs NIFTY's flat ~22%).
- But at SHORT horizons NIFTY is the CLEANER signal: BANKNIFTY's tod-neutral IC collapses
  (−0.082 vs −0.138 @15m) → much of BankNifty's raw 15m signal is the shared intraday U-shape,
  while NIFTY's survives time-of-day neutralization. So: NIFTY cleaner ≤15m, BANKNIFTY stronger ≥30–60m.
- BANKNIFTY is simply more volatile (60m RV 12% low-gamma vs NIFTY 8%) and more gamma-sensitive
  (higher-beta, bank-concentrated → dealer hedging bites harder over the hour).
- Practical: use NIFTY's gauge for the next 15–30m, BANKNIFTY's for 30–60m — each is sharpest there.

## Study #6 — ML to predict a TRUE gamma blast (point-in-time)  → NO DIRECTIONAL EDGE
`scripts/build_blast_dataset.py` (leak-free features over a 10-min obs window; target =
continuation AFTER, over the next 30 min) + `scripts/train_blast_model.py` (HistGradientBoosting +
logistic, WALK-FORWARD temporal split, shuffle control, EV-by-decile). NIFTY, 8142 crossings,
16 features (blast-score components + per-strike OI/ΔOI/IV/skew/premium/turnover + |GEX|/PCR/
momentum-so-far/rv-so-far/DTE/time-of-day). Target: big blast = cont > 0.15% (base rate ~16–19%).

- Test AUC **0.642** (GBM) / 0.640 (logistic) — LOOKS like skill, but:
  - **EV ~0 across ALL deciles**: top-predicted-prob decile mean cont −0.011% (vs base −0.002%);
    no positive EV gradient. It concentrates big moves (25% big vs 16%) but with SYMMETRIC
    excursions (MFE +0.12% ≈ |MAE| −0.14%) — bigger moves BOTH ways, not a directional edge.
  - **Top features are all volatility proxies** (iv 0.094, turnover, rv_sofar, absg) → the model
    re-learned move SIZE (the known |GEX|/IV→vol signal), NOT direction.
  - **Overfit/leak flags**: train AUC 0.816 vs test 0.642; SHUFFLE control 0.522 (not clean 0.50).

**Verdict:** no predictable DIRECTIONAL gamma-blast edge — direction at entry is a coin flip
(base 51%, top-decile EV ~0). Full ML confirms Study #5 / the whole arc: **volatility (size) is
predictable, direction is not.** The only tradeable read here is NON-DIRECTIONAL: high blast-
probability = a big move likely EITHER way → a straddle/vol play, not a directional entry.
Next (if pursued): straddle-at-crossing P&L to see if the vol concentration beats theta/spread.

## Study #6b — straddle-at-crossing P&L (the non-directional angle)  → NO EDGE
`scripts/straddle_pnl.py`. The model predicts move SIZE, not direction → test a straddle (buy
crossed-strike CE+PE at +10min, exit +30min). NIFTY test set, 3% round-trip cost.

| | mean net P&L | win |
|---|---|---|
| Unconditional (every straddle) | −4.29% | 22% |
| Top blast-prob decile | **−4.74%** | 21% |

Move size rises monotonically by decile (0.10%→0.27%) — the vol prediction IS real — but the
straddle loses in EVERY decile, even GROSS of cost (top decile −1.74% gross). The premium already
prices in the expected vol, so predicting size gives no straddle edge; theta finishes it. Top
decile is even worse than unconditional. **Market is efficient here: the forecastable vol is
already in the option price.**

**Gamma-blast final verdict:** not a systematic edge in EITHER form — direction is a coin flip
(Study #6), size is predictable but priced-in (#6b). Blast score = real-time CONFIRMATION only.

## Study #5 across books — BANKNIFTY blast (point-in-time)  → ~0, same as NIFTY
`analyze_dhan_blast.py BANKNIFTY` (138 near-expiry days — monthly-only, 2746 crossings):
partial|momentum IC **+0.003** (raw −0.019), %continued flat ~50% across score buckets. The blast
score has no point-in-time predictive power for BankNifty either (small-n was +0.37, didn't
transfer). Confirmation-only in both indices.

## Study #6c — do INTRADAY FUTURES help the blast model? (50-day subset)  → NO
`scripts/blast_futures_test.py`. Only ~50 days have Dhan intraday futures (90-day cap). Added
3 point-in-time futures features (OI build-up %, direction-aligned price momentum, turnover) to
the crossing set (NIFTY, 50 days, 810 crossings) and compared options-only vs options+futures.

- Univariate Spearman vs continuation: fut_doi +0.041, fut_mom +0.044, fut_to +0.023 — all noise.
- Walk-forward CV AUC: options-only 0.514, options+futures 0.512, **futures-only 0.490** (no skill);
  **Δ from futures −0.002**. Intraday futures OI/volume add nothing, even where available.

Consistent with the futures-OI directional null. Confirms: the gamma blast is not predictable, and
futures data does not change that. (Futures intraday remains useful only as the shallow 90-day
descriptive store, not as a blast predictor.)

## Study #7 — daily straddle vol-timing around the GEX regime  → NO EDGE (DTE confound)
`scripts/straddle_gex_timing.py`. Hypothesis: HIGH opening |GEX| → realized vol < implied → sell
straddle. Entry ATM straddle 09:20, hold to 15:25 (value the SAME strikes at exit); bucket by
opening |GEX|. NIFTY, 1229 days.

- **RAW looked amazing** (SELL high-|GEX| straddle +34%/trade, 99% win) — but it's a **DTE confound**:
  |GEX| ∝ 1/√T, so HIGH-|GEX| ≈ near-expiry (dte 1.9 vs LOW 5.0). "Sell high-|GEX| vol" = "sell
  near-expiry straddles" = harvest theta, nothing to do with GEX. (An early version also had a
  selection bug — required same ATM strike open→close, dropping trending days; fixed.)
- **DTE-CONTROLLED (decisive):** within a DTE band, LOW vs HIGH |GEX| straddle P&L barely differs
  (1.5–3d: −2.14% vs −2.50%, move/BE 0.48 vs 0.46). If anything HIGH-|GEX| has slightly HIGHER
  move/BE (long better) — OPPOSITE of the sell-vol thesis. HIGH-|GEX| days carry LOWER ATM IV
  (15.4 vs 18.7%) but the same realized move/BE → IV already prices the calmer vol.

**Conclusion:** |GEX|→vol is a real REGIME read (the dashboard gauge) but NOT a tradeable straddle
edge — the vol is in the premium; the raw "edge" was theta-by-expiry. Same efficient-market result
as the blast straddle (#6b). Expiry-day (≤1.5d) band shows fat-tailed LONG-vol payoff (0-DTE blast
lottery), not a clean/sellable edge.

## Study #8 — greek-exposure screen (beyond GEX)  → direction dead; |VANNA| > |GEX| for vol
`scripts/greek_exposure_screen.py`. Dealer exposures from per-strike OTM-smile greeks (r=q=0),
ATM±10: DEX (Σδ·OI), DFLOW (δ·volume), DDOI (δ·ΔOI), CHARM, VANNA, VEGA, GEX. Point-in-time IC vs
forward 30-min return (direction) and vol (size). NIFTY, stride-4 sample, 23,303 stamps.

- **DIRECTION: dead.** signed→ret and Δ15→ret ~0 for ALL greeks (|IC|≤0.045) — delta/charm/vanna/
  flow/fresh-ΔOI included. No greek predicts direction. Closes the "other greeks for edge" question.
- **VOL: |VANNA| beats |GEX|.** raw |·|→fvol / partial-vs-IV / partial-vs-DTE:
  VANNA −0.419 / **−0.169** / −0.421 ; GEX −0.164 / **−0.088** / −0.206 ; VEGA −0.217 / **+0.098** / −0.215.
  VANNA's raw signal is ~⅔ IV confound (∝1/σ) but the IV-partial (−0.169) still ~2× GEX (−0.088) and
  survives DTE — genuinely better forward-vol prediction. VEGA is pure IV (flips +0.098 under control).
  GEX survives both controls (gauge not a DTE artifact).

**Takeaway:** no new alpha (vanna is still a vol-REGIME metric; direction dead, vol priced-in), but
|VANNA| is a ~2× sharper vol-regime metric than |GEX| — candidate to upgrade the dashboard gauge
(vanna weights OTM OI differently: zero at ATM, peaks OTM). TODO before productionizing: confirm on
full sample (stride 1) + BankNifty.

## Study #9 — ΔPCR conditioned on GEX regime  → NULL
`scripts/analyze_deltapcr_gex.py`. Static PCR was null; this tests the intraday ΔPCR flow (30-min
change in putOI/callOI — meaningful because Dhan OI updates live) vs de-trended forward return, by
|GEX| tercile and GEX sign. NIFTY, 1239 days, 93k stamps.

- POOLED and every |GEX| bin ~0 (|IC|≤0.05). Only the GEX− bin nudges +0.06 (15/30m, n=15.9k) — one
  bin, weak, no LOW-vs-HIGH sign flip; the classic decays-to-zero shape. No regime-conditional edge.

ΔPCR flow adds nothing over static PCR (both null). Consistent with the OI-positioning direction null.

### Study #8 confirmed on FULL sample (stride 1) + BankNifty
|·|→fvol | IV-partial (the real test — beats GEX beyond IV?):
| metric | NIFTY-W | BANKNIFTY |
|--------|--------:|----------:|
| VANNA  | −0.187 | −0.190 |
| GEX    | −0.128 | −0.169 |
| CHARM  | −0.228 | −0.182 |
| VEGA   | +0.080 | −0.149 |

**|VANNA| beats |GEX| in BOTH books** (strongly NIFTY ~46%, modestly BankNifty ~12%); direction
still dead (signed→ret/Δ15→ret ~0 everywhere). CHARM is strongest on NIFTY (−0.228) but inconsistent
(only ~GEX on BankNifty). Running a strike-weighting bake-off (GEX vs GEXpeak/GEXkey3/GEXotm vs
VANNA vs CHARM) to pick the single best vol-regime metric before wiring it into the dashboard gauge.

## Study #10 — Charm vs Gamma-blast (does charm call blast DIRECTION?)  → NO
`scripts/analyze_charm_blast.py` (+ `charm`/`charm_flow` added to build_blast_dataset). Charm is the
one greek with a directional mechanism (expiry re-hedging drift), tested at the 8142 near-expiry
crossings for the thing the blast score can't do — direction.

- signed charm → market direction: IC +0.030; charm_flow → direction +0.022; momentum −0.003. Null.
- charm·(crossing dir) → continuation: −0.003 (level) / +0.014 (flow) at 0-DTE; partial-vs-mom ~0. Null.
- TRUE-blast alignment (0-DTE): charm-flow sign matches the crossing direction 55% (vs 50%) — the
  ONLY thing above noise, but +5pp, post-hoc (conditioned on the outcome), forward-IC +0.014. Noise.

**Verdict:** charm does NOT predict blast direction — direction is dead across the ENTIRE greek suite
(delta/charm/vanna/flow). Charm-FLOW is marginally the least-dead (flow>level, consistent with the
project's pattern) but not tradeable. Charm remains a strong near-expiry VOL metric only. Closes the
Charm-vs-blast TODO.

## Study #11 (T4) — vol-risk-premium: does vanna add beyond IV?  → MODEST cross-book residual
`scripts/analyze_vol_risk_premium.py`. VRP = IV − realized (entry 09:20 → close). NIFTY 1234 days.

- **Unconditional VRP +8.3%** (IV 17.8 vs realized 9.5; IV>RV 97% of days) — the classic vol risk
  premium (sell vol generally pays, but it's crash-risk compensation, not alpha).
- Dominant driver is IV LEVEL: Spearman(IV, VRP) +0.80 ("sell high IV"). Raw vanna→VRP −0.36 is
  just vanna∝1/IV re-encoding that.
- **Decisive control:** partial(|vanna|, VRP | IV) = **+0.33**, | IV+DTE = **+0.11** (NIFTY),
  **+0.24 (BankNifty)** — FLIPS positive and holds cross-book. At matched IV+DTE, higher vanna →
  larger VRP → IV under-adjusts for the extra calm vanna predicts → a regime-timed short-vol tilt.

**Verdict:** the FIRST vol result to survive IV+DTE controls in the theoretically-correct direction
AND replicate cross-book — genuinely promising vs the sea of nulls, BUT modest (+0.11/+0.24) and NOT
yet a proven tradeable edge. Follow-up (new TODO): robust RV (BankNifty mean RV is bad-tick-corrupted
— rank partials survive but a P&L needs clean RV), a cost-adjusted regime-timed short-vol backtest,
and out-of-sample. This is the one thread worth pursuing for actual vol-alpha.

## Study #11b (T4b) — is the vanna-VRP residual TRADEABLE?  → NO (intraday horizon)
`scripts/backtest_vanna_shortvol.py`. Sell ATM straddle 09:20→close, ACTUAL premium P&L (robust to
spot bad-ticks), cost 2%, walk-forward OOS. NIFTY 1229 days.

- Unconditional sell-every-day: net −1.27%, win 62% (small wins, occasional big-move loss).
- **partial(|vanna|, seller P&L | IV+DTE) = −0.017** (Study #11's VRP partial was +0.11) — the
  residual VANISHES in actual straddle P&L. HIGH−LOW vanna seller P&L negative in 3/4 DTE bands.
- **Walk-forward OOS: vanna-selected days −1.07% vs unconditional +0.60%** — selection underperforms.

**Verdict:** the #11 spot-RV residual does NOT monetize — the straddle premium already prices the
vanna signal; intraday theta/tail noise swamps a +0.11 stat edge. Efficient, same as #6b/#7. Caveat:
this is entry→CLOSE (1-day) straddle — a poor VRP vehicle; the proper **to-EXPIRY** hold is the one
untested horizon (low prior). Vol stays a REGIME read, not alpha — now with an OOS backtest behind it.

## Study #12 (A1) — MAGNET LOCK: does spot get PULLED to the pin from a distance?  → NEGATIVE
`scripts/analyze_magnet_lock.py` (scenario catalog A1). Needs the levels-enriched feature cache
(`call_wall/put_wall/magnet/max_pain/gamma_flip` per 5-min stamp). Terminal, point-in-time test:
`closer` = does the **15:15 spot** land nearer a target (magnet / peak-OI / max-pain) than spot did at
time *t*? Measured ONLY when the starting gap is meaningful (≥ 0.25 × σ√τ expected-move-to-close), vs a
**MIRROR placebo** (same gap, opposite side) — a real pull must beat its mirror. NIFTY 1234 days.

- **Expiry day, gapped stamps: closer-rate to peak-OI 39%, BELOW its mirror 51%** (magnet 38 [43],
  max-pain 39 [46]). Holds at every decision stamp (11:15 / 13:45 / 14:45) and every DTE bucket. The
  σ-shrink is **negative** (median −0.11 to −0.14): the gap *grows* to close. Spot mildly *continues
  away* from the pin, it is not sucked in.
- **Concentration doesn't rescue it:** peak-OI closer-rate by |charm|×gap grid — no cell reaches 50%;
  HIGH-charm + small-gap is the *worst* (34%). The "high pin → stronger pull" prediction FAILS.
- **45% of expiry stamps are ALREADY pinned** (spot within 0.25 exp-move of peak-OI, no gap to close).

**Conclusion:** the pin is **stickiness, not attraction** — when spot already sits at the peak-OI
strike it tends to stay (that IS the pin), but once a real gap opens there is **no pull back toward
the pin**; if anything, mild continuation away (mirror beats target by ~11 pts). Directly kills the
popular expiry pin-convergence trade (e.g. "spot 20020, pin 20100 at 13:45 → buy the 20050 CALL
expecting drift up"): the gap does **not** systematically close toward the pin. Consistent with #2
(no OI-specific pinning edge) and sharper — the same-distance mirror is a stronger control than #2's
open/SMA anchors, and it isolates the *actionable* large-gap/to-close case. ⚠️ conditioning on a gap
selects the trending stamps, so "continuation away" is partly mechanical — the tradeable takeaway is
the negative one: you cannot fade a gap toward the pin. Sign-free. Recorded DEAD (no dashboard chip).
Lowers the prior on A2 (blast-target drift, which is exactly this pull-from-distance bet).

## Study #13 (H) — GAMMA-BLAST COMPRESSION precondition: tight morning → blast?  → REFUTED
`scripts/analyze_blast_compression.py` (scenario catalog H; user hypothesis). Does a small spot
high-low range from the open to 13:45 (< ~1%) prime an afternoon BLAST (expansion)? Uses the RAW
1-minute spot (271 bars 09:15→13:45; accurate HL proxy, no OHLC download needed). EXPIRY days only
(458), non-expiry (775) as contrast. Point-in-time (morning range known at 13:45). Sign-free.

- **The opposite of the folklore — it's vol PERSISTENCE, not a coil.** ρ(morning-range, afternoon-
  range) = **+0.453** on expiry (+0.495 non-expiry): a tight morning predicts a *quieter* afternoon.
- Tercile monotone the "wrong" way: LOW-morning tercile → afternoon range **0.31%** (P(aft>morning)
  27%); HIGH-morning → **0.55%**. The coiled-morning has the SMALLEST afternoon, not the biggest.
- **User's explicit 1% cut:** morning <1% → afternoon range 0.36%, only **2%** of days exceed 1% in
  the afternoon; morning ≥1% → 0.59%, **14%** exceed 1%. A WIDE morning is ~7× more likely to blast.
- **High-GEX-pinning doesn't rescue it:** within the top-|charm| (most-pinned) expiry tercile the
  autocorr is still +0.394 and LOW-morning still smaller (0.26% vs 0.50%). Refuted in every slice.

**Conclusion:** the "<1% morning compression → gamma blast" precondition is REFUTED — intraday realized
vol clusters, so a quiet morning begets a quiet afternoon (even under max pinning). The *actionable
flip*: an afternoon blast is more likely when the morning is ALREADY wide (≥1%), not tight — realized-
vol persistence/momentum, the tell is a morning that's already moving. Consistent with the arc (vol is
a REGIME/persistence read; the blast is confirmation-not-prediction, #6). Recorded DEAD as a filter;
if anything, invert it (don't expect a blast off an ultra-quiet morning). Sign-free; 1-min range.

## Study #14 (B1/B2) — WALL bounce (calm) vs break (wild): a regime-conditional DIRECTION?  → NEGATIVE
`scripts/analyze_wall_break.py` (scenario catalog B). SAME geometry (spot approaching a wall) split by
regime: does HIGH concentration (calm) → REJECT (bounce away) and LOW (wild) → BREAK (continue
through)? Signed "push toward the wall" over 15/30/60m (day-detrended, ±8% clipped), concentration =
|vanna| (|charm| ≤1.5 DTE), same-distance MID-ZONE placebo. NIFTY 1239 / NIFTY_M 1239 / BankNifty 1222.

- **Direction NULL, all three books.** Push near a wall is ~0 in every tercile (a few bp vs a ~30 bp
  typical move); the LOW−HIGH break-vs-bounce contrast **flips sign across horizons/books** (NIFTY
  +0.2/+0.4/+0.3; NIFTY_M +1.3/−4.5/−3.2; BankNifty +0.3/+3.9/−1.2) and is not distinguishable from
  the mid-zone placebo. Walls do NOT call bounce vs break direction.
- **Containment (cross-rate) is the only real effect — and it's just the vol regime.** LOW-conc
  crosses the wall more often than HIGH-conc, consistent sign in all books (NIFTY +4.6/+5.5/+6.3pp;
  NIFTY_M +1.2/+1.8/+1.8; BankNifty +0.4/+1.8/+1.7). High concentration CONTAINS price inside the
  walls — a restatement of |vanna|→lower vol, not a directional edge (and weaker off-NIFTY).

**Conclusion:** the wall is a CONTAINMENT boundary (calm → fewer crossings, a vol statement), not a
directional pivot — even at the wall, the break is a coin flip. Direction stays dead; the wall's only
information is the vol regime we already price. Consistent with A1 (#12), the blast (#6), and the whole
arc. Recorded DEAD for direction (no dashboard chip beyond the existing vol gauge). Sign-free.

## Study #15 — concrete DAILY MOVE per regime bucket (|vanna| vs |charm| vs |GEX|)  → descriptive
`scripts/analyze_vanna_daily_move.py`. Translates the SIDEWAYS/TRENDY gauge label into the actual
per-day move: daily high-low range (and open→close), INTRADAY from today's open (overnight gap
excluded — the regime metric is intraday), robust stats (median ± MAD, IQR), 1-min spot, bad-tick
days (HL>12%) dropped. Bucketed by daily-median |metric| terciles. NIFTY.

- **NIFTY non-expiry — daily HL range by bucket:** |vanna| SIDEWAYS **0.61% (~140 pt)** → TRENDY
  **1.14% (~222 pt)**, a **1.86×** separation (best). |GEX| 0.66%→0.96% (**1.46×**). |charm|
  0.70%→0.86% (**1.24×**). Close-to-close move tracks it (vanna 0.30%→0.51%).
- **Expiry (≤1.5 DTE):** |vanna| 1.43×, |GEX| 1.16×, |charm| ~1.05× (≈flat). NB charm barely
  separates the FULL-DAY range — its validated edge (T1b) is the next-30-min forward RV near expiry
  (intraday timing), a different horizon; a daily-median bucketing washes it out.
- **BankNifty non-expiry:** |vanna| SIDEWAYS ~432 pt → TRENDY ~561 pt (same direction, bigger points).

**Use:** wired as the dashboard "expected daily move ≈ ±X pt" badge (`regime_daily_move_now` →
`_gex_regime_banner`), mirroring the gauge's tercile so the badge's bucket matches the shown label.
|vanna| is the daily-move discriminator; the badge reads from the 5-min cache spot (marginally
conservative vs 1-min). Descriptive (not a forecast test), sign-free.

## Study #16 (I) — Opening-Range Breakout (ORB) × pin regime + OR-width  → NEGATIVE
`scripts/analyze_orb.py` (scenario catalog I; user scenario). OR 15/30/60-min high/low as price S/R;
breakout = 1-min close beyond a boundary by ≥ max(0.10·OR,3bp); TRUE run = MFE ≥ 1×OR_range before
close; pin = concentration at OR-close (|vanna| off-expiry / |charm| ≤1.5 DTE). Sign-free, 1-min spot.
NIFTY 1230 days (775 non-expiry).

- **Pin tercile does NOT flip ORB follow-through.** TRUE-run rate flat across HIGH/MID/LOW pin
  (30-min 48/54/50%; 15-min 67/69/64%; 60-min 36/37/36%). Vol-partial ρ(pin, mfe_r|atm_iv) = **−0.07**
  (adds nothing beyond vol); OOS HIGH−LOW spread flips **+4pp → −8pp**. No B1/B2 flip.
- **The OR is almost always broken** (~99%, range-hold ~1%), so breakout *incidence* is non-
  discriminating — "smaller OR → more ORB" is **false**: every width breaks.
- **"Smaller OR → trendy" is a NORMALIZATION ARTIFACT.** In OR-multiples SMALL-OR looks trendier
  (true-run 58% vs WIDE 41%, mfe_r 1.26 vs 0.81) — but that's mechanical (÷ smaller OR). In ABSOLUTE
  terms it reverses: SMALL-OR mfe **0.25%** & day-HL **0.59%** vs WIDE-OR mfe **0.46%** & day-HL
  **1.17%**; **ρ(OR_range%, day-HL%) = +0.59** → a tight opening range predicts a *smaller* day
  (persistence, NOT a coiled spring) — consistent with Study #13/H.
- **The opening range isn't special:** a random mid-day 30-min range runs MORE (placebo TRUE-run 72%
  vs opening 50%) — the opening window is just the day's widest/most-volatile, so its OR-normalized run
  is the lowest; no special S/R power.
- The one real pattern is old news: day-HL by pin ≈ Study #15 baselines (0.62/0.80/1.17 vs
  0.61/0.81/1.14) — LOW-pin days simply have bigger ranges, but the ORB *run* is a ~50% coin flip.

**Conclusion:** ORB carries no directional/timing edge on Nifty and no pin interaction — the run just
re-expresses the vol regime (bigger on trendy days because the whole day is bigger, per #15), and the
"tight OR → big trend" folklore is a ratio illusion (absolute moves show persistence, #13). Consistent
with B1/B2 (#14 — price levels no better than OI walls) and the whole arc. Recorded DEAD. Sign-free.
**Robust to the concentration metric:** re-run with **|GEX| terciles** (`analyze_orb.py NIFTY gex`) —
same NEGATIVE (30-min TRUE-run 48/53/51%, vol-partial −0.04, OOS spread flips +4→−11pp); |GEX| separates
the day-HL slightly less than |vanna| (per #15), otherwise identical. The pin metric is now a CLI arg
(`auto` = vanna/charm switch, `gex` = |net_gex|).

## Study #17 — REGIME PERSISTENCE: is the vol-regime label sticky into tomorrow?  → POSITIVE (cross-book)
`scripts/analyze_regime_persistence.py`. The arc's FIRST controls-surviving positive. Point-in-time
(today known at close; tomorrow = next session, pairs only across gaps ≤4d). Day-HL from 5-min cache
spot; |vanna|(eod) = last-stamp |vanna| (the value you have at the close). Sign-free. NIFTY 1236 pairs,
BankNifty 973, NIFTY_M 1027.

- **(A) Realized-vol regime CLUSTERS** — Spearman(today-HL, tomorrow-HL) = **+0.34 / +0.36 / +0.26**
  (NIFTY/BN/NIFTY_M). Transition matrix strongly diagonal: SIDEWAYS→SIDEWAYS ~50%, TRENDY→TRENDY ~48%
  (vs 33% chance), ~1.9-day runs. Quiet begets quiet, wild begets wild.
- **(B) Today's |vanna| gauge ADDS a next-day forecast beyond realized vol** — median tomorrow-HL is
  monotone by today's |vanna| tercile (NIFTY SIDEWAYS 0.63% → TRENDY 0.94%), and the partial SURVIVES:
  ρ(today-vanna, tomorrow-HL) −0.30/−0.21/−0.24, **partial|today-HL = −0.22/−0.15/−0.18**, partial
  |today-HL+DTE = −0.20/−0.27/−0.26. So the label is NOT just a same-day vol read — today's positioning
  forecasts tomorrow's vol regime incrementally (option OI is sticky day-to-day, and positioning drives
  vol). All three books, both controls.
- **(C) The label is STICKY and real** — P(tomorrow same |vanna| tercile) = 57/73/73% raw, **56/59/62%
  DTE-stripped** (rank within DTE bin) vs 33% chance. The DTE control matters for the monthly/BankNifty
  books (bigger weekly cycle inflates the raw number) but genuine positioning persistence remains.

**Conclusion:** the SIDEWAYS/TRENDY vol-regime label has real **next-session predictive value** — it
persists (~56–62% same tomorrow, DTE-controlled) and forecasts tomorrow's realized range beyond today's
own move. Still VOL, not direction (consistent with the arc), but it means the gauge and the Study-#15
expected-move badge earn their keep as a **next-day prior**, not only an intraday read. Mechanistic
(positioning stickiness), cross-book. **Dashboard implication:** the regime read can be carried into the
next session with a "~50% likely to persist" note / a next-day expected-move. Sign-free.

## Study #18 (T4b close) — vanna-VRP hold-to-EXPIRY: is it TRADEABLE?  → NO (closes T4b)
`scripts/backtest_vanna_toexpiry.py`. The one untested horizon from #11b — sell the ATM straddle 09:20
and hold to EXPIRY settle (deterministic payoff |spot_expiry−K|, so no intermediate strike prices
needed), conditioned on entry |vanna|, IV+DTE controls, cost 2%, walk-forward OOS. NIFTY 725 holds.

- **Unconditional:** mean net **+2.87%**, median +19.2%, win 59% — the classic short-vol profile (keeps
  premium most days, rare big tail loss drags the mean; crash-risk compensation, not alpha).
- **The vanna residual does NOT survive to P&L:** Spearman(|vanna|, seller P&L) = −0.05, **partial|IV =
  +0.03, partial|IV+DTE = +0.017 ≈ 0** (cf. #11's +0.11 spot-RV partial — same fate as #11b intraday).
- HIGH−LOW vanna P&L is *negative* (−4/−17/−16% by DTE band), but that's the IV confound: LOW-vanna =
  HIGH-IV (23→12), i.e. it just re-expresses "sell high IV" (the dominant VRP driver, #11). Vanna adds 0.
- **OOS:** selected +8.6% vs unconditional +7.2%, but it sells 78% of test days (barely selective) —
  +1.4pp is noise given partial ≈ 0.

**Conclusion:** the #11 vanna-VRP residual is real *statistically* (spot-RV) but dies in tradeable
straddle P&L at BOTH horizons — intraday (#11b) and now hold-to-expiry (#18). The only thing that pays
is selling high IV generally (raw VRP = crash-risk compensation, priced), and |vanna| adds nothing
beyond IV. **T4b is closed: no tradeable vanna vol-alpha.** Vol stays a REGIME read (now with a
confirmed next-day persistence edge, #17), not a straddle-timing signal. Sign-free.

## Study #19 — model-free risk-neutral SKEWNESS (Bakshi-Kapadia-Madan) → direction?  → NO
`scripts/analyze_rn_skew.py`. The whole-chain successor to `analyze_skew.py` (a crude 3%-OTM
premium-ratio proxy). BKM recovers RN skewness by integrating OTM option **prices** across the full
09:20 cross-section — `V,W,X = ∫ g·M dK`, `M = call if K>S else put`; with the engine's `r=q=0` the
call/put kernels collapse to one continuous function of `x=ln(K/S)` and **RN skewness is T-free** (no
expiry-landmine dependence). Tested vs day-detrended forward return (intraday 09:20→15:15, next1/next2
15:15→15:15), IV+DTE vol-partial, tercile spread, walk-forward OOS. NIFTY 1233 days, BankNifty 1187.
Literature (Xing-Zhang-Zhao; Bali-Murray; Guo et al. SSE50-ETF): steeper/more-negative RN skew tends
to precede HIGHER returns cross-sectionally (skewness preference) — sign reported, not assumed.

- **Level sanity:** NIFTY median RN skew **−0.179** (correctly negative — equity index left tail is
  priced). BankNifty median **+0.060** (positive) — already a red flag that the truncated-±2.3% wing
  is not measuring a stable economic quantity across the two strike grids (NIFTY step 50, BN step 100).
- **IC is null and vol-fragile:** raw Spearman(RN skew, fwd) ranges −0.01…−0.07 both books; **partial
  |IV+DTE ≈ −0.005…−0.065** — no horizon clears noise.
- **Tercile spread FLIPS sign across books:** NIFTY intraday HIGH−LOW **−0.33%** but next1/next2
  **+0.08/+0.28%** (not monotone); BankNifty all-positive (+0.46/+0.99/+0.46%) — i.e. the *opposite*
  cross-book sign. The one eye-catching number (BN next1 HIGH-skew +1.07%) is contradicted by NIFTY.
- **OOS confirms nothing:** frozen-edge long-low/short-high spread +0.09% (BN) / −0.11% (NIFTY), both
  ≈ noise and neither agreeing with its own in-sample tercile direction (the skew *level* drifts across
  the 5-yr window, so frozen edges barely populate the test-set "low" bucket — 30–67 days).

**Conclusion:** RN skewness carries **no stable directional edge** for either index — null IC,
sign-flips across books, no OOS confirmation. Extends the direction-is-dead arc (#12/#14/#16 pins,
participant-OI, futures) to the option-implied-asymmetry family the literature is strongest on. Two
honest caveats keep it from being a *strong* null: the ATM±10 store truncates the wings at ~±2.3% (the
tail integral BKM most needs is clipped, and the NIFTY↔BN sign flip may be a truncation artifact of the
different strike grids), and these papers are cross-sectional (rank many names) whereas we have one
index time series. A full-wing chain (deep OTM) could revisit it, but the prior is now firmly negative.
Sign-free target; not a trade rule.

## Study #20 — signed INTRADAY momentum (Gao-Han-Li-Zhou): first 30m → last 30m?  → NO
`scripts/analyze_intraday_tsm.py`. The cheap spot-only directional baseline: does the first 30-min
return (09:15→09:45) predict the last 30-min return (14:45→15:15)? Point-in-time clean (non-overlapping
windows), both day-detrended, cost 3bps, high-IV split + Gao "prior-day-last" combo + OOS. Distinct
from #13 (which found morning *range* predicts afternoon *range* — vol persistence, not signed return).

- **Null both books on robust stats:** Spearman(r_first, r_last) = **+0.040 / −0.047** (NIFTY/BN —
  sign even flips), sign-agreement **52.8% / 49.3%** (≈ 50% chance). Tercile NOT monotone (NIFTY's
  FLAT-morning bucket has the *highest* last-half mean; BN inverted).
- **Not tradeable:** enter-14:45-in-morning's-direction nets +0.07% NIFTY (win 45%) but **OOS −0.024%**;
  BankNifty −1.49% (outlier-inflated means — lead on the rank IC, which is null). Gao combo negative.
  High-IV subsample net +0.17% NIFTY (in-sample only, unconfirmed).

**Conclusion:** signed intraday momentum is **dead** on Nifty — IC ≈ 0, sign-agreement at chance,
non-monotone, OOS negative, cross-book sign-flip. Only the VOL persistence (#13, morning range →
afternoon range) survives; the signed-return version does not. Directional bet, day-detrended, sign-free
of the GEX landmine.

## Study #21 — IV SKEW (Xing-Zhang-Zhao) level & change → direction?  → NO
`scripts/analyze_iv_skew_change.py`. The two-point IV-smirk workhorse: `skew_xing = IV(2%OTM put) −
IV(ATM)` and the risk-reversal `rr = IV(2%OTM put) − IV(2%OTM call)`, from the real per-strike Dhan IV
smile at 09:20. Three predictors — LEVEL, DAILY Δskew, and the intraday-Δskew MOMENTUM (09:15→09:45 →
return 09:45→15:15, point-in-time) — vs day-detrended forward returns, IV+DTE vol-partial, tercile, OOS.

- **Measurement is CLEAN (a strong null):** skew_xing median **+4.58** vol-pts (NIFTY) / **+2.86** (BN),
  rr +3.10 / +2.90 — correctly positive, put IV richer than ATM (downside fear priced), and *stable
  across both books* (unlike the truncated BKM moment #19, which flipped sign). So the signal is
  genuinely absent, not obscured by measurement.
- **No predictor clears noise:** every IC (level, Δskew, momentum) sits in **±0.01…±0.07** raw and
  after partial|IV+DTE. The largest cross-book-consistent one is intraday-Δskew momentum (−0.027 /
  −0.058) but it's tiny, vol-fragile, and unconfirmed by tercile/OOS.
- **Tercile & OOS inconsistent:** NIFTY intraday HIGH−LOW +0.26% but next1 −0.08% (flips); BankNifty
  the opposite pattern (+0.06% / +0.64%). OOS next1 spread −0.002% (NIFTY) / +0.089% (BN) ≈ noise.

**Conclusion:** IV skew — level or change — carries **no stable directional edge** for either index.
Because the level sign is economically correct and cross-book-stable, this is a *strong* null: the
best-documented two-point option-skew signal in the literature simply doesn't predict Nifty direction.
Joins RN-skew (#19), momentum (#20), pins (#12/#14/#16), participant-OI, and futures. Sign-free.

---

### Direction-family scoreboard (as of Study #21)
Every option-implied *directional* signal the literature pull flagged has now been tested on both books
and come back **NULL**: dealer-gamma pins (#12/#14/#16), participant/OI flow, futures, RN skewness
(#19, BKM), signed intraday momentum (#20), IV-skew level & change (#21). The single surviving edge is
**vol/short-premium** — sell rich IV (S6/S11) plus the confirmed next-day *vol*-regime persistence
(#17). Direction is dead on Nifty at every horizon we can measure; the tool is decision-support, and
the one thing that pays is being the vol seller, sized for the tail. This closes the directional
re-examination prompted by the Claude-Web literature review.

## Study #22 — futures BUILD-UP × VOLUME conviction (daily) → does volume sharpen direction?  → NO
`scripts/analyze_futures_buildup_volume.py`. Daily raw F&O bhavcopy (instr IDF, **NIFTY 543 days**;
BankNifty not backfilled → single-book). Per day: front-contract Δprice, aggregate ΔOI (rollover-netted)
→ price×OI build-up label; turnover = agg_vol/agg_oi as conviction. Target = next-day spot return,
day-detrended. Directly tests "wire volume into the dashboard build-up tag as conviction".

- **Volume as naive conviction HURTS:** plain build-up direction IC +0.039 → **×volume (amplify) +0.024**
  (worse) → ÷volume (fade) +0.053 (marginally better). The "high volume = trust it more" form is the
  worst of the three, echoing the §4a options-streak finding (high turnover ≈ exhaustion, not conviction).
- **Conviction split is not a clean confirm:** high-vol amplifies the ΔOI direction, not the price×OI
  label — `long build-up` high-vol +0.22% (confirms) but `short build-up` high-vol +0.09% (a bearish
  label going UP → reverses). Mixed by quadrant.
- **Multi-factor leave-one-day-out OOS:** build-up-alone has **no OOS signal** (LOO IC −0.24, in-sample
  +0.04 collapses). Adding momentum (prior 1-/5-day return), turnover, or ΔOI-intensity — every combo is
  "base has no signal — moot"; none yields a stable positive OOS IC.

**Conclusion:** volume does **not** sharpen the futures build-up direction — the naive-conviction form
measurably degrades it, and there's no directional signal in the daily build-up for any factor to improve.
**Dashboard call: keep volume as a displayed intensity gauge, do NOT fold it into the direction tag.**
Sign-free of the GEX landmine.

## Study #23 — intraday BUILD-UP × MOMENTUM (5-yr proxy) → does momentum improve the direction?  → NO (it's reversion, and it subsumes the build-up)
`scripts/analyze_intraday_buildup_momentum.py`. The multi-year stand-in for the intraday futures
build-up (intraday futures OI caps at ~90 days). On the 5-min options cache (**NIFTY 1239 / BankNifty
1222 days, ~88k stamps each**): build-up = classify(sign Δspot(15m) × sign ΣΔOI(15m)); momentum =
trailing 30-/60-min return; target = forward 30-/60-min return, **per-day demeaned** (strip the day's
drift). Honest metrics: per-day-averaged IC (day = independent unit) + day-block 70/30 OOS.

- **Everything is intraday MEAN-REVERSION, cross-book:** all univariate per-day ICs NEGATIVE — mom60→fwd60
  **−0.247 (NIFTY) / −0.267 (BN)** (±0.008 over ~1215 days), build→fwd60 −0.084 / −0.104. Recent move
  fades; the OLS combos' *positive* OOS IC is just the model **fading** momentum (fitted weight sign −).
- **Momentum is ~3× stronger than the build-up, and SUBSUMES it:** OOS build+mom60→fwd60 = **+0.191 =
  mom60 alone** (NIFTY, build adds nothing); BankNifty build+mom60 **+0.104 < mom60-alone +0.176** (adding
  the build-up *hurts*). The build-up is the weak link; momentum replaces it, doesn't complement it.

**Conclusion:** adding intraday momentum raises the fitted IC, but only because momentum-**fade** (generic
mean-reversion, §5) is a much stronger signal than the build-up — which it *subsumes* (build adds ~0 on
NIFTY, negative on BankNifty). So this is not "momentum sharpening the build-up"; it's "replace the weak
build-up with a reversion signal." And that reversion is the known generic intraday mean-reversion, whose
tradeability net of 5-min bid-ask/costs is doubtful (§5 thrust work: reverts but doesn't pay after costs).
No positioning edge; sign-free of the GEX landmine.

## Study #24 — does the intraday MEAN-REVERSION MONETIZE on the near-month FUTURE, and is it high-|vanna|?  → NO (dead after cost; the mechanism is refuted)
`scripts/backtest_futures_reversion.py`. The cost-and-regime test of the ONE non-noise intraday effect
(#23's mom→fwd IC ≈ −0.25): does fading the trailing move PAY when executed on the near-month **future**
(~3-5 bps round-trip) instead of 5-min **option** round-trips (~3%, which killed it in #3/#6b), and does
the net edge concentrate in the high-|vanna| (SIDEWAYS) regime the dealer-dampening mechanism (#1/#8)
predicts? Signal = `pos = −sign(trailing Δ-min return)`, fill t+1min, exit t+h; **Δ,h ∈ {15,30,60}**;
cost swept **{0..8} bps**. Price path = the raw **1-min spot** (5-yr proxy for the future), costed as the
future; regime = book-wide |vanna| tercile at t. Point-in-time (signal ≤t, fill t+1), sign-free of GEX.
Pre-registered PASS = ALL of: net>0 after ≥4 bps · monotone LOW→HIGH |vanna| · OOS survives · beats the
direction-shuffle + random-window placebos · spot tracks the actual future.

- **DEAD on the clean feed (NIFTY, 1235 days, ~600k trades):** the raw fade **loses at ZERO cost** — gross
  −0.001 to −0.006%/trade, **breakeven −0.1 to −0.6 bps** across the whole Δ×h grid; the direction-shuffle
  placebo confirms it is significantly *negative* (real −0.0028% vs shuffled 0.000, **z=−4.2**). At 15-60m
  the raw path carries enough persistent drift that fading it bleeds. Reversion only appears AFTER
  **day-detrending** (+0.008%/trade ≈ **0.8 bp**) — a look-ahead diagnostic (needs the day's full drift,
  unknowable live) and still far **below a 4 bp cost**. NIFTY_M (monthly book, same index) = **identical**.
- **Mechanism REFUTED — not high-|vanna|:** the |vanna| tercile split is **non-monotone** (LOW −0.008 / MID
  +0.004 / HIGH −0.003 @30×30), HIGH−LOW +0.005% but noise; **partial(fade P&L, |vanna| | IV, DTE) = +0.008
  ≈ 0**. Fading does NOT pay more when dealer-gamma is high — the dealer-dampening story does not monetize.
  OOS (day-block 70/30) gross decays to **+0.0002%** (net@4 −0.040%). Every criterion fails except one.
- **Spot IS a sound path proxy (the one PASS):** on the ~50-day intraday-futures overlap the strategy on the
  **actual future** vs the spot proxy has per-trade P&L **corr +0.959 (NIFTY) / +0.966 (BankNifty)**, mean
  |basis drift| 8 / 26 pts over a 30m hold — so the 5-yr spot-proxy backtest is trustworthy. (But a valid
  proxy is not an edge.)
- **⚠ Landmine caught in the act — phantom reversion from corrupt spot:** the raw **NIFTY_M / BANKNIFTY**
  spot feeds manufactured a spectacular FAKE edge (+0.36% / +0.99%/trade) that a naive run would have
  reported as a win. Diagnosis: the same-underlying cross-check — **NIFTY_M shares NIFTY's index, yet read
  +0.36% while NIFTY read −0.003%**; swapping NIFTY_M onto the clean NIFTY feed collapsed it to −0.003%
  (identical to NIFTY). Root cause = **sustained ~2.2× scale corruption** in those stores' `spot` column
  (e.g. 47,672 printed for a true 21,646, for *runs* of bars) — a snap-back cleaner alone misses it; it
  took a long-window (±30-bar) robust median guard **plus** routing NIFTY_M through the clean feed. The
  residual BankNifty positive is **entirely pre-2025** (era: pre-Nov24 +0.11%, Nov24-Aug25 +0.0002%, Sep25+
  −0.002%) and its **OOS = +0.0006%** — dead once the contaminated era is excluded.

**Conclusion:** the known intraday mean-reversion does **not** monetize on the future — it loses at zero
cost raw, its detrended (look-ahead) residual is ~0.8 bp << realistic 3-5 bp futures cost, it is **not**
concentrated in high-|vanna| (mechanism refuted), and it dies OOS. Confirms the arc: the fade is real as a
rank-IC but not a tradeable edge net of cost, at any Δ×h, cross-book. **Data-quality flag for all future
work: the NIFTY_M and BANKNIFTY `spot` columns carry sustained pre-2025 corruption — cross-check against
the clean NIFTY feed or restrict to Sep25+/OOS.** Sign-free of the GEX landmine.

## Study #25 — does MARKET PROFILE (TPO on the underlying) add anything — direction or vol-regime — beyond today-HL + |vanna|?  → NO (direction dead under the mirror; vol content subsumed by today-HL)
`app/market_profile.py` + `scripts/analyze_market_profile.py`. A TPO **time-at-price** profile is a
*realized-behaviour* level (where price actually spent time), not a positioning level — and #12 found
pinning is *stickiness, not attraction*, so a profile is plausibly the cleaner encoding of that effect
than peak-OI. Built at native **1-min** (finer than the traditional 30-min brackets): POC = modal price
bin, value area = the 70% time band (VAH/VAL), plus the day-type **SHAPE** stats that are the real
content — value-area width ÷ range, distribution **mode count**, and **time-at-POC ratio** (a
realized-vol-regime descriptor). NIFTY, **1234 clean-spot days** (2021-07→2026-07); bad-tick days
(>12% HL) dropped. Point-in-time: today's FULL profile is known at today's close → clean for predicting
TOMORROW; the same-day POC look-ahead trap (the #5 +0.64→+0.02 shape) is avoided by testing next-day only.

- **DIRECTION — dead under the mirror rig:** prior-day POC as a next-day attractor — price closes TOWARD
  the POC **50%** vs **50%** toward its same-distance MIRROR (POC ≈ mirror). The same null the
  mid-zone/mirror placebos delivered in #12/#14/#16; a POC is the same geometry with a different
  construction, and it fails the same way.
- **VOL-REGIME (Test A) — shape has raw IC but it is a RESTATEMENT of today-HL:** today's profile shape →
  tomorrow's realized HL%: **tpoc_ratio IC −0.316**, **n_modes +0.239** (raw, real-looking) — but both
  **collapse under today-HL** (−0.061 / −0.048) and fully under HL+|vanna| (−0.065 / −0.012);
  `va_width_pct` is ~0 throughout. The shape is re-encoding today's range (vol clustering, #17), not new
  information — fails the incremental bar exactly as the build-up did in #23.
- **VOL-PRICING (Test B) — realized value-area vs implied move, weak + unstable:** `accept_pct` (VA
  half-width, ~1σ realized) → tomorrow HL IC **+0.309** but **−0.004 under today-HL** (it IS today's
  range). The ratio **r = accept ÷ implied** (realized acceptance ÷ σ√τ at the open; <1 ⇒ vol
  over-priced) is only **+0.066** raw and **sign-unstable across controls** (−0.199 | today-HL, +0.187 |
  ATM-IV) with **OOS decay +0.092 → +0.028** — the classic decays-to-zero shape; its non-zero IV-partial
  is today-HL's vol-clustering leaking through, not an IV-orthogonal MP signal.

**Conclusion:** Market Profile on Nifty carries **no direction edge** (POC = its mirror) and **no
vol-regime information beyond today's realized range + |vanna|** (every shape/acceptance IC collapses
under today-HL) — a clean null, the same "statistically real residual that adds nothing incremental"
shape as vanna-VRP (#11/#18). The levels remain a legitimate *decision-support map* (where price
accepted), not a signal. **Pre-registered follow-up (ONE run, bar set here):** Test B measured `r`
against tomorrow's *range* when the hypothesis was vol *over-pricing*, so the correct target is the
**forward short-straddle P&L**, not spot-RV (the exact venue where #11's +0.11 residual died in
#11b/#18). It SURVIVES only if — all four — the partial of `r` against the existing sell-IV/VRP signal
is non-zero, the sign is stable across the **IV and DTE** controls, it clears **walk-forward OOS on
actual straddle P&L**, and it is strictly point-in-time (`r` known at entry, payoff measured after) with
**no contemporaneous-VRP leakage** (r ≈ realized÷implied ≈ the VRP itself). Prior: **low** (r already
sign-unstable + OOS-decayed; the arc's rule is that real residuals don't monetize). If it fails any
criterion it is a **footnote here, not a #26**. Sign-free of the GEX landmine.

> **Follow-up RESULT — ran once, FAILED the bar, MP closed** (`scripts/backtest_mp_straddle.py`,
> 1156 NIFTY days). r(t−1) → the ATM short-straddle **seller** return(t) (enter t open, exit t close;
> median seller ret +17.2% of premium): raw IC only **+0.052**, IV-entry partial **+0.049** (→ **+0.019**
> adding DTE). It fails every criterion — **(4) the prior-VRP partial is +0.009 ≈ 0** (control for
> yesterday's realized÷implied and it vanishes → r IS the VRP restated, the contemporaneous-VRP leak the
> bar was built to catch); **(2) sign is unstable** (IV terciles +0.083 → **−0.016** flips; DTE terciles
> +0.005 → +0.120 swings); **(3) OOS decays** +0.065 → +0.029. So the vol-over-pricing residual does **not**
> monetize into forward straddle P&L — the first-time-would-have-been-surprising outcome that, per the arc
> (#11b/#18), did not happen. Market Profile is fully closed: a decision-support map, not an edge.

**#25 addenda (two extension tests, both confirming the null from new angles):**

*(a) Do our FORWARD-PRICED option levels predict where price will later ACCEPT?* — the hunch that
options, being forward-priced, should carry information about tomorrow's POC / VAH / VAL.
`scripts/analyze_mp_vs_options.py`, 1234 NIFTY days, EOD option levels of day t → the TPO of t+1 …
t+H (1D/1W/2W/1M composites), everything in **deviations-from-today's-close** to kill the
trivial-anchor trap (magnet ≈ max-pain ≈ POC ≈ close, all near spot). **NO.** The naive **close is
the best POC predictor at every horizon** and no option level beats it: at 1D, close error 110 pts,
**magnet 118, max_pain 125** (both WORSE), and the deviation-corrs are weak and the **wrong sign**
(magnet −0.06, max_pain −0.10 — a faint *anti*-prediction, the opposite of "price gravitates to the
level"), partial-beyond-today-POC ≈ −0.04/−0.06. Walls → VAH/VAL: corr ~+0.01–0.04 (no relationship);
at 2W/1M a wall's error is marginally below a single noisy day's VAH, but that is *lower variance*,
not prediction (corr ≈ 0.01). So the forward-priced intuition does **not** translate — option
positioning clusters near spot too, and once the common anchor is removed the residual is ~0 with a
faint wrong sign. Option levels are a positioning *map*, not a predictor of realized acceptance.

*(b) Does the price DISTRIBUTION (POC momentum / stretch / shape / day-type) predict direction or
reversals?* — `scripts/analyze_mp_shape_direction.py`, 1234 NIFTY days, all point-in-time (feature at
t's close → t+1). **NO, on every construction.** POC MOMENTUM (5D %Δ) → next return IC **+0.014**,
partial | price-momentum **+0.022** (and price-mom itself is +0.007) — POC momentum is price momentum
restated, and neither predicts. STRETCH from value (close−POC) → next return IC **+0.020** but
**−0.006** controlling for today's own return — no reversion-from-value. SHAPE → *direction*: all ~0
(n_modes +0.007, tpoc +0.009, va_width −0.042). SHAPE → next-day *|move|*: `tpoc_ratio −0.146`,
`n_modes +0.101` are real but **collapse under today-HL** (→ −0.009/−0.050) — vol-clustering restated
(#25 again). DAY-TYPE BOXES (close vs the value area): next-day win% spans **47–55%** against a **52%
base**, **non-monotone** (below-VAL 54, lower-VA 53, upper-VA 47, above-VAH 55) — noise. The only
repeatable thing is that a **single-print day → a calmer next day** (0.52% vs 0.67% next |move|) — a
legitimate *vol-regime* read (rotational→calm), which is exactly the decision-support use and exactly
the content already in today-HL + |vanna|; it says nothing about direction. Confirms the arc: the
distribution is a regime/vol descriptor, never a directional signal — which is why it ships as the
labelled POC/value overlay beside the walls, not as a trigger.

---

**Scoreboard update (as of #25):** the directional nulls now also cover *combining* factors — volume
conviction (#22) and intraday momentum (#23) neither rescue nor complement the price+OI build-up — and the
one non-noise effect, generic mean-reversion, is now shown **not tradeable even on the cheap futures leg**
(#24: loses at zero cost, mechanism refuted, dead OOS). And **Market Profile** (#25) — realized
time-at-price levels, a genuinely different data source (where price *accepted*, not where OI is *open*) —
nulls the same way: direction = its own mirror, and the day-type shape is subsumed by today-HL + |vanna|,
so it adds nothing incremental (and the pre-registered vol-over-pricing follow-up on forward straddle P&L
was run once and FAILED the bar — the ratio is contemporaneous VRP restated, prior-VRP partial +0.009,
sign-unstable, OOS-decayed — so MP is fully closed). The only surviving edge remains **vol/short-premium**
(sell rich IV S6/S11 + next-day vol-regime persistence #17). Direction is dead on Nifty at every horizon and
every execution venue we can measure; the tool is decision-support, and the one thing that pays is being the
vol seller, sized for the tail.
