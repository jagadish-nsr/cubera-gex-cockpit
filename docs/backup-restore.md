# Backup & restore runbook (Phase D)

Decision-support data durability. **No auto-execution of restores into live paths.**

## What is backed up

| Path | Why |
|------|-----|
| `data/sentinel.db` | 1m tape + Sentinel state |
| `data/journal/recommendations.db` | Ideas / GO / TE history |
| `data/journal/paper_trades.db` | Paper blotter |
| `data/journal/admin_ops.db` | Ops alerts / tasks |
| `data/journal/market_snapshot.db` | Session KPI snaps |
| `data/journal/go_efficiency.db` | GO efficiency journal |
| `data/journal/commentary.db` | Commentary (if present) |
| `data/journal/latest_snap_*.pkl` | Last published GEX book (cold-start LKGS) |

Missing files are skipped, not failed.

## Nightly backup

```bash
cd /path/to/cubera-gex-cockpit   # use the *running* tree (often releases/current)
./.venv/bin/python scripts/backup_journals.py
```

Default destination: `data/journal/backups/<YYYYMMDDTHHMMSSZ>/` with a `MANIFEST.txt`.
Retention: 14 days (`--keep-days N` to change; `--no-prune` to skip).

Optional cron (post-close, e.g. 16:30 IST):

```cron
30 16 * * 1-5  cd /path/to/releases/current && .venv/bin/python scripts/backup_journals.py >> logs/backup.log 2>&1
```

External disk:

```bash
./.venv/bin/python scripts/backup_journals.py --backup-root /Volumes/BackupDisk/cubera
```

SQLite copies use the online `backup()` API so WAL writers do not corrupt the copy.

## Restore dry-run (scratch only)

```bash
./.venv/bin/python scripts/restore_journals_dry_run.py \
  --source data/journal/backups/20260327T120000Z \
  --dest data/journal/backups/scratch_restore_test
```

Rules enforced in code (`app.durability.assert_scratch_dest`):

1. Dest path must contain the substring `scratch`.
2. Dest must not be live `data/` or `data/journal/`.
3. Dest under `data/journal/` is allowed only under `data/journal/backups/`.

To inspect a restored Sentinel DB without touching live:

```bash
sqlite3 data/journal/backups/scratch_restore_test/sentinel.db "PRAGMA integrity_check;"
```

**Promoting a restore into live** is a manual operator step (stop serve+ingest, copy chosen files, restart). This script will not do it.

## Atomic latest_snap publish (C4)

Writers use `app.latest_snap.write_latest_snap` — unique temp file in the same directory, then `os.replace`. A crash mid-write leaves no half-readable pickle at the final path.

## Cold-start LKGS (C5)

On HTTP process startup, `warm_lkgs_from_pickles()` seeds empty LKGS slots from `latest_snap_*.pkl` without inventing spot and without restamping `session_snapshots`. First cockpit paint after restart can show the last good book as degraded until live publish arrives.

## Post-close WAL TRUNCATE (C3)

After 15:45 IST on a trading day, the admin health loop runs one successful `PRAGMA wal_checkpoint(TRUNCATE)` on `sentinel.db` (retries while busy). During session, only PASSIVE checkpoint when WAL > 25 MB.
