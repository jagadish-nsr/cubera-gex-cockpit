# Session Evaluation — Tuesday, 17-Nov-2026

Nifty weekly expiry day. Cash session closed. Eval from prod journals
(`cubera-gex-cockpit-prod`), tag **v5.3.149**. Canvas:
`session-eval-2026-11-17.canvas.tsx`.

## Reliability scorecard

| Metric | Value | Notes |
|---|---|---|
| Process version | v5.3.149 | Health reports 5.3.149 |
| Degrade mode (session) | FEED_DOWN / CHAIN path stuck | Every cash minute not-ready |
| Ready minutes (09:20–15:20) | **0** | 360 unique minutes sampled |
| Not-ready minutes | **360** | Burn **100%** (SLO ≤ 1%) |
| Not-ready reasons | `no_fresh_chain` + `feed_detail_missing` | Same pair on every minute |
| Watchdog restarts | 0 mid-session | Serve+ingest held since 16-Nov 07:58 |
| Ingest heartbeat | age ~0.5–3s all day | File fresh; **detail `{}`** |
| NSE fallback events | **993** | Nifty 704, Bank Nifty 289 — Dhan HTTP 429 |
| Chain publishes (ingest) | **4,718** | Real books were written; serve readiness did not see them |

### What the DEGRADED banner actually meant

Two independent gaps stacked. Either one alone keeps the reliability banner loud.

**1. Zombie in-memory chain on EXTERNAL serve (primary).**

Prod serve runs `COCKPIT_INGEST_EXTERNAL=1`. Ingest owns Dhan and writes
`latest_snap_*.pkl` + `.meta.json`. Serve readiness should age the book from
that meta.

`app.readiness._chain_probe` prefers `live_fetcher` **memory** when any snap is
present. Serve still holds a memory snap whose `fetched` froze at
**09:01:42 IST**. Chain ages in `slo.readiness.minute` then grew ~60s per
minute for the entire cash session (1,144s at 09:20 → 22,705s at 15:20). The
probe never fell through to the meta ingest was refreshing. Health right now
still reports those chains as `source=memory` with multi-hour ages, while on-disk
meta is the 15:40 close write (~6.7h old).

So the banner said the chain was dark while ingest was publishing ~700 chains
per hour.

**2. Empty ingest heartbeat feed detail (always-on).**

EXTERNAL serve judges the WS feed from `ingest.heartbeat` → `detail`. Today's
file stayed `{"detail": {}}`. Readiness treats that as `feed_detail_missing`.
`_resolve_degrade_mode` ranks feed failure as `FEED_DOWN` before chain mode.
Heartbeat **age** was fine (ingest alive); only the feed fields were missing.
A second ingest PID (82466) is still writing that empty detail over the
pid-file owner (72805).

**3. Real Dhan pressure (secondary, real).**

993 `egress.nse_fallback` events — almost all `HTTP 429` on the 17-Nov / 23-Nov
expiries. Timeseries sources: 42 dhan / 38 nse-fallback. That is a genuine
degraded **book source**, and would correctly light `book_from_nse_scrape` /
`CHAIN_DEGRADED` if serve readiness were reading ingest's live books. It was
not; the false `no_fresh_chain` path drowned that signal.

### Gap to fix (engineering)

1. EXTERNAL `_chain_probe`: ignore serve memory (or ignore memory older than
   meta). Prefer snap meta / pickle mtime owned by ingest.
2. Heartbeat writer: refuse to publish `detail={}`; always merge WS status; one
   ingest owner (kill orphan beat writers).
3. Surface `degrade_mode` on `slo.readiness.minute` (today only `reasons` string).
4. Keep NSE-fallback counting — that part of the story was real.

## Scorecard (desk)

| Dimension | Rating | Notes |
|---|:---:|---|
| Macro | Expiry day | Nifty weekly Tuesday; shared-gamma closes common |
| Tape completeness | Weak after 10:44 | Sentinel 1m bars stop at 10:44 on all three indices |
| Trade Ideas | Mixed | 269 ideas; 39 still labelled **0DTE** on expiry (prod lacks label fix); 258 refuses (IV/walls/VWAP) |
| Sentinel OF | Thin | **1** signal all day (Sensex CONT 10:17); tape cut at 10:44 |
| GO Desk | Quiet | 1 EMIT (Sensex ICEBERG 09:09); 0 paper trades today |
| Stability | Failed | 100% readiness burn; DEGRADED banner all cash session |

## Close book

| Index | Spot | Prev | Walls | Flip | Source |
|---|---:|---:|---|---:|---|
| Nifty | 25,387.4 | 25,291.1 | 25,400 / 25,400 | 25,387.4 | session-close (unnamed; prod lacks modal-wall helper) |
| Bank Nifty | 62,273.25 | 61,575.9 | 62,000 / 62,000 | 62,133.2 | session-close |
| Sensex | 82,532.19 | 82,104.79 | 82,000 / 82,000 | 81,926.2 | session-close |

Nifty timeseries modal pairs were shared-gamma (25,400/25,400 and 25,450/25,450)
with a mixed dhan/nse day. Equal walls are one concentration; prod UI still
captions them as separate call/put walls.

## What burned / what was loud

- Reliability banner DEGRADED/NOT_READY for every cash minute — **false chain
  darkness** on serve + empty feed detail.
- Dhan 429 → NSE scrape ~half the Nifty book path.
- Ideas refused when walls/IV/VWAP missing (258) — consistent with scrape /
  cold book windows.
- Rec publisher log noise: `sigma_90` referenced before assignment (thousands of
  lines in web.jsonl).
- Sentinel tape ended at 10:44; only one continuation printed.
- Prod still on v5.3.149 without yesterday’s uncommitted desk fixes (shared-gamma
  KPI copy, 0DTE label gate, modal close walls, trail→paper close).

## Follow-ups

1. Fix EXTERNAL chain probe so zombie serve memory cannot mask ingest meta.
2. One heartbeat writer; require `ws_connected` / `is_live` in detail.
3. Restart dual stack after the probe fix so memory is cleared.
4. Investigate sentinel stop at 10:44 (feed/resync).
5. Promote desk fixes from dev (0DTE label, shared-gamma caption, close-wall
   source naming, RVol open limb, scout EV gate).
6. Cap or fix `sigma_90` unbound local in the rec publisher path.

## Disclaimer

Decision-support tooling, not financial advice. The user confirms all decisions.
