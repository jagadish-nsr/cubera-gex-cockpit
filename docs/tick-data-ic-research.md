# Tick-data Information Coefficient — research & reading list

Deep-research pass (2026-07-27) behind the big-money tape's flow-IC harness. The organizing
finding: **contemporaneous** order-flow→price links are strong and robust; **predictive**
(future-return, i.e. what IC measures) links are weak, regime-dependent, and decay within
minutes. Two BankNifty studies independently reproduce our own ~zero directional edge once the
spot component is removed. See the memory note `tick-data-ic-research` for the condensed version.

## Papers to download (Uni account) — the 5 high-value gated ones

1. **Kolm, Turiel & Westray — "Deep Order Flow Imbalance: Extracting Alpha at Multiple
   Horizons from the Limit Order Book."** *Mathematical Finance* 33(4), 2023.
   DOI: 10.1111/mafi.12413 · Wiley: https://onlinelibrary.wiley.com/doi/10.1111/mafi.12413 ·
   SSRN 3900141: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3900141
   *The definitive "IC from order flow" paper — 115 Nasdaq names, per-horizon OOS results,
   order flow (stationary) beats raw book. Get the per-horizon IC/R² tables.*

2. **Cont, Kukanov & Stoikov — "The Price Impact of Order Book Events."**
   *Journal of Financial Econometrics* 12(1):47–88, 2014.
   Oxford: https://academic.oup.com/jfec/article-abstract/12/1/47/816163 ·
   free preprint (arXiv 1011.6402): https://arxiv.org/pdf/1011.6402
   *Foundational linear OFI→price impact model (slope ∝ 1/depth). CONTEMPORANEOUS, not a
   forecast — the published version has the cleaner tables.*

3. **Easley, López de Prado & O'Hara — "Flow Toxicity and Liquidity in a High-Frequency
   World" (VPIN).** *Review of Financial Studies* 25(5):1457–1493, 2012.
   Plus the critique: **Andersen & Bondarenko (2014), "VPIN and the Flash Crash,"**
   *Journal of Financial Markets* 17:1–46.
   *VPIN predicts volatility/toxicity, NOT direction; Bondarenko disputes even the volatility
   claim. Read both together.*

4. **"Informed Trading and the Price Impact of Block Trades: A High-Frequency Trading
   Analysis."** *International Review of Financial Analysis*, 2016.
   ScienceDirect: https://www.sciencedirect.com/science/article/abs/pii/S1057521916301156
   *Large trades: strong contemporaneous impact, informed traders prefer size, but no serial
   predictability before/after the block. Closest to our PRINTS/BURSTS "smart money" question.*

5. **"Returns and Order Flow Imbalances: Intraday Dynamics and Macroeconomic News Effects."**
   (Northwestern/Kellogg dissertation chapter.)
   HTML preprint (arXiv 2508.06788): https://arxiv.org/html/2508.06788
   *Endogeneity between returns and OFI, intraday variation of the relationship, news-day
   effects. Get the journal version for the full endogeneity treatment.*

## Free / already read (no Uni account needed)

- **Zhang, Zohren & Roberts — DeepLOB** (arXiv 1808.03668): https://arxiv.org/pdf/1808.03668 —
  CNN-LSTM on LSE book; predictability is a very-short-horizon phenomenon.
- **Verma — "Order-flow analysis on Bank Nifty"** (GitHub):
  https://github.com/lakshyaverma2414/Order-flow-analysis-on-Bank-Nifty — BankNifty options,
  27 days; **after spot adjustment** mean Spearman residual −0.009, t −0.76, SNR 0.014 →
  "reactive, not leading." Near-exact replication of our null.
- **"Order Book Filtration and Directional Signal Extraction at High Frequency"**
  (arXiv 2507.22712): https://arxiv.org/html/2507.22712v1 — **BankNifty index futures**, NSE,
  10s→1s; "filtration enhances associative clarity more reliably than causal directionality."
- **The Drift Burst Hypothesis** (arXiv 2601.08974): https://arxiv.org/pdf/2601.08974 —
  formalizes locally-explosive drift + vol burst; a description of our "burst" object, not a
  forward signal.
- **Markwick — "Order Flow Imbalance, a HFT signal":**
  https://dm13450.github.io/2022/02/02/Order-Flow-Imbalance.html — one dataset showing the whole
  story: contemporaneous OFI R²≈0.40; lagged (predictive) OOS R²≈0.03, hit-rate 53%, Sharpe 0.12.
- IC methodology: https://microalphas.com/information-coefficient/ ·
  https://ml4trading.io/primer/reading-the-information-coefficient-stability-icir-and-horizon-decay/

## Concrete numbers (contemporaneous vs predictive)

| Setting | Contemporaneous | Predictive (lagged / future return = IC) |
|---|---|---|
| Crypto OFI (Markwick) | R² ≈ 0.40 | OOS R² ≈ 0.03, hit 53%, Sharpe 0.12 |
| Large-tick Nasdaq (deep-book OFI) | RMSE −68–74% | peaks ~1–2 min, decays > 1 day |
| BankNifty options, spot-adjusted (Verma) | — | Spearman −0.009, t −0.76 (≈ 0) |
| BankNifty futures 10s→1s (2507.22712) | correlation/regime signal | "associative, not causal" |
| VPIN | volatility/toxicity gauge | no reliable DIRECTION; vol claim disputed |

## Implications for this repo

1. Our "map, not a forecast" labeling matches the consensus; the +0.38 / −0.46 CVD↔price
   sign-flip is the documented regime instability, not noise to average away at n=2.
2. If we ever chase predictive IC: the edge lives at **seconds–2 min** (our 15-min buckets are
   past the decay), needs **conditioning** (regime clustering, trade-type co-occurrence,
   structural filtration of fleeting orders), and **must spot-adjust first** — both India studies
   find flow looks predictive only through spot co-movement (cf. the `spot-feed-corruption` memory).
3. The tape's defensible value is the **contemporaneous positioning read** — OI build-up
   quadrants, POC shelves, absorption vs continuation — the column where the evidence is strong.
