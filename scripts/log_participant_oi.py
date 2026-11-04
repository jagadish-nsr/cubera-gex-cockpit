"""Daily post-close capture of NSE participant-wise OI (Client/DII/FII/Pro index F&O).

  python scripts/log_participant_oi.py              # fill the last 7 calendar days
  python scripts/log_participant_oi.py --backfill 14

Best-effort: walks the last N calendar days and caches every published clearing-corp
file (idempotent). Newest-only capture leaves a hole when an earlier evening missed
a day that NSE published later — the overnight FII card then sticks on a stale
``through`` date. Default N=7 closes those gaps. Writes
``data/journal/participant_oi/YYYY-MM-DD.csv`` (flat + market-wide; gitignored).
Wire to a scheduled task ~18:30 IST. Exits non-zero only when nothing is reachable
and the journal is empty. Sign-free, decision-support only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.participant_oi import backfill, derive_signals, latest_available  # noqa: E402


def _print(s: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    from app.obs import boot, shutdown
    boot("log_participant_oi")
    ap = argparse.ArgumentParser(description="Capture NSE participant-wise OI (EOD).")
    ap.add_argument(
        "--backfill",
        type=int,
        metavar="N",
        default=7,
        help="fetch every available file over the last N calendar days "
             "(idempotent; default 7 so a missed evening still fills gaps)",
    )
    args = ap.parse_args()

    got = backfill(n_days=max(1, args.backfill))
    if got:
        _print(f"backfill: captured {len(got)} day(s): {', '.join(got)}")

    latest = latest_available()
    if not latest:
        _print(
            "participant OI capture FAILED — no file reachable (bot-block, not "
            "published yet, or run from a non-Indian connection)."
        )
        shutdown(ok=False, message="no file reachable")
        sys.exit(1)

    date_ist, parsed = latest
    parsed = parsed or {}
    sig = derive_signals(parsed) or {}
    fii_fut = sig.get("fii_net_fut")
    fii_txt = f"{fii_fut:+,}" if fii_fut is not None else "—"
    if not got:
        _print(
            f"backfill: nothing new in the last {args.backfill} days "
            f"(already cached, or newer file not published yet)."
        )
    _print(
        f"participant OI {date_ist}: {len(parsed)} participants captured  "
        f"FII net fut {fii_txt}  top option writer {sig.get('top_option_writer') or '—'}"
    )
    if sig.get("dealer_sign_hint"):
        _print(f"  dealer-sign hint: {sig['dealer_sign_hint']}")
    shutdown(
        ok=True,
        message="captured" if got else "nothing new",
        day=date_ist,
        participants=len(parsed),
        days=len(got),
    )


if __name__ == "__main__":
    main()
