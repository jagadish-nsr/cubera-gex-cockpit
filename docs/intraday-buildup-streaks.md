# Intraday build-up streaks + the turnover findings

The dashboard's **🔥 Intraday build-up streaks** tile flags strikes where OI is building
in one direction across consecutive snapshots — the intraday-momentum companion to the
vs-prev-close footprint tag. This note documents the 2026-07 rework and the (mostly
negative) empirical findings on turnover as a trade signal.

## What the tile shows (`app/oi_footprint.py::intraday_buildup_streaks`)
Each interval between snapshots is tagged from the (ΔOI step, premium %-step) matrix:
**green** = long build-up (OI↑ & premium↑), **red** = writing/short (OI↑ & premium↓);
OI-down and flat intervals break the run. A strike is flagged when one colour runs ≥3.

Two changes shipped this cycle:

1. **Live-only (no stale runs).** The detector used to report each strike's *day-longest*
   run, so it surfaced dead morning runs all afternoon. `only_live=True` uses the
   **trailing run** — the streak still building at the latest snapshot — and drops anything
   that already ended. (Verified: on 07-06 it surfaces the live put-green builds into the
   close; on 07-07 it correctly shows *nothing* live at the last snapshot instead of six
   dead 09:28 morning streaks.)
2. **Turnover as conviction.** Each streak carries `turnover` = run volume ÷ current OI;
   rows are ranked by it and it's shown as a column. It normalizes for wall size (a small
   strike hit hard outranks a big sleepy one).

## Turnover findings (n≈13k fine-day strike-intervals) — the important part
Turnover is a **conviction / expected-move gauge, not a direction signal.** Three
independent tests all land there:

- **Direction:** IC(turnover, forward premium return) ≈ **0** (−0.06 at 15m, −0.09 at 30m).
  Buying a high-turnover option as a naked long *loses* (top quintile −2.5%/−3.4%, ~40% win)
  — half of high turnover is *writing* (premium down), not buying.
- **Magnitude:** IC(turnover, |forward return|) ≈ **+0.3** (monotonic: low→high turnover ≈
  **4×** the move, 3.6% → 13.2% at 15m). Turnover robustly predicts a *bigger* move — in
  *either* direction.
- **Thrust + turnover (momentum):** premium up 3–5% in one bar *with* big turnover
  **mean-reverts** — worse than baseline (−3.3% at 15m), and the bigger the thrust the
  harder it fades (≥5% + hi-turnover: −3.8%/15m, −5.6%/30m).

**Implication:** a turnover spike says *"a large move is brewing here,"* not *"buy this
strike."* The right expression is a **long-gamma structure** (straddle/strangle), never a
naked directional bet.

## Do the streaks predict premium direction? No.
`scripts/analyze_streak_ic.py` (native cadence, live streak onsets, forward 15/30/60m):
**n=211, IC −0.12 / −0.14 / −0.05** — mean-reverting, no naked-long edge. Green (long
build-up) longs lose worse over time (−12% at 1h, 26% win). Splitting by turnover doesn't
help (HI-turnover IC ≈ 0, LO slightly more mean-reverting). An earlier "red 30m contrarian
pocket" turned out to be a 15-min-gridding artifact and washed out on native cadence.

## Bottom line
Intraday build-up streaks are a **structure / where-the-money-is-now** read, and turnover
grades **conviction / expected move size** — neither is a directional entry signal. Near-
expiry option premia mean-revert intraday, and no premium/turnover/thrust combination flips
that into a positive naked-long edge. Sign-free, decision-support only.
