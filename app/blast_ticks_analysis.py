"""Consume captured gamma-blast anchor-strike ticks (app/blast_ticks.py) → the real-premium crossover.

Parity at a fixed strike K near expiry: call − put = DF·(F − K) ≈ spot − K. So from the CAPTURED call &
put premiums alone we can reconstruct where spot crossed K — the crossover the blast fires on — at tick
resolution, without a separate spot feed. This module aligns the two async tick streams, derives the
implied spot (K + (call−put)), and flags crossings, plus premium/spread/OI summaries.

The point: validate the WS capture and give P1/P2 something concrete — a tick-timed cross to compare
against the old ~10s-spot cross. Sign-free (parity only, no dealer-sign); decision-support; not a signal.
"""
from __future__ import annotations


def load_pair(symbol: str, day: str):
    """(call_df, put_df) captured tick frames for a symbol/day, epoch-sorted, or (None, None)."""
    from app.blast_ticks import load_blast_ticks
    df = load_blast_ticks(symbol, day)
    if df is None or getattr(df, "empty", True):
        return None, None
    df = df.sort_values("epoch")
    call = df[df["side"] == "CE"].reset_index(drop=True)
    put = df[df["side"] == "PE"].reset_index(drop=True)
    return (call if len(call) else None), (put if len(put) else None)


def align(call, put):
    """Merge the two async streams onto ONE timeline (union of tick epochs, each side forward-filled to
    its last known quote). Returns a frame: epoch, time_ist, call_ltp, put_ltp, call_bid/ask, put_bid/ask,
    call_oi, put_oi, diff (=call_ltp−put_ltp), implied_spot (=strike+diff). None until both sides have a tick."""
    import pandas as pd
    if call is None or put is None or call.empty or put.empty:
        return None
    strike = float(call["strike"].iloc[0])

    def _side(df, tag):
        cols = {"ltp": f"{tag}_ltp", "bid": f"{tag}_bid", "ask": f"{tag}_ask", "oi": f"{tag}_oi"}
        return df[["epoch", "time_ist", *cols]].rename(columns=cols)

    c, p = _side(call, "call"), _side(put, "put")
    g = pd.merge_ordered(c, p.drop(columns=["time_ist"]), on="epoch", how="outer").sort_values("epoch")
    for col in ("call_ltp", "call_bid", "call_ask", "call_oi", "put_ltp", "put_bid", "put_ask", "put_oi"):
        g[col] = g[col].ffill()
    g["time_ist"] = g["time_ist"].ffill().bfill()
    g = g.dropna(subset=["call_ltp", "put_ltp"]).reset_index(drop=True)   # both sides seen
    if g.empty:
        return None
    g["strike"] = strike
    g["diff"] = g["call_ltp"] - g["put_ltp"]               # ≈ spot − K
    g["implied_spot"] = strike + g["diff"]                 # parity-reconstructed spot
    return g


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
    spread, OI open→close, and the reconstructed crossovers. None if nothing captured."""
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

    def _oi(df):
        o = df["oi"].dropna()
        return (float(o.iloc[0]), float(o.iloc[-1])) if len(o) else (None, None)

    xs = crossovers(g)
    return {
        "symbol": symbol, "day": day, "strike": strike, "expiry": str(call["expiry"].iloc[0]),
        "n_call": int(len(call)), "n_put": int(len(put)),
        "span": [str(g["time_ist"].iloc[0]), str(g["time_ist"].iloc[-1])],
        "call_ltp_range": [round(float(call["ltp"].min()), 2), round(float(call["ltp"].max()), 2)],
        "put_ltp_range": [round(float(put["ltp"].min()), 2), round(float(put["ltp"].max()), 2)],
        "call_spread_med": _med_spread(call), "put_spread_med": _med_spread(put),
        "call_oi": _oi(call), "put_oi": _oi(put),
        "implied_spot_range": [round(float(g["implied_spot"].min()), 1),
                               round(float(g["implied_spot"].max()), 1)],
        "n_crossovers": len(xs), "crossovers": xs}
