"""Annual lot / expiry / holiday VERIFY drill (Pillar E4).

Surfaces exchange-notice constants the operator must re-confirm — never invents
a lot size or a holiday. Fail loud when the year file or lot table drifts.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

# User-confirmed 2026-10-24 (CLAUDE.md). Do not revive Nifty 75 / BN 35 / Sensex 10.
EXPECTED_LOTS: dict[str, int] = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
    "SENSEX": 20,
}

# Expiry weekday: Mon=0 … Sun=6. Weekly Nifty Tuesday; Sensex Thursday;
# Bank Nifty monthly last Tuesday (has_weekly=False).
EXPECTED_EXPIRY: dict[str, dict[str, Any]] = {
    "NIFTY": {"weekday": 1, "has_weekly": True, "label": "every Tuesday"},
    "BANKNIFTY": {"weekday": 1, "has_weekly": False, "label": "last Tuesday of month"},
    "SENSEX": {"weekday": 3, "has_weekly": True, "label": "every Thursday"},
}


def build_verify_drill(*, as_of: date | None = None) -> dict[str, Any]:
    """Checklist payload for Admin Ops — lot constants + holiday year coverage."""
    import config
    from data import holidays as hol

    today = as_of or date.today()
    year = today.year
    checks: list[dict[str, Any]] = []

    for sym, expected in EXPECTED_LOTS.items():
        idx = config.INDICES.get(sym)
        actual = int(idx.lot_size) if idx else None
        ok = actual == expected
        checks.append({
            "id": f"lot_{sym.lower()}",
            "label": f"{sym} lot size == {expected}",
            "ok": ok,
            "detail": f"config={actual}" if ok else f"config={actual} expected={expected}",
            "verify": "VERIFY against current NSE/BSE circular",
        })

    for sym, spec in EXPECTED_EXPIRY.items():
        idx = config.INDICES.get(sym)
        has_weekly = bool(idx.has_weekly) if idx else None
        ok = has_weekly == bool(spec["has_weekly"])
        checks.append({
            "id": f"expiry_{sym.lower()}",
            "label": f"{sym} expiry · {spec['label']}",
            "ok": ok,
            "detail": (
                f"has_weekly={has_weekly}"
                if ok
                else f"has_weekly={has_weekly} expected={spec['has_weekly']}"
            ),
            "verify": "VERIFY weekday; holiday → previous trading day",
        })

    # Holiday coverage: embedded names for this year OR cache file on disk.
    year_names = [
        iso for iso in hol.NSE_HOLIDAY_NAMES
        if iso.startswith(f"{year}-")
    ]
    cache_path = hol._cache_path(year)
    cache_ok = cache_path.exists()
    names_ok = len(year_names) >= 1
    hol_ok = names_ok or cache_ok
    checks.append({
        "id": f"holiday_year_{year}",
        "label": f"Holiday calendar covers {year}",
        "ok": hol_ok,
        "detail": (
            f"embedded={len(year_names)} cache={'yes' if cache_ok else 'no'} "
            f"path={cache_path.name}"
        ),
        "verify": "Refresh data/journal/holidays/<year>.json when NSE publishes the list",
    })

    # Next year eager fetch expectation in December.
    if today.month == 12:
        ny = year + 1
        ny_names = [iso for iso in hol.NSE_HOLIDAY_NAMES if iso.startswith(f"{ny}-")]
        ny_cache = hol._cache_path(ny).exists()
        checks.append({
            "id": f"holiday_year_{ny}_dec_eager",
            "label": f"December eager: {ny} holiday list present",
            "ok": bool(ny_names) or ny_cache,
            "detail": f"embedded={len(ny_names)} cache={'yes' if ny_cache else 'no'}",
            "verify": "Fetch next-year holidays before year roll",
        })

    # Substring trap: BANKNIFTY must not resolve via NIFTY-in-name lot.
    bn = config.get_index("BANKNIFTY")
    checks.append({
        "id": "index_key_banknifty",
        "label": "BANKNIFTY lot not via NIFTY substring",
        "ok": bn.lot_size == EXPECTED_LOTS["BANKNIFTY"] and config.index_key("BANKNIFTY-FUT") == "BANKNIFTY",
        "detail": f"lot={bn.lot_size} key={config.index_key('BANKNIFTY-FUT')}",
        "verify": "Never choose lot/expiry with a substring check",
    })

    failed = [c["id"] for c in checks if not c.get("ok")]
    return {
        "as_of": today.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pass": len(failed) == 0,
        "failed": failed,
        "checks": checks,
        "expected_lots": dict(EXPECTED_LOTS),
        "doc": "CLAUDE.md · docs/high-reliability-plan.md Pillar E4",
    }
