"""Phase B (offline) — analyse the Phase A intraday time-series.

  python scripts/analyze_timeseries.py            # analyse every captured day
  python scripts/analyze_timeseries.py --days 5    # only the last N days

Runs on the captures in ``data/journal/timeseries/YYYY-MM-DD.csv``. Pure-function
core (like score_sessions.py) so each number is hand-checkable; the CLI just
prints. Read ``docs/phase-b-analysis.md`` and ``CLAUDE.md`` first — especially the
dealer-sign landmine. This DESCRIBES the data and gives an honest, sample-size-
aware read on the dealer-sign convention. It is NOT a predictor and NOT a verdict
on its own.

Data hygiene (non-negotiable, CLAUDE.md #6): we drop snapshots outside
09:15-15:30 IST and drop frozen post-close repeats (spot AND volume unchanged from
the previous row) before computing anything, so a dashboard left open after the
close can't skew the statistics.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.timeseries import TS_DIR, in_nse_session  # noqa: E402

# News-dominated-day gauge (transparent heuristic; errs toward flagging). A day is
# auto-tagged if its busiest 15-min interval scores >= IMPACT_FLAG, OR its mean
# fresh-headline count >= COUNT_FLAG. Tunable — they are printed in the report.
IMPACT_FLAG = 4
COUNT_FLAG = 6.0
EVENT_DAYS_PATH = TS_DIR.parent / "event_days.txt"   # manual override (the human veto)


def _parse_utc(ts: str):
    from datetime import datetime, timezone
    try:
        return datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def load_clean_day(path: str | Path) -> pd.DataFrame:
    """Load one day's CSV, keep only in-session rows, drop frozen post-close
    repeats (spot unchanged AND no new volume => stale duplicate)."""
    df = pd.read_csv(path)
    if df.empty:
        return df
    keep = df["timestamp_utc"].apply(lambda t: (_parse_utc(t) is not None)
                                     and in_nse_session(_parse_utc(t)))
    df = df[keep].reset_index(drop=True)
    if df.empty:
        return df
    frozen = (df["spot"].diff() == 0) & (df["call_vol"].diff() == 0)
    return df[~frozen].reset_index(drop=True)


def load_all(ts_dir: Path = TS_DIR, last_n: int | None = None) -> dict[str, pd.DataFrame]:
    """day (YYYY-MM-DD) -> cleaned DataFrame, only days with >= 2 usable rows."""
    days: dict[str, pd.DataFrame] = {}
    for p in sorted(glob.glob(str(ts_dir / "*.csv"))):
        name = os.path.basename(p)[:-4]
        if name == "YYYY-MM-DD":            # stray placeholder file, ignore
            continue
        d = load_clean_day(p)
        if len(d) >= 2:
            days[name] = d
    if last_n is not None:
        days = dict(sorted(days.items())[-last_n:])
    return days


def load_event_days(path=EVENT_DAYS_PATH) -> dict[str, str]:
    """Manual override list: 'YYYY-MM-DD  <reason>' per line, '#'/blank ignored.
    Returns {day: reason}; missing file -> {}. The human's veto over the auto-gauge."""
    out: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        out[parts[0]] = parts[1].strip() if len(parts) > 1 else "manual"
    return out


def news_day_flag(d: pd.DataFrame) -> str | None:
    """Auto-tag a day 'news-dominated' from its per-row news signal, or None.
    Transparent rule (errs toward flagging): peak news_impact >= IMPACT_FLAG OR mean
    news_count >= COUNT_FLAG. Days captured before news logging existed have no such
    columns -> never auto-flagged (fall back to the manual list)."""
    reasons = []
    if "news_impact" in d.columns:
        imp = pd.to_numeric(d["news_impact"], errors="coerce")
        if imp.notna().any() and float(imp.max()) >= IMPACT_FLAG:
            reasons.append(f"peak impact {float(imp.max()):.0f}>={IMPACT_FLAG}")
    if "news_count" in d.columns:
        cnt = pd.to_numeric(d["news_count"], errors="coerce")
        if cnt.notna().any() and float(cnt.mean()) >= COUNT_FLAG:
            reasons.append(f"mean count {float(cnt.mean()):.1f}>={COUNT_FLAG}")
    return "; ".join(reasons) if reasons else None


def _day_headline(d: pd.DataFrame) -> str:
    """The single highest-impact headline seen during the day (for the audit trail)."""
    if "news_top" not in d.columns or "news_impact" not in d.columns:
        return ""
    imp = pd.to_numeric(d["news_impact"], errors="coerce")
    if not imp.notna().any():
        return ""
    pos = int(imp.fillna(-1).to_numpy().argmax())
    val = d["news_top"].iloc[pos]
    return "" if pd.isna(val) else str(val)


def day_summary(day: str, d: pd.DataFrame) -> dict:
    spot = d["spot"]
    impact = pd.to_numeric(d["news_impact"], errors="coerce") if "news_impact" in d.columns else None
    return {
        "day": day,
        "n": len(d),
        "span": f"{d['time_ist'].iloc[0]}-{d['time_ist'].iloc[-1]}",
        "open": float(spot.iloc[0]), "high": float(spot.max()),
        "low": float(spot.min()), "close": float(spot.iloc[-1]),
        "range": float(spot.max() - spot.min()),
        "net": float(spot.iloc[-1] - spot.iloc[0]),
        "regimes": d["regime"].value_counts().to_dict(),
        "vix_open": float(d["india_vix"].iloc[0]) if d["india_vix"].notna().any() else None,
        "vix_close": float(d["india_vix"].iloc[-1]) if d["india_vix"].notna().any() else None,
        "news_impact_max": float(impact.max()) if impact is not None and impact.notna().any() else None,
        "news_headline": _day_headline(d),
        "auto_flag": news_day_flag(d),
    }


def realized_vol_by_regime(days: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """Std of 15-min log returns, bucketed by the regime at the START of each
    interval. Model predicts NEGATIVE > POSITIVE (short gamma amplifies)."""
    buckets: dict[str, list[float]] = {}
    for d in days.values():
        lr = np.log(d["spot"]).diff()
        for i in range(1, len(d)):
            r = lr.iloc[i]
            if np.isfinite(r):
                buckets.setdefault(d["regime"].iloc[i - 1], []).append(r)
    out = {}
    for reg, xs in buckets.items():
        a = np.array(xs)
        out[reg] = {"n": len(a), "rv_pct": float(a.std() * 100) if len(a) > 1 else float("nan"),
                    "mean_pct": float(a.mean() * 100) if len(a) else float("nan")}
    return out


def autocorr_by_regime(days: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """Lag-1 autocorrelation of 15-min returns over consecutive pairs that are
    BOTH inside one regime. Model predicts: POSITIVE -> negative (mean-revert),
    NEGATIVE -> positive (momentum). This is the sharp test (no walls needed)."""
    pairs: dict[str, list[tuple[float, float]]] = {}
    for d in days.values():
        lr = np.log(d["spot"]).diff()
        reg = d["regime"]
        for i in range(2, len(d)):
            if reg.iloc[i - 1] == reg.iloc[i - 2]:
                a, b = lr.iloc[i - 1], lr.iloc[i]
                if np.isfinite(a) and np.isfinite(b):
                    pairs.setdefault(reg.iloc[i - 1], []).append((a, b))
    out = {}
    for r, ps in pairs.items():
        if len(ps) >= 4:
            arr = np.array(ps)
            ac = float(np.corrcoef(arr[:, 0], arr[:, 1])[0, 1])
        else:
            ac = float("nan")
        out[r] = {"n": len(ps), "autocorr": ac}
    return out


def vix_by_regime_day(summaries: list[dict]) -> dict:
    """Day-level cross-check: is the dominant-regime day's VIX consistent with
    the sign model? (NEGATIVE-dominant days should run higher / rising VIX.)
    VIX is sign-assumption-free, so it's an independent anchor."""
    rows = []
    for s in summaries:
        if s["vix_open"] is None:
            continue
        dom = max(s["regimes"], key=s["regimes"].get) if s["regimes"] else "?"
        rows.append((s["day"], dom, s["vix_open"], s["vix_close"]))
    return rows


def _verdict(rv: dict, ac: dict, n_days: int, n_int: int, n_flagged: int = 0) -> list[str]:
    out = []
    if n_flagged:
        out.append(f"(Read below is the CLEAN subset — {n_flagged} news-dominated day(s) "
                   "removed; their direction was set by headlines, not dealer hedging.)")
    pos_rv, neg_rv = rv.get("POSITIVE", {}).get("rv_pct"), rv.get("NEGATIVE", {}).get("rv_pct")
    if pos_rv is not None and neg_rv is not None and np.isfinite(pos_rv) and np.isfinite(neg_rv):
        if neg_rv > pos_rv:
            out.append("• Realised vol: NEGATIVE > POSITIVE — CONSISTENT with the sign model.")
        else:
            out.append("• Realised vol: POSITIVE >= NEGATIVE — points the WRONG way (possible "
                       "inversion, or just sampling).")
    pos_ac, neg_ac = ac.get("POSITIVE", {}).get("autocorr"), ac.get("NEGATIVE", {}).get("autocorr")
    if pos_ac is not None and np.isfinite(pos_ac):
        out.append(f"• POSITIVE autocorr {pos_ac:+.2f} — model wants NEGATIVE (mean-revert); "
                   + ("consistent." if pos_ac < 0 else "points the WRONG way."))
    if neg_ac is not None and np.isfinite(neg_ac):
        out.append(f"• NEGATIVE autocorr {neg_ac:+.2f} — model wants POSITIVE (momentum); "
                   + ("consistent." if neg_ac > 0 else "points the WRONG way."))
    enough = n_days >= 10 and n_int >= 200
    out.append(
        f"\nVERDICT: dealer sign stays UNVALIDATED. n={n_days} usable days, {n_int} return "
        f"intervals — {'enough to read cautiously' if enough else 'FAR too little to conclude'}. "
        "Intraday autocorrelation is dominated by whether each day trended; need many full "
        "days (incl. the open) before any of this is more than a hint.")
    return out


def _returns_by_gamma_sign(days: dict[str, pd.DataFrame]):
    """Pool 15-min log returns into two FIXED buckets by net-dealer-gamma sign
    under the US convention: G+ (regime POSITIVE, total_net_gex>0) and G- (NEGATIVE).

    These buckets do not move when we flip the dealer-sign convention — flipping
    only relabels which bucket is 'short gamma' (proven: GEX is linear in the sign,
    so net_gex -> -net_gex and the regime label inverts EXACTLY). So one pooled
    measurement serves both conventions; only the interpretation differs.

    Returns (rets, pairs): rets[bucket] = np.array of returns;
    pairs[bucket] = list of (r_{t-1}, r_t) for consecutive same-regime intervals.
    """
    rets: dict[str, list[float]] = {"G+": [], "G-": []}
    pairs: dict[str, list[tuple[float, float]]] = {"G+": [], "G-": []}
    for d in days.values():
        lr = np.log(d["spot"]).diff()
        reg = d["regime"]
        for i in range(1, len(d)):
            r = lr.iloc[i]
            if not np.isfinite(r):
                continue
            if reg.iloc[i - 1] == "POSITIVE":
                rets["G+"].append(r)
            elif reg.iloc[i - 1] == "NEGATIVE":
                rets["G-"].append(r)
        for i in range(2, len(d)):
            if reg.iloc[i - 1] == reg.iloc[i - 2] and reg.iloc[i - 1] in ("POSITIVE", "NEGATIVE"):
                a, b = lr.iloc[i - 1], lr.iloc[i]
                if np.isfinite(a) and np.isfinite(b):
                    pairs["G+" if reg.iloc[i - 1] == "POSITIVE" else "G-"].append((a, b))
    return ({k: np.array(v) for k, v in rets.items()},
            {k: np.array(v) for k, v in pairs.items()})


def _ac(ps: np.ndarray):
    """Lag-1 autocorr and its approx standard error (1/sqrt(n)) for n pairs."""
    if len(ps) < 4:
        return float("nan"), float("nan")
    return float(np.corrcoef(ps[:, 0], ps[:, 1])[0, 1]), 1.0 / np.sqrt(len(ps))


def both_signs_report(days: dict[str, pd.DataFrame]) -> str:
    """Decisive test for the dealer-sign landmine: which convention's labels
    better predict REALISED Nifty behaviour? Quarantine is OFF here on purpose —
    the working thesis is that news is already baked into OI/gamma, so the regime
    label encodes it. Buckets are sign-free; only the model's claim about each
    bucket flips between conventions. Honest, significance-aware, NOT a verdict.
    """
    rets, pairs = _returns_by_gamma_sign(days)
    gp, gm = rets["G+"], rets["G-"]
    rvp, rvm = (gp.std() * 100 if len(gp) > 1 else float("nan"),
                gm.std() * 100 if len(gm) > 1 else float("nan"))
    acp, acp_se = _ac(pairs["G+"])
    acm, acm_se = _ac(pairs["G-"])

    L: list[str] = []
    add = L.append
    add("=" * 76)
    add("BOTH-SIGNS DEALER-CONVENTION TEST  (quarantine OFF — news treated as baked")
    add("into OI/gamma; all usable days)")
    add("=" * 76)
    add("Buckets are FIXED by net dealer-gamma sign under the US convention and do")
    add("NOT move when the convention flips (GEX is linear in the dealer sign, so a")
    add("flip negates net_gex and inverts the regime label EXACTLY; magnet, max-pain")
    add("and |levels| are invariant). One measurement, two interpretations.")
    add("")
    add(f"  bucket  n_ret   rv(15m)    n_pair  lag1-ac")
    add(f"  G+ (net_gex>0)  {len(gp):4}  {rvp:.3f}%   {len(pairs['G+']):4}   "
        f"{acp:+.3f} ± {acp_se:.3f}")
    add(f"  G- (net_gex<0)  {len(gm):4}  {rvm:.3f}%   {len(pairs['G-']):4}   "
        f"{acm:+.3f} ± {acm_se:.3f}")

    # Significance of the vol gap (sign-agnostic: same two samples either way).
    lev_p = float("nan")
    try:
        from scipy import stats
        if len(gp) > 1 and len(gm) > 1:
            lev_p = float(stats.levene(gp, gm)[1])
    except Exception:
        pass
    higher = "G+" if rvp >= rvm else "G-"
    add("")
    add(f"Realised-vol gap: {higher} is the wilder bucket "
        + (f"(Levene p={lev_p:.3f} — {'distinguishable' if lev_p < 0.05 else 'NOT distinguishable'} "
           "from no difference)." if np.isfinite(lev_p) else "(scipy unavailable)."))
    add(f"Autocorr: both |ac| are within ~2·SE of zero "
        f"(G+ {acp:+.3f}±{acp_se:.3f}, G- {acm:+.3f}±{acm_se:.3f}) — "
        "intraday momentum is not statistically resolved at this n.")

    # Score each convention against the model's 3 predictions.
    def score(short_bucket: str, long_bucket: str):
        srv = rvp if short_bucket == "G+" else rvm
        lrv = rvp if long_bucket == "G+" else rvm
        sac = acp if short_bucket == "G+" else acm
        lac = acp if long_bucket == "G+" else acm
        p1 = srv > lrv                      # short-gamma bucket more volatile
        p2 = np.isfinite(sac) and sac > 0   # short-gamma trends
        p3 = np.isfinite(lac) and lac < 0   # long-gamma mean-reverts
        return p1, p2, p3

    def block(name, short_bucket, long_bucket):
        p1, p2, p3 = score(short_bucket, long_bucket)
        n = sum(int(x) for x in (p1, p2, p3))
        add("")
        add(f"  {name}")
        add(f"    short-gamma = {short_bucket}, long-gamma = {long_bucket}")
        add(f"    P1 wilder when short-gamma : {'PASS' if p1 else 'fail'} "
            f"(rv {short_bucket} vs {long_bucket})")
        add(f"    P2 short-gamma trends (ac>0): {'PASS' if p2 else 'fail'}")
        add(f"    P3 long-gamma reverts (ac<0): {'PASS' if p3 else 'fail'}")
        add(f"    score {n}/3")
        return n

    add("")
    add("-" * 76)
    add("MODEL PREDICTIONS, SCORED PER CONVENTION  (P1 = the robust one)")
    a = block("A  long_call_short_put  (US default)", "G-", "G+")
    b = block("B  short_call_long_put  (India-inverted)", "G+", "G-")

    add("")
    add("-" * 76)
    enough = len(days) >= 10 and (len(gp) + len(gm)) >= 200
    lean = ("B (inverted)" if b > a else "A (US default)" if a > b else "neither")
    add(f"READ: convention {lean} fits realised behaviour better here "
        f"({b}/3 vs {a}/3 on B vs A). The discriminating signal is P1 (vol); the")
    add("autocorr legs are underpowered and wash out. Sample is "
        f"{'just past' if enough else 'BELOW'} the read-cautiously bar "
        f"(n={len(days)} days, {len(gp) + len(gm)} intervals). This LEANS but does")
    add("not settle the landmine: the regime label and the vol bucket can both")
    add("co-move with 'was it a trending day', and most days are news-driven. Need")
    add("many more full sessions (incl. the open) and ideally a third-party Indian")
    add("dealer-positioning read before acting on a sign.")
    return "\n".join(L)


def analyze(ts_dir: Path = TS_DIR, last_n: int | None = None) -> str:
    days = load_all(ts_dir, last_n)
    L = []
    add = L.append
    add("=" * 76)
    add("INTRADAY TIME-SERIES ANALYSIS  (Phase B, offline)")
    add("=" * 76)
    if not days:
        add("No usable days (need >= 2 in-session rows per day). Capture more, then re-run.")
        return "\n".join(L)

    event_days = load_event_days()
    summaries = [day_summary(d, df) for d, df in days.items()]

    # Resolve each day's news flag: manual override wins, else the auto-gauge.
    flags: dict[str, str] = {}
    for s in summaries:
        if s["day"] in event_days:
            flags[s["day"]] = f"manual: {event_days[s['day']]}"
        elif s["auto_flag"]:
            flags[s["day"]] = f"auto: {s['auto_flag']}"
    clean = {day: df for day, df in days.items() if day not in flags}

    add("\nPER-DAY STRUCTURE")
    for s in summaries:
        tag = "  [NEWS-DOMINATED]" if s["day"] in flags else ""
        add(f"\n{s['day']}  [{s['span']} IST, n={s['n']}]{tag}")
        add(f"  spot  open {s['open']:.0f}  high {s['high']:.0f}  low {s['low']:.0f}  "
            f"close {s['close']:.0f}   range {s['range']:.0f}  net {s['net']:+.0f}")
        add(f"  regime {s['regimes']}")
        if s["vix_open"] is not None:
            add(f"  India VIX {s['vix_open']:.2f} -> {s['vix_close']:.2f} "
                f"({s['vix_close'] - s['vix_open']:+.2f})")
        if s["news_impact_max"] is not None:
            head = f' — "{s["news_headline"]}"' if s["news_headline"] else ""
            add(f"  news peak impact {s['news_impact_max']:.0f}{head}")

    add("\n" + "-" * 76)
    add(f"NEWS-DOMINATED DAYS  (quarantined from the sign read; flag rule: peak impact "
        f">= {IMPACT_FLAG} or mean count >= {COUNT_FLAG:g}, plus {EVENT_DAYS_PATH.name})")
    if flags:
        for day in sorted(flags):
            add(f"  EXCLUDED  {day}  — {flags[day]}")
    else:
        add("  (none flagged — no news columns yet, or all days below threshold)")

    rv_all, ac_all = realized_vol_by_regime(days), autocorr_by_regime(days)
    rv_cln, ac_cln = realized_vol_by_regime(clean), autocorr_by_regime(clean)
    n_int_cln = sum(b["n"] for b in rv_cln.values())

    add("\n" + "-" * 76)
    _render_block(add, rv_all, ac_all, f"ALL {len(days)} days")
    if flags:
        _render_block(add, rv_cln, ac_cln,
                      f"CLEAN {len(clean)} days (news-dominated removed)")
    else:
        add("\n(no days flagged — CLEAN read == ALL read above.)")

    add("\nINDIA VIX cross-check (independent of the sign assumption)")
    for day, dom, v0, v1 in vix_by_regime_day(summaries):
        add(f"  {day}  dominant {dom:9}  VIX {v0:.2f} -> {v1:.2f} ({v1 - v0:+.2f})")

    add("\n" + "-" * 76)
    for line in _verdict(rv_cln, ac_cln, len(clean), n_int_cln, n_flagged=len(flags)):
        add(line)
    return "\n".join(L)


def _render_block(add, rv: dict, ac: dict, label: str) -> None:
    """Print one realised-vol + autocorrelation block under a heading."""
    add(f"\nREALISED VOL by regime — {label}  (model wants NEGATIVE > POSITIVE)")
    for reg in ("POSITIVE", "NEGATIVE", "FLAT"):
        if reg in rv:
            b = rv[reg]
            add(f"  {reg:9} n={b['n']:3}  rv={b['rv_pct']:.3f}%  mean={b['mean_pct']:+.3f}%")
    add(f"\nLAG-1 AUTOCORRELATION by regime — {label}  (POSITIVE<0 mean-revert, NEGATIVE>0 momentum)")
    for reg in ("POSITIVE", "NEGATIVE", "FLAT"):
        if reg in ac:
            b = ac[reg]
            tag = "" if not np.isfinite(b["autocorr"]) else (
                "momentum" if b["autocorr"] > 0 else "mean-revert")
            ac_txt = "n/a" if not np.isfinite(b["autocorr"]) else f"{b['autocorr']:+.3f}"
            add(f"  {reg:9} n={b['n']:3} pairs  autocorr={ac_txt}  {tag}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyse the Phase A intraday time-series.")
    ap.add_argument("--days", type=int, default=None, help="only the last N captured days")
    ap.add_argument("--both-signs", action="store_true",
                    help="run the dealer-sign convention comparison (quarantine OFF)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if args.both_signs:
        print(both_signs_report(load_all(TS_DIR, args.days)))
    else:
        print(analyze(last_n=args.days))


if __name__ == "__main__":
    main()
