"""One-shot smoke test of the Dhan expired-options API (does the token work; is OI+IV there?).

  python scripts/dhan_test.py                # ATM CALL, WEEK expiry, a small recent window

Needs the access token in env DHAN_ACCESS_TOKEN or data/.dhan_token (gitignored). Prints the
response shape + a couple of sample candles (OI/IV/spot) — NOT the token. Run this before any
backfill to confirm the endpoint, auth, expiryCode semantics, and that oi/iv come back.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.dhan import fetch_rolling_option, to_frame  # noqa: E402


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    to_d = date.today()
    from_d = to_d - timedelta(days=7)
    strike = sys.argv[1] if len(sys.argv) > 1 else "ATM"
    _print(f"Dhan rollingoption test — NIFTY {strike} CALL, WEEK expiryCode=0, 1-min, "
           f"{from_d}..{to_d}\n")
    resp = fetch_rolling_option(from_date=str(from_d), to_date=str(to_d), strike=strike,
                                opt_type="CALL", expiry_flag="WEEK", expiry_code=0, interval="1")
    if resp and "_error" in resp:
        _print(f"❌ API error: {resp['_error']}  body: {resp.get('_body')}")
        _print("   (401/403 = token/auth; check DHAN_ACCESS_TOKEN or data/.dhan_token, and "
               "whether client-id is required.)")
        return
    df = to_frame(resp, "ce")
    if df is None or df.empty:
        _print(f"⚠️ empty response. raw keys: {list((resp or {}).get('data', {}) or {})}")
        _print(f"   raw sample: {str(resp)[:400]}")
        return
    _print(f"✅ got {len(df)} candles · cols: {list(df.columns)}")
    _print(f"   span: {df['date_ist'].min()} {df['time_ist'].iloc[0]} → "
           f"{df['date_ist'].max()} {df['time_ist'].iloc[-1]}")
    have = [c for c in ("oi", "iv", "spot", "strike", "volume") if c in df.columns
            and df[c].notna().any() and (df[c] != 0).any()]
    _print(f"   populated: {have}")
    _print("\n   last 3 candles:")
    show = [c for c in ("date_ist", "time_ist", "close", "volume", "oi", "iv", "spot", "strike")
            if c in df.columns]
    _print(df[show].tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
