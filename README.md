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
- ✅ **Live pipeline:** Dhan REST option chain (primary) → NSE (automatic fallback),
  clock-aligned background fetch; a Dhan Market-Feed **websocket** drives the live
  big-money futures tape and the expiry-day blast tick capture.
- ✅ **Daily dashboard** (`app/simple_dashboard.py` / `app/dashboard.py`) — the plain-language face: value
  profile (TPO) + live candle + GEX level ladder, the **big-money tape**, OI
  footprint / trailing-flow windows, positioning tabs, and on expiry the **gamma-blast** verdict (the
  original hero **plus an experimental "study model" shown beside it** — realized-delta
  + 3PM-window + an explicit exit discipline). One-click launchers for **Windows and
  macOS/Linux**; the dashboard self-captures its own history while open.
- ✅ **Multi-index:** Nifty 50, Bank Nifty, Sensex (Sensex borrows Nifty's vanna regime).
- ✅ CLI table + playbook (`cli.py`); intraday time-series + per-strike OI logging with
  Windows Task Scheduler setup (captures India VIX, USD/INR, a news-intensity gauge).
- ✅ **Sign-free positioning suite** (all immune to the dealer-sign landmine):
  magnet drift, per-strike ΔOI footprint, participant-wise OI (FII/Client/DII/Pro),
  intraday build-up streaks, the true-vs-fake **gamma-blast score**, a prior-day-level
  **stop-run sweep** study, and **Nifty futures OI build-up**. See **Research & findings**.
- ✅ **313 tests pass** (`python -m pytest -q`) — hand-checkable fixtures, NSE
  adapter/parse, expiry calendar, regime, parity, positioning, blast, and scoring.
- ⏳ Dealer-sign model and lot size / expiry rules remain **VERIFY-live** (below).

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
Local dashboard — one-click: `run_simple.bat` (Windows) or `run_simple.command` (macOS/Linux).
Or run it directly:
```
python3 -m streamlit run app/simple_dashboard.py --server.port 8610
```
Or the multi-tab shell:
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
- `app/` — the Streamlit dashboards (`simple_dashboard.py` is the daily face,
  `dashboard.py` the technical one), live data access + fetch lanes, intraday capture,
  and the sign-free modules: `oi_footprint`, `gammablast`, `blast_study` (the study
  model) / `blast_ticks`, `big_money*` (the tape + WS feed), `stophunt`, `timeseries`,
  `participant_oi`, `futures_buildup`, `magnet_trend`.
- `data/` — `nse.py` (NSE adapter), `dhan*.py` (Dhan REST + Market-Feed websocket),
  `expiry.py`, `parity.py`, `replay.py` (stored-day replay), `news.py`.
- `scripts/` — session/time-series logging, EOD captures, the headless collectors
  (`run_blast_ticks.py`, `run_big_money_collector.py`), and the point-in-time studies
  (`analyze_*`, `blast_study.py`, `preempt_study.py`).
- Launchers — `run_simple.bat` (Windows) / `run_simple.command` (macOS/Linux) for the
  dashboard; `setup_*scheduler*.bat` + `scripts/install_*.ps1` for Windows scheduled capture.
- `docs/` — scoping, `simple-dashboard.md` (the in-app help), user guide, and the
  research/findings notes below.


## Research & findings (sign-free, decision-support only)
Exploratory studies — read the **sign** and hit-rate, never the decimals; tiny,
autocorrelated samples. The recurring result: **OI/volume/turnover read *positioning*
and *expected move size*, not next-move *direction*.**
- `docs/findings-summary.md` — **start here.** The research arc's closer: directional
  edge from OI/GEX is ~zero at every horizon; the volatility-regime read is the real part.
- `docs/simple-dashboard.md` — the dashboard component guide (same text as the in-app 📖 Help).
- `docs/gamma-blast-research.md` / `gamma-blast-score.md` — the true-vs-fake blast score
  (Spearman +0.79; live-validated) and the ignition-timing study; the newer offline study
  behind the dashboard's "study model" (realized-delta + the 3PM-window separator).
- `docs/futures-buildup.md` — Nifty futures OI build-up: verified endpoints, the
  rollover/units landmines, next-day IC (underpowered, ~0).
- `docs/intraday-buildup-streaks.md` — turnover predicts move *size* (|move| IC +0.3)
  not *direction* (≈ 0); streaks mean-revert.
- `docs/participant-oi.md` — participant-wise OI + FII-drift IC.
- `docs/tick-data-ic-research.md`, `docs/big-money-tape.md` — the big-money tape + the
  literature review on tick-data predictive IC (contemporaneous strong, predictive ~0).

## Verify-live before trusting (never guess — see `BUILD_PLAN.md` §3)
1. NSE data source + pricing.   2. Current Nifty lot size.
3. Current NSE expiry rules.    4. Dealer-sign model for India (the landmine).

## Reused from the SPX project
`gex/`, `regime/`, `data/schema.py`, `data/parity.py`, and the synthetic fixture
tests. Bug fixes in these may need porting to/from `../GEX` (two copies can
diverge; a shared core package is a future option).
