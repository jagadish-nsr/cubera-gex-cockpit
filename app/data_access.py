"""Dashboard data pipeline — NO streamlit import, so it is unit-testable offline.

load_dashboard() runs the full chain: pull (NSE or demo) -> snapshot -> filter
window -> compute_levels -> regime view, and returns a small bundle the UI renders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import config
from data.expiry import filter_expiry_window
from data.nse import from_nse_results
from gex.levels import Levels, compute_levels
from regime.classify import RegimeView, from_levels

WINDOW_CHOICES = ["0dte_to_next_monthly", "weekly", "0dte", "monthly", "all"]


@dataclass
class DashboardData:
    levels: Levels
    view: RegimeView
    fetched_at: datetime
    n_contracts: int
    expiries: list
    window: str
    source: str
    oi_volume: dict | None = None  # OI/ΔOI/volume aggregates (live NSE only)


def load_dashboard(window: str | None = None, use_demo: bool = False) -> DashboardData:
    window = window or config.EXPIRY_WINDOW_DEFAULT
    fetched_at = datetime.now(timezone.utc)

    if use_demo:
        from data.demo import build_demo_rows

        rows, spot = build_demo_rows(fetched_at=fetched_at)
        source = "DEMO-SYNTHETIC"
        payload = None
    else:
        from data.nse import aggregate_oi_volume, fetch_chain, parse_nse_option_chain

        payload = fetch_chain(config.UNDERLYING)   # single live pull
        rows, spot = parse_nse_option_chain(payload)
        source = "nse"

    snap = from_nse_results(
        rows, spot=spot, expiry_window=window,
        dealer_sign_model=config.DEALER_SIGN_MODEL, source=source,
        fetched_at=fetched_at, oi_timestamp=fetched_at, underlying=config.UNDERLYING,
    )
    snap.contracts = filter_expiry_window(snap.contracts, window, fetched_at.date())
    snap.validate()

    levels = compute_levels(
        snap, contract_size=config.CONTRACT_SIZE,
        grid_range=config.FLIP_GRID_RANGE, grid_step=config.FLIP_GRID_STEP,
        oi_stale_hours=config.OI_STALE_HOURS,
        expiry_weighting=config.EXPIRY_WEIGHTING,
        weight_halflife_days=config.EXPIRY_WEIGHT_HALFLIFE_DAYS,
    )
    view = from_levels(levels)
    expiries = sorted(snap.contracts["expiry"].unique().tolist())
    oi_volume = aggregate_oi_volume(payload) if payload is not None else None
    return DashboardData(
        levels=levels, view=view, fetched_at=fetched_at,
        n_contracts=len(snap.contracts), expiries=expiries, window=window, source=source,
        oi_volume=oi_volume,
    )
