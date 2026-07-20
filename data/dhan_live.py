"""Dhan LIVE option-chain adapter — the robust primary feed (NSE stays the backup).

Turns Dhan's REST ``/v2/optionchain`` (+ ``/marketfeed/quote`` for a true day OHLC) into the
SAME ``payload`` shape ``data/nse.py`` produces, so EVERY downstream parser
(``parse_nse_option_chain``, ``aggregate_oi_volume``, ``per_strike_oi``,
``per_strike_nearest_changes``, ``nearest_expiry_premiums``) and the whole engine run UNCHANGED.

⚠️ VERIFY-LIVE (CLAUDE.md): the Dhan JSON field names below are from the v2 docs, NOT yet
confirmed against a live response. ``scripts/dhan_optionchain_smoke.py`` saves a real fixture to
``data/fixtures/dhan_optionchain_sample.json``; reconcile ``_F`` / ``_OC_KEYS`` against it, then
``tests/test_dhan_live.py`` locks it. ``_to_nse_payload`` RAISES on an unexpected shape rather
than guess — ``data_access`` catches that and falls back to NSE, so a wrong map degrades the
feed, never a trade number.

Auth reuses ``data/dhan.py`` (bearer access-token from ``data/.dhan_token``; SEBI-capped at 24h,
re-pasted daily via the dashboard). Rate limit: ``/optionchain`` is 1 request / 3s — a process
-local throttle serialises all chain calls to ≥3.1s spacing.
"""

from __future__ import annotations

import threading
import time

from data.dhan import DHAN_BASE, DHAN_UNDERLYING, _headers

# --- Dhan index underlyings (option chain uses the INDEX segment, not FNO) -------------
IDX_SEG = "IDX_I"
NIFTY_SCRIP = DHAN_UNDERLYING["NIFTY"]        # 13
_SCRIP = dict(DHAN_UNDERLYING)                # {"NIFTY":13,"BANKNIFTY":25}

# --- FIELD MAP (VERIFY vs data/fixtures/dhan_optionchain_sample.json) ------------------
# The single place to fix if the smoke output shows different keys.
_OC_KEYS = ("oc", "optionChain")              # dict of {strike -> {ce,pe}} on the chain payload
_SPOT_KEYS = ("last_price", "ltp")            # underlying spot on the chain payload
# Confirmed live 2026-07-18 against data/fixtures/dhan_optionchain_sample.json.
_F = {
    "oi": "oi",
    "prev_oi": "previous_oi",                 # -> changeinOpenInterest = oi - previous_oi
    "volume": "volume",
    "iv": "implied_volatility",               # PERCENT (e.g. 12.93), matching NSE's convention
    "ltp": "last_price",
    "prev_close": "previous_close_price",     # -> change = last_price - previous_close_price
    "bid": "top_bid_price",                   # -> buyPrice1 (for a true mid, like NSE's)
    "ask": "top_ask_price",                   # -> sellPrice1
}
_OHLC_KEYS = ("ohlc",)                        # day OHLC dict on a marketfeed/quote cell

_HORIZON_DAYS = 35                            # window = expiries within ~next monthly (calendar-agnostic)
_MAX_EXPIRIES = 4                             # cap chain calls per snapshot (near-dated dominate the GEX)
_MIN_INTERVAL = 3.1                           # s between /optionchain calls (limit is 1/3s)
_throttle_lock = threading.Lock()
_last_call = [0.0]


def _throttle() -> None:
    with _throttle_lock:
        wait = _MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


def _seg_scrip(symbol: str) -> tuple[str, int]:
    return IDX_SEG, _SCRIP.get(symbol.upper(), NIFTY_SCRIP)


def _post(path: str, body: dict, timeout: int = 30):
    import requests
    return requests.post(f"{DHAN_BASE}{path}", json=body, headers=_headers(), timeout=timeout)


def fetch_expiry_list(symbol: str = "NIFTY", timeout: int = 20) -> list[str]:
    """Upcoming expiries (YYYY-MM-DD, ascending) for the index, or [] on any soft failure."""
    seg, scrip = _seg_scrip(symbol)
    try:
        r = _post("/optionchain/expirylist", {"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout)
        if not r.ok:
            return []
        return sorted((r.json() or {}).get("data") or [])
    except Exception:  # noqa: BLE001
        return []


def fetch_index_ohlc(symbol: str = "NIFTY", timeout: int = 20) -> dict | None:
    """True day OHLC (+ ltp) for the index spot via ``/marketfeed/quote`` — the accurate basis for
    the daily-candle range bar (the snapshot-reconstructed high/low understates the true range).
    Returns {open,high,low,close,ltp} or None. Best-effort: never raises."""
    seg, scrip = _seg_scrip(symbol)          # seg is always IDX_I for the supported indices
    try:
        ids = [scrip] + ([DHAN_VIX_ID] if scrip != DHAN_VIX_ID else [])   # bundle VIX into one call
        r = _post("/marketfeed/quote", {seg: ids}, timeout)
        if not r.ok:
            return None
        seg_d = (r.json() or {}).get("data", {}).get(seg, {})
        # Only the REQUESTED scrip. This used to fall back to `next(iter(seg_d.values()))`, which on
        # a partial response silently returned a DIFFERENT index's OHLC (India VIX rides the same
        # quote) — a wrong day-range for the candle, presented as fact. Fail closed instead.
        cell = seg_d.get(str(scrip))
        if not cell:
            return None
        ohlc = next((cell[k] for k in _OHLC_KEYS if k in cell), {}) or {}
        out = {k: _num(ohlc.get(k)) for k in ("open", "high", "low", "close")}
        out["ltp"] = _num(cell.get("last_price") or cell.get("ltp"))
        out["vix"] = _vix_from_cell(seg_d.get(str(DHAN_VIX_ID)))          # India VIX, same quote
        return out
    except Exception:  # noqa: BLE001
        return None


DHAN_VIX_ID = 21                              # INDIA VIX index on the IDX_I segment (scrip master)


def _vix_from_cell(cell) -> dict | None:
    """{last, change, pct_change} (parse_india_vix shape) from a marketfeed quote cell for VIX.
    VIX ``net_change`` is often 0 in the feed, so change falls back to the intraday move (last-open)."""
    if not cell:
        return None
    last = _num(cell.get("last_price"))
    if last is None:
        return None
    chg = _num(cell.get("net_change")) or None
    if not chg:
        o = _num((cell.get("ohlc") or {}).get("open"))
        chg = (last - o) if o else None
    pct = (chg / (last - chg) * 100.0) if (chg and last and (last - chg)) else None
    return {"last": last, "change": chg, "pct_change": pct}


def fetch_india_vix(timeout: int = 15) -> dict | None:
    """Standalone live India VIX (IDX_I:21) via the marketfeed quote. Prefer the VIX that
    ``fetch_index_ohlc`` bundles into ITS quote (one call) — this is the fallback."""
    try:
        r = _post("/marketfeed/quote", {IDX_SEG: [DHAN_VIX_ID]}, timeout)
        if not r.ok:
            return None
        return _vix_from_cell((r.json() or {}).get("data", {}).get(IDX_SEG, {}).get(str(DHAN_VIX_ID)))
    except Exception:  # noqa: BLE001
        return None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# --- live index FUTURES (near-month levels + aggregate across serial months) ----------
_FUT_CACHE: dict = {}       # symbol -> {day, seg, ids (near->far), near_expiry}


def _future_contracts(symbol: str = "NIFTY") -> dict:
    """Serial-month index-futures securityIds + the F&O segment for ``symbol`` (NSE_FNO for
    NIFTY/BankNifty, BSE_FNO for SENSEX), resolved from the Dhan scrip master and CACHED per day
    (the master is a ~30MB download). Returns {seg, ids (near→far, ≤3), near_expiry}; empty ids
    if none are current. Best-effort — never raises."""
    import datetime as _dt
    today = _dt.date.today().isoformat()
    c = _FUT_CACHE.get(symbol)
    if c and c.get("day") == today:
        return c
    out = {"day": today, "seg": None, "ids": [], "near_expiry": None}
    try:
        import io
        import pandas as pd
        import requests
        from data.dhan import _SCRIP_MASTER
        df = pd.read_csv(io.BytesIO(requests.get(_SCRIP_MASTER, timeout=90).content), low_memory=False)
        f = df[(df["SEM_INSTRUMENT_NAME"] == "FUTIDX")
               & (df["SEM_TRADING_SYMBOL"].astype(str).str.upper().str.startswith(symbol.upper() + "-"))].copy()
        f["exp"] = pd.to_datetime(f["SEM_EXPIRY_DATE"], errors="coerce")
        f = f[f["exp"].dt.date >= _dt.date.today()].sort_values("exp")
        if not f.empty:
            out["seg"] = f"{str(f.iloc[0]['SEM_EXM_EXCH_ID']).upper()}_FNO"
            out["ids"] = [int(x) for x in f["SEM_SMST_SECURITY_ID"].tolist()][:3]
            out["near_expiry"] = f.iloc[0]["exp"].date().isoformat()
    except Exception:  # noqa: BLE001
        pass
    _FUT_CACHE[symbol] = out
    return out


def fetch_futures_dhan(symbol: str = "NIFTY", timeout: int = 20) -> dict | None:
    """Live index-futures record from Dhan — near-month levels + AGGREGATE across serial months
    (rollover-netted), in the SAME shape as ``data.nse.parse_futures_live`` so it drops straight
    into ``capture_futures_snapshot``: {symbol, expiry, price, change, oi, volume, agg_oi, agg_vol,
    spot}. Best-effort None on any failure (incl. a transient 429)."""
    c = _future_contracts(symbol)
    if not c["ids"] or not c["seg"]:
        return None
    try:
        r = _post("/marketfeed/quote", {c["seg"]: c["ids"]}, timeout)
        if r.status_code == 429:                 # quote rate-limit — brief backoff, one retry
            import time as _t
            _t.sleep(1.1)
            r = _post("/marketfeed/quote", {c["seg"]: c["ids"]}, timeout)
        if not r.ok:
            return None
        d = (r.json() or {}).get("data", {}).get(c["seg"], {})
        cells = [d.get(str(i)) for i in c["ids"] if d.get(str(i))]
        near = d.get(str(c["ids"][0]))
        if not near or not cells:
            return None
        price = _num(near.get("last_price"))
        chg = _num(near.get("net_change")) or None
        if not chg and price is not None:                      # VIX-style fallback: vs day open
            o = _num((near.get("ohlc") or {}).get("open"))
            chg = (price - o) if o else None

        def _sum(k):
            vals = [v for v in (_num(x.get(k)) for x in cells) if v is not None]
            return sum(vals) if vals else None

        return {"symbol": symbol, "expiry": c["near_expiry"], "price": price, "change": chg,
                "oi": _num(near.get("oi")), "volume": _num(near.get("volume")),
                "agg_oi": _sum("oi"), "agg_vol": _sum("volume"), "spot": None}
    except Exception:  # noqa: BLE001
        return None


def _ddmmyyyy(iso: str) -> str:
    """Dhan expiry 'YYYY-MM-DD' -> 'DD-MM-YYYY' (a format data.nse._parse_nse_date accepts)."""
    y, m, d = iso.split("-")
    return f"{d}-{m}-{y}"


def _to_nse_rows(dhan_json: dict, expiry_iso: str) -> tuple[list[dict], float | None]:
    """One Dhan chain response -> NSE-shaped rows for ONE expiry + the underlying spot.

    RAISES ValueError if the documented structure (an ``oc`` strike map + a spot) is absent, so
    a shape mismatch fails loud (→ NSE fallback) instead of silently emitting empty/wrong levels.
    """
    data = (dhan_json or {}).get("data") or {}
    oc = next((data[k] for k in _OC_KEYS if isinstance(data.get(k), dict)), None)
    spot = next((data[k] for k in _SPOT_KEYS if data.get(k) is not None), None)
    if not oc or spot is None:
        raise ValueError(f"Dhan chain missing oc/spot (keys={sorted(data)[:12]}) — shape mismatch")
    exp = _ddmmyyyy(expiry_iso)
    rows: list[dict] = []
    for strike_key, cell in oc.items():
        try:
            strike = float(strike_key)
        except (TypeError, ValueError):
            continue
        for side_key, nse_side in (("ce", "CE"), ("pe", "PE")):
            s = cell.get(side_key) or {}
            if not s:
                continue
            oi = _num(s.get(_F["oi"]))
            poi = _num(s.get(_F["prev_oi"]))
            ltp = _num(s.get(_F["ltp"]))
            pclose = _num(s.get(_F["prev_close"]))
            rows.append({
                "strikePrice": strike,
                "expiryDate": exp,
                nse_side: {
                    "openInterest": oi or 0.0,
                    "changeinOpenInterest": (oi - poi) if (oi is not None and poi is not None) else 0.0,
                    "totalTradedVolume": _num(s.get(_F["volume"])) or 0.0,
                    "impliedVolatility": _num(s.get(_F["iv"])) or 0.0,   # PERCENT (parser /100)
                    "lastPrice": ltp,
                    "buyPrice1": _num(s.get(_F["bid"])),                 # true bid/ask -> mid downstream
                    "sellPrice1": _num(s.get(_F["ask"])),
                    "change": (ltp - pclose) if (ltp is not None and pclose is not None) else None,
                    "strikePrice": strike,
                    "expiryDate": exp,
                },
            })
    return rows, float(spot)


def fetch_chain(symbol: str = "NIFTY", expiries: list[str] | None = None,
                max_expiries: int | None = None, timeout: int = 30) -> dict:
    """LIVE Dhan pull assembled into ONE NSE-shaped ``{records:{data, underlyingValue}}`` payload
    (drop-in for ``data.nse.fetch_chain``). Fetches each in-window expiry's chain (throttled to
    the 1-req/3s limit) and merges the rows. Bounds the expiry set to the next monthly by default
    (``0dte_to_next_monthly``); pass ``expiries`` (YYYY-MM-DD) or ``max_expiries`` to override.

    RAISES on no usable data so ``data_access`` falls back to NSE."""
    seg, scrip = _seg_scrip(symbol)
    exps = expiries or _window_expiries(symbol, max_expiries)
    if not exps:
        raise RuntimeError("Dhan returned no expiries (token/entitlement?) — falling back")

    merged: list[dict] = []
    spot = None
    errors = []
    for e in exps:
        _throttle()
        try:
            r = _post("/optionchain", {"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": e}, timeout)
            if not r.ok:
                errors.append(f"{e}: HTTP {r.status_code}")
                continue
            rows, sp = _to_nse_rows(r.json(), e)
            merged.extend(rows)
            spot = spot or sp
        except Exception as ex:  # noqa: BLE001
            errors.append(f"{e}: {type(ex).__name__} {str(ex)[:120]}")
    if not merged or spot is None:
        raise RuntimeError(f"Dhan chain produced no data. errors: {errors or 'empty'}")
    return {"records": {"data": merged, "underlyingValue": spot}}


def _window_expiries(symbol: str, max_expiries: int | None) -> list[str]:
    """Near-dated Dhan expiries to fetch. Bounded by a DAY horizon (calendar-agnostic — works for
    NSE-Tuesday, BankNifty-monthly AND BSE-Thursday SENSEX alike) and capped in count, since the
    exp-weighting means far expiries barely move the levels and each call costs ≥3s."""
    from datetime import date, timedelta
    exps = fetch_expiry_list(symbol)
    if not exps:
        return []
    horizon = (date.today() + timedelta(days=_HORIZON_DAYS)).isoformat()
    win = [e for e in exps if e <= horizon] or exps[:1]
    return win[:(max_expiries or _MAX_EXPIRIES)]


def ping(symbol: str = "NIFTY") -> dict:
    """Quick live health check for the UI: does the token reach the Data API and see the chain?
    Returns {ok, expiries, spot, error} — never raises."""
    try:
        exps = fetch_expiry_list(symbol)
        if not exps:
            return {"ok": False, "expiries": 0, "spot": None,
                    "error": "no expiries (token expired / Data API not subscribed?)"}
        _throttle()
        seg, scrip = _seg_scrip(symbol)
        r = _post("/optionchain", {"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": exps[0]})
        if not r.ok:
            return {"ok": False, "expiries": len(exps), "spot": None,
                    "error": f"chain HTTP {r.status_code}"}
        _, spot = _to_nse_rows(r.json(), exps[0])
        return {"ok": True, "expiries": len(exps), "spot": spot, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "expiries": 0, "spot": None, "error": f"{type(e).__name__}: {str(e)[:150]}"}
