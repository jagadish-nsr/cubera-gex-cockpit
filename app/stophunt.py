"""Stop-run / liquidity-sweep study on the big-money raw tick archive (near-month futures).

Tests the recurring "stop-loss hunting" read: a fast thrust breaches a level where stops cluster,
triggers a cascade, then reverses. This module DETECTS candidate sweeps point-in-time and MEASURES
their forward outcome against a placebo — it does not trade and makes no forecast. Decision-support;
a description of a tendency, gated behind a placebo baseline and a t-stat.

Pre-registered (fixed before looking at outcomes):
  level     — a price where stops plausibly cluster, live as of t:
                rolling  : max/min of ltp over the prior ``lookback_min`` (point-in-time);
                round    : nearest multiple of ``round_step`` (round-number stops);
                openrng  : high/low of the first ``or_min`` minutes (opening-range break);
                pday     : prior-day high/low/close (passed in via ``prev_day``).
  SWEEP     — ltp breaches a live level by >= ``margin`` pts AND it is a THRUST (move over the last
              ``thrust_s`` s >= ``margin``): a fast stab, not a slow grind. One fire per level per
              ``cooldown_s`` (debounce the cluster of snapshots around the tip).
  outcome   — over the next ``horizon_min``: FADE = ltp returns back across the broken level;
              ``fwd`` = oriented pts (up-sweep that falls / down-sweep that rises => +, "hunt worked");
              ``mfe``/``mae`` = favourable/adverse excursion in that fade orientation;
              ``hit`` = reversed by >= ``barrier`` pts BEFORE continuing by >= ``barrier`` (a clean,
              cost-aware label — set ``barrier`` above the round-trip cost to make FADE tradeable).
  features  — ``doi`` (OI change over the lookback: <0 = positions exiting = the stop-out fingerprint);
              ``book_imb`` = (tot_buy_qty − tot_sell_qty)/(sum): resting-depth tilt at the breach
              (for an up-sweep, a supply-heavy book, book_imb<0, is absorption that supports a fade).

Honest caveats: the archive is ~2–3 s SNAPSHOTS, not per-trade tick — the sub-2 s cascade tip is
undersampled. Effects must clear a PLACEBO (same forward question at random matched-direction points)
before being believed, and a few days is not enough n. See docs/tick-data-ic-research.md.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


@dataclass
class Config:
    lookback_min: float = 20.0      # window that defines the rolling extreme
    margin: float = 5.0             # pts beyond a level to count as a breach
    thrust_s: float = 30.0          # breach must move >= margin over the last thrust_s seconds
    horizon_min: float = 5.0        # forward outcome window
    barrier: float = 8.0            # cost-aware first-touch barrier for the ``hit`` label (pts)
    cooldown_s: float = 120.0       # min gap between two fires of the SAME level type+side
    round_step: float = 50.0        # round-number level grid (0 disables)
    or_min: float = 15.0            # opening-range window (0 disables)
    n_placebo: int = 2000           # random baseline points per day
    seed: int = 7
    level_types: tuple = ("rolling", "round", "openrng", "pday")


@dataclass
class Sweep:
    epoch: int
    time_ist: str
    direction: str                  # 'up' | 'down'
    level_type: str
    price: float
    level: float
    thrust: float
    doi: float | None
    book_imb: float | None
    faded: bool | None = None
    fwd: float | None = None
    mfe: float | None = None
    mae: float | None = None
    hit: int | None = None          # +1 fade first, -1 continue first, 0 neither within horizon


def _arrays(df):
    """Pull the near-month, epoch-sorted arrays this study needs from a raw-archive frame."""
    if df is None or getattr(df, "empty", True) or "roll" not in df.columns:
        e = np.array([])
        return e, e, e, e, e, np.array([], dtype=object)
    n = df[df["roll"] == "near"].sort_values("epoch").reset_index(drop=True)
    ep = n["epoch"].values.astype(float)
    px = n["ltp"].values.astype(float)
    oi = n["oi"].values.astype(float) if "oi" in n else np.full(len(n), np.nan)
    tb = n["tot_buy_qty"].values.astype(float) if "tot_buy_qty" in n else np.full(len(n), np.nan)
    ts = n["tot_sell_qty"].values.astype(float) if "tot_sell_qty" in n else np.full(len(n), np.nan)
    tstr = n["time_ist"].values if "time_ist" in n else np.array([""] * len(n))
    return ep, px, oi, tb, ts, tstr


def _mins(tstr: str) -> float:
    try:
        h, m, *_ = str(tstr).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return np.nan


def _live_levels(i, ep, px, cfg: Config, or_hl, prev_day):
    """The (level, type) pairs a sweep could breach as of index i — all point-in-time (prior data only)."""
    out = []
    t, p = ep[i], px[i]
    if "rolling" in cfg.level_types:
        m = (ep >= t - cfg.lookback_min * 60) & (ep < t)
        if m.sum() >= 10:
            out.append((px[m].max(), "rolling", "up"))
            out.append((px[m].min(), "rolling", "down"))
    if "round" in cfg.level_types and cfg.round_step:
        lvl_up = np.ceil(p / cfg.round_step) * cfg.round_step
        lvl_dn = np.floor(p / cfg.round_step) * cfg.round_step
        out.append((lvl_up, "round", "up"))
        out.append((lvl_dn, "round", "down"))
    if "openrng" in cfg.level_types and or_hl is not None:
        hi, lo = or_hl
        if hi is not None:
            out.append((hi, "openrng", "up"))
            out.append((lo, "openrng", "down"))
    if "pday" in cfg.level_types and prev_day:
        for k, side in (("high", "up"), ("low", "down"), ("close", "up"), ("close", "down")):
            if prev_day.get(k) is not None:
                out.append((float(prev_day[k]), "pday", side))
    return out


def detect(df, cfg: Config | None = None, prev_day: dict | None = None) -> list[Sweep]:
    """Point-in-time sweep detection over one raw day. Returns Sweeps with outcomes filled."""
    cfg = cfg or Config()
    ep, px, oi, tb, ts, tstr = _arrays(df)
    if len(ep) < 20:
        return []
    # opening-range high/low, known only after or_min minutes from the first bar
    or_hl, or_ready_t = None, None
    if cfg.or_min:
        t0 = _mins(tstr[0])
        orm = np.array([_mins(x) for x in tstr])
        orwin = orm <= (t0 + cfg.or_min)
        if orwin.sum() >= 5:
            or_hl = (px[orwin].max(), px[orwin].min())
            or_ready_t = ep[orwin][-1]
    th, thr, hz, bar = cfg.lookback_min * 60, cfg.thrust_s, cfg.horizon_min * 60, cfg.barrier
    last_fire: dict = {}                       # (type, side) -> epoch, for the cooldown
    sweeps: list[Sweep] = []
    for i in range(len(ep)):
        t, p = ep[i], px[i]
        tmask = (ep >= t - thr) & (ep <= t)
        thrust = p - px[tmask][0] if tmask.sum() >= 2 else 0.0
        for lvl, ltype, side in _live_levels(i, ep, px, cfg, or_hl, prev_day):
            if ltype == "openrng" and (or_ready_t is None or t <= or_ready_t):
                continue                       # opening range not formed yet
            up = side == "up"
            breach = (p >= lvl + cfg.margin) if up else (p <= lvl - cfg.margin)
            fast = (thrust >= cfg.margin) if up else (-thrust >= cfg.margin)
            if not (breach and fast):
                continue
            key = (ltype, side)
            if key in last_fire and t - last_fire[key] < cfg.cooldown_s:
                continue
            lb = (ep >= t - th) & (ep < t)
            doi = float(oi[i] - oi[lb][0]) if (lb.sum() and not np.isnan(oi[i])) else None
            denom = (tb[i] + ts[i])
            book_imb = float((tb[i] - ts[i]) / denom) if (denom and not np.isnan(denom)) else None
            sw = Sweep(epoch=int(t), time_ist=str(tstr[i]), direction=side, level_type=ltype,
                       price=float(p), level=float(lvl), thrust=float(thrust), doi=doi, book_imb=book_imb)
            _fill_outcome(sw, ep, px, i, hz, bar, up)
            if sw.faded is not None:            # enough forward data to score
                sweeps.append(sw)
                last_fire[key] = t
    return sweeps


def _fill_outcome(sw: Sweep, ep, px, i, hz, bar, up):
    t, p, lvl = ep[i], px[i], sw.level
    fmask = (ep > t) & (ep <= t + hz)
    if fmask.sum() < 3:
        return
    fpx = px[fmask]
    sw.faded = bool((fpx <= lvl).any()) if up else bool((fpx >= lvl).any())
    sw.fwd = float(p - fpx[-1]) if up else float(fpx[-1] - p)          # + = reverted ("hunt worked")
    fav = (p - fpx) if up else (fpx - p)                              # favourable = reversal excursion
    sw.mfe, sw.mae = float(fav.max()), float(fav.min())
    # first-touch barrier: did it revert by >= bar before continuing by >= bar?
    sw.hit = 0
    for v in fav:
        if v >= bar:
            sw.hit = 1
            break
        if v <= -bar:
            sw.hit = -1
            break


def _placebo(df, cfg: Config) -> np.ndarray:
    """Oriented forward return at random matched-direction points — the baseline a real sweep must beat."""
    ep, px, *_ = _arrays(df)
    if len(ep) < 20:
        return np.array([])
    rng = np.random.default_rng(cfg.seed)
    idx = rng.choice(len(ep), size=min(cfg.n_placebo, len(ep)), replace=False)
    thr, hz = cfg.thrust_s, cfg.horizon_min * 60
    out = []
    for i in idx:
        t = ep[i]
        tmask = (ep >= t - thr) & (ep <= t)
        thrust = px[i] - px[tmask][0] if tmask.sum() >= 2 else 0.0
        fmask = (ep > t) & (ep <= t + hz)
        if fmask.sum() < 3 or thrust == 0:
            continue
        pend = px[fmask][-1]
        out.append((px[i] - pend) if thrust > 0 else (pend - px[i]))   # fade orientation
    return np.array(out)


def study(days: list, cfg: Config | None = None, prev_days: dict | None = None) -> dict:
    """Pool sweeps + placebo across days. ``days`` = list of (label, raw_df); ``prev_days`` maps label ->
    prior-day {high,low,close}. Returns overall stats, a per-level-type and per-signature breakdown."""
    cfg = cfg or Config()
    prev_days = prev_days or {}
    allsw: list[Sweep] = []
    plc: list[np.ndarray] = []
    for label, df in days:
        allsw += detect(df, cfg, prev_days.get(label))
        p = _placebo(df, cfg)
        if len(p):
            plc.append(p)
    placebo = np.concatenate(plc) if plc else np.array([])
    return {"cfg": asdict(cfg), "n_days": len(days), "placebo": _stat(placebo),
            "overall": _score(allsw, placebo),
            "by_level": {lt: _score([s for s in allsw if s.level_type == lt], placebo)
                         for lt in cfg.level_types},
            "by_signature": {
                "oi_unwind": _score([s for s in allsw if s.doi is not None and s.doi < 0], placebo),
                "oi_build": _score([s for s in allsw if s.doi is not None and s.doi >= 0], placebo),
                "absorption": _score([s for s in allsw if _absorb(s)], placebo),
            },
            "sweeps": [asdict(s) for s in allsw]}


def _absorb(s: Sweep) -> bool:
    """Resting book leans AGAINST the breakout at the breach (supply-heavy on an up-sweep / demand on a down)."""
    if s.book_imb is None:
        return False
    return s.book_imb < 0 if s.direction == "up" else s.book_imb > 0


def _stat(a) -> dict:
    a = np.asarray(a, float)
    return {"n": int(len(a)), "mean": (float(a.mean()) if len(a) else None)}


def _score(sw: list[Sweep], placebo) -> dict:
    if not sw:
        return {"n": 0}
    fwd = np.array([s.fwd for s in sw])
    faded = np.array([s.faded for s in sw], float)
    hit = np.array([s.hit for s in sw if s.hit is not None])
    d = {"n": len(sw), "up": sum(s.direction == "up" for s in sw),
         "fade_rate": round(float(faded.mean()), 3),
         "mean_fwd": round(float(fwd.mean()), 2), "median_fwd": round(float(np.median(fwd)), 2),
         "mean_mfe": round(float(np.mean([s.mfe for s in sw])), 2),
         "mean_mae": round(float(np.mean([s.mae for s in sw])), 2),
         "hit_net": (round(float(np.mean(hit)), 3) if len(hit) else None)}   # +1 fade / -1 continue
    if len(placebo):
        edge = fwd.mean() - placebo.mean()
        d["edge_vs_placebo"] = round(float(edge), 2)
        d["t"] = None                                    # needs >1 sweep for a variance/t
        if len(fwd) > 1:
            se = float(np.sqrt(fwd.var(ddof=1) / len(fwd) + placebo.var(ddof=1) / len(placebo)))
            d["t"] = round(float(edge / se), 2) if se else None
    return d
