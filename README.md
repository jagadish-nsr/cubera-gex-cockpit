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
- ✅ Live NSE option-chain pull (`data/nse.py: fetch_chain` / `fetch_chain_v3`).
- ✅ CLI table + playbook (`cli.py`) and a local Streamlit dashboard (`app/`).
- ✅ Intraday time-series logging + Windows Task Scheduler setup; captures India
  VIX, USD/INR, and a news-intensity gauge alongside the GEX levels.
- ✅ **55 tests pass** (`python -m pytest -q`) — hand-checkable fixtures, NSE
  adapter/parse, expiry calendar, regime, parity, and time-series scoring.
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
Local dashboard (one-click on Windows: double-click `run_app.bat`):
```
python -m streamlit run app\dashboard.py
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
- `app/` — Streamlit dashboard, data access, and intraday time-series capture.
- `scripts/` — session logging, time-series logging/analysis, live smoke test.
- `*.bat` / `setup_*scheduler*` — Windows launchers + scheduled capture setup.
- `docs/` — scoping, user guide, and Phase-B analysis notes.

## Verify-live before trusting (never guess — see `BUILD_PLAN.md` §3)
1. NSE data source + pricing.   2. Current Nifty lot size.
3. Current NSE expiry rules.    4. Dealer-sign model for India (the landmine).

## Reused from the SPX project
`gex/`, `regime/`, `data/schema.py`, `data/parity.py`, and the synthetic fixture
tests. Bug fixes in these may need porting to/from `../GEX` (two copies can
diverge; a shared core package is a future option).
