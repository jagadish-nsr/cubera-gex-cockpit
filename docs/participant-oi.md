# Participant-wise OI — big-player positioning (sign-free)

Built 2026-07-02. The DIRECT big-player positioning read: Client / DII / FII / Pro
long-vs-short in index F&O, from NSE's clearing-corp EOD file. Unlike GEX (which infers
positioning through the option chain and sits under the unvalidated dealer-gamma-sign
landmine), this says *who is actually positioned* — so it's the cleanest clue to who
writes index options, and it's **sign-free**. Decision-support only; the user confirms
all decisions (see CLAUDE.md).

## Data source
```
https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_DDMMYYYY.csv
```
- Daily **EOD** file (T+0, published in the evening IST after the 15:30 close), one per
  trading day. The `www.nseindia.com` host 404s; use the `nsearchives` archives host.
- Fetched via the primed NSE session (`data.nse._primed_session`). Fail-soft on any error.
- **Market-wide** (not split Nifty-vs-BankNifty). Index+stock combined file — we keep only
  the **index** columns.
- Format: line 0 title (stray quotes, skip), line 1 header (some cols have trailing
  spaces), then rows `Client, DII, FII, Pro, TOTAL`. ⚠️ the option column order is
  **Call Long, Put Long, Call Short, Put Short** (not L/S paired per type).

## Pipeline
| Layer | Location | What |
|---|---|---|
| Fetch + parse | `data/nse.py` | `fetch_participant_oi(date)` (primed GET, fail-soft None) · `parse_participant_oi(text)` → `{participant: {fut_long/short, opt_call/put_long/short}}` (index cols only) |
| Logic + journal | `app/participant_oi.py` | flat market-wide journal `data/journal/participant_oi/YYYY-MM-DD.csv` (1 row/participant, gitignored) · `save/load/available/latest` · `derive_signals` · `capture_latest` (newest, walk-back) · `backfill(n_days)` · `participant_series` · `series_drift` |
| Daily capture | `scripts/log_participant_oi.py` | `capture_latest`; `--backfill N` for history; exit-1 on miss (for the scheduler) |
| Scheduler | `run_participant_oi.bat` + `setup_participant_oi_scheduler.ps1` | `GEX-ParticipantOI` weekday task ~20:00 IST (post-publication), wake-to-run + S4U — run the `.ps1` once as admin |
| Dashboard | `app/dashboard.py::_participant_oi_section` + `_fii_drift_readout` | FII net-fut, option-writing, dealer-sign hint, multi-day FII drift |
| IC research | `scripts/analyze_participant_ic.py` | FII futures → next-day Nifty return (point-in-time Spearman) |
| Tests | `tests/test_participant_oi.py` | 13 hand-checks (parse/derive/journal/capture/backfill/series/drift) |

## Signals (`derive_signals`)
- `net_fut = fut_long − fut_short` — index-futures direction (net long = bullish, net
  short = bearish/hedged).
- `net_opt_writing = (call_short+put_short) − (call_long+put_long)` — >0 = net WRITER.
- **Dealer-sign hint** = dominant net option writer among Client/Pro/FII. If Client/Pro
  dominate → supports the **inverted** dealer-sign lean (model B); if FII dominates →
  closer to the US default (model A). A *hint*, not a resolution of the landmine.
- Bull/bear from options (`_net_calls_puts`): net short puts = bullish (put writing);
  net short calls = bearish (call writing); long puts = bearish/hedge; long calls = bullish.

## Multi-day FII drift (`_fii_drift_readout`)
"Drift beats level" (same idea as the magnet drift). `series_drift` is a slope-based
classifier whose tolerance scales with the series' **magnitude**, so a signed series that
crosses zero (FII net-futures) classifies correctly — `magnet_trend.classify_trend`'s
level-relative band degenerates near zero and can't be reused here. Direction words:
futures **rising** = covering shorts / adding longs = bullish shift; **falling** = adding
shorts = bearish shift. Option-writing drift is shown as neutral *flow* (its bull/bear
sense depends on the call-vs-put mix). Always discounts a futures-short that isn't backed
by directional options as a likely **cash hedge**.

## First empirical reads (2026-07-02, 8 days backfilled: 22–25 Jun, 29 Jun–2 Jul)
- **Dealer-sign:** Client is the dominant index-option writer → **supports the inverted
  sign (model B)** — first concrete corroboration of the `both-signs-dealer-sign-read`
  lean. Still one-day, sign-free hint; landmine not resolved.
- **Directional (02-Jul):** FII bearish/hedged (net short futures −256,900, short calls,
  long puts) vs retail bullish (net long futures, heavy put-writing) — a mildly cautious
  configuration.
- **FII futures drift (8d):** net futures −222,784 → −256,900, drift ≈ −41k = a steady
  **bearish/cautious** positioning drift; option hedges (long puts) simultaneously
  *shrinking*, so mixed underneath.
- **IC (`analyze_participant_ic.py`, point-in-time):** FII net-fut LEVEL → next-day Nifty
  return **Spearman −0.90 (n=5)**; DRIFT → **−0.80 (n=4)**. Negative sign ⇒ FII futures
  shorts look **contrarian** (fade), consistent with the hedge read. **Statistically
  meaningless** at this n (3/5 pairs span data gaps; close is a proxy); read sign/shape
  only. Re-run after ~20–30 banked days.

## Usage
```
python scripts/log_participant_oi.py               # capture newest day
python scripts/log_participant_oi.py --backfill 12  # fill last 12 calendar days
python scripts/analyze_participant_ic.py            # FII -> next-day return IC
```

## Caveats (standing)
EOD only (T+0); market-wide (not per-index); index cols of a combined index+stock file;
sign-free but the put=support/writer-heuristics are exploratory; FII futures shorts are
often cash hedges (the single most misread NSE number); a headline overrides positioning.
GEX/positioning is a probability map, not a predictor.
