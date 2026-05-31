"""Nifty GEX CLI — prints the level table + one-sentence playbook.

  python cli.py --demo            # offline SYNTHETIC data (no NSE) — sanity-check
  python cli.py --live            # live NSE Nifty option chain (run locally)
  python cli.py --live --window 0dte

Every assumption is logged: lot size (VERIFY), dealer-sign model (UNVERIFIED for
India), expiry window, OI timestamp, and parity-implied r/q.
Decision-support only — not financial advice.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import config
from data.expiry import filter_expiry_window
from data.nse import from_nse_results
from gex.levels import compute_levels
from regime.classify import from_levels


def _build_snapshot(args):
    fetched_at = datetime.now(timezone.utc)
    if args.demo:
        from data.demo import build_demo_rows

        rows, spot = build_demo_rows(fetched_at=fetched_at)
        source = "DEMO-SYNTHETIC"
    else:
        from data.nse import fetch_chain, parse_nse_option_chain

        rows, spot = parse_nse_option_chain(fetch_chain(config.UNDERLYING))
        source = "nse"

    snap = from_nse_results(
        rows, spot=spot, expiry_window=args.window,
        dealer_sign_model=config.DEALER_SIGN_MODEL, source=source,
        fetched_at=fetched_at, oi_timestamp=fetched_at, underlying=config.UNDERLYING,
    )
    snap.contracts = filter_expiry_window(snap.contracts, args.window, fetched_at.date())
    snap.validate()
    return snap


def _print_table(levels, n_contracts: int) -> None:
    L = levels
    view = from_levels(L)
    flip = f"{L.flip:,.2f}" if L.flip is not None else "NONE (no zero-crossing in grid)"
    print("=" * 66)
    print("  NIFTY GEX LEVELS  --  decision-support only, not financial advice")
    print("=" * 66)
    print(f"  PLAYBOOK: {view.headline}")
    if view.conflict:
        print("  ** REGIME/FLIP CONFLICT — de-size **")
    print("-" * 66)
    print(f"  spot            : {L.spot:,.2f}")
    print(f"  regime          : {L.regime}  (total net GEX = {L.total_net_gex:,.0f})")
    print(f"  call wall        : {L.call_wall:,.2f}")
    print(f"  put wall         : {L.put_wall:,.2f}")
    print(f"  gamma flip       : {flip}")
    print(f"  magnet (peak GEX): {L.magnet:,.2f}")
    print(f"  max pain         : {L.max_pain:,.2f}")
    print("-" * 66)
    print("  ASSUMPTIONS (logged on every output):")
    print(f"    lot size       : {config.CONTRACT_SIZE}  (VERIFY against NSE circular)")
    print(f"    dealer sign    : {L.dealer_sign_model}  (UNVERIFIED for India)")
    print(f"    expiry window  : {L.expiry_window}")
    print(f"    contracts used : {n_contracts}")
    print(f"    source         : {L.source}")
    print(f"    OI timestamp   : {L.oi_timestamp}  (stale={L.oi_stale})")
    print("    r/q per expiry : implied from put-call parity")
    for exp, fit in sorted(L.parity_fits.items(), key=lambda kv: str(kv[0])):
        if fit.flagged:
            print(f"      {exp}: FLAGGED — {fit.note}")
        else:
            print(f"      {exp}: r={fit.r:.4f} q={fit.q:.4f}  ({fit.note})")
    if L.flags:
        print("-" * 66)
        print("  FLAGS:")
        for f in L.flags:
            print(f"    ! {f}")
    print("=" * 66)
    print("  ⚠️  Nifty dealer-sign model is UNVALIDATED — do not trust walls/flip "
          "until checked empirically.")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    p = argparse.ArgumentParser(description="Nifty GEX level table")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="fetch live NSE chain")
    mode.add_argument("--demo", action="store_true", help="offline synthetic demo data")
    p.add_argument("--window", default=config.EXPIRY_WINDOW_DEFAULT)
    args = p.parse_args()

    snap = _build_snapshot(args)
    levels = compute_levels(
        snap, contract_size=config.CONTRACT_SIZE,
        grid_range=config.FLIP_GRID_RANGE, grid_step=config.FLIP_GRID_STEP,
        oi_stale_hours=config.OI_STALE_HOURS,
    )
    _print_table(levels, n_contracts=len(snap.contracts))
    if snap.source == "DEMO-SYNTHETIC":
        print("\n  NOTE: DEMO data is SYNTHETIC — do not trade on it.\n")


if __name__ == "__main__":
    main()
