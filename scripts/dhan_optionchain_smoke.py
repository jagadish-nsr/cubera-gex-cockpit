"""PHASE 0 — verify the Dhan LIVE data surface before building anything on it (CLAUDE.md:
'verify-live, never guess'). Throwaway probe: it makes a few authenticated calls, PRINTS the
real response shapes (so we build the adapter against fact, not memory), saves one option-chain
JSON as a test fixture, and answers the open questions:

  1. Is the token entitled for /v2/optionchain + /optionchain/expirylist ?
  2. Does the chain carry IV, OI, prev-close/prev-OI (for dOI), and greeks ?
  3. Does the index MARKET QUOTE give an accurate day OHLC (for the range bar) ?
  4. Does the chain SPOT match NSE (the pre-2025 dhan spot-corruption landmine) ?
  5. What is the access-token's validity window (via /v2/profile) ?

Reads the token the SAME way the historical client does (data/dhan.py: env DHAN_ACCESS_TOKEN or
gitignored data/.dhan_token). The token is NEVER printed. Nothing here is wired into the app —
it's a manual check you run once:

  python scripts/dhan_optionchain_smoke.py            # NIFTY (scrip 13, seg IDX_I)

Rate limit on /optionchain is 1 request / 3s, so the probe sleeps 3.5s between chain calls.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.dhan import DHAN_BASE, _headers  # noqa: E402  (reuse the exact auth the app uses)

NIFTY_SCRIP = 13
IDX_SEG = "IDX_I"
FIXTURE = ROOT / "data" / "fixtures" / "dhan_optionchain_sample.json"


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _post(path: str, body: dict, timeout: int = 30):
    import requests
    t0 = time.time()
    try:
        r = requests.post(f"{DHAN_BASE}{path}", json=body, headers=_headers(), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return None, f"EXC {type(e).__name__}: {str(e)[:200]}", 0.0
    return r, f"HTTP {r.status_code}", time.time() - t0


def _get(path: str, timeout: int = 30):
    import requests
    try:
        r = requests.get(f"{DHAN_BASE}{path}", headers=_headers(), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return None, f"EXC {type(e).__name__}: {str(e)[:200]}"
    return r, f"HTTP {r.status_code}"


def _keys(d, n=40):
    return sorted(d.keys())[:n] if isinstance(d, dict) else f"<{type(d).__name__}>"


def _jget(r):
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    _print("=" * 78)
    _print("DHAN LIVE SMOKE  ·  NIFTY (scrip 13, seg IDX_I)  ·  token NOT printed")
    _print("=" * 78)

    # --- (5) token validity via profile -------------------------------------------------
    _print("\n[profile] GET /v2/profile  (token validity window)")
    r, status = _get("/profile")
    _print(f"   {status}")
    if r is not None and r.ok:
        p = _jget(r) or {}
        data = p.get("data", p)
        for k in ("dhanClientId", "tokenValidity", "activeSegment", "ddpi", "dataPlan", "dataValidity"):
            if isinstance(data, dict) and k in data:
                _print(f"   {k}: {data[k]}")
        _print(f"   (all keys: {_keys(data)})")
    elif r is not None:
        _print(f"   body: {r.text[:300]}")

    # --- (1) expiry list ----------------------------------------------------------------
    _print("\n[expirylist] POST /v2/optionchain/expirylist")
    r, status, dt = _post("/optionchain/expirylist",
                          {"UnderlyingScrip": NIFTY_SCRIP, "UnderlyingSeg": IDX_SEG})
    _print(f"   {status}  ({dt:.2f}s)")
    expiries = []
    if r is not None and r.ok:
        j = _jget(r) or {}
        expiries = j.get("data") or []
        _print(f"   expiries returned: {len(expiries)} -> {expiries[:6]}")
    elif r is not None:
        _print(f"   body: {r.text[:400]}")
    if not expiries:
        _print("\n!! No expiries — cannot probe the chain. Check entitlement / token / body shape.")
        _print("   (If HTTP 401/403: token not entitled for the Data/Option-Chain API.)")
        return

    time.sleep(3.5)  # respect the 1-req/3s option-chain limit

    # --- (2) option chain for the nearest expiry ----------------------------------------
    exp = expiries[0]
    _print(f"\n[optionchain] POST /v2/optionchain  Expiry={exp}")
    r, status, dt = _post("/optionchain",
                          {"UnderlyingScrip": NIFTY_SCRIP, "UnderlyingSeg": IDX_SEG, "Expiry": exp})
    _print(f"   {status}  ({dt:.2f}s)")
    chain_spot = None
    if r is not None and r.ok:
        j = _jget(r) or {}
        data = j.get("data") or {}
        _print(f"   data keys: {_keys(data)}")
        chain_spot = data.get("last_price") or data.get("ltp")
        _print(f"   underlying last_price (SPOT): {chain_spot}")
        oc = data.get("oc") or data.get("optionChain") or {}
        _print(f"   strikes in chain: {len(oc)}")
        if oc:
            k0 = sorted(oc.keys())[len(oc) // 2]           # a near-ATM strike
            cell = oc[k0]
            _print(f"   sample strike {k0}: sides={_keys(cell)}")
            for side in ("ce", "pe"):
                s = cell.get(side) or {}
                if s:
                    _print(f"     {side}: {_keys(s)}")
                    for fld in ("last_price", "oi", "previous_oi", "volume",
                                "implied_volatility", "previous_close_price", "greeks"):
                        if fld in s:
                            _print(f"        {fld} = {s[fld]}")
        # Save the raw JSON as the adapter/test fixture.
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(j, indent=2), encoding="utf-8")
        _print(f"   >> saved fixture: {FIXTURE.relative_to(ROOT)}")
    elif r is not None:
        _print(f"   body: {r.text[:400]}")

    time.sleep(3.5)

    # --- (3) market quote -> accurate day OHLC (for the vertical candle) -----------------
    _print("\n[quote] POST /v2/marketfeed/quote  { IDX_I: [13] }  (day OHLC for the range bar)")
    r, status, dt = _post("/marketfeed/quote", {IDX_SEG: [NIFTY_SCRIP]})
    _print(f"   {status}  ({dt:.2f}s)")
    quote_spot = None
    if r is not None and r.ok:
        j = _jget(r) or {}
        data = j.get("data") or {}
        seg = data.get(IDX_SEG) or {}
        cell = seg.get(str(NIFTY_SCRIP)) or (next(iter(seg.values())) if seg else {})
        _print(f"   quote keys: {_keys(cell)}")
        quote_spot = cell.get("last_price") or cell.get("ltp")
        ohlc = cell.get("ohlc") or {}
        _print(f"   last_price: {quote_spot}   ohlc: {ohlc}")
    elif r is not None:
        _print(f"   body: {r.text[:400]}")

    # --- (4) spot cross-check vs NSE (corruption landmine) -------------------------------
    _print("\n[cross-check] Dhan spot vs NSE underlyingValue")
    nse_spot = None
    try:
        from data.nse import fetch_chain, parse_nse_option_chain
        _, nse_spot = parse_nse_option_chain(fetch_chain("NIFTY"))
    except Exception as e:  # noqa: BLE001
        _print(f"   (NSE read failed — fine if off-hours/bot-gated: {str(e)[:150]})")
    _print(f"   dhan chain spot : {chain_spot}")
    _print(f"   dhan quote spot : {quote_spot}")
    _print(f"   nse   spot      : {nse_spot}")
    if chain_spot and nse_spot:
        diff = abs(float(chain_spot) - float(nse_spot))
        _print(f"   |dhan-nse|      : {diff:.2f} pts  "
               f"({'OK (<15)' if diff < 15 else 'MISMATCH — investigate scale/staleness'})")

    _print("\n" + "=" * 78)
    _print("Read the field lists above. GO if: chain carries oi + implied_volatility +")
    _print("previous_close_price/previous_oi, quote carries ohlc, and dhan spot ~= nse spot.")
    _print("=" * 78)


if __name__ == "__main__":
    main()
