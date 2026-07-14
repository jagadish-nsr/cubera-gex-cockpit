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
