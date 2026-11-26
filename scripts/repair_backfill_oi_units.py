"""One-off repair: convert Dhan 1-min backfill OI from shares to lots in the strike journal.

Until v5.3.172 ``app/oi_backfill.py`` wrote Dhan ``rollingoption`` OI as shares, while the live
capture in the same ``oi_strikes_dhan/<SYMBOL>/<date>.csv`` file writes lots. Every footprint
window that crossed a backfill/live boundary saw a fake 20–65x OI jump.

Backfill rows are recognised by what the backfill never writes: ``net_gex``, ``call_doi`` and
``call_vol`` are all blank. Their ``call_oi/put_oi/call_oi_near/put_oi_near`` are divided by the
index lot from ``config.INDICES``.

Guards (fail loud):
  * A file is refused unless at least 90% of its backfill OI values divide exactly by the lot
    (share counts do; already-converted lots almost never do), so a second run is a no-op refusal.
  * Dry run by default. ``--apply`` writes ``<file>.bak-oiunits-<stamp>`` first.

  python scripts/repair_backfill_oi_units.py --root ../cubera-gex-cockpit-prod
  # add --apply once the dry-run counts look right
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import time
from pathlib import Path

OI_COLS = ("call_oi", "put_oi", "call_oi_near", "put_oi_near")
MIN_DIVISIBLE = 0.90


class RepairRefused(RuntimeError):
    pass


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def is_backfill_row(row: dict) -> bool:
    return all(not str(row.get(c) or "").strip() for c in ("net_gex", "call_doi", "call_vol"))


def plan_file(path: Path, lot: int) -> tuple[list[str], list[dict], int]:
    """Return (header, converted rows, n backfill rows). Raises RepairRefused."""
    if lot <= 1:
        raise RepairRefused(f"{path}: lot {lot} — not converting")
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        rows = list(reader)
    back = [r for r in rows if is_backfill_row(r)]
    vals = [v for r in back for c in ("call_oi", "put_oi") if (v := _f(r.get(c))) and v > 0]
    if not vals:
        raise RepairRefused(f"{path}: no backfill OI rows")
    frac = sum(1 for v in vals if v % lot == 0) / len(vals)
    if frac < MIN_DIVISIBLE:
        raise RepairRefused(f"{path}: only {frac:.0%} of backfill OI divides by lot {lot} — "
                            "already lots, or a different lot")
    for r in back:
        for c in OI_COLS:
            v = _f(r.get(c))
            if v is not None:
                r[c] = repr(v / lot)
    return header, rows, len(back)


def write_file(path: Path, header: list[str], rows: list[dict], stamp: str) -> Path:
    bak = path.with_name(f"{path.name}.bak-oiunits-{stamp}")
    shutil.copy2(path, bak)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)
    return bak


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from config import get_index

    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = root / "data" / "journal" / "oi_strikes_dhan"
    refused = 0
    for sym_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        lot = int(get_index(sym_dir.name).lot_size)
        for path in sorted(sym_dir.glob("????-??-??.csv")):
            try:
                header, rows, n = plan_file(path, lot)
            except RepairRefused as exc:
                refused += 1
                print(f"REFUSED {exc}")
                continue
            msg = f"{sym_dir.name} {path.stem}: {n} backfill rows / lot {lot}"
            if args.apply:
                bak = write_file(path, header, rows, stamp)
                msg += f"  (backup {bak.name})"
            print(msg)
    print(f"{'applied' if args.apply else 'dry run'}; {refused} file(s) refused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
