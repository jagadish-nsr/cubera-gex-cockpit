# Cubera GEX Cockpit

Two decision-support surfaces share this repo:

1. **NSE / BSE index options** (Nifty, Bank Nifty, Sensex) — legacy Cubera cockpit below.
2. **CryptoCubera** — modular crypto GEX foundation (Deribit signal venue / Delta execution path). See **[crypto/README.md](crypto/README.md)**.

```bash
./run_crypto.sh          # CryptoCubera → http://localhost:8760 (Advisory / mock)
./run_crypto_paper.sh    # Manual Decision + ETH/SOL + Gemma + public MD (live_place=0)
# Optional paper sim: CRYPTO_PAPER_SIM=1 ./run_crypto_paper.sh
# Advisory live feeds only: CRYPTO_LIVE_FEEDS=1 CRYPTO_FLAG_FEEDS_DERIBIT=1 CRYPTO_FLAG_FEEDS_DELTA=1 ./run_crypto.sh
./run_prod.sh            # NSE Cubera  → http://localhost:8640
```

### CryptoCubera paper ops (Manual Decision + ETH/SOL + Gemma + live MD)

1. **Start** — `./run_crypto_paper.sh` → http://localhost:8760 (badge **Manual Decision**; BTC default; ETH/SOL on; Gemma on; public Deribit/Delta feeds on; soft-fallback to mock if WS fails)
2. **Switch asset** — topbar BTC / ETH / SOL (or `PATCH /api/underlying`)
3. **Copy / Export** — GO card → Copy levels / Export JSON (hand place on Delta); optional Log decision
4. **Session export** — `curl -s 'localhost:8760/api/session/export?format=json' -o paper_session.json`

Optional paper sim: `CRYPTO_PAPER_SIM=1 ./run_crypto_paper.sh`. Optional:
`ollama serve` + `gemma2:12b` for real Gemma answers; without Ollama the paper
path soft-falls back to keywords/templates. Full checklist:
**[crypto/docs/paper-runbook.md](crypto/docs/paper-runbook.md)**. Preference: no
auto trade placement — `exec.live_place` stays off (feeds ≠ exchange POST).

**Not financial advice.** CryptoCubera defaults to Advisory; preferred paper-ops
path is Manual Decision (copy/export). Paper sim OK. Auto stays off.

> **NSE handoff:** See **[docs/HANDOFF.md](docs/HANDOFF.md)**.
> NSE web cockpit: **http://localhost:8640** (`./run_prod.sh`, or `python3 web_server.py --port 8640`).
> Dev is **http://localhost:8650** (`./run_dev.sh`).
> Automatic background data collection is Windows-only for now (`setup_data_collection.ps1`);
> on macOS use cron/launchd, or leave the server open (it self-captures while running).

> Read `CLAUDE.md` first. The engine has one convention: call-gamma magnitude
> minus put-gamma magnitude. Walls are concentration strikes. That is not a
> claim about who delta-hedges, and it is not a pin forecast. Lot size and the
> expiry calendar are still VERIFY-live.

## Status
- ✅ **Live pipeline:** Dhan REST option chain (primary) → NSE (automatic fallback),
  clock-aligned background fetch; a Dhan Market-Feed **websocket** drives the live
  big-money futures tape and the expiry-day blast tick capture.
- ✅ **Web cockpit** (`web_server.py`, `app/static/`) — the decision screen: GEX ladder,
  book-balance line, big-money tape, OI footprint, and the expiry gamma-blast card.
  `./run_prod.sh` (port 8640) and `./run_dev.sh` (port 8650). The server self-captures
  while it is open.
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
| `auto`         | **Auto (Dhan / NSE)** (default) | NIFTY, BANKNIFTY, SENSEX | Dhan credentials optional (uses NSE/demo fallback) |
| `dhan`         | Dhan broker               | NIFTY, BANKNIFTY, SENSEX   | Client ID + access token (static) |
| `nse`          | NSE scraper (legacy)      | NIFTY, BANKNIFTY only      | None (run from home/Indian IP) |

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
python3 cli.py --live --symbol SENSEX     # SENSEX (via Dhan)
python3 cli.py --live --window 0dte
```
Web cockpit:
```
./run_prod.sh                              # http://localhost:8640
python3 web_server.py --port 8640
```
Every output logs its assumptions: lot size, sign convention `short_call_short_put`,
expiry window, OI timestamp, and the parity-implied forward + carry.

## Tests
```
python -m pytest -q
```

## Layout
- `gex/` — pure GEX math: BS gamma, core exposure, flip, walls, max pain (unchanged).
- `regime/` — regime classification + playbook (unchanged).
- `data/schema.py`, `data/parity.py` — common schema + parity r/q + spot (unchanged).
- `data/nse.py` — **NSE adapter**: live Nifty option-chain fetch + parse (legacy).
- `data/dhan.py` — **Dhan adapter**: option chain + OI aggregation (static token).
- `data/expiry.py` — **NSE expiry calendar** (VERIFY current weekly/monthly rules).
- `data/news.py` — news-intensity gauge (flags exogenous-catalyst days).
- `config.py` — India config: `INDICES` registry, `DATA_SOURCE` flag, adapter
  credentials, dealer sign model, flip grid, OI staleness.
- `app/` — shared services for the web cockpit: live data access, fetch lanes,
  intraday capture, and `oi_footprint`, `gammablast`, `blast_study` / `blast_ticks`,
  `big_money*` (the tape + WS feed), `stophunt`, `timeseries`, `participant_oi`,
  `futures_buildup`, `magnet_trend`. The screen itself is `web_server.py` plus
  `app/static/`.
- `data/` — `nse.py` (NSE adapter), `dhan*.py` (Dhan REST + Market-Feed websocket),
  `expiry.py`, `parity.py`, `session_flags.py` (expiry and blast labels), `news.py`.
- `scripts/` — session/time-series logging, EOD captures, the headless collectors
  (`run_blast_ticks.py`, `run_big_money_collector.py`), and the point-in-time studies
  (`analyze_*`, `blast_study.py`, `preempt_study.py`).
- Launchers — `run_prod.sh` (port 8640) and `run_dev.sh` (port 8650);
  `setup_*scheduler*.bat` + `scripts/install_*.ps1` for Windows scheduled capture.
- `docs/` — user guide, the lessons register, and the research notes below.


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

## Verify-live before trusting
Current lots, user-confirmed 2026-10-24, live in `config.INDICES` only:

| Index | Lot | Expiry |
|---|---|---|
| NIFTY | 65 | every Tuesday |
| BANKNIFTY | 30 | last Tuesday of the month |
| SENSEX | 20 | every Thursday |

If that date is an exchange holiday, the expiry is the previous trading day (`data/expiry.py`). Retired and wrong if used now: Nifty 75, Bank Nifty 35, Sensex 10. Also confirm open interest is in lots before `contract_size` is applied.

## Engine convention
`gex/` uses one book-balance convention (`short_call_short_put`). Do not add a
second sign model. Older notes in `docs/` that compare a long-call/short-put
reading are research history, not the production path.
