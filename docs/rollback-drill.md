# Quarterly rollback drill (Pillar H3)

Prove that a bad promote can be undone under a clock. Run once per quarter
(or after any change to `scripts/promote.sh` / `scripts/rollback.sh`).

**No auto-execution.** This drill stages a restore; a human confirms when to
touch live 8640.

## Goal

Timed restore of the previous release tag via `./scripts/rollback.sh`, then
re-promote (or leave on the prior tag if that is the intended desk state).

Pass criteria:

| Metric | Target |
|--------|--------|
| Time from decision to `/api/health` 200 on prior tag | ≤ 5 minutes |
| Health schema | `version` matches target tag; `readiness.ready` / `degrade_mode` / `probes.ingest` present |
| Desk awareness | Session eval notes start/end IST and measured seconds |

## Dry-run (no prod change)

```bash
chmod +x scripts/rollback_drill.sh
./scripts/rollback_drill.sh
```

Prints the previous tag, checklist steps, and records a drill log line under
`logs/rollback_drills.log` with `mode=dry-run`.

## Live drill (market closed preferred)

1. Confirm dual ingest+serve is healthy: `curl -s http://127.0.0.1:8640/api/health | jq .version,.readiness`
2. Note current tag: `git describe --tags --abbrev=0`
3. Start the stopwatch, then:

```bash
./scripts/rollback.sh          # previous tag
# or: ./scripts/rollback.sh v5.3.xxx
```

4. Poll until health returns; assert schema (same keys as promote Stage 5).
5. Stop the stopwatch. Record seconds in the session eval.
6. Re-promote the intended tip when ready: `./scripts/promote.sh`

```bash
./scripts/rollback_drill.sh --execute   # wraps rollback.sh + schema poll + timing
```

`--execute` requires an interactive `YES` confirmation and refuses while the
NSE cash session is open (09:15–15:30 IST) unless `--force` is passed.

## Known limits

- `rollback.sh` currently restarts a single `web_server.py` on 8640. Dual
  ingest (`COCKPIT_INGEST_EXTERNAL`) must be re-established via `run_prod.sh`
  or a subsequent promote if the desk needs the serve/ingest split after
  rollback.
- Do not use this drill as a substitute for the pre-open soak
  ([chaos-drill.md](chaos-drill.md)).

## Log

Each dry-run or execute appends:

```
YYYY-MM-DD HH:MM:SS | ROLLBACK_DRILL | mode=... | target=... | elapsed_s=... | ok=...
```

to `logs/rollback_drills.log`.
