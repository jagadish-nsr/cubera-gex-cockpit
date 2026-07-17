"""Scenario B1/B2 — WALL: bounce (calm) vs break (wild).  [docs/scenario-catalog.md]

The regime-conditional core: SAME geometry (spot approaching a wall), OPPOSITE regime → opposite
outcome?
  B1  HIGH dealer-gamma concentration (calm/pinning) → REJECTION: spot bounces AWAY from the wall.
  B2  LOW concentration (wild/trending)             → BREAK: spot pushes THROUGH and continues.

Signed "push toward the wall" over a forward horizon (day-detrended, point-in-time):
  approaching CALL wall from below  → push = +fwd_ret   (up = toward/through the call wall)
  approaching PUT  wall from above  → push = −fwd_ret   (down = toward/through the put wall)
"Near a wall" = gap-to-wall ≤ the horizon's expected move (spot can plausibly reach it in that time),
using σ√τ from ATM IV. Concentration tercile from |vanna| (the validated sign-free vol-regime metric;
|charm| substituted at ≤1.5 DTE). The B1-vs-B2 test is the CONTRAST push(LOW-conc) − push(HIGH-conc):
positive ⇒ wild breaks/continues while calm rejects. A shared mean-reversion (you reach a wall by
trending into it) is differenced out by the regime split; a MID-ZONE placebo (near neither wall)
shows the non-wall baseline.

  python scripts/analyze_wall_break.py            # NIFTY   [store]

⚠️ Exploratory; overlapping windows autocorrelate; sign-free. Requires the levels-enriched cache."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIN_PER_YEAR = 252 * 375
HORIZONS_MIN = [15, 30, 60]
GRID = 5


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _prep(store):
    import numpy as np
    import pandas as pd
    from app.intraday_features import load_features
    f = load_features(store)
    if f is None or "call_wall" not in f.columns:
        _print("No levels-enriched feature cache — rebuild it first.")
        return None
    f = f.sort_values(["day", "time_ist"]).reset_index(drop=True)

    # forward returns (day-detrended) per horizon
    parts = []
    for _, g in f.groupby("day"):
        g = g.copy()
        sp = g["spot"].to_numpy()
        for h in HORIZONS_MIN:
            m = h // GRID
            fwd = np.full(len(g), np.nan)
            for i in range(len(g) - m):
                fwd[i] = np.clip(sp[i + m] / sp[i] - 1.0, -0.08, 0.08)   # kill bad-tick spikes
            g[f"fwd_{h}"] = (fwd - np.nanmean(fwd)) if np.isfinite(fwd).any() else fwd  # day-neutral
        parts.append(g)
    f = pd.concat(parts, ignore_index=True)

    # concentration metric: |vanna|, or |charm| near expiry (matches the live gauge switch)
    f["conc"] = np.where(f["dte"] <= 1.5, f["charm"].abs(), f["vanna"].abs())
    f["em1"] = f["spot"] * f["atm_iv"] / np.sqrt(MIN_PER_YEAR)   # 1-min expected move unit
    return f


def _push_cols(f, h):
    """Signed push-toward-wall for horizon h, and the near-call / near-put / mid-zone masks."""
    import numpy as np
    em_h = f["em1"] * np.sqrt(h)                                  # expected move over h minutes
    gap_call = f["call_wall"] - f["spot"]
    gap_put = f["spot"] - f["put_wall"]
    near_call = (gap_call > 0) & (gap_call <= em_h)
    near_put = (gap_put > 0) & (gap_put <= em_h)
    fwd = f[f"fwd_{h}"]
    push = np.where(near_call, fwd, np.where(near_put, -fwd, np.nan))
    near_wall = near_call | near_put
    # mid-zone placebo: comfortably inside the band, not near either wall
    mid = (~near_wall) & (gap_call > em_h) & (gap_put > em_h)
    return push, near_wall.to_numpy(), near_call.to_numpy(), near_put.to_numpy(), mid.to_numpy()


def main() -> None:
    import numpy as np
    import pandas as pd
    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    f = _prep(store)
    if f is None:
        return
    _print(f"B1/B2 · WALL bounce-vs-break · {store} · {f['day'].nunique()} days  "
           f"(push = signed return TOWARD the wall; >0 break/continue, <0 reject)\n")

    # tercile cuts on concentration over near-wall stamps (pooled, all horizons share the metric)
    q1, q2 = f["conc"].quantile([1 / 3, 2 / 3])

    _print("MEAN push near a wall (bp, P&L-relevant), by concentration tercile — B1(calm)-vs-B2(wild):")
    _print(f"  {'horizon':>8} | {'HIGH-conc':>10} {'MID':>8} {'LOW-conc':>10} | "
           f"{'LOW−HIGH':>9} {'uncond':>8} {'mid-zone*':>10}   {'n_wall':>7}")
    for h in HORIZONS_MIN:
        push, near_wall, _, _, mid = _push_cols(f, h)
        d = pd.DataFrame({"push": push, "conc": f["conc"].to_numpy(),
                          "mid_push": np.where(mid, f[f"fwd_{h}"], np.nan)})
        w = d[np.isfinite(d["push"])]
        hi = w[w["conc"] >= q2]["push"].mean()
        md = w[(w["conc"] > q1) & (w["conc"] < q2)]["push"].mean()
        lo = w[w["conc"] <= q1]["push"].mean()
        uncond = w["push"].mean()
        midz = d["mid_push"].mean()
        _print(f"  {h:>6}m  | {hi*1e4:>+9.1f} {md*1e4:>+7.1f} {lo*1e4:>+9.1f} | "
               f"{(lo-hi)*1e4:>+8.1f} {uncond*1e4:>+7.1f} {midz*1e4:>+9.1f}   {len(w):>7,}")
    _print("  (units: basis points of the underlying · *mid-zone = near NEITHER wall, the placebo)\n")

    # break-through: does spot CROSS the wall within the horizon, HIGH vs LOW concentration? -------
    _print("Wall CROSS-rate within horizon (of near-wall stamps), HIGH vs LOW concentration:")
    _print(f"  {'horizon':>8} | {'cross% HIGH':>11} {'cross% LOW':>11} {'LOW−HIGH':>9}")
    for h in HORIZONS_MIN:
        m = h // GRID
        _, near_wall, near_call, near_put, _ = _push_cols(f, h)
        sp = f["spot"].to_numpy(); cw = f["call_wall"].to_numpy(); pw = f["put_wall"].to_numpy()
        conc = f["conc"].to_numpy()
        fut = np.full(len(f), np.nan)                            # spot at t+m within the same day
        for _, idx in f.groupby("day").groups.items():
            ii = np.array(sorted(idx)); s = sp[ii]
            for j in range(len(ii) - m):
                fut[ii[j]] = s[j + m]
        crossed = np.where(near_call, fut > cw, np.where(near_put, fut < pw, np.nan)).astype(float)
        ok = np.isfinite(crossed)
        hi = crossed[ok & (conc >= q2)]; lo = crossed[ok & (conc <= q1)]
        ch, cl = 100.0 * hi.mean(), 100.0 * lo.mean()
        _print(f"  {h:>6}m  | {ch:>10.1f}% {cl:>10.1f}% {cl-ch:>+8.1f}")

    _print("\n  Read B: if HIGH-conc push < 0 (reject) and LOW-conc push > 0 (break) — i.e. LOW−HIGH "
           "clearly POSITIVE and above the mid-zone placebo — the wall bounce/break flips with regime "
           "(a genuine conditional edge). If both hug 0 / match the placebo, walls carry no directional "
           "edge (the base-rate expectation). ⚠️ exploratory; sign-free; overlapping windows.")


if __name__ == "__main__":
    main()
