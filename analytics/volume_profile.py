"""Volume-at-price profile from 1-min futures OHLC + volume.

Distinct from TPO (time-at-price): each bar's volume is spread evenly across the
price bins it traded. VPOC is the densest bin. HVNs are secondary local peaks;
LVNs are valleys between peaks. Map only — not a direction call.

Point-in-time: pass bars up to clock ``t`` for a same-session profile. A full-day
profile is only known at the close.
"""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np

# Match TPO bin geometry so VP and TPO can be compared on the same grid.
BIN_PTS = {"NIFTY": 5.0, "NIFTY_M": 5.0, "BANKNIFTY": 20.0, "SENSEX": 20.0}


def bin_pts_for(symbol: str | None, strike_step: float | None = None) -> float:
    from config import index_key

    key = index_key(symbol)
    if key in BIN_PTS:
        return float(BIN_PTS[key])
    step = float(strike_step or 50.0)
    return max(5.0, step / 10.0)


def _rows(bars: Iterable[Any]) -> list[dict]:
    out = []
    if bars is None:
        return out
    # Accept DataFrame / Series without boolean truth checks.
    if hasattr(bars, "to_dict") and hasattr(bars, "columns"):
        try:
            iterable = bars.to_dict("records")
        except Exception:
            iterable = []
    else:
        iterable = bars
    for raw in iterable:
        if hasattr(raw, "to_dict") and not isinstance(raw, dict):
            try:
                row = raw.to_dict()
            except Exception:
                continue
        elif isinstance(raw, dict):
            row = raw
        else:
            continue
        try:
            high = float(row.get("high"))
            low = float(row.get("low"))
            vol = float(row.get("volume") or 0.0)
        except (TypeError, ValueError):
            continue
        if high <= 0 or low <= 0 or high < low or vol <= 0:
            continue
        close = row.get("close")
        try:
            close_f = float(close) if close is not None else high
        except (TypeError, ValueError):
            close_f = high
        out.append({"high": high, "low": low, "close": close_f, "volume": vol})
    return out


def volume_histogram(
    bars: Iterable[Any],
    bin_pts: float = 5.0,
    va_frac: float = 0.70,
) -> dict | None:
    """Build a volume-at-price histogram from OHLC bars.

    Volume in each bar is split evenly across every bin the bar's [low, high]
    range touches. Degenerate bars (high == low) put all volume in one bin.
    """
    rows = _rows(bars)
    if len(rows) < 20:
        return None
    bp = float(bin_pts)
    if bp <= 0:
        return None

    session_lo = min(r["low"] for r in rows)
    session_hi = max(r["high"] for r in rows)
    if session_hi <= session_lo:
        return None

    b0 = np.floor(session_lo / bp) * bp
    nb = int((session_hi - b0) // bp) + 1
    if nb < 3:
        return None
    volumes = np.zeros(nb, dtype=float)

    for r in rows:
        i0 = int(max(0, min(nb - 1, (r["low"] - b0) // bp)))
        i1 = int(max(0, min(nb - 1, (r["high"] - b0) // bp)))
        if i1 < i0:
            i0, i1 = i1, i0
        n_bins = i1 - i0 + 1
        share = r["volume"] / float(n_bins)
        volumes[i0 : i1 + 1] += share

    total = float(volumes.sum())
    if total <= 0:
        return None

    centers = b0 + (np.arange(nb) + 0.5) * bp
    vpoc_i = int(volumes.argmax())
    vpoc = float(centers[vpoc_i])

    # Volume value area: expand from VPOC until va_frac of total volume.
    li = ri = vpoc_i
    acc = float(volumes[vpoc_i])
    while acc < va_frac * total and (li > 0 or ri < nb - 1):
        up = float(volumes[ri + 1]) if ri < nb - 1 else -1.0
        dn = float(volumes[li - 1]) if li > 0 else -1.0
        if up >= dn:
            ri += 1
            acc += max(up, 0.0)
        else:
            li -= 1
            acc += max(dn, 0.0)

    return {
        "vpoc": round(vpoc, 1),
        "vah": round(float(b0 + (ri + 1) * bp), 1),
        "val": round(float(b0 + li * bp), 1),
        "hi": round(float(session_hi), 1),
        "lo": round(float(session_lo), 1),
        "total_volume": round(total, 1),
        "n_bars": len(rows),
        "bin_pts": bp,
        "centers": centers,
        "volumes": volumes,
        "vpoc_i": vpoc_i,
        "val_i": li,
        "vah_i": ri,
    }


def detect_nodes(
    hist: dict | None,
    *,
    hvn_frac: float = 0.55,
    lvn_frac: float = 0.30,
    max_hvn: int = 3,
    max_lvn: int = 2,
    smooth: int = 1,
) -> dict:
    """HVN / LVN nodes from a volume histogram. VPOC is always listed separately."""
    empty = {"vpoc": None, "hvns": [], "lvns": [], "vah": None, "val": None}
    if not hist:
        return empty

    centers = np.asarray(hist["centers"], dtype=float)
    volumes = np.asarray(hist["volumes"], dtype=float)
    if len(centers) < 5 or float(volumes.max()) <= 0:
        return {
            "vpoc": hist.get("vpoc"),
            "hvns": [],
            "lvns": [],
            "vah": hist.get("vah"),
            "val": hist.get("val"),
        }

    if smooth > 0:
        k = np.ones(2 * smooth + 1) / (2 * smooth + 1)
        sm = np.convolve(volumes, k, mode="same")
    else:
        sm = volumes

    peak = float(sm.max())
    vpoc_i = int(hist.get("vpoc_i", int(sm.argmax())))
    hvn_floor = hvn_frac * peak
    lvn_ceil = lvn_frac * peak

    hvn_idx = []
    for i in range(1, len(sm) - 1):
        if sm[i] < hvn_floor:
            continue
        if sm[i] > sm[i - 1] and sm[i] >= sm[i + 1]:
            if i == vpoc_i:
                continue
            hvn_idx.append(i)
    hvn_idx.sort(key=lambda i: float(sm[i]), reverse=True)
    hvn_idx = hvn_idx[: max(0, int(max_hvn))]

    # LVNs: local minima below the LVN ceiling, preferring valleys between HVN/VPOC peaks.
    peak_set = sorted({vpoc_i, *hvn_idx})
    lvn_idx = []
    for i in range(1, len(sm) - 1):
        if sm[i] > lvn_ceil:
            continue
        if sm[i] < sm[i - 1] and sm[i] <= sm[i + 1]:
            lvn_idx.append(i)
    # Prefer valleys that sit between two peaks.
    def _between_peaks(i: int) -> bool:
        left = [p for p in peak_set if p < i]
        right = [p for p in peak_set if p > i]
        return bool(left and right)

    lvn_idx.sort(key=lambda i: (0 if _between_peaks(i) else 1, float(sm[i])))
    lvn_idx = lvn_idx[: max(0, int(max_lvn))]

    def _node(i: int, kind: str) -> dict:
        return {
            "kind": kind,
            "price": round(float(centers[i]), 1),
            "volume": round(float(volumes[i]), 1),
            "share": round(float(volumes[i]) / float(volumes.sum()), 4),
        }

    return {
        "vpoc": hist.get("vpoc"),
        "vah": hist.get("vah"),
        "val": hist.get("val"),
        "hvns": [_node(i, "hvn") for i in hvn_idx],
        "lvns": [_node(i, "lvn") for i in lvn_idx],
        "total_volume": hist.get("total_volume"),
        "n_bars": hist.get("n_bars"),
        "bin_pts": hist.get("bin_pts"),
        "hi": hist.get("hi"),
        "lo": hist.get("lo"),
    }


def profile_from_bars(
    bars: Iterable[Any],
    *,
    symbol: str | None = None,
    bin_pts: float | None = None,
    strike_step: float | None = None,
    day: str | None = None,
) -> dict | None:
    """Convenience: histogram + nodes in one payload for the structural map."""
    bp = float(bin_pts) if bin_pts is not None else bin_pts_for(symbol, strike_step)
    hist = volume_histogram(bars, bin_pts=bp)
    if not hist:
        return None
    nodes = detect_nodes(hist)
    out = {
        "day": day,
        "bin_pts": bp,
        "vpoc": nodes["vpoc"],
        "vah": nodes["vah"],
        "val": nodes["val"],
        "hvns": nodes["hvns"],
        "lvns": nodes["lvns"],
        "total_volume": nodes.get("total_volume"),
        "n_bars": nodes.get("n_bars"),
        "hi": nodes.get("hi"),
        "lo": nodes.get("lo"),
        "source": "futures_volume",
    }
    return out
