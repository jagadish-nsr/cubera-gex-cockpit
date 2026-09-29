"""Longitudinal WS-vs-REST gamma-blast comparison journal (P2 data).

The dashboard shows the live Δ between the tick-WS lane and the 10s-chain-REST lane; this persists one
row PER EXPIRY DAY (upserted with the latest reads) so the question "is the WS TRUE score systematically
earlier / different than the chain score?" can be answered over WEEKS, not a single day's impression.

Sign-free; decision-support; the archive is gitignored (under data/journal/). Not a signal.
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "data" / "journal" / "blast_compare"            # gitignored

COLS = ["day", "symbol", "k_ws", "k_rest",
        "ws_cross", "ws_score", "ws_grade", "ws_cross_time", "ws_cover", "ws_wall",
        "rest_cross", "rest_score", "rest_grade", "rest_cross_time", "rest_cover", "rest_wall",
        "d_score", "d_cross_s", "updated"]


def _secs(t):
    try:
        p = [int(x) for x in str(t).split(":")]
        while len(p) < 3:
            p.append(0)
        return p[0] * 3600 + p[1] * 60 + p[2]
    except (ValueError, AttributeError):
        return None


def _row(day: str, symbol: str, ws: dict, rest: dict, updated: str) -> dict:
    ws, rest = ws or {}, rest or {}
    d_score = d_cross = None
    if ws.get("cross") and rest.get("cross"):
        if ws.get("score") is not None and rest.get("score") is not None:
            d_score = int(ws["score"]) - int(rest["score"])          # + = WS scores higher
        tw, tr = _secs(ws.get("cross_time")), _secs(rest.get("cross_time"))
        if tw is not None and tr is not None:
            d_cross = tw - tr                                        # − = WS crossed earlier
    return {"day": day, "symbol": symbol,
            "k_ws": ws.get("strike"), "k_rest": rest.get("K"),
            "ws_cross": bool(ws.get("cross")), "ws_score": ws.get("score"), "ws_grade": ws.get("grade"),
            "ws_cross_time": ws.get("cross_time"), "ws_cover": ws.get("cover_frac"), "ws_wall": ws.get("wall_ratio"),
            "rest_cross": bool(rest.get("cross")), "rest_score": rest.get("score"), "rest_grade": rest.get("grade"),
            "rest_cross_time": rest.get("cross_time"), "rest_cover": rest.get("cover_frac"), "rest_wall": rest.get("wall_ratio"),
            "d_score": d_score, "d_cross_s": d_cross, "updated": updated}


def log(day: str, symbol: str, ws: dict, rest: dict, updated: str) -> None:
    """Upsert the (day, symbol) row with the latest reads — one row per expiry day, overwritten as the
    session develops (so the file ends the day holding each expiry's final WS-vs-REST comparison)."""
    DIR.mkdir(parents=True, exist_ok=True)
    p = DIR / f"{symbol}.csv"
    rows: dict[str, dict] = {}
    if p.exists():
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["day"]] = r
    rows[day] = _row(day, symbol, ws, rest, updated)            # upsert this day
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for d in sorted(rows):
            w.writerow(rows[d])


def load(symbol: str):
    """The comparison journal for a symbol (DataFrame), or None."""
    import pandas as pd
    p = DIR / f"{symbol}.csv"
    return pd.read_csv(p) if p.exists() else None
