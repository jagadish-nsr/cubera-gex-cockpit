"""Point-in-time forward outcomes from a first-print blast score minute grid.

Decision-support research only. Spot and option paths are locations / premiums —
not a CE/PE direction model. Uses the collapsed minute grid so polluted
multi-writer score CSVs cannot invent EXTRA ACTIVE minutes.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


def _mins(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        return None


def _hhmm(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def load_minute_grid(path: Path) -> list[dict]:
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def active_episodes(rows: Iterable[dict]) -> list[dict]:
    """Collapse consecutive TRIGGERED first-prints into episodes.

    Each episode: start/end time_ist, start score/spot/primary_strike/badge.
    """
    ordered = sorted(
        (r for r in rows if (r.get("time_ist") or "")),
        key=lambda r: r["time_ist"],
    )
    episodes: list[dict] = []
    cur = None
    for row in ordered:
        active = (row.get("state") or "") == "TRIGGERED"
        if active and cur is None:
            cur = {
                "start": row["time_ist"],
                "end": row["time_ist"],
                "score": row.get("score"),
                "spot": row.get("spot"),
                "primary_strike": row.get("primary_strike"),
                "badge": row.get("badge"),
                "call_wall": row.get("call_wall"),
                "put_wall": row.get("put_wall"),
                "n_minutes": 1,
            }
        elif active and cur is not None:
            cur["end"] = row["time_ist"]
            cur["n_minutes"] += 1
        elif (not active) and cur is not None:
            episodes.append(cur)
            cur = None
    if cur is not None:
        episodes.append(cur)
    return episodes


def spot_forward(
    by_minute: dict[str, dict],
    start_hhmm: str,
    horizons_min: tuple[int, ...] = (5, 15, 30),
) -> dict:
    """Signed spot change from start; MFE/MAE vs start over each horizon."""
    t0 = _mins(start_hhmm)
    if t0 is None or start_hhmm not in by_minute:
        return {}
    try:
        s0 = float(by_minute[start_hhmm]["spot"])
    except (TypeError, ValueError, KeyError):
        return {}
    out: dict = {"spot0": s0}
    for h in horizons_min:
        path = []
        for m in range(0, h + 1):
            key = _hhmm(t0 + m)
            row = by_minute.get(key)
            if not row:
                continue
            try:
                path.append(float(row["spot"]))
            except (TypeError, ValueError):
                continue
        if len(path) < 2:
            out[f"d{h}"] = None
            out[f"mfe{h}"] = None
            out[f"mae{h}"] = None
            continue
        deltas = [s - s0 for s in path]
        out[f"d{h}"] = round(deltas[-1], 2)
        out[f"mfe{h}"] = round(max(deltas), 2)
        out[f"mae{h}"] = round(min(deltas), 2)
    # Side of primary strike at entry (location, not a forecast).
    try:
        k = float(by_minute[start_hhmm].get("primary_strike") or 0)
    except (TypeError, ValueError):
        k = 0.0
    out["vs_primary"] = (
        "above" if k and s0 >= k - 1e-6 else ("below" if k else "")
    )
    return out


def option_forward_from_ticks(
    tick_rows: list[dict],
    *,
    strike: float,
    start_hhmm: str,
    horizon_min: int = 15,
) -> dict:
    """CE/PE LTP at strike near start vs +horizon (blast_ticks journal)."""
    t0 = _mins(start_hhmm)
    if t0 is None or strike is None:
        return {}
    t1 = t0 + horizon_min

    def _near(side: str, target_min: int):
        best = None
        best_dist = 10**9
        for row in tick_rows:
            try:
                if abs(float(row.get("strike") or 0) - float(strike)) > 1e-6:
                    continue
            except (TypeError, ValueError):
                continue
            if (row.get("side") or "").upper() != side:
                continue
            tm = _mins((row.get("time_ist") or "")[:5])
            if tm is None:
                continue
            dist = abs(tm - target_min)
            if dist < best_dist:
                try:
                    ltp = float(row.get("ltp") or 0)
                except (TypeError, ValueError):
                    continue
                if ltp <= 0:
                    continue
                best = ltp
                best_dist = dist
        return best if best_dist <= 2 else None  # within 2 minutes

    ce0, pe0 = _near("CE", t0), _near("PE", t0)
    ce1, pe1 = _near("CE", t1), _near("PE", t1)
    out = {"ce0": ce0, "pe0": pe0, "ce1": ce1, "pe1": pe1}
    if ce0 and ce1:
        out["ce_ret"] = round(ce1 / ce0 - 1.0, 4)
    if pe0 and pe1:
        out["pe_ret"] = round(pe1 / pe0 - 1.0, 4)
    return out


def join_active_forward(
    minute_rows: list[dict],
    tick_rows: list[dict] | None = None,
    horizons_min: tuple[int, ...] = (5, 15, 30),
) -> list[dict]:
    """One research row per ACTIVE episode with spot (and optional option) forwards."""
    by_minute = {r["time_ist"]: r for r in minute_rows if r.get("time_ist")}
    out = []
    for ep in active_episodes(minute_rows):
        row = {
            "start": ep["start"],
            "end": ep["end"],
            "n_minutes": ep["n_minutes"],
            "score": ep.get("score"),
            "badge": ep.get("badge"),
            "primary_strike": ep.get("primary_strike"),
            **spot_forward(by_minute, ep["start"], horizons_min),
        }
        try:
            strike = float(ep.get("primary_strike") or 0)
        except (TypeError, ValueError):
            strike = 0.0
        if tick_rows and strike > 0:
            row.update(
                option_forward_from_ticks(
                    tick_rows, strike=strike, start_hhmm=ep["start"], horizon_min=15,
                )
            )
        out.append(row)
    return out
