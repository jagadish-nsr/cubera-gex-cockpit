"""Hand-checks for the option-securityId resolver (data/dhan_feed.resolve_option / resolve_blast_pair) —
the P0 foundation for WS-tracking the gamma-blast 13:45 ATM call & put. Date-relative synthetic master so
the test doesn't rot when an expiry passes."""
from datetime import date, timedelta

import pandas as pd

from data.dhan_feed import resolve_option, resolve_blast_pair


def _master():
    near = (date.today() + timedelta(days=3)).isoformat() + " 14:30:00"     # nearest expiry
    far = (date.today() + timedelta(days=31)).isoformat() + " 14:30:00"
    past = (date.today() - timedelta(days=4)).isoformat() + " 14:30:00"     # expired → filtered
    rows = []
    for exp in (near, far, past):
        for strike in (23900, 23950, 24000, 24050):
            for side in ("CE", "PE"):
                rows.append({
                    "SEM_INSTRUMENT_NAME": "OPTIDX",
                    "SEM_TRADING_SYMBOL": f"NIFTY-x-{strike}-{side}",
                    "SEM_STRIKE_PRICE": float(strike), "SEM_OPTION_TYPE": side,
                    "SEM_EXPIRY_DATE": exp, "SEM_SMST_SECURITY_ID": strike * 10 + (1 if side == "CE" else 2),
                    "SEM_LOT_UNITS": 65})
    # a future FUTIDX + a foreign-symbol option that must never match
    rows.append({"SEM_INSTRUMENT_NAME": "FUTIDX", "SEM_TRADING_SYMBOL": "NIFTY-x-FUT",
                 "SEM_STRIKE_PRICE": 0.0, "SEM_OPTION_TYPE": "XX", "SEM_EXPIRY_DATE": near,
                 "SEM_SMST_SECURITY_ID": 999, "SEM_LOT_UNITS": 65})
    rows.append({"SEM_INSTRUMENT_NAME": "OPTIDX", "SEM_TRADING_SYMBOL": "BANKNIFTY-x-24000-CE",
                 "SEM_STRIKE_PRICE": 24000.0, "SEM_OPTION_TYPE": "CE", "SEM_EXPIRY_DATE": near,
                 "SEM_SMST_SECURITY_ID": 111, "SEM_LOT_UNITS": 30})
    return pd.DataFrame(rows)


def test_resolve_option_nearest_expiry_exact_strike():
    m = _master()
    ce = resolve_option("NIFTY", 24000, "CE", master=m)
    assert ce["security_id"] == 240001 and ce["side"] == "CE" and ce["strike"] == 24000.0
    assert ce["expiry"] == (date.today() + timedelta(days=3)).isoformat()   # nearest, not the far one
    assert ce["segment"] == "NSE_FNO" and ce["lot"] == 65
    pe = resolve_option("NIFTY", 24000, "PE", master=m)
    assert pe["security_id"] == 240002 and pe["side"] == "PE"


def test_resolve_option_specific_expiry_and_misses():
    m = _master()
    far = (date.today() + timedelta(days=31)).isoformat()
    assert resolve_option("NIFTY", 23950, "CE", expiry=far, master=m)["expiry"] == far
    assert resolve_option("NIFTY", 99999, "CE", master=m) is None          # no such strike
    assert resolve_option("NIFTY", 24000, "XX", master=m) is None          # bad side
    # never returns the expired expiry or the BANKNIFTY row
    assert resolve_option("NIFTY", 24000, "CE", master=m)["security_id"] != 111


def test_resolve_blast_pair_snaps_to_nearest_strike():
    m = _master()
    pair = resolve_blast_pair("NIFTY", spot=24012, master=m)               # 24012 → nearest listed 24000
    assert pair["strike"] == 24000.0
    assert pair["call"]["security_id"] == 240001 and pair["put"]["security_id"] == 240002
    assert pair["expiry"] == (date.today() + timedelta(days=3)).isoformat()
    # spot equidistant-ish rounds to the actual nearest grid strike
    assert resolve_blast_pair("NIFTY", spot=23961, master=m)["strike"] == 23950.0


def test_resolve_blast_pair_empty_master():
    assert resolve_blast_pair("NIFTY", 24000, master=pd.DataFrame(
        {"SEM_INSTRUMENT_NAME": [], "SEM_TRADING_SYMBOL": []})) is None
