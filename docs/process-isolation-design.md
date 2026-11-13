# Process Isolation Design — Ingest vs HTTP

Status: **design locked** (2026-11-13). Implementation is phased; this document
is the target architecture. Do not invent a second GEX sign model or a
simulator inside either process.

## Problem

One FastAPI process hosts:

- Dhan WebSocket spot ingest
- per-index chain ingest threads (`live_fetcher`)
- recommendation publisher
- blast ticker
- HTTP + browser WebSocket telemetry
- SQLite readers/writers (Sentinel, journal, admin)

An event-loop stall (SQLite lock, long unpickle, broker timeout on the wrong
thread) takes down the decision screen. The prod watchdog (`COCKPIT_WATCHDOG=1`
on port 8640) restarts the whole process — correct for survival, expensive for
tape continuity.

## Target split

```
┌─────────────────────────────┐     shared memory / pickle / SQLite
│  cubera-ingest (no public   │ ──────────────────────────────────►
│  HTTP)                      │
│  - ws_spot_feed             │
│  - live_fetcher ×3          │
│  - blast_ticker             │
│  - rec.publisher            │
│  - pre/post market pollers  │
│  - dhan.sync scheduler      │
└─────────────────────────────┘
              │
              │ latest_snap_*.pkl + LKGS disk + sentinel.db WAL
              ▼
┌─────────────────────────────┐
│  cubera-serve (port 8640)   │
│  - FastAPI routes           │
│  - /ws/telemetry (read)     │
│  - admin health / readiness │
│  - NEVER calls Dhan on      │
│    request threads          │
└─────────────────────────────┘
```

## Contracts (non-negotiable)

1. **HTTP never blocks on broker I/O.** `_load_active_dashboard` already reads
   `live_fetcher.latest` then LKGS. Keep that boundary absolute after the split.
2. **One writer for chain publish.** Only the ingest process writes
   `latest_snap_{SYMBOL}.pkl` and LKGS memory is process-local — serve restores
   from disk pickle on cold start. Same for Ideas: ingest writes
   `latest_rec_{SYMBOL}.pkl`; EXTERNAL serve reads it for GO/Ideas latch and
   must not start `rec.publisher`.
3. **Sentinel DB:** ingest owns tick→bar writes; serve is read-mostly. WAL mode
   + `busy_timeout` stay. Health daemon PASSIVE checkpoints when WAL > 25 MB.
4. **No synthetic data path** in either process. Replay remains a separate
   application on another port (UI lesson #19).
5. **Lots / expiry / holiday calendar** stay in `config.py` / `data/expiry.py`
   shared package — never duplicated.

## Phased delivery

| Phase | Ship | Done when |
|-------|------|-----------|
| A | WAL PASSIVE checkpoint + holiday fail-loud + deep `/api/health` | Done (v5.3.117) |
| B | Extract ingest entrypoint `scripts/run_ingest.py`; serve skips broker lanes when `COCKPIT_INGEST_EXTERNAL=1`; dual `run_prod.sh` | Done |
| C | Shared readiness: serve fails ready if ingest heartbeat stale > 90s (session); chain/spot/feed from pickle meta + SQLite + heartbeat detail (not local empty WS) | Done (probes.ingest + EXTERNAL shared probes) |
| D | Optional: move Sentinel tick writer fully into ingest; serve opens DB read-only | Mid-session SQLite stalls leave HTTP responsive |

## Explicit non-goals

- Multi-broker abstraction
- Kubernetes until a second live host forces it
- Splitting per-index into three ingest processes (one ingest process is enough)

## Operator notes

- Prod today: single process + watchdog. Treat Phase A mitigations as mandatory.
- After Phase B: both `run_prod.sh` and a new `run_ingest.sh` must be supervised;
  if ingest dies, serve must show DEGRADED via readiness, not invent a book.
