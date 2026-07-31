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
    per_day: list[dict] = []                      # for the day-clustered (honest) test
    for label, df in days:
        sw = detect(df, cfg, prev_days.get(label))
        p = _placebo(df, cfg)
        allsw += sw
        if len(p):
            plc.append(p)
        if sw and len(p):                         # this day's mean sweep-edge over its own placebo
            per_day.append({"day": label, "n": len(sw),
                            "edge": float(np.mean([s.fwd for s in sw]) - p.mean())})
    placebo = np.concatenate(plc) if plc else np.array([])
    return {"cfg": asdict(cfg), "n_days": len(days), "placebo": _stat(placebo),
            "overall": _score(allsw, placebo),
            "day_clustered": _day_cluster(per_day),
            "by_level": {lt: _score([s for s in allsw if s.level_type == lt], placebo)
                         for lt in cfg.level_types},
            "by_signature": {
                "oi_unwind": _score([s for s in allsw if s.doi is not None and s.doi < 0], placebo),
                "oi_build": _score([s for s in allsw if s.doi is not None and s.doi >= 0], placebo),
                "absorption": _score([s for s in allsw if _absorb(s)], placebo),
            },
            "sweeps": [asdict(s) for s in allsw]}


def _day_cluster(per_day: list[dict]) -> dict:
    """The HONEST significance test: treat each DAY as one observation (its mean sweep-edge over its own
    placebo), then t across days. Pooling all sweeps overstates n because same-day sweeps are correlated —
    this uses n = trading days, the real independent sample. Also reports how many days leaned positive."""
    if len(per_day) < 2:
        return {"n_days": len(per_day), "note": "need >=2 days"}
    edges = np.array([d["edge"] for d in per_day])
    se = float(edges.std(ddof=1) / np.sqrt(len(edges)))
    return {"n_days": len(edges), "mean_daily_edge": round(float(edges.mean()), 2),
            "t_across_days": round(float(edges.mean() / se), 2) if se else None,
            "days_positive": int((edges > 0).sum()),
            "per_day": [{"day": d["day"], "n": d["n"], "edge": round(d["edge"], 2)} for d in per_day]}


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


# --- live sweep-watch (for the dashboard strip) --------------------------------------------------
# The STUDY's detect() only returns sweeps with a completed forward outcome; the live strip also needs
# the pending latest sweep. scan_live() reports the day's prior-day levels + their current sweep state.

def prev_day_levels(df) -> dict | None:
    """Prior-day high/low/close from that day's near-month raw ticks (the salient levels the study found
    survive the placebo). None if the frame is empty / lacks a near contract."""
    ep, px, *_ = _arrays(df)
    if len(px) < 3:
        return None
    return {"high": float(px.max()), "low": float(px.min()), "close": float(px[-1])}


def scan_live(df, prev_day: dict | None, cfg: Config | None = None, upto_epoch: float | None = None) -> dict:
    """Live prior-day-level sweep watch (the one signal that cleared the placebo on both instruments).
    Returns each prior-day level with distance from the latest price and, if it was swept today (fast
    breach), when + the current state: FADED (came back through), BEYOND (still past the level), or
    PENDING (just swept, not enough time yet). Descriptive; the study says salient-level sweeps LEAN to
    fade — a watch item, never an auto-signal."""
    cfg = cfg or Config()
    ep, px, *_ = _arrays(df)
    if len(px) < 10 or not prev_day:
        return {"spot": (float(px[-1]) if len(px) else None), "levels": []}
    if upto_epoch is not None:
        m = ep <= upto_epoch
        ep, px = ep[m], px[m]
        if len(px) < 10:
            return {"spot": (float(px[-1]) if len(px) else None), "levels": []}
    now_px, now_t = float(px[-1]), float(ep[-1])
    thr, hz = cfg.thrust_s, cfg.horizon_min * 60
    out = []
    for name, side in (("high", "up"), ("low", "down"), ("close", "up")):
        lvl = prev_day.get(name)
        if lvl is None:
            continue
        lvl = float(lvl)
        up = side == "up"
        # find the LATEST CROSSING of this level (was on-side the prior bar, breached-with-thrust now) —
        # the actual sweep moment, not every bar that merely sits beyond after an early break.
        beyond = (px >= lvl + cfg.margin) if up else (px <= lvl - cfg.margin)
        swept_i = None
        for i in range(1, len(ep)):
            if not beyond[i] or beyond[i - 1]:
                continue                                          # only fresh crossings into 'beyond'
            tmask = (ep >= ep[i] - thr) & (ep <= ep[i])
            thrust = px[i] - px[tmask][0] if tmask.sum() >= 2 else 0.0
            if (thrust >= cfg.margin) if up else (-thrust >= cfg.margin):
                swept_i = i
        rec = {"name": name, "level": lvl, "dist": round(now_px - lvl, 1)}
        if swept_i is not None:
            t0 = ep[swept_i]
            after = px[(ep > t0) & (ep <= now_t)]
            back = (now_px <= lvl) if up else (now_px >= lvl)          # price returned through the level
            if back or (len(after) and ((after <= lvl).any() if up else (after >= lvl).any())):
                state = "faded"
            elif now_t - t0 >= hz:
                state = "beyond"                                       # held past the level beyond horizon
            else:
                state = "pending"
            rec.update(swept=True, dir=side, swept_at=_epoch_hhmm(df, t0), state=state)
        else:
            rec.update(swept=False)
        out.append(rec)
    return {"spot": now_px, "levels": out}


def _epoch_hhmm(df, epoch) -> str:
    """Best-effort HH:MM for an epoch, from the raw frame's own time_ist column (avoids tz guessing)."""
    n = df[df["roll"] == "near"] if "roll" in df.columns else df
    hit = n[n["epoch"] == int(epoch)]
    if len(hit) and "time_ist" in hit.columns:
        return str(hit.iloc[0]["time_ist"])[:5]
    return ""
