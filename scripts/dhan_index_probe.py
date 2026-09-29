"""PHASE 0b — discover the Dhan scrip-id / segment / lot for the THREE target indices before
wiring them (NIFTY-NSE, BANKNIFTY-NSE, SENSEX-BSE). SENSEX is a BSE index, so its underlying id,
option-chain segment, lot size and expiry rules are all VERIFY-LIVE unknowns (CLAUDE.md landmine).

Reads the Dhan scrip master for the index underlying ids + an option lot size, then live-tests
POST /v2/optionchain/expirylist (+ one chain) so we confirm the chain is actually served. Token
NEVER printed. Throwaway.

  python scripts/dhan_index_probe.py
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.dhan import DHAN_BASE, _SCRIP_MASTER, _headers  # noqa: E402

TARGETS = ["NIFTY", "BANKNIFTY", "SENSEX"]
KNOWN = {"NIFTY": 13, "BANKNIFTY": 25}          # NSE index underlyings (confirmed)


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _post(path, body, timeout=30):
    import requests
    try:
        return requests.post(f"{DHAN_BASE}{path}", json=body, headers=_headers(), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return e


def discover_ids():
    """Return {symbol: {index_id, exch, lot}} from the Dhan scrip master."""
    import requests
    import pandas as pd
    r = requests.get(_SCRIP_MASTER, timeout=90)
    df = pd.read_csv(io.BytesIO(r.content), low_memory=False)
    _print(f"scrip master: {len(df):,} rows, cols={list(df.columns)[:8]}…")
    out = {}
    sym_col = "SEM_TRADING_SYMBOL"
    for sym in TARGETS:
        # index underlying row(s): instrument INDEX (falls back to a name match)
        idx = df[(df.get("SEM_INSTRUMENT_NAME").astype(str).str.upper() == "INDEX")
                 & (df[sym_col].astype(str).str.upper().str.replace(" ", "") == sym)]
        if idx.empty:
            idx = df[df[sym_col].astype(str).str.upper().str.replace(" ", "").str.startswith(sym)
                     & (df.get("SEM_INSTRUMENT_NAME").astype(str).str.upper() == "INDEX")]
        # a representative OPTIDX row for the lot size
        opt = df[(df.get("SEM_INSTRUMENT_NAME").astype(str).str.upper() == "OPTIDX")
                 & (df[sym_col].astype(str).str.upper().str.startswith(sym))]
        rec = {"index_id": None, "exch": None, "lot": None}
        if not idx.empty:
            row = idx.iloc[0]
            rec["index_id"] = int(row["SEM_SMST_SECURITY_ID"])
            rec["exch"] = str(row.get("SEM_EXM_EXCH_ID"))
        if not opt.empty and "SEM_LOT_UNITS" in opt.columns:
            try:
                rec["lot"] = int(opt.iloc[0]["SEM_LOT_UNITS"])
            except (ValueError, TypeError):
                pass
        out[sym] = rec
        _print(f"  {sym:<10} index_id={rec['index_id']} exch={rec['exch']} lot={rec['lot']} "
               f"(index rows={len(idx)}, optidx rows={len(opt)})")
    return out


def test_chain(sym, scrip, seg):
    _print(f"\n[{sym}] optionchain seg={seg} scrip={scrip}")
    r = _post("/optionchain/expirylist", {"UnderlyingScrip": scrip, "UnderlyingSeg": seg})
    if isinstance(r, Exception):
        _print(f"   expirylist EXC: {r}"); return
    if not r.ok:
        _print(f"   expirylist HTTP {r.status_code}: {r.text[:200]}"); return
    exps = sorted((r.json() or {}).get("data") or [])
    _print(f"   expiries: {len(exps)} -> {exps[:5]}")
    if not exps:
        return
    time.sleep(3.2)
    r = _post("/optionchain", {"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": exps[0]})
    if isinstance(r, Exception) or not getattr(r, "ok", False):
        _print(f"   chain FAIL: {getattr(r,'status_code',r)} {getattr(r,'text','')[:200]}"); return
    d = (r.json() or {}).get("data") or {}
    oc = d.get("oc") or {}
    _print(f"   chain OK · spot={d.get('last_price')} · strikes={len(oc)}")


def main():
    ids = discover_ids()
    for sym in TARGETS:
        scrip = ids[sym]["index_id"] or KNOWN.get(sym)
        # NSE indices use IDX_I; try IDX_I first for SENSEX too (Dhan indexes both exchanges there)
        test_chain(sym, scrip, "IDX_I")
        time.sleep(3.2)


if __name__ == "__main__":
    main()
