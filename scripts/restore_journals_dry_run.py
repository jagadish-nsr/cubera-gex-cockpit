#!/usr/bin/env python3
"""Restore a backup stamp into a *scratch* directory only.

Never writes into the live data/journal tree. Dest path must include the
substring ``scratch`` (see docs/backup-restore.md).

Usage:
  python scripts/restore_journals_dry_run.py \\
      --source data/journal/backups/20260327T120000Z \\
      --dest data/journal/backups/scratch_restore_test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.durability import restore_dry_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Backup stamp directory")
    parser.add_argument(
        "--dest",
        type=Path,
        required=True,
        help="Scratch destination (path name must contain 'scratch')",
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()

    try:
        report = restore_dry_run(args.source, args.dest, root=args.root)
    except (ValueError, FileNotFoundError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    print(f"restored {len(report.copied)} file(s) → {report.dest}")
    for name in report.copied:
        print(f"  + {name}")
    for err in report.errors:
        print(f"  ERROR {err}", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
