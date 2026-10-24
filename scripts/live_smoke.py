"""Live NSE smoke test (run locally — NSE blocks datacenter IPs).

Pulls the official Nifty option chain, reports field population, saves the raw
JSON, then runs the engine and prints the level table + playbook.

  python scripts/live_smoke.py

If NSE blocks the fetch, the error explains the options (run from a normal
connection, or switch to a broker API per BUILD_PLAN Phase 0).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from data.expiry import filter_expiry_window  # noqa: E402
from data.nse import fetch_chain, from_nse_results, parse_nse_option_chain  # noqa: E402
from gex.levels import compute_levels  # noqa: E402


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    print("Fetching live NSE Nifty option chain ...")
    payload = fetch_chain(config.UNDERLYING)

    fetched_at = datetime.now(timezone.utc)
    stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "data" / "fixtures" / f"nse_nifty_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload))
    print(f"Saved raw payload -> {out.relative_to(ROOT)}")

    rows, spot = parse_nse_option_chain(payload)
    n = len(rows)
    iv_null = sum(1 for r in rows if r["iv"] is None)
    px_null = sum(1 for r in rows if r["price"] is None)
    oi_zero = sum(1 for r in rows if not r["oi"])
    expiries = sorted({r["expiry"] for r in rows})
    print(f"\nContracts: {n} | spot: {spot:,.2f} | expiries: {len(expiries)}")
    print(f"  IV null   : {iv_null}/{n}")
    print(f"  price null: {px_null}/{n}")
    print(f"  OI == 0   : {oi_zero}/{n}")

    snap = from_nse_results(
        rows, spot=spot, expiry_window=config.EXPIRY_WINDOW_DEFAULT,
        dealer_sign_model=config.DEALER_SIGN_MODEL,
        fetched_at=fetched_at, oi_timestamp=fetched_at, underlying=config.UNDERLYING,
    )
    snap.contracts = filter_expiry_window(snap.contracts, config.EXPIRY_WINDOW_DEFAULT,
                                          fetched_at.date())
    snap.validate()
    print(f"Contracts in window '{config.EXPIRY_WINDOW_DEFAULT}': {len(snap.contracts)}")

    levels = compute_levels(
        snap, contract_size=config.CONTRACT_SIZE,
        grid_range=config.FLIP_GRID_RANGE, grid_step=config.FLIP_GRID_STEP,
        oi_stale_hours=config.OI_STALE_HOURS,
        expiry_weighting=config.EXPIRY_WEIGHTING,
        weight_halflife_days=config.EXPIRY_WEIGHT_HALFLIFE_DAYS,
    )
    from regime.classify import from_levels

    view = from_levels(levels)
    print("\n" + "=" * 64)
    print("  NIFTY GEX LEVELS  --  decision-support only, not financial advice")
    print("=" * 64)
    print(f"  PLAYBOOK: {view.headline}")
    flip = f"{levels.flip:,.2f}" if levels.flip is not None else "NONE (no zero-crossing)"
    print(f"  spot {levels.spot:,.2f} | regime {levels.regime} "
          f"(net GEX {levels.total_net_gex:,.0f})")
    print(f"  call wall {levels.call_wall:,.0f} | put wall {levels.put_wall:,.0f} | "
          f"flip {flip} | magnet {levels.magnet:,.0f} | max pain {levels.max_pain:,.0f}")
    print(f"  lot size {config.CONTRACT_SIZE} (VERIFY) | convention "
          f"{levels.dealer_sign_model} (call magnitude minus put magnitude)")
    for f in levels.flags:
        print(f"  ! {f}")
    print("=" * 64)
    print("\n  Book balance only. Walls are gamma concentration, not a pin and not a breakout trigger.\n")


if __name__ == "__main__":
    main()
