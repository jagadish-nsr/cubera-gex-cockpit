# Cockpit latency (CRY-21)

Target: **sub-millisecond compute** for underlying switch and dashboard
snapshot reads via an in-memory per-asset cache. No SQLite / network I/O on
the hot path.

Manual Decision / `exec.live_place=0` / `exec.auto=0` are unchanged.

## What “sub-ms” means

| Metric | Meaning |
| --- | --- |
| `timing.compute_ms` | Server time inside `CockpitService.snapshot` / `switch_underlying` |
| HTTP RTT | Client ↔ uvicorn round-trip + JSON encode/decode — **not** in compute_ms |

A warm cache hit is a shallow dict copy + metadata. That compute is sub-ms.
Browser `fetch` to localhost still typically lands in the **1–10 ms** RTT
band depending on OS scheduling; that is transport, not engine work.

## Hot path

1. **Startup** — `prewarm_snapshots()` builds BTC / ETH / SOL (enabled) into
   `SnapshotCache` (cold build; may hit fixtures / feeds / SQLite).
2. **`GET /api/snapshot`** — cache hit for active underlying → return copy
   with `timing.cache_hit=true`.
3. **`PATCH /api/underlying`** — `set_underlying` in memory, return cached
   payload as `snapshot` + `spot` (`timing` included). Feed restart + cache
   refresh run as a **background** task.
4. **UI** — applies embedded `snapshot` from PATCH (no second round-trip);
   optional quiet follow-up refresh after live feeds catch up.

Diagnostics: `GET /api/cache/status`.

## Measured (2026-11-22, paper ops on localhost:8760, v0.5.8)

After startup prewarm of BTC/ETH/SOL:

| Path | compute_ms (p50) | HTTP RTT (p50) | Notes |
| --- | --- | --- | --- |
| Cold build (prewarm) | ~176–7200 ms | n/a | Live REST / SQLite; off hot path |
| Warm `GET /api/snapshot` | **~0.007 ms** | **~0.8 ms** | Cache hit; RTT = JSON + transport |
| Warm `PATCH /api/underlying` | **~0.03–0.05 ms** | **~1.8–2.6 ms** | Cache hit + embedded snapshot body |
| In-process warm snapshot (no HTTP) | **~0.002 ms** | n/a | Pure memory |

**Takeaway:** engine compute is sub-millisecond when warm. Localhost HTTP RTT
is still ~1–3 ms because of JSON serialization and the ASGI stack — that is
outside `timing.compute_ms`. Rapid-fire switches schedule a **debounced**
background feed realign so I/O does not stall the response path.

Re-measure anytime:

```bash
python3 -m pytest tests/cryptocubera/test_snapshot_cache_latency.py -q
curl -s localhost:8760/api/cache/status | python3 -m json.tool
curl -s localhost:8760/api/snapshot | python3 -c "import sys,json; print(json.load(sys.stdin).get('timing'))"
```

## Stale-serve guards

| Risk | Guard |
| --- | --- |
| Wrong-asset body in cache | `put`/`get` reject mismatched `underlying`; corrupt entries dropped |
| Cached live GO after chain ages | On hit, recompute `chain.age_s`/`stale`; if live source + stale → force **NO-GO** and schedule refresh (still sub-ms) |
| Forever-serving one prewarm | Hard TTL **30s** on entry age → cache miss + rebuild |
| Live-upgrade race after rapid switches | Debounced realign generation; refresh skips mismatched bodies |
| Refresh stampede | Per-underlying `_refreshing` set |

Fixture/mock soft-fallback sources do **not** use the live 2s hard veto (same as
`is_live_market_data_source`), but still refresh age metadata on every hit.

## Non-goals

- Sub-ms **wall-clock** including WAN/RTT
- Skipping background persist forever (recording still happens off-path)
- Changing Manual Decision semantics or enabling Auto / live place
