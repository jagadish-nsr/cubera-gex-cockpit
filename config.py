"""Named, explicit configuration for the Nifty/Sensex GEX engine.

Same discipline as the SPX project: anything that changes a number the user might
trade on lives here as a named constant and is echoed onto every output. India-
specific values are marked VERIFY — they must be checked live, never assumed from
memory (NSE changes lot sizes and expiry rules by circular).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Load .env (if present) before reading any os.environ values below.
# python-dotenv is optional — if not installed the .env is simply ignored and
# credentials must be set in the shell environment instead.
try:
    from dotenv import load_dotenv as _load_dotenv  # type: ignore
    _load_dotenv(override=True)  # Overwrite existing shell env vars so daemon reloads pick up new token
except ImportError:
    pass  # python-dotenv not installed: rely on shell environment


# --- Tradable indices (the per-index landmines) -----------------------------
# Each index carries ONLY what differs between indices: the option-chain
# symbol, the contract multiplier (lot size), whether the index has WEEKLY
# expiries, and the per-strike OI band half-width. The pure GEX engine is
# index-agnostic and reused unchanged; only these knobs change.
#
# ⚠️ lot_size + has_weekly are "VERIFY LIVE, never guess" landmines (CLAUDE.md):
# NSE revises lot sizes and expiry rules by circular. The dealer-sign model is
# the SAME unvalidated landmine for BOTH NSE indices (India retail-SELLING flow);
# Bank Nifty is, if anything, more retail-heavy — no extra trust there.
@dataclass(frozen=True)
class Index:
    symbol: str        # option-chain symbol ("NIFTY" / "BANKNIFTY" / "SENSEX")
    label: str         # display name
    lot_size: int      # contract multiplier — VERIFY against the current circular
    has_weekly: bool   # are weekly expiries served? (drives the expiry window/fetch)
    band_pts: float    # per-strike OI band: keep strikes within ± this of spot
    exchange: str = "NSE"         # "NSE" (NFO) or "BSE" (BFO) — for expiry routing
    regime_proxy: str | None = None  # borrow this index's |vanna| regime.
    strike_step: int = 50         # standard strike interval (Nifty: 50, Bank Nifty: 100, Sensex: 100)


# CONFIRMED 2026-07 (user-verified against broker):
#  - NIFTY: lot 65 (SEBI revision effective Jan-2026 onward, was 75); WEEKLY +
#    monthly, expiry Tuesday.
#  - BANKNIFTY: lot 30 (NSE circular NSE/FAOP/70616, eff. Jan-2026 cycle, was 35);
#    MONTHLY ONLY (last Tuesday) — NSE discontinued Bank Nifty weeklies on
#    20-Nov-2024 under SEBI's single-weekly-per-exchange rule. Bigger index
#    (~3000-pt strikes are 100 apart), so a wider OI band than Nifty.
#  - SENSEX: lot 20 (BSE F&O); WEEKLY + monthly; expiry THURSDAY (BSE/BFO).
#    SENSEX is Dhan-only (BSE). Its own vanna doesn't grade its move cleanly so it
#    borrows NIFTY's vanna for the v1 regime gauge (regime_proxy="NIFTY").
INDICES: dict[str, Index] = {
    "NIFTY":     Index("NIFTY",     "Nifty 50",   65,  True,  2000, "NSE", strike_step=50),
    "BANKNIFTY": Index("BANKNIFTY", "Bank Nifty", 30,  False, 4000, "NSE", strike_step=100),
    "SENSEX":    Index("SENSEX",    "Sensex",     20,  True,  6000, "BSE", regime_proxy="NIFTY", strike_step=100),
}
DEFAULT_INDEX = "NIFTY"


def get_index(symbol: str | None) -> Index:
    """Resolve a symbol to its Index (None -> the default). Fails loud on an
    unknown symbol rather than silently trading the wrong contract."""
    sym = (symbol or DEFAULT_INDEX).upper()
    try:
        return INDICES[sym]
    except KeyError:
        raise ValueError(
            f"unknown index {sym!r}; known: {sorted(INDICES)}"
        ) from None


# ---------------------------------------------------------------------------
# DATA SOURCE FEATURE FLAG
# ---------------------------------------------------------------------------
# Controls which data provider is used for option-chain fetches.
#   "auto"         — DEFAULT. Uses Dhan when token is present, else NSE backup.
#   "dhan"         — Dhan broker API (requires DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN).
#   "nse"          — Legacy: scrapes official NSE endpoint.
DATA_SOURCE: str = os.environ.get("DATA_SOURCE", "auto").strip().lower()
LIVE_SOURCE = DATA_SOURCE

VALID_DATA_SOURCES = {"nse", "dhan", "auto"}
if DATA_SOURCE not in VALID_DATA_SOURCES:
    raise ValueError(
        f"Unknown DATA_SOURCE={DATA_SOURCE!r}; valid: {sorted(VALID_DATA_SOURCES)}"
    )


# --- Dhan API credentials ---------------------------------------------------
DHAN_CLIENT_ID:    str | None = os.environ.get("DHAN_CLIENT_ID")
DHAN_ACCESS_TOKEN: str | None = os.environ.get("DHAN_ACCESS_TOKEN")

# --- Simulation Mode Flag (Zero-Fabrication Contract) -----------------------
# When True: Allows synthetic test/demo generators (reseed, mock ticks, synthetic drift).
# When False (DEFAULT): Strict Zero-Fabrication Contract. Live data from Dhan WS/API or EOD snapshots.
SIMULATION_MODE: bool = os.environ.get("SIMULATION_MODE", "false").strip().lower() in ("true", "1", "yes")


# --- Dealer gamma sign convention -------------------------------------------
# Native Indian Market Mode: Indian index option participants and prop desks
# are systematic net sellers of both legs (short_call_short_put / SCSP).
# Calls and Puts are written heavily; Gamma Flip represents the Writer Equilibrium
# (|Call GEX| == |Put GEX|).
DEALER_SIGN_MODEL = os.environ.get("DEALER_SIGN_MODEL", "short_call_short_put")

# --- Contract multiplier (default index lot size) ---------------------------
# ⚠️ VERIFY LIVE. NSE revises lot sizes by circular. This scales EVERY GEX number.
# Back-compat aliases for the default index; per-index code reads Index.lot_size
# off the INDICES registry above (the single source of truth).
NIFTY_LOT_SIZE = INDICES[DEFAULT_INDEX].lot_size  # VERIFY against the current NSE circular
CONTRACT_SIZE = NIFTY_LOT_SIZE

# --- Expiry window ----------------------------------------------------------
# The engine will fetch all expiries in this window, aggregate their gamma,
# and plot a single surface. Default expiry window on startup (see data/expiry.py for logic).
# For Indian markets, "weekly" is the safest default because options do not expire
# every day. If "0dte" is used on a non-expiry day, the chain will be empty.
EXPIRY_WINDOW_DEFAULT = "weekly"

# --- Gamma-flip profile grid ------------------------------------------------
FLIP_GRID_RANGE = 0.10   # +/- 10% around spot
FLIP_GRID_STEP = 0.0025  # 0.25% steps

# --- Expiry weighting -------------------------------------------------------
# Near-dated gamma drives intraday action, so weight each contract's GEX by its
# days-to-expiry. Explicit + logged + tunable (gex/weighting.py). 'equal' = off.
#   'exp' with a 5-day half-life: a 5-DTE contract counts half a 0-DTE one.
EXPIRY_WEIGHTING = "equal"           # equal | exp | inverse | nearest
EXPIRY_WEIGHT_HALFLIFE_DAYS = 5.0  # only used by 'exp'

# --- OI staleness -----------------------------------------------------------
OI_STALE_HOURS = 30  # NSE OI is end-of-day lagging; flag beyond ~1 trading day

# --- Per-strike OI/ΔOI capture ----------------------------------------------
# Phase A also logs per-strike OI / ΔOI (to test whether price respects the
# strike where OI builds — the question aggregate ΔOI can't answer). To bound
# the file, only strikes within this many points of spot are kept (the walls
# always sit well inside this band; far-OTM strikes carry negligible OI).
# Back-compat alias for the default index (per-index code reads Index.band_pts).
OI_STRIKE_BAND_PTS = INDICES[DEFAULT_INDEX].band_pts

# --- Market / timezone ------------------------------------------------------
MARKET_TZ = "Asia/Kolkata"
UNDERLYING = INDICES[DEFAULT_INDEX].symbol   # back-compat alias for the default index

# NSE session, minutes-since-midnight IST. ⚠️ VERIFY-LIVE landmine (CLAUDE.md): from
# 2026-08-03 NSE extended EQUITY-DERIVATIVES (F&O: index/stock futures + options) trading
# to 15:40 (was 15:30), to align with the cash Closing-Auction-Session (15:15–15:35). Open
# is unchanged 09:15. Data collectors of FUTURES/OPTIONS ticks use FNO_CLOSE_MIN so they don't
# stop 10 min early and miss the peak expiry-settlement window. NSE circular 2026-05-30.
MARKET_OPEN_MIN = 9 * 60 + 15        # 09:15
FNO_CLOSE_MIN = 15 * 60 + 40         # 15:40 (F&O), effective 2026-08-03; was 15:30
CASH_CLOSE_MIN = 15 * 60 + 30        # 15:30 continuous cash (CAS auction 15:15–15:35 sets the close)

# --- Risk-free / dividend sourcing ------------------------------------------
# r and q are IMPLIED per-expiry from put-call parity off the same chain — no
# Indian rate feed (MIBOR/T-bill) or dividend assumption needed.
RISK_FREE_FALLBACK = None


def is_market_open(now: datetime | None = None, include_fno_extended: bool = True) -> bool:
    """Return True if currently during live Indian market trading hours.

    By default, include_fno_extended=True enforces FnO trading hours (09:15 AM to 03:40 PM IST).
    If include_fno_extended=False, checks continuous cash trading (09:15 AM to 03:30 PM IST).
    """
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    if now is None:
        now = datetime.now(ist)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ist)
    else:
        now = now.astimezone(ist)

    if now.weekday() >= 5:  # Saturday or Sunday
        return False

    # Check NSE F&O trading holiday calendar
    try:
        from data.holidays import is_holiday
        if is_holiday(now.date()):
            return False
    except Exception:
        pass

    t_min = now.hour * 60 + now.minute
    close_min = FNO_CLOSE_MIN if include_fno_extended else CASH_CLOSE_MIN
    return MARKET_OPEN_MIN <= t_min < close_min
