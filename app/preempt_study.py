"""Does PASSIVE big-money positioning preempt the multi-day move? — the pre-registered test.

Implements exactly docs/preempt-positioning-preregistration.md (tier A, ungated, full participant-OI
history): signal = k-day DRIFT of FII net-futures / dominant-writer net-option-writing, z-scored within a
trailing 60-day window; outcome = forward spot log-return at h in {1,3,5,10} trading days; the test is the
PARTIAL IC (signal and return residualised on the trailing 5-day return = the momentum confound), with a
block-bootstrap CI for the overlapping windows and a circular-shift placebo. Sign-free; decision-support;
no forecast is asserted beyond what the CI/placebo support. See CLAUDE.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

K_DRIFT = 3                 # lookback (trading days) for the accumulation drift
Z_WIN = 60                  # trailing window for the z-score
TRAIL = 5                   # trailing-return control window (days)
HORIZONS = (1, 3, 5, 10)    # forward-return horizons (trading days)
N_BOOT = 2000
SEED = 7


# --- data assembly -------------------------------------------------------------------------------

def _closes() -> dict:
    from scripts.analyze_participant_ic import nifty_daily_close
    return nifty_daily_close()


def _participant_frame():
    """Chronological FII net_fut + dominant-writer net_opt_writing per captured day."""
    from app.participant_oi import available_days, load_participant_day, derive_signals
    rows = []
    writer_tally: dict[str, int] = {}
    per_day = {}
    for d in available_days():
        parsed = load_participant_day(d)
        sig = derive_signals(parsed) if parsed else None
        if not sig:
            continue
        pp = sig["per_participant"]
        fii = (pp.get("FII") or {}).get("net_fut")
        tw = sig.get("top_option_writer")
        if tw:
            writer_tally[tw] = writer_tally.get(tw, 0) + 1
        per_day[d] = pp
        rows.append({"date": d, "fii_net_fut": fii})
    df = pd.DataFrame(rows).dropna(subset=["fii_net_fut"]).reset_index(drop=True)
    # dominant writer cohort = the one that is top writer on the most days (a fixed structural choice)
    cohort = max(writer_tally, key=writer_tally.get) if writer_tally else "Client"
    df["writer_net"] = [
        (per_day.get(d, {}).get(cohort) or {}).get("net_opt_writing") for d in df["date"]]
    return df, cohort, writer_tally


def build_frame() -> tuple[pd.DataFrame, dict]:
    """One row per participant-day that has the full feature set + a close, with forward returns at every
    horizon and the trailing-return control. Point-in-time: drift/z use only prior days; forward uses the
    close calendar (all trading days), not just participant days."""
    closes = _closes()
    pf, cohort, tally = _participant_frame()
    cdays = sorted(closes)
    cidx = {d: i for i, d in enumerate(cdays)}

    def fwd(d, h):
        i = cidx.get(d)
        if i is None or i + h >= len(cdays):
            return np.nan
        return float(np.log(closes[cdays[i + h]] / closes[d]))

    def trail(d):
        i = cidx.get(d)
        if i is None or i - TRAIL < 0:
            return np.nan
        return float(np.log(closes[d] / closes[cdays[i - TRAIL]]))

    pf = pf[pf["date"].isin(closes)].reset_index(drop=True)
    # drift = k participant-days back
    pf["fii_drift"] = pf["fii_net_fut"] - pf["fii_net_fut"].shift(K_DRIFT)
    pf["writer_drift"] = pf["writer_net"] - pf["writer_net"].shift(K_DRIFT)
    # z-score each drift within the trailing Z_WIN window (point-in-time: shift so t excluded from its own μ/σ)
    for c in ("fii_drift", "writer_drift"):
        mu = pf[c].shift(1).rolling(Z_WIN, min_periods=Z_WIN // 2).mean()
        sd = pf[c].shift(1).rolling(Z_WIN, min_periods=Z_WIN // 2).std()
        pf[c + "_z"] = (pf[c] - mu) / sd
    pf["trail5"] = pf["date"].map(trail)
    for h in HORIZONS:
        pf[f"fwd{h}"] = pf["date"].map(lambda d: fwd(d, h))
    meta = {"cohort": cohort, "writer_tally": tally, "n_participant_days": len(pf),
            "n_close_days": len(cdays), "date_span": [pf["date"].min(), pf["date"].max()]}
    return pf, meta


# --- statistics ----------------------------------------------------------------------------------

def _spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = ~(np.isnan(a) | np.isnan(b))
    a, b = a[m], b[m]
    if len(a) < 8 or np.std(a) == 0 or np.std(b) == 0:
        return np.nan
    return float(np.corrcoef(pd.Series(a).rank(), pd.Series(b).rank())[0, 1])


def _residualise(y, x):
    """OLS residual of y on x (both 1-D); NaN-safe on the paired mask handled by caller."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    if np.std(x) == 0:
        return y - np.nanmean(y)
    b = np.polyfit(x, y, 1)
    return y - np.polyval(b, x)


def partial_ic(sig, fwd, ctrl):
    """Spearman IC of sig vs fwd AFTER residualising BOTH on ctrl (the trailing-return momentum control)."""
    s, f, c = (np.asarray(v, float) for v in (sig, fwd, ctrl))
    m = ~(np.isnan(s) | np.isnan(f) | np.isnan(c))
    s, f, c = s[m], f[m], c[m]
    if len(s) < 12:
        return np.nan, 0
    return _spearman(_residualise(s, c), _residualise(f, c)), len(s)


def _block_boot(sig, fwd, ctrl, h, partial=True):
    """Block-bootstrap CI on the (partial) IC: contiguous ~2h-day blocks preserve the overlap-induced
    autocorrelation. Returns (mean, lo, hi) at 2.5/97.5%."""
    rng = np.random.default_rng(SEED)
    s, f, c = (np.asarray(v, float) for v in (sig, fwd, ctrl))
    m = ~(np.isnan(s) | np.isnan(f) | np.isnan(c))
    s, f, c = s[m], f[m], c[m]
    n = len(s)
    if n < 20:
        return np.nan, np.nan, np.nan
    bl = max(2 * h, 2)
    nblocks = int(np.ceil(n / bl))
    ics = []
    for _ in range(N_BOOT):
        starts = rng.integers(0, n - bl + 1, size=nblocks)
        idx = np.concatenate([np.arange(st, st + bl) for st in starts])[:n]
        ss, ff, cc = s[idx], f[idx], c[idx]
        ic = (_spearman(_residualise(ss, cc), _residualise(ff, cc)) if partial
              else _spearman(ss, ff))
        if not np.isnan(ic):
            ics.append(ic)
    ics = np.array(ics)
    return float(ics.mean()), float(np.percentile(ics, 2.5)), float(np.percentile(ics, 97.5))


def _placebo(sig, fwd, ctrl, h, partial=True):
    """Circular-shift placebo: shift fwd by a random >=20-day offset (breaks signal->return, keeps
    autocorrelations). Returns the null distribution's 2.5/97.5%ile of the (partial) IC."""
    rng = np.random.default_rng(SEED + 1)
    s, f, c = (np.asarray(v, float) for v in (sig, fwd, ctrl))
    m = ~(np.isnan(s) | np.isnan(f) | np.isnan(c))
    s, f, c = s[m], f[m], c[m]
    n = len(s)
    if n < 40:
        return np.nan, np.nan
    ics = []
    for _ in range(N_BOOT):
        off = rng.integers(20, n - 20)
        ff = np.roll(f, off)
        ic = (_spearman(_residualise(s, c), _residualise(ff, c)) if partial
              else _spearman(s, ff))
        if not np.isnan(ic):
            ics.append(ic)
    ics = np.array(ics)
    return float(np.percentile(ics, 2.5)), float(np.percentile(ics, 97.5))


def run(signal_col: str = "fii_drift_z") -> dict:
    """The full pre-registered test for one signal across all horizons."""
    pf, meta = build_frame()
    out = {"meta": meta, "signal": signal_col, "horizons": {}}
    for h in HORIZONS:
        sig, fwd, ctrl = pf[signal_col], pf[f"fwd{h}"], pf["trail5"]
        raw = _spearman(sig, fwd)
        pic, n = partial_ic(sig, fwd, ctrl)
        bmean, blo, bhi = _block_boot(sig, fwd, ctrl, h)
        plo, phi = _placebo(sig, fwd, ctrl, h)
        out["horizons"][h] = {
            "n": n, "raw_ic": _r(raw), "partial_ic": _r(pic),
            "boot_mean": _r(bmean), "ci": [_r(blo), _r(bhi)],
            "placebo_ci": [_r(plo), _r(phi)],
            "ci_excludes_0": bool(not np.isnan(blo) and (blo > 0 or bhi < 0)),
            "beats_placebo": bool(not np.isnan(pic) and not np.isnan(phi)
                                  and (pic > phi or pic < plo))}
    out["verdict"] = _verdict(out["horizons"])
    return out


def _verdict(hz: dict) -> str:
    """Pre-registered decision rule: CONFIRM iff partial IC > 0, CI excludes 0, and beats placebo at BOTH
    h=5 and h=10; KILL iff partial IC < 0 with CI excluding 0 at h>=5; else NULL."""
    h5, h10 = hz.get(5, {}), hz.get(10, {})
    def pos_sig(x):
        return x.get("partial_ic", 0) is not None and (x.get("partial_ic") or 0) > 0 \
            and x.get("ci_excludes_0") and x.get("beats_placebo")
    def neg_sig(x):
        return (x.get("partial_ic") or 0) < 0 and x.get("ci_excludes_0")
    if pos_sig(h5) and pos_sig(h10):
        return "CONFIRM"
    if neg_sig(h5) or neg_sig(h10):
        return "KILL"
    return "NULL"


def _r(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 3)
