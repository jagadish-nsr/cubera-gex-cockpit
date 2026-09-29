"""Backfill the per-strike OI journal for a day from Dhan's 1-min history.

If the dashboard/logger started mid-session, the early hours are missing from the live per-strike
capture, so "since open" and the longer trailing windows can't compute. This fills those gaps from
Dhan's ``rollingoption`` 1-min bars (real per-strike OI + premium), at NATIVE 1-min resolution (no
resampling), writing ONLY the timestamps not already captured — idempotent, safe to re-run.

Writes to the SEPARATE Dhan journal (``oi_strikes_dhan/<SYMBOL>/<date>.csv``) so Dhan and NSE data
never mix. The trailing windows difference absolute OI across snapshots, so only
``call_oi/put_oi/call_ltp/put_ltp`` per (time, strike) are needed — all present in the Dhan bars.

⚠️ Dhan per-strike history covers ATM±10 (near-money — where the walls/footprint live). Exchange OI
disseminates ~every 3 min, so 1-min bars step at that granularity. Decision-support only.
"""

from __future__ import annotations


def _v(x):
    """A finite float, or None (csv writes None as blank)."""
    try:
        f = float(x)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def maybe_backfill_oi(symbol: str, date_ist: str, max_gap_min: float = 2.0, log=print) -> dict:
    """Run the 1-min backfill ONLY if the Dhan per-strike journal has a gap > ``max_gap_min``
    anywhere in the session so far — the largest gap among {open→first stamp, between consecutive
    stamps, last stamp→now}. So a normally-running 1-min capture is a no-op, but a LATE START
    (missing early hours, even though the latest stamp is current) or a missed stretch triggers a
    fill. Returns the backfill summary, or {'skipped', 'max_gap_min'}."""
    from datetime import datetime, timedelta, timezone
    from app.timeseries import OISTRIKES_DHAN_DIR, load_strikes_day

    def _dt(t):
        try:
            return datetime.strptime(f"{date_ist} {t}", "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return None

    now_ist = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).replace(tzinfo=None)
    open_ist, close_ist = _dt("09:15"), _dt("15:30")
    end = min(now_ist, close_ist)
    if end <= open_ist:
        return {"skipped": True, "reason": "pre-open"}
    df = load_strikes_day(date_ist, symbol, directory=OISTRIKES_DHAN_DIR)
    if getattr(df, "empty", True) or "time_ist" not in df.columns or not len(df):
        return backfill_oi_strikes_dhan(symbol, date_ist, log=log)
    stamps = sorted({d for t in df["time_ist"].astype(str) if (d := _dt(t)) and open_ist <= d <= end})
    pts = [open_ist] + stamps + [end]
    max_gap = max((pts[i + 1] - pts[i]).total_seconds() / 60.0 for i in range(len(pts) - 1))
    if max_gap <= max_gap_min:
        return {"skipped": True, "max_gap_min": round(max_gap, 1)}
    return backfill_oi_strikes_dhan(symbol, date_ist, log=log)


def backfill_oi_strikes_dhan(symbol: str, date_ist: str, log=print) -> dict:
    """Fill missing 1-min per-strike OI snapshots for ``symbol`` on ``date_ist`` (YYYY-MM-DD) into
    the Dhan journal. Returns {stamps, rows, had} (new stamps written, strike-rows, stamps already
    present). Best-effort: an empty/failed Dhan pull returns zeros, never raises."""
    from datetime import date as _date, datetime, timedelta, timezone

    from data.dhan import (DHAN_OPT_FLAG, DHAN_UNDERLYING, NIFTY_SECURITY_ID,
                           fetch_option_matrix)
    from app.timeseries import (OISTRIKES_DHAN_DIR, append_strike_rows, load_strikes_day)

    flag = DHAN_OPT_FLAG.get(symbol, "WEEK")
    sid = DHAN_UNDERLYING.get(symbol, NIFTY_SECURITY_ID)
    seg = "BSE_FNO" if symbol == "SENSEX" else "NSE_FNO"
    d0 = _date.fromisoformat(date_ist)
    mat, misses = fetch_option_matrix(d0.isoformat(), (d0 + timedelta(days=1)).isoformat(),
                                      flag=flag, code=1, interval="1", security_id=sid, segment=seg)
    if mat is None or getattr(mat, "empty", True):
        log(f"  {symbol} {date_ist}: no Dhan 1-min data ({len(misses)} series-misses)")
        return {"stamps": 0, "rows": 0, "had": 0}

    have_df = load_strikes_day(date_ist, symbol, directory=OISTRIKES_DHAN_DIR)
    have = set(have_df["time_ist"].astype(str)) if not have_df.empty else set()

    stamps = rows_written = 0
    for t, g in mat.groupby("time_ist"):
        if str(t) in have:                                  # already captured — don't duplicate
            continue
        try:
            ist_dt = datetime.strptime(f"{date_ist} {t}", "%Y-%m-%d %H:%M")
            ts_utc = ((ist_dt - timedelta(hours=5, minutes=30)).replace(tzinfo=timezone.utc)
                      .strftime("%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            ts_utc = ""
        by_k: dict[float, dict] = {}
        spot_t = None
        for r in g.itertuples(index=False):
            k = _v(r.strike)
            if k is None:
                continue
            spot_t = _v(r.spot) or spot_t
            cell = by_k.setdefault(k, {"call_oi": None, "put_oi": None,
                                       "call_ltp": None, "put_ltp": None})
            if r.side == "CE":
                cell["call_oi"], cell["call_ltp"] = _v(r.oi), _v(r.close)
            else:
                cell["put_oi"], cell["put_ltp"] = _v(r.oi), _v(r.close)
        batch = []
        for k, cell in sorted(by_k.items()):
            batch.append({
                "timestamp_utc": ts_utc, "time_ist": str(t), "date_ist": date_ist,
                "spot": round(spot_t, 2) if spot_t else None, "strike": k,
                # rollingoption OI is the NEAREST-expiry contract -> fill BOTH call_oi and the
                # source-consistent call_oi_near (the field the trailing windows difference).
                "call_oi": cell["call_oi"], "put_oi": cell["put_oi"],
                "call_oi_near": cell["call_oi"], "put_oi_near": cell["put_oi"],
                "call_ltp": round(cell["call_ltp"], 2) if cell["call_ltp"] is not None else None,
                "put_ltp": round(cell["put_ltp"], 2) if cell["put_ltp"] is not None else None,
            })
        if batch:
            append_strike_rows(batch, date_ist=date_ist, symbol=symbol,
                               directory=OISTRIKES_DHAN_DIR)
            stamps += 1
            rows_written += len(batch)
    log(f"  {symbol} {date_ist}: filled {stamps} new 1-min stamps ({rows_written} strike-rows); "
        f"{len(have)} already present")
    return {"stamps": stamps, "rows": rows_written, "had": len(have)}
