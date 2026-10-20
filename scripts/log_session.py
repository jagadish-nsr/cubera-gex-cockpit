"""Append today's Nifty GEX snapshot to a session log — for the BEHAVIOURAL
dealer-sign validation (see VALIDATION.md). Run once near the OPEN each trading
day; fill the outcome columns at the CLOSE.

  python scripts/log_session.py

Writes data/journal/sessions.csv (gitignored). After ~10 sessions we can tell
whether positive-gamma days range and flip-breaks trend (sign confirmed), or the
opposite (sign inverted -> flip config.DEALER_SIGN_MODEL).
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from data.expiry import filter_expiry_window  # noqa: E402
from data.nse import fetch_chain, from_nse_results, parse_nse_option_chain  # noqa: E402
from gex.levels import compute_levels  # noqa: E402
from regime.classify import from_levels  # noqa: E402

LOG = ROOT / "data" / "journal" / "sessions.csv"
FIELDS = [
    # snapshot at the open (filled automatically)
    "timestamp_utc", "date_ist", "spot", "regime", "mode_label", "flip",
    "call_wall", "put_wall", "magnet", "max_pain", "net_gex",
    "dealer_sign_model", "expiry_weighting", "n_contracts",
    # outcome (fill by hand at the close)
    "eod_spot", "stayed_in_range", "broke_flip", "trended", "notes",
]


def main(ignore_session: bool = False):
    fa = datetime.now(timezone.utc)
    ist = fa + timedelta(hours=5, minutes=30)

    # Only record live session reads (09:15-15:30 IST, trading days). Off-hours runs
    # are stale end-of-day OI and pollute the dealer-sign scoring (see VALIDATION).
    try:
        from data.holidays import is_trading_day
        is_trade_day = is_trading_day(ist.date())
    except Exception:
        is_trade_day = ist.weekday() < 5
    minute = ist.hour * 60 + ist.minute
    in_session = is_trade_day and 9 * 60 + 15 <= minute <= 15 * 60 + 30
    if not ignore_session and not in_session:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
        print(f"{ist:%Y-%m-%d %H:%M IST}  outside NSE session - skipped "
              "(use --ignore-session to force).")
        return

    rows, spot = parse_nse_option_chain(fetch_chain(config.UNDERLYING))
    snap = from_nse_results(
        rows, spot=spot, expiry_window=config.EXPIRY_WINDOW_DEFAULT,
        dealer_sign_model=config.DEALER_SIGN_MODEL, fetched_at=fa, oi_timestamp=fa,
    )
    snap.contracts = filter_expiry_window(snap.contracts, config.EXPIRY_WINDOW_DEFAULT, fa.date())
    lv = compute_levels(
        snap, contract_size=config.CONTRACT_SIZE,
        expiry_weighting=config.EXPIRY_WEIGHTING,
        weight_halflife_days=config.EXPIRY_WEIGHT_HALFLIFE_DAYS,
        oi_stale_hours=config.OI_STALE_HOURS,
    )
    v = from_levels(lv)

    row = {
        "timestamp_utc": fa.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date_ist": ist.strftime("%Y-%m-%d"),
        "spot": round(lv.spot, 2), "regime": lv.regime, "mode_label": v.mode_label,
        "flip": round(lv.flip, 1) if lv.flip else "",
        "call_wall": int(lv.call_wall), "put_wall": int(lv.put_wall),
        "magnet": int(lv.magnet), "max_pain": int(lv.max_pain),
        "net_gex": int(lv.total_net_gex),
        "dealer_sign_model": lv.dealer_sign_model, "expiry_weighting": lv.expiry_weighting,
        "n_contracts": len(snap.contracts),
        "eod_spot": "", "stayed_in_range": "", "broke_flip": "", "trended": "", "notes": "",
    }

    LOG.parent.mkdir(parents=True, exist_ok=True)
    new_file = not LOG.exists()
    with open(LOG, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(f"Logged -> {LOG.relative_to(ROOT)}")
    flip_txt = f"{lv.flip:,.0f}" if lv.flip else "NONE"
    print(f"{ist:%Y-%m-%d %H:%M IST}  spot {lv.spot:,.2f}  {lv.regime} / {v.mode_label}")
    print(f"  range {lv.put_wall:,.0f}-{lv.call_wall:,.0f} | flip {flip_txt} | magnet {lv.magnet:,.0f}")
    print("At the CLOSE, fill: eod_spot, stayed_in_range (Y/N), broke_flip (Y/N), trended (Y/N), notes.")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Log one Nifty GEX session snapshot.")
    ap.add_argument("--ignore-session", action="store_true",
                    help="record even outside NSE hours (testing)")
    main(ignore_session=ap.parse_args().ignore_session)
