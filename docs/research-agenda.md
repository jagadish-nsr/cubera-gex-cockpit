# Research agenda — what the OI/options/futures literature says, and what WE can test

A survey of the academic + practitioner literature on options/futures **open interest**,
mapped to the data this project already captures. Goal: find studies that unlock value from
our data. Read with `CLAUDE.md` (the dealer-sign landmine) and `docs/futures-buildup.md`.

## The single most important reframe (read first)
**The documented OI→return predictability lives at DAILY-to-MONTHLY horizons, not intraday.**
Every academic result below that finds a *directional* edge measures it over days to a month
(OI quintiles → 62 bps/month; PCR → 2.5–12 days; futures interest → monthly). Our intraday
15/30/60-min studies (streaks, build-ups, turnover) keep finding **no directional edge and
mean-reversion** — which is *consistent with the literature*, not a failure. The intraday
edge in the literature is about **volatility and pinning (sign-free), not direction.**

So split the program by horizon:
- **Intraday → volatility & pinning** (dealer gamma, VPIN-style flow, max-pain pull). Sign-free,
  literature-strong. This is where our 5-min data is a genuine asset.
- **Daily+ → direction** (OI imbalance, hedging pressure, participant positioning, PCR, skew).
  Needs the daily journals to accrue; this is where the futures/participant/PCR data pays off.

Our recurring "turnover = size not direction; high-turnover build-up reverts" finding is
exactly what demand-based pricing + VPIN predict: **flow moves price then reverts; conviction
marks toxicity/exhaustion, not trend.**

---

## Literature map (theme → key work → what it found)

### 1. Options OI predicts returns (informed trading) — *daily+/direction*
- **Zhou, "Why does option open interest predict stock returns?"** — sorting on past option OI
  gives ~**62 bps/month** spread; **OTM-call OI growth** predicts market returns. [pdf](https://acfr.aut.ac.nz/__data/assets/pdf_file/0004/686830/1b-Yi-Zhou.pdf)
- **Seo (2020), "Index options open interest and stock market returns," J. Futures Markets** —
  *index-level* OI (not single-stock) predicts market returns — the closest analogue to Nifty.
  [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1002/fut.22095)
- **Pan & Poteshman (2006), "The information in option volume for future stock prices," RFS** —
  a **signed** put/call *volume* ratio (buyer-initiated, from a proprietary dataset) predicts
  next-day returns; the public unsigned PCR is much weaker. Motivates *signing* our flow.
- Practitioner: [trading on OI info, US equity options](https://www.researchgate.net/publication/305194232_Trading_on_the_information_content_of_open_interest_Evidence_from_the_US_equity_options_market).

### 2. Demand-based option pricing (the theory under GEX) — *mechanism*
- **Gârleanu, Pedersen & Poteshman (2009), "Demand-Based Option Pricing," RFS** — dealers are
  risk-averse and can't hedge perfectly, so **end-user net demand moves option prices**
  (∝ variance of the unhedgeable part). End-users are net-long index puts → explains the
  skew/expensiveness. This is the theoretical backbone for reading OI as *positioning* and for
  the whole dealer-hedging/GEX edifice. [NBER](https://www.nber.org/papers/w11843) ·
  [pdf](http://docs.lhpedersen.com/DBOP.pdf)

### 3. Dealer gamma → realized volatility — *intraday, SIGN-FREE, strongest for us*
- **SqueezeMetrics, "Gamma Exposure (GEX)" white paper** — aggregate dealer gamma is **inversely
  correlated with realized volatility**: short-gamma dealers hedge *with* the move (amplify),
  long-gamma *against* it (dampen). Explains much of intraday realized vol. [pdf](https://squeezemetrics.com/monitor/download/pdf/white_paper.pdf)
- **CBOE, 0DTE index options & market volatility** — quantifies dealer-hedging impact on
  intraday vol from short-dated options (relevant to Nifty's weekly/expiry dynamics). [pdf](https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf)
- We already compute `net_gex` per snapshot → **this is the most defensible study we can run**
  (sign-free: |GEX| predicts |return|, not its direction).

### 4. Hedging pressure in futures → returns — *daily+/direction*
- **Hong & Yogo (2011), "What does futures market interest tell us…?," NBER** — **aggregate
  futures open interest** predicts commodity/bond/currency returns (a macro risk signal).
  [pdf](https://www.nber.org/system/files/working_papers/w16712/revisions/w16712.rev1.pdf)
- **Fernández-Pérez et al., "Hedging Pressure and Returns in Futures: Evidence Across Asset
  Classes"** — hedging-pressure (net positioning) risk premium in commodity, **equity**, and FX
  futures. [pdf](https://acfr.aut.ac.nz/__data/assets/pdf_file/0007/188161/AFP-Hedging-pressure-everywhere_Adrian-Fernandez-Perez_2018-DMC.pdf)
- Direct analogue: our **futures OI build-up** + **participant-wise net positioning** (FII).

### 5. Price–OI–volume (long/short build-up) — *the framework we use; evidence is asymmetric*
- The 4-quadrant matrix (long build-up / short build-up / short covering / long unwinding) is
  practitioner-standard; **volume tells substance vs fade**. [tradejini](https://www.tradejini.com/blogs/how-to-interpret-open-interest-and-price-data-a-traders-guide)
- Academic: **asymmetric, regime-dependent** OI/volume→price relations (QNARDL, commodity
  futures) — no clean linear edge, which matches our nulls. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0301420721002889)

### 6. Put–call ratio (India-specific) — *daily/direction*
- **MDPI Economies, "Put–Call Ratio Volume vs. Open Interest in Predicting Market Return"** —
  on **NSE Nifty (2001–2013)**: **OI-PCR predicts at ~12 days**, **volume-PCR at ~2.5 days**;
  robust to futures info. India nuance: **writer-dominated → high PCR = bullish** (matches our
  landmine notes). [MDPI](https://www.mdpi.com/2227-7099/7/1/24)

### 7. Participant-wise OI (FII/DII/Client/Pro) — *daily/direction, India*
- Practitioner consensus: **FII index-futures net positioning leads** market direction; DII
  hedges, Pro/Client are short-term. Academic coverage thin → *original research opportunity*.
  [Sensibull](https://web.sensibull.com/fii-dii-data/fno) · we already capture this EOD.

### 8. IV skew/smirk predicts returns — *daily/direction*
- **Xing, Zhang & Zhao (2010), "What does individual option volatility smirk tell us about
  future equity returns?," JFQA** — steep put smirk → **lower** future returns (informed put
  buying). [RG](https://www.researchgate.net/publication/46543470_What_Does_Individual_Option_Volatility_Smirk_Tell_Us_About_Future_Equity_Returns)
- **Guo (2023), "Term spreads of IV smirk and variance risk premium," JFM** — smirk term-spread
  predicts straddle/variance-swap returns. [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1002/fut.22409)
- We have per-strike premiums → can build a skew proxy (OTM-put vs OTM-call richness).

### 9. Pinning / max-pain at expiry — *intraday, SIGN-FREE, extends gamma-blast*
- **Ni, Pearson & Poteshman (2005), "Stock price clustering on option expiry dates," JFE** —
  prices **cluster at high-OI strikes** on expiry via delta-hedging; effect ~**0.65%**, stronger
  with large OI. [RG](https://www.researchgate.net/publication/222573093_Stock_price_clustering_on_option_expiry_dates)
- Max-pain gravitational pull is real but modest (1–2% of moves; stronger on big-OI expiries).
  [CXO Advisory](https://www.cxoadvisory.com/equity-options/stock-price-pinning-at-options-expiration/)
- Direct test for our **magnet / max-pain / gamma-blast** on Nifty expiry days.

### 10. Order-flow toxicity / VPIN — *intraday, volatility/jumps*
- **Easley, López de Prado & O'Hara, VPIN** — buy/sell **volume imbalance** in volume-buckets
  proxies informed-trading toxicity and **predicts short-term volatility & price jumps** (not
  direction). [pdf](https://www.stern.nyu.edu/sites/default/files/assets/documents/con_035928.pdf)
- We have per-strike & futures **volume** → a VPIN-style toxicity gauge (approximate signing via
  the build-up/tick rule). Ties directly to our "high-turnover build-up reverts" result.

---

## Our data assets (what we can actually compute on)
| Data | Cadence | Fields | Journal |
|---|---|---|---|
| Index futures | 5-min intraday + EOD bhavcopy | price, OHLC, OI (near+agg), volume, ΔOI | `futures_intraday/`, `futures/` |
| Per-strike options | 5-min | OI, ΔOI (all-exp + exact near-exp), premium, volume, net_gex | `oi_strikes/` |
| GEX levels | 5-min | walls, flip, magnet, max-pain, net_gex, PCR | `timeseries/` |
| Participant OI | EOD | FII/DII/Client/Pro fut+opt long/short | `participant_oi/` |
| India VIX, USDINR, news gauge | 5-min | context | `timeseries/` |

## Prioritized studies we can run (mapped to the literature)
Ranked by *strength of evidence × feasibility with our data × sign-free (dodges the landmine)*.

1. **Dealer-gamma → intraday realized vol** (SqueezeMetrics §3). Sign-free, strongest.
   `net_gex` (or |GEX|) at t → realized |return| over next 15–60m. We started this in
   `analyze_timeseries.py` (both-signs RV buckets) — formalize it as the headline intraday study.
2. **Max-pain / magnet pull on expiry** (Ni-Pearson-Poteshman §9). Sign-free. Does spot drift
   toward the high-OI strike / magnet into the close on expiry days? Extends `gammablast` +
   `analyze_blast`. We have magnet/max-pain + intraday spot.
3. **VPIN-style toxicity → vol/exhaustion** (§10). Sign-free. Build a signed-volume-imbalance
   gauge from futures + per-strike volume; test it as a **fade/exhaustion** signal — the formal
   version of "high-turnover build-up reverts."
4. **Daily OI-imbalance / OTM-OI-growth → next-day direction** (Zhou, Seo §1). The signed
   informed-flow signal (OTM-call vs OTM-put ΔOI growth) → next-day Nifty return. Needs the
   daily journals to accrue; this is the *right-horizon* direction test.
5. **Hedging pressure: FII futures net positioning → returns** (Hong-Yogo, Fernández-Pérez §4).
   Extends `analyze_participant_ic.py` to the hedging-pressure framing (net positioning drift).
6. **PCR-OI (12d) vs PCR-vol (2.5d) horizon replication on Nifty** (MDPI §6). We have PCR;
   replicate the frequency-domain horizon finding on our data.
7. **IV-skew proxy → next-day return** (Xing-Zhang-Zhao §8). Build skew from per-strike premiums;
   steep put-skew → lower forward return.
8. **Combined conditioning** (demand-based §2): gate the daily direction signals by the intraday
   gamma-vol regime (calm long-gamma vs wild short-gamma) — the synthesis.

## Worth pulling full-text via your university login
The paywalled ones most worth reading in full for methodology we'd borrow:
- **Gârleanu, Pedersen & Poteshman (2009), RFS** — the demand-pressure model (do dealers hedge;
  how to sign end-user demand).
- **Pan & Poteshman (2006), RFS** — how they *sign* option volume for the predictive PCR.
- **Ni, Pearson & Poteshman (2005), JFE** — the pinning identification (clean event-study design).
- **Seo (2020), JFM** — index-level OI predictor construction.
- **Xing, Zhang & Zhao (2010), JFQA** — the smirk measure definition.
- **Hong & Yogo (2011), NBER** — aggregate-OI signal construction.

## Honest caveats
- Most cited edges are **US/large-sample**; India (writer-dominated, retail-heavy, weekly-expiry
  churn) may differ — re-validate, never assume (CLAUDE.md).
- Public OI/PCR is **unsigned**; the strongest results (Pan-Poteshman) use *signed* flow we must
  *approximate* (build-up/tick rule) — a known weakness.
- Everything here is **decision-support research, not a trade rule**; point-in-time discipline,
  de-trending, and honest small-n caveats apply (see the `analyze_*` scripts).
