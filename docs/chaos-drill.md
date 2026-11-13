# Chaos drill runbook (Pillar G)

Prove degrade paths **without writing live SQLite**. The chaos host is a separate
process on port **8660**. It does not import `web_server` and never opens
`data/sentinel.db` / live journal DBs for write.

**Last drill:** 2026-11-13 — `scripts/chaos_drill.py` → **DRILL PASS** (all five
faults failed the session checklist).

Full market-replay (minute parquet playhead) remains specified in
[websocket-simulator-replay-and-stress-engine-plan.md](websocket-simulator-replay-and-stress-engine-plan.md).
This host is the smallest end-to-end fault injector that satisfies the
high-reliability success criterion: empty-chain + feed-silence → checklist FAIL.

## Start

```bash
chmod +x scripts/run_chaos.sh
./scripts/run_chaos.sh
# → http://127.0.0.1:8660/docs
```

## Inject + assert

```bash
python3 scripts/chaos_drill.py
```

Cases (each must FAIL checklist while `force_market_open=true`):

| Fault | Expected failed checks |
|-------|------------------------|
| `empty_chain` | `chain`, `readiness` |
| `feed_silence` | `feed`, `readiness` |
| `cb_open` | `circuits` |
| `stale_spot` | `spot`, `readiness` |
| `missing_vwap` | `ideas_vwap` |

Manual:

```bash
curl -s -X POST http://127.0.0.1:8660/api/chaos/inject \
  -H 'content-type: application/json' \
  -d '{"empty_chain":true,"feed_silence":true,"force_market_open":true}' | jq .
curl -s http://127.0.0.1:8660/api/admin/session-checklist | jq .pass,.failed
curl -s -X POST http://127.0.0.1:8660/api/chaos/reset
```

## Pre-open soak (G3)

Before first use of a release on a weekly expiry day, soak dual prod ≥ 30 min:

```bash
# Dual stack already up via run_prod.sh / promote
python3 scripts/pre_open_soak.py --base http://127.0.0.1:8640 --minutes 30
```

Dry-run: `--minutes 2 --interval 10`.

Pass: no health drop, no version flip, no watchdog.restart, readiness burn ≤ 10% of polls while `market_open`.

## Isolation rules

- Chaos process: `COCKPIT_ROLE=chaos`, port 8660 only.
- Live 8640/8650 untouched.
- No Dhan socket, no paper ticket writes, no fabricated premiums.
