"""Deep backfill of the FREE daily NSE archives for the daily-horizon studies (#4, #5).

Fills two journals as far back as the archives go (F&O bhavcopy ≈ Jul-2024 onward, UDiFF):
  * options_eod/  — per-strike EOD option OI/ΔOI/vol + spot (UndrlygPric) from the bhavcopy
  * participant_oi/ — FII/DII/Client/Pro EOD long/short from the clearing-corp archive

Uses ONE primed session, re-primed periodically (Akamai cookies expire) and on any miss, so
~2 years of fetches don't hammer NSE with a fresh handshake each day. Idempotent — safe to
re-run to fill gaps.

  python scripts/backfill_daily.py --days 800          # ~2+ years back, NIFTY
  python scripts/backfill_daily.py --days 800 --only options
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.nse import (_primed_session, fetch_fo_bhavcopy,  # noqa: E402
                      fetch_participant_oi, parse_index_options)
from app.options_eod import backfill as options_backfill  # noqa: E402
from app.participant_oi import backfill as participant_backfill  # noqa: E402
from data.nse import parse_participant_oi  # noqa: E402


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


class _Session:
    """A primed NSE session that re-primes every ``refresh`` calls and on any failure —
    keeps a long backfill alive as Akamai cookies rotate."""

    def __init__(self, refresh: int = 40):
        self.s = _primed_session()
        self.n = 0
        self.refresh = refresh

    def fetch(self, fetch_fn, arg):
        self.n += 1
        if self.n % self.refresh == 0:
            self.s = _primed_session()
        r = fetch_fn(arg, session=self.s)
        if r is None:                      # maybe cookie/bot block — re-prime once, retry
            self.s = _primed_session()
            r = fetch_fn(arg, session=self.s)
        return r


def main() -> None:
    ap = argparse.ArgumentParser(description="Deep backfill of free daily NSE archives.")
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--days", type=int, default=800, help="calendar days back")
    ap.add_argument("--only", choices=["options", "participant"], default=None)
    args = ap.parse_args()

    sess = _Session()

    if args.only in (None, "options"):
        _print(f"Backfilling options_eod ({args.symbol}) up to {args.days} days…")
        got = options_backfill(args.symbol, n_days=args.days,
                               fetch_fn=lambda d: sess.fetch(fetch_fo_bhavcopy, d),
                               parse_fn=parse_index_options)
        _print(f"  options_eod: +{len(got)} days"
               + (f"  ({got[0]} … {got[-1]})" if got else " (all cached / none reachable)"))

    if args.only in (None, "participant"):
        _print(f"Backfilling participant_oi up to {args.days} days…")
        got = participant_backfill(n_days=args.days,
                                   fetch_fn=lambda d: sess.fetch(fetch_participant_oi, d),
                                   parse_fn=parse_participant_oi)
        _print(f"  participant_oi: +{len(got)} days"
               + (f"  ({got[0]} … {got[-1]})" if got else " (all cached / none reachable)"))


if __name__ == "__main__":
    main()
