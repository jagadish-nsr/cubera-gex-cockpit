"""Daily capture of NIFTY (index) futures OI build-up from the NSE F&O bhavcopy.

  python scripts/log_futures_buildup.py                  # newest day, NIFTY
  python scripts/log_futures_buildup.py --backfill 30     # fill last 30 days
  python scripts/log_futures_buildup.py --symbol BANKNIFTY

Best-effort: fetches the F&O bhavcopy (EOD, per-contract), parses the index-future
rows, and writes one CSV per trading day to ``data/journal/futures/<SYMBOL>/`` (per-
index; gitignored). The bhavcopy is published after close — wire this to a scheduled
task in the evening. Idempotent: re-running a captured day is a no-op. Exits non-zero
on total failure so a scheduler surfaces the miss. Sign-free, decision-support only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.futures_buildup import (backfill, buildup_bias,  # noqa: E402
                                 capture_latest, classify_futures_buildup)


def _print(s: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    from app.obs import boot, shutdown
    boot("log_futures_buildup")
    ap = argparse.ArgumentParser(description="Capture NIFTY index-futures OI build-up (EOD).")
    ap.add_argument("--symbol", default="NIFTY", help="index symbol (default NIFTY)")
    ap.add_argument("--backfill", type=int, metavar="N", default=0,
                    help="fetch every available bhavcopy over the last N calendar days "
                         "(idempotent) instead of just the newest")
    args = ap.parse_args()

    if args.backfill:
        got = backfill(symbol=args.symbol, n_days=args.backfill)
        if not got:
            _print(f"backfill: nothing new in the last {args.backfill} days "
                    "(already cached, or none reachable).")
            shutdown(ok=True, message="nothing new", symbol=args.symbol)
            return
        _print(f"backfill {args.symbol}: captured {len(got)} day(s): {', '.join(got)}")
        shutdown(ok=True, message="backfill", symbol=args.symbol, days=len(got))
        return

    got = capture_latest(symbol=args.symbol)
    if not got:
        _print(f"{args.symbol} futures capture FAILED — no bhavcopy reachable (not "
               "published yet, bot-block, or run from a non-Indian connection).")
        shutdown(ok=False, message="no bhavcopy reachable", symbol=args.symbol)
        sys.exit(1)
    date_ist, rec = got
    agg = classify_futures_buildup(rec.get("dprice"), rec.get("agg_doi"))
    near = classify_futures_buildup(rec.get("dprice"), rec.get("near_doi"))
    dp, doi = rec.get("dprice"), rec.get("agg_doi")
    _print(f"{args.symbol} futures {date_ist}: close {rec.get('close')}  "
           f"ΔPrice {dp:+.1f}  aggΔOI {doi:+,.0f}  "
           f"-> {agg or '—'} ({buildup_bias(agg) or '—'})")
    if near != agg:
        _print(f"  near-month reads '{near}' (rollover skew — trust the aggregate).")
    shutdown(ok=True, message="captured", symbol=args.symbol, day=date_ist)


if __name__ == "__main__":
    main()
