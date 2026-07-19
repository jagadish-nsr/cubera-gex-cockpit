"""Offline REPLAY of a real stored day — step a historical 1-min day through the dashboard so you
can watch the UI (candle, developing TPO profile, trailing OI windows, regime chip, blast) build up
with time while the market is closed.

Everything is written to an ISOLATED journal date (REPLAY_DATE, never a real trading day) so live
data is never touched — delete `data/journal/oi_strikes_dhan/<SYMBOL>/2099-07-07.csv` to reset.
Source is tagged 'dhan (replay)' so the live-feed panels render (they route to the same Dhan
journal the replay writes). Decision-support demo only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

REPLAY_DATE = "2099-07-07"          # isolated journal date for replay (not a real trading day)


def _mins(t) -> int:
    h, m = str(t).split(":")
    return int(h) * 60 + int(m)


def available_days(symbol: str = "NIFTY") -> list[str]:
    from data.dhan import available_intraday_days
    return available_intraday_days(symbol)


def stamps(symbol: str, day: str) -> list[str]:
    """Sorted 1-min stamps (HH:MM) available for a stored day."""
    from data.dhan import load_intraday_day
    df = load_intraday_day(symbol, day)
    if df is None or getattr(df, "empty", True):
        return []
    return sorted(df["time_ist"].astype(str).unique(), key=_mins)


def reset(symbol: str, replay_date: str = REPLAY_DATE) -> None:
    """Delete the replay journal for a fresh run."""
    from app.timeseries import OISTRIKES_DHAN_DIR
    p = OISTRIKES_DHAN_DIR / symbol / f"{replay_date}.csv"
    if p.exists():
        p.unlink()


def _sliced(symbol: str, day: str, upto_min: str):
    from data.dhan import load_intraday_day
    df = load_intraday_day(symbol, day)
    if df is None or getattr(df, "empty", True):
        return None
    return df[df["time_ist"].astype(str).map(_mins) <= _mins(upto_min)].copy()


def ensure_journal(symbol: str, day: str, upto_min: str, replay_date: str = REPLAY_DATE) -> None:
    """Write the stored day's per-strike bars (≤ ``upto_min``) into the isolated replay journal so
    the profile / footprint / candle develop. Idempotent — skips stamps already written."""
    from app.timeseries import OISTRIKES_DHAN_DIR, append_strike_rows, load_strikes_day
    df = _sliced(symbol, day, upto_min)
    if df is None or df.empty:
        return
    have = load_strikes_day(replay_date, symbol, directory=OISTRIKES_DHAN_DIR)
    seen = set(have["time_ist"].astype(str)) if not have.empty else set()
    firsts = df.sort_values("time_ist").groupby(["strike", "side"]).first()

    def _f(x):
        try:
            v = float(x)
            return v if v == v else None
        except (TypeError, ValueError):
            return None

    for t, g in df.groupby("time_ist"):
        if str(t) in seen:
            continue
        ts = ((datetime.strptime(f"{replay_date} {t}", "%Y-%m-%d %H:%M") - timedelta(hours=5, minutes=30))
              .replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        spot_t = _f(g["spot"].dropna().iloc[0]) if g["spot"].notna().any() else None
        by: dict[float, dict] = {}
        for r in g.itertuples(index=False):
            k = _f(r.strike)
            if k is None:
                continue
            oi, cl = _f(r.oi), _f(r.close)
            f_oi = _f(firsts.loc[(r.strike, r.side), "oi"]) if (r.strike, r.side) in firsts.index else oi
            doi = (oi - f_oi) if (oi is not None and f_oi is not None) else 0.0
            cell = by.setdefault(k, {})
            if r.side == "CE":
                cell.update(call_oi=oi, call_ltp=cl, call_oi_near=oi, call_doi_near=doi)
            else:
                cell.update(put_oi=oi, put_ltp=cl, put_oi_near=oi, put_doi_near=doi)
        rows = [{"timestamp_utc": ts, "time_ist": str(t), "date_ist": replay_date,
                 "spot": round(spot_t, 2) if spot_t else None, "strike": k, **cell}
                for k, cell in sorted(by.items())]
        if rows:
            append_strike_rows(rows, date_ist=replay_date, symbol=symbol, directory=OISTRIKES_DHAN_DIR)


def build_snapshot(symbol: str, day: str, upto_min: str, replay_date: str = REPLAY_DATE):
    """A DashboardData for the replay clock at ``upto_min`` — levels from the latest bar's per-strike
    OI/premiums, developing day OHLC from the sliced spot. fetched_at is set so the dashboard's date
    is REPLAY_DATE (so the profile/footprint read the replay journal)."""
    import config
    from data.expiry import filter_expiry_window
    from data.nse import (aggregate_oi_volume, from_nse_results, nearest_expiry_premiums,
                          parse_nse_option_chain, per_strike_nearest_changes, per_strike_oi)
    from gex.levels import compute_levels
    from regime.classify import from_levels
    from app.data_access import DashboardData, _live_greeks

    idx = config.get_index(symbol)
    df = _sliced(symbol, day, upto_min)
    if df is None or df.empty:
        return None
    ist_dt = datetime.strptime(f"{replay_date} {upto_min}", "%Y-%m-%d %H:%M")
    fetched_at = (ist_dt - timedelta(hours=5, minutes=30)).replace(tzinfo=timezone.utc)
    exp = (ist_dt.date() + timedelta(days=2)).strftime("%d-%m-%Y")     # synthetic near expiry

    sp = df.sort_values("time_ist")
    spot = float(sp["spot"].dropna().iloc[-1])
    latest = sp.groupby(["strike", "side"]).last()
    first = sp.groupby(["strike", "side"]).first()

    def _v(x):
        try:
            v = float(x)
            return v if v == v else None
        except (TypeError, ValueError):
            return None

    rows_data = []
    for k in sorted(latest.index.get_level_values(0).unique()):
        row = {"strikePrice": float(k), "expiryDate": exp}
        for side in ("CE", "PE"):
            if (k, side) not in latest.index:
                continue
            oi, cl, iv = (_v(latest.loc[(k, side), c]) for c in ("oi", "close", "iv"))
            f_oi = _v(first.loc[(k, side), "oi"]) if (k, side) in first.index else oi
            row[side] = {"openInterest": oi or 0.0,
                         "changeinOpenInterest": (oi - f_oi) if (oi is not None and f_oi is not None) else 0.0,
                         "totalTradedVolume": 0.0, "impliedVolatility": iv or 0.0, "lastPrice": cl,
                         "change": 0.0, "strikePrice": float(k), "expiryDate": exp}
        rows_data.append(row)
    payload = {"records": {"data": rows_data, "underlyingValue": spot}}
    parsed, spot = parse_nse_option_chain(payload)
    snap = from_nse_results(parsed, spot=spot, expiry_window=config.EXPIRY_WINDOW_DEFAULT,
                            dealer_sign_model=config.DEALER_SIGN_MODEL, source="dhan (replay)",
                            fetched_at=fetched_at, oi_timestamp=fetched_at, underlying=idx.symbol)
    snap.contracts = filter_expiry_window(snap.contracts, config.EXPIRY_WINDOW_DEFAULT,
                                          fetched_at.date(), has_weekly=idx.has_weekly)
    levels = compute_levels(snap, contract_size=idx.lot_size, grid_range=config.FLIP_GRID_RANGE,
                            grid_step=config.FLIP_GRID_STEP, oi_stale_hours=config.OI_STALE_HOURS,
                            expiry_weighting=config.EXPIRY_WEIGHTING,
                            weight_halflife_days=config.EXPIRY_WEIGHT_HALFLIFE_DAYS)
    oi_strikes = per_strike_oi(payload, spot=spot, band_pts=idx.band_pts)
    prem = nearest_expiry_premiums(parsed)
    nc = per_strike_nearest_changes(payload, spot=spot, band_pts=idx.band_pts)
    gex = (dict(zip(levels.per_strike["strike"], levels.per_strike["net_gex"]))
           if levels.per_strike is not None else {})
    for s in oi_strikes:
        k = s["strike"]
        s["call_ltp"] = prem.get(k, {}).get("call_ltp")
        s["put_ltp"] = prem.get(k, {}).get("put_ltp")
        s["net_gex"] = gex.get(k)
        for kk in ("call_doi_near", "put_doi_near", "call_dprem", "put_dprem", "call_oi_near", "put_oi_near"):
            s[kk] = nc.get(k, {}).get(kk)
    ss = sp["spot"].dropna()
    day_ohlc = {"open": float(ss.iloc[0]), "high": float(ss.max()), "low": float(ss.min()),
                "close": float(ss.iloc[-1]), "ltp": float(ss.iloc[-1])}
    gk = _live_greeks(snap.contracts, float(levels.spot), idx.lot_size)
    return DashboardData(
        levels=levels, view=from_levels(levels), fetched_at=fetched_at,
        n_contracts=len(snap.contracts), expiries=sorted(snap.contracts["expiry"].unique().tolist()),
        window=config.EXPIRY_WINDOW_DEFAULT, source="dhan (replay)", symbol=idx.symbol,
        index_label=idx.label, oi_volume=aggregate_oi_volume(payload), oi_strikes=oi_strikes,
        india_vix=None, usdinr=None, news=None, vanna_now=gk["vanna"], charm_now=gk["charm"],
        dte_now=gk["dte"], day_ohlc=day_ohlc, source_note="REPLAY (stored day)")
