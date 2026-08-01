#!/usr/bin/env python
"""Run the pre-registered PASSIVE-preempt study (tier A, ungated) and print the report.

    python scripts/preempt_study.py                # FII net-fut drift (primary)
    python scripts/preempt_study.py --signal writer_drift_z   # dominant-writer drift (secondary)

See docs/preempt-positioning-preregistration.md. Descriptive; sign-free; decision-support.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.preempt_study import run, run_tier_b, HORIZONS, K_DRIFT, Z_WIN, TRAIL   # noqa: E402


def _tier_b(signal):
    r = run_tier_b(signal)
    print(f"\nPASSIVE-preempt study · TIER B (passivity-gated) · signal = {r['signal']}")
    print(f"  tape/participant overlap days: {r['n_overlap']}  ·  tape |flow| median = {r['tape_median_abs_cr']} cr")
    print("  overlap-day tape flow (₹cr) & class:")
    for d, cls in r["classes"].items():
        print(f"    {d}  net {r['flow'][d]:>7} cr  → {cls}")
    print("\n  gated full-history IC (≈ tier A — the gate only drops a few recent aggressive days):")
    print(f"  {'h':>4} {'n':>5} {'partial IC':>11}   passive-tape days with an h-forward return")
    for h in HORIZONS:
        g = r["gated_full_history"][h]
        print(f"  {h:>4} {g['n']:>5} {str(g['partial_ic']):>11}   {r['passive_fwd_available'][h]}")
    print("\n  VERDICT: NOT ESTIMABLE — the passivity CONTRAST (passive vs aggressive build → forward")
    print("  return) needs the tape-covered subset to have forward returns at h=5/10; it currently has")
    print("  ~1/0. Deferred until the big-tape archive accrues, exactly as the pre-registration states.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signal", default="fii_drift_z",
                    choices=["fii_drift_z", "writer_drift_z", "fii_drift", "writer_drift"])
    ap.add_argument("--tier", default="a", choices=["a", "b"])
    args = ap.parse_args()
    if args.tier == "b":
        _tier_b(args.signal)
        return
    r = run(args.signal)
    m = r["meta"]
    print(f"\nPASSIVE-preempt study (pre-registered, tier A) · signal = {r['signal']}")
    print(f"  participant days {m['n_participant_days']} · close days {m['n_close_days']} · "
          f"span {m['date_span'][0]}..{m['date_span'][1]}")
    print(f"  dominant option-writer cohort = {m['cohort']}  (tally {m['writer_tally']})")
    print(f"  cfg: drift k={K_DRIFT}d · z-window {Z_WIN}d · trailing-return control {TRAIL}d\n")
    print(f"  {'h':>4} {'n':>5} {'raw IC':>8} {'partial IC':>11} {'boot 95% CI':>18} "
          f"{'placebo 95%':>16} {'CI≠0':>5} {'>plc':>5}")
    for h in HORIZONS:
        d = r["horizons"][h]
        ci = f"[{d['ci'][0]}, {d['ci'][1]}]"
        pc = f"[{d['placebo_ci'][0]}, {d['placebo_ci'][1]}]"
        print(f"  {h:>4} {d['n']:>5} {str(d['raw_ic']):>8} {str(d['partial_ic']):>11} {ci:>18} "
              f"{pc:>16} {'Y' if d['ci_excludes_0'] else '·':>5} {'Y' if d['beats_placebo'] else '·':>5}")
    print(f"\n  VERDICT (pre-registered rule): {r['verdict']}")
    print("  CONFIRM needs partial IC>0, CI excludes 0, and beats placebo at BOTH h=5 and h=10.")
    print("  ⚠️ Decision-support; sign-free; the partial-IC control removes trailing-return momentum.")


if __name__ == "__main__":
    main()
