"""Indian GEX CLI — prints the level table + one-sentence playbook.

  python cli.py --demo                          # offline SYNTHETIC data (no NSE) — sanity-check
  python cli.py --live                          # live data (per DATA_SOURCE in .env)
  python cli.py --live --symbol SENSEX          # SENSEX (requires Dhan credentials)
  python cli.py --live --symbol BANKNIFTY       # Bank Nifty
  python cli.py --live --window 0dte

DATA_SOURCE in .env controls which provider is used:
  DATA_SOURCE=auto          — default; uses Dhan when token present, else NSE
  DATA_SOURCE=dhan          — Dhan broker API (NIFTY, BANKNIFTY, SENSEX)
  DATA_SOURCE=nse           — legacy NSE scraper (NIFTY, BANKNIFTY only)

Every assumption is logged: lot size (VERIFY), dealer-sign model (UNVERIFIED for
India), expiry window, OI timestamp, and the parity-implied forward + carry.
Decision-support only — not financial advice.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timezone

import config
from data.expiry import filter_expiry_window
from data.nse import from_nse_results
from gex.levels import compute_levels
from regime.classify import from_levels


def _build_snapshot(args):
    window = args.window or config.EXPIRY_WINDOW_DEFAULT
    if args.demo and not args.window:
        window = "all"
    fetched_at = datetime.now(timezone.utc)
    idx = config.get_index(args.symbol)

    if args.demo:
        from data.demo import build_demo_rows

        rows, spot = build_demo_rows(fetched_at=fetched_at)
        source = "DEMO-SYNTHETIC"
    elif config.DATA_SOURCE in ("dhan", "auto"):
        client_id    = config.DHAN_CLIENT_ID
        access_token = config.DHAN_ACCESS_TOKEN
        if client_id and access_token:
            from data.dhan import fetch_chain_dhan

            rows, spot = fetch_chain_dhan(
                idx.symbol,
                client_id=client_id,
                access_token=access_token,
                has_weekly=idx.has_weekly,
            )
            source = "dhan"
        elif config.DATA_SOURCE == "dhan":
            print(
                "ERROR: DATA_SOURCE=dhan requires DHAN_CLIENT_ID and "
                "DHAN_ACCESS_TOKEN in .env",
                file=sys.stderr,
            )
            sys.exit(1)
        else:
            if idx.symbol == "SENSEX":
                print(
                    "ERROR: SENSEX is a BSE index not available via NSE scraper. "
                    "Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in .env.",
                    file=sys.stderr,
                )
                sys.exit(1)
            from data.nse import fetch_chain, parse_nse_option_chain

            rows, spot = parse_nse_option_chain(fetch_chain(idx.symbol, has_weekly=idx.has_weekly))
            source = "nse"
    else:
        if idx.symbol == "SENSEX":
            print(
                "ERROR: SENSEX is a BSE index and not available via NSE scraper. "
                "Use DATA_SOURCE=dhan with credentials in .env.",
                file=sys.stderr,
            )
            sys.exit(1)
        from data.nse import fetch_chain, parse_nse_option_chain

        rows, spot = parse_nse_option_chain(fetch_chain(idx.symbol, has_weekly=idx.has_weekly))
        source = "nse"

    snap = from_nse_results(
        rows, spot=spot, expiry_window=window,
        dealer_sign_model=config.DEALER_SIGN_MODEL, source=source,
        fetched_at=fetched_at, oi_timestamp=fetched_at, underlying=idx.symbol,
    )
    snap.contracts = filter_expiry_window(
        snap.contracts, window, fetched_at.date(),
        has_weekly=idx.has_weekly, exchange=idx.exchange,
    )
    snap.validate()
    return snap, idx


def _print_table(levels, n_contracts: int, idx) -> None:
    L = levels
    view = from_levels(L)
    flip = f"{L.flip:,.2f}" if L.flip is not None else "NONE (no zero-crossing in grid)"
    print("=" * 66)
    print(f"  {idx.label} GEX LEVELS  --  decision-support only, not financial advice")
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
    print(f"    index          : {idx.symbol} ({idx.label})")
    print(f"    data source    : {L.source}")
    print(f"    lot size       : {idx.lot_size}  (VERIFY against exchange circular)")
    print(f"    dealer sign    : {L.dealer_sign_model}  (Indian Market Mode)")
    print(f"    expiry window  : {L.expiry_window}")
    print(f"    contracts used : {n_contracts}")
    print(f"    OI timestamp   : {L.oi_timestamp}  (stale={L.oi_stale})")
    print("    forward & carry: implied from put-call parity (robust forward)")
    for exp, fit in sorted(L.parity_fits.items(), key=lambda kv: str(kv[0])):
        if fit.flagged or not fit.forward:
            print(f"      {exp}: FLAGGED — {fit.note}")
        else:
            carry = math.log(fit.forward / L.spot) / fit.T
            print(f"      {exp}: F={fit.forward:,.1f}  carry(r-q)={carry * 100:+.2f}%  "
                  f"({fit.n_strikes} strikes)")
    if L.flags:
        print("-" * 66)
        print("  FLAGS:")
        for f in L.flags:
            print(f"    ! {f}")
    print("=" * 66)
    print("\n  ⚠️  Indian Market Mode Active (Negative Regime): Walls act as breakout triggers, not ceilings.")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    p = argparse.ArgumentParser(description="Indian GEX level table (Nifty / Bank Nifty / Sensex)")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true",
                      help="fetch live chain (Dhan or NSE per DATA_SOURCE in .env)")
    mode.add_argument("--demo", action="store_true", help="offline synthetic demo data")
    p.add_argument("--symbol", default=None,
                   help="index symbol: NIFTY (default) / BANKNIFTY / SENSEX")
    p.add_argument("--window", default=None)
    args = p.parse_args()

    snap, idx = _build_snapshot(args)
    levels = compute_levels(
        snap, contract_size=idx.lot_size,
        grid_range=config.FLIP_GRID_RANGE, grid_step=config.FLIP_GRID_STEP,
        oi_stale_hours=config.OI_STALE_HOURS,
        expiry_weighting=config.EXPIRY_WEIGHTING,
        weight_halflife_days=config.EXPIRY_WEIGHT_HALFLIFE_DAYS,
    )
    _print_table(levels, n_contracts=len(snap.contracts), idx=idx)
    if snap.source == "DEMO-SYNTHETIC":
        print("\n  NOTE: DEMO data is SYNTHETIC — do not trade on it.\n")


if __name__ == "__main__":
    main()
