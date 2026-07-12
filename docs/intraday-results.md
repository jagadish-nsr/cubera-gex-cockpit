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
