# gex-indian — Nifty GEX Engine

The **Nifty (NSE)** port of the SPX GEX engine. Pulls the live Nifty option chain,
computes dealer gamma-exposure levels (call/put walls, gamma flip, magnet, max
pain), classifies the regime, and emits a one-sentence playbook for Nifty index
options. Decision-support only — **not financial advice, no order execution.**

> **Just want to run it?** See **[docs/HANDOFF.md](docs/HANDOFF.md)** — a plain-language
> setup guide. The dashboard runs the same on both platforms (served at
> **http://localhost:8610**): **Windows** — double-click `run_simple.bat`; **macOS/Linux** —
> `./run_simple.command` (first run: `chmod +x run_simple.command`, or in Finder right-click →
> Open). Automatic background data collection is Windows-only for now (`setup_data_collection.ps1`);
> on macOS use cron/launchd, or just leave the dashboard open (it self-captures while running).

> Read `CLAUDE.md` first, especially the **dealer-sign landmine**: the US
> long-call / short-put-gamma convention may NOT hold for India's retail-selling
> microstructure and must be re-validated empirically before any wall/flip output
> is trusted. Treat the dealer sign as UNKNOWN, not as the US default.

## Status
- ✅ Engine inherited from `../GEX` (market-agnostic) + India data layer.
- ✅ Live NSE option-chain pull (`data/nse.py: fetch_chain` / `fetch_chain_v3`).
- ✅ CLI table + playbook (`cli.py`) and a local Streamlit dashboard (`app/`).
- ✅ Intraday time-series logging + Windows Task Scheduler setup; captures India
  VIX, USD/INR, and a news-intensity gauge alongside the GEX levels.
- ✅ **Sign-free positioning suite** (all immune to the dealer-sign landmine):
  magnet drift, per-strike ΔOI footprint, participant-wise OI (FII/Client/DII/Pro),
  intraday build-up streaks, a true-vs-fake **gamma-blast score**, and **Nifty
  futures OI build-up** (daily bhavcopy + live intraday). See **Research & findings**.
- ✅ **133 tests pass** (`python -m pytest -q`) — hand-checkable fixtures, NSE
  adapter/parse, expiry calendar, regime, parity, positioning, and scoring.
- ⏳ Dealer-sign model and lot size / expiry rules remain **VERIFY-live** (below).

## Install
```
pip install -r requirements.txt
```

## Run
```
python cli.py --demo            # offline SYNTHETIC chain (no network) — sanity check
python cli.py --live            # live NSE Nifty option chain (run locally)
python cli.py --live --window 0dte
```
Local dashboard — one-click: `run_simple.bat` (Windows) or `run_simple.command` (macOS/Linux).
Or run it directly (forward slashes work on every OS):
```
python -m streamlit run app/simple_dashboard.py --server.port 8610
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
- `data/nse.py` — **NSE adapter**: live Nifty option-chain fetch + parse.
- `data/expiry.py` — **NSE expiry calendar** (VERIFY current weekly/monthly rules).
- `data/news.py` — news-intensity gauge (flags exogenous-catalyst days).
- `config.py` — India config: `NIFTY_LOT_SIZE` (VERIFY), expiry window, dealer
  model (UNVERIFIED for India), flip grid, OI staleness.
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
