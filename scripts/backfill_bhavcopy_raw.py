"""Deep backfill of the FULL raw NIFTY F&O bhavcopy (data/journal/bhavcopy_fo/).

Stores every NIFTY futures+option row (all strikes/expiries, OHLC/OI/ΔOI/vol/premium/spot)
per day — the complete offline substrate for PCR / IV-skew / historical-GEX / OTM-OI studies.
Uses the robust re-priming session from backfill_daily so ~2 years of fetches survive Akamai
cookie rotation. Idempotent — safe to re-run to fill gaps.

  python scripts/backfill_bhavcopy_raw.py --days 800          # ~2+ years, NIFTY
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.nse import fetch_fo_bhavcopy  # noqa: E402
from app.bhavcopy_raw import backfill_raw  # noqa: E402
from scripts.backfill_daily import _Session  # noqa: E402


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Deep backfill of the raw NIFTY F&O bhavcopy.")
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--days", type=int, default=800, help="calendar days back")
    args = ap.parse_args()

    sess = _Session()
    _print(f"Backfilling raw F&O bhavcopy ({args.symbol}) up to {args.days} days…")
    got = backfill_raw(args.symbol, n_days=args.days,
                       fetch_fn=lambda d: sess.fetch(fetch_fo_bhavcopy, d))
    _print(f"  bhavcopy_fo: +{len(got)} days"
           + (f"  ({got[0]} … {got[-1]})" if got else " (all cached / none reachable)"))


if __name__ == "__main__":
    main()
