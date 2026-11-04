"""Daily capture of index futures OI build-up from the NSE F&O bhavcopy.

  python scripts/log_futures_buildup.py                  # fill last 7 days, NIFTY
  python scripts/log_futures_buildup.py --backfill 30
  python scripts/log_futures_buildup.py --symbol BANKNIFTY

Best-effort: walks the last N calendar days and caches every published bhavcopy
(idempotent). Newest-only capture leaves a hole when an earlier evening missed a
day that NSE published later — the overnight card then sticks on a stale window.
Default N=7 closes those gaps. Writes ``data/journal/futures/<SYMBOL>/`` (per-
index; gitignored). Wire to a scheduled task in the evening. Exits non-zero only
when nothing is reachable and the journal has no day for the symbol. Sign-free,
decision-support only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.futures_buildup import (  # noqa: E402
    backfill,
    buildup_bias,
    classify_futures_buildup,
    futures_series,
    load_futures_day,
)


def _print(s: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    from app.obs import boot, shutdown
    boot("log_futures_buildup")
    ap = argparse.ArgumentParser(description="Capture index-futures OI build-up (EOD).")
    ap.add_argument("--symbol", default="NIFTY", help="index symbol (default NIFTY)")
    ap.add_argument(
        "--backfill",
        type=int,
        metavar="N",
        default=7,
        help="fetch every available bhavcopy over the last N calendar days "
             "(idempotent; default 7 so a missed evening still fills gaps)",
    )
    args = ap.parse_args()
    symbol = (args.symbol or "NIFTY").upper()

    got = backfill(symbol=symbol, n_days=max(1, args.backfill))
    if got:
        _print(f"backfill {symbol}: captured {len(got)} day(s): {', '.join(got)}")

    series = futures_series(symbol, n=1)
    if not series:
        _print(
            f"{symbol} futures capture FAILED — no bhavcopy reachable (not "
            "published yet, bot-block, or run from a non-Indian connection)."
        )
        shutdown(ok=False, message="no bhavcopy reachable", symbol=symbol)
        sys.exit(1)

    date_ist = series[-1]["date"]
    rec = load_futures_day(date_ist, symbol) or {}
    agg = classify_futures_buildup(rec.get("dprice"), rec.get("agg_doi"))
    near = classify_futures_buildup(rec.get("dprice"), rec.get("near_doi"))
    dp, doi = rec.get("dprice"), rec.get("agg_doi")
    dp_txt = f"{dp:+.1f}" if dp is not None else "—"
    doi_txt = f"{doi:+,.0f}" if doi is not None else "—"
    if not got:
        _print(
            f"backfill {symbol}: nothing new in the last {args.backfill} days "
            "(already cached, or newer file not published yet)."
        )
    _print(
        f"{symbol} futures {date_ist}: close {rec.get('close')}  "
        f"ΔPrice {dp_txt}  aggΔOI {doi_txt}  "
        f"-> {agg or '—'} ({buildup_bias(agg) or '—'})"
    )
    if near != agg:
        _print(f"  near-month reads '{near}' (rollover skew — trust the aggregate).")
    shutdown(
        ok=True,
        message="captured" if got else "nothing new",
        symbol=symbol,
        day=date_ist,
        days=len(got),
    )


if __name__ == "__main__":
    main()
