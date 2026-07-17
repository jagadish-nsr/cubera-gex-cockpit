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
    exchange: str = "NSE"   # "NSE" (NFO) or "BSE" (BFO) — for Breeze API routing


# CONFIRMED 2026-07 (user-verified against broker):
#  - NIFTY: lot 65 (SEBI revision effective Jan-2026 onward, was 75); WEEKLY +
#    monthly, expiry Tuesday.
#  - BANKNIFTY: lot 30 (NSE circular NSE/FAOP/70616, eff. Jan-2026 cycle, was 35);
#    MONTHLY ONLY (last Tuesday) — NSE discontinued Bank Nifty weeklies on
#    20-Nov-2024 under SEBI's single-weekly-per-exchange rule. Bigger index
#    (~3000-pt strikes are 100 apart), so a wider OI band than Nifty.
#  - SENSEX: lot 20 (BSE F&O); WEEKLY + monthly; expiry THURSDAY (BSE/BFO).
#    SENSEX is only available via DATA_SOURCE="icici_breeze".
INDICES: dict[str, Index] = {
    "NIFTY":     Index("NIFTY",     "Nifty 50",   65,  True,  2000, "NSE"),
    "BANKNIFTY": Index("BANKNIFTY", "Bank Nifty", 30,  False, 4000, "NSE"),
    "SENSEX":    Index("SENSEX",    "Sensex",     20,  True,  3000, "BSE"),
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
#   "icici_breeze" — DEFAULT. ICICI Direct Breeze API (requires API key + secret +
#                    daily session token; set via .env). Supports NIFTY, BANKNIFTY,
#                    and SENSEX (BSE/BFO).
#   "dhan"         — Dhan broker API (requires DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN;
#                    static token, no daily refresh). Supports NIFTY, BANKNIFTY.
#   "nse"          — Legacy: scrapes the official NSE endpoint (no credentials
#                    required, but rate-limited and blocks datacenter IPs).
#                    Supports NIFTY and BANKNIFTY only. SENSEX is not on NSE.
#
# Set DATA_SOURCE in your .env to switch adapters.  Default: icici_breeze.
DATA_SOURCE: str = os.environ.get("DATA_SOURCE", "icici_breeze").strip().lower()

VALID_DATA_SOURCES = {"nse", "icici_breeze", "dhan"}
if DATA_SOURCE not in VALID_DATA_SOURCES:
    raise ValueError(
        f"Unknown DATA_SOURCE={DATA_SOURCE!r}; valid: {sorted(VALID_DATA_SOURCES)}"
    )

# --- ICICI Direct Breeze API credentials ------------------------------------
# Required when DATA_SOURCE="icici_breeze". Set via .env (never commit real keys).
# The session_token must be refreshed every day — generate it at:
#   https://api.icicidirect.com/apiuser/login?api_key=<url-encoded-api-key>
ICICI_BREEZE_API_KEY:       str | None = os.environ.get("ICICI_BREEZE_API_KEY")
ICICI_BREEZE_API_SECRET:    str | None = os.environ.get("ICICI_BREEZE_API_SECRET")
ICICI_BREEZE_SESSION_TOKEN: str | None = os.environ.get("ICICI_BREEZE_SESSION_TOKEN")

# --- Dhan API credentials ---------------------------------------------------
# Required when DATA_SOURCE="dhan". Set via .env (never commit real keys).
# The access token is static (no daily refresh unlike ICICI Breeze).
# Generate at: https://dhanhq.co/docs/latest/authentication/
DHAN_CLIENT_ID:    str | None = os.environ.get("DHAN_CLIENT_ID")
DHAN_ACCESS_TOKEN: str | None = os.environ.get("DHAN_ACCESS_TOKEN")

# --- Dealer gamma sign convention -------------------------------------------
# Indian Market Mode: Institutional dealers (FII/PRO) are typically net option sellers.
# This implies they are short gamma on BOTH calls and puts (short_call_short_put).
# This creates a structurally volatile regime where OI walls act as breakout triggers.
DEALER_SIGN_MODEL = os.environ.get("DEALER_SIGN_MODEL", "short_call_short_put")

# --- Contract multiplier (default index lot size) ---------------------------
# ⚠️ VERIFY LIVE. NSE revises lot sizes by circular. This scales EVERY GEX number.
# Back-compat aliases for the default index; per-index code reads Index.lot_size
# off the INDICES registry above (the single source of truth).
NIFTY_LOT_SIZE = INDICES[DEFAULT_INDEX].lot_size  # VERIFY against the current NSE circular
CONTRACT_SIZE = NIFTY_LOT_SIZE

# --- Expiry window ----------------------------------------------------------
# The engine will fetch all expiries in this window, aggregate their gamma,
# and plot a single surface. Default is 0dte for the Indian market to capture Gamma Blasts.
EXPIRY_WINDOW_DEFAULT = "0dte"

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
MARKET_TZ = "Asia/Kolkata"   # NSE session 09:15–15:30 IST
UNDERLYING = INDICES[DEFAULT_INDEX].symbol   # back-compat alias for the default index

# --- Risk-free / dividend sourcing ------------------------------------------
# r and q are IMPLIED per-expiry from put-call parity off the same chain — no
# Indian rate feed (MIBOR/T-bill) or dividend assumption needed.
RISK_FREE_FALLBACK = None
