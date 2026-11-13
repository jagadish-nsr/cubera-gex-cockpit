"""Fit + persist empirical TOD RVol baselines from ``data/sentinel.db``.

  python scripts/log_rvol_baseline.py
  python scripts/log_rvol_baseline.py --as-of 2026-11-13
  python scripts/log_rvol_baseline.py --dry-run

Post-close the ingest health daemon also runs this once per trading day after
15:45 IST. Manual runs are for ops / recovery when the daemon missed a day.

Writes ``data/journal/rvol_baseline.json`` only when every index passes the
prevalence band. Rejected fits leave the prior file (or frozen in-code curves)
untouched. Does **not** change CONT / ABS / CVD classifier gates.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    from app.obs import boot, shutdown
    from sentinel.engine.rvol_fit import (
        DEFAULT_DB,
        DEFAULT_PROFILE_PATH,
        fit_from_db,
        profile_payload,
        refit_and_persist,
    )

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--out", type=Path, default=DEFAULT_PROFILE_PATH)
    p.add_argument("--as-of", type=str, default=None, help="ISO date (default: today IST)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fit + validate only; do not write the journal profile",
    )
    args = p.parse_args(argv)

    boot("log_rvol_baseline")
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    try:
        if args.dry_run:
            report = fit_from_db(args.db, as_of=as_of)
            payload = profile_payload(report) if report.ok else report.to_dict()
            print(json.dumps(payload, indent=2, sort_keys=True))
            shutdown(ok=report.ok, message=report.reason)
            return 0 if report.ok else 2
        report = refit_and_persist(args.db, args.out, as_of=as_of)
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "reason": report.reason,
                    "path": report.path,
                    "sessions": list(report.sessions),
                    "prevalence_rvol15": {
                        k: round(v, 4) for k, v in report.prevalence.items()
                    },
                    "curves": {k: dict(v._asdict()) for k, v in report.curves.items()},
                },
                indent=2,
                sort_keys=True,
            )
        )
        shutdown(ok=report.ok, message=report.reason)
        return 0 if report.ok else 2
    except Exception as exc:
        shutdown(ok=False, message=str(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
