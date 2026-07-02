"""Daily post-close capture of NSE participant-wise OI (Client/DII/FII/Pro index F&O).

  python scripts/log_participant_oi.py        # fetch newest available file, cache it

Best-effort: fetches the latest published clearing-corp file (today, walking back a
few days on a miss), parses the INDEX columns, and appends one CSV per trading day to
``data/journal/participant_oi/YYYY-MM-DD.csv`` (flat + market-wide; gitignored). The
file is EOD, published in the evening — wire this to a scheduled task ~18:30 IST.
Idempotent: re-running the same day is a no-op. Exits non-zero on total failure so a
scheduler surfaces the miss. Sign-free, decision-support only.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.participant_oi import capture_latest, derive_signals  # noqa: E402


def _print(s: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    got = capture_latest()
    if not got:
        _print("participant OI capture FAILED — no file reachable (bot-block, not "
               "published yet, or run from a non-Indian connection).")
        sys.exit(1)
    date_ist, parsed = got
    sig = derive_signals(parsed) or {}
    fii_fut = sig.get("fii_net_fut")
    fii_txt = f"{fii_fut:+,}" if fii_fut is not None else "—"
    _print(f"participant OI {date_ist}: {len(parsed)} participants captured  "
           f"FII net fut {fii_txt}  top option writer {sig.get('top_option_writer') or '—'}")
    if sig.get("dealer_sign_hint"):
        _print(f"  dealer-sign hint: {sig['dealer_sign_hint']}")


if __name__ == "__main__":
    main()
