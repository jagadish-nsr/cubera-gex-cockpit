"""Consume captured gamma-blast anchor-strike ticks (app/blast_ticks.py) → the real-premium crossover.

Parity at a fixed strike K near expiry: call − put = DF·(F − K) ≈ spot − K. So from the CAPTURED call &
put premiums alone we can reconstruct where spot crossed K — the crossover the blast fires on — at tick
resolution, without a separate spot feed. This module aligns the two async tick streams, derives the
implied spot (K + (call−put)), and flags crossings, plus premium/spread/OI summaries.

The point: validate the WS capture and give P1/P2 something concrete — a tick-timed cross to compare
against the old ~10s-spot cross. Sign-free (parity only, no dealer-sign); decision-support; not a signal.
"""
from __future__ import annotations


def _anchor_only(df):
    """Rows for the ANCHOR strike only. Uses the ``role`` column when present (walls are role wall_up/
    wall_dn); falls back for legacy pair-only captures (no role) to the strike that has BOTH CE and PE."""
    if "role" in df.columns and df["role"].notna().any():
        return df[df["role"] == "anchor"]
    both = [k for k, g in df.groupby("strike") if set(g["side"]) >= {"CE", "PE"}]
    return df[df["strike"].isin(both)]


def load_pair(symbol: str, day: str):
    """(call_df, put_df) for the ANCHOR strike, epoch-sorted, or (None, None). Wall rows are excluded."""
    from app.blast_ticks import load_blast_ticks
    df = load_blast_ticks(symbol, day)
    if df is None or getattr(df, "empty", True):
        return None, None
    a = _anchor_only(df.sort_values("epoch"))
    call = a[a["side"] == "CE"].reset_index(drop=True)
    put = a[a["side"] == "PE"].reset_index(drop=True)
    return (call if len(call) else None), (put if len(put) else None)


def load_walls(symbol: str, day: str):
    """(wall_up_call_df, wall_dn_put_df) — the continuation-wall contracts (CALL above / PUT below the
    anchor), epoch-sorted, or (None, None) if not captured (legacy pair-only day / grid edge)."""
    from app.blast_ticks import load_blast_ticks
    df = load_blast_ticks(symbol, day)
    if df is None or getattr(df, "empty", True) or "role" not in df.columns:
        return None, None
    df = df.sort_values("epoch")
    up = df[df["role"] == "wall_up"].reset_index(drop=True)
    dn = df[df["role"] == "wall_dn"].reset_index(drop=True)
    return (up if len(up) else None), (dn if len(dn) else None)


_FFILL = ("call_ltp", "call_bid", "call_ask", "call_oi", "call_vol",
          "put_ltp", "put_bid", "put_ask", "put_oi", "put_vol")


def align(call, put):
    """Merge the two async streams onto ONE timeline (union of tick epochs, each side forward-filled to
    its last known quote). Returns a frame: epoch, time_ist, call_ltp, put_ltp, call_bid/ask, put_bid/ask,
    call_oi, put_oi, call_vol, put_vol, diff (=call_ltp−put_ltp), implied_spot (=strike+diff). None until
    both sides have a tick."""
    import pandas as pd
    if call is None or put is None or call.empty or put.empty:
        return None
    strike = float(call["strike"].iloc[0])

    def _side(df, tag):
        cols = {"ltp": f"{tag}_ltp", "bid": f"{tag}_bid", "ask": f"{tag}_ask",
                "oi": f"{tag}_oi", "volume": f"{tag}_vol"}
        return df[["epoch", "time_ist", *cols]].rename(columns=cols)

    c, p = _side(call, "call"), _side(put, "put")
    g = pd.merge_ordered(c, p.drop(columns=["time_ist"]), on="epoch", how="outer").sort_values("epoch")
    for col in _FFILL:
        g[col] = g[col].ffill()
    g["time_ist"] = g["time_ist"].ffill().bfill()
    g = g.dropna(subset=["call_ltp", "put_ltp"]).reset_index(drop=True)   # both sides seen
    if g.empty:
        return None
    g["strike"] = strike
    g["diff"] = g["call_ltp"] - g["put_ltp"]               # ≈ spot − K
    g["implied_spot"] = strike + g["diff"]                 # parity-reconstructed spot
    return g


def _delta(g, col):
    """(first, last, Δ) of a forward-filled column over the frame, or (None, None, None)."""
    s = g[col].dropna()
    if not len(s):
        return None, None, None
    f, l = float(s.iloc[0]), float(s.iloc[-1])
    return f, l, l - f


def deltas(aligned) -> dict:
    """OI and cumulative-VOLUME change per side over the aligned window (Δvol needs the cumulative day
    volume the feed carries). {call_oi:[f,l,Δ], put_oi, call_vol, put_vol}. {} if empty."""
    if aligned is None or aligned.empty:
        return {}
    return {k: list(_delta(aligned, k)) for k in ("call_oi", "put_oi", "call_vol", "put_vol")}


def _crossed_snapshots(aligned, from_epoch=None):
    """The aligned rows (optionally from ``from_epoch`` onward) as the {call_oi, put_oi, call_ltp,
    put_ltp} snapshot list app.gammablast.blast_score consumes — so the TRUE score here is computed by
    the SAME canonical grader as the live dashboard (never drifts)."""
    g = aligned if from_epoch is None else aligned[aligned["epoch"] >= from_epoch]
    return [{"call_oi": r.call_oi, "put_oi": r.put_oi, "call_ltp": r.call_ltp, "put_ltp": r.put_ltp}
            for r in g.itertuples()]


def _wall_ahead(wall_df, side: str, from_epoch=None):
    """The continuation-wall contract's post-cross snapshots as the {side}_oi/{side}_ltp list blast_score's
    ``ahead`` consumes — so its OI (size + drift) feeds the path/cap component. None if no wall captured."""
    if wall_df is None or getattr(wall_df, "empty", True):
        return None
    g = wall_df if from_epoch is None else wall_df[wall_df["epoch"] >= from_epoch]
    if g.empty:
        return None
    return [{f"{side}_oi": r.oi, f"{side}_ltp": r.ltp} for r in g.itertuples()]


def true_score(aligned, direction: str, from_epoch=None, wall_up=None, wall_dn=None) -> dict | None:
    """TRUE-vs-Fake blast score over the (post-cross) window, via app.gammablast.blast_score — reuses the
    exact ITM-writers-covering (ignition) + premium-expansion (thrust) + wall-ahead (path) grading. The
    wall is the CALL above (up-cross) or PUT below (down-cross); if not captured, path assumes a clear road
    and the score leans on ignition+thrust. Also attaches the ITM-side ΔOI/Δvol."""
    from app.gammablast import blast_score
    if aligned is None or aligned.empty or direction not in ("up", "down"):
        return None
    side = "call" if direction == "up" else "put"
    ahead = _wall_ahead(wall_up if direction == "up" else wall_dn, side, from_epoch)
    sc = blast_score(direction, _crossed_snapshots(aligned, from_epoch), ahead=ahead)
    if sc is None:
        return None
    win = aligned if from_epoch is None else aligned[aligned["epoch"] >= from_epoch]
    itm = sc["itm_side"]                                   # 'call' | 'put'
    sc["itm_doi"] = _delta(win, f"{itm}_oi")[2]            # ITM-side OI change over the window (fuel)
    sc["itm_dvol"] = _delta(win, f"{itm}_vol")[2]         # ITM-side volume traded over the window
    sc["has_wall"] = ahead is not None
    return sc


def graded_crossovers(aligned, wall_up=None, wall_dn=None) -> list[dict]:
    """Each reconstructed crossover with its TRUE score, graded over the slice from that cross to the end
    of capture (staying-power window), using the direction-matched wall when available. Empty if none."""
    out = []
    for x in crossovers(aligned):
        sc = true_score(aligned, x["direction"], from_epoch=x["epoch"],
                        wall_up=wall_up, wall_dn=wall_dn) or {}
        out.append({**x, "score": sc.get("score"), "grade": sc.get("grade"),
                    "itm_side": sc.get("itm_side"), "cover_frac": sc.get("cover_frac"),
                    "prem_exp": sc.get("prem_exp"), "wall_ratio": sc.get("wall_ratio"),
                    "wall_frac": sc.get("wall_frac"), "has_wall": sc.get("has_wall"),
                    "itm_doi": sc.get("itm_doi"), "itm_dvol": sc.get("itm_dvol")})
    return out


def crossovers(aligned, tol: float = 0.0):
    """Crossings of diff through 0 (= spot crossing the anchor strike K). Returns a list of
    {time_ist, epoch, direction, implied_spot}: 'up' = call became richer (spot moved above K), 'down'
    = put became richer. ``tol`` ignores flips inside ±tol premium points (noise guard)."""
    if aligned is None or aligned.empty:
        return []
    out = []
    prev = None
    for _, r in aligned.iterrows():
        d = float(r["diff"])
        sign = 1 if d > tol else -1 if d < -tol else 0
        if sign != 0 and prev is not None and sign != prev:
            out.append({"time_ist": r["time_ist"], "epoch": int(r["epoch"]),
                        "direction": "up" if sign > 0 else "down",
                        "implied_spot": round(float(r["implied_spot"]), 1)})
        if sign != 0:
            prev = sign
    return out


def summary(symbol: str, day: str) -> dict | None:
    """One-shot read of a captured day: strike/expiry, per-side tick counts, premium ranges, median
    spread, OI + cumulative-VOLUME deltas per side, the reconstructed crossovers WITH their TRUE score,
    and — if there's a cross — the day's headline blast grade. None if nothing captured."""
    call, put = load_pair(symbol, day)
    if call is None or put is None:
        return None
    g = align(call, put)
    if g is None:
        return None
    strike = float(call["strike"].iloc[0])

    def _med_spread(df):
        s = (df["ask"] - df["bid"]).dropna()
        s = s[s > 0]
        return round(float(s.median()), 2) if len(s) else None

    dl = deltas(g)
    wall_up, wall_dn = load_walls(symbol, day)
    gx = graded_crossovers(g, wall_up=wall_up, wall_dn=wall_dn)
    out = {
        "symbol": symbol, "day": day, "strike": strike, "expiry": str(call["expiry"].iloc[0]),
        "n_call": int(len(call)), "n_put": int(len(put)),
        "span": [str(g["time_ist"].iloc[0]), str(g["time_ist"].iloc[-1])],
        "call_ltp_range": [round(float(call["ltp"].min()), 2), round(float(call["ltp"].max()), 2)],
        "put_ltp_range": [round(float(put["ltp"].min()), 2), round(float(put["ltp"].max()), 2)],
        "call_spread_med": _med_spread(call), "put_spread_med": _med_spread(put),
        "call_oi": dl.get("call_oi", [None, None, None])[:2],   # (first, last) — back-compat shape
        "put_oi": dl.get("put_oi", [None, None, None])[:2],
        "deltas": dl,                                            # {call_oi/put_oi/call_vol/put_vol: [f,l,Δ]}
        "implied_spot_range": [round(float(g["implied_spot"].min()), 1),
                               round(float(g["implied_spot"].max()), 1)],
        "walls": {"up": (float(wall_up["strike"].iloc[0]) if wall_up is not None else None),
                  "dn": (float(wall_dn["strike"].iloc[0]) if wall_dn is not None else None)},
        "n_crossovers": len(gx), "crossovers": gx}
    if gx:                                                       # headline = the LAST cross's grade
        last = gx[-1]
        out["blast_grade"] = last.get("grade")
        out["blast_score"] = last.get("score")
    return out


def hero_read(symbol: str, day: str) -> dict | None:
    """Distil the captured WS day into the fields the dashboard's WS blast panel + the REST-vs-WS delta
    need: anchor/expiry, tick counts, and the LATEST crossover's score/grade/components/timing. ``cross``
    is False (no cross yet) but the panel still shows the tick lane is live. None if nothing captured."""
    s = summary(symbol, day)
    if not s:
        return None
    xs = s.get("crossovers") or []
    last = xs[-1] if xs else {}
    return {
        "symbol": symbol, "day": day, "strike": s["strike"], "expiry": s["expiry"],
        "n_call": s["n_call"], "n_put": s["n_put"], "span": s["span"], "walls": s["walls"],
        "cross": bool(xs), "cross_time": last.get("time_ist"), "direction": last.get("direction"),
        "score": last.get("score"), "grade": last.get("grade"),
        "cover_frac": last.get("cover_frac"), "wall_ratio": last.get("wall_ratio"),
        "has_wall": last.get("has_wall"), "implied_spot": last.get("implied_spot")}
