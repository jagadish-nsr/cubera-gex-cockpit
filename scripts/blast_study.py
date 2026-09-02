"""Run the offline gamma-blast study across the captured expiry days and print the harness table.

  python scripts/blast_study.py                 # all expiry days the timeseries covers
  python scripts/blast_study.py --symbol NIFTY --cut 13:45

Two panels: (1) BROAD RUN/PIN classification from spot alone (every expiry day) with the 3PM-window
move that separates them; (2) premium/delta/exit features on the days that also have a captured ladder.
Sign-free, decision-support; NOT a validated classifier (small sample). NEVER auto-executes."""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.blast_study import classify_spot, study_day        # noqa: E402


def _expiry_days(symbol: str) -> list[str]:
    """Expiry Tuesdays (NIFTY weekly = Tuesday since Sep 2025) that the timeseries archive covers."""
    fs = sorted(glob.glob(str(ROOT / "data" / "journal" / "timeseries" / symbol / "*.csv")))
    out = []
    for f in fs:
        day = os.path.basename(f)[:-4]
        try:
            if dt.date.fromisoformat(day).weekday() == 1:     # Tuesday
                out.append(day)
        except ValueError:
            pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--cut", default="13:45")
    args = ap.parse_args()

    days = _expiry_days(args.symbol)
    if not days:
        print(f"No expiry days in the timeseries archive for {args.symbol}."); return

    print(f"== BROAD RUN/PIN (spot-only) · {args.symbol} · anchor {args.cut} ==")
    print(f"{'day':11} {'K':>7} {'verdict':>7} {'commit':>6} {'finalD':>6} "
          f"{'maxUp':>5} {'maxDn':>5} {'d@3pm':>6} {'move_after_3pm':>14}")
    runs = pins = 0
    for d in days:
        s = classify_spot(args.symbol, d, args.cut)
        if not s:
            print(f"{d:11}  -- no spot --"); continue
        runs += s["verdict"] == "RUN"; pins += s["verdict"] == "PIN"
        print(f"{d:11} {s['strike']:7.0f} {s['verdict']:>7} {str(s['commit_time']):>6} "
              f"{s['final_disp']:6.0f} {s['max_up']:5.0f} {s['max_dn']:5.0f} "
              f"{str(s['disp_at_3pm']):>6} {str(s['move_after_3pm']):>14}")
    print(f"  -> {runs} RUN / {pins} PIN.  Note: |move_after_3pm| is the cleanest separator in-sample "
          f"(runs large, pins ~flat) — validates the 3PM prior.\n")

    print(f"== PREMIUM / DELTA / EXIT (days with a captured ladder) ==")
    print(f"{'day':11} {'verd':>4} {'dir':>4} {'commit':>6} {'rdelta':>6} {'premX':>6} {'exit':>22} {'cap':>7}")
    for d in days:
        s = study_day(args.symbol, d, args.cut)
        if not s:
            continue
        e = s["exit"]
        print(f"{d:11} {s['verdict']:>4} {s['direction']:>4} {str(s['commit_time']):>6} "
              f"{str(s['rdelta_commit']):>6} {str(s['prem_exp']):>6} "
              f"{e['reason'] + '@' + str(e.get('time_ist', '')):>22} {str(e.get('itm_ltp', '')):>7}")
    print("\nrdelta = realized ITM-side delta (OLS slope of premium vs real spot) → 1 committed, ~.5 pinned.")
    print("exit = the discipline's exit (spot-trailing stop / hard pre-settlement time-stop); cap = ITM premium there.")


if __name__ == "__main__":
    main()
