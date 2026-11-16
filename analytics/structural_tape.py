"""Load futures tape for volume-profile and supply/demand structural levels."""

from __future__ import annotations

from pathlib import Path


def _fut_dir() -> Path:
    from data.dhan import DHAN_FUT_DIR
    return DHAN_FUT_DIR


def available_futures_days(symbol: str) -> list[str]:
    folder = _fut_dir() / str(symbol).upper()
    if not folder.is_dir():
        return []
    return sorted(
        p.stem for p in folder.glob("*.parquet")
        if len(p.stem) == 10
    )


def prior_futures_day(symbol: str, today: str) -> str | None:
    days = [d for d in available_futures_days(symbol) if d < today]
    return days[-1] if days else None


def load_futures_bars(symbol: str, day: str):
    """OHLC+volume rows for one day, or None."""
    try:
        from data.dhan import load_futures_intraday_day
        frame = load_futures_intraday_day(symbol.upper(), day)
    except Exception:
        return None
    if frame is None or getattr(frame, "empty", True):
        return None
    return frame


def vp_and_zones_for(
    symbol: str,
    today: str,
    spot: float,
    *,
    strike_step: float = 50.0,
) -> dict:
    """Prior-session volume profile + supply/demand boxes; optional session VPOC."""
    from analytics.supply_demand import (
        detect_supply_demand,
        impulse_pts_for,
        zones_nearest_spot,
    )
    from analytics.volume_profile import bin_pts_for, profile_from_bars

    sym = symbol.upper()
    out = {
        "volume_profile": None,
        "session_vpoc": None,
        "supply_demand": [],
        "prior_day": None,
        "session_day": None,
    }

    prior = prior_futures_day(sym, today)
    impulse = impulse_pts_for(sym, strike_step)
    bp = bin_pts_for(sym, strike_step)

    if prior:
        out["prior_day"] = prior
        prior_bars = load_futures_bars(sym, prior)
        if prior_bars is not None:
            out["volume_profile"] = profile_from_bars(
                prior_bars,
                symbol=sym,
                bin_pts=bp,
                strike_step=strike_step,
                day=prior,
            )
            zones = detect_supply_demand(
                prior_bars,
                impulse_pts=impulse,
                day=prior,
            )
            out["supply_demand"] = zones_nearest_spot(zones, spot, max_each=2)

    session_bars = load_futures_bars(sym, today)
    if session_bars is not None and len(session_bars) >= 20:
        out["session_day"] = today
        session_vp = profile_from_bars(
            session_bars,
            symbol=sym,
            bin_pts=bp,
            strike_step=strike_step,
            day=today,
        )
        if session_vp and session_vp.get("vpoc"):
            out["session_vpoc"] = {
                "price": session_vp["vpoc"],
                "day": today,
                "n_bars": session_vp.get("n_bars"),
                "total_volume": session_vp.get("total_volume"),
            }
    return out
