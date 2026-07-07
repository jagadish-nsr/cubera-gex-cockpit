# Nifty futures OI build-up

The sign-free **directional** positioning read. Nifty *futures* give a single OI series and
price *is* the index, so the classic price+OI matrix is unambiguous and free of the
dealer-*gamma*-sign landmine (a long build-up is a long build-up). Companion to the options
footprint, and the futures analogue of the participant-OI read.

## The matrix (`app/futures_buildup.py::classify_futures_buildup`)
| Price | OI | Build-up | Bias |
|---|---|---|---|
| ↑ | ↑ | **long build-up** (fresh longs) | bullish |
| ↓ | ↑ | **short build-up** (fresh shorts) | bearish |
| ↑ | ↓ | **short covering** (shorts exit) | bullish, weaker/late |
| ↓ | ↓ | **long unwinding** (longs exit) | bearish, weaker |

Volume = **conviction / expected move size, not direction** (the recurring lesson — see
`docs/intraday-buildup-streaks.md`).

## Data sources (verified live 2026-07-07/08)
| Track | Source | Granularity | History |
|---|---|---|---|
| **Daily** | F&O bhavcopy (UDiFF) | 1 row/day (OHLCV + OI + ΔOI + settle) | ✅ backfillable (years) |
| **Intraday** | live `liveEquity-derivatives` | your snapshot cadence | ❌ forward-only (capture from now) |

- **Daily / history — F&O bhavcopy:** `https://nsearchives.nseindia.com/content/fo/
  BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip`. Index futures = rows with
  `FinInstrmTp == 'IDF'`, `TckrSymb == 'NIFTY'`. Carries OHLC, `PrvsClsgPric`, `OpnIntrst`,
  **`ChngInOpnIntrst`**, `TtlTradgVol`, `SttlmPric`, `UndrlygPric`. One summary row per
  contract per day — **not** an intraday series. UDiFF format since ~Jul-2024; older history
  is the legacy `foDDMMMYYYYbhav.csv.zip` under `archives.nseindia.com`.
- **Intraday — live quote:** `https://www.nseindia.com/api/liveEquity-derivatives?index=
  nse50_fut` (Referer `.../get-quotes/derivatives?symbol=NIFTY`). Real-time `openInterest` +
  `volume` + `lastPrice` + `underlyingValue` **levels** per serial month. ⚠️
  `changeinOpenInterest`/`totalTradedVolume` come back **null** — difference OI/volume across
  your own snapshots. (`quote-derivative` is dead/404.)

**Two data landmines:**
1. **Rollover.** Single near-month ΔOI is garbage around monthly expiry (positions roll
   near→next month). Example 06-29: near-month ΔOI −3.06M vs **aggregate +740k** (the truth).
   Always use the **aggregate across the 3 serial months** (`agg_*`); near-month is shown only
   for contrast.
2. **Units.** Bhavcopy volume/OI are in **lots**; the live endpoint is in **units** (a
   lot-size multiple apart). Don't mix the two scales. (Lot size itself is a project VERIFY
   item.) Nifty futures are **monthly** (3 serial months), not weekly.

## What's built
- `data/nse.py`: `fetch_fo_bhavcopy` / `parse_index_futures` (daily) and `fetch_futures_live`
  / `parse_futures_live` (intraday) — both return near-month **and** aggregate OI/vol/price.
- `app/futures_buildup.py`: the matrix classifier + a per-symbol daily journal
  (`data/journal/futures/<SYM>/`) with `backfill` / `capture_latest` / `futures_series`, plus
  the intraday journal (`futures_intraday/<SYM>/`) with `capture_futures_snapshot` /
  `intraday_futures_buildup`.
- `scripts/log_futures_buildup.py` — `--backfill N` / `--symbol`; the intraday snapshot
  piggybacks `scripts/log_timeseries.py` (runs under `NiftyGEX-TimeSeries`).
- Dashboard tile `_futures_buildup_section` — daily EOD build-up + recent-days strip +
  today's intraday build-up.
- Scheduler: `setup_futures_buildup_scheduler.ps1` + `run_futures_buildup.bat` register
  `GEX-FuturesBuildup` (weekday ~20:00 IST, EOD daily capture). Intraday needs no separate
  task.

## Findings so far
Backfilled 30 NIFTY trading days (25-May → 07-Jul): a coherent macro story — late-May/early-
June **short build-up** selloff (24063→23174) → mid-June **short-covering** rally (price up on
shrinking OI = no fresh longs) → early-July more covering.

**Next-day-return IC** (`scripts/analyze_futures_ic.py`, point-in-time): **n=29 pairs, NO
edge** — momentum `sign(ΔPrice)` IC −0.09, fresh-money (+1 long-build-up / −1 short-build-up)
−0.11, raw aggregate ΔOI −0.04, all ≈ 0. Per-state cells are tiny/imbalanced (13 short-
covering, 10 short-build-up, 3 long-build-up), and the "bearish" states were followed by mild
*up* days (mean-reversion) — but at n=3–10 that's noise. **Underpowered, not a rejection.** The
study is wired and point-in-time correct; it gains power as the journal accrues. Consistent
with every other study here: build-up is a **positioning/structure read, not a next-day
predictor.** Sign-free, decision-support only.
