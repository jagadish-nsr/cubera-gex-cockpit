"""Offline gamma-blast study — a pure-function harness for a BETTER true-vs-fake read + an EXIT signal.

Built from the offline study of the captured expiry days (2026-09-02). Three findings drove it:

  1. The live grader's dominant term (ITM-side OI covering = "ignition", weight 0.5) is CONFOUNDED by
     expiry-afternoon settlement decay: OI falls 30-90% on BOTH sides every expiry, so it reads
     "covering" (→ TRUE) on pins too. It does not separate a real run from a pin.
  2. ITM-side PREMIUM EXPANSION and REALIZED DELTA (the fraction of the spot move the ITM option
     captures) DO separate them — and are the naturally sign-free "gamma" reads. But they confirm
     SLOWLY: both real runs in-sample committed 45-95 min after the 13:45 anchor. There is no fast
     ignition to catch; the honest job is to recognise the commit when it forms and then manage the ride.
  3. The crossover has no persistence gate, so pin chop fires 16-85 spurious crosses. A dwell/hysteresis
     gate turns those into 0-1 real breaks.

Plus a data-hygiene fix (drop the final-second settlement/contract-remap tick) and an EXIT block
(spot-trailing stop + a hard pre-settlement time-stop — the piece the live hero lacks).

Primary data = the 1-min gamma_blast ladder archive (real spot, full strike ladder, net_gex, longer
history). Everything here is a PURE function: a frame/series in, a dict/number out. Sign-free, heuristic,
decision-support only — NOT a validated classifier (in-sample is a handful of expiries). NEVER auto-executes.
"""
from __future__ import annotations

STEP = 50.0                 # NIFTY strike step (points); verified from the ladder
COMMIT_PTS = 40.0           # |spot-K| that counts as "committed past the strike" (~0.8 of a step)
DWELL_S = 45                # a break must persist this long before it's a real crossover
MARGIN_PTS = 10.0           # spot must clear K by this margin to arm the break (hysteresis)


def _mins(hhmm: str) -> int:
    """'HH:MM' -> minutes since midnight, or -1 if unparseable."""
    try:
        h, m = str(hhmm)[:5].split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return -1


ROOT = None                 # set lazily in _root()


def _root():
    global ROOT
    if ROOT is None:
        from pathlib import Path
        ROOT = Path(__file__).resolve().parents[1]
    return ROOT


# per-strike ladder sources with real spot + call/put ltp + oi + net_gex, best (finest/most complete)
# first. gamma_blast is 1-min but was truncated on some days; oi_strikes is ~5-min but more complete.
_LADDER_SOURCES = ("gamma_blast", "oi_strikes")


def _complete(df, cut: str = "13:45", end_min: str = "15:10") -> bool:
    """A ladder day is usable only if it brackets the blast window: a snapshot within 10 min of the cut
    AND coverage out to at least ``end_min`` (so commit/exit aren't cut off). Guards the truncated days."""
    if df is None or df.empty or "time_ist" not in df:
        return False
    tm = df["time_ist"].map(_mins)
    return bool((tm.between(_mins(cut) - 10, _mins(cut) + 10)).any() and tm.max() >= _mins(end_min))


def _wide_from_intraday(symbol: str, day: str):
    """The Dhan intraday store (data.dhan.load_intraday_day) reshaped to the wide per-(time,strike) ladder
    the study consumes. It's LONG (one CE/PE row per strike-minute) with ``close`` as the price, so pivot
    CE→call_*, PE→put_*. This is the SAME source the dashboard replay renders, and it reaches back years —
    so the study/replay run on any stored day, not just the few with a journal capture. None if absent."""
    try:
        from data.dhan import load_intraday_day
    except Exception:  # noqa: BLE001
        return None
    raw = load_intraday_day(symbol, day)
    if raw is None or getattr(raw, "empty", True) or "side" not in raw:
        return None
    ce = (raw[raw["side"] == "CE"][["time_ist", "strike", "spot", "close", "oi"]]
          .rename(columns={"close": "call_ltp", "oi": "call_oi"}))
    pe = (raw[raw["side"] == "PE"][["time_ist", "strike", "close", "oi"]]
          .rename(columns={"close": "put_ltp", "oi": "put_oi"}))
    wide = ce.merge(pe, on=["time_ist", "strike"], how="outer")
    return wide if not wide.empty else None


def load_day(symbol: str, day: str, cut: str = "13:45"):
    """The per-strike ladder for one day (real spot, call/put ltp+oi), from the best source that brackets
    the blast window: gamma_blast (1-min) if complete, else oi_strikes (~5-min), else the Dhan intraday
    store (long history — what the dashboard replay uses). None if no source covers the day. Drops the
    settlement-remap artifact (see below)."""
    import pandas as pd
    for src in _LADDER_SOURCES:
        p = _root() / "data" / "journal" / src / symbol / f"{day}.csv"
        if p.exists():
            df = pd.read_csv(p)
            if _complete(df, cut):
                return _drop_remap(df)
    wide = _wide_from_intraday(symbol, day)          # fallback: the long-history intraday store
    if wide is not None and _complete(wide, cut):
        return _drop_remap(wide)
    return None


def load_spot_day(symbol: str, day: str):
    """Just the intraday spot path (from the timeseries archive — much longer history than the ladders),
    for the broad RUN/PIN classification on every expiry day. Returns a frame of time_ist, mins, spot or
    None. No premiums — use load_day for those."""
    import pandas as pd
    p = _root() / "data" / "journal" / "timeseries" / symbol / f"{day}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p)
    if df.empty or "spot" not in df:
        return None
    df = df[["time_ist", "spot"]].dropna().sort_values("time_ist").reset_index(drop=True)
    df["mins"] = df["time_ist"].map(_mins)
    return df


def _drop_remap(df):
    """Data hygiene: near the close the feed can emit a final tick whose OI/volume jump discontinuously
    (a settlement print or a next-expiry contract remap) with a price that craters — it corrupts any
    first/last read. Drop rows whose anchor-band OI leaps >50% above its running median. Conservative:
    only fires on the genuine discontinuity, leaves the ordinary intraday OI decay alone."""
    import pandas as pd
    if "call_oi" not in df or df.empty:
        return df
    out = []
    for _, g in df.groupby("strike"):
        g = g.sort_values("time_ist")
        med = g["call_oi"].rolling(30, min_periods=5).median()
        keep = (med.isna()) | (g["call_oi"] <= med * 1.5)
        out.append(g[keep])
    return pd.concat(out).sort_values(["time_ist", "strike"]).reset_index(drop=True)


def anchor_strike(df, cut: str = "13:45") -> float | None:
    """The blast anchor strike as of ``cut``. Uses PUT-CALL PARITY, not rounded spot: at expiry the ATM
    is the strike where call ≈ put (call − put = spot − K, zero when K = spot), which is exactly the
    strike the blast fires on. Picking the strike that MINIMISES |call − put| at the cut is robust to the
    off-by-one that rounding spot causes near a strike boundary (it flipped run/pin on borderline days).
    Falls back to nearest-spot when premiums are absent (spot-only source)."""
    at = df[df["time_ist"].str[:5] == cut]
    if at.empty:                                    # fall back to the first snapshot at/after cut
        cm = _mins(cut)
        at = df[df["time_ist"].map(_mins) >= cm]
        if at.empty:
            return None
        at = at[at["time_ist"] == at["time_ist"].iloc[0]]
    if {"call_ltp", "put_ltp"} <= set(at.columns) and at[["call_ltp", "put_ltp"]].notna().any().all():
        parity = (at["call_ltp"] - at["put_ltp"]).abs()
        return float(at.iloc[parity.values.argmin()]["strike"])
    spot = float(at["spot"].iloc[0])
    return float(at.iloc[(at["strike"] - spot).abs().argmin()]["strike"])


def anchor_series(df, K: float, cut: str = "13:45"):
    """One row per minute from ``cut`` onward for the anchor strike K: time_ist, mins, spot, call_ltp,
    put_ltp, call_oi, put_oi, call_vol, put_vol, net_gex, disp (= spot - K). Epoch-ordered."""
    cm = _mins(cut)
    a = df[(df["strike"] == K) & (df["time_ist"].map(_mins) >= cm)].copy()
    if a.empty:
        return None
    a = a.sort_values("time_ist").reset_index(drop=True)
    a["mins"] = a["time_ist"].map(_mins) - cm
    a["disp"] = a["spot"] - K
    return a


def persistence_cross(a, margin: float = MARGIN_PTS, dwell_min: float = DWELL_S / 60.0):
    """The FIRST real break of K: spot clears K by ``margin`` and STAYS on that side for ``dwell_min``
    minutes continuously (hysteresis). Returns {time_ist, mins, direction, spot} or None. This is the
    pin-chop killer — a momentary touch of K does not qualify, only a sustained break does."""
    if a is None or a.empty:
        return None
    arm = None                                      # (sign, start_mins) of a candidate break
    for r in a.itertuples():
        d = r.disp
        sign = 1 if d >= margin else -1 if d <= -margin else 0
        if sign == 0:
            arm = None
            continue
        if arm is None or arm[0] != sign:
            arm = (sign, r.mins)
        elif r.mins - arm[1] >= dwell_min:
            return {"time_ist": r.time_ist, "mins": float(arm[1]),
                    "direction": "up" if sign > 0 else "down", "spot": float(r.spot)}
    return None


def realized_delta(a, itm: str, win_min: float = 8.0) -> float | None:
    """Realized ITM-side delta over the last ``win_min`` minutes = the OLS slope of ITM premium vs the
    REAL spot (sign-free, model-free; slope is stabler than an endpoint ratio, which blows up when the
    endpoints barely differ). ~1 = the option captures the whole move (committed), ~0.5 = ATM/pinned,
    ~0 = out-of-the-money. None if spot didn't move enough in the window to read a slope. This is the
    tractable 'gamma' read — its ramp over time IS realized gamma."""
    import numpy as np
    if a is None or len(a) < 3:
        return None
    last = a["mins"].iloc[-1]
    w = a[a["mins"] >= last - win_min]
    S = w["spot"].to_numpy(float)
    P = w[f"{itm}_ltp"].to_numpy(float)
    if len(S) < 3 or (S.max() - S.min()) < 6.0:     # need real spot dispersion to fit a slope
        return None
    slope = float(np.polyfit(S, P, 1)[0])
    return slope if itm == "call" else -slope        # put premium falls as spot rises → report |delta|


def commit_time(a, thresh: float = COMMIT_PTS) -> dict | None:
    """The first minute after which |spot - K| stays >= ``thresh`` for the WHOLE rest of the session —
    i.e. spot committed past the strike and never came back. Returns {time_ist, mins, direction} or None
    (no sustained commit = a pin). This is the in-sample ground-truth discriminator: runs commit, pins don't."""
    if a is None or a.empty:
        return None
    disp = a["disp"].values
    times = a["time_ist"].values
    mins = a["mins"].values
    for i in range(len(a)):
        rest = disp[i:]
        if abs(rest).min() >= thresh:
            return {"time_ist": str(times[i]), "mins": float(mins[i]),
                    "direction": "up" if rest[-1] >= 0 else "down"}
    return None


def premium_expansion(a, itm: str, from_mins: float = 0.0) -> float | None:
    """ITM-side premium change (fraction) from ``from_mins`` to the end of the window. >0 = the now-ITM
    option's premium expanded (delta ramped toward 1 → follow-through); <0 = it bled (pin/theta)."""
    if a is None or a.empty:
        return None
    w = a[a["mins"] >= from_mins]
    if len(w) < 2:
        return None
    p0, p1 = float(w[f"{itm}_ltp"].iloc[0]), float(w[f"{itm}_ltp"].iloc[-1])
    return (p1 / p0 - 1.0) if p0 else None


def exit_signal(a, itm: str, entry_mins: float, trail_pts: float = 30.0,
                flat_by: str = "15:25", cut: str = "13:45") -> dict:
    """Manage-the-ride EXIT for a blast trade entered at ``entry_mins`` on the ITM side. Two rules, from
    the study: (1) a trailing stop on SPOT EXCURSION past K (NOT premium — premium whipsaws on the early
    chop), giving back at most ``trail_pts`` from the best excursion; (2) a HARD flat by ``flat_by`` IST,
    because the last minutes carry an unhittable settlement print. Returns which fired, when, and the
    ITM premium captured at exit. Sign-free; decision-support — the human still confirms."""
    flat_m = _mins(flat_by) - _mins(cut)
    w = a[a["mins"] >= entry_mins].reset_index(drop=True)
    if w.empty:
        return {"reason": "no-window"}
    up = w["disp"].iloc[0] >= 0
    best = w["disp"].iloc[0]
    for r in w.itertuples():
        best = max(best, r.disp) if up else min(best, r.disp)
        give = (best - r.disp) if up else (r.disp - best)   # how far spot has retraced from its best
        if r.mins >= flat_m:
            return {"reason": "settlement-time-stop", "time_ist": r.time_ist, "mins": float(r.mins),
                    "itm_ltp": float(getattr(r, f"{itm}_ltp")), "best_excursion": float(best)}
        if give >= trail_pts:
            return {"reason": "spot-trailing-stop", "time_ist": r.time_ist, "mins": float(r.mins),
                    "itm_ltp": float(getattr(r, f"{itm}_ltp")), "best_excursion": float(best)}
    last = w.iloc[-1]
    return {"reason": "held-to-end", "time_ist": last["time_ist"], "mins": float(last["mins"]),
            "itm_ltp": float(last[f"{itm}_ltp"]), "best_excursion": float(best)}


def classify_spot(symbol: str, day: str, cut: str = "13:45") -> dict | None:
    """Broad RUN/PIN read needing ONLY the spot path (so it runs on every expiry day the timeseries
    covers, not just the few with captured premiums). Anchor = spot at ``cut`` rounded to the strike
    grid; verdict = did spot commit past that anchor (commit_time) and how far did it travel. This is the
    ground-truth label the premium/delta features are validated against. None if no spot for the day."""
    sp = load_spot_day(symbol, day)
    if sp is None or sp.empty:
        return None
    cm = _mins(cut)
    at = sp[sp["mins"] >= cm]
    if at.empty:
        return None
    K = round(float(at["spot"].iloc[0]) / STEP) * STEP
    a = at.assign(disp=at["spot"] - K).reset_index(drop=True)
    commit = commit_time(a)
    at3 = a[a["time_ist"].str[:5] >= "15:00"]
    return {
        "symbol": symbol, "day": day, "strike": K,
        "verdict": "RUN" if commit else "PIN",
        "commit_time": (commit or {}).get("time_ist"),
        "direction": (commit or {}).get("direction"),
        "final_disp": round(float(a["disp"].iloc[-1]), 1),
        "max_up": round(float(a["disp"].max()), 1), "max_dn": round(float(a["disp"].min()), 1),
        "disp_at_3pm": round(float(at3["disp"].iloc[0]), 1) if len(at3) else None,
        "move_after_3pm": round(float(a["spot"].iloc[-1] - at3["spot"].iloc[0]), 1) if len(at3) else None,
    }


def move_features(a, itm: str, from_m: float = 0.0) -> dict:
    """Measure the ITM-side features OVER THE MOVE — from ``from_m`` (the break/commit) to the PEAK
    excursion — not over the tail of the day. Sampling the tail understated them: by 15:30 the move is
    done and spot is flat, so a tail window reads ~0. The move is [from_m → the minute of max |disp|].
    Returns {rdelta, prem_exp, peak_mins, peak_disp}."""
    w = a[a["mins"] >= from_m]
    if w is None or len(w) < 2:
        return {"rdelta": None, "prem_exp": None, "peak_mins": None, "peak_disp": None}
    up = w["disp"].iloc[-1] >= 0 if itm == "call" else True
    peak_i = w["disp"].abs().values.argmax()
    peak_mins = float(w["mins"].iloc[peak_i])
    seg = w[w["mins"] <= peak_mins]                  # the active-move segment
    rd = realized_delta(seg, itm, win_min=peak_mins - from_m + 1) if len(seg) >= 3 else None
    # premium expansion = ITM premium at the peak vs at the break (the full ramp, not the faded tail)
    p0 = float(seg[f"{itm}_ltp"].iloc[0]); p1 = float(seg[f"{itm}_ltp"].iloc[-1])
    pe = (p1 / p0 - 1.0) if p0 else None
    return {"rdelta": rd, "prem_exp": pe, "peak_mins": peak_mins,
            "peak_disp": round(float(w["disp"].iloc[peak_i]), 1)}


def study_day(symbol: str, day: str, cut: str = "13:45") -> dict | None:
    """One-shot offline read of an expiry day: anchor, the persistence-gated break, the sustained-commit
    verdict (run vs pin), realized delta + premium expansion at commit, the 3PM-window move, and the exit
    the discipline would have taken. The harness row. None if the day isn't captured."""
    df = load_day(symbol, day)
    if df is None:
        return None
    K = anchor_strike(df, cut)
    if K is None:
        return None
    a = anchor_series(df, K, cut)
    if a is None or len(a) < 2:
        return None
    brk = persistence_cross(a)
    commit = commit_time(a)
    direction = (commit or brk or {}).get("direction") or ("up" if a["disp"].iloc[-1] >= 0 else "down")
    itm = "call" if direction == "up" else "put"
    from_m = (commit or brk or {}).get("mins", 0.0)
    # 3PM prior: displacement at 15:00 and the move made AFTER 15:00
    at3 = a[a["time_ist"].str[:5] >= "15:00"]
    move_after_3 = float(a["spot"].iloc[-1] - at3["spot"].iloc[0]) if len(at3) else None
    disp3 = float(at3["disp"].iloc[0]) if len(at3) else None
    mf = move_features(a, itm, from_m)
    ex = exit_signal(a, itm, from_m, cut=cut) if (commit or brk) else {"reason": "no-break"}
    return {
        "symbol": symbol, "day": day, "strike": K, "direction": direction,
        "verdict": "RUN" if commit else "PIN",
        "commit_time": (commit or {}).get("time_ist"),
        "break_time": (brk or {}).get("time_ist"),
        "final_disp": round(float(a["disp"].iloc[-1]), 1),
        "max_up": round(float(a["disp"].max()), 1), "max_dn": round(float(a["disp"].min()), 1),
        "rdelta_commit": (lambda v: round(v, 2) if v is not None else None)(mf["rdelta"]),
        "prem_exp": (lambda v: round(v, 2) if v is not None else None)(mf["prem_exp"]),
        "peak_time": None if mf["peak_mins"] is None else a[a["mins"] == mf["peak_mins"]]["time_ist"].iloc[0],
        "disp_at_3pm": None if disp3 is None else round(disp3, 1),
        "move_after_3pm": None if move_after_3 is None else round(move_after_3, 1),
        "exit": ex,
    }


# ── offline replay: what the (improved) hero WOULD have shown, minute by minute ──────────────────
# Every row uses ONLY data up to that minute (point-in-time, CLAUDE.md) — no lookahead. This is the
# honest test of the read: does it call the run late-but-right, stay quiet on pins, and exit well?
LEAN_MOVE_PTS = 25.0        # spot travel since 15:00 (half a strike) to start leaning RUN
LEAN_DELTA = 0.60           # realized ITM-side delta that says the option is committing
TRAIL_PTS = 30.0           # spot-excursion trailing stop once in the ride
FLAT_BY = "15:25"          # hard pre-settlement time-stop


def hero_verdict_at(s, K: float, now_min: float, cut: str = "13:45") -> dict:
    """The verdict from the series-so-far ``s`` (rows up to now only). Sign-free, point-in-time.

    Tiers, in the order the study says they become knowable: WATCH (no sustained break, or pre-3PM — we
    proved there's no reliable early signal) → LEAN <dir> (a persistence break AND, past 15:00, spot has
    travelled ≥LEAN_MOVE_PTS with realized delta ≥LEAN_DELTA — the option is committing) → RUN <dir>
    (also displaced ≥COMMIT_PTS now). FADE = broke but pulled back inside / delta weak."""
    if s is None or len(s) < 2:
        return {"state": "WATCH", "dir": None, "rdelta": None, "move3": None}
    brk = persistence_cross(s)
    disp_now = float(s["disp"].iloc[-1])
    at3 = s[s["time_ist"].str[:5] >= "15:00"]
    move3 = float(s["spot"].iloc[-1] - at3["spot"].iloc[0]) if len(at3) else None
    if not brk:
        return {"state": "WATCH", "dir": None, "rdelta": None, "move3": move3}
    d = brk["direction"]
    itm = "call" if d == "up" else "put"
    rd = realized_delta(s, itm, win_min=10)
    # The study says the signal only becomes knowable in the 15:00 window — so pre-3PM a break is just
    # WATCH+ (hold, no action), and only AFTER 15:00 do we read the 3PM move + delta to lean/confirm/fade.
    if move3 is None:                               # pre-15:00
        state = "WATCH+"
    else:
        signed_move3 = move3 if d == "up" else -move3     # travel in the break's direction since 15:00
        committed = abs(disp_now) >= COMMIT_PTS and rd is not None and rd >= LEAN_DELTA
        leaning = signed_move3 >= LEAN_MOVE_PTS and rd is not None and rd >= LEAN_DELTA
        if signed_move3 <= -LEAN_MOVE_PTS:          # broke then reversed hard past 3PM → failed break
            state = "FADE"
        elif committed:
            state = "RUN"
        elif leaning:
            state = "LEAN"
        else:
            state = "WATCH+"
    return {"state": state, "dir": d, "rdelta": None if rd is None else round(rd, 2),
            "move3": None if move3 is None else round(move3, 1)}


def replay_series(a, K: float, cut: str = "13:45") -> list[dict]:
    """Walk an anchor series ``a`` (cols: time_ist, mins, spot, disp, call_ltp, put_ltp) minute by minute,
    point-in-time, emitting one row per minute: {time_ist, spot, disp, state, dir, rdelta, move3, action,
    call_ltp, put_ltp, itm_ltp}. ``action`` narrates the discipline's trade: ENTER on first LEAN/RUN, then
    HOLD, then EXIT on the spot-trailing stop or the hard 15:25 settlement stop. The SAME engine backs
    both the stored-day replay and the live accumulation, so they can never drift. [] if too short."""
    if a is None or len(a) < 3:
        return []
    out = []
    in_trade = False
    entry_dir = None
    best = None
    exited = False
    for i in range(len(a)):
        s = a.iloc[: i + 1]
        r = a.iloc[i]
        v = hero_verdict_at(s, K, float(r["mins"]), cut)
        itm = "call" if (entry_dir or v["dir"]) == "up" else "put"
        action = "—"
        if not in_trade and not exited and v["state"] in ("LEAN", "RUN"):
            in_trade, entry_dir, best = True, v["dir"], float(r["disp"]); action = f"ENTER {v['dir']}"
        elif in_trade:
            best = max(best, r["disp"]) if entry_dir == "up" else min(best, r["disp"])
            give = (best - r["disp"]) if entry_dir == "up" else (r["disp"] - best)
            if r["time_ist"][:5] >= FLAT_BY:
                action, in_trade, exited = "EXIT settlement", False, True
            elif give >= TRAIL_PTS:
                action, in_trade, exited = "EXIT trail", False, True
            else:
                action = "HOLD"
        out.append({"time_ist": r["time_ist"], "spot": round(float(r["spot"]), 1),
                    "disp": round(float(r["disp"]), 1), "state": v["state"], "dir": v["dir"],
                    "rdelta": v["rdelta"], "move3": v["move3"], "action": action,
                    "call_ltp": round(float(r["call_ltp"]), 2), "put_ltp": round(float(r["put_ltp"]), 2),
                    "itm_ltp": round(float(r[f"{itm}_ltp"]), 2)})
    return out


def series_from_points(points: dict, K: float, cut: str = "13:45"):
    """Build the anchor-series frame the study consumes from a live-accumulated ``points`` dict
    {`HH:MM`: {spot, call_ltp, put_ltp, call_oi, put_oi}} (what the dashboard collects each rerun) — so
    the LIVE study lane reuses the exact replay engine. None until there are >=3 timed points."""
    import pandas as pd
    if not points or len(points) < 3:
        return None
    rows = []
    for t, v in sorted(points.items(), key=lambda kv: _mins(kv[0])):
        if v.get("spot") is None or v.get("call_ltp") is None or v.get("put_ltp") is None:
            continue
        rows.append({"time_ist": t, "spot": float(v["spot"]), "call_ltp": float(v["call_ltp"]),
                     "put_ltp": float(v["put_ltp"]), "call_oi": v.get("call_oi"), "put_oi": v.get("put_oi")})
    if len(rows) < 3:
        return None
    df = pd.DataFrame(rows)
    df["mins"] = df["time_ist"].map(_mins) - _mins(cut)
    df["disp"] = df["spot"] - K
    return df


def replay_day(symbol: str, day: str, cut: str = "13:45") -> list[dict] | None:
    """Minute-by-minute point-in-time replay of the improved hero for one STORED day. None if not
    captured; otherwise delegates the walk to replay_series (shared with the live lane)."""
    df = load_day(symbol, day)
    if df is None:
        return None
    K = anchor_strike(df, cut)
    a = anchor_series(df, K, cut) if K is not None else None
    return replay_series(a, K, cut) or None
