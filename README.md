# gex-indian — Nifty GEX Engine

The **Nifty (NSE)** port of the SPX GEX engine. Pulls the live Nifty option chain,
computes dealer gamma-exposure levels (call/put walls, gamma flip, magnet, max
pain), classifies the regime, and emits a one-sentence playbook for Nifty index
options. Decision-support only — **not financial advice, no order execution.**

> Read `CLAUDE.md` first, especially the **dealer-sign landmine**: the US
> long-call / short-put-gamma convention may NOT hold for India's retail-selling
> microstructure and must be re-validated empirically before any wall/flip output
> is trusted. Treat the dealer sign as UNKNOWN, not as the US default.

## Status
- ✅ Engine inherited from `../GEX` (market-agnostic) + India data layer.
- ✅ Live option-chain pull via **three configurable adapters**: ICICI Direct Breeze (default), Dhan, NSE scraper.
- ✅ CLI table + playbook (`cli.py`) and a local Streamlit dashboard (`app/`).
- ✅ Intraday time-series logging + Windows Task Scheduler setup; captures India
  VIX, USD/INR, and a news-intensity gauge alongside the GEX levels.
- ✅ **Sign-free positioning suite** (all immune to the dealer-sign landmine):
  magnet drift, per-strike δOI footprint, participant-wise OI (FII/Client/DII/Pro),
  intraday build-up streaks, a true-vs-fake **gamma-blast score**, and **Nifty
  futures OI build-up** (daily bhavcopy + live intraday). See **Research & findings**.
- ✅ **180 tests pass** (`python3 -m pytest -q`) — hand-checkable fixtures, NSE
  adapter/parse, ICICI Breeze adapter, Dhan adapter, expiry calendar, regime, parity,
  positioning, and scoring.
- ⛳ Dealer-sign model and lot size / expiry rules remain **VERIFY-live** (below).

## Data Sources

The adapter is configured via `DATA_SOURCE` in your `.env` file:

| `DATA_SOURCE`   | Provider                  | Indices supported          | Credentials needed |
|----------------|---------------------------|----------------------------|--------------------|
| `icici_breeze` | **ICICI Direct** (default)| NIFTY, BANKNIFTY, SENSEX   | API key + secret + **daily** session token |
| `dhan`         | Dhan broker               | NIFTY, BANKNIFTY           | Client ID + access token (static) |
| `nse`          | NSE scraper (legacy)      | NIFTY, BANKNIFTY only      | None (run from home/Indian IP) |

### ICICI Direct Breeze (default)
Copy `.env.example` → `.env` and fill in:
```
DATA_SOURCE=icici_breeze
ICICI_BREEZE_API_KEY=...
ICICI_BREEZE_API_SECRET=...
ICICI_BREEZE_SESSION_TOKEN=...   # refresh daily
```
Generate your session token at:
`https://api.icicidirect.com/apiuser/login?api_key=<url-encoded-api-key>`

### Dhan
```
DATA_SOURCE=dhan
DHAN_CLIENT_ID=...
DHAN_ACCESS_TOKEN=...  # static, no daily refresh
```
Generate credentials at: https://dhanhq.co/docs/latest/authentication/

### NSE Scraper (legacy)
```
DATA_SOURCE=nse
```
No credentials needed. Runs from a home/Indian IP connection (NSE blocks datacenter IPs).
Supports NIFTY and BANKNIFTY only — not SENSEX.

## Run
```
python3 cli.py --demo            # offline SYNTHETIC chain (no network) — sanity check
python3 cli.py --live            # live chain via DATA_SOURCE adapter in .env
python3 cli.py --live --symbol SENSEX     # SENSEX (requires DATA_SOURCE=icici_breeze)
python3 cli.py --live --window 0dte
```
Local dashboard:
```
python3 -m streamlit run app/dashboard.py
```
Every output logs its assumptions: lot size (VERIFY), dealer-sign model
(UNVERIFIED for India), expiry window, OI timestamp, and the parity-implied
forward + carry.

## Tests
```
python -m pytest -q
```

## Layout
- `gex/` — pure GEX math: BS gamma, core exposure, flip, walls, max pain (unchanged).
- `regime/` — regime classification + playbook (unchanged).
- `data/schema.py`, `data/parity.py` — common schema + parity r/q + spot (unchanged).
- `data/nse.py` — **NSE adapter**: live Nifty option-chain fetch + parse (legacy).
- `data/icici_breeze.py` — **ICICI Direct Breeze adapter**: full GEX + OI + VIX (default).
- `data/dhan.py` — **Dhan adapter**: option chain + OI aggregation (static token).
- `data/expiry.py` — **NSE expiry calendar** (VERIFY current weekly/monthly rules).
- `data/news.py` — news-intensity gauge (flags exogenous-catalyst days).
- `config.py` — India config: `INDICES` registry, `DATA_SOURCE` flag, adapter
  credentials, dealer sign model, flip grid, OI staleness.
- `app/` — Streamlit dashboard, data access, intraday time-series capture, and the
  sign-free positioning modules (`oi_footprint`, `gammablast`, `participant_oi`,
  `futures_buildup`, `magnet_trend`).
- `scripts/` — session/time-series logging, EOD captures (participant OI, futures
  bhavcopy), and the point-in-time IC studies (`analyze_*`).
- `*.bat` / `setup_*scheduler*` — Windows launchers + scheduled capture setup
  (time-series, participant OI, futures build-up, gamma-blast).
- `docs/` — scoping, user guide, and the research/findings notes below.

## Research & findings (sign-free, decision-support only)
Exploratory studies — read the **sign** and hit-rate, never the decimals; tiny,
autocorrelated samples. The recurring result: **OI/volume/turnover read *positioning*
and *expected move size*, not next-move *direction*.**
- `docs/futures-buildup.md` — Nifty futures OI build-up: verified NSE endpoints
  (bhavcopy + live), the rollover/units landmines, next-day IC (underpowered, ~0).
- `docs/gamma-blast-score.md` — true-vs-fake blast score (Spearman +0.79; live-
  validated 07-07); turnover-ignition and two-sided-parity confirms both **rejected**.
- `docs/intraday-buildup-streaks.md` — live-only + turnover rework; turnover predicts
  move *size* (|move| IC +0.3) not *direction* (IC ≈ 0); streaks mean-revert.
- `docs/participant-oi.md` — participant-wise OI + FII-drift IC.

## Verify-live before trusting (never guess — see `BUILD_PLAN.md` §3)
1. NSE data source + pricing.   2. Current Nifty lot size.
3. Current NSE expiry rules.    4. Dealer-sign model for India (the landmine).

## Reused from the SPX project
`gex/`, `regime/`, `data/schema.py`, `data/parity.py`, and the synthetic fixture
tests. Bug fixes in these may need porting to/from `../GEX` (two copies can
diverge; a shared core package is a future option).
