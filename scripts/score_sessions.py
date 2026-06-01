"""Score the session log for the dealer-sign behavioural test.

With log_session.py run twice a day (open + ~3PM), each date has two snapshots.
We pair them and check whether the ~3PM price PINNED inside the OPEN's wall range.

Dealer-sign logic: if our `long_call_short_put` sign is correct, POSITIVE-gamma
(calm) days should pin and NEGATIVE days should not. If POSITIVE-labelled days
routinely escape the range (and/or NEGATIVE days pin), the sign may be INVERTED.

  python scripts/score_sessions.py

Pure functions (pair_by_day / score / summarise) are unit-tested offline.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "journal" / "sessions.csv"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pair_by_day(rows: list[dict]) -> list[tuple]:
    """Group rows by date_ist; return (date, open_row, afternoon_row) sorted by date.
    open = earliest snapshot of the day, afternoon = latest (same row if only one)."""
    by = defaultdict(list)
    for r in rows:
        by[r.get("date_ist", "")].append(r)
    out = []
    for d, rs in by.items():
        rs = sorted(rs, key=lambda r: r.get("timestamp_utc", ""))
        out.append((d, rs[0], rs[-1]))
    return sorted(out)


def score(rows: list[dict]) -> list[dict]:
    """Per-day record: did the afternoon price stay inside the open's wall range?"""
    recs = []
    for d, op, aft in pair_by_day(rows):
        pw, cw = _f(op.get("put_wall")), _f(op.get("call_wall"))
        osp, asp = _f(op.get("spot")), _f(aft.get("spot"))
        if None in (pw, cw, osp, asp):
            continue
        paired = op.get("timestamp_utc") != aft.get("timestamp_utc")
        recs.append({
            "date": d,
            "regime": op.get("regime", ""),
            "mode": op.get("mode_label", ""),
            "open_spot": osp,
            "pm_spot": asp,
            "move": round(asp - osp, 1),
            "width": round(cw - pw, 1),
            "in_range": pw <= asp <= cw,   # pinned inside the open's walls
            "paired": paired,              # False = only one snapshot that day
        })
    return recs


def summarise(recs: list[dict]) -> dict:
    paired = [r for r in recs if r["paired"]]
    pos = [r for r in paired if r["regime"] == "POSITIVE"]
    neg = [r for r in paired if r["regime"] == "NEGATIVE"]

    def pin_rate(g):
        return (sum(1 for r in g if r["in_range"]) / len(g)) if g else None

    return {
        "days": len(recs),
        "paired": len(paired),
        "pos_days": len(pos),
        "pos_pin_rate": pin_rate(pos),
        "neg_days": len(neg),
        "neg_pin_rate": pin_rate(neg),
    }


def verdict(s: dict) -> str:
    pr = s["pos_pin_rate"]
    if s["pos_days"] < 5:
        return (f"Not enough paired POSITIVE-gamma days yet ({s['pos_days']}; aim for ~5–10). "
                "Keep logging.")
    if pr >= 0.6:
        msg = f"POSITIVE-gamma days pinned {pr:.0%} of the time — CONSISTENT with the long_call_short_put sign."
        if s["neg_pin_rate"] is not None and s["neg_pin_rate"] < pr:
            msg += f" (Negative days pinned less: {s['neg_pin_rate']:.0%} — further support.)"
        return msg
    if pr < 0.4:
        return (f"POSITIVE-gamma days pinned only {pr:.0%} — the sign may be INVERTED or the "
                "signal weak. Investigate before trusting (try flipping DEALER_SIGN_MODEL).")
    return f"Mixed: positive-gamma pin rate {pr:.0%}. Keep logging for a clearer read."


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if not LOG.exists():
        print(f"No log yet at {LOG}. Run scripts/log_session.py first.")
        return
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    recs = score(rows)
    print(f"{'date':<12}{'regime':<10}{'open':>9}{'3pm':>9}{'move':>7}  in-range")
    for r in recs:
        flag = "" if r["paired"] else "  (1 snapshot)"
        print(f"{r['date']:<12}{r['regime']:<10}{r['open_spot']:>9,.0f}{r['pm_spot']:>9,.0f}"
              f"{r['move']:>+7,.0f}  {'YES' if r['in_range'] else 'no '}{flag}")
    s = summarise(recs)
    print("-" * 60)
    print(f"paired days: {s['paired']}/{s['days']} | "
          f"POSITIVE {s['pos_days']} (pin {s['pos_pin_rate'] if s['pos_pin_rate'] is None else format(s['pos_pin_rate'],'.0%')}) | "
          f"NEGATIVE {s['neg_days']} (pin {s['neg_pin_rate'] if s['neg_pin_rate'] is None else format(s['neg_pin_rate'],'.0%')})")
    print("VERDICT:", verdict(s))
    print("\n(Dealer-sign stays UNVALIDATED until this reads clearly. Paper only.)")


if __name__ == "__main__":
    main()
