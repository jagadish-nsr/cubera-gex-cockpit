"""Structural reaction map — price levels already known to the cockpit.

Aggregates GEX concentrations, prior-session marks, day range, prior TPO value
area, futures volume-profile nodes (VPOC/HVN/LVN), and rule-based supply/demand
boxes into one reaction-candidate list. Overlaps are clusters within a
strike-step tolerance. This is a map, not a forecast that price will fade or
break a level.
"""

from __future__ import annotations

from typing import Any


def _f(raw: Any) -> float | None:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val <= 0 or val != val:  # noqa: PLR0124 — NaN check
        return None
    return val


def _point(
    level_id: str,
    label: str,
    kind: str,
    price: float | None,
    *,
    source: str,
    date: str | None = None,
) -> dict | None:
    if price is None:
        return None
    out = {
        "id": level_id,
        "label": label,
        "kind": kind,
        "price": round(price, 1),
        "lo": None,
        "hi": None,
        "source": source,
    }
    if date:
        out["date"] = date
    return out


def _band(
    level_id: str,
    label: str,
    kind: str,
    lo: float | None,
    hi: float | None,
    *,
    source: str,
    poc: float | None = None,
    inside: bool | None = None,
    date: str | None = None,
) -> dict | None:
    if lo is None or hi is None or hi < lo:
        return None
    out = {
        "id": level_id,
        "label": label,
        "kind": kind,
        "price": round(poc if poc is not None else (lo + hi) / 2.0, 1),
        "lo": round(lo, 1),
        "hi": round(hi, 1),
        "source": source,
    }
    if poc is not None:
        out["poc"] = round(poc, 1)
    if inside is not None:
        out["inside"] = bool(inside)
    if date:
        out["date"] = date
    return out


def _anchor(level: dict) -> float:
    if level.get("lo") is not None and level.get("hi") is not None:
        return float(level["price"])
    return float(level["price"])


def _near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _level_near(level: dict, price: float, tol: float) -> bool:
    lo = level.get("lo")
    hi = level.get("hi")
    if lo is not None and hi is not None:
        return (float(lo) - tol) <= price <= (float(hi) + tol)
    return _near(float(level["price"]), price, tol)


def _member_prices(level: dict) -> list[float]:
    """Anchor only — a wide value-area band must not inflate overlap diameter."""
    return [float(level["price"])]


def _pair_near(left: dict, right: dict, tol: float) -> bool:
    """True when two levels share a price neighbourhood within ``tol``."""
    left_anchor = _anchor(left)
    right_anchor = _anchor(right)
    if _near(left_anchor, right_anchor, tol):
        return True
    # Point inside / near a band edge (not the whole band diameter).
    if left.get("lo") is not None and left.get("hi") is not None:
        if _level_near(left, right_anchor, tol):
            return True
    if right.get("lo") is not None and right.get("hi") is not None:
        if _level_near(right, left_anchor, tol):
            return True
    return False


def _find_overlaps(levels: list[dict], tol: float) -> list[dict]:
    """Cluster levels that sit in the same neighbourhood.

    Pairwise proximity uses ``tol``. Components are kept only when their
    price diameter is ≤ ``tol``, so a chain of steps does not merge the
    whole ladder into one fake overlap.
    """
    if len(levels) < 2:
        return []

    max_span = float(tol)
    parent = {lv["id"]: lv["id"] for lv in levels}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def component_of(root: str) -> list[dict]:
        return [lv for lv in levels if find(lv["id"]) == root]

    def diameter(members: list[dict]) -> float:
        prices = [p for m in members for p in _member_prices(m)]
        return max(prices) - min(prices) if prices else 0.0

    by_id = {lv["id"]: lv for lv in levels}
    ids = list(by_id)
    for i, left_id in enumerate(ids):
        left = by_id[left_id]
        for right_id in ids[i + 1 :]:
            right = by_id[right_id]
            if not _pair_near(left, right, tol):
                continue
            ra, rb = find(left_id), find(right_id)
            if ra == rb:
                continue
            merged = component_of(ra) + component_of(rb)
            if diameter(merged) <= max_span:
                parent[rb] = ra

    clusters: dict[str, list[dict]] = {}
    for lv in levels:
        clusters.setdefault(find(lv["id"]), []).append(lv)

    overlaps = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        prices = [p for m in members for p in _member_prices(m)]
        lo, hi = min(prices), max(prices)
        # Prefer GEX / session labels first for a readable chip title.
        kind_rank = {"gex": 0, "session": 1, "tpo": 2, "vp": 3, "sd": 4, "oi": 5}
        members_sorted = sorted(
            members,
            key=lambda m: (kind_rank.get(m.get("kind"), 9), m["label"]),
        )
        overlaps.append({
            "label": " · ".join(m["label"] for m in members_sorted),
            "lo": round(lo, 1),
            "hi": round(hi, 1),
            "mid": round((lo + hi) / 2.0, 1),
            "members": [m["id"] for m in members_sorted],
        })
    overlaps.sort(key=lambda row: row["mid"], reverse=True)
    return overlaps


def _chip_for(level: dict, spot: float, overlap_ids: set[str]) -> dict:
    dist = round(float(level["price"]) - float(spot), 1)
    sign = "+" if dist >= 0 else ""
    if level.get("lo") is not None and level.get("hi") is not None:
        display = f"{level['lo']:,.0f}–{level['hi']:,.0f}"
        if level.get("poc") is not None:
            display = f"POC {level['poc']:,.0f} · {display}"
    else:
        display = f"{level['price']:,.0f}"
    chip = {
        "id": level["id"],
        "tag": level["label"],
        "display": display,
        "price": level["price"],
        "lo": level.get("lo"),
        "hi": level.get("hi"),
        "source": level["source"],
        "kind": level["kind"],
        "overlap": level["id"] in overlap_ids,
        "dist_pts": dist,
        "dist_text": f"{sign}{dist:,.0f} pts",
    }
    if level.get("side"):
        chip["side"] = level["side"]
    if level.get("impulse_pts") is not None:
        chip["impulse_pts"] = level["impulse_pts"]
    return chip


def _summary(
    spot: float,
    levels: list[dict],
    overlaps: list[dict],
    value_area: dict | None,
    *,
    n_vp: int = 0,
    n_sd: int = 0,
) -> str:
    bits = []
    if value_area and value_area.get("inside") is True:
        bits.append("Spot inside prior value area")
    elif value_area and value_area.get("inside") is False:
        bits.append("Spot outside prior value area")
    if n_vp:
        bits.append(f"{n_vp} volume-profile node{'s' if n_vp != 1 else ''}")
    if n_sd:
        bits.append(f"{n_sd} supply/demand box{'es' if n_sd != 1 else ''}")
    if overlaps:
        bits.append(f"{len(overlaps)} overlap{'s' if len(overlaps) != 1 else ''} near concentration")
    else:
        bits.append("No multi-source overlaps within one strike step")
    above = sum(1 for lv in levels if float(lv["price"]) > float(spot))
    below = sum(1 for lv in levels if float(lv["price"]) < float(spot))
    bits.append(f"{above} above / {below} below spot")
    return " · ".join(bits) + ". Reaction candidates only."


def _vp_levels(volume_profile: dict | None, session_vpoc: dict | None) -> list[dict]:
    levels: list[dict] = []
    vp = volume_profile or {}
    vp_day = vp.get("day")
    vpoc = _f(vp.get("vpoc"))
    if vpoc is not None:
        levels.append(_point("vpoc", "VPOC", "vp", vpoc, source="VP", date=vp_day))
    for i, node in enumerate(vp.get("hvns") or []):
        levels.append(
            _point(f"hvn_{i}", f"HVN {i + 1}", "vp", _f(node.get("price")), source="VP", date=vp_day)
        )
    for i, node in enumerate(vp.get("lvns") or []):
        levels.append(
            _point(f"lvn_{i}", f"LVN {i + 1}", "vp", _f(node.get("price")), source="VP", date=vp_day)
        )
    vah, val = _f(vp.get("vah")), _f(vp.get("val"))
    if vah is not None and val is not None:
        levels.append(
            _band(
                "vp_value_area",
                "VP value area",
                "vp",
                val,
                vah,
                source="VP",
                poc=vpoc,
                date=vp_day,
            )
        )
    sess = session_vpoc or {}
    levels.append(
        _point(
            "session_vpoc",
            "Session VPOC",
            "vp",
            _f(sess.get("price")),
            source="VP·today",
            date=sess.get("day"),
        )
    )
    return [lv for lv in levels if lv is not None]


def _sd_levels(supply_demand: list[dict] | None) -> list[dict]:
    levels: list[dict] = []
    counters = {"supply": 0, "demand": 0}
    for zone in supply_demand or []:
        side = str(zone.get("side") or "").lower()
        if side not in ("supply", "demand"):
            continue
        label = "Supply" if side == "supply" else "Demand"
        idx = counters[side]
        counters[side] = idx + 1
        item = _band(
            f"{side}_{idx}",
            f"{label} zone",
            "sd",
            _f(zone.get("lo")),
            _f(zone.get("hi")),
            source="S/D",
            date=zone.get("day"),
        )
        if item is None:
            continue
        item["side"] = side
        item["impulse_pts"] = zone.get("impulse_pts")
        item["strength"] = zone.get("strength")
        item["rule"] = zone.get("rule")
        item["mitigated"] = bool(zone.get("mitigated"))
        levels.append(item)
    return levels


def build_structural_map(
    spot: float,
    *,
    call_wall: float | None = None,
    put_wall: float | None = None,
    gamma_flip: float | None = None,
    magnet: float | None = None,
    max_pain: float | None = None,
    prior_marks: dict | None = None,
    day_ohlc: dict | None = None,
    value_area: dict | None = None,
    standing_oi: dict | None = None,
    volume_profile: dict | None = None,
    session_vpoc: dict | None = None,
    supply_demand: list[dict] | None = None,
    strike_step: float = 50.0,
    overlap_tol: float | None = None,
) -> dict:
    """Build the structural reaction map from already-computed cockpit levels."""
    spot_f = _f(spot)
    if spot_f is None:
        return {
            "spot": None,
            "levels": [],
            "overlaps": [],
            "chips": [],
            "summary": "No spot for a structural map.",
            "overlap_tol": float(overlap_tol or strike_step),
            "volume_profile": None,
            "supply_demand": [],
        }

    tol = float(overlap_tol if overlap_tol is not None else max(float(strike_step), 1.0))
    prior = prior_marks or {}
    day = day_ohlc or {}
    va = value_area or {}
    oi = standing_oi or {}
    prior_day = prior.get("date")
    va_day = va.get("date")

    levels: list[dict] = []
    for item in (
        _point("call_wall", "Call wall", "gex", _f(call_wall), source="GEX"),
        _point("put_wall", "Put wall", "gex", _f(put_wall), source="GEX"),
        _point("gamma_flip", "Call−put balance", "gex", _f(gamma_flip), source="GEX"),
        _point("magnet", "Magnet", "gex", _f(magnet), source="GEX"),
        _point("max_pain", "Max pain", "gex", _f(max_pain), source="GEX"),
        _point("prior_high", "Prior high", "session", _f(prior.get("high")), source="PDH", date=prior_day),
        _point("prior_low", "Prior low", "session", _f(prior.get("low")), source="PDL", date=prior_day),
        _point("prior_close", "Prior close", "session", _f(prior.get("close")), source="PDC", date=prior_day),
        _point("day_high", "Day high", "session", _f(day.get("high")), source="Session"),
        _point("day_low", "Day low", "session", _f(day.get("low")), source="Session"),
        _point("day_open", "Day open", "session", _f(day.get("open")), source="Session"),
        _point("put_oi", "Put OI centre", "oi", _f(oi.get("put_centre")), source="OI"),
        _point("call_oi", "Call OI centre", "oi", _f(oi.get("call_centre")), source="OI"),
        _band(
            "value_area",
            "Prior value area",
            "tpo",
            _f(va.get("val")),
            _f(va.get("vah")),
            source="TPO",
            poc=_f(va.get("poc")),
            inside=va.get("inside"),
            date=va_day,
        ),
        _point("poc", "Prior POC", "tpo", _f(va.get("poc")), source="TPO", date=va_day),
    ):
        if item is not None:
            levels.append(item)

    # Drop POC point when the value-area band already carries the same POC —
    # keep the band as the primary TPO object; POC remains on the band payload.
    if any(lv["id"] == "value_area" for lv in levels):
        levels = [lv for lv in levels if lv["id"] != "poc"]

    vp_levels = _vp_levels(volume_profile, session_vpoc)
    sd_levels = _sd_levels(supply_demand)
    levels.extend(vp_levels)
    levels.extend(sd_levels)

    overlaps = _find_overlaps(levels, tol)
    overlap_ids = {mid for row in overlaps for mid in row["members"]}
    chips = [_chip_for(lv, spot_f, overlap_ids) for lv in levels]
    chips.sort(key=lambda row: float(row["price"]), reverse=True)

    return {
        "spot": round(spot_f, 1),
        "levels": levels,
        "overlaps": overlaps,
        "chips": chips,
        "summary": _summary(
            spot_f,
            levels,
            overlaps,
            va if va.get("poc") is not None else None,
            n_vp=len(vp_levels),
            n_sd=len(sd_levels),
        ),
        "overlap_tol": tol,
        "volume_profile": volume_profile,
        "session_vpoc": session_vpoc,
        "supply_demand": list(supply_demand or []),
    }


def mark_ladder_structural(
    ladder: list[dict],
    structural_map: dict | None,
    strike_step: float,
) -> None:
    """Tag nearest ladder rows for value-area / day marks (mutates in place)."""
    if not ladder or not structural_map:
        return
    step = float(strike_step)
    level_flags = (
        ("prior_high", "is_prior_high"),
        ("prior_low", "is_prior_low"),
        ("prior_close", "is_prior_close"),
        ("day_high", "is_day_high"),
        ("day_low", "is_day_low"),
        ("poc", "is_poc"),
        ("value_area", None),  # handled via vah/val/poc below
    )
    by_id = {lv["id"]: lv for lv in structural_map.get("levels") or []}

    for row in ladder:
        row.setdefault("is_day_high", False)
        row.setdefault("is_day_low", False)
        row.setdefault("is_vah", False)
        row.setdefault("is_val", False)
        row.setdefault("is_poc", False)
        row.setdefault("is_vpoc", False)
        row.setdefault("is_hvn", False)
        row.setdefault("is_lvn", False)
        row.setdefault("is_supply", False)
        row.setdefault("is_demand", False)
        row.setdefault("is_structural_overlap", False)

    def _tag(price: float | None, flag: str) -> None:
        if price is None:
            return
        nearest = min(ladder, key=lambda row: abs(float(row["strike"]) - float(price)))
        if abs(float(nearest["strike"]) - float(price)) <= step:
            nearest[flag] = True

    for level_id, flag in level_flags:
        if flag is None:
            continue
        lv = by_id.get(level_id)
        if lv:
            _tag(lv.get("price"), flag)

    va = by_id.get("value_area")
    if va:
        _tag(va.get("hi"), "is_vah")
        _tag(va.get("lo"), "is_val")
        _tag(va.get("poc") or va.get("price"), "is_poc")

    for lv in structural_map.get("levels") or []:
        lid = str(lv.get("id") or "")
        if lid == "vpoc" or lid == "session_vpoc":
            _tag(lv.get("price"), "is_vpoc")
        elif lid.startswith("hvn_"):
            _tag(lv.get("price"), "is_hvn")
        elif lid.startswith("lvn_"):
            _tag(lv.get("price"), "is_lvn")
        elif lid.startswith("supply_"):
            _tag(lv.get("price"), "is_supply")
            _tag(lv.get("lo"), "is_supply")
            _tag(lv.get("hi"), "is_supply")
        elif lid.startswith("demand_"):
            _tag(lv.get("price"), "is_demand")
            _tag(lv.get("lo"), "is_demand")
            _tag(lv.get("hi"), "is_demand")

    overlap_mids = {float(row["mid"]) for row in structural_map.get("overlaps") or []}
    for mid in overlap_mids:
        nearest = min(ladder, key=lambda row: abs(float(row["strike"]) - mid))
        if abs(float(nearest["strike"]) - mid) <= step:
            nearest["is_structural_overlap"] = True
