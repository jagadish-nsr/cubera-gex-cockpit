#!/usr/bin/env python3
"""Nightly (or on-demand) backup of cockpit DBs + latest_snap pickles.

Writes timestamped copies under data/journal/backups/<stamp>/ by default.
Prunes stamp dirs older than --keep-days (default 14).

Usage:
  python scripts/backup_journals.py
  python scripts/backup_journals.py --keep-days 30
  python scripts/backup_journals.py --backup-root /Volumes/ext/cubera-backups
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.durability import DEFAULT_BACKUP_ROOT, DEFAULT_RETENTION_DAYS, backup_paths, prune_backups


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=DEFAULT_BACKUP_ROOT,
        help=f"Destination parent (default {DEFAULT_BACKUP_ROOT})",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help=f"Prune stamp dirs older than this many days (default {DEFAULT_RETENTION_DAYS})",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Skip retention prune",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repo root that holds data/",
    )
    args = parser.parse_args()

    report = backup_paths(root=args.root, backup_root=args.backup_root)
    print(f"backup stamp={report.stamp} dest={report.dest}")
    print(f"  copied={len(report.copied)} skipped={len(report.skipped)} errors={len(report.errors)}")
    for err in report.errors:
        print(f"  ERROR {err}", file=sys.stderr)

    if not args.no_prune:
        removed = prune_backups(args.backup_root, keep_days=args.keep_days)
        if removed:
            print(f"  pruned {len(removed)} old stamp(s): {', '.join(removed[:5])}"
                  + ("..." if len(removed) > 5 else ""))

    return 0 if report.ok or (report.copied and not report.errors) else 1


if __name__ == "__main__":
    raise SystemExit(main())
